import os
import re
import time
import logging
import tempfile
from io import BytesIO
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

DOWNLOAD_DIR = os.getenv(
    "DOWNLOAD_DIR",
    "/tmp/gamefa_images"
)

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
# VALIDATE TOKEN
# ============================================================

if not BALE_TOKEN:
    raise RuntimeError(
        "BALE_TOKEN در متغیرهای محیطی Railway یا فایل .env تنظیم نشده است."
    )


# ============================================================
# CREATE DOWNLOAD DIRECTORY
# ============================================================

os.makedirs(
    DOWNLOAD_DIR,
    exist_ok=True
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
        "q=0.9,image/avif,image/webp,image/apng,*/*;"
        "q=0.8"
    ),
    "Accept-Language": (
        "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7"
    ),
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

    text = unescape(text)

    text = text.replace(
        "\xa0",
        " "
    )

    text = text.strip()

    text = re.sub(
        r"[ \t]+",
        " ",
        text
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

    return text


# ============================================================
# URL NORMALIZATION
# ============================================================

def normalize_url(url):

    if not url:
        return ""

    url = url.strip().strip("<>")

    if not url.startswith(
        ("http://", "https://")
    ):
        url = "https://" + url

    return url


# ============================================================
# EXTRACT URL
# ============================================================

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


# ============================================================
# GAMEFA URL CHECK
# ============================================================

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

        response.encoding = (
            response.apparent_encoding
        )

    return BeautifulSoup(
        response.text,
        "html.parser"
    )


# ============================================================
# EXTRACT TITLE
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

        element = soup.select_one(
            selector
        )

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
            meta.get(
                "content",
                ""
            )
        )

        if title:
            return title

    return "خبر گیمفا"


# ============================================================
# IMAGE URL NORMALIZER
# ============================================================

def normalize_image_url(
    image_url,
    article_url
):

    if not image_url:
        return None

    image_url = image_url.strip()

    image_url = image_url.replace(
        "&amp;",
        "&"
    )

    if image_url.startswith(
        "//"
    ):

        image_url = (
            "https:" + image_url
        )

    elif image_url.startswith(
        "/"
    ):

        image_url = urljoin(
            article_url,
            image_url
        )

    elif not image_url.startswith(
        ("http://", "https://")
    ):

        image_url = urljoin(
            article_url,
            image_url
        )

    return image_url


# ============================================================
# EXTRACT IMAGE URL
# ============================================================

def extract_image(
    soup,
    article_url
):

    # --------------------------------------------------------
    # 1. OpenGraph
    # --------------------------------------------------------

    og_image = soup.find(
        "meta",
        property="og:image"
    )

    if og_image:

        image = og_image.get(
            "content"
        )

        if image:

            return normalize_image_url(
                image,
                article_url
            )

    # --------------------------------------------------------
    # 2. og:image:url
    # --------------------------------------------------------

    og_image_url = soup.find(
        "meta",
        property="og:image:url"
    )

    if og_image_url:

        image = og_image_url.get(
            "content"
        )

        if image:

            return normalize_image_url(
                image,
                article_url
            )

    # --------------------------------------------------------
    # 3. Twitter image
    # --------------------------------------------------------

    twitter = soup.find(
        "meta",
        attrs={
            "name": "twitter:image"
        }
    )

    if twitter:

        image = twitter.get(
            "content"
        )

        if image:

            return normalize_image_url(
                image,
                article_url
            )

    # --------------------------------------------------------
    # 4. Twitter image src
    # --------------------------------------------------------

    twitter2 = soup.find(
        "meta",
        attrs={
            "property": "twitter:image"
        }
    )

    if twitter2:

        image = twitter2.get(
            "content"
        )

        if image:

            return normalize_image_url(
                image,
                article_url
            )

    # --------------------------------------------------------
    # 5. Article images
    # --------------------------------------------------------

    selectors = [

        "article img",

        ".entry-content img",

        ".post-content img",

        ".article-content img",

        ".single-content img",

        ".td-post-content img",

        ".content-area img",

        "main img",

    ]

    for selector in selectors:

        images = soup.select(
            selector
        )

        for image in images:

            candidates = [

                image.get("src"),

                image.get("data-src"),

                image.get("data-lazy-src"),

                image.get("data-original"),

                image.get("data-image"),

                image.get("data-url"),

            ]

            # srcset
            srcset = image.get(
                "srcset"
            )

            if srcset:

                parts = srcset.split(",")

                for part in parts:

                    part = part.strip()

                    if part:

                        candidates.append(
                            part.split()[0]
                        )

            for candidate in candidates:

                if not candidate:
                    continue

                candidate = normalize_image_url(
                    candidate,
                    article_url
                )

                if not candidate:
                    continue

                # حذف تصاویر خیلی کوچک / آیکون‌ها
                lower = candidate.lower()

                bad_words = [
                    "logo",
                    "avatar",
                    "icon",
                    "favicon",
                    "emoji",
                    "gravatar",
                    "loader",
                    "loading",
                ]

                if any(
                    word in lower
                    for word in bad_words
                ):
                    continue

                return candidate

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
# VALID PARAGRAPH
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

            remove_unwanted(
                copy
            )

            paragraphs = []

            for paragraph in copy.find_all(
                "p"
            ):

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

                best_paragraphs = (
                    paragraphs
                )

    return best_paragraphs[:2]


