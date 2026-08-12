import os
import re
import time
import logging
import tempfile
from html import unescape
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import bale


# ============================================================
# LOAD ENVIRONMENT
# ============================================================

load_dotenv()


# ============================================================
# CONFIG
# ============================================================

BALE_TOKEN = os.getenv("BALE_TOKEN", "").strip()

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "10"))
RETRY_DELAY = int(os.getenv("RETRY_DELAY", "15"))

FOOTER = "@Gamefa_official"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("gamefa-bale-bot")


# ============================================================
# TOKEN CHECK
# ============================================================

if not BALE_TOKEN:
    raise RuntimeError(
        "BALE_TOKEN در متغیرهای محیطی Railway تنظیم نشده است."
    )


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://gamefa.com/",
})


# ============================================================
# ADMIN CHECK
# ============================================================

def is_admin(user_id):

    try:
        return int(user_id) in ADMIN_IDS

    except Exception:
        return False


# ============================================================
# TEXT CLEANING
# ============================================================

def clean_text(text):

    if not text:
        return ""

    text = unescape(text)

    text = text.replace("\xa0", " ")
    text = text.replace("\r", "\n")

    # حذف فاصله‌های اضافی
    text = re.sub(r"[ \t]+", " ", text)

    # حذف فاصله قبل از نقطه و علائم
    text = re.sub(r"\s+([،؛,:.!؟])", r"\1", text)

    # حذف خطوط خالی زیاد
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ============================================================
# URL
# ============================================================

def normalize_url(url):

    if not url:
        return ""

    url = url.strip()
    url = url.strip("<>")

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    return url


def extract_url(text):

    if not text:
        return None

    # URL کامل
    match = re.search(
        r"https?://[^\s<>\"']+",
        text,
        re.IGNORECASE
    )

    if match:

        url = match.group(0)

        url = url.rstrip(
            ").,؛،!؟»"
        )

        return normalize_url(url)

    # gamefa.com بدون https
    match = re.search(
        r"(?:www\.)?gamefa\.com/[^\s<>\"']+",
        text,
        re.IGNORECASE
    )

    if match:

        url = match.group(0)

        url = url.rstrip(
            ").,؛،!؟»"
        )

        return normalize_url(url)

    return None


def is_gamefa_url(url):

    try:

        parsed = urlparse(url)

        host = parsed.netloc.lower()

        if host.startswith("www."):
            host = host[4:]

        return (
            host == "gamefa.com"
            or host.endswith(".gamefa.com")
        )

    except Exception:

        return False


# ============================================================
# FETCH ARTICLE
# ============================================================

def fetch_soup(url):

    response = session.get(
        url,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True
    )

    response.raise_for_status()

    # اگر سایت encoding مشخص نکرده
    if response.apparent_encoding:

        response.encoding = response.apparent_encoding

    return BeautifulSoup(
        response.text,
        "html.parser"
    )


# ============================================================
# TITLE
# ============================================================

def extract_title(soup):

    selectors = [
        "h1.entry-title",
        "h1.post-title",
        "article h1",
        ".entry-header h1",
        ".post-header h1",
        "main h1",
        "h1",
    ]

    for selector in selectors:

        element = soup.select_one(selector)

        if not element:
            continue

        title = clean_text(
            element.get_text(
                " ",
                strip=True
            )
        )

        if title:
            return title

    # OpenGraph
    meta = soup.find(
        "meta",
        property="og:title"
    )

    if meta:

        title = clean_text(
            meta.get("content", "")
        )

        if title:
            return title

    return "خبر گیمفا"


# ============================================================
# IMAGE URL EXTRACTION
# ============================================================

