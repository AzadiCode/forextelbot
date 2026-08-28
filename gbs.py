#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GramBotSaz — پلتفرم حرفه‌ای ربات‌ساز تلگرام
════════════════════════════════════════════════════════════════════
ساخت، مدیریت و اجرای ربات‌های تلگرام با سیستم اقتصاد داخلی (GramCoin)
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
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Any
from flask import Flask, request

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, InvalidToken
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
log = logging.getLogger("GramBotSaz")

# ══════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════
PLATFORM_BOT_TOKEN = os.environ.get("PLATFORM_BOT_TOKEN", "8649622614:AAFs3Hoz9EbjBGiFeua3wt8j_XJFRbtMNxA")
PORT = int(os.environ.get("PORT", 5000))

_admin_raw = os.environ.get("ADMIN_IDS", "605611554")
ADMIN_IDS = {int(x) for x in _admin_raw.replace(" ", "").split(",") if x.strip()}

FORCE_JOIN_CHANNEL = os.environ.get("FORCE_JOIN_CHANNEL", "@testchanellaz")

# ══════════════════════════════════════════════════════════════════════
# ECONOMY
# ══════════════════════════════════════════════════════════════════════
GRAMCOIN_SYMBOL = "GC"
REFERRAL_BONUS_GC = 50
DAILY_REWARD_GC = 5
BOT_DAILY_COST_GC = 2  # هزینه روزانه نگهداری هر ربات

