# CBot.py — compact & optimized (no Google Sheets)
# python-telegram-bot==20.3, fastapi, uvicorn

import os, json, re, asyncio
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton as B, InlineKeyboardMarkup as MK, ReplyKeyboardMarkup, KeyboardButton, Chat
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

# ========== CONFIG ==========
BOT_TOKEN=os.environ.get("BOT_TOKEN"); WEBHOOK_URL=os.environ.get("WEBHOOK_URL")
GROUP_CHAT_ID=int(os.environ.get("GROUP_CHAT_ID","0"))
DATACENTER_CHAT_ID=int(os.environ.get("DATACENTER_CHAT_ID",str(GROUP_CHAT_ID or 0)))
SUPPORT_USERNAME=os.environ.get("SUPPORT_USERNAME","ifyoulostme")
CHANNEL_URL=os.environ.get("CHANNEL_URL",""); GROUP_URL=os.environ.get("GROUP_URL",""); INSTAGRAM_URL=os.environ.get("INSTAGRAM_URL","")
CAFE_INTRO_USERNAME=(os.environ.get("CAFE_INTRO_USERNAME") or "ifyoulostme").lstrip("@")
OWNER_USERNAME=(os.environ.get("OWNER_USERNAME") or "").strip().lstrip("@")
ADMIN_USERNAMES=[u.strip().lstrip("@") for u in (os.environ.get("ADMIN_USERNAMES") or "").split(",") if u.strip()]
AUTO_APPROVE_DELAY=12*60*60
SHOW_JSON_IN_PINNED=os.environ.get("SHOW_JSON_IN_PINNED","0")=="1"
MALE_LIMIT_PER_EVENT=int(os.environ.get("MALE_LIMIT_PER_EVENT","5"))

DEFAULT_EVENTS=[{"id":"intro01","title":"2nd Meeting!","when":"چهارشنبه 30 مهر- ۱۸:۰۰","price":"سفارش از کافه","capacity":12,
"desc":"The legendary 2nd session. It's all gonna be about chill & chat. btw topic will be decided in the group. stay tuned!"}]
try:
    EVENTS=json.loads(os.environ.get("EVENTS_JSON","") or "[]") or DEFAULT_EVENTS
    if not isinstance(EVENTS,list): EVENTS=DEFAULT_EVENTS
except: EVENTS=DEFAULT_EVENTS
try: MEETUP_LINKS=json.loads(os.environ.get("MEETUP_LINKS_JSON","{}"))
except: MEETUP_LINKS={}

# ========== STATE ==========
PENDING={}          # user_chat_id -> info
ROSTER={}           # event_id -> list of dict
ALL_USERS={}        # chat_id -> {username,name}
ROSTER_MESSAGE_ID=None

# ========== TEXTS & UI ==========
reply_main=ReplyKeyboardMarkup([["شروع مجدد 🔄"]],resize_keyboard=True)
WELCOME="سلام! به *ChillChat Community* خوش اومدی ☕🇬🇧\nاینجا می‌تونی رویدادهای زبان انگلیسی رو ببینی و ثبت‌نام کنی."
FAQ=("❔ **سوالات متداول درباره ChillChat**\n\n"
"🗣️ به انگلیسی حرف می‌زنیم، بازی می‌کنیم، موضوع‌های روز رو تمرین می‌کنیم.\n"
"☕ جلسات در کافه برگزار می‌شه.\n"
"💶 معمولاً رایگان؛ فقط یک سفارش از کافه.\n"
"📸 گاهی با رضایت.\n"
"📝 ثبت‌نامت میره برای تایید ادمین.")
RULES=("⚠️ **قوانین ChillChat**\n\n"
"💬 احترام؛ 🗣️ تا حد ممکن انگلیسی؛ ⏰ وقت‌شناسی؛ 📱 بی‌صدا؛ 🙏 اگر نمیای زود خبر بده.")

def SOCIAL_TEXT():
    parts=["🌐 **شبکه‌های اجتماعی:**\n"]
    if CHANNEL_URL: parts.append(f"📢 کانال: {CHANNEL_URL}\n")
    if GROUP_URL: parts.append(f"💬 گروه: {GROUP_URL}\n")
    if INSTAGRAM_URL: parts.append(f"📸 اینستاگرام: {INSTAGRAM_URL}\n")
    if len(parts)==1: parts.append("بزودی لینک‌ها تکمیل می‌شن.")
    return "".join(parts)

