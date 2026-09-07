from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from rail_api import RailAPIError, RailClient, validate_journey_date
from storage import Store, Watch


load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("train-alert")

FROM, TO, DATE, CLASS, TRAIN = range(5)
CLASSES = {"S_CHAIR", "SHOVAN", "SHULOV", "AC_S", "AC_B", "SNIGDHA", "F_SEAT", "F_BERTH"}

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_TELEGRAM_ID"])
DB_PATH = os.getenv("DATABASE_PATH", "data/watches.db")
INTERVAL = max(30, int(os.getenv("CHECK_INTERVAL_SECONDS", "60")))

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
store = Store(DB_PATH)
rail = RailClient(os.environ["RAILWAY_MOBILE"], os.environ["RAILWAY_PASSWORD"])


def private(handler):
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user is None or update.effective_user.id != ADMIN_ID:
            if update.effective_message:
                await update.effective_message.reply_text("⛔ এই বটটি private।")
            return ConversationHandler.END
        return await handler(update, context)
    return wrapped


@private
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🚆 Train Ticket Alert Bot\n\n"
        "/watch — নতুন টিকিট খোঁজা শুরু\n"
        "/list — চলমান watch দেখুন\n"
        "/check — এখনই সবগুলো পরীক্ষা\n"
        "/delete ID — watch মুছুন\n"
        "/cancel — বর্তমান ধাপ বাতিল"
    )


@private
async def watch_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["watch"] = {}
    await update.message.reply_text("কোন স্টেশন থেকে? ইংরেজিতে লিখুন, যেমন: Dhaka")
    return FROM


