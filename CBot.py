# CBot.py — ChillChat Bot (compact, pinned JSON, admin DM/Broadcasts, no Sheets)
# python-telegram-bot==20.3, fastapi, uvicorn

import os, json, re, asyncio
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton as B, InlineKeyboardMarkup as MK, ReplyKeyboardMarkup, KeyboardButton, Chat
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

# =========================
#          CONFIG
# =========================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
GROUP_CHAT_ID = int(os.environ.get("GROUP_CHAT_ID", "0"))
DATACENTER_CHAT_ID = int(os.environ.get("DATACENTER_CHAT_ID", str(GROUP_CHAT_ID or 0)))

SUPPORT_USERNAME = os.environ.get("SUPPORT_USERNAME", "ifyoulostme")
CHANNEL_URL = os.environ.get("CHANNEL_URL", "")
GROUP_URL   = os.environ.get("GROUP_URL", "")
INSTAGRAM_URL = os.environ.get("INSTAGRAM_URL", "")
CAFE_INTRO_USERNAME = (os.environ.get("CAFE_INTRO_USERNAME") or "ifyoulostme").lstrip("@")

# اعلان/پخش پیام (فقط Owner/Admins)
OWNER_USERNAME = (os.environ.get("OWNER_USERNAME") or "").strip().lstrip("@")
ADMIN_USERNAMES = [u.strip().lstrip("@") for u in (os.environ.get("ADMIN_USERNAMES") or "").split(",") if u.strip()]

# تایید خودکار پس از 12 ساعت
AUTO_APPROVE_DELAY = int(os.environ.get("AUTO_APPROVE_DELAY", str(12 * 60 * 60)))

# نمایش JSON در پیام پین‌شده؟
SHOW_JSON_IN_PINNED = os.environ.get("SHOW_JSON_IN_PINNED", "1") == "1"

# سقف آقایان در هر رویداد
MALE_LIMIT_PER_EVENT = int(os.environ.get("MALE_LIMIT_PER_EVENT", "5"))

# --- DEFAULT EVENTS (override via EVENTS_JSON) ---
DEFAULT_EVENTS = [
    {
        "id": "intro01",
        "title": "2nd Meeting!",
        "when": "چهارشنبه 30 مهر - ۱۸:۰۰",
        "place": "—",
        "price": "سفارش از کافه",
        "capacity": 12,
        "desc": "The legendary 2nd session. Chill & chat, topic decided in group!",
    }
]
try:
    EVENTS = json.loads(os.environ.get("EVENTS_JSON", "") or "[]") or DEFAULT_EVENTS
    if not isinstance(EVENTS, list):
        EVENTS = DEFAULT_EVENTS
except:
    EVENTS = DEFAULT_EVENTS

try:
    MEETUP_LINKS = json.loads(os.environ.get("MEETUP_LINKS_JSON", "{}"))
except:
    MEETUP_LINKS = {}

# =========================
#     IN-MEMORY STORAGE
# =========================
# درخواست‌های در انتظار تایید
PENDING = {}   # key: user_chat_id -> dict
# افراد تاییدشده به تفکیک رویداد
ROSTER = {}    # key: event_id -> list[dict{chat_id,name,username,phone,gender,age,when,event_title}]
# همه‌ی کاربرانی که بات را استارت/استفاده کرده‌اند (برای /dmall)
ALL_USERS = {} # key: chat_id -> {"username":..,"name":..}
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
    "🗣️ در جلسات به انگلیسی صحبت می‌کنیم، بازی می‌کنیم، موضوع‌های روز رو تمرین می‌کنیم و آشناهای جدید پیدا می‌کنیم!\n\n"
    "☕ محل برگزاری: کافه (برای همه سطوح)\n"
    "💶 هزینه: معمولاً رایگان؛ فقط سفارش از کافه\n"
    "📸 عکس‌برداری با رضایت شرکت‌کننده‌ها\n"
    "📝 ثبت‌نام: درخواستت برای ادمین ارسال میشه و پس از تایید، اسمت در لیست قرار می‌گیره."
)