CAFE_INTRO_TEXT=f"🏠 **معرفی کافه به ChillChat**\nاسم و آدرس کافه مورد نظرت رو برای *@{CAFE_INTRO_USERNAME}* بفرست 🙌"
CAPACITY_CANCEL_MSG="❌ ثبت‌نام شما به دلیل *تکمیل ظرفیت* لغو شد."
CAPACITY_FULL_PREVENT_MSG="❌ ظرفیت این رویداد تکمیل است. لطفاً رویداد دیگری را انتخاب کن."
MALE_CAPACITY_FULL_MSG="❌ سقف ظرفیت شرکت‌کنندگان برای این رویداد تکمیل شده است."

def is_admin_user(user)->bool:
    u=(user.username or "").lower()
    allow=set([OWNER_USERNAME.lower()] if OWNER_USERNAME else [])|{a.lower() for a in ADMIN_USERNAMES}
    return bool(u and u in allow)

def kb_main():
    return MK([
        [B("🎉 رویدادهای پیش‌رو",callback_data="list_events")],
        [B("📝 ثبت‌نام سریع",callback_data="register")],
        [B("🏠 معرفی کافه به ChillChat",callback_data="cafe_intro")],
        [B("🌐 شبکه‌های اجتماعی",callback_data="socials")],
        [B("❔ سوالات متداول",callback_data="faq")],
        [B("🆘 پشتیبانی",callback_data="support")],
        [B("💬 ارسال نظر و پیشنهاد",callback_data="feedback_start")],
    ])
def kb_back(): return MK([[B("↩️ بازگشت به مرحله قبل",callback_data="back_step")]])
def kb_rules(): return MK([[B("✅ قبول دارم و بعدی",callback_data="accept_rules")],[B("↩️ بازگشت به مرحله قبل",callback_data="back_step")]])
def kb_level(): return MK([[B("Beginner (A1–A2)",callback_data="lvl_A")],[B("Intermediate (B1–B2)",callback_data="lvl_B")],[B("Advanced (C1+)",callback_data="lvl_C")],[B("↩️ بازگشت به مرحله قبل",callback_data="back_step")]])
def kb_gender(): return MK([[B("👨 مرد",callback_data="gender_m"),B("👩 زن",callback_data="gender_f")],[B("↩️ بازگشت به مرحله قبل",callback_data="back_step")]])
def kb_age(): return MK([[B("➖ ترجیح می‌دهم نگویم",callback_data="age_na")],[B("↩️ بازگشت به مرحله قبل",callback_data="back_step")]])
def kb_event_register(eid): return MK([[B("📝 ثبت‌نام در همین رویداد",callback_data=f"register_{eid}")],[B("↩️ بازگشت",callback_data="back_home")]])

# ========== HELPERS ==========
def add_user(user,chat_id:int):
    if not chat_id: return
    ALL_USERS.setdefault(chat_id,{"username":None,"name":None})
    ALL_USERS[chat_id]["username"]=user.username if user else None
    ALL_USERS[chat_id]["name"]=user.full_name if user else None

def get_event(eid): return next((e for e in EVENTS if e.get("id")==eid),None)
def approved_count(eid): return len(ROSTER.get(eid,[]))
def remaining_capacity(ev): 
    c=int(ev.get("capacity") or 0); return max(0,c-approved_count(ev["id"])) if c else 10**9
def male_count(eid): return sum(1 for r in ROSTER.get(eid,[]) if r.get("gender")=="male")
def event_text_user(ev):
    t=[f"**{ev.get('title','')}**",f"🕒 {ev.get('when','')}",f"📍 {ev.get('place','—')}",f"💶 {ev.get('price','') or 'Free'}"]
    if ev.get("desc"): t.append(f"📝 {ev['desc']}")
    t.append("\n(آدرس دقیق کافه پیش از رویداد اعلام می‌شود.)"); return "\n".join(t)
def event_text_admin(ev):
    cap=f"👥 ظرفیت: {approved_count(ev['id'])}/{ev.get('capacity')}\n" if ev.get("capacity") else ""
    return f"📌 {ev.get('title','')}\n🕒 {ev.get('when','')}\n{cap}📍 {ev.get('place','—')}\n🗺️ {ev.get('maps','—')}\n💶 {ev.get('price','Free')}\n📝 {ev.get('desc','—')}"

def nav_push(ctx,step): ctx.user_data.setdefault("nav",[]).append(step)
def nav_pop(ctx):
    nv=ctx.user_data.get("nav",[]); 
    if nv: nv.pop(); ctx.user_data["nav"]=nv
    return nv[-1] if nv else None
def nav_cur(ctx): 
    nv=ctx.user_data.get("nav",[]); 
    return nv[-1] if nv else None