def extract_image(soup, page_url):

    candidates = []

    # --------------------------------------------------------
    # OpenGraph
    # --------------------------------------------------------

    meta = soup.find(
        "meta",
        property="og:image"
    )

    if meta:

        image = meta.get("content")

        if image:
            candidates.append(image)

    # --------------------------------------------------------
    # Twitter
    # --------------------------------------------------------

    meta = soup.find(
        "meta",
        attrs={
            "name": "twitter:image"
        }
    )

    if meta:

        image = meta.get("content")

        if image:
            candidates.append(image)

    # --------------------------------------------------------
    # Article image
    # --------------------------------------------------------

    selectors = [
        "article img",
        ".entry-content img",
        ".post-content img",
        ".article-content img",
        ".single-content img",
        ".td-post-content img",
        "main img",
    ]

    for selector in selectors:

        for image_element in soup.select(selector):

            src = (
                image_element.get("src")
                or image_element.get("data-src")
                or image_element.get("data-lazy-src")
                or image_element.get("data-original")
                or image_element.get("data-image")
            )

            if src:
                candidates.append(src)

            # srcset
            srcset = image_element.get("srcset")

            if srcset:

                parts = srcset.split(",")

                for part in parts:

                    part = part.strip()

                    if not part:
                        continue

                    candidate = part.split(" ")[0]

                    if candidate:
                        candidates.append(candidate)

    # --------------------------------------------------------
    # Validate candidates
    # --------------------------------------------------------

    for image_url in candidates:

        if not image_url:
            continue

        image_url = image_url.strip()

        image_url = urljoin(
            page_url,
            image_url
        )

        if not image_url.startswith(
            ("http://", "https://")
        ):
            continue

        # SVG را رد می‌کنیم
        if ".svg" in image_url.lower():
            continue

        return image_url

    return None


# ============================================================
# DOWNLOAD IMAGE
# ============================================================

def download_image(image_url, page_url):

    if not image_url:
        return None

    try:

        image_url = urljoin(
            page_url,
            image_url
        )

        logger.info(
            "Downloading image: %s",
            image_url
        )

        headers = {
            "User-Agent": session.headers["User-Agent"],
            "Accept": (
                "image/avif,image/webp,image/apng,"
                "image/svg+xml,image/*,*/*;q=0.8"
            ),
            "Referer": page_url,
        }

        response = session.get(
            image_url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            stream=True
        )

        response.raise_for_status()

        content_type = (
            response.headers
            .get("Content-Type", "")
            .lower()
        )

        # ----------------------------------------------------
        # بعض CDN ها Content-Type درست نمی‌دهند.
        # بنابراین فقط موارد کاملاً نامعتبر را رد می‌کنیم.
        # ----------------------------------------------------

        if (
            "text/html" in content_type
            or "application/json" in content_type
        ):

            logger.warning(
                "Image URL returned non-image content: %s",
                content_type
            )

            return None

        # ----------------------------------------------------
        # ساخت فایل موقت
        # ----------------------------------------------------

        suffix = ".jpg"

        if "png" in content_type:
            suffix = ".png"

        elif "webp" in content_type:
            suffix = ".webp"

        elif "gif" in content_type:
            suffix = ".gif"

        elif "jpeg" in content_type:
            suffix = ".jpg"

        temp_file = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix
        )

        file_path = temp_file.name

        total_size = 0

        max_size = 15 * 1024 * 1024

        try:

            for chunk in response.iter_content(
                chunk_size=64 * 1024
            ):

                if not chunk:
                    continue

                total_size += len(chunk)

                if total_size > max_size:

                    logger.warning(
                        "Image is larger than 15 MB."
                    )

                    temp_file.close()

                    try:
                        os.unlink(file_path)
                    except Exception:
                        pass

                    return None

                temp_file.write(chunk)

            temp_file.close()

            # بررسی اینکه فایل واقعاً خالی نباشد
            if os.path.getsize(file_path) < 100:

                logger.warning(
                    "Downloaded image is empty."
                )

                try:
                    os.unlink(file_path)
                except Exception:
                    pass

                return None

            logger.info(
                "Image downloaded successfully: %.2f KB",
                total_size / 1024
            )

            return file_path

        except Exception:

            temp_file.close()

            try:
                os.unlink(file_path)
            except Exception:
                pass

            raise

    except Exception as error:

        logger.exception(
            "Failed to download image: %s",
            error
        )

        return None


# ============================================================
# REMOVE UNWANTED ELEMENTS
# ============================================================

