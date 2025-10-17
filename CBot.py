try:
    import sqlite3
except ModuleNotFoundError:
    import pysqlite3 as sqlite3
    import sys
    sys.modules['sqlite3'] = sqlite3

import os
import json
import re
import asyncio
import aiosqlite
import qrcode
import io
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from typing import Optional
from PIL import Image, ImageDraw, ImageFont
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, InputFile
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
GROUP_CHAT_ID = int(os.environ.get("GROUP_CHAT_ID", "0"))
DATACENTER_CHAT_ID = int(os.environ.get("DATACENTER_CHAT_ID", str(GROUP_CHAT_ID or 0)))
DB_PATH = os.environ.get("DB_PATH", "chillchat_full.db")
try:
    ADMIN_IDS = json.loads(os.environ.get("ADMIN_IDS_JSON", "[]"))
except Exception:
    ADMIN_IDS = []

AUTO_APPROVE_DELAY = int(os.environ.get("AUTO_APPROVE_DELAY", str(12 * 60 * 60)))
REMINDER_BEFORE_HOURS = int(os.environ.get("REMINDER_BEFORE_HOURS", "24"))
MALE_LIMIT_PER_EVENT = int(os.environ.get("MALE_LIMIT_PER_EVENT", "5"))
TICKET_WIDTH = 1000
TICKET_HEIGHT = 600
FONT_PATH = os.environ.get("FONT_PATH", "")
if not FONT_PATH:
    try:
        FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    except Exception:
        FONT_PATH = ""

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    xp INTEGER DEFAULT 0,
    badges TEXT DEFAULT '[]',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    title TEXT,
    when_dt TEXT,
    place TEXT,
    price TEXT,
    capacity INTEGER,
    description TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS roster (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    event_id TEXT,
    status TEXT,
    phone TEXT,
    gender TEXT,
    age INTEGER,
    note TEXT,
    username TEXT,
    admin_msg_id INTEGER,
    ticket_sent INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS pending_tasks (
    user_id INTEGER PRIMARY KEY,
    ev_id TEXT,
    run_at TEXT
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    text TEXT,
    created_at TEXT
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_TABLES_SQL)
        await db.commit()

async def ensure_user_record(user):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id FROM users WHERE id=?", (user.id,))
        row = await cur.fetchone()
        if row:
            await db.execute("UPDATE users SET username=?, full_name=? WHERE id=?", (user.username, user.full_name, user.id))
        else:
            await db.execute("INSERT INTO users (id, username, full_name, xp, badges, created_at) VALUES (?,?,?,?,?,?)", (user.id, user.username, user.full_name, 0, '[]', now))
        await db.commit()

async def add_xp(user_id: int, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET xp = xp + ? WHERE id=?", (amount, user_id))
        await db.commit()

async def get_leaderboard(limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, username, full_name, xp FROM users ORDER BY xp DESC LIMIT ?", (limit,))
        rows = await cur.fetchall()
        return rows

def _text_size(draw, text, font):
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        return w, h
    except Exception:
        return draw.textsize(text, font=font)

def make_ticket_image(name: str, event_title: str, ev_id: str) -> io.BytesIO:
    payload = json.dumps({"user": name, "event": event_title, "ev_id": ev_id})
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert('RGBA')
    try:
        font_title = ImageFont.truetype(FONT_PATH, 48) if FONT_PATH else ImageFont.load_default()
        font_sub = ImageFont.truetype(FONT_PATH, 28) if FONT_PATH else ImageFont.load_default()
    except Exception:
        font_title = ImageFont.load_default()
        font_sub = ImageFont.load_default()
    card = Image.new('RGBA', (TICKET_WIDTH, TICKET_HEIGHT), (255, 255, 255, 255))
    draw = ImageDraw.Draw(card)
    band_height = 140
    draw.rectangle([(0, 0), (TICKET_WIDTH, band_height)], fill=(40, 40, 80))
    title_text = "ChillChat Ticket"
    w, h = _text_size(draw, title_text, font_title)
    draw.text(((TICKET_WIDTH - w) / 2, (band_height - h) / 2), title_text, font=font_title, fill=(255, 255, 255))
    ev_title_pos = (40, band_height + 30)
    draw.text(ev_title_pos, event_title, font=font_sub, fill=(10, 10, 10))
    name_box_pos = (40, band_height + 90)
    draw.text(name_box_pos, f"Name: {name}", font=font_sub, fill=(10, 10, 10))
    qr_size = 360
    qr_img = qr_img.resize((qr_size, qr_size))
    card.paste(qr_img, (TICKET_WIDTH - qr_size - 40, band_height + 40), qr_img)
    footer = f"Event ID: {ev_id} — Generated: {datetime.utcnow().date()}"
    fw, fh = _text_size(draw, footer, font_sub)
    draw.text((40, TICKET_HEIGHT - fh - 30), footer, font=font_sub, fill=(80, 80, 80))
    bio = io.BytesIO()
    card.convert('RGB').save(bio, format='JPEG', quality=90)
    bio.seek(0)
    return bio

async def background_worker(app):
    while True:
        try:
            now = datetime.utcnow()
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT user_id, ev_id, run_at FROM pending_tasks WHERE run_at<=?", (now.isoformat(),))
                rows = await cur.fetchall()
                for user_id, ev_id, run_at in rows:
                    await auto_approve_user(app, user_id, ev_id)
                    await db.execute("DELETE FROM pending_tasks WHERE user_id=?", (user_id,))
                await db.commit()
                soon = now + timedelta(hours=REMINDER_BEFORE_HOURS)
                cur = await db.execute("SELECT id, title, when_dt FROM events WHERE when_dt IS NOT NULL")
                evs = await cur.fetchall()
                for ev_id, title, when_dt in evs:
                    try:
                        ev_dt = datetime.fromisoformat(when_dt)
                    except Exception:
                        continue
                    if now < ev_dt <= soon:
                        cur2 = await db.execute("SELECT id, user_id, ticket_sent FROM roster WHERE event_id=? AND status='approved'", (ev_id,))
                        rows2 = await cur2.fetchall()
                        for rid, user_id, ticket_sent in rows2:
                            if ticket_sent & 2 == 0:
                                try:
                                    await app.bot.send_message(chat_id=user_id, text=f"🔔 Reminder: '{title}' at {ev_dt.isoformat()}")
                                except Exception:
                                    pass
                                await db.execute("UPDATE roster SET ticket_sent = ticket_sent | 2 WHERE id=?", (rid,))
                await db.commit()
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(5)

async def auto_approve_user(app, user_chat_id: int, ev_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT capacity, title FROM events WHERE id=?", (ev_id,))
        ev = await cur.fetchone()
        if not ev:
            await db.execute("DELETE FROM roster WHERE user_id=? AND event_id=? AND status='pending'", (user_chat_id, ev_id))
            await db.commit()
            return
        capacity, title = ev
        cur2 = await db.execute("SELECT COUNT(*) FROM roster WHERE event_id=? AND status='approved'", (ev_id,))
        cnt = (await cur2.fetchone())[0]
        if capacity and cnt >= capacity:
            await db.execute("UPDATE roster SET status='rejected' WHERE user_id=? AND event_id=? AND status='pending'", (user_chat_id, ev_id))
            await db.commit()
            try:
                await app.bot.send_message(chat_id=user_chat_id, text="❌ Sorry, event full.")
            except Exception:
                pass
            return
        await db.execute("UPDATE roster SET status='auto_approved' WHERE user_id=? AND event_id=? AND status='pending'", (user_chat_id, ev_id))
        await db.commit()
        try:
            await app.bot.send_message(chat_id=user_chat_id, text=f"🎉 Your registration for '{title}' was auto-approved.")
        except Exception:
            pass
        await add_xp(user_chat_id, 20)

reply_main = ReplyKeyboardMarkup([["شروع مجدد 🔄"]], resize_keyboard=True)
WELCOME = "سلام! به ChillChat خوش آمدی — از منو استفاده کن یا /start را بزن."

def build_main_menu():
    buttons = [
        [InlineKeyboardButton("🎉 رویدادها", callback_data="list_events")],
        [InlineKeyboardButton("📝 ثبت‌نام سریع", callback_data="register")],
        [InlineKeyboardButton("💬 ارسال نظر و پیشنهاد", callback_data="feedback_start")],
        [InlineKeyboardButton("👤 پروفایل من", callback_data="my_profile")],
        [InlineKeyboardButton("🏆 لیدربورد", callback_data="leaderboard")],
    ]
    return InlineKeyboardMarkup(buttons)

def push_step(context, step):
    nav = context.user_data.get("nav", [])
    nav.append(step)
    context.user_data["nav"] = nav

def pop_step(context):
    nav = context.user_data.get("nav", [])
    if nav:
        nav.pop()
    context.user_data["nav"] = nav
    return nav[-1] if nav else None

def current_step(context):
    nav = context.user_data.get("nav", [])
    return nav[-1] if nav else None

def clear_flow(context):
    for k in ["nav", "origin", "selected_event_id", "name", "phone", "level", "gender", "age", "note", "feedback_mode"]:
        context.user_data.pop(k, None)

async def render_home(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    clear_flow(context)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text("یکی از گزینه‌ها رو انتخاب کن:", reply_markup=build_main_menu())
    else:
        if update.message:
            await update.message.reply_text(WELCOME, reply_markup=reply_main)
            await update.message.reply_text("یکی از گزینه‌ها رو انتخاب کن:", reply_markup=build_main_menu())
        elif update.callback_query:
            await update.callback_query.edit_message_text("یکی از گزینه‌ها رو انتخاب کن:", reply_markup=build_main_menu())

async def render_event_list(update: Update):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id,title,when_dt FROM events ORDER BY when_dt ASC LIMIT 50")
        rows = await cur.fetchall()
    if not rows:
        sample = [
            ("evt1", "2nd Meeting", "2025-11-01T18:00:00", "Café A", "order", 12, "Intro meeting"),
            ("evt2", "Debate Night", "2025-11-08T19:00:00", "Café B", "order", 20, "Debates in English"),
        ]
        async with aiosqlite.connect(DB_PATH) as db:
            for r in sample:
                await db.execute("INSERT OR REPLACE INTO events (id,title,when_dt,place,price,capacity,description,created_at) VALUES (?,?,?,?,?,?,?,?)", (r[0], r[1], r[2], r[3], r[4], r[5], r[6], datetime.utcnow().isoformat()))
            await db.commit()
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id,title,when_dt FROM events ORDER BY when_dt ASC LIMIT 50")
            rows = await cur.fetchall()
    buttons = []
    for ev_id, title, when_dt in rows:
        when_text = when_dt.split('T')[0] if when_dt else '—'
        buttons.append([InlineKeyboardButton(f"{title} | {when_text}", callback_data=f"event_{ev_id}")])
    buttons.append([InlineKeyboardButton("↩️ بازگشت", callback_data="back_home")])
    await update.callback_query.edit_message_text("رویدادهای پیش‌رو:", reply_markup=InlineKeyboardMarkup(buttons))

async def render_event_detail(update: Update, ev: dict):
    txt = f"*{ev['title']}*\n🕒 {ev.get('when_dt','—')}\n📍 {ev.get('place','—')}\n💶 {ev.get('price','—')}\n\n{ev.get('description','—')}"
    await update.callback_query.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📝 ثبت‌نام در همین رویداد", callback_data=f"register_{ev['id']}")],[InlineKeyboardButton("↩️ بازگشت", callback_data="back_home")]]))

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    await q.answer()
    if data == 'back_home':
        return await render_home(update, context, edit=True)
    if data == 'list_events':
        return await render_event_list(update)
    if data.startswith('event_'):
        ev_id = data.split('_', 1)[1]
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id,title,when_dt,place,price,capacity,description FROM events WHERE id=?", (ev_id,))
            row = await cur.fetchone()
        if not row:
            return await q.answer("رویداد یافت نشد.", show_alert=True)
        keys = ['id', 'title', 'when_dt', 'place', 'price', 'capacity', 'description']
        ev = dict(zip(keys, row))
        return await render_event_detail(update, ev)
    if data.startswith('register_') or data == 'register':
        if data.startswith('register_'):
            ev_id = data.split('_', 1)[1]
            context.user_data['selected_event_id'] = ev_id
            context.user_data['origin'] = 'event'
        else:
            context.user_data['origin'] = 'menu'
            return await render_event_list(update)
        return await render_rules(update, context)
    if data == 'accept_rules':
        return await render_name(update, context, edit=True)
    if data == 'feedback_start':
        context.user_data['feedback_mode'] = True
        return await q.edit_message_text('📝 لطفاً نظر یا پیشنهادت رو اینجا بنویس. پس از ارسال، پیام برای تیم admin فرستاده می‌شود.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('↩️ بازگشت', callback_data='back_home')]]))
    if data == 'leaderboard':
        rows = await get_leaderboard(10)
        lines = ['🏆 لیدربورد:']
        for i, r in enumerate(rows, start=1):
            uid, uname, fname, xp = r
            lines.append(f"{i}. {fname or uname or uid} — {xp} XP")
        return await q.edit_message_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('↩️ بازگشت', callback_data='back_home')]]))
    if data == 'my_profile':
        uid = q.from_user.id
        profile = await db_get_profile(uid)
        text = format_profile(profile)
        return await q.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('↩️ بازگشت', callback_data='back_home')]]))
    if data.startswith('approve_') or data.startswith('reject_'):
        if q.from_user.id not in ADMIN_IDS:
            return await q.answer('فقط ادمین مجاز است.', show_alert=True)
        try:
            action, user_chat_id, ev_id = data.split('_', 2)
            user_chat_id = int(user_chat_id)
            if action == 'approve':
                await db_set_roster_status(user_chat_id, ev_id, 'approved')
                await application.bot.send_message(chat_id=user_chat_id, text=f"🎉 ثبت‌نام شما در رویداد ({ev_id}) تایید شد.")
                await send_ticket_to_user(user_chat_id, ev_id)
                await add_xp(user_chat_id, 30)
            else:
                await db_set_roster_status(user_chat_id, ev_id, 'rejected')
                await application.bot.send_message(chat_id=user_chat_id, text="❌ ثبت‌نام شما رد شد.")
            try:
                base = q.message.text or ''
                stamp = '✅ تایید شد.' if action == 'approve' else '❌ رد شد.'
                await q.edit_message_text(base + '\n\n' + stamp)
            except Exception:
                pass
            await q.answer('انجام شد')
        except Exception as e:
            print('Admin callback error', e)
            await q.answer('خطا در پردازش', show_alert=True)
        return
    await q.answer()