# ══════════════════════════════════════════════════════════════════════
# BOT TEMPLATES
# ══════════════════════════════════════════════════════════════════════
AVAILABLE_TEMPLATES = {
    "echo": {
        "name": "🤖 Echo Bot",
        "description": "ربات ساده که پیام‌ها را تکرار می‌کند",
        "cost_gc": 0,
        "features": ["تکرار پیام", "پاسخ خودکار ساده"],
        "is_premium": False,
    },
    "shop": {
        "name": "🛒 Shop Bot",
        "description": "فروشگاه آنلاین با سبد خرید",
        "cost_gc": 100,
        "features": ["مدیریت محصولات", "سبد خرید"],
        "is_premium": False,
    },
    "signal": {
        "name": "📊 Signal Bot",
        "description": "ارسال سیگنال VIP به مشترکین",
        "cost_gc": 150,
        "features": ["ارسال سیگنال", "مدیریت اشتراک"],
        "is_premium": True,
    },
    "support": {
        "name": "🎫 Support Bot",
        "description": "سیستم تیکت و پشتیبانی",
        "cost_gc": 80,
        "features": ["تیکت", "FAQ", "پاسخ خودکار"],
        "is_premium": False,
    },
}

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))
PLATFORM_BOT_USERNAME = ""
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
            "bots": {},
            "transactions": [],
            "last_updated": None,
        }
        
        if self._uri and MONGODB_AVAILABLE:
            try:
                self._client = MongoClient(self._uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000)
                self._client.admin.command('ping')
                self._db = self._client.get_database()
                self._collection = self._db["grambotsaz_data"]
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
                    self._data.setdefault("bots", {})
                    self._data.setdefault("transactions", [])
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
    
    # ═══════ USER MANAGEMENT ═══════
    def ensure_user(self, uid: int, username: str = "", full_name: str = "") -> dict:
        key = str(uid)
        with self._lock:
            if key not in self._data["users"]:
                ref_code = hashlib.md5(f"{uid}{time.time()}".encode()).hexdigest()[:8]
                self._data["users"][key] = {
                    "uid": uid,
                    "username": username or "",
                    "full_name": full_name or "",
                    "gramcoin_balance": 0,
                    "total_earned_gc": 0,
                    "referral_code": ref_code,
                    "referred_by": None,
                    "created_at": now_ir().isoformat(),
                    "last_daily_claim": None,
                    "bots_created": [],
                }
                self._dirty = True
            else:
                u = self._data["users"][key]
                if username and u.get("username") != username:
                    u["username"] = username
                    self._dirty = True
                if full_name and u.get("full_name") != full_name:
                    u["full_name"] = full_name
                    self._dirty = True
            return dict(self._data["users"][key])
    
    def get_user(self, uid: int) -> Optional[dict]:
        with self._lock:
            u = self._data["users"].get(str(uid))
            return dict(u) if u else None
    
    def get_all_users(self) -> list:
        with self._lock: return [{"uid_str": k, **v} for k, v in self._data["users"].items()]
    
    def add_gramcoin(self, uid: int, amount: int, reason: str) -> bool:
        key = str(uid)
        with self._lock:
            if key in self._data["users"]:
                self._data["users"][key]["gramcoin_balance"] += amount
                self._data["users"][key]["total_earned_gc"] += amount
                self._dirty = True
                
                tx_id = hashlib.md5(f"{uid}{time.time()}".encode()).hexdigest()[:12]
                self._data["transactions"].append({
                    "tx_id": tx_id,
                    "uid": uid,
                    "amount_gc": amount,
                    "tx_type": reason,
                    "description": f"+{amount} GC ({reason})",
                    "timestamp": now_ir().isoformat(),
                })
                self._data["transactions"] = self._data["transactions"][-1000:]
                return True
            return False
    
    def deduct_gramcoin(self, uid: int, amount: int, reason: str) -> bool:
        key = str(uid)
        with self._lock:
            u = self._data["users"].get(key)
            if u and u.get("gramcoin_balance", 0) >= amount:
                u["gramcoin_balance"] -= amount
                self._dirty = True
                
                tx_id = hashlib.md5(f"{uid}{time.time()}".encode()).hexdigest()[:12]
                self._data["transactions"].append({
                    "tx_id": tx_id,
                    "uid": uid,
                    "amount_gc": -amount,
                    "tx_type": reason,
                    "description": f"-{amount} GC ({reason})",
                    "timestamp": now_ir().isoformat(),
                })
                return True
            return False
    
    def can_claim_daily(self, uid: int) -> bool:
        u = self.get_user(uid)
        if not u or not u.get("last_daily_claim"):
            return True
        try:
            last_claim = datetime.fromisoformat(u["last_daily_claim"])
            return (now_ir() - last_claim).days >= 1
        except:
            return True
    
    def claim_daily_reward(self, uid: int, amount: int) -> bool:
        if not self.can_claim_daily(uid):
            return False
        key = str(uid)
        with self._lock:
            if key in self._data["users"]:
                self._data["users"][key]["last_daily_claim"] = now_ir().isoformat()
                self._dirty = True
        return self.add_gramcoin(uid, amount, "daily_reward")
    
    # ═══════ BOT MANAGEMENT ═══════
    def create_bot(self, owner_uid: int, bot_token: str, template_type: str) -> Optional[dict]:
        bot_id = hashlib.md5(f"{owner_uid}{bot_token}{time.time()}".encode()).hexdigest()[:12]
        with self._lock:
            self._data["bots"][bot_id] = {
                "bot_id": bot_id,
                "owner_uid": owner_uid,
                "bot_token": bot_token,
                "bot_username": "",
                "template_type": template_type,
                "is_active": True,
                "created_at": now_ir().isoformat(),
                "last_activity": None,
                "settings": {},
                "stats": {
                    "messages_received": 0,
                    "messages_sent": 0,
                    "unique_users": 0,
                },
            }
            
            user_key = str(owner_uid)
            if user_key in self._data["users"]:
                self._data["users"][user_key]["bots_created"].append(bot_id)
            
            self._dirty = True
            return dict(self._data["bots"][bot_id])
    
    def get_bot(self, bot_id: str) -> Optional[dict]:
        with self._lock:
            b = self._data["bots"].get(bot_id)
            return dict(b) if b else None
    
    def get_all_bots(self) -> list:
        with self._lock: return [{"bot_id": k, **v} for k, v in self._data["bots"].items()]
    
    def get_user_bots(self, uid: int) -> list:
        with self._lock:
            user_key = str(uid)
            user = self._data["users"].get(user_key)
            if not user: return []
            bot_ids = user.get("bots_created", [])
            return [dict(self._data["bots"][bid]) for bid in bot_ids if bid in self._data["bots"]]
    
    def update_bot_username(self, bot_id: str, username: str):
        with self._lock:
            if bot_id in self._data["bots"]:
                self._data["bots"][bot_id]["bot_username"] = username
                self._dirty = True
    
    def toggle_bot_active(self, bot_id: str) -> bool:
        with self._lock:
            if bot_id in self._data["bots"]:
                self._data["bots"][bot_id]["is_active"] = not self._data["bots"][bot_id]["is_active"]
                self._dirty = True
                return self._data["bots"][bot_id]["is_active"]
            return False
    
    def delete_bot(self, bot_id: str, owner_uid: int) -> bool:
        with self._lock:
            if bot_id in self._data["bots"]:
                del self._data["bots"][bot_id]
                
                user_key = str(owner_uid)
                if user_key in self._data["users"]:
                    if bot_id in self._data["users"][user_key]["bots_created"]:
                        self._data["users"][user_key]["bots_created"].remove(bot_id)
                
                self._dirty = True
                return True
            return False
    
    def update_bot_stats(self, bot_id: str, messages_received: int = 0, messages_sent: int = 0):
        with self._lock:
            if bot_id in self._data["bots"]:
                self._data["bots"][bot_id]["stats"]["messages_received"] += messages_received
                self._data["bots"][bot_id]["stats"]["messages_sent"] += messages_sent
                self._data["bots"][bot_id]["last_activity"] = now_ir().isoformat()
                self._dirty = True
    
    # ═══════ REFERRAL SYSTEM ═══════
    def find_uid_by_referral(self, ref_code: str) -> Optional[int]:
        with self._lock:
            for uid_str, u in self._data["users"].items():
                if u.get("referral_code") == ref_code:
                    return int(uid_str)
            return None
    
    def apply_referral(self, new_uid: int, ref_uid: int) -> bool:
        if new_uid == ref_uid: return False
        n_key, r_key = str(new_uid), str(ref_uid)
        with self._lock:
            users = self._data["users"]
            if n_key not in users or r_key not in users: return False
            if users[n_key].get("referred_by"): return False
            
            users[n_key]["referred_by"] = r_key
            self._dirty = True
        return True
    
    def get_stats(self) -> dict:
        with self._lock:
            return {
                "total_users": len(self._data["users"]),
                "total_bots": len(self._data["bots"]),
                "active_bots": sum(1 for b in self._data["bots"].values() if b.get("is_active")),
            }

cloud = CloudStorage()

