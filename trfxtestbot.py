
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Azadi VIP Signal Bot — نسخه نهایی و کامل
════════════════════════════════════════════════════════════════════
سیستم حرفه‌ای فروش سیگنال VIP با پرداخت دوگانه (AZC و پول واقعی)
موتور فوروارد آنی سیگنال + سیستم اخراج خودکار پس از اتمام اشتراک
"""
import os
import sys
import time
import json
import signal
import atexit
import logging
import hashlib
import asyncio
import threading
import random
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests
from flask import Flask, request

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, filters, MessageHandler,
)

# ══════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO, stream=sys.stdout,
)
log = logging.getLogger("AzadiSignalBot")

# ══════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8972429048:AAE57wdflfIRxyiWLjBMol10WZYK_IqfSds")
PORT = int(os.environ.get("PORT", 5000))
_admin_raw = os.environ.get("ADMIN_IDS", os.environ.get("ADMIN_ID", "605611554"))
ADMIN_IDS = {int(x) for x in _admin_raw.replace(" ", "").split(",") if x.strip()}

# کانال عمومی (برای Force Join)
FORCE_JOIN_CHANNEL = os.environ.get("FORCE_JOIN_CHANNEL", "")

# کانال VIP (جایی که سیگنال‌ها ارسال می‌شوند)
VIP_CHANNEL_ID = os.environ.get("VIP_CHANNEL_ID", "@testerrrrrfg")
VIP_CHANNEL_LINK = os.environ.get("VIP_CHANNEL_LINK", "@testerrrrrfg")

# تنظیمات کوین AZC
REFERRAL_BONUS_AZC = int(os.environ.get("REFERRAL_BONUS_AZC", 50))

# پلن‌های اشتراک (قیمت AZC)
PLAN_WEEKLY_AZC = int(os.environ.get("PLAN_WEEKLY_AZC", 150))
PLAN_MONTHLY_AZC = int(os.environ.get("PLAN_MONTHLY_AZC", 400))
PLAN_QUARTERLY_AZC = int(os.environ.get("PLAN_QUARTERLY_AZC", 1000))

# پلن‌های اشتراک (قیمت دلاری)
PLAN_WEEKLY_USD = float(os.environ.get("PLAN_WEEKLY_USD", 15))
PLAN_MONTHLY_USD = float(os.environ.get("PLAN_MONTHLY_USD", 40))
PLAN_QUARTERLY_USD = float(os.environ.get("PLAN_QUARTERLY_USD", 100))

# اطلاعات پرداخت
PAYMENT_INFO = os.environ.get("PAYMENT_INFO",
    "💳 <b>اطلاعات پرداخت:</b>\n"
    "کارت: 6037-9918-XXXX-XXXX\n"
    "به نام: Azadi Trading\n\n"
    "یا USDT (TRC20): TXxxxxxxxxxxxxxxxxxxxxxxx")

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))
BOT_USERNAME = ""
ADMIN_PAGE_SIZE = 8

def now_ir() -> datetime:
    return datetime.now(IRAN_TZ)

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def _fmt_timedelta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d} روز")
    if h or d: parts.append(f"{h} ساعت")
    parts.append(f"{m} دقیقه")
    return " و ".join(parts) if parts else "کمتر از یک دقیقه"

def mention_html(uid: int, name: str, username: str = "") -> str:
    safe_name = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if not safe_name.strip(): safe_name = f"کاربر {uid}"
    if username: return f'<a href="https://t.me/{username}">{safe_name}</a>'
    return f'<a href="tg://user?id={uid}">{safe_name}</a>'

# ══════════════════════════════════════════════════════════════════════
# CLOUD STORAGE (MongoDB)
# ══════════════════════════════════════════════════════════════════════
try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
    MONGODB_AVAILABLE = True
except ImportError:
    MONGODB_AVAILABLE = False
    log.critical("pymongo نصب نیست!")

class CloudStorage:
    def __init__(self):
        self._uri = os.environ.get("MONGODB_URI", "")
        self._client = None
        self._db = None
        self._collection = None
        self._lock = threading.Lock()
        self._dirty = False
        self._data = {
            "users": {},
            "pending_payments": {},
            "archive_text": "📊 <b>نتایج هفته جاری</b>\n\nهنوز نتیجه‌ای توسط ادمین ثبت نشده است.\nبرای مشاهده عملکرد کانال، چند روز دیگر مراجعه کنید.",
            "stats": {
                "total_signals_sent": 0,
                "total_revenue_usd": 0,
                "total_azc_spent": 0,
            },
            "last_updated": None,
        }
        
        if self._uri and MONGODB_AVAILABLE:
            try:
                self._client = MongoClient(self._uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000)
                self._client.admin.command('ping')
                self._db = self._client.get_database()
                self._collection = self._db["azadi_bot_data"]
                log.info("MongoDB: اتصال برقرار شد ✅")
            except Exception as e:
                log.error(f"MongoDB اتصال ناموفق: {e}")
    
    def load(self):
        if not self._uri or self._collection is None: return
        try:
            doc = self._collection.find_one({"_id": "main"})
            if doc:
                with self._lock:
                    doc.pop("_id", None)
                    for k, v in doc.items(): self._data[k] = v
                    self._data.setdefault("users", {})
                    self._data.setdefault("pending_payments", {})
                    self._data.setdefault("archive_text", "📊 <b>نتایج هفته جاری</b>\n\nهنوز نتیجه‌ای ثبت نشده است.")
                    self._data.setdefault("stats", {"total_signals_sent": 0, "total_revenue_usd": 0, "total_azc_spent": 0})
        except Exception as e:
            log.error(f"MongoDB load error: {e}")
    
    def save(self, force: bool = False):
        if not self._uri or self._collection is None: return
        if not force and not self._dirty: return
        with self._lock:
            self._data["last_updated"] = now_ir().isoformat()
            payload = {"_id": "main"}
            payload.update(self._data)
            try:
                self._collection.replace_one({"_id": "main"}, payload, upsert=True)
                self._dirty = False
            except Exception as e:
                log.error(f"MongoDB save error: {e}")
    
    def ensure_user(self, uid: int, username: str = "", full_name: str = "") -> dict:
        key = str(uid)
        with self._lock:
            if key not in self._data["users"]:
                ref_code = hashlib.md5((key + str(time.time())).encode()).hexdigest()[:8]
                self._data["users"][key] = {
                    "uid": uid, "username": username or "", "full_name": full_name or "",
                    "ref": ref_code, "referred_by": None,
                    "azc_balance": 0, "vip_expiry": None, "vip_plan": None,
                    "invites": 0, "signals_received": 0, "is_blocked": False,
                    "kick_attempted": False,
                }
                self._dirty = True
            else:
                u = self._data["users"][key]
                if username and u.get("username") != username: u["username"] = username; self._dirty = True
                if full_name and u.get("full_name") != full_name: u["full_name"] = full_name; self._dirty = True
            return dict(self._data["users"][key])
    
    def get_user(self, uid: int) -> Optional[dict]:
        with self._lock:
            u = self._data["users"].get(str(uid))
            return dict(u) if u else None
    
    def get_all_users(self) -> list:
        with self._lock: return [{"uid_str": k, **v} for k, v in self._data["users"].items()]
    
    def get_vip_users(self) -> list:
        with self._lock:
            now = now_ir()
            return [{"uid_str": k, **u} for k, u in self._data["users"].items()
                    if u.get("vip_expiry") and datetime.fromisoformat(u["vip_expiry"]) > now]
    
    def find_uid_by_ref(self, ref_code: str) -> Optional[int]:
        with self._lock:
            for uid, data in self._data["users"].items():
                if data.get("ref") == ref_code: return int(uid)
            return None
    
    def apply_referral(self, new_uid: int, ref_uid: int) -> bool:
        if new_uid == ref_uid: return False
        n_key, r_key = str(new_uid), str(ref_uid)
        with self._lock:
            users = self._data["users"]
            if n_key not in users or r_key not in users: return False
            if users[n_key].get("referred_by"): return False
            users[n_key]["referred_by"] = r_key
            users[r_key]["azc_balance"] = users[r_key].get("azc_balance", 0) + REFERRAL_BONUS_AZC
            users[r_key]["invites"] = users[r_key].get("invites", 0) + 1
            self._dirty = True
            return True
    
    def add_azc(self, uid: int, amount: int, reason: str = "") -> bool:
        key = str(uid)
        with self._lock:
            if key in self._data["users"]:
                self._data["users"][key]["azc_balance"] = self._data["users"][key].get("azc_balance", 0) + amount
                self._dirty = True; return True
            return False
    
    def deduct_azc(self, uid: int, amount: int) -> bool:
        key = str(uid)
        with self._lock:
            u = self._data["users"].get(key)
            if u and u.get("azc_balance", 0) >= amount:
                u["azc_balance"] -= amount
                self._dirty = True; return True
            return False
    
    def activate_vip(self, uid: int, plan: str, duration_days: int) -> bool:
        key = str(uid)
        with self._lock:
            u = self._data["users"].get(key)
            if not u: return False
            
            if u.get("vip_expiry"):
                current = datetime.fromisoformat(u["vip_expiry"])
                base = current if current > now_ir() else now_ir()
            else:
                base = now_ir()
            
            u["vip_expiry"] = (base + timedelta(days=duration_days)).isoformat()
            u["vip_plan"] = plan
            u["is_blocked"] = False
            u["kick_attempted"] = False
            self._dirty = True
            return True
    
    def get_expired_vip_users(self) -> list:
        with self._lock:
            now = now_ir()
            return [{"uid_str": k, **u} for k, u in self._data["users"].items()
                    if u.get("vip_expiry") and datetime.fromisoformat(u["vip_expiry"]) <= now and not u.get("is_blocked")]
    
    def mark_user_blocked(self, uid: int):
        key = str(uid)
        with self._lock:
            if key in self._data["users"]:
                self._data["users"][key]["is_blocked"] = True
                self._dirty = True
    
    def mark_kick_attempted(self, uid: int):
        key = str(uid)
        with self._lock:
            if key in self._data["users"]:
                self._data["users"][key]["kick_attempted"] = True
                self._dirty = True
    
    def create_pending_payment(self, uid: int, plan: str, amount_usd: float) -> str:
        payment_id = hashlib.md5(f"{uid}{time.time()}".encode()).hexdigest()[:12]
        with self._lock:
            self._data["pending_payments"][payment_id] = {
                "uid": uid, "plan": plan, "amount_usd": amount_usd,
                "created_at": now_ir().isoformat(), "status": "pending",
            }
            self._dirty = True
        return payment_id
    
    def get_pending_payment(self, payment_id: str) -> Optional[dict]:
        with self._lock: return self._data["pending_payments"].get(payment_id)
    
    def approve_payment(self, payment_id: str) -> bool:
        with self._lock:
            p = self._data["pending_payments"].get(payment_id)
            if not p or p["status"] != "pending": return False
            p["status"] = "approved"
            duration_map = {"weekly": 7, "monthly": 30, "quarterly": 90}
            u = self._data["users"].get(str(p["uid"]))
            if u:
                self.activate_vip(p["uid"], p["plan"], duration_map.get(p["plan"], 30))
                self._data["stats"]["total_revenue_usd"] = self._data["stats"].get("total_revenue_usd", 0) + p["amount_usd"]
                return True
            return False
    
    def reject_payment(self, payment_id: str) -> bool:
        with self._lock:
            p = self._data["pending_payments"].get(payment_id)
            if not p or p["status"] != "pending": return False
            p["status"] = "rejected"
            self._dirty = True
            return True
    
    def get_all_pending_payments(self) -> list:
        with self._lock:
            return [{"payment_id": k, **v} for k, v in self._data["pending_payments"].items() if v.get("status") == "pending"]
    
    def get_archive_text(self) -> str:
        with self._lock: return self._data.get("archive_text", "📊 نتایج هفته جاری\n\nهنوز نتیجه‌ای ثبت نشده است.")
    
    def set_archive_text(self, text: str):
        with self._lock: self._data["archive_text"] = text; self._dirty = True
    
    def increment_signals_sent(self, count: int = 1):
        with self._lock:
            self._data["stats"]["total_signals_sent"] = self._data["stats"].get("total_signals_sent", 0) + count
            self._dirty = True
    
    def get_stats(self) -> dict:
        with self._lock: return dict(self._data.get("stats", {}))

cloud = CloudStorage()

# ══════════════════════════════════════════════════════════════════════
# MEMBERSHIP & KEYBOARDS
# ══════════════════════════════════════════════════════════════════════
async def check_membership(bot, uid: int) -> bool:
    if not FORCE_JOIN_CHANNEL: return True
    try:
        member = await bot.get_chat_member(chat_id=FORCE_JOIN_CHANNEL, user_id=uid)
        return member.status not in ("left", "kicked", "banned")
    except Exception: return True

def kb_join_required() -> InlineKeyboardMarkup:
    btns = []
    if FORCE_JOIN_CHANNEL:
        url = f"https://t.me/{FORCE_JOIN_CHANNEL.lstrip('@')}" if FORCE_JOIN_CHANNEL.startswith("@") else f"https://t.me/c/{str(FORCE_JOIN_CHANNEL).replace('-100', '')}"
        btns.append([InlineKeyboardButton("📢 عضویت در کانال", url=url)])
        btns.append([InlineKeyboardButton("✅ عضو شدم", callback_data="check_join")])
    return InlineKeyboardMarkup(btns)

def kb_main(uid: int = 0) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("👤 پروفایل من", callback_data="menu:profile"),
         InlineKeyboardButton("💎 خرید اشتراک VIP", callback_data="menu:subscribe")],
        [InlineKeyboardButton("🎁 دعوت و کسب AZC", callback_data="menu:invite"),
         InlineKeyboardButton("📊 آرشیو نتایج", callback_data="menu:archive")],
        [InlineKeyboardButton("ℹ️ راهنما و قوانین", callback_data="menu:help")],
    ]
    if uid and is_admin(uid):
        rows.append([InlineKeyboardButton("👑 پنل ادمین", callback_data="admin:panel:0")])
    return InlineKeyboardMarkup(rows)

def kb_back(dest: str = "home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"menu:{dest}")]])

def kb_subscribe() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🥉 هفتگی (7 روز) | {PLAN_WEEKLY_AZC} AZC", callback_data="buy:azc:weekly"),
         InlineKeyboardButton(f"{PLAN_WEEKLY_USD}$", callback_data="buy:fiat:weekly")],
        [InlineKeyboardButton(f"🥈 ماهانه (30 روز) | {PLAN_MONTHLY_AZC} AZC", callback_data="buy:azc:monthly"),
         InlineKeyboardButton(f"{PLAN_MONTHLY_USD}$", callback_data="buy:fiat:monthly")],
        [InlineKeyboardButton(f"🥇 سه ماهه (90 روز) | {PLAN_QUARTERLY_AZC} AZC", callback_data="buy:azc:quarterly"),
         InlineKeyboardButton(f"{PLAN_QUARTERLY_USD}$", callback_data="buy:fiat:quarterly")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="menu:home")],
    ])

# ══════════════════════════════════════════════════════════════════════
# TEXT BUILDERS
# ══════════════════════════════════════════════════════════════════════
def build_welcome_text() -> str:
    return (
        "💎✨ <b>به ربات سیگنال VIP Azadi خوش اومدی!</b> ✨💎\n\n"
        "<blockquote expandable>"
        "اینجا می‌تونی با جمع‌آوری <b>Azadi Coin (AZC)</b> یا پرداخت مستقیم، به جمع تریدرهای حرفه‌ای کانال VIP ما بپیوندی و سیگنال‌های لحظه‌ای و پرسود دریافت کنی!\n"
        "</blockquote>"
        "🔥 <b>ویژگی‌های کانال VIP:</b>\n"
        "  ⚡️ سیگنال‌های روزانه اسکالپ و سویینگ\n"
        "  📊 تحلیل‌های لایو و مدیریت سرمایه\n"
        "  🏆 وین‌ریت بالا و شفافیت کامل در نتایج\n\n"
        "از منوی زیر شروع کن 👇"
    )

def build_profile_text(uid: int, u: dict) -> str:
    status = "🔴 غیرفعال"
    time_left = "—"
    if u.get("vip_expiry"):
        expiry = datetime.fromisoformat(u["vip_expiry"])
        if expiry > now_ir():
            status = f"🟢 فعال ({u.get('vip_plan', 'VIP')})"
            time_left = _fmt_timedelta((expiry - now_ir()).total_seconds())
        else:
            status = "⚫️ منقضی شده"
    
    full = u.get("full_name", "").strip() or u.get("username", "") or "—"
    return (
        f"👤 <b>پروفایل شما</b>\n\n"
        f"📛 نام: <b>{full}</b>\n"
        f"🆔 آیدی: <code>{uid}</code>\n\n"
        f"💎 <b>وضعیت اشتراک VIP:</b> {status}\n"
        f"⏳ <b>زمان باقی‌مانده:</b> {time_left}\n\n"
        f"🪙 <b>موجودی Azadi Coin:</b> <code>{u.get('azc_balance', 0)} AZC</code>\n"
        f"👥 <b>تعداد دعوت‌های موفق:</b> {u.get('invites', 0)}"
    )

def build_referral_text(uid: int, bot_username: str) -> str:
    u = cloud.ensure_user(uid)
    link = f"https://t.me/{bot_username}?start=ref_{u['ref']}"
    return (
        "🎁 <b>دعوت دوستان و کسب AZC</b>\n\n"
        f"🔗 <b>لینک اختصاصی شما:</b>\n<code>{link}</code>\n\n"
        "<blockquote expandable>"
        f"✅ به ازای هر دوستی که از طریق لینک تو وارد ربات شود، <b>{REFERRAL_BONUS_AZC} AZC</b> به صورت آنی به حسابت اضافه می‌شود!\n"
        "با جمع‌آوری AZC می‌توانی اشتراک VIP کانال را به صورت کاملاً <b>رایگان</b> خریداری کنی.\n"
        "</blockquote>"
        f"👥 تعداد دعوت‌های موفق تو: <b>{u.get('invites', 0)}</b>"
    )

def build_help_text() -> str:
    return (
        "📖 <b>راهنمای استفاده از ربات Azadi</b>\n\n"
        "<blockquote expandable>"
        "🔹 <b>اشتراک هفتگی:</b> فقط دریافت سیگنال در همین ربات (بدون لینک کانال)\n"
        "🔹 <b>اشتراک ماهانه و سه‌ماهه:</b> دریافت سیگنال در ربات + <b>دسترسی به کانال VIP</b>\n\n"
        "🪙 <b>Azadi Coin (AZC) چیست؟</b>\n"
        "یک کوین داخلی است که با دعوت از دوستان تریدر خود می‌توانید آن را کسب کنید و به جای پول واقعی، با آن اشتراک VIP بخرید.\n\n"
        "💳 <b>پرداخت با پول واقعی:</b>\n"
        "پس از انتخاب پلن، اطلاعات پرداخت (کارت/تتر) به شما نمایش داده می‌شود. پس از واریز، رسید را ارسال کنید تا ادمین ظرف چند دقیقه اشتراک شما را فعال کند.\n"
        "</blockquote>"
        "⚖️ <b>قوانین و سلب مسئولیت:</b>\n"
        "• بازارهای مالی دارای ریسک هستند. مسئولیت سود و ضرر با خود شماست.\n"
        "• اکانت‌های فیک و ربات از سیستم رفرال حذف خواهند شد.\n"
        "• پس از اتمام اشتراک، دسترسی شما به کانال VIP به صورت خودکار قطع خواهد شد."
    )

# ══════════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════
async def _require_membership(update: Update, bot) -> bool:
    uid = update.effective_user.id
    if await check_membership(bot, uid): return True
    await update.effective_message.reply_text(
        "⛔️ <b>برای استفاده از ربات باید عضو کانال ما باشی!</b>\n\n👉 ابتدا عضو شو، سپس روی «✅ عضو شدم» بزن.",
        parse_mode=ParseMode.HTML, reply_markup=kb_join_required(),
    )
    return False

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    username = user.username or ""
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    is_new = cloud.get_user(uid) is None
    cloud.ensure_user(uid, username, full_name)
    
    if ctx.args:
        arg = ctx.args[0]
        if is_new and arg.startswith("ref_"):
            ref_code = arg[4:]
            if not await check_membership(ctx.bot, uid):
                ctx.user_data["pending_referral"] = ref_code
                await ctx.bot.send_message(chat_id=uid,
                    text="⛔️ <b>برای دریافت پاداش دعوت باید عضو کانال باشی!</b>\n\n👉 ابتدا عضو شو، سپس روی «✅ عضو شدم» بزن.",
                    parse_mode=ParseMode.HTML, reply_markup=kb_join_required())
                return
            
            ref_uid = cloud.find_uid_by_ref(ref_code)
            if ref_uid and cloud.apply_referral(uid, ref_uid):
                try:
                    await ctx.bot.send_message(chat_id=ref_uid, parse_mode=ParseMode.HTML,
                        text=f"🎉 <b>یک دعوت موفق!</b>\n👤 کاربر جدید وارد ربات شد.\n⭐️ <b>+{REFERRAL_BONUS_AZC} AZC</b> به موجودی شما اضافه شد.")
                    await ctx.bot.send_message(chat_id=uid, parse_mode=ParseMode.HTML,
                        text=f"🎁 <b>خوش اومدی!</b> چون از لینک دعوت وارد شدی، سیستم ربات رو بهت معرفی می‌کنه.")
                except Exception: pass
    
    if not await check_membership(ctx.bot, uid):
        await update.effective_message.reply_text(
            "⛔️ <b>برای استفاده از ربات باید عضو کانال ما باشی!</b>",
            parse_mode=ParseMode.HTML, reply_markup=kb_join_required())
        return
    
    await update.effective_message.reply_text(build_welcome_text(), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
    cloud.save()

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _require_membership(update, ctx.bot): return
    await update.effective_message.reply_text(build_help_text(), parse_mode=ParseMode.HTML, reply_markup=kb_back())

async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _require_membership(update, ctx.bot): return
    user = update.effective_user
    u = cloud.ensure_user(user.id, user.username or "", f"{user.first_name or ''} {user.last_name or ''}".strip())
    await update.effective_message.reply_text(build_profile_text(user.id, u), parse_mode=ParseMode.HTML, reply_markup=kb_back())

# ══════════════════════════════════════════════════════════════════════
# CALLBACK HANDLERS
# ══════════════════════════════════════════════════════════════════════
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    uid = q.from_user.id
    cloud.ensure_user(uid, q.from_user.username or "", f"{q.from_user.first_name or ''} {q.from_user.last_name or ''}".strip())
    
    if data != "check_join":
        if not await check_membership(q.get_bot(), uid):
            await q.answer()
            try:
                if q.message.chat.type == "channel":
                    await q.get_bot().send_message(chat_id=uid, text="⛔️ <b>ابتدا عضو کانال شو!</b>", parse_mode=ParseMode.HTML, reply_markup=kb_join_required())
                else:
                    await q.edit_message_text("⛔️ <b>ابتدا عضو کانال شو!</b>", parse_mode=ParseMode.HTML, reply_markup=kb_join_required())
            except Exception: pass
            return
    
    if data == "check_join":
        if await check_membership(q.get_bot(), uid):
            await q.answer("✅ تایید شد!")
            pending_ref = ctx.user_data.get("pending_referral")
            if pending_ref:
                ref_uid = cloud.find_uid_by_ref(pending_ref)
                if ref_uid and cloud.apply_referral(uid, ref_uid):
                    try: await q.get_bot().send_message(chat_id=ref_uid, parse_mode=ParseMode.HTML, text=f"🎉 یک دعوت موفق! +{REFERRAL_BONUS_AZC} AZC")
                    except Exception: pass
                ctx.user_data.pop("pending_referral", None)
                cloud.save(force=True)
            await q.edit_message_text(build_welcome_text(), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        else:
            await q.answer("⛔️ هنوز عضو نشدی!", show_alert=True)
        return
    
    # Main Menu
    if data.startswith("menu:"):
        await q.answer()
        u = cloud.ensure_user(uid)
        if data == "menu:home": await q.edit_message_text(build_welcome_text(), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        elif data == "menu:profile": await q.edit_message_text(build_profile_text(uid, u), parse_mode=ParseMode.HTML, reply_markup=kb_back())
        elif data == "menu:subscribe": await q.edit_message_text("💎 <b>پلن‌های اشتراک VIP Azadi</b>\n\nیک پلن را برای خرید انتخاب کنید:\n\n💡 <i>اشتراک ماهانه و سه‌ماهه شامل لینک عضویت در کانال VIP نیز می‌باشد.</i>", parse_mode=ParseMode.HTML, reply_markup=kb_subscribe())
        elif data == "menu:invite": 
            bot_username = (await ctx.bot.get_me()).username
            await q.edit_message_text(build_referral_text(uid, bot_username), parse_mode=ParseMode.HTML, reply_markup=kb_back())
        elif data == "menu:archive": await q.edit_message_text(cloud.get_archive_text(), parse_mode=ParseMode.HTML, reply_markup=kb_back())
        elif data == "menu:help": await q.edit_message_text(build_help_text(), parse_mode=ParseMode.HTML, reply_markup=kb_back())
        return
    
    # Buying Flow
    if data.startswith("buy:"):
        _, method, plan = data.split(":")
        await q.answer()
        u = cloud.get_user(uid)
        
        if method == "azc":
            price_map = {"weekly": (PLAN_WEEKLY_AZC, 7), "monthly": (PLAN_MONTHLY_AZC, 30), "quarterly": (PLAN_QUARTERLY_AZC, 90)}
            price, days = price_map[plan]
            if u.get("azc_balance", 0) >= price:
                cloud.deduct_azc(uid, price)
                cloud.activate_vip(uid, plan, days)
                cloud.save(force=True)
                
                msg = f"✅ <b>اشتراک {plan} با موفقیت فعال شد!</b>\n\n🪙 {price} AZC از حساب شما کسر شد.\n⏳ مدت زمان: {days} روز"
                if plan in ("monthly", "quarterly") and VIP_CHANNEL_ID:
                    try:
                        link = await ctx.bot.create_chat_invite_link(chat_id=VIP_CHANNEL_ID, member_limit=1, expire_date=int((now_ir() + timedelta(days=days+1)).timestamp()))
                        msg += f"\n\n🔗 <b>لینک عضویت در کانال VIP (یکبار مصرف):</b>\n{link.invite_link}"
                    except Exception as e:
                        log.error(f"Invite link generation failed: {e}")
                        msg += f"\n\n⚠️ خطا در تولید لینک کانال. لطفاً به ادمین پیام دهید."
                
                await q.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb_back())
            else:
                await q.edit_message_text(f"❌ <b>موجودی AZC شما کافی نیست!</b>\n\n💰 قیمت پلن: {price} AZC\n💼 موجودی شما: {u.get('azc_balance', 0)} AZC\n\nاز بخش «دعوت و کسب AZC» می‌تونی کوین جمع کنی.", parse_mode=ParseMode.HTML, reply_markup=kb_subscribe())
            return
        
        elif method == "fiat":
            price_map = {"weekly": (PLAN_WEEKLY_USD, 7), "monthly": (PLAN_MONTHLY_USD, 30), "quarterly": (PLAN_QUARTERLY_USD, 90)}
            price, days = price_map[plan]
            payment_id = cloud.create_pending_payment(uid, plan, price)
            
            ctx.user_data["awaiting"] = "payment_receipt"
            ctx.user_data["payment_id"] = payment_id
            
            await q.edit_message_text(
                f"💳 <b>پرداخت برای پلن {plan}</b>\n\n"
                f"💰 مبلغ قابل پرداخت: <b>{price}$</b>\n\n"
                f"{PAYMENT_INFO}\n\n"
                f"📸 <b>لطفاً عکس رسید واریزی یا هش تراکنش (TXID) را همینجا ارسال کنید:</b>\n"
                f"(برای لغو /cancel بزنید)",
                parse_mode=ParseMode.HTML)
            return
    
    # Admin Panel
    if not is_admin(uid):
        await q.answer("⛔️ دسترسی ندارید.", show_alert=True)
        return
    
    if data.startswith("admin:panel:"):
        await q.answer()
        stats = cloud.get_stats()
        text = (f"👑 <b>پنل مدیریت Azadi</b>\n\n"
                f"👥 کل کاربران: {len(cloud.get_all_users())}\n"
                f"💎 کاربران VIP فعال: {len(cloud.get_vip_users())}\n"
                f"📨 کل سیگنال‌های ارسالی: {stats.get('total_signals_sent', 0)}\n\n"
                f"یک گزینه را انتخاب کنید:")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✍️ ویرایش متن آرشیو", callback_data="admin:edit_archive")],
            [InlineKeyboardButton("⏳ پرداخت‌های در انتظار", callback_data="admin:pending_payments")],
            [InlineKeyboardButton("👥 لیست کاربران", callback_data="admin:users:0")],
            [InlineKeyboardButton("📢 ارسال همگانی", callback_data="admin:broadcast:prompt")],
            [InlineKeyboardButton("🔙 بازگشت به منو", callback_data="menu:home")],
        ])
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return
    
    if data == "admin:edit_archive":
        ctx.user_data["awaiting"] = "admin_archive_text"
        await q.answer()
        await q.edit_message_text("✍️ <b>ویرایش متن آرشیو نتایج</b>\n\nمتن جدید را ارسال کنید (پشتیبانی از HTML):\n(برای لغو /cancel)", parse_mode=ParseMode.HTML)
        return
    
    if data == "admin:pending_payments":
        await q.answer()
        payments = cloud.get_all_pending_payments()
        if not payments:
            await q.edit_message_text("✅ هیچ پرداخت در انتظاری وجود ندارد.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel:0")]]))
            return
        
        rows = []
        text = "⏳ <b>پرداخت‌های در انتظار تایید:</b>\n\n"
        for p in payments:
            u = cloud.get_user(p["uid"])
            name = u.get("full_name", "—") if u else "—"
            text += f"• {name} | {p['plan']} | {p['amount_usd']}$\n"
            rows.append([
                InlineKeyboardButton(f"✅ تایید {p['payment_id'][:6]}", callback_data=f"pay:approve:{p['payment_id']}"),
                InlineKeyboardButton(f"❌ رد", callback_data=f"pay:reject:{p['payment_id']}"),
            ])
        rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel:0")])
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return
    
    if data.startswith("pay:"):
        _, action, payment_id = data.split(":")
        await q.answer()
        if action == "approve":
            if cloud.approve_payment(payment_id):
                await q.edit_message_text("✅ پرداخت تایید و اشتراک کاربر فعال شد.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel:0")]]))
                p = cloud.get_pending_payment(payment_id)
                if p:
                    try:
                        await ctx.bot.send_message(chat_id=p["uid"], parse_mode=ParseMode.HTML,
                            text=f"🎉 <b>پرداخت شما تایید شد!</b>\nاشتراک {p['plan']} شما فعال گردید.")
                    except Exception: pass
            else:
                await q.edit_message_text("❌ خطا در تایید.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel:0")]]))
            cloud.save(force=True)
        elif action == "reject":
            cloud.reject_payment(payment_id)
            cloud.save(force=True)
            await q.edit_message_text("❌ پرداخت رد شد.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel:0")]]))
        return
    
    if data.startswith("admin:users:"):
        page = int(data.split(":")[-1])
        await q.answer()
        users = sorted(cloud.get_all_users(), key=lambda u: u.get("azc_balance", 0), reverse=True)
        total = len(users)
        start = page * ADMIN_PAGE_SIZE
        end = min(start + ADMIN_PAGE_SIZE, total)
        text = f"👥 <b>لیست کاربران</b> (صفحه {page + 1})\n\n"
        rows = []
        for u in users[start:end]:
            u_id = u.get("uid")
            name = u.get("full_name", "—")
            text += f"• <b>{name}</b> | <code>{u_id}</code>\n"
            rows.append([InlineKeyboardButton(f"🎁 +50 AZC به {name[:10]}", callback_data=f"admin:giveazc:{u_id}:50")])
        
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"admin:users:{page - 1}"))
        if end < total: nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"admin:users:{page + 1}"))
        if nav: rows.append(nav)
        rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel:0")])
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return
    
    if data.startswith("admin:giveazc:"):
        _, _, u_id, amount = data.split(":")
        cloud.add_azc(int(u_id), int(amount))
        cloud.save(force=True)
        await q.answer(f"✅ {amount} AZC به کاربر {u_id} اضافه شد.", show_alert=True)
        return
    
    if data == "admin:broadcast:prompt":
        ctx.user_data["awaiting"] = "broadcast_text"
        await q.answer()
        await q.edit_message_text("📢 <b>ارسال همگانی</b>\n\nپیام خود را بنویسید (HTML پشتیبانی می‌شود):\n(برای لغو /cancel)", parse_mode=ParseMode.HTML)
        return
    
    await q.answer()

# ══════════════════════════════════════════════════════════════════════
# MESSAGE HANDLER (User Receipts & Admin Actions)
# ══════════════════════════════════════════════════════════════════════
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text_in = (update.message.text or "").strip()
    awaiting = ctx.user_data.get("awaiting")
    
    if text_in == "/cancel":
        ctx.user_data.pop("awaiting", None)
        ctx.user_data.pop("payment_id", None)
        await update.message.reply_text("❌ لغو شد.", reply_markup=kb_main(uid))
        return
    
    # User sending payment receipt
    if awaiting == "payment_receipt":
        payment_id = ctx.user_data.get("payment_id")
        ctx.user_data.pop("awaiting", None)
        ctx.user_data.pop("payment_id", None)
        
        # Forward receipt to admins
        for admin_id in ADMIN_IDS:
            try:
                await ctx.bot.send_message(chat_id=admin_id, parse_mode=ParseMode.HTML,
                    text=f"🚨 <b>پرداخت جدید در انتظار تایید</b>\n\n"
                         f"👤 کاربر: {mention_html(uid, update.effective_user.full_name, update.effective_user.username)}\n"
                         f"🆔 آیدی: <code>{uid}</code>\n"
                         f"🧾 ID پرداخت: <code>{payment_id}</code>\n\n"
                         f"👇 رسید ارسالی کاربر:")
                if update.message.photo:
                    await ctx.bot.forward_message(chat_id=admin_id, from_chat_id=uid, message_id=update.message.message_id)
                elif update.message.text:
                    await ctx.bot.send_message(chat_id=admin_id, text=update.message.text)
            except Exception as e:
                log.error(f"Forwarding receipt failed: {e}")
        
        await update.message.reply_text("✅ <b>رسید شما با موفقیت ثبت شد!</b>\n\nپس از بررسی توسط ادمین (معمولاً زیر ۱۰ دقیقه)، اشتراک شما فعال و به شما اطلاع‌رسانی خواهد شد.", parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        return
    
    if not is_admin(uid): return
    
    # Admin actions
    if awaiting == "admin_archive_text":
        ctx.user_data.pop("awaiting", None)
        cloud.set_archive_text(text_in)
        cloud.save(force=True)
        await update.message.reply_text("✅ متن آرشیو با موفقیت به‌روزرسانی شد.", parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        return
    
    if awaiting == "broadcast_text":
        ctx.user_data.pop("awaiting", None)
        users = cloud.get_all_users()
        sent = 0
        for u in users:
            try:
                await ctx.bot.send_message(chat_id=int(u.get("uid_str") or u.get("uid")), text=text_in, parse_mode=ParseMode.HTML)
                sent += 1
                await asyncio.sleep(0.05)
            except Exception: pass
        await update.message.reply_text(f"✅ ارسال همگانی پایان یافت.\nارسال موفق: {sent} از {len(users)}", parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        return

# ══════════════════════════════════════════════════════════════════════
# CHANNEL POST HANDLER (SIGNAL ENGINE)
# ══════════════════════════════════════════════════════════════════════
async def on_channel_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """شکار سیگنال از کانال VIP و ارسال آنی به کاربران VIP"""
    post = update.channel_post
    if not post: return
    
    # اطمینان از اینکه پست از کانال VIP آمده
    if VIP_CHANNEL_ID and str(post.chat.id) != str(VIP_CHANNEL_ID).replace("-100", "") and str(post.chat.id) != str(VIP_CHANNEL_ID):
        return
    
    vip_users = cloud.get_vip_users()
    if not vip_users: return
    
    sent_count = 0
    for u in vip_users:
        uid = int(u.get("uid_str") or u.get("uid"))
        try:
            # کپی کردن پست به پی‌وی کاربر
            await ctx.bot.copy_message(chat_id=uid, from_chat_id=post.chat.id, message_id=post.message_id)
            sent_count += 1
            await asyncio.sleep(0.03)
        except Forbidden:
            cloud.mark_user_blocked(uid)
        except Exception as e:
            log.warning(f"Signal forward failed for {uid}: {e}")
    
    cloud.increment_signals_sent(sent_count)
    cloud.save()
    log.info(f"📡 Signal forwarded to {sent_count} VIP users.")

# ══════════════════════════════════════════════════════════════════════
# BACKGROUND TASKS
# ══════════════════════════════════════════════════════════════════════
async def expiration_watcher(app: Application):
    """بررسی اشتراک‌های منقضی شده و اخراج از کانال VIP"""
    while True:
        try:
            await asyncio.sleep(600)  # هر 10 دقیقه
            expired = cloud.get_expired_vip_users()
            for u in expired:
                uid = int(u.get("uid_str") or u.get("uid"))
                cloud.mark_user_blocked(uid)
                
                kick_status = "❌ ناموفق (نیاز به اخراج دستی)"
                if VIP_CHANNEL_ID and not u.get("kick_attempted"):
                    try:
                        await app.bot.ban_chat_member(chat_id=VIP_CHANNEL_ID, user_id=uid)
                        await app.bot.unban_chat_member(chat_id=VIP_CHANNEL_ID, user_id=uid, only_if_banned=True)
                        kick_status = "✅ اخراج خودکار از کانال انجام شد"
                    except Exception as e:
                        log.warning(f"Kick failed for {uid}: {e}")
                    cloud.mark_kick_attempted(uid)
                
                # هشدار به ادمین
                for admin_id in ADMIN_IDS:
                    try:
                        name = u.get("full_name", "—")
                        await app.bot.send_message(chat_id=admin_id, parse_mode=ParseMode.HTML,
                            text=f"🚨 <b>اشتراک کاربر به پایان رسید!</b>\n\n"
                                 f"👤 نام: {mention_html(uid, name, u.get('username', ''))}\n"
                                 f"🆔 آیدی: <code>{uid}</code>\n"
                                 f"📦 پلن: {u.get('vip_plan', 'VIP')}\n\n"
                                 f"⚠️ وضعیت: {kick_status}")
                    except Exception: pass
                cloud.save(force=True)
        except asyncio.CancelledError: break
        except Exception as e: log.error(f"expiration_watcher error: {e}")

async def autosave_loop(app: Application):
    while True:
        try:
            await asyncio.sleep(300)
            cloud.save()
        except asyncio.CancelledError: break
        except Exception as e: log.error(f"autosave_loop error: {e}")

async def on_startup(app: Application):
    global BOT_USERNAME
    try:
        me = await app.bot.get_me()
        BOT_USERNAME = me.username or ""
    except Exception: pass
    asyncio.create_task(expiration_watcher(app))
    asyncio.create_task(autosave_loop(app))
    log.info("Azadi Signal Bot ready ✅")

async def on_shutdown(app: Application):
    cloud.save(force=True)
    log.info("shutdown")

# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════
def main():
    if not BOT_TOKEN:
        log.critical("BOT_TOKEN تنظیم نشده!")
        sys.exit(1)
    
    cloud.load()
    app = (Application.builder().token(BOT_TOKEN)
           .post_init(on_startup).post_shutdown(on_shutdown).build())
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_message))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, on_message))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, on_channel_post))
    
    def _sig(s, f): cloud.save(force=True); sys.exit(0)
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    atexit.register(lambda: cloud.save(force=True))
    
    webhook_url = os.environ.get("RENDER_EXTERNAL_URL")
    if webhook_url:
        log.info("🌐 Running with Flask Webhook")
        main_loop = asyncio.new_event_loop()
        def run_event_loop(loop):
            asyncio.set_event_loop(loop)
            loop.run_forever()
        threading.Thread(target=run_event_loop, args=(main_loop,), daemon=True).start()
        
        flask_app = Flask(__name__)
        @flask_app.route("/")
        def index(): return "🤖 Azadi Bot Alive!", 200
        @flask_app.route(f"/{BOT_TOKEN}", methods=["POST"])
        def webhook():
            try:
                update = Update.de_json(request.get_json(force=True), app.bot)
                asyncio.run_coroutine_threadsafe(app.process_update(update), main_loop)
            except Exception as e: log.error(f"Webhook error: {e}")
            return "OK", 200
        
        async def setup():
            await app.bot.set_webhook(url=webhook_url.rstrip("/") + "/" + BOT_TOKEN, allowed_updates=["message", "callback_query", "channel_post"], drop_pending_updates=True)
        asyncio.run_coroutine_threadsafe(setup(), main_loop).result(timeout=30)
        
        async def init():
            await app.initialize()
            await app.start()
            await on_startup(app)
        asyncio.run_coroutine_threadsafe(init(), main_loop).result(timeout=30)
        
        flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
    else:
        log.warning("⚠️ Fallback to polling")
        app.run_polling(allowed_updates=["message", "callback_query", "channel_post"], drop_pending_updates=True)

if __name__ == "__main__":
    main()