async def db_set_roster_status(user_id, ev_id, status):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE roster SET status=? WHERE user_id=? AND event_id=? AND status IN ('pending','auto_approved')", (status, user_id, ev_id))
        if status == 'approved':
            await db.execute("UPDATE roster SET ticket_sent=0 WHERE user_id=? AND event_id=?", (user_id, ev_id))
        await db.commit()

async def db_get_profile(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id,username,full_name,xp,badges,created_at FROM users WHERE id=?", (user_id,))
        r = await cur.fetchone()
        if not r:
            return None
        uid, uname, fname, xp, badges, created_at = r
        cur2 = await db.execute("SELECT event_id, status, created_at FROM roster WHERE user_id=? ORDER BY created_at DESC LIMIT 10", (user_id,))
        hist = await cur2.fetchall()
        return {"id": uid, "username": uname, "full_name": fname, "xp": xp, "badges": json.loads(badges or '[]'), "created_at": created_at, "history": hist}

def format_profile(p):
    if not p:
        return 'پروفایلی یافت نشد.'
    lines = [f"*{p['full_name'] or p['username']}*", "\n"]
    lines.append(f"XP: {p['xp']}")
    lines.append(f"Badges: {', '.join(p['badges']) if p['badges'] else '—'}")
    lines.append(f"عضو از: {p['created_at'][:10]}" if p.get('created_at') else '')
    if p.get('history'):
        lines.append('\nتاریخچه ثبت‌نام‌ها:')
        for ev_id, status, created_at in p['history']:
            lines.append(f"- {ev_id} — {status} ({created_at[:10]})")
    return '\n'.join(lines)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or '').strip()
    user = update.effective_user
    await ensure_user_record(user)
    step = current_step(context)
    if re.fullmatch(r"شروع\s*مجدد(?:\s*🔄)?", text):
        return await render_home(update, context)
    if context.user_data.get('feedback_mode'):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO feedback (user_id, username, text, created_at) VALUES (?,?,?,?)", (user.id, user.username, text, datetime.utcnow().isoformat()))
            await db.commit()
        try:
            header = f"💬 بازخورد از {user.full_name} (@{user.username})" if user.username else f"💬 بازخورد از {user.full_name}"
            if GROUP_CHAT_ID:
                await application.bot.send_message(chat_id=GROUP_CHAT_ID, text=header)
                await application.bot.forward_message(chat_id=GROUP_CHAT_ID, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
        except Exception:
            pass
        await update.message.reply_text('ممنون! پیام شما برای تیم ارسال شد.', reply_markup=reply_main)
        context.user_data['feedback_mode'] = False
        await add_xp(user.id, 5)
        return
    if step == 'name':
        if 2 <= len(text) <= 60:
            context.user_data['name'] = text
            return await render_phone(update, context)
        else:
            return await update.message.reply_text('لطفاً نام معتبر وارد کن (2 تا 60 کاراکتر).')
    if step == 'phone':
        context.user_data['phone'] = text
        await update.message.reply_text('شماره دریافت شد ✅', reply_markup=reply_main)
        return await render_note(update, context)
    if step == 'note':
        context.user_data['note'] = text
        return await finalize_and_send(update, context)
    await render_home(update, context)

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if current_step(context) == 'phone':
        context.user_data['phone'] = update.message.contact.phone_number
        await update.message.reply_text('شماره دریافت شد ✅', reply_markup=reply_main)
        await render_note(update, context)

async def render_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    push_step(context, 'rules')
    RULES = '⚠️ قوانین:\n1) احترام\n2) انگلیسی تمرین\n3) اطلاع در صورت عدم حضور'
    if update.callback_query:
        await update.callback_query.edit_message_text(RULES, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ قبول دارم و بعدی', callback_data='accept_rules')]]))
    else:
        await update.message.reply_text(RULES, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ قبول دارم و بعدی', callback_data='accept_rules')]]))

async def render_name(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    push_step(context, 'name')
    txt = 'لطفاً نام و نام خانوادگی را وارد کن:'
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('↩️ بازگشت', callback_data='back_step')]]))
    else:
        await update.effective_chat.send_message(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('↩️ بازگشت', callback_data='back_step')]]))