# ══════════════════════════════════════════════════════════════════════
# BOT FACTORY - سیستم ساخت و اجرای ربات‌ها
# ══════════════════════════════════════════════════════════════════════
class BotFactory:
    """مدیریت ساخت و اجرای ربات‌های کاربران"""
    
    def __init__(self):
        self._running_bots: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
    
    async def start_bot(self, bot_id: str, bot_token: str, template_type: str):
        """شروع اجرای یک ربات"""
        async with self._lock:
            if bot_id in self._running_bots:
                log.warning(f"Bot {bot_id} already running")
                return
            
            try:
                # ساخت Application برای ربات
                bot_app = (Application.builder()
                    .token(bot_token)
                    .build())
                
                # ثبت handler ها بر اساس template
                if template_type == "echo":
                    self._setup_echo_bot(bot_app, bot_id)
                elif template_type == "shop":
                    self._setup_shop_bot(bot_app, bot_id)
                elif template_type == "signal":
                    self._setup_signal_bot(bot_app, bot_id)
                elif template_type == "support":
                    self._setup_support_bot(bot_app, bot_id)
                else:
                    self._setup_echo_bot(bot_app, bot_id)
                
                # دریافت username ربات
                await bot_app.initialize()
                me = await bot_app.bot.get_me()
                cloud.update_bot_username(bot_id, me.username or "")
                cloud.save()
                
                # شروع polling در task جداگانه
                task = asyncio.create_task(self._run_bot_polling(bot_app, bot_id))
                self._running_bots[bot_id] = task
                
                log.info(f"✅ Bot {bot_id} (@{me.username}) started successfully")
            except InvalidToken:
                log.error(f"❌ Invalid token for bot {bot_id}")
                await self._notify_owner_invalid_token(bot_id)
            except Exception as e:
                log.error(f"❌ Failed to start bot {bot_id}: {e}")
    
    async def stop_bot(self, bot_id: str):
        """توقف اجرای یک ربات"""
        async with self._lock:
            if bot_id in self._running_bots:
                task = self._running_bots[bot_id]
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                del self._running_bots[bot_id]
                log.info(f"⏹ Bot {bot_id} stopped")
    
    async def _run_bot_polling(self, app: Application, bot_id: str):
        """اجرای polling برای ربات"""
        try:
            await app.start()
            await app.updater.start_polling(
                allowed_updates=["message", "callback_query"],
                drop_pending_updates=True,
            )
            # نگه داشتن task تا زمانی که cancel شود
            while True:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            log.info(f"Bot {bot_id} polling cancelled")
        except Exception as e:
            log.error(f"Bot {bot_id} polling error: {e}")
        finally:
            try:
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
            except:
                pass
    
    def _setup_echo_bot(self, app: Application, bot_id: str):
        """راه‌اندازی Echo Bot"""
        async def echo_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            await update.message.reply_text(
                "🤖 <b>سلام! من یک Echo Bot هستم.</b>\n\n"
                "هر پیامی بفرستی، همون رو برات تکرار می‌کنم!",
                parse_mode=ParseMode.HTML
            )
            cloud.update_bot_stats(bot_id, messages_received=1, messages_sent=1)
        
        async def echo_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if update.message.text:
                await update.message.reply_text(update.message.text)
                cloud.update_bot_stats(bot_id, messages_received=1, messages_sent=1)
        
        app.add_handler(CommandHandler("start", echo_start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_message))
    
    def _setup_shop_bot(self, app: Application, bot_id: str):
        """راه‌اندازی Shop Bot"""
        async def shop_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            await update.message.reply_text(
                "🛒 <b>به فروشگاه خوش آمدید!</b>\n\n"
                "🏪 این یک قالب Shop Bot است.\n"
                "📦 در نسخه‌های آینده قابلیت مدیریت محصولات اضافه می‌شود.",
                parse_mode=ParseMode.HTML
            )
            cloud.update_bot_stats(bot_id, messages_received=1, messages_sent=1)
        
        app.add_handler(CommandHandler("start", shop_start))
    
    def _setup_signal_bot(self, app: Application, bot_id: str):
        """راه‌اندازی Signal Bot"""
        async def signal_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            await update.message.reply_text(
                "📊 <b>به کانال سیگنال خوش آمدید!</b>\n\n"
                "⚡️ این یک قالب Signal Bot است.\n"
                "🔐 در نسخه‌های آینده قابلیت مدیریت اشتراک اضافه می‌شود.",
                parse_mode=ParseMode.HTML
            )
            cloud.update_bot_stats(bot_id, messages_received=1, messages_sent=1)
        
        app.add_handler(CommandHandler("start", signal_start))
    
    def _setup_support_bot(self, app: Application, bot_id: str):
        """راه‌اندازی Support Bot"""
        async def support_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            await update.message.reply_text(
                "🎫 <b>به پشتیبانی خوش آمدید!</b>\n\n"
                "💬 این یک قالب Support Bot است.\n"
                "📝 در نسخه‌های آینده قابلیت تیکت اضافه می‌شود.",
                parse_mode=ParseMode.HTML
            )
            cloud.update_bot_stats(bot_id, messages_received=1, messages_sent=1)
        
        app.add_handler(CommandHandler("start", support_start))
    
    async def _notify_owner_invalid_token(self, bot_id: str):
        """اطلاع‌رسانی به مالک ربات درباره توکن نامعتبر"""
        bot_data = cloud.get_bot(bot_id)
        if not bot_data: return
        
        owner_uid = bot_data.get("owner_uid")
        try:
            from telegram import Bot
            temp_bot = Bot(token=PLATFORM_BOT_TOKEN)
            await temp_bot.send_message(
                chat_id=owner_uid,
                text=f"⚠️ <b>خطا در ربات شما!</b>\n\n"
                     f"🤖 ربات با ID <code>{bot_id}</code> به دلیل توکن نامعتبر متوقف شد.\n"
                     f"لطفاً توکن جدید از BotFather دریافت کنید و ربات را دوباره فعال کنید.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            log.error(f"Failed to notify owner: {e}")
    
    async def start_all_active_bots(self):
        """شروع همه ربات‌های فعال"""
        all_bots = cloud.get_all_bots()
        for bot_data in all_bots:
            if bot_data.get("is_active"):
                await self.start_bot(
                    bot_data["bot_id"],
                    bot_data["bot_token"],
                    bot_data["template_type"]
                )
    
    async def stop_all_bots(self):
        """توقف همه ربات‌ها"""
        for bot_id in list(self._running_bots.keys()):
            await self.stop_bot(bot_id)

bot_factory = BotFactory()

# ══════════════════════════════════════════════════════════════════════
# MEMBERSHIP CHECK
# ══════════════════════════════════════════════════════════════════════
async def check_membership(bot, uid: int) -> bool:
    if not FORCE_JOIN_CHANNEL: return True
    try:
        member = await bot.get_chat_member(chat_id=FORCE_JOIN_CHANNEL, user_id=uid)
        return member.status not in ("left", "kicked", "banned")
    except: return True

# ══════════════════════════════════════════════════════════════════════
# KEYBOARDS
# ══════════════════════════════════════════════════════════════════════
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
         InlineKeyboardButton("🤖 ربات‌های من", callback_data="menu:mybots")],
        [InlineKeyboardButton("➕ ساخت ربات جدید", callback_data="menu:create_bot"),
         InlineKeyboardButton("🎁 دریافت جایزه روزانه", callback_data="menu:daily")],
        [InlineKeyboardButton("👥 دعوت دوستان", callback_data="menu:invite"),
         InlineKeyboardButton("📊 تراکنش‌ها", callback_data="menu:transactions")],
        [InlineKeyboardButton("ℹ️ راهنما", callback_data="menu:help")],
    ]
    if uid and is_admin(uid):
        rows.append([InlineKeyboardButton("👑 پنل ادمین", callback_data="admin:panel:0")])
    return InlineKeyboardMarkup(rows)

