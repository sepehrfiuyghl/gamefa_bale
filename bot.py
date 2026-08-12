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
IMAGE_TIMEOUT = int(os.getenv("IMAGE_TIMEOUT", "60"))

MAX_RETRIES = int(os.getenv("MAX_RETRIES", "10"))
RETRY_DELAY = int(os.getenv("RETRY_DELAY", "15"))

FOOTER = "@Gamefa_official"

MAX_IMAGE_SIZE = 15 * 1024 * 1024


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("gamefa-bale-bot")


# ============================================================
# TOKEN VALIDATION
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

    text = unescape(str(text))
    text = text.replace("\xa0", " ")
    text = text.replace("\u200c", "\u200c")

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ============================================================
# URL HELPERS
# ============================================================

def normalize_url(url, base_url=None):
    if not url:
        return ""

    url = str(url).strip().strip("<>\"'")

    if url.startswith("//"):
        url = "https:" + url

    elif base_url and not url.startswith(("http://", "https://")):
        url = urljoin(base_url, url)

    elif not url.startswith(("http://", "https://")):
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
            ").,؛،!؟"
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
            ").,؛،!؟"
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
    logger.info("Downloading article: %s", url)

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

    title_tag = soup.find("title")

    if title_tag:

        title = clean_text(
            title_tag.get_text(
                " ",
                strip=True
            )
        )

        if title:
            return title

    return "خبر گیمفا"


# ============================================================
# IMAGE URL EXTRACTION
# ============================================================