def clear_flow(ctx):
    for k in ["nav","origin","selected_event_id","name","phone","level","gender","age","note","feedback_mode"]: ctx.user_data.pop(k,None)

async def send_or_edit(update,text,markup=None,parse=False,edit=False):
    if edit and update.callback_query: 
        await update.callback_query.edit_message_text(text,reply_markup=markup,parse_mode=("Markdown" if parse else None))
    else:
        chat=update.effective_chat
        await chat.send_message(text,reply_markup=markup,parse_mode=("Markdown" if parse else None))

# ========== PINNED JSON ==========
def _extract_json(text):
    if not text: return None
    m=re.search(r"```json\s*(\{.*?\})\s*```",text,flags=re.DOTALL)
    if not m: return None
    try: return json.loads(m.group(1))
    except: return None

async def restore_from_pinned(app):
    global ROSTER_MESSAGE_ID,ROSTER,ALL_USERS
    if not DATACENTER_CHAT_ID: return
    try: chat:Chat=await app.bot.get_chat(DATACENTER_CHAT_ID)
    except Exception as e: 
        print("restore get_chat:",e); return
    pm=getattr(chat,"pinned_message",None)
    if not pm: return
    data=_extract_json(getattr(pm,"text",None) or getattr(pm,"caption",None))
    if data:
        if isinstance(data.get("roster"),dict): ROSTER=data["roster"]
        au=data.get("all_users") or {}
        ALL_USERS={}
        for k,v in au.items():
            try: cid=int(k)
            except: 
                try: cid=int(v.get("chat_id"))
                except: continue
            ALL_USERS[cid]={"username":v.get("username"),"name":v.get("name")}
    ROSTER_MESSAGE_ID=pm.message_id

def _human_roster():
    if not ROSTER: return "📋 لیست تاییدشده‌ها (DataCenter)\n— هنوز کسی تایید نشده."
    L=["📋 لیست تاییدشده‌ها (DataCenter)"]
    for e in EVENTS:
        eid=e["id"]; ppl=ROSTER.get(eid,[])
        L.append(f"\n🗓 {e['title']} — {e['when']} | تاییدشده‌ها: {len(ppl)} (آقایان: {male_count(eid)})")
        if not ppl: L.append("  — هنوز تاییدی نداریم")
        else:
            for i,r in enumerate(ppl,1):
                uname=f"@{r['username']}" if r.get("username") else "—"
                L.append(f"  {i}. {r['name']} | {uname} | {r.get('phone','—')}")
    return "\n".join(L)

async def save_pinned(app):
    global ROSTER_MESSAGE_ID
    if not DATACENTER_CHAT_ID: return
    human=_human_roster()
    if SHOW_JSON_IN_PINNED:
        human+="\n\n---\n```json\n"+json.dumps({
            "events":[{"id":e["id"],"capacity":e.get("capacity"),"title":e["title"],"when":e["when"]} for e in EVENTS],
            "roster":ROSTER,
            "all_users":{str(cid):ALL_USERS[cid] for cid in ALL_USERS}
        },ensure_ascii=False)+"\n```"
    try:
        if ROSTER_MESSAGE_ID:
            await app.bot.edit_message_text(chat_id=DATACENTER_CHAT_ID,message_id=ROSTER_MESSAGE_ID,text=human); return
    except Exception as e: print("edit pinned failed:",e)
    try:
        m=await app.bot.send_message(chat_id=DATACENTER_CHAT_ID,text=human); ROSTER_MESSAGE_ID=m.message_id
        try: await app.bot.pin_chat_message(chat_id=DATACENTER_CHAT_ID,message_id=ROSTER_MESSAGE_ID,disable_notification=True)
        except Exception as e: print("pin failed:",e)
    except Exception as e: print("send pinned failed:",e)

# ========== RENDERERS ==========
async def render_home(update,context,edit=False):
    add_user(update.effective_user,update.effective_chat.id)
    if SHOW_JSON_IN_PINNED: await save_pinned(context.application)
    clear_flow(context); await send_or_edit(update,"یکی از گزینه‌ها رو انتخاب کن:",kb_main(),edit=edit)

async def render_event_list(update):
    rows=[[B(f"{e['title']} | {e['when']}",callback_data=f"event_{e['id']}")] for e in EVENTS]
    rows.append([B("↩️ بازگشت",callback_data="back_home")])
    await update.callback_query.edit_message_text("رویدادهای پیش‌رو:",reply_markup=MK(rows))

async def render_event_detail(update,ev):
    await update.callback_query.edit_message_text(event_text_user(ev),parse_mode="Markdown",reply_markup=kb_event_register(ev["id"]))

