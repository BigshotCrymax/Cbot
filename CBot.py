# CBot.py — English Club Registration Bot (Webhook + FastAPI/Uvicorn)
# python-telegram-bot==20.3, fastapi, uvicorn
# UX per requirements:
# - حذف "لغو عملیات ❌" و فقط "شروع مجدد 🔄" همیشه در ReplyKeyboard (به‌جز مرحله‌ی شماره تماس)
# - Back در تمام مراحل ثبت‌نام
# - عدم نمایش آدرس/لوکیشن/نقشه تا قبل از تایید ادمین؛ فقط عنوان/زمان/قیمت/توضیح به کاربر
# - پیام ادمین شامل آدرس کامل/نقشه؛ پس از تایید، جزییات کامل برای کاربر ارسال می‌شود
# - دکمه Contact فقط در مرحله‌ی خودش و سپس بازگشت به کیبورد اصلی

import os
import json
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
#        SETTINGS
# =========================
BOT_TOKEN = os.environ.get("BOT_TOKEN")                       # REQUIRED
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")                   # REQUIRED (e.g. https://your-app.onrender.com)
GROUP_CHAT_ID = int(os.environ.get("GROUP_CHAT_ID", "0"))     # admin group/channel id (negative for groups)

# OPTIONAL: Google Sheets (kept off by default; don't set creds to disable)
GSPREAD_CREDS_JSON = os.environ.get("GSPREAD_CREDS_JSON")     # JSON string or None
SHEET_NAME = os.environ.get("SHEET_NAME", "EnglishClubRegistrations")