def kb_back(dest: str = "home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"menu:{dest}")]])

def kb_templates() -> InlineKeyboardMarkup:
    rows = []
    for template_id, template in AVAILABLE_TEMPLATES.items():
        premium_badge = " 💎" if template.get("is_premium") else ""
        rows.append([InlineKeyboardButton(
            f"{template['name']}{premium_badge} — {template['cost_gc']} GC",
            callback_data=f"create:{template_id}"
        )])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)

def kb_bot_management(bot_id: str, is_active: bool) -> InlineKeyboardMarkup:
    status_text = "⏹ غیرفعال کردن" if is_active else "▶️ فعال کردن"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(status_text, callback_data=f"bot:toggle:{bot_id}")],
        [InlineKeyboardButton("🗑 حذف ربات", callback_data=f"bot:delete:{bot_id}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="menu:mybots")],
    ])

# ══════════════════════════════════════════════════════════════════════
# TEXT BUILDERS
# ══════════════════════════════════════════════════════════════════════
def build_welcome_text() -> str:
    return (
        "🤖✨ <b>به GramBotSaz خوش اومدی!</b> ✨🤖\n\n"
        "<blockquote expandable>"
        "اینجا می‌تونی بدون نیاز به کدنویسی، ربات تلگرام خودت رو بسازی و مدیریت کنی!\n"
        "</blockquote>"
        "🔥 <b>ویژگی‌های پلتفرم:</b>\n"
        "  ⚡️ ساخت ربات در کمتر از ۱ دقیقه\n"
        "  🎨 قالب‌های آماده (فروشگاه، سیگنال، پشتیبانی)\n"
        "  💰 سیستم اقتصادی با GramCoin\n"
        "  🎁 جایزه روزانه و سیستم رفرال\n\n"
        "از منوی زیر شروع کن 👇"
    )

def build_profile_text(uid: int, u: dict) -> str:
    full = u.get("full_name", "").strip() or u.get("username", "") or "—"
    bots_count = len(u.get("bots_created", []))
    
    daily_status = "✅ قابل دریافت" if cloud.can_claim_daily(uid) else "⏳ فردا دوباره امتحان کن"
    
    return (
        f"👤 <b>پروفایل شما</b>\n\n"
        f"📛 نام: <b>{full}</b>\n"
        f"🆔 آیدی: <code>{uid}</code>\n\n"
        f"💰 <b>موجودی GramCoin:</b> <code>{u.get('gramcoin_balance', 0)} GC</code>\n"
        f"📈 <b>کل درآمد:</b> <code>{u.get('total_earned_gc', 0)} GC</code>\n\n"
        f"🤖 <b>تعداد ربات‌ها:</b> {bots_count}\n"
        f"👥 <b>زیرمجموعه‌ها:</b> {sum(1 for x in cloud.get_all_users() if x.get('referred_by') == str(uid))}\n\n"
        f"🎁 <b>جایزه روزانه:</b> {daily_status}"
    )