RULES = (
    "⚠️ **قوانین ChillChat**\n\n"
    "💬 احترام\n"
    "🗣️ تلاش برای صحبت انگلیسی\n"
    "⏰ وقت‌شناسی\n"
    "📱 بی‌صداسازی گوشی\n"
    "🙏 اگر نمیای، زود اطلاع بده\n"
)

CAFE_INTRO_TEXT = (
    "🏠 **معرفی کافه به ChillChat**\n"
    f"اسم و آدرس کافهٔ پیشنهادی‌ت رو برای *@{CAFE_INTRO_USERNAME}* بفرست 🙌"
)

CAPACITY_CANCEL_MSG = (
    "❌ ثبت‌نام شما به دلیل *تکمیل ظرفیت* لغو شد.\n"
    "از «🎉 رویدادهای پیش‌رو» رویداد دیگری را انتخاب کن."
)
CAPACITY_FULL_PREVENT_MSG = "❌ ظرفیت این رویداد تکمیل است. لطفاً رویداد دیگری را انتخاب کن."
MALE_CAPACITY_FULL_MSG = "❌ سقف ظرفیت شرکت‌کنندگان برای این رویداد تکمیل شده است."

def SOCIAL_TEXT():
    return (
        "🌐 **شبکه‌های اجتماعی:**\n\n"
        + (f"📢 [کانال]({CHANNEL_URL})\n" if CHANNEL_URL else "")
        + (f"💬 [گروه]({GROUP_URL})\n" if GROUP_URL else "")
        + (f"📸 [اینستاگرام]({INSTAGRAM_URL})\n" if INSTAGRAM_URL else "")
        + ("\n(به‌زودی تکمیل می‌شود.)" if not (CHANNEL_URL or GROUP_URL or INSTAGRAM_URL) else "")
    )

# =========================
#          HELPERS
# =========================
def add_user(user, chat_id: int):
    if not chat_id:
        return
    ALL_USERS.setdefault(chat_id, {"username": None, "name": None})
    ALL_USERS[chat_id]["username"] = user.username if user else None
    ALL_USERS[chat_id]["name"] = user.full_name if user else None

def is_admin_user(user) -> bool:
    u = (user.username or "").lower()
    allow = set([OWNER_USERNAME.lower()] if OWNER_USERNAME else []) | {a.lower() for a in ADMIN_USERNAMES}
    return bool(u and u in allow)

def get_event(eid): 
    return next((e for e in EVENTS if e.get("id") == eid), None)

def approved_count(eid): 
    return len(ROSTER.get(eid, []))

def male_count(eid): 
    return sum(1 for r in ROSTER.get(eid, []) if r.get("gender") == "male")

def remaining_capacity(ev): 
    c = int(ev.get("capacity") or 0)
    return max(0, c - approved_count(ev["id"])) if c else 10**9

def event_text_user(ev):
    parts = [
        f"**{ev.get('title','')}**",
        f"🕒 {ev.get('when','')}",
        f"📍 {ev.get('place','—')}",
        f"💶 {ev.get('price','') or 'Free'}",
    ]
    if ev.get("desc"):
        parts.append(f"📝 {ev['desc']}")
    parts.append("\n(آدرس دقیق کافه پیش از رویداد در کانال اعلام می‌شود.)")
    return "\n".join(parts)

def event_text_admin(ev):
    cap_line = ""
    if ev.get("capacity"):
        cap_line = f"👥 ظرفیت: {approved_count(ev['id'])}/{ev['capacity']}\n"
    return (
        f"📌 {ev.get('title','')}\n"
        f"🕒 {ev.get('when','')}\n"
        f"{cap_line}"
        f"📍 {ev.get('place','—')}\n"
        f"💶 {ev.get('price','Free')}\n"
        f"📝 {ev.get('desc','—')}"
    )

def _extract_json(text):
    if not text:
        return None
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except:
        return None