def get_srcset_best_url(srcset):
    """
    از srcset بهترین/بزرگ‌ترین تصویر را انتخاب می‌کند.
    """

    if not srcset:
        return None

    candidates = []

    for item in srcset.split(","):

        item = item.strip()

        if not item:
            continue

        parts = item.split()

        image_url = parts[0]

        score = 0

        if len(parts) > 1:

            descriptor = parts[1]

            match = re.match(
                r"(\d+)w",
                descriptor
            )

            if match:
                score = int(match.group(1))

            else:

                match = re.match(
                    r"([\d.]+)x",
                    descriptor
                )

                if match:
                    score = int(
                        float(match.group(1)) * 1000
                    )

        candidates.append(
            (score, image_url)
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return candidates[0][1]


def extract_image(soup, page_url):

    # --------------------------------------------------------
    # 1. OpenGraph
    # --------------------------------------------------------

    meta = soup.find(
        "meta",
        property="og:image"
    )

    if meta:

        image = meta.get("content")

        if image:
            image = normalize_url(
                image,
                page_url
            )

            if image:
                logger.info(
                    "Image found from og:image: %s",
                    image
                )

                return image

    # --------------------------------------------------------
    # 2. Twitter
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

            image = normalize_url(
                image,
                page_url
            )

            if image:
                logger.info(
                    "Image found from twitter:image: %s",
                    image
                )

                return image

    # --------------------------------------------------------
    # 3. Article images
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

        images = soup.select(selector)

        for image_tag in images:

            srcset = (
                image_tag.get("srcset")
                or image_tag.get("data-srcset")
                or image_tag.get("data-lazy-srcset")
            )

            image = get_srcset_best_url(
                srcset
            )

            if not image:

                possible_attributes = [
                    "data-lazy-src",
                    "data-src",
                    "data-original",
                    "data-url",
                    "src",
                ]

                for attribute in possible_attributes:

                    value = image_tag.get(
                        attribute
                    )

                    if value:
                        image = value
                        break

            if image:

                image = normalize_url(
                    image,
                    page_url
                )

                if image:

                    logger.info(
                        "Image found from article: %s",
                        image
                    )

                    return image

    logger.warning(
        "No image URL found."
    )

    return None


# ============================================================
# DOWNLOAD IMAGE
# ============================================================

def download_image(image_url, page_url):
    """
    تصویر را از Gamefa دانلود می‌کند
    و مسیر فایل موقت را برمی‌گرداند.
    """

    if not image_url:
        return None

    image_url = normalize_url(
        image_url,
        page_url
    )

    logger.info(
        "Downloading image: %s",
        image_url
    )

    image_headers = {
        "User-Agent": session.headers["User-Agent"],
        "Accept": (
            "image/avif,image/webp,image/apng,"
            "image/svg+xml,image/*,*/*;q=0.8"
        ),
        "Referer": page_url,
    }

    temp_path = None

    try:

        response = session.get(
            image_url,
            headers=image_headers,
            timeout=IMAGE_TIMEOUT,
            stream=True,
            allow_redirects=True
        )

        response.raise_for_status()

        content_type = (
            response.headers
            .get("Content-Type", "")
            .lower()
        )

        logger.info(
            "Image response: status=%s content-type=%s",
            response.status_code,
            content_type
        )

        # ----------------------------------------------------
        # بررسی نوع فایل
        # ----------------------------------------------------

        allowed_types = (
            "image/jpeg",
            "image/jpg",
            "image/png",
            "image/webp",
            "image/gif",
            "image/bmp",
            "image/avif",
        )

        # بعضی سرورها Content-Type اشتباه می‌فرستند.
        # در این حالت همچنان فایل را دانلود می‌کنیم.
        if (
            content_type
            and not content_type.startswith("image/")
            and "octet-stream" not in content_type
        ):

            logger.warning(
                "URL does not look like an image: %s",
                content_type
            )

        # ----------------------------------------------------
        # حجم فایل
        # ----------------------------------------------------

        content_length = response.headers.get(
            "Content-Length"
        )

        if content_length:

            try:

                content_length = int(
                    content_length
                )

                if content_length > MAX_IMAGE_SIZE:

                    logger.warning(
                        "Image too large: %s bytes",
                        content_length
                    )

                    return None

            except ValueError:
                pass

        # ----------------------------------------------------
        # تعیین پسوند
        # ----------------------------------------------------

        extension = ".jpg"

        if "png" in content_type:
            extension = ".png"

        elif "webp" in content_type:
            extension = ".webp"

        elif "gif" in content_type:
            extension = ".gif"

        elif "bmp" in content_type:
            extension = ".bmp"

        elif "avif" in content_type:
            extension = ".avif"

        else:

            path = urlparse(
                response.url
            ).path.lower()

            for ext in (
                ".jpg",
                ".jpeg",
                ".png",
                ".webp",
                ".gif",
                ".bmp",
                ".avif",
            ):

                if path.endswith(ext):

                    extension = ext
                    break

        # ----------------------------------------------------
        # ساخت فایل موقت
        # ----------------------------------------------------

        fd, temp_path = tempfile.mkstemp(
            suffix=extension
        )

        os.close(fd)

        total_size = 0

        with open(
            temp_path,
            "wb"
        ) as file:

            for chunk in response.iter_content(
                chunk_size=64 * 1024
            ):

                if not chunk:
                    continue

                total_size += len(chunk)

                if total_size > MAX_IMAGE_SIZE:

                    logger.warning(
                        "Image exceeded maximum size."
                    )

                    file.close()

                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass

                    return None

                file.write(chunk)

        # ----------------------------------------------------
        # بررسی اینکه فایل واقعاً خالی نباشد
        # ----------------------------------------------------

        if not os.path.exists(temp_path):

            return None

        file_size = os.path.getsize(
            temp_path
        )

        if file_size < 100:

            logger.warning(
                "Downloaded image is too small."
            )

            try:
                os.remove(temp_path)
            except Exception:
                pass

            return None

        logger.info(
            "Image downloaded successfully: %s bytes -> %s",
            file_size,
            temp_path
        )

        return temp_path

    except Exception as error:

        logger.warning(
            "Image download failed: %s",
            error
        )

        if temp_path:

            try:
                os.remove(temp_path)
            except Exception:
                pass

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

        for element in container.select(
            selector
        ):

            element.decompose()


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
        "بیشتر بخوانید",
        "کپی لینک",
        "منبع",
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

        containers = soup.select(
            selector
        )

        for container in containers:

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

                if not valid_paragraph(
                    text
                ):
                    continue

                if text in paragraphs:
                    continue

                paragraphs.append(
                    text
                )

            if len(paragraphs) > len(
                best_paragraphs
            ):

                best_paragraphs = paragraphs

    return best_paragraphs[:2]


# ============================================================
# MARKDOWN ESCAPE
# ============================================================

def md_escape(text):

    if not text:
        return ""

    # ترتیب مهم است
    text = text.replace(
        "\\",
        "\\\\"
    )

    text = text.replace(
        "_",
        "\\_"
    )

    text = text.replace(
        "*",
        "\\*"
    )

    text = text.replace(
        "`",
        "\\`"
    )

    # برای اینکه [عنوان] لینک خراب نشود،
    # در عنوان فقط کاراکترهای مشکل‌ساز را کنترل می‌کنیم.
    text = text.replace(
        "[",
        "\\["
    )

    text = text.replace(
        "]",
        "\\]"
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

    title = md_escape(
        title
    )

    paragraph_1 = md_escape(
        paragraphs[0]
    )

    paragraph_2 = md_escape(
        paragraphs[1]
    )

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

    return "\n".join(
        lines
    )


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
        # BOT MESSAGE
        # ----------------------------------------------------

        if getattr(
            user,
            "is_bot",
            False
        ):

            return

        # ----------------------------------------------------
        # ADMIN CHECK
        # ----------------------------------------------------

        if not is_admin(
            user_id
        ):

            return

        # ----------------------------------------------------
        # MESSAGE CONTENT
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
                "🖼 تصویر خبر\n"
                "📑 لینک ادامه خبر\n"
                "🆔 آیدی کانال\n\n"
                "را آماده می‌کند."
            )

            return

        # ----------------------------------------------------
        # EXTRACT URL
        # ----------------------------------------------------

        url = extract_url(
            content
        )

        if not url:
            return

        # ----------------------------------------------------
        # GAMEFA URL CHECK
        # ----------------------------------------------------

        if not is_gamefa_url(
            url
        ):

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

        image_path = None

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
            # IMAGE URL
            # =================================================

            image_url = extract_image(
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

                try:

                    await processing.edit(
                        "❌ نتوانستم دو پاراگراف واقعی "
                        "از مقاله پیدا کنم."
                    )

                except Exception:
                    pass

                return

            # =================================================
            # BUILD CAPTION
            # =================================================

            caption = build_caption(
                title=title,
                paragraphs=paragraphs,
                url=url
            )

            # =================================================
            # DOWNLOAD IMAGE
            # =================================================

            if image_url:

                image_path = download_image(
                    image_url=image_url,
                    page_url=url
                )

            # =================================================
            # SEND IMAGE
            # =================================================

            sent_image = False

            if image_path:

                try:

                    logger.info(
                        "Uploading downloaded image to Bale..."
                    )

                    # -----------------------------------------
                    # تلاش اول:
                    # ارسال فایل محلی
                    # -----------------------------------------

                    await bot.send_photo(
                        chat_id=user_id,
                        photo=image_path,
                        caption=caption,
                    )

                    sent_image = True

                    logger.info(
                        "Image sent successfully."
                    )

                except Exception as image_error:

                    logger.exception(
                        "Bale image upload failed: %s",
                        image_error
                    )

                    sent_image = False

            # =================================================
            # FALLBACK TEXT ONLY
            # =================================================

            if not sent_image:

                logger.warning(
                    "Sending text-only news because image "
                    "could not be uploaded."
                )

                await message.reply(
                    caption
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

        finally:

            # =================================================
            # DELETE TEMP IMAGE
            # =================================================

            if image_path:

                try:

                    if os.path.exists(
                        image_path
                    ):

                        os.remove(
                            image_path
                        )

                        logger.info(
                            "Temporary image deleted."
                        )

                except Exception as cleanup_error:

                    logger.warning(
                        "Could not delete temporary image: %s",
                        cleanup_error
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
    بنابراین bot.run() مستقیماً اجرا می‌شود.
    """

    attempt = 0

    while True:

        attempt += 1

        try:

            logger.info(
                "Starting Bale bot (attempt %s)...",
                attempt
            )

            # مهم:
            # اینجا asyncio.run() استفاده نمی‌شود.

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

        if attempt >= MAX_RETRIES:

            logger.warning(
                "Reached MAX_RETRIES=%s. "
                "Resetting retry counter.",
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
