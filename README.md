# Private Bangladesh Railway Ticket Alert Bot

নিজের জন্য একটি private Telegram bot। নির্দিষ্ট রুট, তারিখ, ক্লাস ও ঐচ্ছিক ট্রেন নিয়মিত পরীক্ষা করে; সিট পাওয়া গেলে অফিসিয়াল booking link-সহ Telegram alert দেয়। এটি টিকিট reserve করে না, CAPTCHA bypass করে না এবং payment করে না।

## Bot commands

- `/watch` — ধাপে ধাপে নতুন watch তৈরি
- `/list` — active watch তালিকা
- `/check` — এখনই পরীক্ষা
- `/delete ID` — watch মুছে দেওয়া
- `/cancel` — চলমান setup বাতিল

## দরকারি তথ্য

1. Telegram-এ `@BotFather` খুলে `/newbot` দিয়ে bot token নিন।
2. `@userinfobot` থেকে নিজের numeric Telegram ID নিন।
3. আপনার বৈধ Rail Sheba mobile number ও password প্রস্তুত রাখুন।

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# .env-এ নিজের মানগুলো দিন
python bot.py
```

Windows activation: `.venv\\Scripts\\activate`

## Railway deployment

1. এই folder GitHub-এর private repository-তে push করুন। `.env` কখনও commit করবেন না।
2. Railway-তে **New Project → Deploy from GitHub Repo** নির্বাচন করুন।
3. Variables-এ `.env.example`-এর ছয়টি key যোগ করুন।
4. একটি persistent volume `/app/data`-তে mount করে `DATABASE_PATH=/app/data/watches.db` দিন।
5. Deploy করুন। Logs-এ polling চালু হলে Telegram-এ `/start` দিন।

## গুরুত্বপূর্ণ

- `CHECK_INTERVAL_SECONDS` ৩০-এর নিচে দিলেও app ৩০ সেকেন্ড ব্যবহার করবে। প্রস্তাবিত ৬০ সেকেন্ড।
- Railway/Shohoz API বদলালে adapter আপডেট লাগতে পারে।
- Login credentials শুধু deployment secret variables-এ রাখুন।
- Official booking page-এই OTP/CAPTCHA/payment সম্পন্ন করুন।