# ============================================================
# MARKDOWN ESCAPE
# ============================================================

def md_escape(text):

    if not text:
        return ""

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
# BUILD CAPTION
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

    return (
        f"📢 *[{title}]({url})*\n\n"
        f"🟣 {paragraph_1}\n\n"
        f"🟣 {paragraph_2}\n\n"
        f"*[📑 ادامه خبر]({url})*\n\n"
        f"🆔 *{FOOTER}*"
    )


# ============================================================
# DOWNLOAD IMAGE
# ============================================================

def download_image(
    image_url,
    article_url
):

    if not image_url:
        return None

    logger.info(
        "Downloading image: %s",
        image_url
    )

    headers = {

        "User-Agent": session.headers.get(
            "User-Agent"
        ),

        "Referer": article_url,

        "Accept": (
            "image/avif,image/webp,"
            "image/apng,image/svg+xml,"
            "image/*,*/*;q=0.8"
        ),

    }

    response = session.get(
        image_url,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
        stream=True
    )

    response.raise_for_status()

    content_type = (
        response.headers.get(
            "Content-Type",
            ""
        ).lower()
    )

    logger.info(
        "Image HTTP status: %s",
        response.status_code
    )

    logger.info(
        "Image content type: %s",
        content_type
    )

    # --------------------------------------------------------
    # Read image safely
    # --------------------------------------------------------

    data = bytearray()

    for chunk in response.iter_content(
        chunk_size=64 * 1024
    ):

        if not chunk:
            continue

        data.extend(
            chunk
        )

        if len(data) > MAX_IMAGE_SIZE:

            raise ValueError(
                "حجم تصویر بیشتر از "
                f"{MAX_IMAGE_SIZE // (1024 * 1024)}MB است."
            )

    if not data:

        raise ValueError(
            "سرور گیمفا فایل تصویر خالی ارسال کرد."
        )

    raw = bytes(data)

    # --------------------------------------------------------
    # Validate content
    # --------------------------------------------------------

    if (
        "text/html" in content_type
        or "application/json" in content_type
    ):

        # بعضی CDNها Content-Type اشتباه می‌دهند
        # بنابراین فقط در صورت HTML واقعی رد می‌کنیم.

        beginning = raw[:500].lower()

        if (
            b"<html" in beginning
            or b"<!doctype" in beginning
        ):

            raise ValueError(
                "لینک تصویر به جای تصویر، "
                "صفحه HTML برگرداند."
            )

    # --------------------------------------------------------
    # Detect extension
    # --------------------------------------------------------

    extension = ".jpg"

    if (
        "png" in content_type
    ):
        extension = ".png"

    elif (
        "webp" in content_type
    ):
        extension = ".webp"

    elif (
        "gif" in content_type
    ):
        extension = ".gif"

    elif (
        "jpeg" in content_type
        or "jpg" in content_type
    ):
        extension = ".jpg"

    else:

        path = urlparse(
            image_url
        ).path.lower()

        if path.endswith(".png"):
            extension = ".png"

        elif path.endswith(".webp"):
            extension = ".webp"

        elif path.endswith(".gif"):
            extension = ".gif"

        elif path.endswith(
            (".jpeg", ".jpg")
        ):
            extension = ".jpg"

    # --------------------------------------------------------
    # Save temporary image
    # --------------------------------------------------------

    temp_file = tempfile.NamedTemporaryFile(
        prefix="gamefa_",
        suffix=extension,
        dir=DOWNLOAD_DIR,
        delete=False
    )

    temp_path = temp_file.name

    try:

        temp_file.write(
            raw
        )

        temp_file.flush()

    finally:

        temp_file.close()

    logger.info(
        "Image downloaded successfully: %s bytes | %s",
        len(raw),
        temp_path
    )

    # --------------------------------------------------------
    # Check actual image with Pillow if available
    # --------------------------------------------------------

    try:

        from PIL import Image

        with Image.open(
            temp_path
        ) as img:

            logger.info(
                "Detected image format: %s | size=%s",
                img.format,
                img.size
            )

            # ------------------------------------------------
            # Bale-compatible JPG conversion
            # ------------------------------------------------

            if img.format in (
                "WEBP",
                "AVIF",
            ):

                converted_path = (
                    temp_path + ".jpg"
                )

                rgb = img.convert(
                    "RGB"
                )

                rgb.save(
                    converted_path,
                    "JPEG",
                    quality=95
                )

                try:
                    os.remove(
                        temp_path
                    )
                except Exception:
                    pass

                temp_path = (
                    converted_path
                )

                logger.info(
                    "Image converted to JPG: %s",
                    temp_path
                )

    except ImportError:

        logger.warning(
            "Pillow is not installed. "
            "Using original image."
        )

    except Exception as image_check_error:

        logger.warning(
            "Could not inspect image: %s",
            image_check_error
        )

    return temp_path