async def render_rules(update,context):
    nav_push(context,"rules"); await send_or_edit(update,RULES,kb_rules(),edit=bool(update.callback_query))

async def render_name(update,context,edit=False):
    nav_push(context,"name"); await send_or_edit(update,"لطفاً *نام و نام خانوادگی* رو وارد کن:",kb_back(),parse=True,edit=edit)

async def render_gender(update,context,edit=False):
    nav_push(context,"gender"); await send_or_edit(update,"جنسیتت رو انتخاب کن:",kb_gender(),edit=edit)

async def render_age(update,context,edit=False):
    nav_push(context,"age"); await send_or_edit(update,"سن‌ت رو به *عدد* بفرست (مثلاً 24). یا دکمهٔ «ترجیح می‌دهم نگویم» را بزن.",kb_age(),parse=True,edit=edit)

async def render_level(update,context,edit=False):
    nav_push(context,"level"); await send_or_edit(update,"سطح زبانت چیه؟ یکی رو انتخاب کن:",kb_level(),edit=edit)

async def render_phone(update,context):
    nav_push(context,"phone")
    contact_btn=ReplyKeyboardMarkup([[KeyboardButton("ارسال شماره تماس 📱",request_contact=True)]],resize_keyboard=True,one_time_keyboard=True)
    await update.effective_chat.send_message("شماره تلفنت رو وارد کن یا دکمه زیر رو بزن:",reply_markup=contact_btn)
    await update.effective_chat.send_message("یا می‌تونی به مرحله قبل برگردی:",reply_markup=kb_back())

async def render_note(update,context,edit=False):
    nav_push(context,"note"); await send_or_edit(update,"یادداشت/نیاز خاص داری؟ (اختیاری) اگر چیزی نداری، فقط یک خط تیره `-` بفرست.",kb_back(),parse=True,edit=edit)

async def go_back(update,context):
    prev=nav_pop(context); origin=context.user_data.get("origin"); ev=get_event(context.user_data.get("selected_event_id"))
    if not prev:
        if origin=="event" and ev and update.callback_query: return await render_event_detail(update,ev)
        return await render_home(update,context,edit=True)
    return await {"rules":render_rules,"name":lambda u,c:render_name(u,c,True),"gender":lambda u,c:render_gender(u,c,True),
                  "age":lambda u,c:render_age(u,c,True),"level":lambda u,c:render_level(u,c,True),
                  "phone":render_phone,"note":lambda u,c:render_note(u,c,True)}.get(prev,lambda u,c:render_home(u,c,True))(update,context)

# ========== AUTO-APPROVE ==========
async def delayed_auto_approve(app,user_chat_id:int,ev_id:str,delay:int=AUTO_APPROVE_DELAY):
    try: await asyncio.sleep(delay)
    except asyncio.CancelledError: return
    info=PENDING.get(user_chat_id); ev=get_event(ev_id)
    if not info or not ev: PENDING.pop(user_chat_id,None); return
    if ev.get("capacity") and remaining_capacity(ev)<=0:
        try: await app.bot.send_message(chat_id=user_chat_id,text=CAPACITY_CANCEL_MSG)
        except: pass
        try:
            if info.get("admin_msg_id"): await app.bot.delete_message(chat_id=GROUP_CHAT_ID,message_id=info["admin_msg_id"])
        except: pass
        PENDING.pop(user_chat_id,None); return
    if info.get("gender")=="male" and male_count(ev_id)>=MALE_LIMIT_PER_EVENT:
        try: await app.bot.send_message(chat_id=user_chat_id,text=MALE_CAPACITY_FULL_MSG)
        except: pass
        try:
            if info.get("admin_msg_id"): await app.bot.delete_message(chat_id=GROUP_CHAT_ID,message_id=info["admin_msg_id"])
        except: pass
        PENDING.pop(user_chat_id,None); return
    ROSTER.setdefault(ev_id,[]).append({"chat_id":user_chat_id,"name":info.get("name","—"),"username":info.get("username"),
        "phone":info.get("phone","—"),"gender":info.get("gender"),"age":info.get("age"),"when":info.get("when","—"),
        "event_title":info.get("event_title","—")})
    await save_pinned(app)
    detail=("🎉 ثبت‌نامت تایید شد!\n\n"+event_text_user(ev)+"\n(Auto-approved by bot)")
    try: await app.bot.send_message(chat_id=user_chat_id,text=detail,parse_mode="Markdown")
    except: pass
    try:
        if info.get("admin_msg_id"): await app.bot.delete_message(chat_id=GROUP_CHAT_ID,message_id=info["admin_msg_id"])
    except: pass
    PENDING.pop(user_chat_id,None)

