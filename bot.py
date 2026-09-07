from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from rail_api import RailAPIError, RailClient, validate_journey_date
from storage import Store, Watch


load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("train-alert")
# Telegram API URLs contain the bot token. Keep HTTP client logs disabled so
# credentials can never be printed by Railway's runtime log collector.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

FROM, TO, DATE, CLASS, TRAIN, SEAT_COUNT, ADJACENT = range(7)
CLASSES = {"S_CHAIR", "SHOVAN", "SHULOV", "AC_S", "AC_B", "SNIGDHA", "F_SEAT", "F_BERTH"}

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_TELEGRAM_ID"])
DB_PATH = os.getenv("DATABASE_PATH", "data/watches.db")
INTERVAL = max(30, int(os.getenv("CHECK_INTERVAL_SECONDS", "60")))
BD_TZ = ZoneInfo("Asia/Dhaka")
CHROMIUM_UI_URL = os.getenv("CHROMIUM_UI_URL", "")
CHROMIUM_WEB_USERNAME = os.getenv("CHROMIUM_WEB_USERNAME", "")
CHROMIUM_WEB_PASSWORD = os.getenv("CHROMIUM_WEB_PASSWORD", "")

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
        "/target — ১৮ সেপ্টেম্বরের preset target চালু\n"
        "/login — Railway cloud browser-এ login\n"
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
    data["train_filter"] = train_filter
    await update.message.reply_text("কয়টি seat লাগবে? ১ থেকে ৪ লিখুন।")
    return SEAT_COUNT


@private
async def get_seat_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = update.message.text.strip()
    if not value.isdigit() or not 1 <= int(value) <= 4:
        await update.message.reply_text("এক booking-এ ১ থেকে ৪টি seat দেওয়া যাবে। আবার লিখুন।")
        return SEAT_COUNT
    context.user_data["watch"]["seat_count"] = int(value)
    await update.message.reply_text("সব seat পাশাপাশি লাগবে? YES অথবা NO লিখুন।")
    return ADJACENT


