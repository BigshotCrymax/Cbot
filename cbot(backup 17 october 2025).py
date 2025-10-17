# CBot.py — ChillChat Community Bot (Webhook + FastAPI/Uvicorn)
# python-telegram-bot==20.3, fastapi, uvicorn
# سازگار با Python 3.13 — بدون JobQueue؛ از asyncio استفاده می‌کند

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
GROUP_CHAT_ID = int(os.environ.get("GROUP_CHAT_ID", "0"))  # گروه ادمین/دیتاسنتر
DATACENTER_CHAT_ID = int(os.environ.get("DATACENTER_CHAT_ID", str(GROUP_CHAT_ID or 0)))  # اگر جداست ست کن

SUPPORT_USERNAME = os.environ.get("SUPPORT_USERNAME", "ifyoulostme")
CHANNEL_URL = os.environ.get("CHANNEL_URL", "")
GROUP_URL   = os.environ.get("GROUP_URL", "")
INSTAGRAM_URL = os.environ.get("INSTAGRAM_URL", "")
CAFE_INTRO_USERNAME = os.environ.get("CAFE_INTRO_USERNAME", "ifyoulostme")  # برای «معرفی کافه»

# (Optional) Google Sheets
GSPREAD_CREDS_JSON = os.environ.get("GSPREAD_CREDS_JSON")
SHEET_NAME = os.environ.get("SHEET_NAME", "EnglishClubRegistrations")

# تایید خودکار پس از 12 ساعت
AUTO_APPROVE_DELAY = 12 * 60 * 60  # 43200 ثانیه

# نمایش JSON در پیام پین‌شده؟ (پیش‌فرض خاموش)
SHOW_JSON_IN_PINNED = os.environ.get("SHOW_JSON_IN_PINNED", "0") == "1"

# سقف آقایان در هر رویداد
MALE_LIMIT_PER_EVENT = int(os.environ.get("MALE_LIMIT_PER_EVENT", "5"))

# === Owner (username/id) + Admins (username/id) ===
OWNER_USER_ID_RAW = os.environ.get("OWNER_USER_ID", "").strip()
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "").strip().lower().lstrip("@")

def _parse_id_set(val: str):
    out = set()
    for p in (val or "").replace(";", ",").split(","):
        p = p.strip()
        if not p:
            continue
        try:
            out.add(int(p))
        except ValueError:
            pass
    return out

def _parse_username_set(val: str):
    out = set()
    for p in (val or "").replace(";", ",").split(","):
        p = p.strip().lower().lstrip("@")
        if p:
            out.add(p)
    return out

ADMIN_USER_IDS = _parse_id_set(os.environ.get("ADMIN_USER_IDS", ""))
ADMIN_USERNAMES = _parse_username_set(os.environ.get("ADMIN_USERNAMES", ""))