def remove_unwanted(container):

    selectors = [

        "script",
        "style",
        "noscript",
        "iframe",
        "svg",
        "form",
        "button",

        "nav",
        "footer",
        "header",

        ".advertisement",
        ".advertisement-container",
        ".ads",
        ".ad",
        ".banner",

        ".social-share",
        ".share-buttons",

        ".related-posts",
        ".related",

        ".comments",
        ".comment-section",

        ".author-box",

        ".post-meta",
        ".entry-meta",
        ".breadcrumb",

        ".sidebar",
        ".widget",
    ]

    for selector in selectors:

        for element in container.select(selector):

            try:
                element.decompose()
            except Exception:
                pass


# ============================================================
# PARAGRAPH VALIDATION
# ============================================================

def valid_paragraph(text):

    if not text:
        return False

    if len(text) < 50:
        return False

    unwanted = [

        "اشتراک‌گذاری",
        "اشتراک گذاری",

        "دیدگاه",
        "دیدگاه‌ها",

        "نظرات",

        "مطالب مرتبط",

        "تبلیغات",

        "عضویت",

        "دنبال کنید",

        "ادامه مطلب",

        "ادامه خبر",

        "Gamefa",
        "گیمفا را دنبال",

    ]

    lower_text = text.lower()

    for word in unwanted:

        if word.lower() in lower_text:
            return False

    return True


# ============================================================
# EXTRACT PARAGRAPHS
# ============================================================

def extract_paragraphs(soup):

    best_paragraphs = []

    selectors = [

        "article",

        ".entry-content",

        ".post-content",

        ".article-content",

        ".single-content",

        ".td-post-content",

        ".content-area",

        "main",
    ]

    for selector in selectors:

        containers = soup.select(selector)

        for container in containers:

            try:

                copy = BeautifulSoup(
                    str(container),
                    "html.parser"
                )

                remove_unwanted(copy)

                paragraphs = []

                for paragraph in copy.find_all("p"):

                    text = clean_text(
                        paragraph.get_text(
                            " ",
                            strip=True
                        )
                    )

                    if not valid_paragraph(text):
                        continue

                    if text in paragraphs:
                        continue

                    paragraphs.append(text)

                if len(paragraphs) > len(
                    best_paragraphs
                ):

                    best_paragraphs = paragraphs

            except Exception as error:

                logger.warning(
                    "Paragraph extraction error: %s",
                    error
                )

    return best_paragraphs[:2]


# ============================================================
# MARKDOWN ESCAPE
# ============================================================

def md_escape(text):

    if not text:
        return ""

    # ترتیب مهم است
    text = text.replace("\\", "\\\\")

    for character in [
        "_",
        "*",
        "`",
        "[",
        "]",
    ]:

        text = text.replace(
            character,
            "\\" + character
        )

    return text


# ============================================================
# BUILD CAPTION
# ============================================================

def build_caption(
    title,
    paragraphs,
    url
):

    safe_title = md_escape(
        title
    )

    safe_paragraph_1 = md_escape(
        paragraphs[0]
    )

    safe_paragraph_2 = md_escape(
        paragraphs[1]
    )

    # ========================================================
    # قالب نهایی دقیقاً طبق درخواست
    # ========================================================

    return (
        f"📢 *[{safe_title}]({url})*\n\n"

        f"🟣 {safe_paragraph_1}\n\n"

        f"🟣 {safe_paragraph_2}\n\n"

        f"*[📑 ادامه خبر]({url})*\n\n"

        f"🆔 *{FOOTER}*"
    )


# ============================================================
# SEND TEXT FALLBACK
# ============================================================

async def send_text_fallback(
    message,
    caption,
    reason=None
):

    if reason:

        logger.warning(
            "Sending text fallback. Reason: %s",
            reason
        )

        await message.reply(
            "⚠️ ارسال تصویر با مشکل مواجه شد.\n\n"
            + caption
        )

    else:

        await message.reply(
            caption
        )


# ============================================================
# DELETE TEMP FILE
# ============================================================

def delete_temp_file(file_path):

    if not file_path:
        return

    try:

        if os.path.exists(file_path):

            os.unlink(file_path)

            logger.info(
                "Temporary image deleted."
            )

    except Exception as error:

        logger.warning(
            "Could not delete temp file: %s",
            error
        )


