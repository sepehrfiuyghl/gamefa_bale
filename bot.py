import os
import re
import json
import html
import asyncio
import logging
import hashlib
import mimetypes
from pathlib import Path
from urllib.parse import urlparse, urljoin

import aiohttp
from bs4 import BeautifulSoup
from openai import AsyncOpenAI

# ============================================================
# BALE SETTINGS
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini").strip()
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")
except (ValueError, TypeError):
    ADMIN_ID = 0

BALE_API = f"https://tapi.bale.ai/bot{BOT_TOKEN}"

MEMORY_FILE = Path("news_memory.json")
MAX_MEMORY = 1500
IMAGE_DIR = Path("gamefa_images")
IMAGE_DIR.mkdir(parents=True, exist_ok=True)
memory = []
prepared = {}
processing_users = set()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("gamefa_bale_bot")


def strip_markup(text):
    """Convert the bot's internal HTML to Bale Markdown while preserving formatting."""
    text = text or ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<\s*(?:b|strong)\s*>(.*?)<\s*/\s*(?:b|strong)\s*>", r"*\1*", text, flags=re.I | re.S)
    text = re.sub(r"<\s*(?:i|em)\s*>(.*?)<\s*/\s*(?:i|em)\s*>", r"_\1_", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


class BaleMessage:
    def __init__(self, data, bot):
        self._data = data or {}
        self.bot = bot
        self.message_id = str(self._data.get("message_id", ""))
        self.chat_id = self._data.get("chat", {}).get("id")
        u = self._data.get("from", {}) or {}
        self.from_user = type("BaleUser", (), {
            "id": u.get("id", 0),
            "user_id": u.get("id", 0),
            "username": u.get("username"),
            "first_name": u.get("first_name", ""),
            "last_name": u.get("last_name", ""),
        })()
        self.text = self._data.get("text") or self._data.get("caption") or ""
        self.content = self.text

    async def answer(self, text, parse_mode=None, reply_markup=None):
        return await self.bot.send_message(self.chat_id, strip_markup(text), components=reply_markup)

    async def answer_photo(self, photo, caption=None, parse_mode=None, reply_markup=None):
        path = getattr(photo, "path", photo)
        return await self.bot.send_photo(self.chat_id, path, caption=strip_markup(caption or ""), components=reply_markup)

    async def delete(self):
        return await self.bot.delete_message(self.chat_id, self.message_id)

    async def edit_text(self, text, parse_mode=None, reply_markup=None):
        return await self.bot.edit_message(self.chat_id, self.message_id, strip_markup(text), components=reply_markup)


class BaleFile:
    def __init__(self, path):
        self.path = str(path)


class BaleBotClient:
    def __init__(self, token):
        self.token = token
        self.base = f"https://tapi.bale.ai/bot{token}"
        self.session = None

    async def start(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(
                total=120,
                connect=25,
                sock_connect=25,
                sock_read=95,
            ),
            connector=aiohttp.TCPConnector(
                ttl_dns_cache=300,
                enable_cleanup_closed=True,
            ),
        )

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def api(self, method, payload=None, form=None):
        if not self.session:
            await self.start()
        url = f"{self.base}/{method}"
        last_error = None
        for attempt in range(4):
            try:
                if form is not None:
                    async with self.session.post(url, data=form) as r:
                        data = await r.json(content_type=None)
                else:
                    async with self.session.post(url, json=payload or {}) as r:
                        data = await r.json(content_type=None)
                if not data.get("ok", True):
                    raise RuntimeError(f"Bale API {method}: {data}")
                return data
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, RuntimeError) as exc:
                last_error = exc
                if attempt >= 3:
                    raise
                delay = min(2 ** attempt, 8)
                log.warning("Bale API %s failed (attempt %s/4): %s; retrying in %ss", method, attempt + 1, exc, delay)
                await asyncio.sleep(delay)
        raise last_error

    async def send_message(self, chat_id, text, components=None, reply_to_message_id=None):
        payload = {"chat_id": chat_id, "text": strip_markup(text), "parse_mode": "Markdown"}
        if components:
            payload["reply_markup"] = components
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        data = await self.api("sendMessage", payload=payload)
        return BaleMessage(data.get("result", {}), self)

    async def send_photo(self, chat_id, photo, caption=None, components=None, reply_to_message_id=None):
        payload = {"chat_id": chat_id, "photo": photo}
        if caption:
            payload["caption"] = strip_markup(caption)
        if components:
            payload["reply_markup"] = components
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        # Bale accepts multipart upload for local files.
        form = aiohttp.FormData()
        for k, v in payload.items():
            if k == "reply_markup":
                v = json.dumps(v, ensure_ascii=False)
            form.add_field(k, str(v))
        with open(photo, "rb") as f:
            form.add_field("photo", f, filename=Path(photo).name, content_type=mimetypes.guess_type(str(photo))[0] or "application/octet-stream")
            # recreate non-file fields because photo above was intentionally omitted
            # (Bale expects the actual file in the multipart field named photo).
            form = aiohttp.FormData()
            form.add_field("chat_id", str(chat_id))
            if caption:
                form.add_field("caption", strip_markup(caption))
            form.add_field("parse_mode", "Markdown")
            if components:
                form.add_field("reply_markup", json.dumps(components, ensure_ascii=False))
            if reply_to_message_id:
                form.add_field("reply_to_message_id", str(reply_to_message_id))
            form.add_field("photo", f, filename=Path(photo).name, content_type=mimetypes.guess_type(str(photo))[0] or "application/octet-stream")
            data = await self.api("sendPhoto", form=form)
        return BaleMessage(data.get("result", {}), self)

    async def edit_message(self, chat_id, message_id, text, components=None):
        payload = {"chat_id": chat_id, "message_id": message_id, "text": strip_markup(text), "parse_mode": "Markdown"}
        if components:
            payload["reply_markup"] = components
        return await self.api("editMessageText", payload=payload)

    async def delete_message(self, chat_id, message_id):
        return await self.api("deleteMessage", payload={"chat_id": chat_id, "message_id": message_id})

    async def answer_callback(self, callback_id, text=""):
        return await self.api("answerCallbackQuery", payload={"callback_query_id": callback_id, "text": text})

    async def delete_webhook(self):
        return await self.api("deleteWebhook", payload={})

    async def get_updates(self, offset=None, timeout=30):
        payload = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        data = await self.api("getUpdates", payload=payload)
        return data.get("result", [])


BALE_BOT = None

# ============================================================
# MEMORY
# ============================================================

def load_memory():
    global memory

    try:
        if not MEMORY_FILE.exists():
            memory = []
            return

        data = json.loads(
            MEMORY_FILE.read_text(
                encoding="utf-8"
            )
        )

        if isinstance(data, list):
            memory = data[-MAX_MEMORY:]
        else:
            memory = []

    except Exception as error:
        log.warning(
            "Memory load error: %s",
            error
        )

        memory = []


