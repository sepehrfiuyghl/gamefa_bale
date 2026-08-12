import os
import re
import logging
from html import unescape

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from bale import Bot

load_dotenv()

TOKEN = os.getenv("BALE_TOKEN")

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

bot = Bot(token=TOKEN)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0"
})


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


def is_admin(user_id):
    return int(user_id) in ADMIN_IDS


def get_url(text):

    if not text:
        return None

    match = re.search(
        r"https?://[^\s]+",
        text
    )

    if match:
        return match.group(0).rstrip(
            ").,،"
        )

    return None


def get_page(url):

    response = session.get(
        url,
        timeout=20
    )

    response.raise_for_status()

    response.encoding = response.apparent_encoding

    return BeautifulSoup(
        response.text,
        "html.parser"
    )


def get_title(soup):

    h1 = soup.find("h1")

    if h1:
        return clean(
            h1.get_text(" ", strip=True)
        )

    return "خبر گیمفا"



def get_image(soup):

    image = soup.find(
        "meta",
        property="og:image"
    )

    if image:
        return image.get(
            "content"
        )

    return None



def get_paragraphs(soup):

    result = []

    for p in soup.find_all("p"):

        text = clean(
            p.get_text(" ", strip=True)
        )

        if len(text) >= 50:

            result.append(text)

        if len(result) == 2:
            break


    return result



def create_caption(
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
""".strip()



@bot.event
async def on_ready():

    logging.info(
        "Gamefa Bale Bot is READY"
    )



@bot.event
async def on_message(message):

    user_id = message.author.id

    if not is_admin(user_id):
        return


    text = message.content or ""


    if text == "/start":

        await message.reply(
            "سلام 👋\n\n"
            "لینک خبر گیمفا را ارسال کنید."
        )

        return



    url = get_url(text)


    if not url:

        return


    if "gamefa.com" not in url:

        await message.reply(
            "❌ فقط لینک گیمفا قبول می‌شود."
        )

        return



    wait = await message.reply(
        "⏳ در حال پردازش خبر..."
    )


    try:

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


        if len(paragraphs) < 2:

            await wait.edit(
                "❌ دو پاراگراف پیدا نشد."
            )

            return



        caption = create_caption(
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


        await wait.edit(
            "✅ آماده شد."
        )


    except Exception as e:

        logging.exception(e)

        await wait.edit(
            f"❌ خطا:\n{e}"
        )



if __name__ == "__main__":

    bot.run()