async def render_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    push_step(context, 'phone')
    contact_btn = ReplyKeyboardMarkup([[KeyboardButton('ارسال شماره تماس 📱', request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
    await update.effective_chat.send_message('شماره تلفنت رو وارد کن یا دکمه زیر را بزن:', reply_markup=contact_btn)

async def render_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    push_step(context, 'note')
    await update.effective_chat.send_message('یادداشت/نیاز خاص؟ (اختیاری). اگر ندارید `-` بفرستید.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('↩️ بازگشت', callback_data='back_step')]]))

async def finalize_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data
    user = update.effective_user
    ev_id = u.get('selected_event_id')
    if not ev_id:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id FROM events ORDER BY when_dt ASC LIMIT 1")
            r = await cur.fetchone()
            ev_id = r[0] if r else None
            context.user_data['selected_event_id'] = ev_id
    if not ev_id:
        await update.effective_chat.send_message('هیچ رویدادی برای ثبت‌نام موجود نیست.', reply_markup=reply_main)
        clear_flow(context)
        return
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT INTO roster (user_id,event_id,status,phone,gender,age,note,username,created_at) VALUES (?,?,?,?,?,?,?,?,?)', (user.id, ev_id, 'pending', u.get('phone','—'), u.get('gender','—'), u.get('age',None), u.get('note','—'), user.username, now))
        await db.commit()
        run_at = (datetime.utcnow() + timedelta(seconds=AUTO_APPROVE_DELAY)).isoformat()
        await db.execute('INSERT OR REPLACE INTO pending_tasks (user_id,ev_id,run_at) VALUES (?,?,?)', (user.id, ev_id, run_at))
        await db.commit()
    await update.effective_chat.send_message('✅ درخواست ثبت‌نام شما ثبت شد و به ادمین ارسال می‌شود.', reply_markup=reply_main)
    if GROUP_CHAT_ID:
        approve_cb = f'approve_{user.id}_{ev_id}'
        reject_cb = f'reject_{user.id}_{ev_id}'
        buttons = InlineKeyboardMarkup([[InlineKeyboardButton('✅ تایید', callback_data=approve_cb), InlineKeyboardButton('❌ رد', callback_data=reject_cb)]])
        admin_txt = f"🔔 ثبت‌نام جدید: {user.full_name} (@{user.username})\nرویداد: {ev_id}\nشماره: {u.get('phone','—')}"
        msg = await application.bot.send_message(chat_id=GROUP_CHAT_ID, text=admin_txt, reply_markup=buttons)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('UPDATE roster SET admin_msg_id=? WHERE user_id=? AND event_id=? AND status="pending"', (msg.message_id, user.id, ev_id))
            await db.commit()
    clear_flow(context)

async def send_ticket_to_user(user_id: int, ev_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute('SELECT title FROM events WHERE id=?', (ev_id,))
        ev = await cur.fetchone()
        cur2 = await db.execute('SELECT full_name FROM users WHERE id=?', (user_id,))
        usr = await cur2.fetchone()
    title = ev[0] if ev else ev_id
    name = usr[0] if usr else f'User {user_id}'
    bio = make_ticket_image(name, title, ev_id)
    bio.seek(0)
    try:
        await application.bot.send_photo(chat_id=user_id, photo=InputFile(bio, filename='ticket.jpg'), caption=f"🎫 بلیت شما برای {title}")
    except Exception as e:
        print('Failed to send ticket:', e)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE roster SET ticket_sent = ticket_sent | 1 WHERE user_id=? AND event_id=?', (user_id, ev_id))
        await db.commit()

if not BOT_TOKEN:
    raise RuntimeError('BOT_TOKEN not set')

application = ApplicationBuilder().token(BOT_TOKEN).job_queue(None).build()
application.add_handler(CommandHandler('start', render_home))
application.add_handler(CallbackQueryHandler(handle_callback))
application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await application.initialize()
    if WEBHOOK_URL:
        try:
            await application.bot.set_webhook(url=WEBHOOK_URL)
        except Exception:
            pass
    await application.start()
    bg = asyncio.create_task(background_worker(application))
    yield
    bg.cancel()
    try:
        await bg
    except Exception:
        pass
    await application.stop()
    await application.shutdown()

app = FastAPI(lifespan=lifespan)

@app.post('/')
async def webhook(request: Request):
    body = await request.json()
    update = Update.de_json(body, application.bot)
    await application.process_update(update)
    return {'status': 'ok'}

@app.get('/')
async def root():
    return {'status': 'ChillChat Full Extended Running'}