def save_memory():

    try:
        MEMORY_FILE.write_text(
            json.dumps(
                memory[-MAX_MEMORY:],
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

    except Exception as error:
        log.warning(
            "Memory save error: %s",
            error
        )


# ============================================================
# TEXT NORMALIZATION
# ============================================================

def norm(text):

    text = text or ""

    text = re.sub(
        r"https?://\S+",
        " ",
        text
    )

    text = text.lower()

    text = re.sub(
        r"[^\w\u0600-\u06FF\s]",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def word_similarity(a, b):

    words_a = set(
        norm(a).split()
    )

    words_b = set(
        norm(b).split()
    )

    if not words_a or not words_b:
        return 0

    return len(
        words_a & words_b
    ) / len(
        words_a | words_b
    )


def similarity(a, b):

    return word_similarity(
        a,
        b
    )


def text_hash(text):

    normalized = norm(text)

    return hashlib.sha256(
        normalized.encode(
            "utf-8"
        )
    ).hexdigest()


def duplicate(text, title=""):

    new_text = text or ""
    new_title = title or ""

    new_hash = text_hash(
        new_text
    )

    for item in memory:

        old_hash = item.get(
            "hash",
            ""
        )

        if old_hash and old_hash == new_hash:
            return True

        old_source = item.get(
            "source",
            ""
        )

        old_title = item.get(
            "title",
            ""
        )

        if new_title and old_title:

            title_score = similarity(
                new_title,
                old_title
            )

            if title_score >= 0.88:
                return True

        if old_source:

            source_score = similarity(
                new_text,
                old_source
            )

            if source_score >= 0.84:
                return True

    return False


# ============================================================
# URL
# ============================================================

def extract_url(text):

    if not text:
        return None

    match = re.search(
        r"https?://[^\s<>()]+",
        text
    )

    if not match:
        return None

    return match.group(0).rstrip(
        ".,)]}"
    )


# ============================================================
# HTML
# ============================================================

def escape_html(text):

    return html.escape(
        text or "",
        quote=False
    )


def clean_text(text):

    text = text or ""

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# ADMIN
# ============================================================

def is_admin(message):

    return bool(
        ADMIN_ID
        and message.from_user
        and message.from_user.id == ADMIN_ID
    )


def is_admin_id(user_id):

    return bool(
        ADMIN_ID
        and user_id == ADMIN_ID
    )


# ============================================================
# PERSIAN START
# ============================================================

PERSIAN_RE = re.compile(
    r"[\u0600-\u06FF]"
)


def starts_with_persian(text):

    if not text:
        return False

    clean = text.strip()

    # حذف علائم و ایموجی‌های ابتدایی
    clean = re.sub(
        r"^[🎮🎬📱🟣📢📰🔵🟢🟡🟠⚪⚫\s\-\–—•]+",
        "",
        clean
    ).strip()

    if not clean:
        return False

    return bool(
        PERSIAN_RE.match(
            clean[0]
        )
    )


def make_persian_start(
    text,
    is_title=False
):

    if not text:
        return text

    text = text.strip()

    if starts_with_persian(text):
        return text

    if is_title:

        return (
            "گزارش جدید درباره "
            + text
        )

    return (
        "براساس گزارش منتشرشده، "
        + text
    )


# ============================================================
# ENSURE PERSIAN START
# ============================================================

def ensure_persian_start(
    text,
    is_title=False
):

    """
    اگر متن با انگلیسی شروع شده باشد،
    قبل از آن یک عبارت فارسی مناسب قرار می‌دهد.
    """

    if not text:
        return text

    text = text.strip()

    # حذف علامت‌های اضافی
    text = re.sub(
        r"^[🎮🎬📱📢🟣📰🔵🟢🟡🟠⚪⚫\s]+",
        "",
        text
    ).strip()

    if starts_with_persian(text):
        return text

    if is_title:
        return "گزارش جدید درباره " + text

    return "براساس گزارش منتشرشده، " + text


# ============================================================
# CATEGORY
# ============================================================

def detect_category(text):

    text_lower = (
        text or ""
    ).lower()

    game_words = [
        "بازی",
        "گیم",
        "game",
        "gaming",
        "playstation",
        "xbox",
        "nintendo",
        "steam",
        "doom",
        "gta",
        "resident evil",
        "halo",
        "final fantasy",
        "devil may cry",
        "assassin",
        "elden ring",
        "sony",
        "microsoft",
        "ps5",
        "ps4",
        "xbox series",
        "switch"
    ]

    movie_words = [
        "فیلم",
        "سریال",
        "بازیگر",
        "movie",
        "film",
        "series",
        "season",
        "actor",
        "actress",
        "netflix",
        "hbo",
        "disney",
        "marvel",
        "dc",
        "cinema"
    ]

    if any(
        word in text_lower
        for word in game_words
    ):
        return "🎮"

    if any(
        word in text_lower
        for word in movie_words
    ):
        return "🎬"

    return "📢"


# ============================================================
# AI CLEANER
# ============================================================

def clean_ai_text(text):

    text = text or ""

    # Markdown bold
    text = re.sub(
        r"\*\*(.*?)\*\*",
        r"\1",
        text,
        flags=re.S
    )

    text = re.sub(
        r"__(.*?)__",
        r"\1",
        text,
        flags=re.S
    )

    # Italic
    text = re.sub(
        r"\*(.*?)\*",
        r"\1",
        text,
        flags=re.S
    )

    # Code
    text = re.sub(
        r"`(.*?)`",
        r"\1",
        text,
        flags=re.S
    )

    # Markdown links
    text = re.sub(
        r"\[([^\]]+)\]\([^)]+\)",
        r"\1",
        text
    )

    forbidden_patterns = [
        r"(?im)^\s*امتیاز دقت.*$",
        r"(?im)^\s*امتیاز ai.*$",
        r"(?im)^\s*امتیاز هوش مصنوعی.*$",
        r"(?im)^\s*اطلاعاتی که reviewer.*$",
        r"(?im)^\s*reviewer.*$",
        r"(?im)^\s*ai score.*$",
        r"(?im)^\s*accuracy score.*$",
        r"(?im)^\s*اطلاعات استخراج شده.*$",
        r"(?im)^\s*اطلاعات بررسی شده.*$",
        r"(?im)^\s*متن کامل صفحه.*$",
        r"(?im)^\s*مقاله شامل.*$",
        r"(?im)^\s*طبق بررسی ai.*$",
        r"(?im)^\s*هوش مصنوعی.*$"
    ]

    for pattern in forbidden_patterns:

        text = re.sub(
            pattern,
            "",
            text
        )

    # Channel
    text = re.sub(
        r"(?im)^\s*(?:🆔\s*)?@Gamefa_official\s*$",
        "",
        text
    )

    # Emojis at line beginning
    text = re.sub(
        r"(?m)^\s*[🎮🎬📱📢🟣📰🔵🟢🟡🟠⚪⚫]\s*",
        "",
        text
    )

    return text.strip()


# ============================================================
# ARTICLE CLEANING
# ============================================================

REMOVE_SELECTORS = [

    "script",
    "style",
    "noscript",
    "svg",
    "nav",
    "footer",
    "form",
    "aside",
    "header",
    "iframe",
    "video",
    "audio",
    "canvas",

    ".related-posts",
    ".related-post",
    ".related",
    ".recommended",
    ".recommendations",
    ".recommended-posts",
    ".more-posts",
    ".latest-posts",
    ".popular-posts",
    ".author-box",
    ".author-info",
    ".author-card",
    ".comments",
    ".comment",
    ".comment-list",
    ".advertisement",
    ".ads",
    ".ad",
    ".banner",
    ".newsletter",
    ".social-share",
    ".share-buttons",
    ".breadcrumb",
    ".breadcrumbs",
    ".sidebar",
    ".widget",
    ".wp-block-latest-posts",
    ".read-more",
    ".post-navigation",
    ".navigation"
]


def remove_unwanted_elements(soup):

    for selector in REMOVE_SELECTORS:

        try:

            for element in soup.select(
                selector
            ):
                element.decompose()

        except Exception:
            pass


def is_probably_noise(text):

    if not text:
        return True

    low = text.lower()

    noise_words = [
        "مطالب مرتبط",
        "مطالب پیشنهادی",
        "اخبار مرتبط",
        "بیشتر بخوانید",
        "related posts",
        "related articles",
        "recommended",
        "subscribe",
        "newsletter",
        "تبلیغات",
        "advertisement",
        "نویسنده",
        "author",
        "دیدگاه",
        "comments",
        "comment",
        "share"
    ]

    if any(
        word in low
        for word in noise_words
    ):
        return True

    return False


# ============================================================
# GAMEFA FETCH
# ============================================================

async def fetch_gamefa(url):

    parsed = urlparse(url)

    if "gamefa.com" not in (
        parsed.netloc.lower()
    ):
        raise ValueError(
            "فقط لینک Gamefa پشتیبانی می‌شود."
        )

    headers = {
        "User-Agent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/151 Safari/537.36",

        "Accept-Language":
            "fa-IR,fa;q=0.9,en;q=0.8"
    }

    timeout = aiohttp.ClientTimeout(
        total=45
    )

    async with aiohttp.ClientSession(
        headers=headers,
        timeout=timeout
    ) as session:

        async with session.get(
            url,
            allow_redirects=True
        ) as response:

            response.raise_for_status()

            final_url = str(
                response.url
            )

            raw = await response.text(
                errors="ignore"
            )

    soup = BeautifulSoup(
        raw,
        "html.parser"
    )

    remove_unwanted_elements(
        soup
    )

    # ========================================================
    # TITLE
    # ========================================================

    title = ""

    h1 = soup.find("h1")

    if h1:

        title = clean_text(
            h1.get_text(
                " ",
                strip=True
            )
        )

    elif soup.title:

        title = clean_text(
            soup.title.get_text(
                " ",
                strip=True
            )
        )

    # ========================================================
    # DESCRIPTION
    # ========================================================

    description = ""

    meta_options = [
        {"name": "description"},
        {"property": "og:description"},
        {"name": "twitter:description"}
    ]

    for attrs in meta_options:

        meta = soup.find(
            "meta",
            attrs=attrs
        )

        if meta and meta.get(
            "content"
        ):

            description = clean_text(
                meta["content"]
            )

            break

    # ========================================================
    # IMAGE
    # ========================================================

    image_candidates = []

    for attrs in [
        {"property": "og:image"},
        {"property": "og:image:url"},
        {"name": "twitter:image"},
        {"name": "twitter:image:src"}
    ]:

        meta = soup.find(
            "meta",
            attrs=attrs
        )

        if meta and meta.get(
            "content"
        ):

            image_candidates.append(
                urljoin(
                    final_url,
                    meta["content"].strip()
                )
            )

    # ========================================================
    # ARTICLE
    # ========================================================

    article = None

    article_selectors = [
        "article",
        "[itemprop='articleBody']",
        ".entry-content",
        ".post-content",
        ".article-content",
        ".single-post-content",
        ".td-post-content",
        ".post-body",
        ".content-area"
    ]

    for selector in article_selectors:

        candidate = soup.select_one(
            selector
        )

        if candidate:

            article = candidate
            break

    if article is None:
        article = soup

    # ========================================================
    # PARAGRAPHS
    # ========================================================

    paragraphs = article.find_all(
        [
            "p",
            "h2",
            "h3",
            "h4"
        ]
    )

    body_parts = []

    seen_paragraphs = set()

    for paragraph in paragraphs:

        text = clean_text(
            paragraph.get_text(
                " ",
                strip=True
            )
        )

        if len(text) < 35:
            continue

        if is_probably_noise(
            text
        ):
            continue

        paragraph_key = norm(
            text
        )

        if paragraph_key in seen_paragraphs:
            continue

        seen_paragraphs.add(
            paragraph_key
        )

        body_parts.append(
            text
        )

    # ========================================================
    # FALLBACK
    # ========================================================

    if len(body_parts) < 3:

        body_parts = []

        for paragraph in soup.find_all(
            "p"
        ):

            text = clean_text(
                paragraph.get_text(
                    " ",
                    strip=True
                )
            )

            if len(text) >= 35:

                if not is_probably_noise(
                    text
                ):
                    body_parts.append(
                        text
                    )

    # ========================================================
    # FULL BODY
    # ========================================================

    body = "\n".join(
        body_parts
    )

    body = body[:70000]

    # ========================================================
    # IMAGE FALLBACK
    # ========================================================

    if not image_candidates:

        for img in article.find_all(
            "img"
        ):

            src = (
                img.get("src")
                or img.get("data-src")
                or img.get("data-lazy-src")
            )

            if not src:
                continue

            src = urljoin(
                final_url,
                src
            )

            image_candidates.append(
                src
            )

    image = (
        image_candidates[0]
        if image_candidates
        else ""
    )

    return {
        "url": final_url,
        "title": title,
        "description": description,
        "body": body,
        "image": image
    }


# ============================================================
# AI CLIENT
# ============================================================

def get_ai_client():

    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY تنظیم نشده است."
        )

    return AsyncOpenAI(
        api_key=OPENAI_API_KEY
    )


# ============================================================
# FACT EXTRACTION PROMPT
# ============================================================

FACT_PROMPT = r"""
تو یک سیستم استخراج اطلاعات برای تحریریه Gamefa هستی.

وظیفه تو تولید خبر نیست.

وظیفه تو این است که مقاله را کامل بخوانی و فقط واقعیت‌های مهم و مستقیم مربوط به موضوع اصلی مقاله را استخراج کنی.

ممکن است صفحه شامل موارد زیر باشد:

- مطالب مرتبط
- مطالب پیشنهادی
- مقالات دیگر
- تبلیغات
- اطلاعات نویسنده
- زمان انتشار
- باکس‌های سایت
- لینک‌های داخلی
- متن‌های جانبی
- Reviewer
- اطلاعات مربوط به عملکرد AI

هیچ‌کدام از این موارد را به‌عنوان محتوای اصلی خبر در نظر نگیر.

فقط اطلاعاتی را استخراج کن که مستقیماً درباره موضوع اصلی مقاله هستند.

اطلاعات مهمی که باید در صورت وجود استخراج شوند:

- اتفاق اصلی
- نام افراد
- نام بازی
- نام فیلم یا سریال
- نام شرکت‌ها
- سازنده
- ناشر
- پلتفرم‌ها
- تاریخ عرضه
- زمان عرضه
- تاریخ انتشار
- تاریخ دسترسی زودهنگام
- زمان پیش‌دانلود
- حجم دانلود
- قیمت
- نسخه‌ها
- وضعیت پروژه
- بازیگران
- کارگردان
- نویسنده
- فروش
- آمار
- تعداد
- ویژگی‌های مهم
- نقل‌قول مهم
- وضعیت تأیید یا شایعه بودن خبر

اگر تاریخ عرضه در مقاله وجود دارد، حتماً آن را استخراج کن.

اگر عدد یا آمار مهمی در مقاله وجود دارد، آن را حذف نکن.

اگر اطلاعاتی وجود ندارد، آن را اختراع نکن.

مقالات مرتبط و مطالب جانبی را با موضوع اصلی قاطی نکن.

خروجی فقط JSON معتبر باشد.

ساختار:

{
  "main_topic": "",
  "main_event": "",
  "facts": [
    {
      "fact": "",
      "importance": 1,
      "type": ""
    }
  ],
  "dates": [],
  "platforms": [],
  "numbers": [],
  "people": [],
  "companies": [],
  "status": "",
  "important_missing": []
}

importance باید عددی بین 1 تا 5 باشد.

فقط اطلاعاتی را وارد کن که واقعاً در مقاله وجود دارند.
"""


# ============================================================
# EXTRACT FACTS
# ============================================================

async def extract_facts(source):

    client = get_ai_client()

    prompt_input = (
        "عنوان مقاله:\n"
        + source.get("title", "")
        + "\n\n"
        "توضیحات:\n"
        + source.get("description", "")
        + "\n\n"
        "متن کامل مقاله:\n"
        + source.get("body", "")
    )

    response = await client.responses.create(
        model=MODEL,
        instructions=FACT_PROMPT,
        input=prompt_input,
        max_output_tokens=3000
    )

    raw = (
        response.output_text
        or ""
    ).strip()

    raw = re.sub(
        r"^```json\s*",
        "",
        raw,
        flags=re.I
    )

    raw = re.sub(
        r"\s*```$",
        "",
        raw
    )

    try:

        data = json.loads(
            raw
        )

    except Exception:

        start = raw.find("{")
        end = raw.rfind("}")

        if start == -1 or end == -1:

            raise RuntimeError(
                "AI نتوانست Factهای مقاله را استخراج کند."
            )

        try:

            data = json.loads(
                raw[start:end + 1]
            )

        except Exception as error:

            raise RuntimeError(
                "JSON استخراج Fact نامعتبر است."
            ) from error

    if not isinstance(
        data,
        dict
    ):

        raise RuntimeError(
            "ساختار Fact نامعتبر است."
        )

    return data


# ============================================================
# NEWS GENERATION
# ============================================================

NEWS_PROMPT = r"""
تو سردبیر ارشد اخبار فارسی Gamefa هستی.

از اطلاعات استخراج‌شده از مقاله، یک خبر حرفه‌ای فارسی تولید کن.

قانون بسیار مهم:

خروجی نهایی باید فقط شامل این موارد باشد:

خط اول:
تیتر

خطوط بعدی:
دقیقاً 7 جمله خبری.

اما در خروجی نهایی، 7 جمله خبر باید همگی در یک پاراگراف قرار بگیرند و بین جمله‌ها Enter نزن.

ساختار:

تیتر

جمله اول. جمله دوم. جمله سوم. جمله چهارم. جمله پنجم. جمله ششم. جمله هفتم.

---

مهم‌ترین قانون:

فقط از Factهای استخراج‌شده استفاده کن.

هیچ اطلاعاتی را حدس نزن.

هیچ اطلاعاتی را از خودت اضافه نکن.

---

قانون بسیار مهم درباره شروع متن:

تیتر MUST با یک کلمه یا عبارت فارسی شروع شود.

هیچ تیتر یا جمله‌ای نباید با کلمه انگلیسی شروع شود.

اگر نام انگلیسی در ابتدای جمله لازم است، ابتدا یک عبارت فارسی کوتاه و طبیعی قرار بده.

مثلاً:

درست:
جیکوب الوردی برای پیوستن به فیلم Scapegoat وارد مذاکره شده است

درست:
بازی GTA 6 طبق گزارش جدید...

درست:
فیلم Supergirl با...

غلط:
Jacob Elordi is in talks...

غلط:
GTA 6 will...

غلط:
Supergirl is...

این قانون برای تک‌تک 7 جمله الزامی است، نه فقط تیتر.

---

اطلاعات مهم نباید حذف شوند.

اگر Factهای مقاله شامل یکی از موارد زیر هستند، در صورت مرتبط بودن باید در خبر استفاده شوند:

- تاریخ عرضه
- زمان عرضه
- تاریخ انتشار
- پیش‌دانلود
- حجم بازی
- قیمت
- پلتفرم
- نسخه‌ها
- بازیگران
- کارگردان
- سازنده
- ناشر
- آمار
- اعداد
- وضعیت پروژه

اگر مقاله درباره حجم و زمان عرضه بازی است و تاریخ عرضه در Factها وجود دارد، حذف تاریخ عرضه ممنوع است.

---

درباره منابع:

نگو:
«طبق توضیحات مقاله»

نگو:
«متن کامل صفحه نشان می‌دهد»

نگو:
«Reviewer گفته»

نگو:
«هوش مصنوعی تشخیص داد»

نگو:
«امتیاز دقت»

نگو:
«در این صفحه»

نگو:
«مقاله با تیتر دیگری همراه است»

نگو:
«اطلاعاتی که Reviewer بررسی کرده»

هیچ اشاره‌ای به سیستم AI، Reviewer، Fact، مقاله ورودی یا فرایند تولید نکن.

---

مطالب مرتبط:

اگر در صفحه اطلاعات مربوط به مقاله دیگری وجود دارد، آن را وارد خبر نکن.

مثلاً اگر موضوع اصلی مقاله درباره Jacob Elordi و فیلم Scapegoat است و صفحه در پایین خود مطلبی درباره The Dog Stars دارد، اطلاعات The Dog Stars نباید وارد خبر Scapegoat شود؛ مگر اینکه مستقیماً در متن اصلی خبر درباره موضوع Scapegoat استفاده شده باشد.

---

تیتر:

کوتاه و خبری باشد.

حتماً با فارسی شروع شود.

---

سبک:

فارسی روان و طبیعی.

لحن خبری.

بدون اغراق.

بدون تحلیل شخصی.

بدون نظر شخصی.

بدون ساخت اطلاعات.

نام‌های انگلیسی مهم را حفظ کن، اما هرگز اجازه نده جمله با آن‌ها شروع شود.

---

این موارد ممنوع هستند:

- Markdown
- Bold
- Bullet
- شماره‌گذاری
- Emoji
- لینک
- آیدی کانال
- Reviewer
- AI Score
- Accuracy Score
- توضیح درباره مقاله
- توضیح درباره فرآیند تولید
- توضیح درباره Factها

---

خروجی فقط:

تیتر
یک پاراگراف شامل دقیقاً 7 جمله خبری

هیچ چیز دیگری ننویس.
"""


async def generate_news(
    source,
    facts,
    retry_instruction=""
):

    client = get_ai_client()

    facts_json = json.dumps(
        facts,
        ensure_ascii=False,
        indent=2
    )

    input_text = (
        "FACTS استخراج‌شده از مقاله:\n\n"
        + facts_json
        + "\n\n"
        "عنوان اصلی مقاله:\n"
        + source.get("title", "")
        + "\n\n"
        "متن اصلی مقاله برای بررسی نهایی:\n"
        + source.get("body", "")
        + "\n\n"
        + retry_instruction
    )

    response = await client.responses.create(
        model=MODEL,
        instructions=NEWS_PROMPT,
        input=input_text,
        max_output_tokens=1800
    )

    result = (
        response.output_text
        or ""
    ).strip()

    if not result:

        raise RuntimeError(
            "AI خروجی خالی تولید کرد."
        )

    return result


# ============================================================
# SENTENCE SPLITTER
# ============================================================

def split_sentences(text):
    """Extract title + exactly seven sentences as robustly as possible."""
    text = clean_ai_text(text)
    text = text.replace("\r", "\n")

    # Remove accidental labels commonly emitted by models.
    text = re.sub(r"(?im)^\s*(?:تیتر|عنوان)\s*[:：]\s*", "", text)
    text = re.sub(r"(?im)^\s*(?:خبر|متن خبر)\s*[:：]\s*", "", text)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "", []

    title = clean_sentence(lines[0]) if 'clean_sentence' in globals() else lines[0]
    body = " ".join(lines[1:])
    body = re.sub(r"\s+", " ", body).strip()

    # Sentence punctuation: Persian/Latin full stops and question/exclamation marks.
    parts = re.split(r"(?<=[.!؟])\s+", body)
    parts = [x.strip() for x in parts if x.strip()]

    # Some models omit punctuation. If there are seven separate non-empty lines,
    # use those lines as sentences.
    if len(parts) < 7 and len(lines) == 8:
        parts = [x.strip() for x in lines[1:] if x.strip()]

    # If the model returns one paragraph without punctuation, split by lines first.
    if len(parts) < 7:
        line_parts = [x.strip() for x in lines[1:] if x.strip()]
        if len(line_parts) == 7:
            parts = line_parts

    return title, parts


# ============================================================
# CLEAN SENTENCE
# ============================================================

def clean_sentence(sentence):

    sentence = sentence.strip()

    sentence = re.sub(
        r"^[•\-–—\d.)]+\s*",
        "",
        sentence
    )

    sentence = re.sub(
        r"^\s*[🎮🎬📱📢🟣📰🔵🟢🟡🟠⚪⚫]+\s*",
        "",
        sentence
    )

    sentence = re.sub(
        r"(?i)\b(?:reviewer|ai score|accuracy score)\b.*$",
        "",
        sentence
    )

    return sentence.strip()


# ============================================================
# INTERNAL OUTPUT VALIDATION
# ============================================================

FORBIDDEN_OUTPUT_TERMS = [
    "reviewer", "ai score", "accuracy score", "امتیاز دقت ai",
    "امتیاز دقت", "اطلاعاتی که reviewer", "هوش مصنوعی بررسی",
    "متن کامل صفحه", "متن کامل مقاله", "در این صفحه",
    "مقاله با تیتر دیگری", "تیتر دیگری", "اطلاعات استخراج شده", "fact"
]


def validate_generated_output(generated):
    title, sentences = split_sentences(generated)
    if not title or len(sentences) != 7:
        return False, title, sentences

    combined = (title + " " + " ".join(sentences)).lower()
    if any(term.lower() in combined for term in FORBIDDEN_OUTPUT_TERMS):
        return False, title, sentences

    if not starts_with_persian(title):
        return False, title, sentences

    for sentence in sentences:
        if not starts_with_persian(sentence):
            return False, title, sentences

    # Telegram photo captions have a 1024-character limit. Keep a margin for safety.
    if len(format_post(generated)) > 1024:
        return False, title, sentences

    return True, title, sentences


# ============================================================
# FACT COVERAGE
# ============================================================

def fact_text_list(facts):

    result = []

    for item in facts.get(
        "facts",
        []
    ):

        if not isinstance(
            item,
            dict
        ):
            continue

        fact = str(
            item.get(
                "fact",
                ""
            )
        ).strip()

        importance = item.get(
            "importance",
            0
        )

        try:

            importance = int(
                importance
            )

        except Exception:

            importance = 0

        if fact and importance >= 4:

            result.append(
                fact
            )

    return result


def check_important_fact_coverage(
    generated,
    facts
):

    important_facts = fact_text_list(
        facts
    )

    if not important_facts:
        return True

    generated_norm = norm(
        generated
    )

    generated_words = set(
        generated_norm.split()
    )

    missed = 0

    for fact in important_facts:

        fact_norm = norm(
            fact
        )

        fact_words = set(
            fact_norm.split()
        )

        if not fact_words:
            continue

        overlap = len(
            fact_words
            & generated_words
        ) / len(
            fact_words
        )

        # بررسی اعداد
        numbers = re.findall(
            r"\d+(?:[.,]\d+)?",
            fact
        )

        if numbers:

            if not any(
                number in generated
                for number in numbers
            ):

                missed += 1
                continue

        if overlap < 0.25:

            missed += 1

    return missed <= max(
        1,
        len(important_facts) // 3
    )


# ============================================================
# FORMAT POST
# ============================================================

def format_post(generated, facts=None):
    generated = clean_ai_text(generated)
    title, sentences = split_sentences(generated)

    sentences = [clean_sentence(x) for x in sentences if clean_sentence(x)]
    if len(sentences) != 7:
        return ""

    title = ensure_persian_start(clean_sentence(title), is_title=True)
    sentences = [ensure_persian_start(x, is_title=False) for x in sentences]

    if not starts_with_persian(title) or any(not starts_with_persian(x) for x in sentences):
        return ""

    category = detect_category(title + " " + " ".join(sentences))
    title = category + " " + title
    body = " ".join(sentences)

    result = (
        "*" + title + "*\n\n"
        + "🟣 " + body + "\n\n"
        + "🆔 *@Gamefa_official*"
    )
    return result


# ============================================================
# FINAL GAMEFA MARKDOWN FORMAT
# ============================================================
def enforce_gamefa_markdown(text):
    text = text or ""
    text = text.strip()
    text = re.sub(r"(?im)^\s*🆔\s*\*?@Gamefa_official\*?\s*$", "", text)
    text = text.strip()
    lines = text.splitlines()
    if lines:
        title = lines[0].strip()
        title = re.sub(r"^\*+(.*?)\*+$", r"\1", title).strip()
        title = re.sub(r"^(🎮|🎬|📢)\s*", r"\1 ", title)
        if not title.startswith(("🎮 ", "🎬 ", "📢 ")):
            title = "🎮 " + title
        lines[0] = "*" + title + "*"
    return "\n".join(lines).rstrip() + "\n\n🆔 *@Gamefa_official*"


# ============================================================
# IMAGE DOWNLOAD
# ============================================================

async def download_image(
    url
):

    if not url:
        return None

    try:

        headers = {
            "User-Agent":
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/151 Safari/537.36",

            "Accept":
                "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
        }

        timeout = aiohttp.ClientTimeout(
            total=35
        )

        async with aiohttp.ClientSession(
            headers=headers,
            timeout=timeout
        ) as session:

            async with session.get(
                url,
                allow_redirects=True
            ) as response:

                if response.status != 200:
                    return None

                content_type = (
                    response.headers.get(
                        "Content-Type",
                        ""
                    ).lower()
                )

                data = await response.read()

        if not data:
            return None

        if len(data) < 1000:
            return None

        if len(data) > 15 * 1024 * 1024:
            return None

        parsed = urlparse(
            url
        )

        extension = Path(
            parsed.path
        ).suffix.lower()

        allowed = [
            ".jpg",
            ".jpeg",
            ".png",
            ".webp"
        ]

        if extension not in allowed:

            if "jpeg" in content_type:
                extension = ".jpg"

            elif "png" in content_type:
                extension = ".png"

            elif "webp" in content_type:
                extension = ".webp"

            else:
                extension = ".jpg"

        filename = (
            "gamefa_"
            + hashlib.md5(
                url.encode(
                    "utf-8"
                )
            ).hexdigest()
            + extension
        )

        path = (
            IMAGE_DIR
            / filename
        )

        path.write_bytes(
            data
        )

        return path

    except Exception as error:

        log.warning(
            "Image download error: %s",
            error
        )

        return None


# ============================================================
# IMAGE SEARCH
# ============================================================

async def find_best_image(
    source
):

    primary = source.get(
        "image",
        ""
    )

    if primary:

        path = await download_image(
            primary
        )

        if path:
            return path

    return None


# ============================================================
# REPLY KEYBOARDS
# ============================================================


async def process_news(
    message,
    text
):

    user_id = message.from_user.id

    if user_id in processing_users:

        await message.answer(
            "⏳ یک خبر در حال پردازش است. لطفاً صبر کن."
        )

        return

    processing_users.add(
        user_id
    )

    status = None
    image_path = None

    try:

        url = extract_url(
            text
        )

        # ====================================================
        # SOURCE
        # ====================================================

        if url:

            status = await message.answer(
                "⏳ در حال دریافت کامل مقاله از Gamefa..."
            )

            article = await fetch_gamefa(
                url
            )

            source = article

            if status:

                try:

                    await status.edit_text(
                        "🧠 مقاله دریافت شد.\n"
                        "در حال استخراج واقعیت‌های مهم..."
                    )

                except Exception:
                    pass

        else:

            source = {
                "url": "",
                "title": "",
                "description": "",
                "body": text,
                "image": ""
            }

        # ====================================================
        # DUPLICATE
        # ====================================================

        source_for_duplicate = (
            source.get("title", "")
            + "\n"
            + source.get("body", "")
        )

        if duplicate(
            source_for_duplicate,
            source.get(
                "title",
                ""
            )
        ):

            await message.answer(
                "⚠️ این خبر یا یک خبر بسیار مشابه قبلاً در آرشیو وجود دارد.",
                reply_markup=main_reply_keyboard()
            )

            return

        # ====================================================
        # FACT EXTRACTION
        # ====================================================

        facts = await extract_facts(
            source
        )

        if status:

            try:

                await status.edit_text(
                    "🧠 اطلاعات اصلی مقاله استخراج شد.\n"
                    "در حال ساخت خبر..."
                )

            except Exception:
                pass

        # ====================================================
        # AI GENERATION
        # ====================================================

        generated = await generate_news(
            source,
            facts
        )

        valid, title, sentences = (
            validate_generated_output(
                generated
            )
        )

        # ====================================================
        # RETRY 1
        # ====================================================

        if not valid:

            log.warning(
                "AI output failed validation. Regenerating..."
            )

            generated = await generate_news(
                source,
                facts,
                retry_instruction=(
                    "\n\n"
                    "خروجی قبلی رد شده است.\n"
                    "این بار دقیقاً این ساختار را رعایت کن:\n\n"
                    "خط اول = یک تیتر فارسی\n"
                    "خط دوم = جمله اول\n"
                    "خط سوم = جمله دوم\n"
                    "خط چهارم = جمله سوم\n"
                    "خط پنجم = جمله چهارم\n"
                    "خط ششم = جمله پنجم\n"
                    "خط هفتم = جمله ششم\n"
                    "خط هشتم = جمله هفتم\n\n"
                    "هیچ جمله‌ای نباید با کلمه انگلیسی شروع شود.\n"
                    "اگر نام انگلیسی ابتدای جمله است، "
                    "ابتدا یک عبارت فارسی قرار بده.\n"
                    "هیچ Reviewer، AI، Fact یا توضیحی درباره فرآیند ننویس."
                )
            )

        # ====================================================
        # FACT COVERAGE
        # ====================================================

        if not check_important_fact_coverage(
            generated,
            facts
        ):

            log.warning(
                "Important facts may be missing. Regenerating..."
            )

            generated = await generate_news(
                source,
                facts,
                retry_instruction=(
                    "\n\n"
                    "نسخه قبلی بعضی اطلاعات مهم را از دست داده است.\n"
                    "تمام Factهای مهم استخراج‌شده را دوباره بررسی کن.\n"
                    "به‌خصوص تاریخ‌ها، اعداد، پلتفرم‌ها، حجم، قیمت، "
                    "بازیگران و وضعیت عرضه را در صورت وجود وارد کن.\n"
                    "خروجی فقط تیتر + 7 جمله باشد.\n"
                    "تیتر و هر 7 جمله حتماً با فارسی شروع شوند."
                )
            )

        # ====================================================
        # FINAL VALIDATION
        # ====================================================

        valid, title, sentences = (
            validate_generated_output(
                generated
            )
        )

        if not valid:

            # یک بار آخر تلاش برای اصلاح ساختار
            log.warning(
                "Final validation failed. Running final repair..."
            )

            generated = await generate_news(
                source,
                facts,
                retry_instruction=(
                    "\n\n"
                    "این آخرین تلاش برای اصلاح خروجی است.\n"
                    "خروجی باید دقیقاً شامل یک تیتر و 7 جمله باشد.\n"
                    "تیتر با فارسی شروع شود.\n"
                    "هر 7 جمله نیز با فارسی شروع شوند.\n"
                    "هیچ خط اضافه‌ای ننویس.\n"
                    "هیچ Markdown، Emoji، لینک، Reviewer یا AI Score ننویس.\n"
                    "نام‌های انگلیسی را فقط بعد از شروع فارسی استفاده کن."
                )
            )

            valid, title, sentences = (
                validate_generated_output(
                    generated
                )
            )

        if not valid:

            title, sentences = split_sentences(generated)

            if len(sentences) >= 5:

                sentences = sentences[:7]

                while len(sentences) < 7:
                    sentences.append(
                        "جزئیات بیشتری درباره این خبر منتشر نشده است."
                    )

                generated = (
                    title
                    + "\n"
                    + "\n".join(sentences)
                )

            else:

                raise RuntimeError(
                    "خروجی AI قابل اصلاح نیست."
                )

        # ====================================================
        # FORMAT
        # ====================================================

        post = format_post(
            generated,
            facts
        )
        post = enforce_gamefa_markdown(post)

        if not post:

            raise RuntimeError(
                "متن نهایی قابل تولید نیست."
            )

        # ====================================================
        # IMAGE
        # ====================================================

        image_path = await find_best_image(
            source
        )

        # ====================================================
        # MEMORY
        # ====================================================

        memory.append(
            {
                "hash": text_hash(
                    source_for_duplicate
                ),
                "title": source.get(
                    "title",
                    ""
                ),
                "source": source_for_duplicate[:25000],
                "post": post,
                "url": url or ""
            }
        )

        memory[:] = memory[
            -MAX_MEMORY:
        ]

        save_memory()

        # ====================================================
        # PREPARE
        # ====================================================

        prepared[user_id] = {
            "text": post,
            "image": (
                str(image_path)
                if image_path
                else ""
            )
        }

        # ====================================================
        # REMOVE STATUS
        # ====================================================

        if status:

            try:
                await status.delete()

            except Exception:
                pass

        # ====================================================
        # PREVIEW
        # ====================================================

        if image_path:
            # متن و تصویر عمداً در یک پیام ارسال می‌شوند.
            # post قبل از این مرحله برای محدودیت 1024 کاراکتر کپشن اعتبارسنجی شده است.
            try:
                await message.answer_photo(
                    BaleFile(image_path),
                    caption=post,
                    reply_markup=publish_keyboard()
                )
            except Exception as error:
                log.warning("Photo preview failed: %s", error)
                await message.answer(
                    post,
                    reply_markup=publish_keyboard()
                )
        else:
            await message.answer(
                post,
                reply_markup=publish_keyboard()
            )

        await message.answer(
            "✅ خبر آماده انتشار است.\n"
            "اگر متن و تصویر مناسب هستند، روی «📢 انتشار در کانال» بزن.",
            reply_markup=main_reply_keyboard()
        )

    except Exception as error:

        log.exception(
            "News processing error"
        )

        if status:

            try:
                await status.delete()

            except Exception:
                pass

        await message.answer(
            "❌ خطا هنگام پردازش خبر:\n\n"
            + str(error)[:1500],
            reply_markup=main_reply_keyboard()
        )

    finally:

        processing_users.discard(
            user_id
        )


# ============================================================
# PUBLISH
# ============================================================

async def publish_news(
    message,
    user_id
):

    item = prepared.get(
        user_id
    )

    if not item:

        await message.answer(
            "❌ هنوز خبری برای انتشار آماده نیست.",
            reply_markup=main_reply_keyboard()
        )

        return

    text = item.get(
        "text",
        ""
    )

    image = item.get(
        "image",
        ""
    )

    try:

        # ====================================================
        # WITH IMAGE
        # ====================================================

        if (
            image
            and Path(image).exists()
        ):

            try:
                await message.bot.send_photo(
                    CHANNEL_ID,
                    image,
                    caption=enforce_gamefa_markdown(text),
                    
                )
            except Exception as error:
                log.warning("Photo publish failed: %s", error)
                await message.bot.send_message(
                    CHANNEL_ID,
                    enforce_gamefa_markdown(text),
                    
                )

        # ====================================================
        # WITHOUT IMAGE
        # ====================================================

        else:

            await message.bot.send_message(
                CHANNEL_ID,
                enforce_gamefa_markdown(text),
                
            )

        await message.answer(
            "✅ خبر با موفقیت در کانال منتشر شد.",
            reply_markup=main_reply_keyboard()
        )

        prepared.pop(
            user_id,
            None
        )

    except Exception as error:

        log.exception(
            "Publish error"
        )

        await message.answer(
            "❌ خطا هنگام انتشار:\n\n"
            + str(error)[:1500],
            reply_markup=main_reply_keyboard()
        )




# ============================================================
# BALE KEYBOARDS
# ============================================================
def menu_keyboard(rows):
    return {"keyboard": [[{"text": x} for x in row] for row in rows], "resize_keyboard": True, "one_time_keyboard": False}

def main_reply_keyboard():
    return menu_keyboard([["🔎 بررسی خبر جدید", "📁 آرشیو"], ["📊 آمار", "⚙️ تنظیمات"]])

def news_reply_keyboard():
    return menu_keyboard([["📝 ارسال خبر", "🔗 ارسال لینک Gamefa"], ["🔙 بازگشت"]])

def archive_reply_keyboard():
    return menu_keyboard([["📚 آخرین اخبار", "🗑 پاکسازی آرشیو"], ["🔙 بازگشت"]])

def settings_reply_keyboard():
    return menu_keyboard([["📢 کانال انتشار", "🧠 مدل AI"], ["🖼 سیستم تصویر", "✍️ قالب خبر"], ["🔙 بازگشت"]])

def publish_keyboard():
    return {"inline_keyboard": [
        [{"text": "📢 انتشار در کانال", "callback_data": "publish_current"}],
        [{"text": "🔙 بازگشت", "callback_data": "home"}],
    ]}


def make_message(update):
    return BaleMessage(update.get("message", {}), BALE_BOT)


def get_user_id(update):
    if update.get("callback_query"):
        return int(update["callback_query"].get("from", {}).get("id", 0))
    return int(update.get("message", {}).get("from", {}).get("id", 0))

async def handle_message(message):
    if not message.text:
        return
    text = message.text.strip()
    if text == "/start":
        if not is_admin(message):
            await message.answer("⛔ این ربات خصوصی است.")
            return
        await message.answer("✨ <b>پنل مدیریت Gamefa</b>\n\nبه پنل مدیریت اخبار خوش آمدید.\nاز منوی زیر عملیات موردنظر را انتخاب کن.", reply_markup=main_reply_keyboard())
        return
    if not is_admin(message):
        return
    if text == "🔎 بررسی خبر جدید":
        await message.answer("🔎 <b>بررسی خبر جدید</b>\n\nیکی از گزینه‌های زیر را انتخاب کن.", reply_markup=news_reply_keyboard()); return
    if text == "📝 ارسال خبر":
        await message.answer("📝 متن خبر را ارسال کن.\n\nAI کل متن را تحلیل می‌کند و یک خبر ۷ جمله‌ای می‌سازد."); return
    if text == "🔗 ارسال لینک Gamefa":
        await message.answer("🔗 لینک مقاله Gamefa را ارسال کن.\n\nربات کل مقاله را دریافت می‌کند، اطلاعات اصلی را استخراج می‌کند و خبر را تولید می‌کند."); return
    if text == "📁 آرشیو":
        await message.answer("📁 <b>آرشیو اخبار</b>\n\nیک گزینه را انتخاب کن.", reply_markup=archive_reply_keyboard()); return
    if text == "📚 آخرین اخبار":
        if not memory:
            await message.answer("📚 آرشیو خالی است.", reply_markup=archive_reply_keyboard()); return
        lines=["📚 آخرین اخبار", ""]
        for index,item in enumerate(reversed(memory[-10:]),1):
            clean=strip_markup(item.get("post","")); first=clean.splitlines()[0] if clean else "خبر بدون عنوان"; lines.append(f"{index}. {first[:100]}")
        await message.answer("\n".join(lines), reply_markup=archive_reply_keyboard()); return
    if text == "🗑 پاکسازی آرشیو":
        await message.answer("⚠️ برای پاک کردن آرشیو دستور /clear را ارسال کن."); return
    if text == "📊 آمار":
        await message.answer(f"📊 <b>آمار ربات</b>\n\n📰 اخبار آرشیو: <b>{len(memory)}</b>\n💾 ظرفیت حافظه: <b>{MAX_MEMORY}</b>\n👤 مدیر: <code>{ADMIN_ID}</code>\n🧠 مدل: <code>{escape_html(MODEL)}</code>", reply_markup=main_reply_keyboard()); return
    if text == "⚙️ تنظیمات":
        await message.answer("⚙️ <b>تنظیمات</b>\n\nیک بخش را انتخاب کن.", reply_markup=settings_reply_keyboard()); return
    if text == "📢 کانال انتشار":
        await message.answer(f"📢 کانال انتشار:\n\n<code>{escape_html(CHANNEL_ID)}</code>", reply_markup=settings_reply_keyboard()); return
    if text == "🧠 مدل AI":
        await message.answer(f"🧠 مدل AI:\n\n<code>{escape_html(MODEL)}</code>", reply_markup=settings_reply_keyboard()); return
    if text == "🖼 سیستم تصویر":
        await message.answer("🖼 سیستم تصویر\n\nربات ابتدا تصویر اصلی og:image مقاله را پیدا می‌کند.\n\nاگر تصویر مناسب پیدا نشود، خبر بدون تصویر منتشر می‌شود.", reply_markup=settings_reply_keyboard()); return
    if text == "✍️ قالب خبر":
        await message.answer("✍️ <b>قالب خبر</b>\n\n• تیتر فارسی\n• دقیقاً ۷ جمله\n• یک پاراگراف واحد\n• اطلاعات مهم مقاله\n• تاریخ و اعداد در صورت وجود\n• حذف اطلاعات Reviewer\n• حذف اطلاعات AI\n• امضای Gamefa", reply_markup=settings_reply_keyboard()); return
    if text == "🔙 بازگشت":
        await message.answer("✨ <b>پنل مدیریت Gamefa</b>", reply_markup=main_reply_keyboard()); return
    if text == "/stats":
        await message.answer(f"📊 تعداد اخبار آرشیو: {len(memory)}", reply_markup=main_reply_keyboard()); return
    if text == "/clear":
        memory.clear(); save_memory(); prepared.clear(); await message.answer("✅ آرشیو با موفقیت پاک شد.", reply_markup=main_reply_keyboard()); return
    if text == "/publish":
        await publish_news(message, message.from_user.id); return
    if text.startswith("/"):
        return
    await process_news(message, text)

async def handle_callback(cq):
    user_id = int(cq.get("from", {}).get("id", 0))
    if not is_admin_id(user_id):
        await BALE_BOT.answer_callback(cq.get("id"), "⛔ دسترسی ندارید."); return
    data = cq.get("data", "")
    raw_msg = cq.get("message", {}) or {}
    msg = BaleMessage(raw_msg, BALE_BOT)
    if data == "publish_current":
        await BALE_BOT.answer_callback(cq.get("id"), "در حال انتشار...")
        await publish_news(msg, user_id)
    elif data == "home":
        await BALE_BOT.answer_callback(cq.get("id"), "")
        await msg.answer("✨ <b>پنل مدیریت Gamefa</b>", reply_markup=main_reply_keyboard())

async def main():
    global BALE_BOT
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN تنظیم نشده است.")
    if not OPENAI_API_KEY: raise RuntimeError("OPENAI_API_KEY تنظیم نشده است.")
    if not ADMIN_ID: raise RuntimeError("ADMIN_ID تنظیم نشده است.")
    if not CHANNEL_ID: raise RuntimeError("CHANNEL_ID تنظیم نشده است.")
    load_memory()
    BALE_BOT = BaleBotClient(BOT_TOKEN)
    await BALE_BOT.start()
    try:
        await BALE_BOT.delete_webhook()
    except Exception as e:
        log.warning("deleteWebhook failed: %s", e)
    offset = None
    log.info("Gamefa Bale Bot started | Admin=%s | Channel=%s | Model=%s | Memory=%s", ADMIN_ID, CHANNEL_ID, MODEL, len(memory))
    try:
        backoff = 2
        while True:
            try:
                # Keep the Bale long-poll short enough that transient network issues
                # cannot kill the Railway process.
                updates = await BALE_BOT.get_updates(offset=offset, timeout=20)
                backoff = 2
                for update in updates:
                    offset = int(update.get("update_id", 0)) + 1
                    try:
                        if update.get("callback_query"):
                            await handle_callback(update["callback_query"])
                        elif update.get("message"):
                            await handle_message(make_message(update))
                    except Exception:
                        log.exception("Update handling error")
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                log.warning("Bale getUpdates network error: %s. Retrying in %ss", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            except Exception:
                log.exception("Unexpected polling error")
                await asyncio.sleep(5)
    finally:
        await BALE_BOT.close()

if __name__ == "__main__":
    asyncio.run(main())
