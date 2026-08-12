import os
import re
import time
import logging
import mimetypes
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
IMAGE_TIMEOUT = int(os.getenv("IMAGE_TIMEOUT", "40"))

MAX_RETRIES = int(os.getenv("MAX_RETRIES", "10"))
RETRY_DELAY = int(os.getenv("RETRY_DELAY", "15"))

IMAGE_RETRIES = int(os.getenv("IMAGE_RETRIES", "3"))

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
    text = text.strip()

    # فاصله‌های اضافی
    text = re.sub(r"[ \t]+", " ", text)

    # خط‌های خالی اضافی
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text


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

    # URL کامل
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

    # URL بدون https
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
    response = session.get(
        url,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True
    )

    response.raise_for_status()

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
# IMAGE URL NORMALIZER
# ============================================================

def normalize_image_url(image_url, article_url):
    if not image_url:
        return None

    image_url = unescape(
        image_url.strip()
    )

    # حذف URLهای غیرتصویری
    if image_url.startswith(
        ("data:", "blob:")
    ):
        return None

    # URL نسبی
    image_url = urljoin(
        article_url,
        image_url
    )

    return image_url


# ============================================================
# IMAGE EXTRACTION
# ============================================================

def extract_image_urls(soup, article_url):

    candidates = []

    def add_candidate(url):

        if not url:
            return

        url = normalize_image_url(
            url,
            article_url
        )

        if not url:
            return

        if url not in candidates:
            candidates.append(url)

    # --------------------------------------------------------
    # 1. OpenGraph
    # --------------------------------------------------------

    meta = soup.find(
        "meta",
        property="og:image"
    )

    if meta:
        add_candidate(
            meta.get("content")
        )

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
        add_candidate(
            meta.get("content")
        )

    # --------------------------------------------------------
    # 3. OpenGraph image secure_url
    # --------------------------------------------------------

    for meta in soup.find_all(
        "meta",
        property="og:image:secure_url"
    ):
        add_candidate(
            meta.get("content")
        )

    # --------------------------------------------------------
    # 4. Article images
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

        for image in soup.select(selector):

            # اولویت src
            sources = [
                image.get("src"),
                image.get("data-src"),
                image.get("data-lazy-src"),
                image.get("data-original"),
                image.get("data-url"),
            ]

            # srcset
            srcset = image.get("srcset")

            if srcset:
                parts = [
                    x.strip().split(" ")[0]
                    for x in srcset.split(",")
                    if x.strip()
                ]

                # بزرگ‌ترین/آخرین گزینه
                sources.extend(
                    reversed(parts)
                )

            for source in sources:
                add_candidate(source)

    return candidates


def extract_image(soup, article_url):

    urls = extract_image_urls(
        soup,
        article_url
    )

    if urls:
        return urls[0]

    return None


# ============================================================
# DOWNLOAD IMAGE
# ============================================================