@private
async def get_adjacent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = update.message.text.strip().upper()
    if value not in {"YES", "NO"}:
        await update.message.reply_text("YES অথবা NO লিখুন।")
        return ADJACENT
    data = context.user_data["watch"]
    data["adjacent_required"] = value == "YES"
    data["check_start"] = "07:00"
    data["check_end"] = "09:00"
    watch_id = await store.add(**data)
    await update.message.reply_text(
        f"✅ Watch #{watch_id} চালু হয়েছে\n"
        f"{data['from_city']} → {data['to_city']}\n"
        f"📅 {data['journey_date']} | 💺 {data['seat_class']}\n"
        f"🚆 {data['train_filter'] or 'সব ট্রেন'}\n"
        f"👥 {data['seat_count']}টি {'পাশাপাশি ' if data['adjacent_required'] else ''}seat\n"
        f"⏰ প্রতিদিন 07:00–09:00 (Bangladesh time)\n\n"
        f"প্রতি {INTERVAL} সেকেন্ডে পরীক্ষা হবে।"
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
        lines.append(
            f"#{w.id} — {w.from_city} → {w.to_city} | {w.journey_date} | "
            f"{w.seat_class} | {w.train_filter or 'সব ট্রেন'} | {w.seat_count}টি"
            f"{' পাশাপাশি' if w.adjacent_required else ''} | {w.check_start}–{w.check_end}"
        )
    await update.message.reply_text("\n".join(lines))


@private
async def delete_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("ব্যবহার: /delete ID")
        return
    deleted = await store.delete(int(context.args[0]))
    await update.message.reply_text("✅ মুছে দেওয়া হয়েছে।" if deleted else "এই ID পাওয়া যায়নি।")


async def delete_sensitive_message(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data
    try:
        await context.bot.delete_message(data["chat_id"], data["message_id"])
    except Exception as exc:
        log.warning("Could not delete temporary login message: %s", exc)


@private
async def browser_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not CHROMIUM_UI_URL or not CHROMIUM_WEB_USERNAME or not CHROMIUM_WEB_PASSWORD:
        await update.message.reply_text("⚠️ Browser login service এখনো configure করা হয়নি।")
        return
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔐 Railway Browser খুলুন", url=CHROMIUM_UI_URL)]]
    )
    sent = await update.message.reply_text(
        "Railway cloud browser login\n\n"
        f"Username: `{CHROMIUM_WEB_USERNAME}`\n"
        f"Password: `{CHROMIUM_WEB_PASSWORD}`\n\n"
        "Browser খুলে Cloudflare verify করুন, তারপর Rail Sheba-তে login করুন।\n"
        "নিরাপত্তার জন্য এই message ৫ মিনিট পরে মুছে যাবে।",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    context.job_queue.run_once(
        delete_sensitive_message,
        when=300,
        data={"chat_id": sent.chat_id, "message_id": sent.message_id},
        name=f"delete-login-{sent.message_id}",
    )


@private
async def target_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = {
        "from_city": "Dhaka",
        "to_city": "Cox's Bazar",
        "journey_date": "2026-09-18",
        "seat_class": "S_CHAIR",
        "train_filter": "Cox",
        "seat_count": 4,
        "adjacent_required": True,
        "check_start": "07:00",
        "check_end": "09:00",
    }
    for watch in await store.list():
        if (
            watch.from_city == target["from_city"]
            and watch.to_city == target["to_city"]
            and watch.journey_date == target["journey_date"]
            and watch.seat_class == target["seat_class"]
            and watch.seat_count == target["seat_count"]
        ):
            await update.message.reply_text(f"✅ Target আগেই চালু আছে: Watch #{watch.id}")
            return
    watch_id = await store.add(**target)
    await update.message.reply_text(
        f"✅ Target Watch #{watch_id} চালু\n"
        "Dhaka → Cox's Bazar | 2026-09-18\n"
        "Cox's Bazar Express | S_CHAIR | ৪টি পাশাপাশি\n"
        "প্রতিদিন 07:00–09:00 (Bangladesh time)"
    )


def state_hash(results) -> str:
    visible = sorted((r.trip_number, r.seat_class, r.seats) for r in results if r.seats > 0)
    return hashlib.sha256(repr(visible).encode()).hexdigest() if visible else "EMPTY"


async def inspect_watch(app: Application, watch: Watch, notify_empty: bool = False) -> None:
    now = datetime.now(BD_TZ).time()
    start = time.fromisoformat(watch.check_start)
    end = time.fromisoformat(watch.check_end)
    if not (start <= now <= end) and not notify_empty:
        return
    try:
        results = await rail.search(watch.from_city, watch.to_city, watch.journey_date, watch.seat_class, watch.train_filter)
    except (RailAPIError, httpx.HTTPError) as exc:
        log.warning("Watch %s failed: %s", watch.id, exc)
        if notify_empty:
            await app.bot.send_message(ADMIN_ID, f"⚠️ Watch #{watch.id} পরীক্ষা করা যায়নি: {exc}")
        return
    available = [item for item in results if item.seats >= watch.seat_count]
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
                f"🎯 প্রয়োজন: {watch.seat_count}টি{' পাশাপাশি' if watch.adjacent_required else ''}\n"
                f"💳 ভাড়া: ৳{item.fare}\n"
                f"🕐 ছাড়বে: {item.departure}\n\n"
                "⚠️ মোট seat যথেষ্ট; পাশাপাশি আছে কি না seat-map খুলে নিশ্চিত করতে হবে।\n"
                "দ্রুত অফিসিয়াল পেজে গিয়ে বুক করুন।"
            )
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎟 Book Now", url=item.booking_url)]])
            await app.bot.send_message(ADMIN_ID, text, reply_markup=keyboard)
    elif notify_empty:
        total = max((item.seats for item in results), default=0)
        await app.bot.send_message(
            ADMIN_ID,
            f"Watch #{watch.id}: সর্বোচ্চ {total}টি matching seat; প্রয়োজন {watch.seat_count}টি।",
        )


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
        ("target", "১৮ সেপ্টেম্বরের target চালু"), ("login", "Railway browser login"),
        ("check", "এখনই পরীক্ষা"),
        ("delete", "watch মুছুন"), ("cancel", "বাতিল"),
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
            SEAT_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_seat_count)],
            ADJACENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_adjacent)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conversation)
    app.add_handler(CommandHandler("list", list_watches))
    app.add_handler(CommandHandler("target", target_watch))
    app.add_handler(CommandHandler("login", browser_login))
    app.add_handler(CommandHandler("delete", delete_watch))
    app.add_handler(CommandHandler("check", check_now))
    app.add_handler(CommandHandler("cancel", cancel))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
