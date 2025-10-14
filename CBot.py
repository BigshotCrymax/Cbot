# CBot.py — Chill & Chat Community Bot (Webhook + FastAPI/Uvicorn)
# Deps: python-telegram-bot==20.3, fastapi, uvicorn

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
#          CONFIG
# =========================
BOT_TOKEN = os.environ.get("BOT_TOKEN")                       # REQUIRED
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")                   # REQUIRED
GROUP_CHAT_ID = int(os.environ.get("GROUP_CHAT_ID", "0"))     # Admin group/channel ID (negative for groups)

# Optional envs
SUPPORT_USERNAME = os.environ.get("SUPPORT_USERNAME", "akamehdii")
CHANNEL_URL = os.environ.get("CHANNEL_URL", "")               # e.g. https://t.me/chillandchatclub
GROUP_URL   = os.environ.get("GROUP_URL", "")                 # e.g. https://t.me/chillandchatcommunity
INSTAGRAM_URL = os.environ.get("INSTAGRAM_URL", "")           # e.g. https://instagram.com/chillandchat

# Google Sheets (OFF by default unless creds provided)
GSPREAD_CREDS_JSON = os.environ.get("GSPREAD_CREDS_JSON")     # JSON string or None
SHEET_NAME = os.environ.get("SHEET_NAME", "EnglishClubRegistrations")

# Events (users won't see address/maps until approval)
DEFAULT_EVENTS = [
    {
        "id": "m1",
        "title": "Coffee & Conversation",
        "when": "2025-10-12 18:30",
        "place": "Café République",
        "maps": "https://maps.google.com/?q=Café+République",
        "price": "Free",
        "desc": "جلسه‌ی گفتگوهای آزاد انگلیسی با موضوعات سبک و دوستانه.",
    }
]
try:
    EVENTS = json.loads(os.environ.get("EVENTS_JSON", "")) or DEFAULT_EVENTS
    if not isinstance(EVENTS, list):
        EVENTS = DEFAULT_EVENTS
except Exception:
    EVENTS = DEFAULT_EVENTS

# Private links to send AFTER approval (optional)
try:
    MEETUP_LINKS = json.loads(os.environ.get("MEETUP_LINKS_JSON", "{}"))
except Exception:
    MEETUP_LINKS = {}

# =========================
#          TEXTS
# =========================
reply_main = ReplyKeyboardMarkup([["شروع مجدد 🔄"]], resize_keyboard=True)

WELCOME = (
    "سلام! به *Chill & Chat Community* خوش اومدی ☕🇬🇧\n"
    "اینجا می‌تونی رویدادهای زبان انگلیسی رو ببینی و ثبت‌نام کنی."
)
FAQ = (
    "❔ **سوالات متداول درباره Chill & Chat**\n\n"
    "🗣️ **در جلسات چی کار می‌کنیم؟**\n"
    "با بقیه به انگلیسی صحبت می‌کنی، بازی می‌کنیم، موضوع‌های روز رو تمرین می‌کنیم، و کلی آشناهای جدید پیدا می‌کنی!\n\n"
    "☕ **کجا برگزار می‌شن؟**\n"
    "جلسات در کافه برگزار می‌شن و برای همه سطوح بازن.\n\n"
    "💶 **هزینه شرکت چقدره؟**\n"
    "رویدادها معمولاً رایگان هستن؛ فقط لازمه یک سفارش از کافه داشته باشی.\n\n"
    "📸 **آیا از جلسات عکس گرفته میشه؟**\n"
    "گاهی بله! فقط با رضایت شرکت‌کننده‌ها برای شبکه‌های اجتماعی.\n\n"
    "📝 **چطور ثبت‌نام کنم؟**\n"
    "ثبت‌نامت ابتدا برای ادمین ارسال میشه و بعد از تایید، آدرس دقیق برات ارسال میشه."
)

RULES = (
    "⚠️ قوانین Chill & Chat:\n"
    "• احترام به همه.\n"
    "• تا حد امکان انگلیسی صحبت کن.\n"
    "• اگر منصرف شدی زودتر خبر بده."
)

