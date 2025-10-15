# CBot.py — ChillChat Community Bot (Webhook + FastAPI/Uvicorn)
# python-telegram-bot==20.3, fastapi, uvicorn
# Compatible with Python 3.13 (uses asyncio instead of JobQueue)

import os
import json
import re
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from telegram import (
    Update,
    InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters,
)

# =========================
#          CONFIG
# =========================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
GROUP_CHAT_ID = int(os.environ.get("GROUP_CHAT_ID", "0"))
DATACENTER_CHAT_ID = int(os.environ.get("DATACENTER_CHAT_ID", str(GROUP_CHAT_ID or 0)))

SUPPORT_USERNAME = os.environ.get("SUPPORT_USERNAME", "Incaseyoulostme")
CHANNEL_URL = os.environ.get("CHANNEL_URL", "")
GROUP_URL   = os.environ.get("GROUP_URL", "")
INSTAGRAM_URL = os.environ.get("INSTAGRAM_URL", "")

# Optional Google Sheets
GSPREAD_CREDS_JSON = os.environ.get("GSPREAD_CREDS_JSON")
SHEET_NAME = os.environ.get("SHEET_NAME", "EnglishClubRegistrations")

# Default event
DEFAULT_EVENTS = [
    {
        "id": "intro01",
        "title": "Introduction Meeting!",
        "when": "پنجشنبه ۲۴ مهر - ۱۸:۰۰",
        "place": "مشهد، صیاد شیرازی 5 ، پرستو 5 ، شمارنده 31",
        "maps": "https://nshn.ir/67_b14yf2JBebv",
        "price": "سفارش از کافه",
        "capacity": 12,
        "desc": "Our first ChillChat session — a friendly introduction meetup!",
    }
]
try:
    EVENTS = json.loads(os.environ.get("EVENTS_JSON", "")) or DEFAULT_EVENTS
    if not isinstance(EVENTS, list):
        EVENTS = DEFAULT_EVENTS
except Exception:
    EVENTS = DEFAULT_EVENTS

try:
    MEETUP_LINKS = json.loads(os.environ.get("MEETUP_LINKS_JSON", "{}"))
except Exception:
    MEETUP_LINKS = {}

# =========================
#     IN-MEMORY STORAGE
# =========================
PENDING = {}   # user_chat_id → info
ROSTER = {}    # event_id → list[dict]
ROSTER_MESSAGE_ID = None

# =========================
#         HELPERS
# =========================
def get_event(ev_id): return next((e for e in EVENTS if e.get("id") == ev_id), None)
def approved_count(ev_id): return len(ROSTER.get(ev_id, []))
def remaining_capacity(ev): 
    cap = int(ev.get("capacity", 0) or 0)
    return max(0, cap - approved_count(ev["id"])) if cap else 999999

# =========================
#   PINNED MESSAGE (JSON)
# =========================
JSON_START, JSON_END = "```json", "```"

def _build_human_roster_text():
    if not ROSTER:
        return "📋 لیست تاییدشده‌ها (DataCenter)\n— هنوز کسی تایید نشده."
    lines = ["📋 لیست تاییدشده‌ها (DataCenter)"]
    for ev in EVENTS:
        ev_id = ev["id"]
        people = ROSTER.get(ev_id, [])
        cap_txt = f" | ظرفیت: {len(people)}/{ev.get('capacity','∞')}" if ev.get("capacity") else ""
        lines.append(f"\n🗓 {ev['title']} — {ev['when']}{cap_txt}")
        if not people:
            lines.append("  — هنوز تاییدی نداریم")
        else:
            for i, r in enumerate(people, 1):
                uname = f"@{r['username']}" if r.get("username") else "—"
                lines.append(f"  {i}. {r['name']} | {uname} | {r.get('phone','—')}")
    return "\n".join(lines)

def _serialize_state_for_json():
    return {
        "events": [{"id": e["id"], "capacity": e.get("capacity")} for e in EVENTS],
        "roster": ROSTER,
    }

def _embed_text_with_json(human, data):
    return f"{human}\n\n---\n{JSON_START}\n{json.dumps(data,ensure_ascii=False)}\n{JSON_END}"

def _extract_json_from_text(text):
    m = re.search(r"```json\s*(\{.*\})\s*```", text or "", re.DOTALL)
    if not m: return None
    try: return json.loads(m.group(1))
    except: return None

async def load_state_from_pinned(app):
    global ROSTER_MESSAGE_ID, ROSTER
    if not DATACENTER_CHAT_ID: return
    try:
        chat = await app.bot.get_chat(DATACENTER_CHAT_ID)
        pin = getattr(chat, "pinned_message", None)
        if not pin: return
        ROSTER_MESSAGE_ID = pin.message_id
        data = _extract_json_from_text(pin.text or pin.caption or "")
        if data and isinstance(data.get("roster"), dict):
            ROSTER = data["roster"]
    except Exception as e:
        print("load_state_from_pinned:", e)

async def save_state_to_pinned(app):
    global ROSTER_MESSAGE_ID
    if not DATACENTER_CHAT_ID: return
    human = _build_human_roster_text()
    payload = _serialize_state_for_json()
    full = _embed_text_with_json(human, payload)
    if ROSTER_MESSAGE_ID:
        try:
            await app.bot.edit_message_text(chat_id=DATACENTER_CHAT_ID,
                                            message_id=ROSTER_MESSAGE_ID, text=full)
            return
        except: pass
    try:
        msg = await app.bot.send_message(chat_id=DATACENTER_CHAT_ID, text=full)
        if ROSTER_MESSAGE_ID:
            try: await app.bot.unpin_chat_message(DATACENTER_CHAT_ID, ROSTER_MESSAGE_ID)
            except: pass
        try: await app.bot.pin_chat_message(DATACENTER_CHAT_ID, msg.message_id, disable_notification=True)
        except: pass
        ROSTER_MESSAGE_ID = msg.message_id
    except Exception as e:
        print("save_state_to_pinned:", e)