def build_mybots_text(uid: int) -> tuple:
    """(متن، کیبورد) لیست ربات‌های کاربر"""
    bots = cloud.get_user_bots(uid)
    
    if not bots:
        text = (
            "🤖 <b>ربات‌های شما</b>\n\n"
            "هنوز رباتی نساخته‌اید.\n\n"
            "💡 برای ساخت اولین ربات، روی دکمه زیر کلیک کنید:"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ ساخت ربات جدید", callback_data="menu:create_bot")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="menu:home")],
        ])
        return text, kb
    
    text = "🤖 <b>ربات‌های شما</b>\n\n"
    rows = []
    for bot in bots:
        status = "🟢 فعال" if bot.get("is_active") else "🔴 غیرفعال"
        template = AVAILABLE_TEMPLATES.get(bot.get("template_type", "echo"), {})
        template_name = template.get("name", "Unknown")
        username = f"@{bot.get('bot_username')}" if bot.get("bot_username") else "—"
        
        text += (
            f"• <b>{template_name}</b>\n"
            f"  🆔 <code>{bot['bot_id']}</code> | {username}\n"
            f"  {status}\n\n"
        )
        rows.append([InlineKeyboardButton(
            f"⚙️ مدیریت {bot['bot_id'][:8]}",
            callback_data=f"bot:manage:{bot['bot_id']}"
        )])
    
    rows.append([InlineKeyboardButton("➕ ساخت ربات جدید", callback_data="menu:create_bot")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu:home")])
    
    return text, InlineKeyboardMarkup(rows)

def build_create_bot_text() -> str:
    text = "🎨 <b>انتخاب قالب ربات</b>\n\nیک قالب برای ربات خود انتخاب کنید:\n\n"
    
    for template_id, template in AVAILABLE_TEMPLATES.items():
        premium_badge = " 💎 پرمیوم" if template.get("is_premium") else ""
        text += (
            f"<b>{template['name']}</b>{premium_badge}\n"
            f"📝 {template['description']}\n"
            f"💰 هزینه: <b>{template['cost_gc']} GC</b>\n"
            f"✨ امکانات: {', '.join(template['features'])}\n\n"
        )
    
    return text

def build_referral_text(uid: int, bot_username: str) -> str:
    u = cloud.ensure_user(uid)
    link = f"https://t.me/{bot_username}?start=ref_{u['referral_code']}"
    referrals_count = sum(1 for x in cloud.get_all_users() if x.get("referred_by") == str(uid))
    
    return (
        "🎁 <b>دعوت دوستان و کسب GramCoin</b>\n\n"
        f"🔗 <b>لینک اختصاصی شما:</b>\n<code>{link}</code>\n\n"
        "<blockquote expandable>"
        f"✅ به ازای هر دوستی که از طریق لینک تو وارد شود، <b>{REFERRAL_BONUS_GC} GC</b> به صورت آنی به حسابت اضافه می‌شود!\n"
        "با جمع‌آوری GC می‌توانی ربات‌های بیشتری بسازی و آن‌ها را فعال نگه داری.\n"
        "</blockquote>"
        f"👥 تعداد دعوت‌های موفق: <b>{referrals_count}</b>"
    )

def build_help_text() -> str:
    return (
        "📖 <b>راهنمای استفاده از GramBotSaz</b>\n\n"
        "<blockquote expandable>"
        "🤖 <b>ساخت ربات:</b>\n"
        "۱. روی «ساخت ربات جدید» کلیک کنید\n"
        "۲. یک قالب انتخاب کنید\n"
        "۳. توکن ربات را از BotFather دریافت کنید\n"
        "۴. توکن را در ربات وارد کنید\n"
        "۵. ربات شما فعال می‌شود!\n\n"
        "💰 <b>GramCoin (GC) چیست؟</b>\n"
        "ارز داخلی پلتفرم است که برای ساخت و نگهداری ربات‌ها استفاده می‌شود.\n\n"
        "🎁 <b>روش‌های کسب GC:</b>\n"
        "• جایزه روزانه: ۵ GC\n"
        "• دعوت دوستان: ۵۰ GC به ازای هر نفر\n\n"
        "💸 <b>هزینه نگهداری:</b>\n"
        f"هر ربات فعال روزانه {BOT_DAILY_COST_GC} GC هزینه دارد.\n"
        "اگر موجودی کافی نباشد، ربات به صورت خودکار غیرفعال می‌شود.\n"
        "</blockquote>"
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
                    text="⛔️ <b>برای دریافت پاداش دعوت باید عضو کانال باشی!</b>",
                    parse_mode=ParseMode.HTML, reply_markup=kb_join_required())
                return
            
            ref_uid = cloud.find_uid_by_referral(ref_code)
            if ref_uid and cloud.apply_referral(uid, ref_uid):
                cloud.add_gramcoin(ref_uid, REFERRAL_BONUS_GC, "referral_bonus")
                cloud.save(force=True)
                try:
                    await ctx.bot.send_message(chat_id=ref_uid, parse_mode=ParseMode.HTML,
                        text=f"🎉 <b>یک دعوت موفق!</b>\n👤 کاربر جدید وارد پلتفرم شد.\n⭐️ <b>+{REFERRAL_BONUS_GC} GC</b> به موجودی شما اضافه شد.")
                except: pass
    
    if not await check_membership(ctx.bot, uid):
        await update.effective_message.reply_text(
            "⛔️ <b>برای استفاده از ربات باید عضو کانال ما باشی!</b>",
            parse_mode=ParseMode.HTML, reply_markup=kb_join_required())
        return
    
    await update.effective_message.reply_text(build_welcome_text(), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
    cloud.save()

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """لغو عملیات در حال انتظار"""
    ctx.user_data.pop("awaiting", None)
    ctx.user_data.pop("pending_bot_template", None)
    await update.message.reply_text("❌ <b>عملیات لغو شد.</b>", parse_mode=ParseMode.HTML, reply_markup=kb_main(update.effective_user.id))

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
                await q.edit_message_text("⛔️ <b>ابتدا عضو کانال شو!</b>", parse_mode=ParseMode.HTML, reply_markup=kb_join_required())
            except: pass
            return
    
    if data == "check_join":
        if await check_membership(q.get_bot(), uid):
            await q.answer("✅ تایید شد!")
            pending_ref = ctx.user_data.get("pending_referral")
            if pending_ref:
                ref_uid = cloud.find_uid_by_referral(pending_ref)
                if ref_uid and cloud.apply_referral(uid, ref_uid):
                    cloud.add_gramcoin(ref_uid, REFERRAL_BONUS_GC, "referral_bonus")
                    try: await q.get_bot().send_message(chat_id=ref_uid, parse_mode=ParseMode.HTML, text=f"🎉 یک دعوت موفق! +{REFERRAL_BONUS_GC} GC")
                    except: pass
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
        elif data == "menu:mybots":
            text, kb = build_mybots_text(uid)
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif data == "menu:create_bot":
            await q.edit_message_text(build_create_bot_text(), parse_mode=ParseMode.HTML, reply_markup=kb_templates())
        elif data == "menu:daily":
            if cloud.can_claim_daily(uid):
                cloud.claim_daily_reward(uid, DAILY_REWARD_GC)
                cloud.save(force=True)
                await q.edit_message_text(
                    f"🎁 <b>جایزه روزانه دریافت شد!</b>\n\n"
                    f"⭐️ <b>+{DAILY_REWARD_GC} GC</b> به موجودی شما اضافه شد.\n"
                    f"💰 موجودی جدید: <code>{u.get('gramcoin_balance', 0) + DAILY_REWARD_GC} GC</code>\n\n"
                    f"⏰ فردا دوباره مراجعه کنید!",
                    parse_mode=ParseMode.HTML, reply_markup=kb_back())
            else:
                await q.answer("⏳ جایزه روزانه قبلاً دریافت شده. فردا دوباره امتحان کن!", show_alert=True)
        elif data == "menu:invite":
            bot_username = (await ctx.bot.get_me()).username
            await q.edit_message_text(build_referral_text(uid, bot_username), parse_mode=ParseMode.HTML, reply_markup=kb_back())
        elif data == "menu:transactions":
            transactions = [tx for tx in cloud._data.get("transactions", []) if tx.get("uid") == uid][-10:]
            if not transactions:
                text = "📊 <b>تراکنش‌های شما</b>\n\nهنوز تراکنشی ندارید."
            else:
                text = "📊 <b>آخرین تراکنش‌های شما:</b>\n\n"
                for tx in reversed(transactions):
                    text += f"• {tx['description']}\n  📅 {tx['timestamp'][:16]}\n\n"
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_back())
        elif data == "menu:help": await q.edit_message_text(build_help_text(), parse_mode=ParseMode.HTML, reply_markup=kb_back())
        return
    
    # Create Bot Flow
    if data.startswith("create:"):
        template_id = data.split(":")[1]
        template = AVAILABLE_TEMPLATES.get(template_id)
        if not template:
            await q.answer("قالب یافت نشد", show_alert=True)
            return
        
        cost = template.get("cost_gc", 0)
        u = cloud.get_user(uid)
        
        if cost > 0 and u.get("gramcoin_balance", 0) < cost:
            await q.answer(f"❌ موجودی GC کافی نیست! نیاز به {cost} GC دارید.", show_alert=True)
            return
        
        ctx.user_data["pending_bot_template"] = template_id
        ctx.user_data["awaiting"] = "bot_token"
        await q.answer()
        await q.edit_message_text(
            f"🤖 <b>ساخت {template['name']}</b>\n\n"
            f"💰 هزینه: <b>{cost} GC</b>\n\n"
            f"📝 <b>مراحل:</b>\n"
            f"۱. به @BotFather در تلگرام مراجعه کنید\n"
            f"۲. دستور /newbot را بزنید\n"
            f"۳. نام و یوزرنیم ربات را انتخاب کنید\n"
            f"۴. توکن دریافتی را اینجا ارسال کنید\n\n"
            f"🔑 <b>لطفاً توکن ربات را ارسال کنید:</b>\n"
            f"(برای لغو /cancel بزنید)",
            parse_mode=ParseMode.HTML)
        return
    
    # Bot Management
    if data.startswith("bot:manage:"):
        bot_id = data.split(":")[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True)
            return
        
        await q.answer()
        template = AVAILABLE_TEMPLATES.get(bot.get("template_type", "echo"), {})
        status = "🟢 فعال" if bot.get("is_active") else "🔴 غیرفعال"
        username = f"@{bot.get('bot_username')}" if bot.get("bot_username") else "—"
        
        text = (
            f"⚙️ <b>مدیریت ربات</b>\n\n"
            f"🤖 <b>نوع:</b> {template.get('name', 'Unknown')}\n"
            f"🆔 <b>ID:</b> <code>{bot_id}</code>\n"
            f"📛 <b>یوزرنیم:</b> {username}\n"
            f"📊 <b>وضعیت:</b> {status}\n\n"
            f"📨 <b>آمار:</b>\n"
            f"• پیام‌های دریافتی: {bot.get('stats', {}).get('messages_received', 0)}\n"
            f"• پیام‌های ارسالی: {bot.get('stats', {}).get('messages_sent', 0)}\n"
        )
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_bot_management(bot_id, bot.get("is_active", False)))
        return
    
    if data.startswith("bot:toggle:"):
        bot_id = data.split(":")[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True)
            return
        
        await q.answer("در حال تغییر وضعیت...")
        new_status = cloud.toggle_bot_active(bot_id)
        cloud.save(force=True)
        
        if new_status:
            await bot_factory.start_bot(bot_id, bot["bot_token"], bot["template_type"])
            msg = "✅ ربات فعال شد!"
        else:
            await bot_factory.stop_bot(bot_id)
            msg = "⏹ ربات غیرفعال شد!"
        
        await q.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb_back("mybots"))
        return
    
    if data.startswith("bot:delete:"):
        bot_id = data.split(":")[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True)
            return
        
        await q.answer("در حال حذف...")
        await bot_factory.stop_bot(bot_id)
        cloud.delete_bot(bot_id, uid)
        cloud.save(force=True)
        await q.edit_message_text("🗑 ربات با موفقیت حذف شد.", parse_mode=ParseMode.HTML, reply_markup=kb_back("mybots"))
        return
    
    # Admin Panel
    if not is_admin(uid):
        await q.answer("⛔️ دسترسی ندارید.", show_alert=True)
        return
    
    if data.startswith("admin:panel:"):
        await q.answer()
        stats = cloud.get_stats()
        text = (
            f"👑 <b>پنل مدیریت GramBotSaz</b>\n\n"
            f"👥 کل کاربران: {stats['total_users']}\n"
            f"🤖 کل ربات‌ها: {stats['total_bots']}\n"
            f"🟢 ربات‌های فعال: {stats['active_bots']}\n\n"
            f"یک گزینه را انتخاب کنید:"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 لیست کاربران", callback_data="admin:users:0")],
            [InlineKeyboardButton("🤖 لیست ربات‌ها", callback_data="admin:bots:0")],
            [InlineKeyboardButton("📢 ارسال همگانی", callback_data="admin:broadcast:prompt")],
            [InlineKeyboardButton("🔙 بازگشت به منو", callback_data="menu:home")],
        ])
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return
    
    if data.startswith("admin:users:"):
        page = int(data.split(":")[-1])
        await q.answer()
        users = sorted(cloud.get_all_users(), key=lambda u: u.get("gramcoin_balance", 0), reverse=True)
        total = len(users)
        start = page * ADMIN_PAGE_SIZE
        end = min(start + ADMIN_PAGE_SIZE, total)
        text = f"👥 <b>لیست کاربران</b> (صفحه {page + 1})\n\n"
        rows = []
        for u in users[start:end]:
            u_id = u.get("uid")
            name = u.get("full_name", "—")
            gc = u.get("gramcoin_balance", 0)
            text += f"• <b>{name}</b> | <code>{u_id}</code>\n  💰 {gc} GC\n"
            rows.append([InlineKeyboardButton(f"🎁 +50 GC به {name[:10]}", callback_data=f"admin:givegc:{u_id}:50")])
        
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"admin:users:{page - 1}"))
        if end < total: nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"admin:users:{page + 1}"))
        if nav: rows.append(nav)
        rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin:panel:0")])
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return
    
    if data.startswith("admin:givegc:"):
        _, _, u_id, amount = data.split(":")
        cloud.add_gramcoin(int(u_id), int(amount), "admin_grant")
        cloud.save(force=True)
        await q.answer(f"✅ {amount} GC به کاربر {u_id} اضافه شد.", show_alert=True)
        return
    
    if data.startswith("admin:bots:"):
        page = int(data.split(":")[-1])
        await q.answer()
        bots = cloud.get_all_bots()
        total = len(bots)
        start = page * ADMIN_PAGE_SIZE
        end = min(start + ADMIN_PAGE_SIZE, total)
        text = f"🤖 <b>لیست ربات‌ها</b> (صفحه {page + 1})\n\n"
        for b in bots[start:end]:
            status = "🟢" if b.get("is_active") else "🔴"
            username = f"@{b.get('bot_username')}" if b.get("bot_username") else "—"
            text += f"{status} <code>{b['bot_id']}</code> | {username}\n  Owner: <code>{b.get('owner_uid')}</code>\n\n"
        
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"admin:bots:{page - 1}"))
        if end < total: nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"admin:bots:{page + 1}"))
        if nav:
            rows = [nav]
        else:
            rows = []
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
        await cmd_cancel(update, ctx)
        return
    
    # User creating bot - receiving token
    if awaiting == "bot_token":
        template_id = ctx.user_data.get("pending_bot_template")
        ctx.user_data.pop("awaiting", None)
        ctx.user_data.pop("pending_bot_template", None)
        
        bot_token = text_in.strip()
        
        # اعتبارسنجی توکن
        if not bot_token or ":" not in bot_token or len(bot_token) < 40:
            await update.message.reply_text(
                "❌ <b>توکن نامعتبر است!</b>\n\n"
                "توکن باید شبیه این باشد:\n"
                "<code>1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ</code>\n\n"
                "برای شروع مجدد، روی دکمه زیر کلیک کنید:",
                parse_mode=ParseMode.HTML, reply_markup=kb_back("create_bot"))
            return
        
        # تست توکن
        try:
            temp_bot = Bot(token=bot_token)
            me = await temp_bot.get_me()
        except InvalidToken:
            await update.message.reply_text(
                "❌ <b>توکن نامعتبر است!</b>\n\n"
                "لطفاً از BotFather توکن صحیح را دریافت کنید.",
                parse_mode=ParseMode.HTML, reply_markup=kb_back("create_bot"))
            return
        except Exception as e:
            await update.message.reply_text(
                f"❌ <b>خطا در بررسی توکن:</b>\n<code>{str(e)[:100]}</code>",
                parse_mode=ParseMode.HTML, reply_markup=kb_back("create_bot"))
            return
        
        # کسر هزینه
        template = AVAILABLE_TEMPLATES.get(template_id, {})
        cost = template.get("cost_gc", 0)
        if cost > 0:
            if not cloud.deduct_gramcoin(uid, cost, f"create_bot_{template_id}"):
                await update.message.reply_text(
                    f"❌ <b>موجودی GC کافی نیست!</b>\n\n"
                    f"💰 هزینه: {cost} GC\n"
                    f"💼 موجودی شما: {cloud.get_user(uid).get('gramcoin_balance', 0)} GC",
                    parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
                return
        
        # ساخت ربات
        bot_data = cloud.create_bot(uid, bot_token, template_id)
        cloud.save(force=True)
        
        # شروع ربات
        await bot_factory.start_bot(bot_data["bot_id"], bot_token, template_id)
        
        await update.message.reply_text(
            f"✅ <b>ربات با موفقیت ساخته شد!</b>\n\n"
            f"🤖 <b>نوع:</b> {template.get('name')}\n"
            f"🆔 <b>ID:</b> <code>{bot_data['bot_id']}</code>\n"
            f"📛 <b>یوزرنیم:</b> @{me.username}\n"
            f"🟢 <b>وضعیت:</b> فعال\n\n"
            f"💰 {cost} GC از حساب شما کسر شد.\n\n"
            f"🎉 ربات شما اکنون فعال است و می‌توانید از آن استفاده کنید!",
            parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        return
    
    if not is_admin(uid): return
    
    # Admin broadcast
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
# BACKGROUND TASKS
# ══════════════════════════════════════════════════════════════════════
async def daily_cost_watcher(app: Application):
    """کسر هزینه روزانه ربات‌ها"""
    while True:
        try:
            await asyncio.sleep(3600)  # هر ساعت چک کن
            
            all_bots = cloud.get_all_bots()
            for bot_data in all_bots:
                if not bot_data.get("is_active"): continue
                
                owner_uid = bot_data.get("owner_uid")
                bot_id = bot_data.get("bot_id")
                
                # کسر هزینه
                if not cloud.deduct_gramcoin(owner_uid, BOT_DAILY_COST_GC, f"bot_daily_cost_{bot_id}"):
                    # موجودی کافی نیست، ربات را غیرفعال کن
                    cloud.toggle_bot_active(bot_id)
                    await bot_factory.stop_bot(bot_id)
                    
                    # اطلاع به مالک
                    try:
                        await app.bot.send_message(
                            chat_id=owner_uid,
                            text=f"⚠️ <b>ربات شما غیرفعال شد!</b>\n\n"
                                 f"🤖 ربات <code>{bot_id}</code> به دلیل عدم موجودی کافی متوقف شد.\n"
                                 f"💰 هزینه روزانه: {BOT_DAILY_COST_GC} GC\n\n"
                                 f"برای فعال‌سازی مجدد، موجودی خود را شارژ کنید.",
                            parse_mode=ParseMode.HTML
                        )
                    except: pass
            
            cloud.save()
        except asyncio.CancelledError: break
        except Exception as e: log.error(f"daily_cost_watcher error: {e}")

async def autosave_loop(app: Application):
    while True:
        try:
            await asyncio.sleep(300)
            cloud.save()
        except asyncio.CancelledError: break
        except Exception as e: log.error(f"autosave_loop error: {e}")

async def on_startup(app: Application):
    global PLATFORM_BOT_USERNAME
    try:
        me = await app.bot.get_me()
        PLATFORM_BOT_USERNAME = me.username or ""
    except: pass
    
    # شروع همه ربات‌های فعال
    await bot_factory.start_all_active_bots()
    
    asyncio.create_task(daily_cost_watcher(app))
    asyncio.create_task(autosave_loop(app))
    log.info("GramBotSaz Platform ready ✅")

async def on_shutdown(app: Application):
    await bot_factory.stop_all_bots()
    cloud.save(force=True)
    log.info("shutdown")

# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════
def main():
    if not PLATFORM_BOT_TOKEN:
        log.critical("PLATFORM_BOT_TOKEN تنظیم نشده!")
        sys.exit(1)
    
    cloud.load()
    app = (Application.builder().token(PLATFORM_BOT_TOKEN)
           .post_init(on_startup).post_shutdown(on_shutdown).build())
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_message))
    
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
        def index(): return "🤖 GramBotSaz Platform Alive!", 200
        @flask_app.route(f"/{PLATFORM_BOT_TOKEN}", methods=["POST"])
        def webhook():
            try:
                update = Update.de_json(request.get_json(force=True), app.bot)
                asyncio.run_coroutine_threadsafe(app.process_update(update), main_loop)
            except Exception as e: log.error(f"Webhook error: {e}")
            return "OK", 200
        
        async def setup():
            full_url = webhook_url.rstrip("/") + "/" + PLATFORM_BOT_TOKEN
            await app.bot.delete_webhook(drop_pending_updates=True)
            await asyncio.sleep(1)
            await app.bot.set_webhook(url=full_url, allowed_updates=["message", "callback_query"], drop_pending_updates=True)
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
        app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)

if __name__ == "__main__":
    main()