# EVENTS & LINKS
# Each event may contain: id, title, when, place, maps, price, desc
DEFAULT_EVENTS = [
    {
        "id": "m1",
        "title": "Coffee & Conversation",
        "when": "2025-10-12 18:30",
        "place": "Café République",  # shown to admins only until approved
        "maps": "https://maps.google.com/?q=Café+République",
        "price": "Free",
        "desc": "گفتگوهای آزاد با موضوع‌های روز؛ همه سطوح خوش آمدید.",
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
#        CONSTANT TEXTS
# =========================
reply_main = ReplyKeyboardMarkup([["شروع مجدد 🔄"]], resize_keyboard=True)

welcome_text = (
    "سلام! به ربات *English Club* خوش اومدی 🇬🇧☕\n"
    "اینجا می‌تونی رویدادهای زبان انگلیسی رو ببینی و ثبت‌نام کنی."
)

faq_text = (
    "**سوالات متداول ❔**\n\n"
    "• **کِی و کجا؟** هر هفته چند میت‌آپ داریم؛ از «🎉 رویدادهای پیش‌رو» ببین.\n"
    "• **سطح زبان؟** فرقی نمی‌کنه؛ سطحت رو می‌پرسیم تا گروه‌بندی بهتر شه.\n"
    "• **هزینه؟** بعضی رایگان، بعضی با هزینه‌ی کم (مثلاً شامل ۱ نوشیدنی).\n"
    "• **نهایی شدن؟** ثبت‌نامت برای ادمین میره؛ با تایید، جزییات کامل برات ارسال میشه."
)

rules_text = (
    "⚠️ قوانین English Club:\n"
    "• احترام به شرکت‌کننده‌ها.\n"
    "• تا حد امکان انگلیسی صحبت کن.\n"
    "• اگر منصرف شدی زودتر خبر بده."
)

# =========================
#       NAV / HELPERS
# =========================
def push_step(context: ContextTypes.DEFAULT_TYPE, step: str):
    nav = context.user_data.get("nav", [])
    nav.append(step)
    context.user_data["nav"] = nav

def pop_step(context: ContextTypes.DEFAULT_TYPE):
    nav = context.user_data.get("nav", [])
    if nav:
        nav.pop()
    context.user_data["nav"] = nav
    return nav[-1] if nav else None

def current_step(context: ContextTypes.DEFAULT_TYPE):
    nav = context.user_data.get("nav", [])
    return nav[-1] if nav else None

def clear_flow(context: ContextTypes.DEFAULT_TYPE):
    keys = ["nav", "selected_event_id", "name", "phone", "level", "note", "origin"]
    for k in keys:
        context.user_data.pop(k, None)

def get_event(ev_id):
    return next((e for e in EVENTS if e.get("id") == ev_id), None)

def build_main_menu():
    buttons = [
        [InlineKeyboardButton("🎉 رویدادهای پیش‌رو", callback_data="list_events")],
        [InlineKeyboardButton("📝 ثبت‌نام", callback_data="register")],
        [InlineKeyboardButton("❔ سوالات متداول", callback_data="faq")],
        [InlineKeyboardButton("🆘 پشتیبانی", callback_data="support")],
    ]
    return InlineKeyboardMarkup(buttons)

def build_events_buttons(compact=False):
    rows = []
    for e in EVENTS:
        label = f"{e['title']} | {e['when']}" if compact else f"{e['title']} | {e['when']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"event_{e['id']}")])
    if not rows:
        rows = [[InlineKeyboardButton("فعلاً رویدادی ثبت نشده", callback_data="noop")]]
    rows.append([InlineKeyboardButton("↩️ بازگشت", callback_data="back_home")])
    return InlineKeyboardMarkup(rows)

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

def event_detail_text_user(ev):
    # To user: hide place/maps until approved
    lines = [
        f"**{ev.get('title','')}**",
        f"🕒 {ev.get('when','')}",
    ]
    if ev.get("price"):
        lines.append(f"💶 {ev['price']}")
    if ev.get("desc"):
        lines.append(f"\n📝 {ev['desc']}")
    lines.append("\n(آدرس دقیق پس از تایید ثبت‌نام ارسال می‌شود.)")
    return "\n".join(lines)

def event_detail_text_admin(ev):
    # To admins: full details
    return (
        f"📌 **{ev.get('title','')}**\n"
        f"🕒 {ev.get('when','')}\n"
        f"📍 {ev.get('place','—')}\n"
        f"🗺️ {ev.get('maps','—')}\n"
        f"💶 {ev.get('price','Free')}\n"
        f"📝 {ev.get('desc','—')}"
    )

def event_inline_register(ev_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 ثبت‌نام در همین رویداد", callback_data=f"register_{ev_id}")],
        [InlineKeyboardButton("↩️ بازگشت", callback_data="list_events")],
    ])

# =========================
#        RENDER STEPS
# =========================
async def render_home(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    clear_flow(context)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text("یکی از گزینه‌ها رو انتخاب کن:", reply_markup=build_main_menu())
    else:
        if update.message:
            await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=reply_main)
            await update.message.reply_text("یکی از گزینه‌ها رو انتخاب کن:", reply_markup=build_main_menu())
        elif update.callback_query:
            await update.callback_query.edit_message_text("یکی از گزینه‌ها رو انتخاب کن:", reply_markup=build_main_menu())

async def render_event_list(update: Update):
    await update.callback_query.edit_message_text("رویدادهای پیش‌رو:", reply_markup=build_events_buttons())

async def render_event_detail(update: Update, ev):
    await update.callback_query.edit_message_text(
        event_detail_text_user(ev),
        parse_mode="Markdown",
        reply_markup=event_inline_register(ev["id"])
    )

async def render_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    push_step(context, "rules")
    if update.callback_query:
        await update.callback_query.edit_message_text(rules_text, reply_markup=rules_inline())
    else:
        await update.message.reply_text(rules_text, reply_markup=rules_inline())

async def render_name(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    push_step(context, "name")
    txt = "لطفاً *نام و نام خانوادگی* رو وارد کن:"
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(txt, parse_mode="Markdown", reply_markup=back_inline())
    else:
        await update.effective_chat.send_message(txt, parse_mode="Markdown", reply_markup=back_inline())

async def render_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    push_step(context, "phone")
    contact_btn = ReplyKeyboardMarkup(
        [[KeyboardButton("ارسال شماره تماس 📱", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )
    await update.effective_chat.send_message("شماره تلفنت رو وارد کن یا دکمه زیر رو بزن:", reply_markup=contact_btn)
    # برای Back از طریق inline در پیام جداگانه:
    await update.effective_chat.send_message("یا می‌تونی به مرحله قبل برگردی:", reply_markup=back_inline())

async def render_level(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    push_step(context, "level")
    if update.callback_query and edit:
        await update.callback_query.edit_message_text("سطح زبانت چیه؟ یکی رو انتخاب کن:", reply_markup=level_inline())
    else:
        await update.effective_chat.send_message("سطح زبانت چیه؟ یکی رو انتخاب کن:", reply_markup=level_inline())

async def render_note(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    push_step(context, "note")
    txt = "یادداشت/نیاز خاص داری؟ (اختیاری) اینجا بنویس و بفرست. اگر چیزی نداری، فقط یک خط تیره `-` بفرست."
    if update.callback_query and edit:
        await update.callback_query.edit_message_text(txt, parse_mode="Markdown", reply_markup=back_inline())
    else:
        await update.effective_chat.send_message(txt, parse_mode="Markdown", reply_markup=back_inline())

async def go_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # remove current
    pop_step(context)
    prev = current_step(context)
    # If no prev, go to origin or home
    origin = context.user_data.get("origin")  # "menu" | "event"
    sel_ev = get_event(context.user_data.get("selected_event_id"))

    if not prev:
        # If came from event detail, back to event detail; else home
        if origin == "event" and sel_ev and update.callback_query:
            return await render_event_detail(update, sel_ev)
        return await render_home(update, context, edit=True)

    # Re-render previous step
    if prev == "rules":
        if update.callback_query:
            await update.callback_query.edit_message_text(rules_text, reply_markup=rules_inline())
        else:
            await update.effective_chat.send_message(rules_text, reply_markup=rules_inline())
    elif prev == "name":
        await render_name(update, context, edit=True)
    elif prev == "phone":
        await render_phone(update, context)
    elif prev == "level":
        await render_level(update, context, edit=True)
    elif prev == "note":
        await render_note(update, context, edit=True)
    else:
        await render_home(update, context, edit=True)

# =========================
#        HANDLERS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await render_home(update, context)

async def restart_shortcut(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await render_home(update, context)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data

    # guard for level (to avoid being swallowed)
    if data.startswith("lvl_"):
        return await handle_level(update, context)

    await q.answer()

    if data == "noop":
        return

    if data == "back_home":
        return await render_home(update, context, edit=True)

    if data == "back_step":
        return await go_back(update, context)

    if data == "faq":
        return await q.edit_message_text(
            faq_text, parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ بازگشت", callback_data="back_home")]])
        )

    if data == "support":
        return await q.edit_message_text(
            "برای پشتیبانی به آیدی زیر پیام بده:\n@englishclub_support",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ بازگشت", callback_data="back_home")]])
        )

    if data == "list_events":
        return await render_event_list(update)

    if data.startswith("event_"):
        ev_id = data.split("_", 1)[1]
        ev = get_event(ev_id)
        if not ev:
            return await q.answer("این رویداد یافت نشد.", show_alert=True)
        # view-only path; if later registers from here, origin="event"
        await render_event_detail(update, ev)
        return

    if data == "register" or data.startswith("register_"):
        # registration path
        if data.startswith("register_"):
            context.user_data["selected_event_id"] = data.split("_", 1)[1]
            context.user_data["origin"] = "event"
        else:
            context.user_data["origin"] = "menu"
            # if no selected event, ask quick selection (compact list)
            if not context.user_data.get("selected_event_id"):
                await q.edit_message_text("یکی از رویدادها رو انتخاب کن:", reply_markup=build_events_buttons(compact=True))
                # Set a light step so that back_home returns to menu
                push_step(context, "pick_event")
                return
        # Continue to rules
        await render_rules(update, context)
        return

    if data == "accept_rules":
        await render_name(update, context, edit=True)
        return

    # Admin approve/reject
    if data.startswith("approve_") or data.startswith("reject_"):
        try:
            action, user_chat_id, ev_id = data.split("_", 2)
            user_chat_id = int(user_chat_id)
            ev = get_event(ev_id)
            admin_name = q.from_user.first_name

            if action == "approve":
                # Send full details to user (now reveal)
                if ev:
                    detail = (
                        "🎉 ثبت‌نامت تایید شد!\n\n"
                        f"📌 {ev.get('title','')}\n"
                        f"🕒 {ev.get('when','')}\n"
                        f"📍 {ev.get('place','—')}\n"
                        f"🗺️ {ev.get('maps','—')}\n"
                        f"💶 {ev.get('price','Free')}\n"
                        f"📝 {ev.get('desc','—')}\n"
                    )
                else:
                    detail = "🎉 ثبت‌نامت تایید شد! به‌زودی اطلاعات نهایی برات ارسال می‌شه."
                link = MEETUP_LINKS.get(ev_id)
                if link:
                    detail += f"\n🔗 لینک گروه/هماهنگی:\n{link}"
                await context.bot.send_message(chat_id=user_chat_id, text=detail)
            else:
                await context.bot.send_message(chat_id=user_chat_id, text="⚠️ متاسفانه ثبت‌نامت تایید نشد.")

            await q.answer("انجام شد.")
        except Exception as e:
            print(f"Admin callback error: {e}")
            await q.answer("مشکلی پیش اومد.", show_alert=True)
        return

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    step = current_step(context)

    # "شروع مجدد 🔄" via reply keyboard
    if text == "شروع مجدد 🔄":
        return await render_home(update, context)

    if step == "pick_event":
        # ignore free text during event pick (we use buttons)
        return

    if step == "name":
        if 2 <= len(text) <= 60:
            context.user_data["name"] = text
            # move to phone
            await render_phone(update, context)
        else:
            await update.message.reply_text("لطفاً نام معتبر وارد کن (۲ تا ۶۰ کاراکتر).")
        return

    if step == "phone":
        # treat as manual phone entry
        context.user_data["phone"] = text
        # restore main keyboard after phone step
        await update.message.reply_text("دریافت شد ✅", reply_markup=reply_main)
        await render_level(update, context, edit=False)
        return

    if step == "note":
        context.user_data["note"] = text
        await finalize_and_send(update, context)
        return

    # otherwise ignore

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if current_step(context) == "phone":
        context.user_data["phone"] = update.message.contact.phone_number
        # restore main keyboard after contact is received
        await update.message.reply_text("شماره دریافت شد ✅", reply_markup=reply_main)
        await render_level(update, context, edit=False)

async def handle_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    await q.answer()
    lvl_map = {"lvl_A": "Beginner (A1–A2)", "lvl_B": "Intermediate (B1–B2)", "lvl_C": "Advanced (C1+)"}
    context.user_data["level"] = lvl_map.get(data, "Unknown")
    await render_note(update, context, edit=True)

async def finalize_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_info = context.user_data
    ev_id = user_info.get("selected_event_id")
    if not ev_id and EVENTS:
        ev_id = EVENTS[0]["id"]
        user_info["selected_event_id"] = ev_id
    ev = get_event(ev_id)

    # Summary for user (no address yet)
    summary = (
        "✅ درخواست ثبت‌نامت ثبت شد و برای ادمین ارسال می‌شود.\n\n"
        f"👤 نام: {user_info.get('name','—')}\n"
        f"📱 تماس: {user_info.get('phone','—')}\n"
        f"🗣️ سطح: {user_info.get('level','—')}\n"
        f"📝 توضیحات: {user_info.get('note','—')}\n"
    )
    if ev:
        summary += f"\n📌 رویداد: {ev.get('title','')}\n🕒 زمان: {ev.get('when','')}\n(آدرس پس از تایید ارسال می‌شود.)"
    await update.effective_chat.send_message(summary, reply_markup=reply_main)

    # Send to admin group with full details
    if GROUP_CHAT_ID:
        user_chat_id = update.effective_chat.id
        approve_cb = f"approve_{user_chat_id}_{ev_id or 'NA'}"
        reject_cb = f"reject_{user_chat_id}_{ev_id or 'NA'}"
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تایید", callback_data=approve_cb),
             InlineKeyboardButton("❌ رد", callback_data=reject_cb)]
        ])
        admin_txt = (
            "🔔 **ثبت‌نام جدید English Club**\n\n"
            f"👤 **نام:** {user_info.get('name','—')}\n"
            f"📱 **تماس:** {user_info.get('phone','—')}\n"
            f"🗣️ **سطح:** {user_info.get('level','—')}\n"
            f"📝 **توضیحات:** {user_info.get('note','—')}\n\n"
        )
        if ev:
            admin_txt += event_detail_text_admin(ev)
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=admin_txt, parse_mode='Markdown', reply_markup=buttons)

    # Optionally write to Google Sheets (kept off unless creds provided)
    await maybe_write_to_sheet(user_info, ev)

    # Clear flow
    clear_flow(context)

# =========================
#  OPTIONAL: Google Sheets
# =========================
async def maybe_write_to_sheet(user_info, ev):
    if not GSPREAD_CREDS_JSON:
        return
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials
        scope = [
            'https://spreadsheets.google.com/feeds',
            'https://www.googleapis.com/auth/drive'
        ]
        creds_dict = json.loads(GSPREAD_CREDS_JSON)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)

        try:
            sh = client.open(SHEET_NAME)
        except Exception:
            sh = client.create(SHEET_NAME)
        ws = sh.sheet1
        # header
        try:
            if ws.get('A1:F1') == []:
                ws.update('A1:F1', [["Timestamp","Event","Name","Phone","Level","Note"]])
        except Exception:
            pass
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        ws.append_row([
            now,
            (ev.get('title') if ev else '—'),
            user_info.get('name','—'),
            user_info.get('phone','—'),
            user_info.get('level','—'),
            user_info.get('note','—'),
        ])
    except Exception as e:
        print("Sheets error:", e)

# =========================
#  PTB App + FastAPI App
# =========================
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
application = ApplicationBuilder().token(BOT_TOKEN).build()

# Handlers (order matters)
application.add_handler(CommandHandler("start", start))
# حذف /cancel؛ فقط شورتکات شروع مجدد
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^شروع مجدد 🔄$"), restart_shortcut))

# Callbacks
application.add_handler(CallbackQueryHandler(handle_level, pattern=r"^lvl_"))  # must be before generic
application.add_handler(CallbackQueryHandler(handle_callback))

# Contact & free text
application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

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
    return {"status": "CBot (webhook) is running."}