@private
async def get_from(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["watch"]["from_city"] = update.message.text.strip()
    await update.message.reply_text("কোন স্টেশনে যাবেন? যেমন: Chattogram")
    return TO


@private
async def get_to(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = update.message.text.strip()
    if value.casefold() == context.user_data["watch"]["from_city"].casefold():
        await update.message.reply_text("শুরুর ও গন্তব্য স্টেশন একই হতে পারবে না। আবার লিখুন।")
        return TO
    context.user_data["watch"]["to_city"] = value
    await update.message.reply_text("যাত্রার তারিখ লিখুন: YYYY-MM-DD (যেমন 2026-09-15)")
    return DATE


@private
async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        context.user_data["watch"]["journey_date"] = validate_journey_date(update.message.text)
    except ValueError as exc:
        await update.message.reply_text(f"ভুল তারিখ: {exc}\nআবার YYYY-MM-DD লিখুন।")
        return DATE
    await update.message.reply_text("ক্লাস লিখুন:\nS_CHAIR, SHOVAN, SHULOV, AC_S, AC_B, SNIGDHA, F_SEAT, F_BERTH")
    return CLASS


@private
async def get_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = update.message.text.strip().upper()
    if value not in CLASSES:
        await update.message.reply_text("ক্লাসটি তালিকায় নেই। আবার লিখুন।")
        return CLASS
    context.user_data["watch"]["seat_class"] = value
    await update.message.reply_text("নির্দিষ্ট ট্রেন চাইলে নাম/নম্বর লিখুন। সব ট্রেন হলে লিখুন: ALL")
    return TRAIN


@private
async def get_train(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = context.user_data["watch"]
    train_filter = update.message.text.strip()
    if train_filter.upper() == "ALL":
        train_filter = ""
    watch_id = await store.add(train_filter=train_filter, **data)
    await update.message.reply_text(
        f"✅ Watch #{watch_id} চালু হয়েছে\n"
        f"{data['from_city']} → {data['to_city']}\n"
        f"📅 {data['journey_date']} | 💺 {data['seat_class']}\n"
        f"🚆 {train_filter or 'সব ট্রেন'}\n\nপ্রতি {INTERVAL} সেকেন্ডে পরীক্ষা হবে।"
    )
    context.user_data.pop("watch", None)
    return ConversationHandler.END


@private
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("watch", None)
    await update.message.reply_text("বাতিল করা হয়েছে।")
    return ConversationHandler.END


@private
async def list_watches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    watches = await store.list()
    if not watches:
        await update.message.reply_text("কোনো watch চালু নেই। /watch দিন।")
        return
    lines = ["🔎 চলমান Watch:"]
    for w in watches:
        lines.append(f"#{w.id} — {w.from_city} → {w.to_city} | {w.journey_date} | {w.seat_class} | {w.train_filter or 'সব ট্রেন'}")
    await update.message.reply_text("\n".join(lines))


@private
async def delete_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("ব্যবহার: /delete ID")
        return
    deleted = await store.delete(int(context.args[0]))
    await update.message.reply_text("✅ মুছে দেওয়া হয়েছে।" if deleted else "এই ID পাওয়া যায়নি।")


def state_hash(results) -> str:
    visible = sorted((r.trip_number, r.seat_class, r.seats) for r in results if r.seats > 0)
    return hashlib.sha256(repr(visible).encode()).hexdigest() if visible else "EMPTY"


async def inspect_watch(app: Application, watch: Watch, notify_empty: bool = False) -> None:
    try:
        results = await rail.search(watch.from_city, watch.to_city, watch.journey_date, watch.seat_class, watch.train_filter)
    except (RailAPIError, httpx.HTTPError) as exc:
        log.warning("Watch %s failed: %s", watch.id, exc)
        if notify_empty:
            await app.bot.send_message(ADMIN_ID, f"⚠️ Watch #{watch.id} পরীক্ষা করা যায়নি: {exc}")
        return
    available = [item for item in results if item.seats > 0]
    new_state = state_hash(results)
    should_alert = bool(available) and new_state != watch.last_state
    await store.set_state(watch.id, new_state)
    if should_alert:
        for item in available:
            text = (
                "🚨 টিকিট পাওয়া গেছে!\n\n"
                f"🚆 {item.train_name} ({item.trip_number})\n"
                f"🛤 {watch.from_city} → {watch.to_city}\n"
                f"📅 {watch.journey_date}\n"
                f"💺 {item.seat_class}: {item.seats}টি\n"
                f"💳 ভাড়া: ৳{item.fare}\n"
                f"🕐 ছাড়বে: {item.departure}\n\n"
                "দ্রুত অফিসিয়াল পেজে গিয়ে বুক করুন।"
            )
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎟 Book Now", url=item.booking_url)]])
            await app.bot.send_message(ADMIN_ID, text, reply_markup=keyboard)
    elif notify_empty:
        total = sum(item.seats for item in available)
        await app.bot.send_message(ADMIN_ID, f"Watch #{watch.id}: এখন {total}টি matching seat পাওয়া গেছে।")


async def check_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    for watch in await store.list():
        await inspect_watch(context.application, watch)


@private
async def check_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    watches = await store.list()
    if not watches:
        await update.message.reply_text("কোনো watch চালু নেই।")
        return
    await update.message.reply_text("🔄 পরীক্ষা করছি…")
    for watch in watches:
        await inspect_watch(context.application, watch, notify_empty=True)


async def post_init(app: Application) -> None:
    await store.init()
    await app.bot.set_my_commands([
        ("watch", "নতুন টিকিট watch"), ("list", "চলমান watch"),
        ("check", "এখনই পরীক্ষা"), ("delete", "watch মুছুন"), ("cancel", "বাতিল"),
    ])
    app.job_queue.run_repeating(check_job, interval=INTERVAL, first=10, name="ticket-checker")


async def post_shutdown(app: Application) -> None:
    await rail.close()


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    conversation = ConversationHandler(
        entry_points=[CommandHandler("watch", watch_start)],
        states={
            FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_from)],
            TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_to)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_date)],
            CLASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_class)],
            TRAIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_train)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conversation)
    app.add_handler(CommandHandler("list", list_watches))
    app.add_handler(CommandHandler("delete", delete_watch))
    app.add_handler(CommandHandler("check", check_now))
    app.add_handler(CommandHandler("cancel", cancel))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

