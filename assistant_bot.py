"""
Personal assistant Telegram bot
- Morning: asks "What's today's schedule? What are the things to be done?" + shows planned tasks
- Night:   asks what you did / skipped, tap buttons to mark each task
- Commands: /start /today /add /summary /tomorrow /setmorning /setnight

Setup:
    pip install "python-telegram-bot[job-queue]==21.6"
    export BOT_TOKEN="123456:ABC..."     # from @BotFather
    python assistant_bot.py
Then open your bot in Telegram and send /start (this locks the bot to you).
"""
import json
import logging
import asyncio
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

TZ = ZoneInfo("Asia/Kolkata")
DATA_FILE = "data.json"
ICON = {None: "⬜", True: "✅", False: "❌"}  # pending / done / skipped

logging.basicConfig(level=logging.INFO)


# ---------- storage ----------
def load():
    if not os.path.exists(DATA_FILE):
        return {"chat_id": None, "morning": "08:00", "night": "21:30", "plan": {}}
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def save(d):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)


def today_key():
    return datetime.now(TZ).date().isoformat()


def parse_day(word):
    w = word.lower()
    if w == "today":
        return today_key()
    if w == "tomorrow":
        return (datetime.now(TZ).date() + timedelta(days=1)).isoformat()
    return date.fromisoformat(word).isoformat()  # YYYY-MM-DD


def pretty(day):
    return date.fromisoformat(day).strftime("%a %d %b")


def owner_only(handler):
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        d = load()
        if d["chat_id"] and update.effective_chat.id != d["chat_id"]:
            return
        return await handler(update, context)
    return wrapped


# ---------- message builders ----------
def task_list_text(day, tasks):
    if not tasks:
        return "Nothing planned yet."
    return "\n".join(f"{ICON[t['done']]} {t['t']}" for t in tasks)


def keyboard(day, tasks):
    rows = [
        [InlineKeyboardButton(f"{ICON[t['done']]} {t['t']}"[:60], callback_data=f"t|{day}|{i}")]
        for i, t in enumerate(tasks)
    ]
    return InlineKeyboardMarkup(rows)


def day_stats(tasks):
    done = sum(1 for t in tasks if t["done"] is True)
    skipped = sum(1 for t in tasks if t["done"] is not True)
    return done, skipped


# ---------- carry-over ----------
def carry_over(d, day):
    """Move yesterday's unfinished 'roll' tasks (study/practice) into today."""
    prev = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    moved = 0
    for t in d["plan"].get(prev, []):
        if t.get("roll") and t["done"] is not True and not t.get("carried"):
            t["carried"] = True
            name = t["t"] if t["t"].startswith("↪") else "↪ " + t["t"]
            d["plan"].setdefault(day, []).append({"t": name, "done": None, "roll": True})
            moved += 1
    return moved


# ---------- scheduled jobs ----------
async def morning_job(context: ContextTypes.DEFAULT_TYPE):
    d = load()
    if not d["chat_id"]:
        return
    day = today_key()
    moved = carry_over(d, day)
    save(d)
    tasks = d["plan"].get(day, [])
    text = (
        f"Good morning! 🌅  ({pretty(day)})\n\n"
        "What's today's schedule? What are the things to be done?\n\n"
        f"Planned for today:\n{task_list_text(day, tasks)}\n\n"
        + (f"↪ {moved} unfinished task(s) carried over from yesterday.\n" if moved else "")
        + "Add anything else with /add today <task>"
    )
    await context.bot.send_message(d["chat_id"], text)


async def night_job(context: ContextTypes.DEFAULT_TYPE):
    d = load()
    if not d["chat_id"]:
        return
    day = today_key()
    tasks = d["plan"].get(day, [])
    if not tasks:
        await context.bot.send_message(d["chat_id"], "Day's over. Nothing was planned today — what did you get done?")
        return
    await context.bot.send_message(
        d["chat_id"],
        "🌙 End of day check-in.\nWhat did you do, and what did you skip?\n"
        "Tap a task to cycle: ⬜ pending → ✅ done → ❌ skipped",
        reply_markup=keyboard(day, tasks),
    )