# ============================================================
# SEND IMAGE TO BALE
# ============================================================

async def send_downloaded_image(
    chat_id,
    image_path,
    caption
):

    logger.info(
        "Uploading image to Bale: %s",
        image_path
    )

    if not os.path.exists(
        image_path
    ):

        raise FileNotFoundError(
            f"Image file does not exist: {image_path}"
        )

    file_size = os.path.getsize(
        image_path
    )

    logger.info(
        "Upload file size: %s bytes",
        file_size
    )

    if file_size <= 0:

        raise ValueError(
            "فایل تصویر خالی است."
        )

    # ========================================================
    # IMPORTANT
    # ========================================================
    #
    # تصویر دیگر به صورت URL به Bale داده نمی‌شود.
    #
    # فایل را با open می‌خوانیم و با InputFile
    # مستقیماً برای Bale آپلود می‌کنیم.
    #
    # ========================================================

    with open(
        image_path,
        "rb"
    ) as image_file:

        image_bytes = (
            image_file.read()
        )

    input_file = bale.InputFile(
        image_bytes,
        file_name="gamefa.jpg"
    )

    result = await bot.send_photo(
        chat_id=chat_id,
        photo=input_file,
        caption=caption
    )

    logger.info(
        "Image uploaded to Bale successfully."
    )

    return result


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
        # ADMIN
        # ----------------------------------------------------

        if not is_admin(
            user_id
        ):

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

        if not is_gamefa_url(
            url
        ):

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

        image_path = None

        try:

            # =================================================
            # FETCH ARTICLE
            # =================================================

            logger.info(
                "Fetching article: %s",
                url
            )

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

            logger.info(
                "Detected image URL: %s",
                image_url
            )

            # =================================================
            # PARAGRAPHS
            # =================================================

            paragraphs = extract_paragraphs(
                soup
            )

            if len(
                paragraphs
            ) < 2:

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

            if image_url:

                try:

                    image_path = download_image(
                        image_url=image_url,
                        article_url=url
                    )

                except Exception as download_error:

                    logger.exception(
                        "Image download failed: %s",
                        download_error
                    )

                    image_path = None

            # =================================================
            # SEND IMAGE
            # =================================================

            if image_path:

                try:

                    await send_downloaded_image(
                        chat_id=user_id,
                        image_path=image_path,
                        caption=caption
                    )

                    # -----------------------------------------
                    # IMAGE SUCCESS
                    # -----------------------------------------

                    logger.info(
                        "News + image sent successfully."
                    )

                except Exception as upload_error:

                    logger.exception(
                        "Bale image upload failed: %s",
                        upload_error
                    )

                    # -----------------------------------------
                    # IMPORTANT:
                    #
                    # فقط در صورت شکست واقعی ارسال تصویر،
                    # هشدار را نشان می‌دهیم.
                    # -----------------------------------------

                    await message.reply(
                        "⚠️ ارسال تصویر با مشکل مواجه شد.\n\n"
                        + caption
                    )

            else:

                # =================================================
                # NO IMAGE
                # =================================================

                logger.warning(
                    "No downloadable image was found."
                )

                await message.reply(
                    "⚠️ تصویر شاخص مقاله پیدا نشد.\n\n"
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
                    "خطای شبکه رخ داد.\n\n"
                    f"{error}"
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
        # DELETE TEMP IMAGE
        # =====================================================

        finally:

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

    attempt = 0

    while True:

        attempt += 1

        try:

            logger.info(
                "Starting Bale bot (attempt %s)...",
                attempt
            )

            # =================================================
            # DO NOT USE asyncio.run()
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

        # ----------------------------------------------------
        # IMPORTANT
        # ----------------------------------------------------
        #
        # اینجا هم asyncio.run() ممنوع است.
        #
        # Bale خودش event loop را مدیریت می‌کند.
        #
        # ----------------------------------------------------

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