# ============================================================
# BALE BOT
# ============================================================

bot = bale.Bot(
    token=BALE_TOKEN
)


# ============================================================
# READY
# ============================================================

@bot.event
async def on_ready():

    logger.info(
        "========================================"
    )

    logger.info(
        "Gamefa Bale News Bot is READY"
    )

    logger.info(
        "Admins: %s",
        len(ADMIN_IDS)
    )

    logger.info(
        "========================================"
    )


# ============================================================
# MESSAGE
# ============================================================

@bot.event
async def on_message(message):

    try:

        # ----------------------------------------------------
        # USER
        # ----------------------------------------------------

        user = getattr(
            message,
            "author",
            None
        )

        user_id = getattr(
            user,
            "id",
            None
        )

        # ----------------------------------------------------
        # IGNORE BOT
        # ----------------------------------------------------

        if getattr(
            user,
            "is_bot",
            False
        ):

            return

        # ----------------------------------------------------
        # ADMIN ONLY
        # ----------------------------------------------------

        if not is_admin(user_id):

            return

        # ----------------------------------------------------
        # CONTENT
        # ----------------------------------------------------

        content = (
            getattr(
                message,
                "content",
                None
            )
            or ""
        ).strip()

        # ----------------------------------------------------
        # HELP
        # ----------------------------------------------------

        if content.lower() in (
            "/start",
            "/help",
            "/راهنما",
        ):

            await message.reply(
                "👋 سلام!\n\n"
                "لینک یک خبر از سایت Gamefa.com "
                "را ارسال کنید.\n\n"
                "ربات به صورت خودکار:\n"
                "📰 عنوان خبر\n"
                "🟣 دو پاراگراف اول\n"
                "🖼 تصویر شاخص\n"
                "🔗 لینک ادامه خبر\n"
                "🆔 آیدی کانال\n\n"
                "را آماده می‌کند."
            )

            return

        # ----------------------------------------------------
        # URL
        # ----------------------------------------------------

        url = extract_url(content)

        if not url:

            return

        # ----------------------------------------------------
        # GAMEFA CHECK
        # ----------------------------------------------------

        if not is_gamefa_url(url):

            await message.reply(
                "❌ فقط لینک‌های سایت "
                "Gamefa.com قابل پردازش هستند."
            )

            return

        # ----------------------------------------------------
        # PROCESSING
        # ----------------------------------------------------

        processing = await message.reply(
            "⏳ در حال دریافت و پردازش خبر از گیمفا..."
        )

        temp_image = None

        try:

            # =================================================
            # FETCH
            # =================================================

            logger.info(
                "Fetching article: %s",
                url
            )

            soup = fetch_soup(url)

            # =================================================
            # TITLE
            # =================================================

            title = extract_title(
                soup
            )

            logger.info(
                "Title: %s",
                title
            )

            # =================================================
            # IMAGE URL
            # =================================================

            image_url = extract_image(
                soup,
                url
            )

            if image_url:

                logger.info(
                    "Featured image found: %s",
                    image_url
                )

            else:

                logger.warning(
                    "No featured image found."
                )

            # =================================================
            # PARAGRAPHS
            # =================================================

            paragraphs = extract_paragraphs(
                soup
            )

            logger.info(
                "Extracted paragraphs: %s",
                len(paragraphs)
            )

            # =================================================
            # VALIDATE
            # =================================================

            if len(paragraphs) < 2:

                try:

                    await processing.edit(
                        "❌ نتوانستم دو پاراگراف واقعی "
                        "از مقاله پیدا کنم."
                    )

                except Exception:
                    pass

                return

            # =================================================
            # CAPTION
            # =================================================

            caption = build_caption(
                title=title,
                paragraphs=paragraphs,
                url=url
            )

            logger.info(
                "Caption created successfully."
            )

            # =================================================
            # DOWNLOAD IMAGE
            # =================================================

            if image_url:

                temp_image = download_image(
                    image_url,
                    url
                )

            # =================================================
            # SEND IMAGE
            # =================================================

            if temp_image:

                try:

                    logger.info(
                        "Uploading image to Bale..."
                    )

                    # طبق API رسمی Bale باید فایل
                    # به صورت InputFile ارسال شود.
                    input_file = bale.InputFile(
                        temp_image
                    )

                    await bot.send_photo(
                        chat_id=user_id,
                        photo=input_file,
                        caption=caption,
                    )

                    logger.info(
                        "Image + caption sent successfully."
                    )

                except Exception as image_error:

                    logger.exception(
                        "Bale image upload failed: %s",
                        image_error
                    )

                    await send_text_fallback(
                        message,
                        caption,
                        reason=image_error
                    )

            else:

                logger.warning(
                    "Image could not be downloaded."
                )

                await send_text_fallback(
                    message,
                    caption,
                    reason="Image download failed"
                )

            # =================================================
            # PROCESSING MESSAGE
            # =================================================

            try:

                await processing.edit(
                    "✅ خبر با موفقیت آماده شد."
                )

            except Exception as error:

                logger.debug(
                    "Could not edit processing message: %s",
                    error
                )

            logger.info(
                "Article processed successfully: %s",
                title
            )

        # =====================================================
        # HTTP ERROR
        # =====================================================

        except requests.RequestException as error:

            logger.exception(
                "HTTP error: %s",
                error
            )

            try:

                await processing.edit(
                    "❌ هنگام دریافت مقاله از گیمفا "
                    "خطای شبکه رخ داد."
                )

            except Exception:
                pass

        # =====================================================
        # GENERAL ERROR
        # =====================================================

        except Exception as error:

            logger.exception(
                "Processing error: %s",
                error
            )

            try:

                await processing.edit(
                    "❌ خطا هنگام پردازش خبر:\n\n"
                    f"{error}"
                )

            except Exception:
                pass

        # =====================================================
        # CLEAN TEMP FILE
        # =====================================================

        finally:

            delete_temp_file(
                temp_image
            )

    # ========================================================
    # GLOBAL MESSAGE ERROR
    # ========================================================

    except Exception as error:

        logger.exception(
            "Unhandled message error: %s",
            error
        )