def schedule_jobs(app: Application):
    d = load()
    for name in ("morning", "night"):
        for j in app.job_queue.get_jobs_by_name(name):
            j.schedule_removal()
    mh, mm = map(int, d["morning"].split(":"))
    nh, nm = map(int, d["night"].split(":"))
    app.job_queue.run_daily(morning_job, time(mh, mm, tzinfo=TZ), name="morning")
    app.job_queue.run_daily(night_job, time(nh, nm, tzinfo=TZ), name="night")


# ---------- commands ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = load()
    if d["chat_id"] and update.effective_chat.id != d["chat_id"]:
        return
    d["chat_id"] = update.effective_chat.id
    save(d)
    await update.message.reply_text(
        f"Hey! I'll message you every morning at {d['morning']} and check in at {d['night']} (IST).\n\n"
        "/today – today's tasks\n"
        "/tomorrow – tomorrow's tasks\n"
        "/add <today|tomorrow|YYYY-MM-DD> <task>\n"
        "/summary – progress so far\n"
        "/setmorning HH:MM, /setnight HH:MM"
    )


@owner_only
async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    day = today_key()
    tasks = load()["plan"].get(day, [])
    await update.message.reply_text(
        f"{pretty(day)}\n{task_list_text(day, tasks)}",
        reply_markup=keyboard(day, tasks) if tasks else None,
    )


@owner_only
async def tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    day = parse_day("tomorrow")
    tasks = load()["plan"].get(day, [])
    await update.message.reply_text(f"{pretty(day)}\n{task_list_text(day, tasks)}")


@owner_only
async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /add <today|tomorrow|YYYY-MM-DD> <task>")
        return
    try:
        day = parse_day(context.args[0])
    except ValueError:
        await update.message.reply_text("Date must be today, tomorrow or YYYY-MM-DD.")
        return
    d = load()
    d["plan"].setdefault(day, []).append({"t": " ".join(context.args[1:]), "done": None, "roll": True})
    save(d)
    await update.message.reply_text(f"Added to {pretty(day)} ✔")


@owner_only
async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = load()
    now = today_key()
    lines, tot_done, tot_all = [], 0, 0
    for day in sorted(d["plan"]):
        tasks = d["plan"][day]
        done, skipped = day_stats(tasks)
        tot_done += done
        tot_all += len(tasks)
        if day <= now:
            lines.append(f"{pretty(day)}: {done}/{len(tasks)} done")
    pct = round(100 * tot_done / tot_all) if tot_all else 0
    await update.message.reply_text(
        "📊 Progress\n" + ("\n".join(lines) or "No days yet.") + f"\n\nOverall: {tot_done}/{tot_all} tasks done ({pct}%)"
    )


async def _set_time(update, context, key):
    try:
        h, m = map(int, context.args[0].split(":"))
        assert 0 <= h < 24 and 0 <= m < 60
    except Exception:
        await update.message.reply_text(f"Usage: /set{key} HH:MM (24h, IST)")
        return
    d = load()
    d[key] = f"{h:02d}:{m:02d}"
    save(d)
    schedule_jobs(context.application)
    await update.message.reply_text(f"{key.capitalize()} time set to {d[key]} ✔")


@owner_only
async def setmorning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_time(update, context, "morning")


@owner_only
async def setnight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_time(update, context, "night")


# ---------- button taps ----------
async def on_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    d = load()
    if q.message.chat.id != d["chat_id"]:
        return
    _, day, idx = q.data.split("|")
    tasks = d["plan"].get(day, [])
    t = tasks[int(idx)]
    t["done"] = {None: True, True: False, False: None}[t["done"]]
    save(d)
    done, skipped = day_stats(tasks)
    await q.answer(f"{done} done · {skipped} not done")
    await q.edit_message_reply_markup(keyboard(day, tasks))


def main():
    app = Application.builder().token(os.environ["BOT_TOKEN"]).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("tomorrow", tomorrow))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CommandHandler("setmorning", setmorning))
    app.add_handler(CommandHandler("setnight", setnight))
    app.add_handler(CallbackQueryHandler(on_tap, pattern=r"^t\|"))
    schedule_jobs(app)
    asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling()


if __name__ == "__main__":
    main()