def download_image(image_url):
    """
    تصویر را از URL دانلود می‌کند و bytes برمی‌گرداند.

    Bale برای آپلود فایل می‌تواند از bale.InputFile
    استفاده کند؛ بنابراین URL را مستقیماً به send_photo
    نمی‌دهیم.
    """

    if not image_url:
        return None, None

    last_error = None

    for attempt in range(
        1,
        IMAGE_RETRIES + 1
    ):

        try:

            logger.info(
                "Downloading image "
                "(attempt %s/%s): %s",
                attempt,
                IMAGE_RETRIES,
                image_url
            )

            response = session.get(
                image_url,
                timeout=IMAGE_TIMEOUT,
                allow_redirects=True,
                stream=True,
                headers={
                    "User-Agent": session.headers["User-Agent"],
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    "Referer": "https://gamefa.com/",
                }
            )

            response.raise_for_status()

            content_type = (
                response.headers.get(
                    "Content-Type",
                    ""
                )
                .lower()
            )

            data = response.content

            if not data:
                raise ValueError(
                    "تصویر دانلود شد اما فایل خالی است."
                )

            # ------------------------------------------------
            # حجم
            # ------------------------------------------------

            size_mb = len(data) / (
                1024 * 1024
            )

            logger.info(
                "Downloaded image: %.2f MB | Content-Type: %s",
                size_mb,
                content_type
            )

            # ------------------------------------------------
            # تشخیص فرمت
            # ------------------------------------------------

            extension = None

            if "jpeg" in content_type or "jpg" in content_type:
                extension = ".jpg"

            elif "png" in content_type:
                extension = ".png"

            elif "webp" in content_type:
                extension = ".webp"

            elif "gif" in content_type:
                extension = ".gif"

            else:
                path = urlparse(
                    response.url
                ).path.lower()

                for ext in (
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".webp",
                    ".gif"
                ):
                    if ext in path:
                        extension = ext
                        break

            # ------------------------------------------------
            # Magic bytes
            # ------------------------------------------------

            if data.startswith(b"\xff\xd8\xff"):
                extension = ".jpg"

            elif data.startswith(b"\x89PNG"):
                extension = ".png"

            elif data.startswith(b"RIFF") and b"WEBP" in data[:16]:
                extension = ".webp"

            elif data.startswith(b"GIF8"):
                extension = ".gif"

            if not extension:
                extension = ".jpg"

            # ------------------------------------------------
            # بررسی اینکه واقعاً تصویر باشد
            # ------------------------------------------------

            image_signatures = (
                b"\xff\xd8\xff",
                b"\x89PNG",
                b"RIFF",
                b"GIF8",
            )

            looks_like_image = (
                data.startswith(
                    image_signatures
                )
            )

            if not looks_like_image:

                # بعضی سرورها Content-Type درست می‌فرستند
                # اما signature متفاوت است؛ اگر content-type
                # تصویر بود اجازه می‌دهیم Bale آن را بررسی کند.

                if not content_type.startswith("image/"):

                    raise ValueError(
                        "فایل دریافت‌شده تصویر نیست."
                    )

            return data, extension

        except Exception as error:

            last_error = error

            logger.warning(
                "Image download failed "
                "(attempt %s/%s): %s",
                attempt,
                IMAGE_RETRIES,
                error
            )

            if attempt < IMAGE_RETRIES:
                time.sleep(1)

    logger.error(
        "Could not download image: %s",
        last_error
    )

    return None, None


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

                if not valid_paragraph(text):
                    continue

                if text in paragraphs:
                    continue

                paragraphs.append(text)

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

    # فقط کاراکترهایی که در Markdown
    # ساختار لینک/ایتالیک را خراب می‌کنند.
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
    # قالب دقیق موردنظر
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
# BUILD IMAGE FAILURE MESSAGE
# ============================================================

