import os
import re
import logging
from html import unescape
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import bale


load_dotenv()

TOKEN = os.getenv("BALE_TOKEN")
ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)


if not TOKEN:
    raise Exception("BALE_TOKEN تنظیم نشده است")


bot = bale.Bot(token=TOKEN)


session = requests.Session()

session.headers.update({
    "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
})


# -------------------------
# ابزارها
# -------------------------

def is_admin(user_id):
    return int(user_id) in ADMIN_IDS


def clean(text):
    if not text:
        return ""

    text = unescape(text)
    text = text.replace("\xa0", " ")

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


def get_url(text):

    match = re.search(
        r"https?://[^\s]+",
        text
    )

    if match:
        return match.group(0)

    return None



def valid_gamefa(url):

    try:

        host = urlparse(url).netloc

        return (
            "gamefa.com" in host
        )

    except:

        return False



# -------------------------
# دریافت مقاله
# -------------------------

def get_page(url):

    r = session.get(
        url,
        timeout=20
    )

    r.raise_for_status()

    return BeautifulSoup(
        r.text,
        "html.parser"
    )



def get_title(soup):

    h1 = soup.find("h1")

    if h1:
        return clean(
            h1.text
        )

    return "خبر گیمفا"



def get_image(soup):

    img = soup.find(
        "meta",
        property="og:image"
    )

    if img:

        return img.get(
            "content"
        )

    return None



def get_paragraphs(soup):

    paragraphs=[]


    article = soup.find(
        "article"
    )


    if not article:

        article = soup



    for p in article.find_all("p"):

        text = clean(
            p.text
        )

        if len(text) > 50:

            paragraphs.append(
                text
            )


        if len(paragraphs)==2:

            break



    return paragraphs



# -------------------------
# ساخت متن
# -------------------------

def make_caption(
    title,
    paragraphs,
    url
):

    return f"""
📢 *[{title}]({url})*

🟣 {paragraphs[0]}

🟣 {paragraphs[1]}

*[ادامه خبر📑]({url})*

🆔 *@Gamefa_official*
"""



# -------------------------
# پیام ها
# -------------------------

@bot.event
async def on_ready():

    logging.info(
        "Gamefa Bale Bot Started"
    )



@bot.event
async def on_message(message):

    try:

        user_id = message.author.id


        if not is_admin(user_id):

            return



        text = message.content


        if text == "/start":

            await message.reply(
                "✅ ربات اخبار گیمفا فعال است.\n\n"
                "لینک خبر گیمفا را ارسال کنید."
            )

            return



        url = get_url(text)


        if not url:

            return



        if not valid_gamefa(url):

            await message.reply(
                "❌ فقط لینک Gamefa قبول می‌شود."
            )

            return



        await message.reply(
            "⏳ در حال دریافت خبر..."
        )


        soup = get_page(url)


        title = get_title(
            soup
        )


        image = get_image(
            soup
        )


        paragraphs = get_paragraphs(
            soup
        )


        if len(paragraphs)<2:

            await message.reply(
                "❌ دو پاراگراف پیدا نشد."
            )

            return



        caption = make_caption(
            title,
            paragraphs,
            url
        )


        if image:

            await bot.send_photo(
                chat_id=user_id,
                photo=image,
                caption=caption
            )

        else:

            await message.reply(
                caption
            )



    except Exception as e:

        logging.error(
            e
        )



# -------------------------
# اجرای اصلی
# -------------------------

if __name__ == "__main__":

    bot.run()