SOCIAL_TEXT = lambda: (
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

def event_text_user(ev):
    # address hidden until approval
    parts = [f"**{ev.get('title','')}**", f"🕒 {ev.get('when','')}"]
    if ev.get("price"): parts.append(f"💶 {ev['price']}")
    if ev.get("desc"):  parts.append(f"\n📝 {ev['desc']}")
    parts.append("\n(آدرس دقیق پس از تایید ارسال می‌شود.)")
    return "\n".join(parts)

def event_text_admin(ev):
    return (
        f"📌 **{ev.get('title','')}**\n"
        f"🕒 {ev.get('when','')}\n"
        f"📍 {ev.get('place','—')}\n"
        f"🗺️ {ev.get('maps','—')}\n"
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
    for k in ["nav","origin","selected_event_id","name","phone","level","note","feedback_mode"]:
        context.user_data.pop(k, None)

# =========================
#          UI
# =========================
def build_main_menu():
    buttons = [
        [InlineKeyboardButton("🎉 رویدادهای پیش‌رو", callback_data="list_events")],
        [InlineKeyboardButton("📝 ثبت‌نام سریع", callback_data="register")],
        [InlineKeyboardButton("❔ سوالات متداول", callback_data="faq")],
        [InlineKeyboardButton("🆘 پشتیبانی", callback_data="support")],
        [InlineKeyboardButton("💬 ارسال نظر و پیشنهاد", callback_data="feedback_start")],
        [InlineKeyboardButton("🌐 شبکه‌های اجتماعی", callback_data="socials")],
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
    await update.callback_query.edit_message_text(
        "رویدادهای پیش‌رو:",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"{e['title']} | {e['when']}", callback_data=f"event_{e['id']}")] for e in EVENTS]
            + [[InlineKeyboardButton("↩️ بازگشت", callback_data="back_home")]]
        )
    )

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