# ========== FINALIZE ==========
async def finalize_and_send(update,context:ContextTypes.DEFAULT_TYPE):
    u=context.user_data; ev_id=u.get("selected_event_id") or (EVENTS[0]["id"] if EVENTS else None); ev=get_event(ev_id)
    if ev and ev.get("capacity") and remaining_capacity(ev)<=0:
        await update.effective_chat.send_message(CAPACITY_CANCEL_MSG,reply_markup=reply_main); clear_flow(context); return
    if u.get("gender")=="male" and ev_id and male_count(ev_id)>=MALE_LIMIT_PER_EVENT:
        await update.effective_chat.send_message(MALE_CAPACITY_FULL_MSG,reply_markup=reply_main)
        if CHANNEL_URL: await update.effective_chat.send_message(f"📢 برای اینکه از اخبار جا نمونی، عضو کانال شو:\n{CHANNEL_URL}")
        clear_flow(context); return
    summary=("✅ درخواست ثبت‌نامت ثبت شد و برای ادمین ارسال می‌شود.\n\n"
             f"👤 نام: {u.get('name','—')}\n⚧ جنسیت: {({'male':'مرد','female':'زن'}).get(u.get('gender'),'—')}\n"
             f"🎂 سن: {u.get('age','—') if u.get('age') is not None else '—'}\n🗣️ سطح: {u.get('level','—')}\n"
             f"📱 تماس: {u.get('phone','—')}\n📝 توضیحات: {u.get('note','—')}\n")
    if ev: summary+=f"\n📌 رویداد: {ev.get('title','')}\n🕒 زمان: {ev.get('when','')}\n(آدرس پس از تایید ارسال می‌شود.)"
    await update.effective_chat.send_message(summary,reply_markup=reply_main)
    if CHANNEL_URL: await update.effective_chat.send_message(f"📢 برای اینکه از اخبار جا نمونی، عضو کانال شو:\n{CHANNEL_URL}")
    if GROUP_CHAT_ID:
        uid=update.effective_chat.id; approve=f"approve_{uid}_{ev_id or 'NA'}"; reject=f"reject_{uid}_{ev_id or 'NA'}"
        admin_txt=("🔔 ثبت‌نام جدید ChillChat\n\n"
                   f"👤 نام: {u.get('name','—')}\n⚧ جنسیت: {({'male':'مرد','female':'زن'}).get(u.get('gender'),'—')}\n"
                   f"🎂 سن: {u.get('age','—') if u.get('age') is not None else '—'}\n🗣️ سطح: {u.get('level','—')}\n"
                   f"📱 تماس: {u.get('phone','—')}\n📝 توضیحات: {u.get('note','—')}\n\n")
        if ev: admin_txt+=event_text_admin(ev)
        msg=await context.bot.send_message(chat_id=GROUP_CHAT_ID,text=admin_txt,reply_markup=MK([[B("✅ تایید",callback_data=approve),B("❌ رد",callback_data=reject)]]))
        task=context.application.create_task(delayed_auto_approve(context.application,uid,ev_id,delay=AUTO_APPROVE_DELAY))
        PENDING[uid]={"name":u.get('name','—'),"phone":u.get('phone','—'),"level":u.get('level','—'),"note":u.get('note','—'),
                      "gender":u.get('gender'),"age":u.get('age'),"event_id":ev_id,"event_title":ev.get('title') if ev else "—",
                      "when":ev.get('when') if ev else "—","username":update.effective_user.username if update.effective_user else None,
                      "admin_msg_id":msg.message_id,"task":task}
    clear_flow(context)

# ========== HANDLERS ==========
async def cmd_start(update,context):
    add_user(update.effective_user,update.effective_chat.id)
    if SHOW_JSON_IN_PINNED: await save_pinned(context.application)
    await update.effective_chat.send_message(WELCOME,parse_mode="Markdown",reply_markup=reply_main)
    await render_home(update,context)

async def cmd_testpin(update,context):
    try: await save_pinned(context.application); await update.message.reply_text("✅ لیست/پایگاه در دیتاسنتر پین شد/آپدیت شد.")
    except Exception as e: await update.message.reply_text(f"⚠️ خطا: {e}")

async def cmd_roster(update,context):
    try:
        await update.message.reply_text("📋 وضعیت فعلی:\n\n"+_human_roster()[:3800])
        await update.message.reply_text(f"👥 کل کاربران استارت کرده: {len(ALL_USERS)}")
    except Exception as e: await update.message.reply_text(f"⚠️ خطا: {e}")