# --- DEFAULT EVENTS (override via EVENTS_JSON) ---
DEFAULT_EVENTS = [
    {
        "id": "4002",
        "title": "2nd Meeting!",
        "when": "پنجشنبه ۱ آبان- ۱۸:۰۰",
        "price": "سفارش از کافه",
        "capacity": 12,
        "desc": "The legendary 2nd session. It's all gonna be about chill & chat. btw topic will be decided in the group. stay tuned!",
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
# PENDING: درخواست‌های در انتظار تایید
# هر ورودی: {name, phone, level, note, gender, age, event_id, event_title, when, username, admin_msg_id, task}
PENDING = {}  # key: user_chat_id -> dict
# ROSTER: افراد تاییدشده به تفکیک رویداد
# هر آیتم: {name, username, phone, gender, age, when, event_title, user_chat_id}
ROSTER = {}   # key: event_id -> list[dict]
ROSTER_MESSAGE_ID = None  # پیام پین‌شده دیتاسنتر

# =========================
#          TEXTS
# =========================
reply_main = ReplyKeyboardMarkup([["شروع مجدد 🔄"]], resize_keyboard=True)

WELCOME = (
    "سلام! به *ChillChat Community* خوش اومدی ☕🇬🇧\n"
    "اینجا می‌تونی رویدادهای زبان انگلیسی رو ببینی و ثبت‌نام کنی."
)

FAQ = (
    "❔ **سوالات متداول درباره ChillChat**\n\n"
    "🗣️ **در جلسات چی کار می‌کنیم؟**\n"
    "با بقیه به انگلیسی صحبت می‌کنی، بازی می‌کنیم، موضوع‌های روز رو تمرین می‌کنیم، و کلی آشناهای جدید پیدا می‌کنی!\n\n"
    "☕ **کجا برگزار می‌شن؟**\n"
    "جلسات در کافه برگزار می‌شن و برای همه سطوح بازن.\n\n"
    "💶 **هزینه شرکت چقدره؟**\n"
    "رویدادها معمولاً رایگان هستن؛ فقط لازمه یک سفارش از کافه داشته باشی.\n\n"
    "📸 **عکس‌برداری**\n"
    "گاهی بله—با رضایت شرکت‌کننده‌ها برای شبکه‌های اجتماعی.\n\n"
    "📝 **ثبت‌نام**\n"
    "درخواستت برای ادمین ارسال میشه و بعد از تایید، اسمت در لیست شرکت‌کنندگان قرار می‌گیره."
)

RULES = (
    "⚠️ **قوانین ChillChat**\n\n"
    "💬 با احترام رفتار کن.\n"
    "🗣️ تا جای ممکن انگلیسی صحبت کن (هدفمون تمرینه).\n"
    "⏰ به‌موقع بیا؛ شروع دقیق.\n"
    "📱 گوشی بی‌صدا.\n"
    "🙏 اگر نمی‌تونی شرکت کنی، زودتر اطلاع بده.\n\n"
    "با رعایت این موارد، تجربهٔ بهتری خواهیم داشت ☕❤️"
)

# معرفی کافه — متن کوتاه
CAFE_INTRO_TEXT = "اسم و آدرس کافهٔ پیشنهادی‌ت رو برای @" + CAFE_INTRO_USERNAME + " بفرست 🙌"

# پیام‌های ظرفیت
CAPACITY_CANCEL_MSG = (
    "❌ ثبت‌نام شما به دلیل *تکمیل ظرفیت* لغو شد.\n"
    "برای شرکت در برنامه‌های بعدی، از «🎉 رویدادهای پیش‌رو» رویداد دیگری را انتخاب کنید."
)
CAPACITY_FULL_PREVENT_MSG = "❌ ظرفیت این رویداد تکمیل است. لطفاً رویداد دیگری را انتخاب کن."
MALE_CAPACITY_FULL_MSG = "❌ ظرفیت شرکت‌کنندگان برای این رویداد تکمیل شده است."

JOIN_CHANNEL_MSG = (
    "📢 برای اینکه از اخبار، زمان دقیق آدرس و هماهنگی‌ها جا نمونی، لطفاً در کانال ما عضو شو:\n"
)

def SOCIAL_TEXT():
    return (
        "🌐 **ما را در شبکه‌های اجتماعی دنبال کن:**\n\n"
        + (f"📢 [کانال تلگرام]({CHANNEL_URL})\n" if CHANNEL_URL else "")
        + (f"💬 [گروه تلگرام]({GROUP_URL})\n" if GROUP_URL else "")
        + (f"📸 [اینستاگرام]({INSTAGRAM_URL})\n" if INSTAGRAM_URL else "")
        + ("\nبزودی لینک‌ها تکمیل می‌شوند." if not (CHANNEL_URL or GROUP_URL or INSTAGRAM_URL) else "")
    )

# =========================
#          HELPERS
# =========================
def get_event(ev_id):
    return next((e for e in EVENTS if e.get("id") == ev_id), None)

def approved_count(ev_id: str) -> int:
    return len(ROSTER.get(ev_id, []))

def remaining_capacity(ev: dict) -> int:
    cap = int(ev.get("capacity", 0) or 0)
    return max(0, cap - approved_count(ev["id"])) if cap else 999999

def male_count(ev_id: str) -> int:
    return sum(1 for r in ROSTER.get(ev_id, []) if r.get("gender") == "male")

def event_text_user(ev):
    # بدون نمایش ظرفیت
    parts = [
        f"**{ev.get('title','')}**",
        f"🕒 {ev.get('when','')}",
        f"💶 {ev.get('price','') or 'Free'}",
    ]
    if ev.get("desc"):  parts.append(f"📝 {ev['desc']}")
    parts.append("\n(آدرس دقیق کافه پیش از رویداد در ChillChat Official اعلام می‌شود.)")
    return "\n".join(parts)

def event_text_admin(ev):
    # برای ادمین — با ظرفیت
    cap_line = ""
    if ev.get("capacity"):
        cap_line = f"👥 ظرفیت: {approved_count(ev['id'])}/{ev['capacity']}\n"
    return (
        f"📌 {ev.get('title','')}\n"
        f"🕒 {ev.get('when','')}\n"
        f"{cap_line}"
        f"💶 {ev.get('price','Free')}\n"
        f"📝 {ev.get('desc','—')}"
    )

def push_step(context, step):
    nav = context.user_data.get("nav", [])
    nav.append(step)
    context.user_data["nav"] = nav

def pop_step(context):
    nav = context.user_data.get("nav", [])
    if nav: nav.pop()
    context.user_data["nav"] = nav
    return nav[-1] if nav else None

def current_step(context):
    nav = context.user_data.get("nav", [])
    return nav[-1] if nav else None

def clear_flow(context):
    for k in ["nav","origin","selected_event_id","name","phone","level","gender","age","note","feedback_mode"]:
        context.user_data.pop(k, None)

# ====== Auth helpers (owner/admin in datacenter) ======
def _owner_match(user) -> bool:
    if not user:
        return False
    uid_ok = False
    if OWNER_USER_ID_RAW:
        try:
            uid_ok = int(OWNER_USER_ID_RAW) == user.id
        except ValueError:
            uid_ok = False
    uname_ok = False
    if OWNER_USERNAME:
        uname_ok = (user.username or "").strip().lower() == OWNER_USERNAME
    return uid_ok or uname_ok

def _admin_match(user) -> bool:
    if not user:
        return False
    if user.id in ADMIN_USER_IDS:
        return True
    uname = (user.username or "").strip().lower()
    return uname in ADMIN_USERNAMES

def _is_authorized_in_datacenter(update: Update) -> bool:
    in_dc = (update.effective_chat and update.effective_chat.id == DATACENTER_CHAT_ID)
    if not in_dc:
        return False
    user = update.effective_user
    return _owner_match(user) or _admin_match(user)

# ====== Datacenter pinned message (human text; JSON optional) ======
def _build_human_roster_text():
    if not ROSTER:
        return "📋 لیست تاییدشده‌ها (DataCenter)\n— هنوز کسی تایید نشده."
    lines = ["📋 لیست تاییدشده‌ها (DataCenter)"]
    for ev in EVENTS:
        ev_id = ev["id"]
        people = ROSTER.get(ev_id, [])
        lines.append(f"\n🗓 {ev['title']} — {ev['when']} | تاییدشده‌ها: {len(people)} (آقایان: {male_count(ev_id)})")
        if not people:
            lines.append("  — هنوز تاییدی نداریم")
        else:
            for i, r in enumerate(people, start=1):
                uname = f"@{r['username']}" if r.get("username") else "—"
                phone = r.get("phone","—")
                lines.append(f"  {i}. {r['name']} | {uname} | {phone}")
    return "\n".join(lines)

async def save_state_to_pinned(application):
    global ROSTER_MESSAGE_ID
    if not DATACENTER_CHAT_ID:
        return

    human = _build_human_roster_text()
    if SHOW_JSON_IN_PINNED:
        human += "\n\n---\n```json\n" + json.dumps(
            {"events":[{"id":e["id"],"capacity":e.get("capacity"),"title":e["title"],"when":e["when"]} for e in EVENTS],
             "roster":ROSTER}, ensure_ascii=False) + "\n```"

    try:
        if ROSTER_MESSAGE_ID:
            await application.bot.edit_message_text(
                chat_id=DATACENTER_CHAT_ID,
                message_id=ROSTER_MESSAGE_ID,
                text=human,
            )
            return
    except Exception as e:
        print("edit pinned roster failed, will recreate:", e)

    try:
        msg = await application.bot.send_message(chat_id=DATACENTER_CHAT_ID, text=human)
        ROSTER_MESSAGE_ID = msg.message_id
        try:
            await application.bot.pin_chat_message(
                chat_id=DATACENTER_CHAT_ID,
                message_id=ROSTER_MESSAGE_ID,
                disable_notification=True
            )
        except Exception as e:
            print("pin roster message failed:", e)
    except Exception as e:
        print("send roster message failed:", e)

async def _update_roster_message(context: ContextTypes.DEFAULT_TYPE):
    await save_state_to_pinned(context.application)

# =========================
#          UI
# =========================
def build_main_menu():
    buttons = [
        [InlineKeyboardButton("🎉 رویدادهای پیش‌رو", callback_data="list_events")],
        [InlineKeyboardButton("📝 ثبت‌نام سریع", callback_data="register")],
        [InlineKeyboardButton("🏠 معرفی کافه به chillchat", callback_data="cafe_intro")],
        [InlineKeyboardButton("🌐 شبکه‌های اجتماعی", callback_data="socials")],
        [InlineKeyboardButton("❔ سوالات متداول", callback_data="faq")],
        [InlineKeyboardButton("🆘 پشتیبانی", callback_data="support")],
        [InlineKeyboardButton("💬 ارسال نظر و پیشنهاد", callback_data="feedback_start")],
    ]
    return InlineKeyboardMarkup(buttons)

def back_inline():
    return InlineKeyboardMarkup([[InlineKeyboardButton("↩️ بازگشت به مرحله قبل", callback_data="back_step")]])

def rules_inline():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ قبول دارم و بعدی", callback_data="accept_rules")],
        [InlineKeyboardButton("↩️ بازگشت به مرحله قبل", callback_data="back_step")],
    ])