async def restore_from_pinned(app):
    global ROSTER_MESSAGE_ID, ROSTER, ALL_USERS
    if not DATACENTER_CHAT_ID:
        return
    try:
        chat: Chat = await app.bot.get_chat(DATACENTER_CHAT_ID)
    except Exception as e:
        print("restore get_chat:", e)
        return
    pm = getattr(chat, "pinned_message", None)
    if not pm:
        return
    data = _extract_json(getattr(pm, "text", None) or getattr(pm, "caption", None))
    if data:
        if isinstance(data.get("roster"), dict):
            ROSTER = data["roster"]
        au = data.get("all_users") or {}
        ALL_USERS = {}
        for k, v in au.items():
            try:
                cid = int(k)
            except:
                continue
            ALL_USERS[cid] = {"username": v.get("username"), "name": v.get("name")}
    ROSTER_MESSAGE_ID = pm.message_id

def _human_roster():
    if not ROSTER:
        return "📋 لیست تاییدشده‌ها (DataCenter)\n— هنوز کسی تایید نشده."
    L = ["📋 لیست تاییدشده‌ها (DataCenter)"]
    for e in EVENTS:
        eid = e["id"]
        ppl = ROSTER.get(eid, [])
        L.append(f"\n🗓 {e['title']} — {e['when']} | تاییدشده‌ها: {len(ppl)} (آقایان: {male_count(eid)})")
        if not ppl:
            L.append("  — هنوز تاییدی نداریم")
        else:
            for i, r in enumerate(ppl, 1):
                uname = f"@{r['username']}" if r.get("username") else "—"
                L.append(f"  {i}. {r['name']} | {uname} | {r.get('phone','—')}")
    return "\n".join(L)

async def save_pinned(app):
    global ROSTER_MESSAGE_ID
    if not DATACENTER_CHAT_ID:
        return
    human = _human_roster()
    if SHOW_JSON_IN_PINNED:
        human += "\n\n---\n```json\n" + json.dumps(
            {
                "events": [{"id": e["id"], "capacity": e.get("capacity"), "title": e["title"], "when": e["when"]} for e in EVENTS],
                "roster": ROSTER,
                "all_users": {str(cid): ALL_USERS[cid] for cid in ALL_USERS},
            },
            ensure_ascii=False
        ) + "\n```"
    try:
        if ROSTER_MESSAGE_ID:
            await app.bot.edit_message_text(chat_id=DATACENTER_CHAT_ID, message_id=ROSTER_MESSAGE_ID, text=human)
            return
    except Exception as e:
        print("edit pinned failed:", e)
    m = await app.bot.send_message(chat_id=DATACENTER_CHAT_ID, text=human)
    ROSTER_MESSAGE_ID = m.message_id
    try:
        await app.bot.pin_chat_message(chat_id=DATACENTER_CHAT_ID, message_id=ROSTER_MESSAGE_ID, disable_notification=True)
    except Exception as e:
        print("pin failed:", e)

# =========================
#          UI
# =========================
def build_main_menu():
    return MK([
        [B("🎉 رویدادهای پیش‌رو", callback_data="list_events")],
        [B("📝 ثبت‌نام سریع", callback_data="register")],
        [B("🏠 معرفی کافه به ChillChat", callback_data="cafe_intro")],
        [B("🌐 شبکه‌های اجتماعی", callback_data="socials")],
        [B("❔ سوالات متداول", callback_data="faq")],
        [B("🆘 پشتیبانی", callback_data="support")],
        [B("💬 ارسال نظر و پیشنهاد", callback_data="feedback_start")],
    ])

def back_inline():
    return MK([[B("↩️ بازگشت به مرحله قبل", callback_data="back_step")]])

def rules_inline():
    return MK([[B("✅ قبول دارم و بعدی", callback_data="accept_rules")],[B("↩️ بازگشت به مرحله قبل", callback_data="back_step")]])

def level_inline():
    return MK([
        [B("Beginner (A1–A2)", callback_data="lvl_A")],
        [B("Intermediate (B1–B2)", callback_data="lvl_B")],
        [B("Advanced (C1+)", callback_data="lvl_C")],
        [B("↩️ بازگشت به مرحله قبل", callback_data="back_step")],
    ])