def _split_once(text:str):
    p=(text or "").strip().split(maxsplit=2); arg1=p[1] if len(p)>1 else ""; rest=p[2] if len(p)>2 else ""; return arg1,rest

async def cmd_dm(update,context):
    if not (is_admin_user(update.effective_user) and update.effective_chat.id==DATACENTER_CHAT_ID): return
    arg1,rest=_split_once(update.message.text); target=arg1.lstrip("@"); msg=rest.strip()
    if not target or not msg: return await update.message.reply_text("فرمت: `/dm @username پیام شما`",parse_mode="Markdown")
    chat_id=None
    for ppl in ROSTER.values():
        for r in ppl:
            if (r.get("username") or "").lower()==target.lower(): chat_id=r.get("chat_id"); break
        if chat_id: break
    if not chat_id:
        for cid,info in ALL_USERS.items():
            if (info.get("username") or "").lower()==target.lower(): chat_id=cid; break
    if not chat_id: return await update.message.reply_text("❌ کاربر پیدا نشد یا chat_id در دسترس نیست.")
    try: await context.bot.send_message(chat_id=chat_id,text=msg); await update.message.reply_text("پیام ارسال شد ✅")
    except Exception as e: await update.message.reply_text(f"ارسال ناموفق ❌: {e}")

async def cmd_dmevent(update,context):
    if not (is_admin_user(update.effective_user) and update.effective_chat.id==DATACENTER_CHAT_ID): return
    ev_id,extra=_split_once(update.message.text); ev=get_event(ev_id.strip())
    if not ev: return await update.message.reply_text("❌ event_id نامعتبره. فرمت: `/dmevent <event_id> [متن اختیاری]`",parse_mode="Markdown")
    base=event_text_user(ev); msg=(extra+"\n\n"+base) if extra else base
    s=f=0
    for r in ROSTER.get(ev["id"],[]):
        cid=r.get("chat_id")
        if not cid: f+=1; continue
        try: await context.bot.send_message(chat_id=cid,text=msg,parse_mode="Markdown"); s+=1
        except: f+=1
    await update.message.reply_text(f"ارسال شد ✅ {s} | ناموفق ❌ {f}")

async def cmd_dmall(update,context):
    if not (is_admin_user(update.effective_user) and update.effective_chat.id==DATACENTER_CHAT_ID): return
    _,msg=_split_once(update.message.text); msg=msg.strip()
    if not msg: return await update.message.reply_text("فرمت: `/dmall پیام شما`",parse_mode="Markdown")
    s=f=0
    for cid in list(ALL_USERS.keys()):
        try: await context.bot.send_message(chat_id=cid,text=msg); s+=1
        except: f+=1
    await update.message.reply_text(f"ارسال شد ✅ {s} | ناموفق ❌ {f}")

async def shortcut_restart(update,context): add_user(update.effective_user,update.effective_chat.id); 
async def _shortcut_restart(update,context):
    add_user(update.effective_user,update.effective_chat.id)
    if SHOW_JSON_IN_PINNED: await save_pinned(context.application)
    await render_home(update,context)