def level_inline():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Beginner (A1–A2)", callback_data="lvl_A")],
        [InlineKeyboardButton("Intermediate (B1–B2)", callback_data="lvl_B")],
        [InlineKeyboardButton("Advanced (C1+)", callback_data="lvl_C")],
        [InlineKeyboardButton("↩️ بازگشت به مرحله قبل", callback_data="back_step")],
    ])

def gender_inline():
    # فقط مرد/زن (گزینه "ترجیح نمی‌گم" حذف شد)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👨 مرد", callback_data="gender_m"),
         InlineKeyboardButton("👩 زن",  callback_data="gender_f")],
        [InlineKeyboardButton("↩️ بازگشت به مرحله قبل", callback_data="back_step")],
    ])

def age_inline():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➖ ترجیح می‌دهم نگویم", callback_data="age_na")],
        [InlineKeyboardButton("↩️ بازگشت به مرحله قبل", callback_data="back_step")],
    ])

def event_inline_register(ev_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 ثبت‌نام در همین رویداد", callback_data=f"register_{ev_id}")],
        [InlineKeyboardButton("↩️ بازگشت", callback_data="back_home")],
    ])

# =========================
#        RENDERERS
# =========================
async def render_home(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    clear_flow(context)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text("یکی از گزینه‌ها رو انتخاب کن:", reply_markup=build_main_menu())
    else:
        if update.message:
            await update.message.reply_text(WELCOME, parse_mode="Markdown", reply_markup=reply_main)
            await update.message.reply_text("یکی از گزینه‌ها رو انتخاب کن:", reply_markup=build_main_menu())
        elif update.callback_query:
            await update.callback_query.edit_message_text("یکی از گزینه‌ها رو انتخاب کن:", reply_markup=build_main_menu())

async def render_event_list(update: Update):
    rows = []
    for e in EVENTS:
        # بدون نمایش ظرفیت
        label = f"{e['title']} | {e['when']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"event_{e['id']}")])
    rows.append([InlineKeyboardButton("↩️ بازگشت", callback_data="back_home")])
    await update.callback_query.edit_message_text("رویدادهای پیش‌رو:", reply_markup=InlineKeyboardMarkup(rows))

async def render_event_detail(update: Update, ev):
    await update.callback_query.edit_message_text(
        event_text_user(ev), parse_mode="Markdown", reply_markup=event_inline_register(ev["id"])
    )

async def render_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    push_step(context, "rules")
    if update.callback_query:
        await update.callback_query.edit_message_text(RULES, reply_markup=rules_inline())
    else:
        await update.message.reply_text(RULES, reply_markup=rules_inline())

async def render_name(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    push_step(context, "name")
    txt = "لطفاً *نام و نام خانوادگی* رو وارد کن:"
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(txt, parse_mode="Markdown", reply_markup=back_inline())
    else:
        await update.effective_chat.send_message(txt, parse_mode="Markdown", reply_markup=back_inline())

async def render_gender(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    push_step(context, "gender")
    txt = "جنسیتت رو انتخاب کن:"
    if update.callback_query and edit:
        await update.callback_query.edit_message_text(txt, reply_markup=gender_inline())
    else:
        await update.effective_chat.send_message(txt, reply_markup=gender_inline())

async def render_age(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    push_step(context, "age")
    txt = "سن‌ت رو به *عدد* بفرست (مثلاً 24). یا دکمهٔ «ترجیح می‌دهم نگویم» رو بزن."
    if update.callback_query and edit:
        await update.callback_query.edit_message_text(txt, parse_mode="Markdown", reply_markup=age_inline())
    else:
        await update.effective_chat.send_message(txt, parse_mode="Markdown", reply_markup=age_inline())

async def render_level(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    push_step(context, "level")
    if update.callback_query and edit:
        await update.callback_query.edit_message_text("سطح زبانت چیه؟ یکی رو انتخاب کن:", reply_markup=level_inline())
    else:
        await update.effective_chat.send_message("سطح زبانت چیه؟ یکی رو انتخاب کن:", reply_markup=level_inline())

async def render_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    push_step(context, "phone")
    contact_btn = ReplyKeyboardMarkup(
        [[KeyboardButton("ارسال شماره تماس 📱", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )
    await update.effective_chat.send_message("شماره تلفنت رو وارد کن یا دکمه زیر رو بزن:", reply_markup=contact_btn)
    await update.effective_chat.send_message("یا می‌تونی به مرحله قبل برگردی:", reply_markup=back_inline())

async def render_note(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    push_step(context, "note")
    txt = "یادداشت/نیاز خاص داری؟ (اختیاری) اینجا بنویس و بفرست. اگر چیزی نداری، فقط یک خط تیره `-` بفرست."
    if update.callback_query and edit:
        await update.callback_query.edit_message_text(txt, parse_mode="Markdown", reply_markup=back_inline())
    else:
        await update.effective_chat.send_message(txt, parse_mode="Markdown", reply_markup=back_inline())

async def go_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pop_step(context)
    prev = current_step(context)
    origin = context.user_data.get("origin")
    sel_ev = get_event(context.user_data.get("selected_event_id"))

    if not prev:
        if origin == "event" and sel_ev and update.callback_query:
            return await render_event_detail(update, sel_ev)
        return await render_home(update, context, edit=True)

    if prev == "rules":
        await render_rules(update, context)
    elif prev == "name":
        await render_name(update, context, edit=True)
    elif prev == "gender":
        await render_gender(update, context, edit=True)
    elif prev == "age":
        await render_age(update, context, edit=True)
    elif prev == "level":
        await render_level(update, context, edit=True)
    elif prev == "phone":
        await render_phone(update, context)
    elif prev == "note":
        await render_note(update, context, edit=True)
    else:
        await render_home(update, context, edit=True)

# =========================
#         HANDLERS
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await render_home(update, context)

async def cmd_testpin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized_in_datacenter(update):
        await update.message.reply_text("⛔️ اجازه نداری.")
        return
    try:
        await save_state_to_pinned(context.application)
        await update.message.reply_text("✅ لیست شرکت‌کنندگان در گروه دیتاسنتر ساخته/آپدیت و پین شد.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ خطا در پین/آپدیت لیست: {e}")

async def cmd_roster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized_in_datacenter(update):
        await update.message.reply_text("⛔️ اجازه نداری.")
        return
    try:
        human = _build_human_roster_text()
        await update.message.reply_text("📋 وضعیت فعلی (in-memory):\n\n" + human[:3800])
    except Exception as e:
        await update.message.reply_text(f"⚠️ خطا در نمایش لیست: {e}")

# ===== Broadcast & DM (فقط Owner/Admin داخل دیتاسنتر) =====
async def cmd_broadcast_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized_in_datacenter(update):
        await update.message.reply_text("⛔️ اجازه نداری.")
        return
    text_to_send = (update.message.text or "").split(maxsplit=1)
    payload = text_to_send[1].strip() if len(text_to_send) > 1 else ""
    if not payload and update.message.reply_to_message:
        payload = update.message.reply_to_message.text or ""
    if not payload:
        await update.message.reply_text("متن پیام خالیه. یا بعد از دستور بنویس یا روی یک پیام ریپلای کن.")
        return

    sent = 0; failed = 0
    all_users = set()
    for lst in ROSTER.values():
        for r in lst:
            uid = r.get("user_chat_id")
            if uid:
                all_users.add(uid)
    for uid in all_users:
        try:
            await context.bot.send_message(chat_id=uid, text=payload)
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"📣 ارسال شد. موفق: {sent} | ناموفق: {failed}")

async def cmd_broadcast_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized_in_datacenter(update):
        await update.message.reply_text("⛔️ اجازه نداری.")
        return
    args = (update.message.text or "").split(maxsplit=2)
    if len(args) < 3:
        await update.message.reply_text("الگو: /broadcast_event <event_id> <message>\nمثال: /broadcast_event intro01 سلام بچه‌ها...")
        return
    ev_id = args[1].strip()
    payload = args[2].strip()
    if not ROSTER.get(ev_id):
        await update.message.reply_text("برای این ایونت کسی در لیست نیست.")
        return
    sent = 0; failed = 0
    for r in ROSTER[ev_id]:
        uid = r.get("user_chat_id")
        if not uid:
            continue
        try:
            await context.bot.send_message(chat_id=uid, text=payload)
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"📣 ارسال به ایونت {ev_id} تمام شد. موفق: {sent} | ناموفق: {failed}")

async def cmd_dm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized_in_datacenter(update):
        await update.message.reply_text("⛔️ اجازه نداری.")
        return
    args = (update.message.text or "").split(maxsplit=2)
    if len(args) < 3:
        await update.message.reply_text("الگو: /dm <chat_id> <message>\nمثال: /dm 123456789 سلام!")
        return
    try:
        uid = int(args[1])
    except ValueError:
        await update.message.reply_text("chat_id نامعتبر است.")
        return
    payload = args[2].strip()
    try:
        await context.bot.send_message(chat_id=uid, text=payload)
        await update.message.reply_text("✅ ارسال شد.")
    except Exception as e:
        await update.message.reply_text(f"ارسال ناموفق: {e}")

async def shortcut_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await render_home(update, context)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data

    if data.startswith("lvl_"):
        return await handle_level(update, context)

    if data.startswith("gender_"):
        return await handle_gender(update, context)

    if data == "age_na":
        context.user_data["age"] = None
        await q.answer()
        return await render_level(update, context, edit=True)

    await q.answer()

    if data == "back_home":
        return await render_home(update, context, edit=True)
    if data == "back_step":
        return await go_back(update, context)

    if data == "faq":
        return await q.edit_message_text(FAQ, parse_mode="Markdown",
                                         reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ بازگشت", callback_data="back_home")]]))

    if data == "support":
        txt = f"🆘 برای پشتیبانی به آیدی زیر پیام بده:\n@{SUPPORT_USERNAME}"
        return await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ بازگشت", callback_data="back_home")]]))

    if data == "cafe_intro":
        return await q.edit_message_text(CAFE_INTRO_TEXT, parse_mode="Markdown",
                                         reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ بازگشت", callback_data="back_home")]]))

    if data == "socials":
        return await q.edit_message_text(SOCIAL_TEXT(), parse_mode="Markdown",
                                         reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ بازگشت", callback_data="back_home")]]))

    if data == "feedback_start":
        context.user_data["feedback_mode"] = True
        return await q.edit_message_text(
            "📝 نظرت درباره رویدادها، پیشنهاد برای بهبود، یا هر حرف دیگه‌ای رو بنویس و بفرست.\n"
            "پیامت مستقیم برای تیم ChillChat ارسال میشه 💌",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ بازگشت", callback_data="back_home")]])
        )

    if data == "list_events":
        return await render_event_list(update)

    if data.startswith("event_"):
        ev = get_event(data.split("_",1)[1])
        if not ev:
            return await q.answer("این رویداد یافت نشد.", show_alert=True)
        return await render_event_detail(update, ev)

    if data == "register" or data.startswith("register_"):
        target_ev = None
        if data.startswith("register_"):
            ev_id = data.split("_",1)[1]
            target_ev = get_event(ev_id)
            context.user_data["selected_event_id"] = ev_id
            context.user_data["origin"] = "event"
        else:
            context.user_data["origin"] = "menu"
            if not context.user_data.get("selected_event_id"):
                return await render_event_list(update)

        if not target_ev and context.user_data.get("selected_event_id"):
            target_ev = get_event(context.user_data["selected_event_id"])

        # ظرفیت کلی رویداد (این همچنان قبل از شروع فرایند چک می‌شود)
        if target_ev and target_ev.get("capacity") and remaining_capacity(target_ev) <= 0:
            return await q.edit_message_text(
                CAPACITY_FULL_PREVENT_MSG,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ بازگشت", callback_data="back_home")]])
            )
        return await render_rules(update, context)

    if data == "accept_rules":
        return await render_name(update, context, edit=True)

    # Admin Approve / Reject
    if data.startswith("approve_") or data.startswith("reject_"):
        try:
            action, user_chat_id, ev_id = data.split("_", 2)
            user_chat_id = int(user_chat_id)
            ev = get_event(ev_id)
            approver = q.from_user
            approved_by = approver.full_name

            # ظرفیت کلی رویداد
            if action == "approve" and ev and ev.get("capacity") and remaining_capacity(ev) <= 0:
                await q.answer("ظرفیت تکمیل است؛ امکان تایید نیست.", show_alert=True)
                base_text = q.message.text or ""
                stamp = "⚠️ تلاش برای تایید، اما ظرفیت تکمیل است."
                try:
                    await q.edit_message_text(base_text + "\n\n" + stamp)
                except:
                    pass
                return

            # سقف آقایان (تنها هنگام تایید)
            info_preview = PENDING.get(user_chat_id, {})
            if action == "approve" and info_preview.get("gender") == "male":
                if male_count(ev_id) >= MALE_LIMIT_PER_EVENT:
                    await q.answer("سقف آقایان تکمیل است؛ امکان تایید نیست.", show_alert=True)
                    base_text = q.message.text or ""
                    stamp = "⚠️ تلاش برای تایید آقا، اما سقف آقایان تکمیل است."
                    try:
                        await q.edit_message_text(base_text + "\n\n" + stamp)
                    except:
                        pass
                    try:
                        await context.bot.send_message(chat_id=user_chat_id, text=MALE_CAPACITY_FULL_MSG)
                    except:
                        pass
                    return

            # پیام به کاربر
            if action == "approve":
                detail = (
                    "🎉 ثبت‌نامت تایید شد!\n\n"
                    f"📌 {ev.get('title','')}\n"
                    f"🕒 {ev.get('when','')}\n"
                    f"💶 {ev.get('price','Free')}\n"
                    f"📝 {ev.get('desc','—')}\n"
                ) if ev else "🎉 ثبت‌نامت تایید شد!"
                link = MEETUP_LINKS.get(ev_id)
                if link:
                    detail += f"\n🔗 لینک گروه/هماهنگی:\n{link}"
                await context.bot.send_message(chat_id=user_chat_id, text=detail)
                if CHANNEL_URL:
                    await context.bot.send_message(chat_id=user_chat_id, text=JOIN_CHANNEL_MSG + CHANNEL_URL)
            else:
                await context.bot.send_message(chat_id=user_chat_id, text=CAPACITY_CANCEL_MSG)

            # حذف دکمه‌ها + مهر تایید/رد
            base_text = q.message.text or ""
            stamp = "✅ توسط {0} تایید شد.".format(approved_by) if action == "approve" else "❌ توسط {0} رد شد.".format(approved_by)
            try:
                await q.edit_message_text(base_text + "\n\n" + stamp)
            except Exception:
                try:
                    await q.edit_message_reply_markup(reply_markup=None)
                except:
                    pass

            # لغو تسک auto-approve اگر وجود دارد
            info = PENDING.get(user_chat_id)
            if info and info.get("task"):
                try:
                    info["task"].cancel()
                except Exception:
                    pass

            # انتقال به ROSTER
            if action == "approve":
                info = PENDING.pop(user_chat_id, None)
                if info:
                    if ev and ev.get("capacity") and remaining_capacity(ev) <= 0:
                        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text="⚠️ ظرفیت پر شد؛ تایید نهایی انجام نشد.")
                    else:
                        # چک نهایی سقف آقایان
                        if info.get("gender") == "male" and male_count(ev_id) >= MALE_LIMIT_PER_EVENT:
                            try:
                                await context.bot.send_message(chat_id=user_chat_id, text=MALE_CAPACITY_FULL_MSG)
                            except:
                                pass
                        else:
                            ROSTER.setdefault(ev_id, []).append({
                                "name": info.get("name","—"),
                                "username": info.get("username"),
                                "phone": info.get("phone","—"),
                                "gender": info.get("gender"),
                                "age": info.get("age"),
                                "when": info.get("when","—"),
                                "event_title": info.get("event_title","—"),
                                "user_chat_id": user_chat_id,
                            })
                            await _update_roster_message(context)

            await q.answer("انجام شد.")
        except Exception as e:
            print("Admin callback error:", e)
            await q.answer("مشکلی پیش اومد.", show_alert=True)
        return

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    step = current_step(context)

    # ری‌استارت منعطف
    if re.fullmatch(r"شروع\s*مجدد(?:\s*🔄)?", text):
        return await render_home(update, context)

    # Feedback mode
    if context.user_data.get("feedback_mode"):
        try:
            if GROUP_CHAT_ID:
                user = update.effective_user
                header = f"💬 پیام جدید از کاربر ChillChat:\n👤 نام: {user.full_name}\n" + (f"🆔 @{user.username}\n" if user.username else "🆔 —\n")
                await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=header)
                try:
                    await context.bot.forward_message(
                        chat_id=GROUP_CHAT_ID,
                        from_chat_id=update.effective_chat.id,
                        message_id=update.message.message_id
                    )
                except Exception:
                    await context.bot.copy_message(
                        chat_id=GROUP_CHAT_ID,
                        from_chat_id=update.effective_chat.id,
                        message_id=update.message.message_id
                    )
            await update.message.reply_text("ممنون از بازخوردت 💛 پیام تو برای تیم ChillChat ارسال شد.", reply_markup=reply_main)
        finally:
            context.user_data["feedback_mode"] = False
        return

    # Registration flow
    if step == "name":
        if 2 <= len(text) <= 60:
            context.user_data["name"] = text
            return await render_gender(update, context, edit=False)
        else:
            return await update.message.reply_text("لطفاً نام معتبر وارد کن (۲ تا ۶۰ کاراکتر).")

    if step == "age":
        if text == "-" or text == "—":
            context.user_data["age"] = None
        else:
            if not re.fullmatch(r"\d{1,3}", text):
                return await update.message.reply_text("لطفاً سن را به عدد وارد کن (مثلاً 23) یا از دکمه «ترجیح می‌دهم نگویم» استفاده کن.")
            age_int = int(text)
            if not (1 <= age_int <= 120):
                return await update.message.reply_text("سن نامعتبر است. یک عدد بین 1 تا 120 بفرست یا روی «ترجیح می‌دهم نگویم» بزن.")
            context.user_data["age"] = age_int
        return await render_level(update, context, edit=False)

    if step == "level":
        return await render_level(update, context, edit=False)

    if step == "phone":
        context.user_data["phone"] = text
        await update.message.reply_text("شماره دریافت شد ✅", reply_markup=reply_main)
        return await render_note(update, context, edit=False)

    if step == "note":
        context.user_data["note"] = text
        return await finalize_and_send(update, context)

    return await render_home(update, context)

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if current_step(context) == "phone":
        context.user_data["phone"] = update.message.contact.phone_number
        await update.message.reply_text("شماره دریافت شد ✅", reply_markup=reply_main)
        await render_note(update, context, edit=False)

async def handle_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    await q.answer()
    lvl_map = {"lvl_A": "Beginner (A1–A2)", "lvl_B": "Intermediate (B1–B2)", "lvl_C": "Advanced (C1+)"}
    context.user_data["level"] = lvl_map.get(data, "Unknown")
    await render_phone(update, context)

async def handle_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data  # gender_m / gender_f
    await q.answer()
    gmap = {"gender_m": "male", "gender_f": "female"}
    gender = gmap.get(data, "female")
    context.user_data["gender"] = gender
    # دیگه اینجا قطع نمی‌کنیم؛ کاربر کل فرایند رو می‌ره و در انتها پیام ظرفیت می‌گیره
    await render_age(update, context, edit=True)

# =========================
#  AUTO-APPROVE via asyncio (12h)
# =========================
async def delayed_auto_approve(app, user_chat_id: int, ev_id: str, delay: int = AUTO_APPROVE_DELAY):
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    info = PENDING.get(user_chat_id)
    if not info:
        return
    ev = get_event(ev_id)
    if not ev:
        PENDING.pop(user_chat_id, None)
        return

    # ظرفیت کلی
    if ev.get("capacity") and remaining_capacity(ev) <= 0:
        try:
            await app.bot.send_message(chat_id=user_chat_id, text=CAPACITY_CANCEL_MSG)
        except Exception:
            pass
        try:
            if info.get("admin_msg_id"):
                await app.bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=info["admin_msg_id"])
        except Exception:
            pass
        PENDING.pop(user_chat_id, None)
        return

    # سقف آقایان
    if info.get("gender") == "male" and male_count(ev_id) >= MALE_LIMIT_PER_EVENT:
        try:
            await app.bot.send_message(chat_id=user_chat_id, text=MALE_CAPACITY_FULL_MSG)
        except Exception:
            pass
        try:
            if info.get("admin_msg_id"):
                await app.bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=info["admin_msg_id"])
        except Exception:
            pass
        PENDING.pop(user_chat_id, None)
        return

    # add to roster
    ROSTER.setdefault(ev_id, []).append({
        "name": info.get("name","—"),
        "username": info.get("username"),
        "phone": info.get("phone","—"),
        "gender": info.get("gender"),
        "age": info.get("age"),
        "when": info.get("when","—"),
        "event_title": info.get("event_title","—"),
        "user_chat_id": user_chat_id,
    })
    await save_state_to_pinned(app)

    # notify user
    detail = (
        "🎉 ثبت‌نامت تایید شد!\n\n"
        f"📌 {ev.get('title','')}\n"
        f"🕒 {ev.get('when','')}\n"
        f"💶 {ev.get('price','Free')}\n"
        f"📝 {ev.get('desc','—')}\n"
        "(Auto-approved by bot)"
    )
    link = MEETUP_LINKS.get(ev_id)
    if link:
        detail += f"\n🔗 لینک گروه/هماهنگی:\n{link}"
    try:
        await app.bot.send_message(chat_id=user_chat_id, text=detail)
        if CHANNEL_URL:
            await app.bot.send_message(chat_id=user_chat_id, text=JOIN_CHANNEL_MSG + CHANNEL_URL)
    except Exception:
        pass

    # delete admin message
    try:
        if info.get("admin_msg_id"):
            await app.bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=info["admin_msg_id"])
    except Exception:
        pass

    PENDING.pop(user_chat_id, None)

# =========================
#   FINALIZE & SEND (to admin)
# =========================
async def finalize_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data
    ev_id = u.get("selected_event_id") or (EVENTS[0]["id"] if EVENTS else None)
    ev = get_event(ev_id)

    # ظرفیت کلی رویداد
    if ev and ev.get("capacity") and remaining_capacity(ev) <= 0:
        await update.effective_chat.send_message(CAPACITY_CANCEL_MSG, reply_markup=reply_main)
        clear_flow(context)
        return

    # — اینجا دیگر محدودیت آقایان را قبل از ارسال به ادمین چک نمی‌کنیم —
    # پیام خلاصه برای کاربر:
    summary = (
        "✅ درخواست ثبت‌نامت ثبت شد و برای ادمین ارسال می‌شود.\n\n"
        f"👤 نام: {u.get('name','—')}\n"
        f"⚧ جنسیت: {({'male':'مرد','female':'زن'}).get(u.get('gender'),'—')}\n"
        f"🎂 سن: {u.get('age','—') if u.get('age') is not None else '—'}\n"
        f"🗣️ سطح: {u.get('level','—')}\n"
        f"📱 تماس: {u.get('phone','—')}\n"
        f"📝 توضیحات: {u.get('note','—')}\n"
    )
    if ev:
        summary += f"\n📌 رویداد: {ev.get('title','')}\n🕒 زمان: {ev.get('when','')}\n(آدرس پس از تایید ارسال می‌شود.)"
    await update.effective_chat.send_message(summary, reply_markup=reply_main)
    if CHANNEL_URL:
        await update.effective_chat.send_message(JOIN_CHANNEL_MSG + CHANNEL_URL)

    # Send to admin group
    admin_msg = None
    if GROUP_CHAT_ID:
        user_chat_id = update.effective_chat.id
        approve_cb = f"approve_{user_chat_id}_{ev_id or 'NA'}"
        reject_cb = f"reject_{user_chat_id}_{ev_id or 'NA'}"
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تایید", callback_data=approve_cb),
             InlineKeyboardButton("❌ رد", callback_data=reject_cb)]
        ])
        admin_txt = (
            "🔔 ثبت‌نام جدید ChillChat\n\n"
            f"👤 نام: {u.get('name','—')}\n"
            f"⚧ جنسیت: {({'male':'مرد','female':'زن'}).get(u.get('gender'),'—')}\n"
            f"🎂 سن: {u.get('age','—') if u.get('age') is not None else '—'}\n"
            f"🗣️ سطح: {u.get('level','—')}\n"
            f"📱 تماس: {u.get('phone','—')}\n"
            f"📝 توضیحات: {u.get('note','—')}\n\n"
        )
        if ev:
            admin_txt += event_text_admin(ev)
        admin_msg = await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=admin_txt, reply_markup=buttons)

        # Save to pending + schedule auto-approve (12h) via asyncio
        task = context.application.create_task(
            delayed_auto_approve(context.application, user_chat_id, ev_id, delay=AUTO_APPROVE_DELAY)
        )
        PENDING[user_chat_id] = {
            "name": u.get('name','—'),
            "phone": u.get('phone','—'),
            "level": u.get('level','—'),
            "note":  u.get('note','—'),
            "gender": u.get('gender'),
            "age":    u.get('age'),
            "event_id": ev_id,
            "event_title": ev.get('title') if ev else "—",
            "when": ev.get('when') if ev else "—",
            "username": update.effective_user.username if update.effective_user else None,
            "admin_msg_id": admin_msg.message_id if admin_msg else None,
            "task": task,
        }

    await maybe_write_to_sheet(u, ev)
    clear_flow(context)

# =========================
#     Google Sheets (opt)
# =========================
async def maybe_write_to_sheet(user_info, ev):
    if not GSPREAD_CREDS_JSON:
        return
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(GSPREAD_CREDS_JSON), scope)
        client = gspread.authorize(creds)
        try:
            sh = client.open(SHEET_NAME)
        except Exception:
            sh = client.create(SHEET_NAME)
        ws = sh.sheet1
        try:
            if ws.get('A1:I1') == []:
                ws.update('A1:I1', [["Timestamp","Event","When","Name","Phone","Level","Gender","Age","Note"]])
        except Exception:
            pass
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        ws.append_row([
            now,
            (ev.get('title') if ev else '—'),
            (ev.get('when') if ev else '—'),
            user_info.get('name','—'),
            user_info.get('phone','—'),
            user_info.get('level','—'),
            ({'male':'مرد','female':'زن'}).get(user_info.get('gender'),'—'),
            (user_info.get('age') if user_info.get('age') is not None else '—'),
            user_info.get('note','—'),
        ])
    except Exception as e:
        print("Sheets error:", e)

