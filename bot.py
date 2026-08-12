import os
import re
import time
import logging
from html import unescape
from urllib.parse import urlparse

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

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
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
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
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

    # حذف فاصله‌های اضافی
    text = re.sub(r"[ \t]+", " ", text)

    # حذف چند خط خالی پشت سر هم
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

    # لینک کامل
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

    # لینک بدون https
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
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    # تشخیص encoding
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

    # عنوان پیش‌فرض
    return "خبر گیمفا"


# ============================================================
# IMAGE
# ============================================================

def extract_image(soup):

    # OpenGraph
    meta = soup.find(
        "meta",
        property="og:image"
    )

    if meta:

        image = meta.get("content")

        if image:
            return image.strip()

    # Twitter image
    meta = soup.find(
        "meta",
        attrs={
            "name": "twitter:image"
        }
    )

    if meta:

        image = meta.get("content")

        if image:
            return image.strip()

    # تصاویر مقاله
    selectors = [
        "article img",
        ".entry-content img",
        ".post-content img",
        ".article-content img",
        "main img",
    ]

    for selector in selectors:

        image = soup.select_one(selector)

        if image:

            src = (
                image.get("src")
                or image.get("data-src")
                or image.get("data-lazy-src")
                or image.get("data-original")
            )

            if src:
                return src.strip()

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

        containers = soup.select(selector)

        for container in containers:

            # ساخت کپی برای جلوگیری از تغییر soup اصلی
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

    lines = [

        f"📢 *[{title}]({url})*",

        "",

        f"🟣 {paragraph_1}",

        "",

        f"🟣 {paragraph_2}",

        "",

        f"*[ادامه خبر📑]({url})*",

        "",

        f"🆔 *{FOOTER}*",
    ]

    return "\n".join(lines)


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

        if not is_admin(user_id):

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

        url = extract_url(content)

        if not url:

            return

        # ----------------------------------------------------
        # GAMEFA URL CHECK
        # ----------------------------------------------------

        if not is_gamefa_url(url):

            await message.reply(
                "❌ فقط لینک‌های سایت Gamefa.com "
                "قابل پردازش هستند."
            )

            return

        # ----------------------------------------------------
        # PROCESSING MESSAGE
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

            soup = fetch_soup(url)

            # ------------------------------------------------
            # TITLE
            # ------------------------------------------------

            title = extract_title(
                soup
            )

            # ------------------------------------------------
            # IMAGE
            # ------------------------------------------------

            image_url = extract_image(
                soup
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
            # BUILD CAPTION
            # ------------------------------------------------

            caption = build_caption(
                title=title,
                paragraphs=paragraphs,
                url=url
            )

            # ------------------------------------------------
            # SEND IMAGE + TEXT
            # ------------------------------------------------

            if image_url:

                logger.info(
                    "Sending image: %s",
                    image_url
                )

                try:

                    await bot.send_photo(
                        chat_id=user_id,
                        photo=image_url,
                        caption=caption,
                    )

                except Exception as image_error:

                    # اگر Bale نتوانست URL تصویر را مستقیماً
                    # دریافت کند، متن را حداقل ارسال می‌کنیم.

                    logger.warning(
                        "Image sending failed: %s",
                        image_error
                    )

                    await message.reply(
                        "⚠️ ارسال تصویر با مشکل مواجه شد.\n\n"
                        + caption
                    )

            else:

                logger.warning(
                    "No featured image found."
                )

                await message.reply(
                    caption
                )

            # ------------------------------------------------
            # PROCESSING MESSAGE
            # ------------------------------------------------

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
    اجرای اصلی ربات Bale.

    نکته بسیار مهم:

    bale.Bot.run()
    خودش داخل کتابخانه Bale از asyncio.run()
    استفاده می‌کند.

    بنابراین نباید این تابع را داخل:
        asyncio.run()
    یا:
        async def
    اجرا کنیم.

    این تابع کاملاً synchronous است و Bot.run()
    مالک event loop خودش خواهد بود.
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
            # مهم‌ترین قسمت اصلاح
            # =================================================
            #
            # اینجا دیگر:
            #
            # asyncio.run(...)
            #
            # وجود ندارد.
            #
            # bot.run() مستقیماً اجرا می‌شود.
            #
            # =================================================

            bot.run()

            # اگر bot.run() بدون Exception متوقف شد

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
        # اینجا نباید بنویسیم:
        #
        # asyncio.run(run_with_retry())
        #
        # چون Bale خودش asyncio.run() دارد.
        #
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
