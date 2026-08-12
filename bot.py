import os
import re
import logging
import asyncio
from html import unescape
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import bale

load_dotenv()

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("gamefa-bale-bot")

if not BALE_TOKEN:
    raise RuntimeError("BALE_TOKEN در فایل .env تنظیم نشده است.")

session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0 Safari/537.36"
    ),
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
})

def is_admin(user_id):
    try:
        return int(user_id) in ADMIN_IDS
    except Exception:
        return False

def clean_text(text):
    if not text:
        return ""
    text = unescape(text).replace("\xa0", " ").strip()
    text = re.sub(r"[ \t]+", " ", text)
    return text

def normalize_url(url):
    url = url.strip().strip("<>")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url

def extract_url(text):
    if not text:
        return None
    m = re.search(r"https?://[^\s<>\"']+", text)
    if m:
        return normalize_url(m.group(0).rstrip(").,؛،"))
    m = re.search(r"(?:www\.)?gamefa\.com/[^\s<>\"']+", text, re.I)
    if m:
        return normalize_url(m.group(0).rstrip(").,؛،"))
    return None

def is_gamefa_url(url):
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host == "gamefa.com" or host.endswith(".gamefa.com")
    except Exception:
        return False

def fetch_soup(url):
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    return BeautifulSoup(response.text, "html.parser")

def extract_title(soup):
    for selector in [
        "h1.entry-title", "h1.post-title", "article h1",
        "main h1", "h1"
    ]:
        el = soup.select_one(selector)
        if el:
            title = clean_text(el.get_text(" ", strip=True))
            if title:
                return title

    meta = soup.find("meta", property="og:title")
    if meta:
        title = clean_text(meta.get("content", ""))
        if title:
            return title

    return "خبر گیمفا"

def extract_image(soup):
    # OpenGraph معمولاً بهترین گزینه برای تصویر شاخص است.
    meta = soup.find("meta", property="og:image")
    if meta and meta.get("content"):
        return meta["content"].strip()

    for selector in [
        "article img",
        ".entry-content img",
        ".post-content img",
        "main img"
    ]:
        img = soup.select_one(selector)
        if img:
            src = (
                img.get("src")
                or img.get("data-src")
                or img.get("data-lazy-src")
            )
            if src:
                return src.strip()

    return None

def remove_unwanted(container):
    for selector in [
        "script", "style", "noscript", "iframe", "svg",
        "form", "button", "nav", "footer", "header",
        ".advertisement", ".ads", ".ad", ".banner",
        ".social-share", ".share-buttons", ".related-posts",
        ".comments", ".comment-section", ".author-box",
        ".post-meta", ".entry-meta", ".breadcrumb"
    ]:
        for el in container.select(selector):
            el.decompose()

def valid_paragraph(text):
    if not text or len(text) < 50:
        return False
    bad = [
        "اشتراک‌گذاری", "اشتراک گذاری", "دیدگاه",
        "دیدگاه‌ها", "نظرات", "مطالب مرتبط",
        "تبلیغات", "عضویت", "دنبال کنید"
    ]
    return not any(x in text for x in bad)

def extract_paragraphs(soup):
    best = []
    selectors = [
        "article", ".entry-content", ".post-content",
        ".article-content", ".single-content",
        ".td-post-content", ".content-area", "main"
    ]

    for selector in selectors:
        for container in soup.select(selector):
            copy = BeautifulSoup(str(container), "html.parser")
            remove_unwanted(copy)
            paragraphs = []
            for p in copy.find_all("p"):
                text = clean_text(p.get_text(" ", strip=True))
                if valid_paragraph(text) and text not in paragraphs:
                    paragraphs.append(text)
            if len(paragraphs) > len(best):
                best = paragraphs

    return best[:2]

def md_escape(text):
    # عنوان/متن را برای Markdown ساده امن می‌کند.
    for ch in ["\\", "_", "*", "`", "[", "]"]:
        text = text.replace(ch, "\\" + ch)
    return text

def build_caption(title, paragraphs, url):
    lines = [
        f"📢 *[{md_escape(title)}]({url})*",
        "",
        f"🟣 {md_escape(paragraphs[0])}",
        "",
        f"🟣 {md_escape(paragraphs[1])}",
        "",
        f"*[ادامه خبر📑]({url})*",
        "",
        f"🆔 *@{FOOTER.lstrip('@')}*",
    ]
    return "\\n".join(lines)


bot = bale.Bot(token=BALE_TOKEN)

@bot.event
async def on_ready():
    logger.info("Gamefa Bale News Bot is READY")

@bot.event
async def on_message(message):
    try:
        user = getattr(message, "author", None)
        user_id = getattr(user, "id", None)

        if getattr(user, "is_bot", False):
            return

        if not is_admin(user_id):
            return

        content = (getattr(message, "content", None) or "").strip()

        if content in ("/start", "/help", "/راهنما"):
            await message.reply(
                "لینک یک خبر از gamefa.com را ارسال کنید.\n"
                "ربات عکس شاخص + عنوان + دقیقاً دو پاراگراف اول مقاله "
                "را در یک پیام برایتان می‌فرستد."
            )
            return

        url = extract_url(content)

        if not url:
            return

        if not is_gamefa_url(url):
            await message.reply("❌ فقط لینک‌های gamefa.com پذیرفته می‌شوند.")
            return

        processing = await message.reply("⏳ در حال دریافت خبر از گیمفا...")

        try:
            soup = fetch_soup(url)
            title = extract_title(soup)
            image_url = extract_image(soup)
            paragraphs = extract_paragraphs(soup)

            if len(paragraphs) < 2:
                await processing.edit(
                    "❌ نتوانستم دو پاراگراف واقعی مقاله را پیدا کنم."
                )
                return

            caption = build_caption(title, paragraphs, url)

            # عکس + کپشن در یک پیام
            if image_url:
                await bot.send_photo(
                    chat_id=user_id,
                    photo=image_url,
                    caption=caption,
                )
            else:
                await message.reply(
                    "⚠️ تصویر شاخص پیدا نشد؛ متن خبر:",
                    caption if False else None
                )
                await message.reply(caption)

            await processing.edit("✅ خبر آماده شد.")

        except requests.RequestException as e:
            logger.exception("HTTP error: %s", e)
            await processing.edit("❌ دریافت مقاله از گیمفا با خطا مواجه شد.")
        except Exception as e:
            logger.exception("Processing error: %s", e)
            await processing.edit(f"❌ خطا هنگام پردازش خبر:\n{e}")

    except Exception as e:
        logger.exception("Unhandled message error: %s", e)

async def run_with_retry():
    attempt = 0

    while True:
        attempt += 1
        try:
            logger.info("Starting Bale bot (attempt %s)...", attempt)
            bot.run()
            logger.warning(
                "Bot stopped. Retrying in %s seconds...",
                RETRY_DELAY
            )
            await asyncio.sleep(RETRY_DELAY)

        except (OSError, ConnectionError, TimeoutError) as e:
            logger.warning(
                "Network error: %s | retrying in %s seconds...",
                e,
                RETRY_DELAY
            )
            await asyncio.sleep(RETRY_DELAY)

        except Exception as e:
            logger.exception(
                "Bale connection/runtime error: %s | retrying in %s seconds...",
                e,
                RETRY_DELAY
            )
            await asyncio.sleep(RETRY_DELAY)

        if attempt >= MAX_RETRIES:
            logger.warning(
                "Reached MAX_RETRIES=%s; resetting retry counter.",
                MAX_RETRIES
            )
            attempt = 0

if __name__ == "__main__":
    try:
        asyncio.run(run_with_retry())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
