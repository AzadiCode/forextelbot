#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Azadi VIP Signal Bot — نسخه ۵.۰ (ساده و نهایی)
════════════════════════════════════════════════════════════════════
سیستم فروش اشتراک VIP با هشدار انقضا و مدیریت هوشمند
"""
import os
import sys
import time
import logging
import hashlib
import asyncio
import threading
import signal
import atexit
from datetime import datetime, timedelta, timezone
from typing import Optional
from flask import Flask, request

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, filters, MessageHandler,
)

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

FORCE_JOIN_CHANNEL = os.environ.get("FORCE_JOIN_CHANNEL", "@testchanellaz")
VIP_CHANNEL_ID = os.environ.get("VIP_CHANNEL_ID", "-1004450309103")

PLAN_WEEKLY_USD = float(os.environ.get("PLAN_WEEKLY_USD", 15))
PLAN_MONTHLY_USD = float(os.environ.get("PLAN_MONTHLY_USD", 40))

PLAN_WEEKLY_DAYS = 0.0007
PLAN_MONTHLY_DAYS = 0.0015

PAYMENT_INFO = os.environ.get("PAYMENT_INFO",
    "💳 <b>اطلاعات پرداخت:</b>\n"
    "کارت: 6037-9918-XXXX-XXXX\n"
    "به نام: Azadi Trading\n\n"
    "یا USDT (TRC20): TXxxxxxxxxxxxxxxxxxxxxxxx")

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))
BOT_USERNAME = ""
ADMIN_PAGE_SIZE = 8

# ⏱ زمان چک کردن انقضا (هر ۶۰ ثانیه)
EXPIRY_CHECK_INTERVAL = 60

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

def get_vip_channel_id():
    if not VIP_CHANNEL_ID: return None
    s = str(VIP_CHANNEL_ID).strip()
    if not s: return None
    if s.lstrip("-").isdigit():
        if not s.startswith("-100"): s = "-100" + s.lstrip("-")
        return int(s)
    return s

# ══════════════════════════════════════════════════════════════════════
# CLOUD STORAGE (MongoDB)
# ══════════════════════════════════════════════════════════════════════
try:
    from pymongo import MongoClient
    MONGODB_AVAILABLE = True
except ImportError:
    MONGODB_AVAILABLE = False
    log.critical("pymongo نصب نیست!")

class CloudStorage:
    def __init__(self):
        self._uri = os.environ.get("MONGODB_URI", "")
        self._client = None
        self._collection = None
        self._lock = threading.Lock()
        self._dirty = False
        self._data = {
            "users": {},
            "pending_payments": {},
            "archive_text": "📊 <b>نتایج هفته جاری</b>\n\nهنوز نتیجه‌ای توسط ادمین ثبت نشده است.",
            "stats": {"total_signals_sent": 0, "total_revenue_usd": 0, "total_subscriptions": 0},
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
                    self._data.setdefault("archive_text", "📊 نتایج هفته جاری")
                    self._data.setdefault("stats", {"total_signals_sent": 0, "total_revenue_usd": 0, "total_subscriptions": 0})
                    for k, u in self._data["users"].items():
                        u.setdefault("warned_expired", False)
                        u.setdefault("grace_until", None)
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
                self._data["users"][key] = {
                    "uid": uid, "username": username or "", "full_name": full_name or "",
                    "vip_expiry": None, "vip_plan": None,
                    "signals_received": 0, "is_blocked": False,
                    "kick_attempted": False, "last_link": None,
                    "warned_expired": False, "grace_until": None,
                }
                self._dirty = True
            else:
                u = self._data["users"][key]
                if username and u.get("username") != username: u["username"] = username; self._dirty = True
                if full_name and u.get("full_name") != full_name: u["full_name"] = full_name; self._dirty = True
                u.setdefault("warned_expired", False)
                u.setdefault("grace_until", None)
            return dict(self._data["users"][key])
    
    def get_user(self, uid: int) -> Optional[dict]:
        with self._lock:
            u = self._data["users"].get(str(uid))
            return dict(u) if u else None
    
    def get_all_users(self) -> list:
        with self._lock: return [{"uid_str": k, **v} for k, v in self._data["users"].items()]
    
    def get_vip_users(self) -> list:
        """کاربرانی که VIP فعال دارند یا در مهلت ادمین هستند"""
        with self._lock:
            now = now_ir()
            result = []
            for k, u in self._data["users"].items():
                if u.get("grace_until"):
                    try:
                        if datetime.fromisoformat(u["grace_until"]) > now:
                            result.append({"uid_str": k, **u})
                            continue
                    except: pass
                if u.get("vip_expiry"):
                    try:
                        if datetime.fromisoformat(u["vip_expiry"]) > now:
                            result.append({"uid_str": k, **u})
                    except: pass
            return result
    
    def _activate_vip_internal(self, user: dict, plan: str, duration_days: int) -> datetime:
        if user.get("vip_expiry"):
            try:
                current = datetime.fromisoformat(user["vip_expiry"])
                base = current if current > now_ir() else now_ir()
            except:
                base = now_ir()
        else:
            base = now_ir()
        
        new_expiry = base + timedelta(days=duration_days)
        user["vip_expiry"] = new_expiry.isoformat()
        user["vip_plan"] = plan
        user["is_blocked"] = False
        user["kick_attempted"] = False
        user["warned_expired"] = False
        user["grace_until"] = None
        self._dirty = True
        return new_expiry
    
    def activate_vip(self, uid: int, plan: str, duration_days: int) -> Optional[datetime]:
        key = str(uid)
        with self._lock:
            u = self._data["users"].get(key)
            if not u: return None
            return self._activate_vip_internal(u, plan, duration_days)
    
    def save_invite_link(self, uid: int, link: str):
        key = str(uid)
        with self._lock:
            if key in self._data["users"]:
                self._data["users"][key]["last_link"] = link
                self._dirty = True
    
    # ═══════ سیستم انقضا ═══════
    def get_newly_expired_users(self) -> list:
        """کاربرانی که VIP آن‌ها تمام شده و هنوز هشدار انقضا نگرفته‌اند"""
        with self._lock:
            now = now_ir()
            result = []
            for k, u in self._data["users"].items():
                if u.get("warned_expired") or u.get("is_blocked"): continue
                if not u.get("vip_expiry"): continue
                if u.get("grace_until"):
                    try:
                        if datetime.fromisoformat(u["grace_until"]) > now:
                            continue
                    except: pass
                try:
                    exp_dt = datetime.fromisoformat(u["vip_expiry"])
                except: continue
                if exp_dt <= now:
                    result.append({"uid_str": k, **u})
            return result
    
    def mark_warned_expired(self, uid: int):
        with self._lock:
            k = str(uid)
            if k in self._data["users"]:
                self._data["users"][k]["warned_expired"] = True
                self._dirty = True
    
    def grant_grace(self, uid: int, days: int = 3) -> Optional[datetime]:
        with self._lock:
            k = str(uid)
            u = self._data["users"].get(k)
            if not u: return None
            new_grace = now_ir() + timedelta(days=days)
            u["grace_until"] = new_grace.isoformat()
            u["warned_expired"] = False
            u["is_blocked"] = False
            self._dirty = True
            return new_grace
    
    def admin_kick_user(self, uid: int) -> bool:
        with self._lock:
            k = str(uid)
            if k in self._data["users"]:
                self._data["users"][k]["is_blocked"] = True
                self._data["users"][k]["vip_expiry"] = None
                self._data["users"][k]["grace_until"] = None
                self._dirty = True
                return True
            return False
    
    # ═══════ پرداخت‌ها ═══════
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
    
    def approve_payment(self, payment_id: str) -> Optional[tuple]:
        with self._lock:
            p = self._data["pending_payments"].get(payment_id)
            if not p or p["status"] != "pending": return None
            p["status"] = "approved"
            p["approved_at"] = now_ir().isoformat()
            u = self._data["users"].get(str(p["uid"]))
            if u:
                if p["plan"] == "weekly":
                    days = PLAN_WEEKLY_DAYS
                else:
                    days = PLAN_MONTHLY_DAYS
                new_expiry = self._activate_vip_internal(u, p["plan"], days)
                self._data["stats"]["total_revenue_usd"] = self._data["stats"].get("total_revenue_usd", 0) + p["amount_usd"]
                self._data["stats"]["total_subscriptions"] = self._data["stats"].get("total_subscriptions", 0) + 1
                return (p["uid"], p["plan"], new_expiry, days)
            return None
    
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
        with self._lock: return self._data.get("archive_text", "📊 نتایج هفته جاری")
    
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
# VIP LINK GENERATION
# ══════════════════════════════════════════════════════════════════════
async def generate_vip_invite_link(bot, uid: int, plan: str, expiry_dt: datetime) -> Optional[str]:
    vip_id = get_vip_channel_id()
    if not vip_id: return None
    try:
        link_expiry_ts = int((expiry_dt + timedelta(days=1)).timestamp())
        user = cloud.get_user(uid)
        if user and user.get("last_link"):
            try: await bot.revoke_chat_invite_link(chat_id=vip_id, invite_link=user["last_link"])
            except: pass
        link = await bot.create_chat_invite_link(
            chat_id=vip_id, name=f"VIP_{uid}_{plan[:3]}",
            expire_date=link_expiry_ts, member_limit=1,
        )
        cloud.save_invite_link(uid, link.invite_link)
        cloud.save()
        return link.invite_link
    except Forbidden:
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(chat_id=admin_id,
                    text="⚠️ <b>خطای بحرانی!</b>\n\nربات دسترسی <code>Invite Users via Link</code> را در کانال VIP ندارد.",
                    parse_mode=ParseMode.HTML)
            except: pass
        return None
    except Exception as e:
        log.error(f"Link generation failed for {uid}: {e}")
        return None

# ══════════════════════════════════════════════════════════════════════
# MEMBERSHIP & KEYBOARDS
# ══════════════════════════════════════════════════════════════════════
async def check_membership(bot, uid: int) -> bool:
    if not FORCE_JOIN_CHANNEL: return True
    try:
        member = await bot.get_chat_member(chat_id=FORCE_JOIN_CHANNEL, user_id=uid)
        return member.status not in ("left", "kicked", "banned")
    except: return True

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
         InlineKeyboardButton("💎 خرید/تمدید اشتراک", callback_data="menu:subscribe")],
        [InlineKeyboardButton("📊 آرشیو نتایج", callback_data="menu:archive"),
         InlineKeyboardButton("ℹ️ راهنما", callback_data="menu:help")],
    ]
    if uid and is_admin(uid):
        rows.append([InlineKeyboardButton("👑 پنل ادمین", callback_data="admin:panel:0")])
    return InlineKeyboardMarkup(rows)

def kb_back(dest: str = "home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"menu:{dest}")]])

def kb_subscribe() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🥉 هفتگی ({PLAN_WEEKLY_DAYS} روز) — {PLAN_WEEKLY_USD}$", callback_data="buy:fiat:weekly")],
        [InlineKeyboardButton(f"🥈 ماهانه ({PLAN_MONTHLY_DAYS} روز) — {PLAN_MONTHLY_USD}$", callback_data="buy:fiat:monthly")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="menu:home")],
    ])

def kb_renew_now() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 تمدید اشتراک VIP", callback_data="menu:subscribe")],
        [InlineKeyboardButton("🏠 منوی اصلی", callback_data="menu:home")],
    ])

# ══════════════════════════════════════════════════════════════════════
# TEXT BUILDERS
# ══════════════════════════════════════════════════════════════════════
def build_welcome_text() -> str:
    return (
        "💎✨ <b>به ربات سیگنال VIP Azadi خوش اومدی!</b> ✨💎\n\n"
        "<blockquote expandable>"
        "با خرید اشتراک VIP به جمع تریدرهای حرفه‌ای کانال ما بپیوند و سیگنال‌های لحظه‌ای و پرسود دریافت کن!\n"
        "</blockquote>"
        "🔥 <b>ویژگی‌های کانال VIP:</b>\n"
        "  ⚡️ سیگنال‌های روزانه اسکالپ و سویینگ\n"
        "  📊 تحلیل‌های لایو و مدیریت سرمایه\n"
        "  🏆 وین‌ریت بالا و شفافیت کامل\n\n"
        "🔒 <b>کانال VIP کاملاً خصوصی است</b> و فقط با لینک یکبار مصرف قابل ورود است.\n\n"
        "از منوی زیر شروع کن 👇"
    )

def build_profile_text(uid: int, u: dict) -> str:
    now = now_ir()
    status = "🔴 غیرفعال"
    time_left = "—"
    
    grace_until = None
    if u.get("grace_until"):
        try:
            grace_dt = datetime.fromisoformat(u["grace_until"])
            if grace_dt > now:
                grace_until = grace_dt
        except: pass
    
    if grace_until:
        status = "🟡 در مهلت تمدید (Grace Period)"
        time_left = _fmt_timedelta((grace_dt - now).total_seconds())
    elif u.get("vip_expiry"):
        try:
            expiry = datetime.fromisoformat(u["vip_expiry"])
            if expiry > now:
                plan_names = {"weekly": "هفتگی", "monthly": "ماهانه"}
                plan_name = plan_names.get(u.get('vip_plan', ''), 'VIP')
                status = f"🟢 فعال ({plan_name})"
                time_left = _fmt_timedelta((expiry - now).total_seconds())
            else:
                status = "⚫️ منقضی شده"
        except: pass
    
    full = u.get("full_name", "").strip() or u.get("username", "") or "—"
    
    link_section = ""
    if u.get("last_link"):
        has_access = False
        if u.get("vip_expiry"):
            try:
                if datetime.fromisoformat(u["vip_expiry"]) > now: has_access = True
            except: pass
        if grace_until: has_access = True
        if has_access:
            link_section = f"\n🔗 <b>لینک ورود به کانال VIP:</b>\n{u['last_link']}\n<i>(یکبار مصرف)</i>\n"
    
    return (
        f"👤 <b>پروفایل شما</b>\n\n"
        f"📛 نام: <b>{full}</b>\n"
        f"🆔 آیدی: <code>{uid}</code>\n\n"
        f"💎 <b>وضعیت اشتراک VIP:</b> {status}\n"
        f"⏳ <b>زمان باقی‌مانده:</b> {time_left}\n"
        f"{link_section}"
    )

def build_help_text() -> str:
    return (
        "📖 <b>راهنمای استفاده از ربات Azadi</b>\n\n"
        "<blockquote expandable>"
        "💳 <b>نحوه خرید اشتراک:</b>\n"
        "۱. روی «خرید اشتراک» کلیک کنید\n"
        "۲. پلن مورد نظر را انتخاب کنید\n"
        "۳. مبلغ را واریز کنید\n"
        "۴. رسید را ارسال کنید\n"
        "۵. پس از تایید ادمین، اشتراک فعال می‌شود\n\n"
        "🔐 <b>امنیت کانال VIP:</b>\n"
        "لینک دعوت شما یکبار مصرف است و تا پایان اشتراک اعتبار دارد.\n"
        "</blockquote>"
        "⚖️ <b>سلب مسئولیت:</b>\n"
        "• بازارهای مالی دارای ریسک هستند.\n"
    )

# ══════════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════
async def _require_membership(update: Update, bot) -> bool:
    uid = update.effective_user.id
    if await check_membership(bot, uid): return True
    await update.effective_message.reply_text(
        "⛔️ <b>برای استفاده از ربات باید عضو کانال ما باشی!</b>",
        parse_mode=ParseMode.HTML, reply_markup=kb_join_required())
    return False

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    cloud.ensure_user(uid, user.username or "", f"{user.first_name or ''} {user.last_name or ''}".strip())
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

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """لغو هر عملیات در حال انتظار"""
    ctx.user_data.pop("awaiting", None)
    ctx.user_data.pop("payment_id", None)
    ctx.user_data.pop("senduser_target", None)
    ctx.user_data.pop("fake_add_name", None)
    ctx.user_data.pop("fake_edit_id", None)
    ctx.user_data.pop("setpts_uid", None)
    
    await update.message.reply_text(
        "❌ <b>عملیات لغو شد.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_main(update.effective_user.id)
    )

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
                await q.edit_message_text("⛔️ <b>ابتدا عضو کانال شو!</b>", parse_mode=ParseMode.HTML, reply_markup=kb_join_required())
            except: pass
            return
    
    if data == "check_join":
        if await check_membership(q.get_bot(), uid):
            await q.answer("✅ تایید شد!")
            await q.edit_message_text(build_welcome_text(), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        else:
            await q.answer("⛔️ هنوز عضو نشدی!", show_alert=True)
        return
    
    if data.startswith("menu:"):
        await q.answer()
        u = cloud.ensure_user(uid)
        if data == "menu:home": await q.edit_message_text(build_welcome_text(), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        elif data == "menu:profile": await q.edit_message_text(build_profile_text(uid, u), parse_mode=ParseMode.HTML, reply_markup=kb_back())
        elif data == "menu:subscribe":
            await q.edit_message_text(
                "💎 <b>پلن‌های اشتراک VIP Azadi</b>\n\n"
                "🔒 کانال VIP کاملاً خصوصی است و هر پلن شامل لینک اختصاصی یکبار مصرف می‌باشد.\n\n"
                "یک پلن را برای خرید یا تمدید انتخاب کنید:",
                parse_mode=ParseMode.HTML, reply_markup=kb_subscribe())
        elif data == "menu:archive": await q.edit_message_text(cloud.get_archive_text(), parse_mode=ParseMode.HTML, reply_markup=kb_back())
        elif data == "menu:help": await q.edit_message_text(build_help_text(), parse_mode=ParseMode.HTML, reply_markup=kb_back())
        return
    
    if data.startswith("buy:fiat:"):
        try: plan = data.split(":")[-1]
        except: await q.answer(); return
        await q.answer()
        price_map = {"weekly": PLAN_WEEKLY_USD, "monthly": PLAN_MONTHLY_USD}
        price = price_map.get(plan)
        if not price: return
        payment_id = cloud.create_pending_payment(uid, plan, price)
        ctx.user_data["awaiting"] = "payment_receipt"
        ctx.user_data["payment_id"] = payment_id
        await q.edit_message_text(
            f"💳 <b>پرداخت برای پلن {plan}</b>\n\n"
            f"💰 مبلغ قابل پرداخت: <b>{price}$</b>\n\n"
            f"{PAYMENT_INFO}\n\n"
            f"📸 <b>لطفاً عکس رسید واریزی یا TXID را همینجا ارسال کنید:</b>\n"
            f"(برای لغو /cancel بزنید)",
            parse_mode=ParseMode.HTML)
        return
    
    # ═══════ ADMIN ACTIONS ═══════
    if not is_admin(uid):
        await q.answer("⛔️ دسترسی ندارید.", show_alert=True)
        return
    
    if data.startswith("admin:panel:"):
        await q.answer()
        stats = cloud.get_stats()
        text = (f"👑 <b>پنل مدیریت Azadi</b>\n\n"
                f"👥 کل کاربران: {len(cloud.get_all_users())}\n"
                f"💎 کاربران VIP فعال: {len(cloud.get_vip_users())}\n"
                f"💰 درآمد کل: {stats.get('total_revenue_usd', 0):.2f}$\n"
                f"📨 سیگنال‌های ارسالی: {stats.get('total_signals_sent', 0)}\n\n"
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
        await q.edit_message_text("✍️ <b>ویرایش متن آرشیو</b>\n\nمتن جدید را ارسال کنید:\n(برای لغو /cancel)", parse_mode=ParseMode.HTML)
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
            text += f"• <b>{name}</b> | {p['plan']} | {p['amount_usd']}$\n"
            rows.append([
                InlineKeyboardButton(f"✅ تایید", callback_data=f"pay:approve:{p['payment_id']}"),
                InlineKeyboardButton(f"❌ رد", callback_data=f"pay:reject:{p['payment_id']}"),
            ])
        rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel:0")])
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return
    
    if data.startswith("pay:approve:"):
        payment_id = data.split(":")[-1]
        await q.answer("در حال پردازش...")
        result = cloud.approve_payment(payment_id)
        cloud.save(force=True)
        if result:
            p_uid, p_plan, new_expiry, days = result
            await q.edit_message_text(
                f"✅ <b>پرداخت تایید و اشتراک فعال شد.</b>\n\n"
                f"👤 کاربر: <code>{p_uid}</code>\n📦 پلن: {p_plan}\n"
                f"📅 انقضا: {new_expiry.strftime('%Y-%m-%d')}\n"
                f"⏳ مدت: {days} روز",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel:0")]]))
            try:
                msg_to_user = f"🎉 <b>پرداخت شما تایید شد!</b>\n\nاشتراک {p_plan} شما فعال گردید.\n📅 انقضا: {new_expiry.strftime('%Y-%m-%d')}\n⏳ مدت: {days} روز"
                if VIP_CHANNEL_ID:
                    link = await generate_vip_invite_link(ctx.bot, p_uid, p_plan, new_expiry)
                    if link:
                        msg_to_user += f"\n\n🔐 <b>لینک اختصاصی ورود به کانال VIP:</b>\n{link}"
                await ctx.bot.send_message(chat_id=p_uid, text=msg_to_user, parse_mode=ParseMode.HTML)
            except Exception as e:
                log.error(f"Failed to notify user {p_uid}: {e}")
        else:
            await q.edit_message_text("❌ خطا در تایید.", parse_mode=ParseMode.HTML,
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel:0")]]))
        return
    
    if data.startswith("pay:reject:"):
        payment_id = data.split(":")[-1]
        cloud.reject_payment(payment_id)
        cloud.save(force=True)
        await q.answer()
        await q.edit_message_text("❌ پرداخت رد شد.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel:0")]]))
        return
    
    if data.startswith("admin:kick:"):
        try: target_uid = int(data.split(":")[-1])
        except: await q.answer(); return
        await q.answer("در حال حذف کاربر...")
        cloud.admin_kick_user(target_uid)
        vip_id = get_vip_channel_id()
        kick_result = "❌ ناموفق"
        if vip_id:
            try:
                await ctx.bot.ban_chat_member(chat_id=vip_id, user_id=target_uid)
                await ctx.bot.unban_chat_member(chat_id=vip_id, user_id=target_uid, only_if_banned=True)
                kick_result = "✅ اخراج انجام شد"
            except Exception as e:
                kick_result = f"❌ خطا: {str(e)[:60]}"
        u = cloud.get_user(target_uid)
        if u and u.get("last_link") and vip_id:
            try: await ctx.bot.revoke_chat_invite_link(chat_id=vip_id, invite_link=u["last_link"])
            except: pass
        cloud.save(force=True)
        try:
            await q.edit_message_text(
                f"✅ <b>کاربر حذف شد</b>\n\n👤 ID: <code>{target_uid}</code>\n⚠️ وضعیت: {kick_result}",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel:0")]]))
        except: pass
        return
    
    if data.startswith("admin:extend:"):
        try: target_uid = int(data.split(":")[-1])
        except: await q.answer(); return
        await q.answer("در حال تمدید...")
        new_grace = cloud.grant_grace(target_uid, days=3)
        cloud.save(force=True)
        if new_grace:
            try:
                await q.edit_message_text(
                    f"✅ <b>مهلت ۳ روزه اعطا شد</b>\n\n👤 ID: <code>{target_uid}</code>\n📅 مهلت: {new_grace.strftime('%Y-%m-%d %H:%M')}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel:0")]]))
                await ctx.bot.send_message(chat_id=target_uid, parse_mode=ParseMode.HTML,
                    text=f"🎁 <b>هدیه ویژه از تیم Azadi!</b>\n\nاشتراک شما به مدت <b>۳ روز</b> تمدید شد.\n📅 مهلت: {new_grace.strftime('%Y-%m-%d')}")
            except Exception as e:
                log.error(f"Extend notify failed: {e}")
        return
    
    if data.startswith("admin:users:"):
        page = int(data.split(":")[-1])
        await q.answer()
        users = sorted(cloud.get_all_users(), key=lambda u: u.get("vip_expiry") or "1970", reverse=True)
        total = len(users)
        start = page * ADMIN_PAGE_SIZE
        end = min(start + ADMIN_PAGE_SIZE, total)
        text = f"👥 <b>لیست کاربران</b> (صفحه {page + 1})\n\n"
        rows = []
        for u in users[start:end]:
            u_id = u.get("uid")
            name = u.get("full_name", "—")
            vip_exp = u.get("vip_expiry")
            exp_short = vip_exp[:10] if vip_exp else "—"
            text += f"• <b>{name}</b> | <code>{u_id}</code>\n  VIP: {exp_short}\n"
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"admin:users:{page - 1}"))
        if end < total: nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"admin:users:{page + 1}"))
        if nav: rows.append(nav)
        rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel:0")])
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return
    
    if data == "admin:broadcast:prompt":
        ctx.user_data["awaiting"] = "broadcast_text"
        await q.answer()
        await q.edit_message_text("📢 <b>ارسال همگانی</b>\n\nپیام خود را بنویسید:\n(برای لغو /cancel)", parse_mode=ParseMode.HTML)
        return
    
    await q.answer()

# ══════════════════════════════════════════════════════════════════════
# MESSAGE HANDLER
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
    
    if awaiting == "payment_receipt":
        payment_id = ctx.user_data.get("payment_id")
        ctx.user_data.pop("awaiting", None)
        ctx.user_data.pop("payment_id", None)
        for admin_id in ADMIN_IDS:
            try:
                await ctx.bot.send_message(chat_id=admin_id, parse_mode=ParseMode.HTML,
                    text=f"🚨 <b>پرداخت جدید</b>\n\n"
                         f"👤 کاربر: {mention_html(uid, update.effective_user.full_name, update.effective_user.username)}\n"
                         f"🆔 آیدی: <code>{uid}</code>\n"
                         f"🧾 ID: <code>{payment_id}</code>\n\n"
                         f"👇 رسید:")
                if update.message.photo:
                    await ctx.bot.forward_message(chat_id=admin_id, from_chat_id=uid, message_id=update.message.message_id)
                elif update.message.text:
                    await ctx.bot.send_message(chat_id=admin_id, text=update.message.text)
            except Exception as e:
                log.error(f"Forwarding failed: {e}")
        await update.message.reply_text("✅ <b>رسید ثبت شد!</b>\n\nپس از بررسی ادمین، اشتراک فعال می‌شود.", parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        return
    
    if not is_admin(uid): return
    
    if awaiting == "admin_archive_text":
        ctx.user_data.pop("awaiting", None)
        cloud.set_archive_text(text_in)
        cloud.save(force=True)
        await update.message.reply_text("✅ متن آرشیو به‌روزرسانی شد.", parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
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
            except: pass
        await update.message.reply_text(f"✅ ارسال شد: {sent} از {len(users)}", parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        return

# ══════════════════════════════════════════════════════════════════════
# CHANNEL POST HANDLER
# ══════════════════════════════════════════════════════════════════════
async def on_channel_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    if not post: return
    vip_id = get_vip_channel_id()
    if vip_id:
        if str(post.chat.id) != str(vip_id):
            return
    vip_users = cloud.get_vip_users()
    if not vip_users: return
    sent_count = 0
    for u in vip_users:
        uid_int = int(u.get("uid_str") or u.get("uid"))
        try:
            await ctx.bot.copy_message(chat_id=uid_int, from_chat_id=post.chat.id, message_id=post.message_id)
            sent_count += 1
            await asyncio.sleep(0.05)
        except Forbidden:
            with cloud._lock:
                k = str(uid_int)
                if k in cloud._data["users"]:
                    cloud._data["users"][k]["is_blocked"] = True
                    cloud._dirty = True
        except Exception as e:
            log.warning(f"Signal forward failed for {uid_int}: {e}")
    cloud.increment_signals_sent(sent_count)
    cloud.save()
    log.info(f"📡 Signal forwarded to {sent_count} VIP users.")

# ══════════════════════════════════════════════════════════════════════
# EXPIRATION WATCHER (فقط هشدار نهایی انقضا)
# ══════════════════════════════════════════════════════════════════════
async def expiration_watcher(app: Application):
    """بررسی دوره‌ای انقضای اشتراک‌ها"""
    await asyncio.sleep(10)
    
    while True:
        try:
            now = now_ir()
            newly_expired = cloud.get_newly_expired_users()
            
            for u in newly_expired:
                uid_int = int(u.get("uid_str") or u.get("uid"))
                
                # ═══ ارسال هشدار به کاربر ═══
                user_msg_sent = False
                try:
                    await app.bot.send_message(chat_id=uid_int, parse_mode=ParseMode.HTML,
                        text=f"🔴 <b>اشتراک VIP شما به پایان رسید</b>\n\n"
                             f"👤 {u.get('full_name', '')} عزیز،\n"
                             f"متاسفانه اشتراک شما به پایان رسیده است.\n\n"
                             f"⚠️ از این لحظه:\n"
                             f"• سیگنال‌های جدید ارسال نمی‌شوند\n"
                             f"• دسترسی به کانال VIP محدود شده است\n\n"
                             f"💎 برای بازگشت، اشتراک خود را تمدید کنید:",
                        reply_markup=kb_renew_now())
                    user_msg_sent = True
                    log.info(f"✅ Expiry warning sent to user {uid_int}")
                except Forbidden:
                    with cloud._lock:
                        k = str(uid_int)
                        if k in cloud._data["users"]:
                            cloud._data["users"][k]["is_blocked"] = True
                            cloud._dirty = True
                    user_msg_sent = True
                except Exception as e:
                    log.error(f"❌ User expiry warning failed for {uid_int}: {e}")
                
                # ═══ ارسال هشدار به ادمین ═══
                admin_msg_sent = False
                for admin_id in ADMIN_IDS:
                    try:
                        name = u.get("full_name", "—")
                        username = u.get("username", "")
                        admin_kb = InlineKeyboardMarkup([
                            [
                                InlineKeyboardButton("🗑 حذف کاربر", callback_data=f"admin:kick:{uid_int}"),
                                InlineKeyboardButton("🎁 تمدید ۳ روزه", callback_data=f"admin:extend:{uid_int}"),
                            ],
                        ])
                        await app.bot.send_message(chat_id=admin_id, parse_mode=ParseMode.HTML,
                            text=f"🚨 <b>اشتراک کاربر به پایان رسید!</b>\n\n"
                                 f"👤 نام: {mention_html(uid_int, name, username)}\n"
                                 f"🆔 آیدی: <code>{uid_int}</code>\n"
                                 f"📦 پلن قبلی: {u.get('vip_plan', 'VIP')}\n\n"
                                 f"⚠️ کاربر حذف نشده است. تصمیم بگیرید:",
                            reply_markup=admin_kb)
                        admin_msg_sent = True
                        log.info(f"✅ Admin notification sent for {uid_int}")
                    except Exception as e:
                        log.error(f"❌ Admin notification failed: {e}")
                
                if user_msg_sent or admin_msg_sent:
                    cloud.mark_warned_expired(uid_int)
                    cloud.save(force=True)
            
            await asyncio.sleep(EXPIRY_CHECK_INTERVAL)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"expiration_watcher error: {e}")

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
    except: pass
    asyncio.create_task(expiration_watcher(app))
    asyncio.create_task(autosave_loop(app))
    log.info("Azadi Signal Bot v5.0 ready ✅")

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
    app.add_handler(CommandHandler("cancel", cmd_cancel))  # ← این خط را اضافه کنید
    
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
        def index(): return "🤖 Azadi Bot v5.0 Alive!", 200
        @flask_app.route(f"/{BOT_TOKEN}", methods=["POST"])
        def webhook():
            try:
                update = Update.de_json(request.get_json(force=True), app.bot)
                asyncio.run_coroutine_threadsafe(app.process_update(update), main_loop)
            except Exception as e: log.error(f"Webhook error: {e}")
            return "OK", 200
        
        async def setup():
            full_url = webhook_url.rstrip("/") + "/" + BOT_TOKEN
            await app.bot.delete_webhook(drop_pending_updates=True)
            await asyncio.sleep(1)
            await app.bot.set_webhook(
                url=full_url,
                allowed_updates=["message", "callback_query", "channel_post"],
                drop_pending_updates=True)
            log.info(f"✅ Webhook set: {full_url[:60]}...")
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