async def handle_callback(update,context):
    add_user(update.effective_user,update.effective_chat.id)
    if SHOW_JSON_IN_PINNED: await save_pinned(context.application)
    q=update.callback_query; d=q.data
    if d.startswith("lvl_"): return await handle_level(update,context)
    if d.startswith("gender_"): return await handle_gender(update,context)
    if d=="age_na": context.user_data["age"]=None; await q.answer(); return await render_level(update,context,edit=True)
    await q.answer()
    if d=="back_home": return await render_home(update,context,edit=True)
    if d=="back_step": return await go_back(update,context)
    if d=="faq": return await q.edit_message_text(FAQ,parse_mode="Markdown",reply_markup=MK([[B("↩️ بازگشت",callback_data="back_home")]]))
    if d=="support": return await q.edit_message_text(f"🆘 برای پشتیبانی: @{SUPPORT_USERNAME}",reply_markup=MK([[B("↩️ بازگشت",callback_data="back_home")]]))
    if d=="cafe_intro": return await q.edit_message_text(CAFE_INTRO_TEXT,parse_mode="Markdown",reply_markup=MK([[B("↩️ بازگشت",callback_data="back_home")]]))
    if d=="socials": return await q.edit_message_text(SOCIAL_TEXT(),reply_markup=MK([[B("↩️ بازگشت",callback_data="back_home")]]))
    if d=="feedback_start":
        context.user_data["feedback_mode"]=True
        return await q.edit_message_text("📝 نظرت یا پیشنهادت رو بنویس و بفرست. مستقیم به تیم ارسال می‌شه 💌",reply_markup=MK([[B("↩️ بازگشت",callback_data="back_home")]]))
    if d=="list_events": return await render_event_list(update)
    if d.startswith("event_"):
        ev=get_event(d.split("_",1)[1]); 
        if not ev: return await q.answer("این رویداد یافت نشد.",show_alert=True)
        return await render_event_detail(update,ev)
    if d=="register" or d.startswith("register_"):
        target=None
        if d.startswith("register_"):
            eid=d.split("_",1)[1]; target=get_event(eid); context.user_data["selected_event_id"]=eid; context.user_data["origin"]="event"
        else:
            context.user_data["origin"]="menu"
            if not context.user_data.get("selected_event_id"): return await render_event_list(update)
        if not target and context.user_data.get("selected_event_id"): target=get_event(context.user_data["selected_event_id"])
        if target and target.get("capacity") and remaining_capacity(target)<=0:
            return await q.edit_message_text(CAPACITY_FULL_PREVENT_MSG,reply_markup=MK([[B("↩️ بازگشت",callback_data="back_home")]]))
        return await render_rules(update,context)
    if d=="accept_rules": return await render_name(update,context,edit=True)

    if d.startswith("approve_") or d.startswith("reject_"):
        try:
            action,uid,ev_id=d.split("_",2); uid=int(uid); ev=get_event(ev_id); approver=q.from_user
            if action=="approve" and ev and ev.get("capacity") and remaining_capacity(ev)<=0:
                await q.answer("ظرفیت تکمیل است.",show_alert=True); 
                try: await q.edit_message_text((q.message.text or "")+"\n\n⚠️ تلاش برای تایید، اما ظرفیت تکمیل است.")
                except: pass
                return
            info_preview=PENDING.get(uid,{})
            if action=="approve" and info_preview.get("gender")=="male" and male_count(ev_id)>=MALE_LIMIT_PER_EVENT:
                await q.answer("سقف آقایان تکمیل است.",show_alert=True)
                try: await q.edit_message_text((q.message.text or "")+"\n\n⚠️ تلاش برای تایید آقا، اما سقف آقایان تکمیل است.")
                except: pass
                try: await context.bot.send_message(chat_id=uid,text=MALE_CAPACITY_FULL_MSG)
                except: pass
                return
            if action=="approve":
                detail=("🎉 ثبت‌نامت تایید شد!\n\n"+(event_text_user(ev) if ev else "")); 
                link=MEETUP_LINKS.get(ev_id); 
                if link: detail+=f"\n🔗 لینک هماهنگی:\n{link}"
                await context.bot.send_message(chat_id=uid,text=detail,parse_mode="Markdown")
            else:
                await context.bot.send_message(chat_id=uid,text=CAPACITY_CANCEL_MSG)
            base=q.message.text or ""; stamp="✅ توسط {0} تایید شد.".format(approver.full_name) if action=="approve" else "❌ توسط {0} رد شد.".format(approver.full_name)
            try: await q.edit_message_text(base+"\n\n"+stamp)
            except: 
                try: await q.edit_message_reply_markup(reply_markup=None)
                except: pass
            info=PENDING.get(uid)
            if info and info.get("task"):
                try: info["task"].cancel()
                except: pass
            if action=="approve":
                info=PENDING.pop(uid,None)
                if info:
                    if ev and ev.get("capacity") and remaining_capacity(ev)<=0:
                        await context.bot.send_message(chat_id=GROUP_CHAT_ID,text="⚠️ ظرفیت پر شد؛ تایید نهایی انجام نشد.")
                    else:
                        if info.get("gender")=="male" and male_count(ev_id)>=MALE_LIMIT_PER_EVENT:
                            try: await context.bot.send_message(chat_id=uid,text=MALE_CAPACITY_FULL_MSG)
                            except: pass
                        else:
                            ROSTER.setdefault(ev_id,[]).append({"chat_id":uid,"name":info.get("name","—"),"username":info.get("username"),
                                "phone":info.get("phone","—"),"gender":info.get("gender"),"age":info.get("age"),
                                "when":info.get("when","—"),"event_title":info.get("event_title","—")})
                            await save_pinned(context.application)
            await q.answer("انجام شد.")
        except Exception as e:
            print("Admin callback error:",e); await q.answer("مشکلی پیش اومد.",show_alert=True)
        return