# =========================
#   AUTO APPROVE (ASYNCIO)
# =========================
async def delayed_auto_approve(app, user_chat_id: int, ev_id: str, delay: int = 60):
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    info = PENDING.get(user_chat_id)
    if not info: return
    ev = get_event(ev_id)
    if not ev:
        PENDING.pop(user_chat_id, None)
        return
    if ev.get("capacity") and remaining_capacity(ev) <= 0:
        try: await app.bot.send_message(chat_id=user_chat_id, text="❌ ظرفیت تکمیل شد.")
        except: pass
        try:
            if info.get("admin_msg_id"):
                await app.bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=info["admin_msg_id"])
        except: pass
        PENDING.pop(user_chat_id, None)
        return
    ROSTER.setdefault(ev_id, []).append({
        "name": info.get("name","—"),
        "username": info.get("username"),
        "phone": info.get("phone","—"),
        "when": info.get("when","—"),
        "event_title": info.get("event_title","—"),
    })
    await save_state_to_pinned(app)
    msg = (f"🎉 ثبت‌نامت تایید شد!\n\n📌 {ev['title']}\n🕒 {ev['when']}\n📍 {ev['place']}\n🗺️ {ev['maps']}")
    try: await app.bot.send_message(chat_id=user_chat_id, text=msg)
    except: pass
    try:
        if info.get("admin_msg_id"):
            await app.bot.delete_message(GROUP_CHAT_ID, info["admin_msg_id"])
    except: pass
    PENDING.pop(user_chat_id, None)

# =========================
#   COMMANDS / CALLBACKS
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام 👋 خوش اومدی به ChillChat Community!")
    await update.message.reply_text("برای شروع، یکی از گزینه‌ها رو انتخاب کن:")

async def cmd_testpin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await save_state_to_pinned(context.application)
    await update.message.reply_text("✅ لیست در دیتاسنتر پین شد یا به‌روزرسانی شد.")

# تایید/رد توسط ادمین
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    if data.startswith("approve_") or data.startswith("reject_"):
        try:
            action, user_chat_id, ev_id = data.split("_", 2)
            user_chat_id = int(user_chat_id)
            ev = get_event(ev_id)
            info = PENDING.get(user_chat_id)
            if not info: return await q.answer("قبلاً بررسی شده.")
            # لغو تاخیر
            if info.get("task"):
                try: info["task"].cancel()
                except: pass
            if action == "approve":
                ROSTER.setdefault(ev_id, []).append({
                    "name": info["name"],
                    "username": info["username"],
                    "phone": info["phone"],
                    "when": info["when"],
                    "event_title": info["event_title"],
                })
                await save_state_to_pinned(context.application)
                msg = (f"🎉 ثبت‌نامت تایید شد!\n📌 {ev['title']}\n🕒 {ev['when']}")
                await context.bot.send_message(user_chat_id, text=msg)
                await q.edit_message_text(q.message.text + "\n✅ تایید شد.")
            else:
                await context.bot.send_message(user_chat_id, text="❌ درخواستت رد شد.")
                await q.edit_message_text(q.message.text + "\n❌ رد شد.")
            PENDING.pop(user_chat_id, None)
            await q.answer("انجام شد.")
        except Exception as e:
            print("admin callback:", e)
            await q.answer("خطا.")
    else:
        await q.answer()

# =========================
#   ثبت نام نمونه (ساده)
# =========================
async def finalize_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ev = EVENTS[0]
    user_chat_id = update.effective_chat.id
    info = {
        "name": "Tester",
        "phone": "000",
        "level": "A1",
        "note": "-",
        "event_id": ev["id"],
        "event_title": ev["title"],
        "when": ev["when"],
        "username": update.effective_user.username,
    }
    PENDING[user_chat_id] = info
    task = context.application.create_task(
        delayed_auto_approve(context.application, user_chat_id, ev["id"], delay=60)
    )
    PENDING[user_chat_id]["task"] = task
    await update.message.reply_text("✅ درخواست ثبت شد و برای ادمین ارسال گردید (auto-approve بعد از ۶۰ث).")

# =========================
#   FASTAPI + LIFESPAN
# =========================
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

application = ApplicationBuilder().token(BOT_TOKEN).job_queue(None).build()
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(CommandHandler("testpin", cmd_testpin))
application.add_handler(CallbackQueryHandler(handle_callback))
application.add_handler(CommandHandler("registertest", finalize_and_send))

@asynccontextmanager
async def lifespan(app: FastAPI):
    await application.initialize()
    await load_state_from_pinned(application)
    if WEBHOOK_URL:
        await application.bot.set_webhook(WEBHOOK_URL)
    await application.start()
    yield
    await application.stop()
    await application.shutdown()

app = FastAPI(lifespan=lifespan)

@app.post("/")
async def webhook(request: Request):
    body = await request.json()
    update = Update.de_json(body, application.bot)
    await application.process_update(update)
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"status": "ChillChat bot running (asyncio auto-approve OK)"}