async def render_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    push_step(context, "phone")
    contact_btn = ReplyKeyboardMarkup(
        [[KeyboardButton("ارسال شماره تماس 📱", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )
    await update.effective_chat.send_message("شماره تلفنت رو وارد کن یا دکمه زیر رو بزن:", reply_markup=contact_btn)
    # Back inline as separate message
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
    pop_step(context)   # remove current
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
    elif prev == "phone":
        await render_phone(update, context)
    elif prev == "level":
        await render_level(update, context, edit=True)
    elif prev == "note":
        await render_note(update, context, edit=True)
    else:
        await render_home(update, context, edit=True)

# =========================
#         HANDLERS
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await render_home(update, context)

async def shortcut_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await render_home(update, context)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data

    # route lvl_ early
    if data.startswith("lvl_"):
        return await handle_level(update, context)

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

    if data == "socials":
        return await q.edit_message_text(SOCIAL_TEXT(), parse_mode="Markdown",
                                         reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ بازگشت", callback_data="back_home")]]))

    if data == "list_events":
        return await render_event_list(update)

    if data.startswith("event_"):
        ev = get_event(data.split("_",1)[1])
        if not ev:
            return await q.answer("این رویداد یافت نشد.", show_alert=True)
        return await render_event_detail(update, ev)

    if data == "register" or data.startswith("register_"):
        if data.startswith("register_"):
            context.user_data["selected_event_id"] = data.split("_",1)[1]
            context.user_data["origin"] = "event"
        else:
            context.user_data["origin"] = "menu"
            if not context.user_data.get("selected_event_id"):
                await q.edit_message_text("یکی از رویدادها رو انتخاب کن:",
                                          reply_markup=InlineKeyboardMarkup(
                                              [[InlineKeyboardButton(f"{e['title']} | {e['when']}", callback_data=f"event_{e['id']}")] for e in EVENTS]
                                              + [[InlineKeyboardButton("↩️ بازگشت", callback_data="back_home")]]
                                          ))
                push_step(context, "pick_event")
                return
        return await render_rules(update, context)

    if data == "accept_rules":
        return await render_name(update, context, edit=True)

    if data == "feedback_start":
        context.user_data["feedback_mode"] = True
        txt = "💬 نظرت یا پیشنهادت درباره Chill & Chat چیه؟ همین‌جا برامون بفرست.\n(می‌تونی متن، عکس، ویس یا ویدیو بفرستی.)"
        return await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ بازگشت", callback_data="back_home")]]))

    # Admin approve/reject
    if data.startswith("approve_") or data.startswith("reject_"):
        try:
            action, user_chat_id, ev_id = data.split("_", 2)
            user_chat_id = int(user_chat_id)
            ev = get_event(ev_id)

            if action == "approve":
                # Reveal full details to user
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
                    detail = "🎉 ثبت‌نامت تایید شد!"
                link = MEETUP_LINKS.get(ev_id)
                if link:
                    detail += f"\n🔗 لینک گروه/هماهنگی:\n{link}"
                await context.bot.send_message(chat_id=user_chat_id, text=detail)
            else:
                await context.bot.send_message(chat_id=user_chat_id, text="⚠️ متاسفانه ثبت‌نامت تایید نشد.")
            await q.answer("انجام شد.")
        except Exception as e:
            print("Admin callback error:", e)
            await q.answer("مشکلی پیش اومد.", show_alert=True)
        return

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    step = current_step(context)

    # Global restart
    if text == "شروع مجدد 🔄":
        return await render_home(update, context)

    # Feedback mode: forward any message to admin group
    if context.user_data.get("feedback_mode"):
        if GROUP_CHAT_ID:
            user = update.effective_user
            header = (
                "💬 پیام جدید از کاربر Chill & Chat:\n"
                f"👤 نام: {user.full_name}\n"
                f"🆔 @{user.username}" if user.username else "🆔 —"
            )
            try:
                await update.effective_chat.send_action("typing")
            except Exception:
                pass
            # send header then forward original
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=header)
            await context.bot.forward_message(
                chat_id=GROUP_CHAT_ID,
                from_chat_id=update.effective_chat.id,
                message_id=update.message.message_id
            )
        await update.message.reply_text("ممنون از بازخوردت 💛 پیام تو برای تیم Chill & Chat ارسال شد.", reply_markup=reply_main)
        context.user_data["feedback_mode"] = False
        return

    # Registration flow
    if step == "pick_event":
        return  # ignore free text

    if step == "name":
        if 2 <= len(text) <= 60:
            context.user_data["name"] = text
            return await render_phone(update, context)
        else:
            return await update.message.reply_text("لطفاً نام معتبر وارد کن (۲ تا ۶۰ کاراکتر).")

    if step == "phone":
        context.user_data["phone"] = text
        await update.message.reply_text("دریافت شد ✅", reply_markup=reply_main)
        return await render_level(update, context, edit=False)

    if step == "note":
        context.user_data["note"] = text
        return await finalize_and_send(update, context)

    # otherwise ignore

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if current_step(context) == "phone":
        context.user_data["phone"] = update.message.contact.phone_number
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
    u = context.user_data
    ev_id = u.get("selected_event_id") or (EVENTS[0]["id"] if EVENTS else None)
    ev = get_event(ev_id)

    summary = (
        "✅ درخواست ثبت‌نامت ثبت شد و برای ادمین ارسال می‌شود.\n\n"
        f"👤 نام: {u.get('name','—')}\n"
        f"📱 تماس: {u.get('phone','—')}\n"
        f"🗣️ سطح: {u.get('level','—')}\n"
        f"📝 توضیحات: {u.get('note','—')}\n"
    )
    if ev:
        summary += f"\n📌 رویداد: {ev.get('title','')}\n🕒 زمان: {ev.get('when','')}\n(آدرس پس از تایید ارسال می‌شود.)"
    await update.effective_chat.send_message(summary, reply_markup=reply_main)

    if GROUP_CHAT_ID:
        user_chat_id = update.effective_chat.id
        approve_cb = f"approve_{user_chat_id}_{ev_id or 'NA'}"
        reject_cb = f"reject_{user_chat_id}_{ev_id or 'NA'}"
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تایید", callback_data=approve_cb),
             InlineKeyboardButton("❌ رد", callback_data=reject_cb)]
        ])
        admin_txt = (
            "🔔 **ثبت‌نام جدید Chill & Chat**\n\n"
            f"👤 **نام:** {u.get('name','—')}\n"
            f"📱 **تماس:** {u.get('phone','—')}\n"
            f"🗣️ **سطح:** {u.get('level','—')}\n"
            f"📝 **توضیحات:** {u.get('note','—')}\n\n"
        )
        if ev:
            admin_txt += event_text_admin(ev)
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=admin_txt, parse_mode='Markdown', reply_markup=buttons)

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
#     PTB + FastAPI APP
# =========================
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
application = ApplicationBuilder().token(BOT_TOKEN).build()

# Handlers (order matters)
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^شروع مجدد 🔄$"), shortcut_restart))
application.add_handler(CallbackQueryHandler(handle_level, pattern=r"^lvl_"))  # must be before generic
application.add_handler(CallbackQueryHandler(handle_callback))
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
    return {"status": "Chill & Chat bot is running."}