# =========================
#     PTB + FastAPI APP
# =========================
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

# مهم: JobQueue را خاموش می‌کنیم تا خطای weakref در Py 3.13 پیش نیاید
application = ApplicationBuilder().token(BOT_TOKEN).job_queue(None).build()

# هندلرها
application.add_handler(CommandHandler("start", cmd_start))
# ری‌استارت منعطف (با/بی ایموجی)
application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^شروع\s*مجدد(?:\s*🔄)?$"), shortcut_restart))

# دستورات مدیریت ارسال پیام (فقط در دیتاسنتر و با احراز هویت)
application.add_handler(CommandHandler("broadcast_all", cmd_broadcast_all))
application.add_handler(CommandHandler("broadcast_event", cmd_broadcast_event))
application.add_handler(CommandHandler("dm", cmd_dm))

application.add_handler(CallbackQueryHandler(handle_level, pattern=r"^lvl_"))
application.add_handler(CallbackQueryHandler(handle_callback))
application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
application.add_handler(CommandHandler("testpin", cmd_testpin))
application.add_handler(CommandHandler("roster",  cmd_roster))

@asynccontextmanager
async def lifespan(app: FastAPI):
    await application.initialize()
    if WEBHOOK_URL:
        await application.bot.set_webhook(url=WEBHOOK_URL)
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
    return {"status": "ChillChat bot is running (12h auto-approve, hidden capacity, male cap=5, admin broadcast)." }