def build_image_failure_message(
    caption
):

    return (
        "⚠️ ارسال تصویر با مشکل مواجه شد.\n\n"
        + caption
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
# SEND TEXT
# ============================================================

async def send_text_message(
    message,
    text
):

    try:

        return await message.reply(
            text
        )

    except Exception as error:

        logger.exception(
            "Failed to send text message: %s",
            error
        )

        raise


# ============================================================
# SEND IMAGE
# ============================================================

async def send_image_message(
    chat_id,
    image_bytes,
    extension,
    caption
):
    """
    ارسال واقعی فایل تصویر به Bale.

    به جای ارسال URL، فایل را به InputFile می‌دهیم.
    """

    # --------------------------------------------------------
    # تلاش اول: InputFile با bytes
    # --------------------------------------------------------

    try:

        logger.info(
            "Trying Bale InputFile(bytes)..."
        )

        input_file = bale.InputFile(
            image_bytes
        )

        result = await bot.send_photo(
            chat_id=chat_id,
            photo=input_file,
            caption=caption,
        )

        logger.info(
            "Image sent successfully using InputFile(bytes)."
        )

        return result

    except Exception as first_error:

        logger.warning(
            "InputFile(bytes) failed: %s",
            first_error
        )

    # --------------------------------------------------------
    # تلاش دوم: فایل موقت
    # --------------------------------------------------------

    temp_path = None

    try:

        import tempfile

        suffix = extension or ".jpg"

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix
        ) as temp_file:

            temp_file.write(
                image_bytes
            )

            temp_path = temp_file.name

        logger.info(
            "Trying Bale InputFile(path): %s",
            temp_path
        )

        input_file = bale.InputFile(
            temp_path
        )

        result = await bot.send_photo(
            chat_id=chat_id,
            photo=input_file,
            caption=caption,
        )

        logger.info(
            "Image sent successfully using temporary file."
        )

        return result

    except Exception as second_error:

        logger.exception(
            "Temporary-file image sending failed: %s",
            second_error
        )

        raise

    finally:

        if temp_path:

            try:

                if os.path.exists(
                    temp_path
                ):
                    os.remove(
                        temp_path
                    )

            except Exception as cleanup_error:

                logger.warning(
                    "Could not remove temporary image: %s",
                    cleanup_error
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
        # ADMIN
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
                "📰 عنوان خبر\n"
                "🟣 دو پاراگراف اول\n"
                "🖼 تصویر شاخص\n"
                "🔗 لینک ادامه خبر\n"
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

        try:

            # ------------------------------------------------
            # FETCH
            # ------------------------------------------------

            logger.info(
                "Fetching article: %s",
                url
            )

            soup = fetch_soup(
                url
            )

            # ------------------------------------------------
            # TITLE
            # ------------------------------------------------

            title = extract_title(
                soup
            )

            logger.info(
                "Title: %s",
                title
            )

            # ------------------------------------------------
            # IMAGE URLS
            # ------------------------------------------------

            image_urls = extract_image_urls(
                soup,
                url
            )

            logger.info(
                "Found %s possible image(s).",
                len(image_urls)
            )

            # ------------------------------------------------
            # PARAGRAPHS
            # ------------------------------------------------

            paragraphs = extract_paragraphs(
                soup
            )

            # ------------------------------------------------
            # VALIDATE
            # ------------------------------------------------

            if len(paragraphs) < 2:

                await processing.edit(
                    "❌ نتوانستم دو پاراگراف واقعی "
                    "از مقاله پیدا کنم.\n\n"
                    "ممکن است ساختار این صفحه با سایر "
                    "مقالات گیمفا متفاوت باشد."
                )

                return

            # ------------------------------------------------
            # CAPTION
            # ------------------------------------------------

            caption = build_caption(
                title=title,
                paragraphs=paragraphs,
                url=url
            )

            # ------------------------------------------------
            # IMAGE SEND
            # ------------------------------------------------

            image_sent = False

            # برای هر URL تصویر تلاش می‌کنیم.
            for index, image_url in enumerate(
                image_urls,
                start=1
            ):

                logger.info(
                    "Trying image %s/%s: %s",
                    index,
                    len(image_urls),
                    image_url
                )

                image_bytes, extension = (
                    download_image(
                        image_url
                    )
                )

                if not image_bytes:
                    continue

                try:

                    await send_image_message(
                        chat_id=user_id,
                        image_bytes=image_bytes,
                        extension=extension,
                        caption=caption
                    )

                    image_sent = True

                    logger.info(
                        "Image sent successfully."
                    )

                    break

                except Exception as image_error:

                    logger.warning(
                        "Could not send image %s: %s",
                        image_url,
                        image_error
                    )

                    continue

            # ------------------------------------------------
            # IF IMAGE FAILED
            # ------------------------------------------------

            if not image_sent:

                logger.warning(
                    "All image sending attempts failed."
                )

                await send_text_message(
                    message,
                    build_image_failure_message(
                        caption
                    )
                )

            # ------------------------------------------------
            # PROCESSING MESSAGE
            # ------------------------------------------------

            try:

                await processing.edit(
                    "✅ خبر با موفقیت آماده شد."
                )

            except Exception as edit_error:

                logger.warning(
                    "Could not edit processing message: %s",
                    edit_error
                )

            # ------------------------------------------------
            # SUCCESS
            # ------------------------------------------------

            logger.info(
                "Article processed successfully: %s",
                title
            )

        # ----------------------------------------------------
        # HTTP ERROR
        # ----------------------------------------------------

        except requests.RequestException as error:

            logger.exception(
                "HTTP error while processing article: %s",
                error
            )

            try:

                await processing.edit(
                    "❌ هنگام دریافت مقاله از گیمفا "
                    "خطای شبکه رخ داد.\n\n"
                    f"جزئیات: {error}"
                )

            except Exception:
                pass

        # ----------------------------------------------------
        # GENERAL PROCESSING ERROR
        # ----------------------------------------------------

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

    # --------------------------------------------------------
    # MESSAGE HANDLER ERROR
    # --------------------------------------------------------

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
    اجرای Bale.

    نکته مهم:
    bot.run() خودش event loop داخلی Bale را مدیریت می‌کند.

    بنابراین:
        asyncio.run(bot.run())
    یا:
        asyncio.run(run_with_retry())
    نباید استفاده شود.
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
            # مهم:
            # bot.run() مستقیماً اجرا می‌شود.
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

        except KeyboardInterrupt:

            logger.info(
                "Bot stopped by user."
            )

            break

        except (
            OSError,
            ConnectionError,
            TimeoutError,
        ) as error:

            logger.warning(
                "Network error: %s | "
                "retrying in %s seconds...",
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
        # RESET RETRY COUNTER
        # ----------------------------------------------------

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

        # =====================================================
        # بسیار مهم:
        #
        # اینجا asyncio.run() نداریم.
        #
        # Bale خودش event loop را مدیریت می‌کند.
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
