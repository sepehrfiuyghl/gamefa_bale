import os
import re
import time
import uuid
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

TEMP_DIR = os.path.join(
    tempfile.gettempdir(),
    "gamefa_bale_bot"
)

os.makedirs(
    TEMP_DIR,
    exist_ok=True
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("gamefa-bale-bot")


# ============================================================
# VALIDATE TOKEN
# ============================================================

if not BALE_TOKEN:
    raise RuntimeError(
        "BALE_TOKEN در متغیرهای محیطی Railway یا فایل .env تنظیم نشده است."
    )


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,image/webp,"
        "image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://gamefa.com/",
    "Connection": "keep-alive",
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

    text = unescape(str(text))
    text = text.replace("\xa0", " ")
    text = text.replace("\u200c", "\u200c")

    text = re.sub(r"[ \t]+", " ", text)

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

    return text.strip()


# ============================================================
# URL
# ============================================================

def normalize_url(url):
    if not url:
        return ""

    url = url.strip().strip("<>")

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    return url


def extract_url(text):
    if not text:
        return None

    match = re.search(
        r"https?://[^\s<>\"']+",
        text,
        re.IGNORECASE
    )

    if match:
        url = match.group(0)

        url = url.rstrip(
            ").,؛،!؟\"'"
        )

        return normalize_url(url)

    match = re.search(
        r"(?:www\.)?gamefa\.com/[^\s<>\"']+",
        text,
        re.IGNORECASE
    )

    if match:
        url = match.group(0)

        url = url.rstrip(
            ").,؛،!؟\"'"
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
    logger.info(
        "Downloading article: %s",
        url
    )

    response = session.get(
        url,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True
    )

    response.raise_for_status()

    if response.apparent_encoding:
        response.encoding = response.apparent_encoding

    logger.info(
        "Article downloaded successfully. Status=%s",
        response.status_code
    )

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

        if element:

            title = clean_text(
                element.get_text(
                    " ",
                    strip=True
                )
            )

            if title:
                return title

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
# IMAGE URL HELPERS
# ============================================================

def clean_image_url(url, base_url):
    if not url:
        return None

    url = url.strip()

    if url.startswith("//"):
        url = "https:" + url

    url = urljoin(
        base_url,
        url
    )

    return url


def get_attribute_image(element):

    attributes = [
        "src",
        "data-src",
        "data-lazy-src",
        "data-original",
        "data-image",
        "data-url",
        "data-flickity-lazyload",
    ]

    for attr in attributes:

        value = element.get(attr)

        if value:
            return value.strip()

    # srcset
    srcset = element.get("srcset")

    if srcset:

        parts = [
            x.strip()
            for x in srcset.split(",")
            if x.strip()
        ]

        if parts:

            # آخرین تصویر معمولاً رزولوشن بالاتری دارد
            last = parts[-1]

            image_url = last.split()[0]

            if image_url:
                return image_url

    return None


# ============================================================
# EXTRACT IMAGE
# ============================================================

def extract_image(soup, article_url):

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

    for attrs in [
        {"name": "twitter:image"},
        {"property": "twitter:image"},
    ]:

        meta = soup.find(
            "meta",
            attrs=attrs
        )

        if meta:

            image = meta.get("content")

            if image:
                candidates.append(image)

    # --------------------------------------------------------
    # Schema / JSON-LD
    # --------------------------------------------------------

    for script in soup.find_all(
        "script",
        type="application/ld+json"
    ):

        try:

            raw = script.string or script.get_text()

            if not raw:
                continue

            # جستجوی URL تصویر بدون نیاز به parse کامل JSON
            matches = re.findall(
                r'https?://[^"\']+\.(?:jpg|jpeg|png|webp)(?:\?[^"\']*)?',
                raw,
                re.IGNORECASE
            )

            candidates.extend(matches)

        except Exception:
            pass

    # --------------------------------------------------------
    # Article images
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

            image_url = get_attribute_image(
                image_element
            )

            if image_url:
                candidates.append(
                    image_url
                )

    # --------------------------------------------------------
    # Normalize + remove duplicates
    # --------------------------------------------------------

    final_candidates = []

    seen = set()

    for image_url in candidates:

        image_url = clean_image_url(
            image_url,
            article_url
        )

        if not image_url:
            continue

        if image_url in seen:
            continue

        seen.add(image_url)

        final_candidates.append(
            image_url
        )

    logger.info(
        "Found %s image candidates.",
        len(final_candidates)
    )

    for index, image_url in enumerate(
        final_candidates,
        start=1
    ):

        logger.info(
            "Image candidate %s: %s",
            index,
            image_url
        )

    if final_candidates:
        return final_candidates

    return []


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
        "گیمفا را در",
        "ما را دنبال کنید",
    ]

    for word in unwanted:

        if word in text:
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

                if len(paragraphs) > len(best_paragraphs):

                    best_paragraphs = paragraphs

            except Exception as error:

                logger.warning(
                    "Paragraph extraction error: %s",
                    error
                )

    logger.info(
        "Extracted %s valid paragraphs.",
        len(best_paragraphs)
    )

    return best_paragraphs[:2]


# ============================================================
# MARKDOWN ESCAPE
# ============================================================

def md_escape(text):

    if not text:
        return ""

    # برای MarkdownV1
    for character in [
        "\\",
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
# BUILD NEWS CAPTION
# ============================================================

def build_caption(
    title,
    paragraphs,
    url
):

    title = md_escape(title)

    paragraph_1 = md_escape(
        paragraphs[0]
    )

    paragraph_2 = md_escape(
        paragraphs[1]
    )

    # ========================================================
    # قالب نهایی دقیقاً طبق چیزی که خواستی
    # ========================================================

    lines = [
        f"📢 *[{title}]({url})*",
        "",
        f"🟣 {paragraph_1}",
        "",
        f"🟣 {paragraph_2}",
        "",
        f"*[📑 ادامه خبر]({url})*",
        "",
        f"🆔 *{FOOTER}*",
    ]

    return "\n".join(lines)


# ============================================================
# DOWNLOAD IMAGE
# ============================================================

def download_image(
    image_url,
    article_url
):

    logger.info(
        "Trying to download image: %s",
        image_url
    )

    headers = {
        "User-Agent": session.headers["User-Agent"],
        "Accept": (
            "image/avif,image/webp,image/apng,"
            "image/svg+xml,image/*,*/*;q=0.8"
        ),
        "Referer": article_url,
    }

    try:

        response = session.get(
            image_url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            stream=True,
            allow_redirects=True
        )

        response.raise_for_status()

        content_type = (
            response.headers.get(
                "Content-Type",
                ""
            )
            .lower()
        )

        logger.info(
            "Image response: status=%s content-type=%s size=%s",
            response.status_code,
            content_type,
            response.headers.get(
                "Content-Length",
                "unknown"
            )
        )

        # ----------------------------------------------------
        # تشخیص پسوند
        # ----------------------------------------------------

        extension = ".jpg"

        if "png" in content_type:
            extension = ".png"

        elif "webp" in content_type:
            extension = ".webp"

        elif "jpeg" in content_type:
            extension = ".jpg"

        elif "gif" in content_type:
            extension = ".gif"

        else:

            path = urlparse(
                response.url
            ).path.lower()

            if path.endswith(".png"):
                extension = ".png"

            elif path.endswith(".webp"):
                extension = ".webp"

            elif path.endswith(".jpeg"):
                extension = ".jpg"

            elif path.endswith(".jpg"):
                extension = ".jpg"

        # ----------------------------------------------------
        # فایل موقت
        # ----------------------------------------------------

        filename = (
            "gamefa_"
            + uuid.uuid4().hex
            + extension
        )

        filepath = os.path.join(
            TEMP_DIR,
            filename
        )

        total_size = 0

        with open(
            filepath,
            "wb"
        ) as file:

            for chunk in response.iter_content(
                chunk_size=64 * 1024
            ):

                if not chunk:
                    continue

                file.write(chunk)

                total_size += len(chunk)

        # ----------------------------------------------------
        # Validate
        # ----------------------------------------------------

        if total_size < 100:

            try:
                os.remove(filepath)
            except Exception:
                pass

            raise RuntimeError(
                "فایل تصویر خالی یا بسیار کوچک است."
            )

        logger.info(
            "Image downloaded successfully: %s bytes -> %s",
            total_size,
            filepath
        )

        return filepath

    except Exception as error:

        logger.warning(
            "Failed to download image %s: %s",
            image_url,
            error
        )

        return None


# ============================================================
# TRY DOWNLOAD IMAGE CANDIDATES
# ============================================================

def download_best_image(
    image_candidates,
    article_url
):

    if not image_candidates:
        return None

    for index, image_url in enumerate(
        image_candidates,
        start=1
    ):

        logger.info(
            "Trying image candidate %s/%s",
            index,
            len(image_candidates)
        )

        filepath = download_image(
            image_url=image_url,
            article_url=article_url
        )

        if filepath:

            logger.info(
                "Selected image: %s",
                filepath
            )

            return filepath

    logger.error(
        "All image candidates failed."
    )

    return None


# ============================================================
# BALE BOT
# ============================================================

bot = bale.Bot(
    token=BALE_TOKEN
)


# ============================================================
# READY EVENT
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
# SEND PHOTO AS FILE
# ============================================================

async def send_image_file(
    chat_id,
    filepath,
    caption
):

    logger.info(
        "Uploading image to Bale: %s",
        filepath
    )

    # ========================================================
    # نکته مهم:
    #
    # URL را مستقیماً به Bale نمی‌دهیم.
    #
    # فایل دانلود شده را با InputFile ارسال می‌کنیم.
    # ========================================================

    input_file = bale.InputFile(
        filepath
    )

    await bot.send_photo(
        chat_id=chat_id,
        photo=input_file,
        caption=caption,
    )

    logger.info(
        "Image uploaded successfully."
    )


# ============================================================
# MESSAGE EVENT
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
                "📢 عنوان خبر\n"
                "🟣 دو پاراگراف اول\n"
                "🖼 تصویر شاخص\n"
                "📑 لینک ادامه خبر\n"
                "🆔 آیدی کانال\n\n"
                "را آماده می‌کند."
            )

            return

        # ----------------------------------------------------
        # URL
        # ----------------------------------------------------

        url = extract_url(
            content
        )

        if not url:
            return

        # ----------------------------------------------------
        # GAMEFA CHECK
        # ----------------------------------------------------

        if not is_gamefa_url(url):

            await message.reply(
                "❌ فقط لینک‌های سایت Gamefa.com "
                "قابل پردازش هستند."
            )

            return

        # ----------------------------------------------------
        # PROCESSING
        # ----------------------------------------------------

        processing = await message.reply(
            "⏳ در حال دریافت و پردازش خبر از گیمفا..."
        )

        filepath = None

        try:

            # =================================================
            # FETCH ARTICLE
            # =================================================

            soup = fetch_soup(
                url
            )

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
            # IMAGE
            # =================================================

            image_candidates = extract_image(
                soup,
                url
            )

            # =================================================
            # PARAGRAPHS
            # =================================================

            paragraphs = extract_paragraphs(
                soup
            )

            if len(paragraphs) < 2:

                await processing.edit(
                    "❌ نتوانستم دو پاراگراف واقعی "
                    "از مقاله پیدا کنم."
                )

                return

            # =================================================
            # CAPTION
            # =================================================

            caption = build_caption(
                title=title,
                paragraphs=paragraphs,
                url=url
            )

            # =================================================
            # DOWNLOAD IMAGE
            # =================================================

            filepath = download_best_image(
                image_candidates=image_candidates,
                article_url=url
            )

            # =================================================
            # SEND IMAGE
            # =================================================

            if filepath:

                try:

                    await send_image_file(
                        chat_id=user_id,
                        filepath=filepath,
                        caption=caption
                    )

                except Exception as image_error:

                    logger.exception(
                        "Bale image upload failed: %s",
                        image_error
                    )

                    # ----------------------------------------
                    # اگر آپلود Bale شکست خورد
                    # ----------------------------------------

                    await message.reply(
                        "⚠️ ارسال تصویر با مشکل مواجه شد.\n\n"
                        + caption
                    )

            else:

                # =================================================
                # هیچ تصویری از سایت قابل دانلود نبود
                # =================================================

                logger.error(
                    "No downloadable image found."
                )

                await message.reply(
                    "⚠️ تصویر شاخص خبر قابل دریافت نبود.\n\n"
                    + caption
                )

            # =================================================
            # PROCESSING MESSAGE
            # =================================================

            try:

                await processing.edit(
                    "✅ خبر با موفقیت آماده شد."
                )

            except Exception:
                pass

            logger.info(
                "Article processed successfully: %s",
                title
            )

        # =====================================================
        # REQUEST ERROR
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

            if filepath:

                try:

                    if os.path.exists(filepath):

                        os.remove(
                            filepath
                        )

                        logger.info(
                            "Temporary image deleted: %s",
                            filepath
                        )

                except Exception as cleanup_error:

                    logger.warning(
                        "Could not delete temporary file: %s",
                        cleanup_error
                    )

    # =========================================================
    # UNHANDLED MESSAGE ERROR
    # =========================================================

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
    مهم:

    bot.run() خودش event loop را مدیریت می‌کند.

    بنابراین نباید بنویسیم:

        asyncio.run(bot.run())

    یا:

        asyncio.run(run_with_retry())

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
            # اجرای مستقیم Bale
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
                "Bale connection/runtime error: %s | "
                "retrying in %s seconds...",
                error,
                RETRY_DELAY
            )

            time.sleep(
                RETRY_DELAY
            )

        # ----------------------------------------------------
        # RESET
        # ----------------------------------------------------

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
        "Temporary directory: %s",
        TEMP_DIR
    )

    logger.info(
        "========================================"
    )

    try:

        # =====================================================
        # فقط همین:
        #
        # bot.run() خودش event loop می‌سازد.
        # =====================================================

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