def gender_inline():
    return MK([
        [B("👨 مرد", callback_data="gender_m"), B("👩 زن",  callback_data="gender_f")],
        [B("↩️ بازگشت به مرحله قبل", callback_data="back_step")],
    ])

def age_inline():
    return MK([
        [B("➖ ترجیح می‌دهم نگویم", callback_data="age_na")],
        [B("↩️ بازگشت به مرحله قبل", callback_data="back_step")],
    ])

def event_inline_register(ev_id):
    return MK([[B("📝 ثبت‌نام در همین رویداد", callback_data=f"register_{ev_id}")],[B("↩️ بازگشت", callback_data="back_home")]])

# =========================
#        RENDERERS
# =========================
def push_step(ctx, step): ctx.user_data["step"] = step
def clear_flow(ctx):
    for k in ["step","origin","selected_event_id","name","phone","level","gender","age","note","feedback_mode"]:
        ctx.user_data.pop(k, None)

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
        label = f"{e['title']} | {e['when']}"
        rows.append([B(label, callback_data=f"event_{e['id']}")])
    rows.append([B("↩️ بازگشت", callback_data="back_home")])
    await update.callback_query.edit_message_text("رویدادهای پیش‌رو:", reply_markup=MK(rows))

async def render_event_detail(update: Update, ev):
    await update.callback_query.edit_message_text(event_text_user(ev), parse_mode="Markdown", reply_markup=event_inline_register(ev["id"]))

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
    contact_btn = ReplyKeyboardMarkup([[KeyboardButton("ارسال شماره تماس 📱", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
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
    step = context.user_data.get("step")
    # بر اساس step برگردیم یک مرحله قبل (ساده)
    order = ["rules","name","gender","age","level","phone","note"]
    if step in order:
        i = max(0, order.index(step) - 1)
        context.user_data["step"] = order[i] if i < len(order) else None
    prev = context.user_data.get("step")
    if prev == "rules": return await render_rules(update, context)
    if prev == "name":  return await render_name(update, context, edit=True)
    if prev == "gender":return await render_gender(update, context, edit=True)
    if prev == "age":   return await render_age(update, context, edit=True)
    if prev == "level": return await render_level(update, context, edit=True)
    if prev == "phone": return await render_phone(update, context)
    if prev == "note":  return await render_note(update, context, edit=True)
    return await render_home(update, context, edit=True)

# =========================
#         HANDLERS
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_user(update.effective_user, update.effective_chat.id)
    await render_home(update, context)

async def cmd_testpin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await save_pinned(context.application)
        await update.message.reply_text("✅ لیست شرکت‌کنندگان در دیتاسنتر ساخته/آپدیت و پین شد.")

async def cmd_roster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        human = _human_roster()
        await update.message.reply_text("📋 وضعیت فعلی:\n\n" + human[:3800])

# ---- Admin DM / Broadcast ----
def _is_dc_admin(update: Update):
    return (update.effective_chat.id == DATACENTER_CHAT_ID) and is_admin_user(update.effective_user)

async def cmd_dm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /dm @username message
    if not _is_dc_admin(update): return
    text = (update.message.text or "").strip()
    m = re.match(r"^/dm\s+@?(\w+)\s+(.+)$", text, flags=re.DOTALL)
    if not m:
        return await update.message.reply_text("فرمت: /dm @username پیام")
    target, msg = m.group(1), m.group(2).strip()
    chat_id = None
    for ppl in ROSTER.values():
        for r in ppl:
            if (r.get("username") or "").lower() == target.lower():
                chat_id = r.get("chat_id")
                if chat_id: break
        if chat_id: break
    if not chat_id:
        # اگر در ROSTER پیدا نشد، از ALL_USERS امتحان کن
        for cid, info in ALL_USERS.items():
            if (info.get("username") or "").lower() == target.lower():
                chat_id = cid
                break
    if not chat_id:
        return await update.message.reply_text("کاربر پیدا نشد یا chat_id در دسترس نیست.")
    try:
        await context.bot.send_message(chat_id=chat_id, text=msg)
        await update.message.reply_text("✅ پیام ارسال شد.")
    except Exception as e:
        await update.message.reply_text(f"❌ ارسال ناموفق: {e}")

async def cmd_dmevent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /dmevent intro01 پیام  (یا روی یک reply فقط /dmevent intro01)
    if not _is_dc_admin(update): return
    text = (update.message.text or "").strip()
    m = re.match(r"^/dmevent\s+(\S+)(?:\s+(.+))?$", text, flags=re.DOTALL)
    if not m:
        return await update.message.reply_text("فرمت: /dmevent <event_id> [پیام]\n(اگر روی یک پیام reply بزنی، همان متن ارسال می‌شود.)")
    ev_id = m.group(1)
    msg = m.group(2) or ""
    if not msg and update.message.reply_to_message:
        msg = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
    if not msg:
        return await update.message.reply_text("متن پیام خالی‌ست.")
    if ev_id not in {e["id"] for e in EVENTS}:
        return await update.message.reply_text("event_id نامعتبر.")
    sent = fail = 0
    for r in ROSTER.get(ev_id, []):
        cid = r.get("chat_id")
        if not cid: 
            fail += 1; continue
        try:
            await context.bot.send_message(chat_id=cid, text=msg)
            sent += 1
        except:
            fail += 1
    await update.message.reply_text(f"نتیجه: ارسال شد {sent} | ناموفق {fail}")

async def cmd_dmall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /dmall پیام  (یا reply)
    if not _is_dc_admin(update): return
    text = (update.message.text or "").strip()
    msg = None
    m = re.match(r"^/dmall\s+(.+)$", text, flags=re.DOTALL)
    if m: msg = m.group(1).strip()
    if not msg and update.message.reply_to_message:
        msg = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
    if not msg:
        return await update.message.reply_text("فرمت: /dmall پیام\n(یا روی پیام reply کن و فقط /dmall بفرست)")
    sent = fail = 0
    for cid in list(ALL_USERS.keys()):
        try:
            await context.bot.send_message(chat_id=cid, text=msg)
            sent += 1
        except:
            fail += 1
    await update.message.reply_text(f"نتیجه: ارسال شد {sent} | ناموفق {fail}")

async def shortcut_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_user(update.effective_user, update.effective_chat.id)
    await render_home(update, context)

# ---------- Callback flow ----------
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    await q.answer()
    # store user
    add_user(q.from_user, q.message.chat.id if q.message else update.effective_chat.id)

    if data == "back_home":
        return await render_home(update, context, edit=True)
    if data == "back_step":
        return await go_back(update, context)

    if data == "faq":
        return await q.edit_message_text(FAQ, parse_mode="Markdown", reply_markup=MK([[B("↩️ بازگشت", callback_data="back_home")]]))
    if data == "support":
        return await q.edit_message_text(f"🆘 پشتیبانی: @{SUPPORT_USERNAME}", reply_markup=MK([[B("↩️ بازگشت", callback_data="back_home")]]))
    if data == "cafe_intro":
        return await q.edit_message_text(CAFE_INTRO_TEXT, parse_mode="Markdown", reply_markup=MK([[B("↩️ بازگشت", callback_data="back_home")]]))
    if data == "socials":
        return await q.edit_message_text(SOCIAL_TEXT(), parse_mode="Markdown", reply_markup=MK([[B("↩️ بازگشت", callback_data="back_home")]]))

    if data == "feedback_start":
        context.user_data["feedback_mode"] = True
        return await q.edit_message_text("📝 نظرت رو بنویس و بفرست. پیامت مستقیم به تیم میره 💌", reply_markup=MK([[B("↩️ بازگشت", callback_data="back_home")]]))

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

        if target_ev and target_ev.get("capacity") and remaining_capacity(target_ev) <= 0:
            return await q.edit_message_text(CAPACITY_FULL_PREVENT_MSG, reply_markup=MK([[B("↩️ بازگشت", callback_data="back_home")]]))
        return await render_rules(update, context)

    if data == "accept_rules":
        return await render_name(update, context, edit=True)

    if data.startswith("lvl_"):
        lvl_map = {"lvl_A": "Beginner (A1–A2)", "lvl_B": "Intermediate (B1–B2)", "lvl_C": "Advanced (C1+)"}
        context.user_data["level"] = lvl_map.get(data, "Unknown")
        return await render_phone(update, context)

    if data.startswith("gender_"):
        gmap = {"gender_m": "male", "gender_f": "female"}
        context.user_data["gender"] = gmap.get(data, "male")
        return await render_age(update, context, edit=True)

    if data == "age_na":
        context.user_data["age"] = None
        return await render_level(update, context, edit=True)

    # Admin Approve / Reject
    if data.startswith("approve_") or data.startswith("reject_"):
        try:
            action, user_chat_id, ev_id = data.split("_", 2)
            user_chat_id = int(user_chat_id)
            ev = get_event(ev_id)
            # ظرفیت کلی رویداد
            if action == "approve" and ev and ev.get("capacity") and remaining_capacity(ev) <= 0:
                await q.answer("ظرفیت تکمیل است؛ امکان تایید نیست.", show_alert=True)
                base = q.message.text or ""
                try: await q.edit_message_text(base + "\n\n⚠️ ظرفیت تکمیل.")
                except: pass
                return
            # سقف آقایان
            info_preview = PENDING.get(user_chat_id, {})
            if action == "approve" and info_preview.get("gender") == "male":
                if male_count(ev_id) >= MALE_LIMIT_PER_EVENT:
                    await q.answer("سقف آقایان تکمیل است؛ امکان تایید نیست.", show_alert=True)
                    base = q.message.text or ""
                    try: await q.edit_message_text(base + "\n\n⚠️ تلاش برای تایید آقا، اما سقف تکمیل.")
                    except: pass
                    try: await context.bot.send_message(chat_id=user_chat_id, text=MALE_CAPACITY_FULL_MSG)
                    except: pass
                    return

            # پیام به کاربر
            if action == "approve":
                detail = ("🎉 ثبت‌نامت تایید شد!\n\n"
                          f"📌 {ev.get('title','')}\n"
                          f"🕒 {ev.get('when','')}\n"
                          f"📍 {ev.get('place','—')}\n"
                          f"💶 {ev.get('price','Free')}\n"
                          f"📝 {ev.get('desc','—')}\n") if ev else "🎉 ثبت‌نامت تایید شد!"
                link = MEETUP_LINKS.get(ev_id)
                if link: detail += f"\n🔗 لینک هماهنگی:\n{link}"
                await context.bot.send_message(chat_id=user_chat_id, text=detail)
            else:
                await context.bot.send_message(chat_id=user_chat_id, text=CAPACITY_CANCEL_MSG)

            # حذف دکمه‌ها
            base = q.message.text or ""
            stamp = "✅ تایید شد." if action == "approve" else "❌ رد شد."
            try: await q.edit_message_text(base + "\n\n" + stamp)
            except:
                try: await q.edit_message_reply_markup(reply_markup=None)
                except: pass

            # لغو تسک auto-approve اگر وجود دارد
            info = PENDING.get(user_chat_id)
            if info and info.get("task"):
                try: info["task"].cancel()
                except: pass

            if action == "approve":
                info = PENDING.pop(user_chat_id, None)
                if info:
                    if ev and ev.get("capacity") and remaining_capacity(ev) <= 0:
                        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text="⚠️ ظرفیت پر شد؛ تایید نهایی انجام نشد.")
                    else:
                        if info.get("gender") == "male" and male_count(ev_id) >= MALE_LIMIT_PER_EVENT:
                            try: await context.bot.send_message(chat_id=user_chat_id, text=MALE_CAPACITY_FULL_MSG)
                            except: pass
                        else:
                            ROSTER.setdefault(ev_id, []).append({
                                "chat_id": user_chat_id,
                                "name": info.get("name","—"),
                                "username": info.get("username"),
                                "phone": info.get("phone","—"),
                                "gender": info.get("gender"),
                                "age": info.get("age"),
                                "when": info.get("when","—"),
                                "event_title": info.get("event_title","—"),
                            })
                            await save_pinned(context.application)

            await q.answer("انجام شد.")
        except Exception as e:
            print("Admin callback error:", e)
            await q.answer("مشکل پیش آمد.", show_alert=True)
        return

# ---------- Messages ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_user(update.effective_user, update.effective_chat.id)
    text = (update.message.text or "").strip()
    step = context.user_data.get("step")

    # ری‌استارت
    if re.fullmatch(r"شروع\s*مجدد(?:\s*🔄)?", text):
        return await render_home(update, context)

    # Feedback
    if context.user_data.get("feedback_mode"):
        try:
            if GROUP_CHAT_ID:
                user = update.effective_user
                header = f"💬 پیام جدید از کاربر:\n👤 {user.full_name}\n" + (f"🆔 @{user.username}\n" if user.username else "🆔 —\n")
                await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=header)
                try:
                    await context.bot.forward_message(chat_id=GROUP_CHAT_ID, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
                except:
                    await context.bot.copy_message(chat_id=GROUP_CHAT_ID, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
            await update.message.reply_text("ممنون از بازخوردت 💛 پیامت ارسال شد.", reply_markup=reply_main)
        finally:
            context.user_data["feedback_mode"] = False
        return

    # Flow
    if step == "name":
        if 2 <= len(text) <= 60:
            context.user_data["name"] = text
            return await render_gender(update, context, edit=False)
        else:
            return await update.message.reply_text("نام معتبر وارد کن (۲ تا ۶۰).")

    if step == "age":
        if text in ["-", "—"]:
            context.user_data["age"] = None
        else:
            if not re.fullmatch(r"\d{1,3}", text):
                return await update.message.reply_text("سن را به عدد وارد کن (مثلاً 23) یا «ترجیح می‌دهم نگویم».")
            a = int(text)
            if not (1 <= a <= 120):
                return await update.message.reply_text("سن نامعتبر است (1 تا 120).")
            context.user_data["age"] = a
        return await render_level(update, context, edit=False)

    if step == "level":
        # فقط با کلیدهای inline انتخاب می‌شود
        return await render_level(update, context, edit=False)

    if step == "phone":
        context.user_data["phone"] = text
        await update.message.reply_text("شماره دریافت شد ✅", reply_markup=reply_main)
        return await render_note(update, context, edit=False)

    if step == "note":
        context.user_data["note"] = text
        return await finalize_and_send(update, context)

    # فالبک
    return await render_home(update, context)

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_user(update.effective_user, update.effective_chat.id)
    if context.user_data.get("step") == "phone":
        context.user_data["phone"] = update.message.contact.phone_number
        await update.message.reply_text("شماره دریافت شد ✅", reply_markup=reply_main)
        await render_note(update, context, edit=False)

async def finalize_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data
    ev_id = u.get("selected_event_id") or (EVENTS[0]["id"] if EVENTS else None)
    ev = get_event(ev_id)

    # ظرفیت کلی رویداد
    if ev and ev.get("capacity") and remaining_capacity(ev) <= 0:
        await update.effective_chat.send_message(CAPACITY_CANCEL_MSG, reply_markup=reply_main)
        clear_flow(context); return

    # سقف آقایان — فقط آخر کار
    if u.get("gender") == "male" and ev_id and male_count(ev_id) >= MALE_LIMIT_PER_EVENT:
        await update.effective_chat.send_message(MALE_CAPACITY_FULL_MSG, reply_markup=reply_main)
        if CHANNEL_URL:
            await update.effective_chat.send_message(f"📢 از اخبار جا نمونی، عضو کانال شو:\n{CHANNEL_URL}")
        clear_flow(context); return

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
        await update.effective_chat.send_message(f"📢 برای اینکه از اخبار جا نمونی، عضو کانال شو:\n{CHANNEL_URL}")

    # Send to admin group
    admin_msg = None
    if GROUP_CHAT_ID:
        user_chat_id = update.effective_chat.id
        approve_cb = f"approve_{user_chat_id}_{ev_id or 'NA'}"
        reject_cb = f"reject_{user_chat_id}_{ev_id or 'NA'}"
        buttons = MK([[B("✅ تایید", callback_data=approve_cb), B("❌ رد", callback_data=reject_cb)]])
        admin_txt = (
            "🔔 ثبت‌نام جدید\n\n"
            f"👤 نام: {u.get('name','—')}\n"
            f"⚧ جنسیت: {({'male':'مرد','female':'زن'}).get(u.get('gender'),'—')}\n"
            f"🎂 سن: {u.get('age','—') if u.get('age') is not None else '—'}\n"
            f"🗣️ سطح: {u.get('level','—')}\n"
            f"📱 تماس: {u.get('phone','—')}\n"
            f"📝 توضیحات: {u.get('note','—')}\n\n"
        )
        if ev: admin_txt += event_text_admin(ev)
        admin_msg = await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=admin_txt, reply_markup=buttons)

        # Save pending + schedule auto-approve
        task = context.application.create_task(delayed_auto_approve(context.application, user_chat_id, ev_id, delay=AUTO_APPROVE_DELAY))
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

    clear_flow(context)

# =========================
#  AUTO-APPROVE via asyncio
# =========================
async def delayed_auto_approve(app, user_chat_id: int, ev_id: str, delay: int = AUTO_APPROVE_DELAY):
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    info = PENDING.get(user_chat_id)
    if not info: return
    ev = get_event(ev_id)
    if not ev:
        PENDING.pop(user_chat_id, None); return

    if ev.get("capacity") and remaining_capacity(ev) <= 0:
        try: await app.bot.send_message(chat_id=user_chat_id, text=CAPACITY_CANCEL_MSG)
        except: pass
        try:
            if info.get("admin_msg_id"):
                await app.bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=info["admin_msg_id"])
        except: pass
        PENDING.pop(user_chat_id, None); return

    if info.get("gender") == "male" and male_count(ev_id) >= MALE_LIMIT_PER_EVENT:
        try: await app.bot.send_message(chat_id=user_chat_id, text=MALE_CAPACITY_FULL_MSG)
        except: pass
        try:
            if info.get("admin_msg_id"):
                await app.bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=info["admin_msg_id"])
        except: pass
        PENDING.pop(user_chat_id, None); return

    ROSTER.setdefault(ev_id, []).append({
        "chat_id": user_chat_id,
        "name": info.get("name","—"),
        "username": info.get("username"),
        "phone": info.get("phone","—"),
        "gender": info.get("gender"),
        "age": info.get("age"),
        "when": info.get("when","—"),
        "event_title": info.get("event_title","—"),
    })
    await save_pinned(app)

    detail = (
        "🎉 ثبت‌نامت تایید شد!\n\n"
        f"📌 {ev.get('title','')}\n"
        f"🕒 {ev.get('when','')}\n"
        f"📍 {ev.get('place','—')}\n"
        f"💶 {ev.get('price','Free')}\n"
        f"📝 {ev.get('desc','—')}\n"
        "(Auto-approved by bot)"
    )
    link = MEETUP_LINKS.get(ev_id)
    if link: detail += f"\n🔗 لینک هماهنگی:\n{link}"
    try: await app.bot.send_message(chat_id=user_chat_id, text=detail)
    except: pass

    try:
        if info.get("admin_msg_id"):
            await app.bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=info["admin_msg_id"])
    except: pass

    PENDING.pop(user_chat_id, None)

# =========================
#     PTB + FastAPI APP
# =========================
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

# نکته‌ی مهم برای Python 3.13: حتماً job_queue(None)
application = ApplicationBuilder().token(BOT_TOKEN).job_queue(None).build()

# هندلرها
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^شروع\s*مجدد(?:\s*🔄)?$"), shortcut_restart))

# Admin commands (فقط در دیتاسنتر + ادمین)
application.add_handler(CommandHandler("dm", cmd_dm))
application.add_handler(CommandHandler("dmevent", cmd_dmevent))
application.add_handler(CommandHandler("dmall", cmd_dmall))

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
    await restore_from_pinned(application)
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
    return {"status": "ChillChat bot is running (pinned restore, admin DMs, no jobqueue)."}