async def handle_message(update,context):
    add_user(update.effective_user,update.effective_chat.id)
    if SHOW_JSON_IN_PINNED: await save_pinned(context.application)
    text=(update.message.text or "").strip(); step=nav_cur(context)
    if re.fullmatch(r"شروع\s*مجدد(?:\s*🔄)?",text): return await render_home(update,context)
    if context.user_data.get("feedback_mode"):
        try:
            if GROUP_CHAT_ID:
                u=update.effective_user; header=f"💬 پیام جدید از کاربر:\n👤 {u.full_name}\n"+(f"🆔 @{u.username}\n" if u.username else "🆔 —\n")
                await context.bot.send_message(chat_id=GROUP_CHAT_ID,text=header)
                try: await context.bot.forward_message(chat_id=GROUP_CHAT_ID,from_chat_id=update.effective_chat.id,message_id=update.message.message_id)
                except: await context.bot.copy_message(chat_id=GROUP_CHAT_ID,from_chat_id=update.effective_chat.id,message_id=update.message.message_id)
            await update.message.reply_text("ممنون از بازخوردت 💛",reply_markup=reply_main)
        finally: context.user_data["feedback_mode"]=False
        return
    if step=="name":
        if 2<=len(text)<=60: context.user_data["name"]=text; return await render_gender(update,context)
        else: return await update.message.reply_text("لطفاً نام معتبر وارد کن (۲ تا ۶۰ کاراکتر).")
    if step == "age":
    if text in ["-", "—"]:
        context.user_data["age"] = None
    else:
        if not re.fullmatch(r"\d{1,3}", text):
            return await update.message.reply_text("سن را به عدد وارد کن (مثلاً 23) یا «ترجیح می‌دهم نگویم».")
        a = int(text)
        if not (1 <= a <= 120):
            return await update.message.reply_text("سن نامعتبر است (1..120).")
        context.user_data["age"] = a
    return await render_level(update, context)

    if step=="level": return await render_level(update,context)
    if step=="phone":
        context.user_data["phone"]=text; await update.message.reply_text("شماره دریافت شد ✅",reply_markup=reply_main); return await render_note(update,context)
    if step=="note": context.user_data["note"]=text; return await finalize_and_send(update,context)
    return await render_home(update,context)

async def handle_contact(update,context):
    add_user(update.effective_user,update.effective_chat.id)
    if SHOW_JSON_IN_PINNED: await save_pinned(context.application)
    if nav_cur(context)=="phone":
        context.user_data["phone"]=update.message.contact.phone_number
        await update.message.reply_text("شماره دریافت شد ✅",reply_markup=reply_main); await render_note(update,context)

async def handle_level(update,context):
    await update.callback_query.answer()
    d=update.callback_query.data; context.user_data["level"]={"lvl_A":"Beginner (A1–A2)","lvl_B":"Intermediate (B1–B2)","lvl_C":"Advanced (C1+)"}[d]
    await render_phone(update,context)

async def handle_gender(update,context):
    await update.callback_query.answer()
    g={"gender_m":"male","gender_f":"female"}[update.callback_query.data]; context.user_data["gender"]=g
    await render_age(update,context,edit=True)

# ========== APP ==========
if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN is not set")
application=ApplicationBuilder().token(BOT_TOKEN).job_queue(None).build()
application.add_handler(CommandHandler("start",cmd_start))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^شروع\s*مجدد(?:\s*🔄)?$"),_shortcut_restart))
application.add_handler(CommandHandler("dm",cmd_dm)); application.add_handler(CommandHandler("dmevent",cmd_dmevent)); application.add_handler(CommandHandler("dmall",cmd_dmall))
application.add_handler(CallbackQueryHandler(handle_level,pattern=r"^lvl_")); application.add_handler(CallbackQueryHandler(handle_callback))
application.add_handler(MessageHandler(filters.CONTACT,handle_contact))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,handle_message))
application.add_handler(CommandHandler("testpin",cmd_testpin)); application.add_handler(CommandHandler("roster",cmd_roster))

@asynccontextmanager
async def lifespan(app:FastAPI):
    await application.initialize()
    if WEBHOOK_URL: await application.bot.set_webhook(url=WEBHOOK_URL)
    await application.start(); await restore_from_pinned(application)
    yield
    await application.stop(); await application.shutdown()

app=FastAPI(lifespan=lifespan)

@app.post("/")
async def webhook(request:Request):
    update=Update.de_json(await request.json(),application.bot)
    await application.process_update(update); return {"status":"ok"}

@app.get("/")
async def root():
    return {"status":"ChillChat bot is running (compact, pinned restore, roster & ALL_USERS, admin DMs)."}