# ============================================================
# RUN WITH RETRY
# ============================================================

def run_with_retry():

    """
    Bale خودش event loop را مدیریت می‌کند.

    بنابراین:

        asyncio.run(bot.run())

    ممنوع است.

    همچنین:

        async def run_with_retry()

    هم لازم نیست.

    bot.run() باید مستقیماً از thread اصلی اجرا شود.
    """

    attempt = 0

    while True:

        attempt += 1

        try:

            logger.info(
                "Starting Bale bot (attempt %s)...",
                attempt
            )

            # =================================================
            # فقط همین
            # =================================================

            bot.run()

            logger.warning(
                "Bale bot stopped normally."
            )

            logger.info(
                "Restarting in %s seconds...",
                RETRY_DELAY
            )

            time.sleep(
                RETRY_DELAY
            )

        except (
            OSError,
            ConnectionError,
            TimeoutError,
        ) as error:

            logger.warning(
                "Network error: %s | retrying in %s seconds...",
                error,
                RETRY_DELAY
            )

            time.sleep(
                RETRY_DELAY
            )

        except Exception as error:

            logger.exception(
                "Bale runtime error: %s | retrying in %s seconds...",
                error,
                RETRY_DELAY
            )

            time.sleep(
                RETRY_DELAY
            )

        if attempt >= MAX_RETRIES:

            logger.warning(
                "Reached MAX_RETRIES=%s. Resetting counter.",
                MAX_RETRIES
            )

            attempt = 0


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    logger.info(
        "========================================"
    )

    logger.info(
        "Starting Gamefa Bale Bot..."
    )

    logger.info(
        "Python PID: %s",
        os.getpid()
    )

    logger.info(
        "========================================"
    )

    try:

        run_with_retry()

    except KeyboardInterrupt:

        logger.info(
            "Bot stopped by user."
        )

    except Exception as error:

        logger.exception(
            "Fatal error: %s",
            error
    )
