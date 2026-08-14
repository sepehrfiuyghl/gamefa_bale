# Gamefa Bale Bot

نسخه بله‌ای ربات Gamefa، تبدیل‌شده از نسخه تلگرام.

## متغیرهای محیطی

- `BOT_TOKEN` = توکن ربات بله
- `OPENAI_API_KEY` = کلید OpenAI
- `ADMIN_ID` = شناسه عددی مدیر در بله
- `CHANNEL_ID` = شناسه/یوزرنیم کانال بله (مثلاً `@channelusername` یا chat id)
- `OPENAI_MODEL` = اختیاری؛ پیش‌فرض `gpt-5.4-mini`

## اجرا

```bash
pip install -r requirements.txt
python bot.py
```

برای Railway نیز Procfile آماده است.

نکته: ربات باید در کانال بله دسترسی ارسال پیام داشته باشد. اگر Webhook قبلی برای ربات فعال باشد، برنامه هنگام شروع آن را حذف می‌کند و با `getUpdates` کار می‌کند.
