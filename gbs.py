#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GramBotSaz v4.1 — پلتفرم حرفه‌ای ربات‌ساز تلگرام (نسخه نهایی)
════════════════════════════════════════════════════════════════════
✅ همه باگ‌ها رفع شده
✅ منوی ۴ دکمه‌ای تمیز
✅ عکس welcome با file_id معتبر
✅ کیبورد فقط یک بار هنگام /start
✅ پاسخ ادمین به تیکت کاملاً کار می‌کند
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
from typing import Optional, Dict, List, Any, Callable
from flask import Flask, request

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
)
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
SIGNUP_BONUS_GC = 50
REFERRAL_BONUS_GC = 50
DAILY_REWARD_GC = 5
SUBSCRIPTION_WEEKLY_GC = 50
SUBSCRIPTION_MONTHLY_GC = 150
SUBSCRIPTION_WEEKLY_DAYS = 7
SUBSCRIPTION_MONTHLY_DAYS = 30
TRIAL_DAYS = 3
MAX_FREE_BOTS = 1
IRAN_TZ = timezone(timedelta(hours=3, minutes=30))
PLATFORM_BOT_USERNAME = ""
ADMIN_PAGE_SIZE = 8

# ══════════════════════════════════════════════════════════════════════
# PLUGINS REGISTRY
# ══════════════════════════════════════════════════════════════════════
AVAILABLE_PLUGINS = {
    "welcome": {
        "name": "👋 خوش‌آمد پیشرفته",
        "description": "پیام خوش‌آمد با عکس، متن سفارشی و دکمه‌های چندتایی",
        "cost_gc": 0,
        "is_premium": False,
        "category": "basic",
        "has_settings": True,
    },
    "support": {
        "name": "🎫 پشتیبانی چت زنده",
        "description": "سیستم چت زنده حرفه‌ای با سازنده ربات",
        "cost_gc": 0,
        "is_premium": False,
        "category": "basic",
        "has_settings": True,
    },
    "keyboard": {
        "name": "⌨️ کیبورد اختصاصی",
        "description": "ساخت دکمه‌های کیبورد زیر صفحه چت ربات شما",
        "cost_gc": 0,
        "is_premium": False,
        "category": "basic",
        "has_settings": True,
    },
    "echo": {
        "name": "🤖 Echo",
        "description": "تکرار پیام‌های کاربران",
        "cost_gc": 0,
        "is_premium": False,
        "category": "basic",
        "has_settings": False,
    },
    "auto_reply": {
        "name": "💬 پاسخ خودکار",
        "description": "پاسخ به کلمات کلیدی خاص",
        "cost_gc": 80, "is_premium": True, "category": "advanced", "has_settings": True,
    },
    "broadcast": {
        "name": "📢 ارسال همگانی",
        "description": "ارسال به همه کاربران ربات شما",
        "cost_gc": 100, "is_premium": True, "category": "advanced", "has_settings": False,
    },
    "shop": {
        "name": "🛒 فروشگاه",
        "description": "فروشگاه آنلاین با سبد خرید",
        "cost_gc": 150, "is_premium": True, "category": "advanced", "has_settings": True,
    },
    "form_builder": {
        "name": "📝 فرم‌ساز",
        "description": "ساخت فرم و جمع‌آوری اطلاعات",
        "cost_gc": 80, "is_premium": True, "category": "advanced", "has_settings": True,
    },
    "referral": {
        "name": "👥 زیرمجموعه‌گیری",
        "description": "سیستم رفرال برای ربات شما",
        "cost_gc": 120, "is_premium": True, "category": "advanced", "has_settings": True,
    },
    "payment": {
        "name": "💳 درگاه پرداخت",
        "description": "دریافت پرداخت از کاربران",
        "cost_gc": 180, "is_premium": True, "category": "advanced", "has_settings": True,
    },
    "stats": {
        "name": "📊 آمار پیشرفته",
        "description": "آمار کامل و نموداری",
        "cost_gc": 50, "is_premium": True, "category": "advanced", "has_settings": False,
    },
}

TRIAL_DEFAULT_PLUGINS = ["welcome", "support", "keyboard"]

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
            "tickets": {},
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
                    self._data.setdefault("tickets", {})
                    self._data.setdefault("transactions", [])
                    for u in self._data["users"].values():
                        u.setdefault("signup_bonus_claimed", False)
                    for b in self._data["bots"].values():
                        b.setdefault("enabled_plugins", [])
                        b.setdefault("purchased_plugins", [])
                        b.setdefault("plugin_settings", {})
                        b.setdefault("subscription_expiry", None)
                        b.setdefault("subscription_plan", None)
                        b.setdefault("is_trial", False)
                        b.setdefault("trial_used", False)
                        b.setdefault("bot_users", [])
                        b.setdefault("next_ticket_id", 1001)
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
    
    def ensure_user(self, uid: int, username: str = "", full_name: str = "") -> tuple:
        key = str(uid)
        is_new = False
        with self._lock:
            if key not in self._data["users"]:
                is_new = True
                ref_code = hashlib.md5(f"{uid}{time.time()}".encode()).hexdigest()[:8]
                self._data["users"][key] = {
                    "uid": uid, "username": username or "", "full_name": full_name or "",
                    "gramcoin_balance": 0, "total_earned_gc": 0,
                    "referral_code": ref_code, "referred_by": None,
                    "created_at": now_ir().isoformat(),
                    "last_daily_claim": None, "bots_created": [],
                    "signup_bonus_claimed": False,
                }
                self._dirty = True
            else:
                u = self._data["users"][key]
                if username and u.get("username") != username:
                    u["username"] = username; self._dirty = True
                if full_name and u.get("full_name") != full_name:
                    u["full_name"] = full_name; self._dirty = True
                u.setdefault("signup_bonus_claimed", False)
            return dict(self._data["users"][key]), is_new
    
    def claim_signup_bonus(self, uid: int) -> bool:
        key = str(uid)
        with self._lock:
            u = self._data["users"].get(key)
            if not u or u.get("signup_bonus_claimed"): return False
            u["signup_bonus_claimed"] = True
            self._dirty = True
        return self.add_gramcoin(uid, SIGNUP_BONUS_GC, "signup_bonus")
    
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
                    "tx_id": tx_id, "uid": uid, "amount_gc": amount,
                    "tx_type": reason, "description": f"+{amount} GC ({reason})",
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
                    "tx_id": tx_id, "uid": uid, "amount_gc": -amount,
                    "tx_type": reason, "description": f"-{amount} GC ({reason})",
                    "timestamp": now_ir().isoformat(),
                })
                return True
            return False
    
    def can_claim_daily(self, uid: int) -> bool:
        u = self.get_user(uid)
        if not u or not u.get("last_daily_claim"): return True
        try:
            last_claim = datetime.fromisoformat(u["last_daily_claim"])
            return (now_ir() - last_claim).days >= 1
        except: return True
    
    def claim_daily_reward(self, uid: int, amount: int) -> bool:
        if not self.can_claim_daily(uid): return False
        key = str(uid)
        with self._lock:
            if key in self._data["users"]:
                self._data["users"][key]["last_daily_claim"] = now_ir().isoformat()
                self._dirty = True
        return self.add_gramcoin(uid, amount, "daily_reward")
    
    def create_bot(self, owner_uid: int, bot_token: str) -> Optional[dict]:
        bot_id = hashlib.md5(f"{owner_uid}{bot_token}{time.time()}".encode()).hexdigest()[:12]
        with self._lock:
            trial_expiry = (now_ir() + timedelta(days=TRIAL_DAYS)).isoformat()
            self._data["bots"][bot_id] = {
                "bot_id": bot_id,
                "owner_uid": owner_uid,
                "bot_token": bot_token,
                "bot_username": "",
                "is_active": True,
                "is_trial": True,
                "trial_used": True,
                "created_at": now_ir().isoformat(),
                "last_activity": None,
                "enabled_plugins": list(TRIAL_DEFAULT_PLUGINS),
                "purchased_plugins": [],
                "plugin_settings": {
                    "welcome": {
                        "message": (
                            "👋 <b>سلام! به ربات ما خوش آمدید!</b>\n\n"
                            "🤖 از اینکه ما را انتخاب کردید سپاسگزاریم.\n\n"
                            "💡 <b>چگونه با ما ارتباط بگیرید:</b>\n"
                            "• از دکمه‌های زیر صفحه استفاده کنید\n"
                            "• برای پشتیبانی /support را بزنید\n\n"
                            "✨ امیدواریم تجربه خوبی داشته باشید!"
                        ),
                        "photo_file_id": None,
                        "buttons": [
                            [
                                {"text": "🎫 پشتیبانی", "type": "support"},
                                {"text": "ℹ️ درباره ما", "type": "text", "value": "ما یک تیم حرفه‌ای هستیم!"},
                            ]
                        ],
                    },
                    "support": {
                        "admin_message": "📩 <b>به پشتیبانی خوش آمدید!</b>\n\nپیام خود را برای تیم پشتیبانی ارسال کنید. در سریع‌ترین زمان ممکن پاسخ می‌دهیم.",
                        "confirm_message": "✅ <b>پیام شما با موفقیت ارسال شد!</b>\n\nتیم پشتیبانی در اسرع وقت پاسخ خواهد داد.",
                        "end_chat_message": "🔚 <b>چت با پشتیبانی پایان یافت.</b>\n\nبرای شروع مجدد، /support را بزنید.",
                    },
                    "keyboard": {
                        "buttons": [
                            [{"text": "🎫 پشتیبانی", "action": "command", "value": "/support"}],
                            [{"text": "ℹ️ درباره ما", "action": "text", "value": "ما یک تیم حرفه‌ای هستیم!"}],
                        ],
                        "resize_keyboard": True,
                        "one_time_keyboard": False,
                        "persistent": True,
                    },
                },
                "subscription_expiry": trial_expiry,
                "subscription_plan": "trial",
                "bot_users": [],
                "next_ticket_id": 1001,
                "stats": {"messages_received": 0, "messages_sent": 0, "unique_users": 0},
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
    
    def add_bot_user(self, bot_id: str, user_id: int, username: str = "", full_name: str = ""):
        with self._lock:
            if bot_id in self._data["bots"]:
                bot_users = self._data["bots"][bot_id].setdefault("bot_users", [])
                for bu in bot_users:
                    if bu["user_id"] == user_id: return
                bot_users.append({
                    "user_id": user_id, "username": username, "full_name": full_name,
                    "joined_at": now_ir().isoformat(),
                })
                self._data["bots"][bot_id]["stats"]["unique_users"] = len(bot_users)
                self._dirty = True
    
    def activate_subscription(self, bot_id: str, plan: str, days: int) -> Optional[datetime]:
        with self._lock:
            if bot_id not in self._data["bots"]: return None
            bot = self._data["bots"][bot_id]
            if bot.get("subscription_expiry"):
                try:
                    current = datetime.fromisoformat(bot["subscription_expiry"])
                    base = current if current > now_ir() else now_ir()
                except: base = now_ir()
            else: base = now_ir()
            new_expiry = base + timedelta(days=days)
            bot["subscription_expiry"] = new_expiry.isoformat()
            bot["subscription_plan"] = plan
            bot["is_active"] = True
            bot["is_trial"] = False
            free_plugins = [pid for pid, p in AVAILABLE_PLUGINS.items() if not p.get("is_premium")]
            for plugin in free_plugins:
                if plugin not in bot["enabled_plugins"]:
                    bot["enabled_plugins"].append(plugin)
            self._dirty = True
            return new_expiry
    
    def check_subscription_expired(self, bot_id: str) -> bool:
        with self._lock:
            bot = self._data["bots"].get(bot_id)
            if not bot or not bot.get("subscription_expiry"): return True
            try:
                expiry = datetime.fromisoformat(bot["subscription_expiry"])
                return now_ir() >= expiry
            except: return True
    
    def deactivate_bot(self, bot_id: str):
        with self._lock:
            if bot_id in self._data["bots"]:
                self._data["bots"][bot_id]["is_active"] = False
                self._dirty = True
    
    def enable_plugin(self, bot_id: str, plugin_id: str) -> bool:
        with self._lock:
            if bot_id not in self._data["bots"]: return False
            bot = self._data["bots"][bot_id]
            if plugin_id not in bot.get("enabled_plugins", []):
                bot.setdefault("enabled_plugins", []).append(plugin_id)
                self._dirty = True
            return True
    
    def disable_plugin(self, bot_id: str, plugin_id: str) -> bool:
        with self._lock:
            if bot_id not in self._data["bots"]: return False
            bot = self._data["bots"][bot_id]
            if plugin_id in bot.get("enabled_plugins", []):
                bot["enabled_plugins"].remove(plugin_id)
                self._dirty = True
            return True
    
    def purchase_plugin(self, bot_id: str, plugin_id: str) -> bool:
        with self._lock:
            if bot_id not in self._data["bots"]: return False
            bot = self._data["bots"][bot_id]
            if plugin_id not in bot.get("purchased_plugins", []):
                bot.setdefault("purchased_plugins", []).append(plugin_id)
                if plugin_id not in bot.get("enabled_plugins", []):
                    bot.setdefault("enabled_plugins", []).append(plugin_id)
                self._dirty = True
            return True
    
    def is_plugin_purchased(self, bot_id: str, plugin_id: str) -> bool:
        with self._lock:
            bot = self._data["bots"].get(bot_id)
            return bot and plugin_id in bot.get("purchased_plugins", []) if bot else False
    
    def is_plugin_enabled(self, bot_id: str, plugin_id: str) -> bool:
        with self._lock:
            bot = self._data["bots"].get(bot_id)
            return bot and plugin_id in bot.get("enabled_plugins", []) if bot else False
    
    def update_plugin_settings(self, bot_id: str, plugin_id: str, settings: dict):
        with self._lock:
            if bot_id in self._data["bots"]:
                self._data["bots"][bot_id].setdefault("plugin_settings", {})[plugin_id] = settings
                self._dirty = True
    
    def get_plugin_settings(self, bot_id: str, plugin_id: str) -> dict:
        with self._lock:
            bot = self._data["bots"].get(bot_id)
            if not bot: return {}
            return bot.get("plugin_settings", {}).get(plugin_id, {})
    
    def create_ticket(self, bot_id: str, user_id: int, username: str, full_name: str) -> Optional[dict]:
        with self._lock:
            bot = self._data["bots"].get(bot_id)
            if not bot: return None
            ticket_num = bot.get("next_ticket_id", 1001)
            bot["next_ticket_id"] = ticket_num + 1
            ticket_id = f"T{ticket_num}"
            self._data["tickets"][ticket_id] = {
                "ticket_id": ticket_id, "bot_id": bot_id,
                "user_id": user_id, "username": username, "full_name": full_name,
                "status": "open",
                "created_at": now_ir().isoformat(),
                "closed_at": None,
            }
            self._dirty = True
            return dict(self._data["tickets"][ticket_id])
    
    def close_ticket(self, ticket_id: str):
        with self._lock:
            ticket = self._data["tickets"].get(ticket_id)
            if ticket and ticket["status"] == "open":
                ticket["status"] = "closed"
                ticket["closed_at"] = now_ir().isoformat()
                self._dirty = True
    
    def get_ticket(self, ticket_id: str) -> Optional[dict]:
        with self._lock:
            t = self._data["tickets"].get(ticket_id)
            return dict(t) if t else None
    
    def get_bot_tickets(self, bot_id: str) -> list:
        with self._lock:
            return [dict(t) for t in self._data["tickets"].values() if t["bot_id"] == bot_id]
    
    def find_uid_by_referral(self, ref_code: str) -> Optional[int]:
        with self._lock:
            for uid_str, u in self._data["users"].items():
                if u.get("referral_code") == ref_code: return int(uid_str)
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
# PLUGIN SYSTEM
# ══════════════════════════════════════════════════════════════════════
class PluginManager:
    def __init__(self):
        self._plugin_handlers: Dict[str, Callable] = {}
        self._register_all_plugins()
    
    def _register_all_plugins(self):
        self._plugin_handlers["echo"] = self._setup_echo
        self._plugin_handlers["welcome"] = self._setup_welcome
        self._plugin_handlers["support"] = self._setup_support
        self._plugin_handlers["keyboard"] = self._setup_keyboard
    
    def setup_plugins_for_bot(self, app: Application, bot_id: str):
        bot_data = cloud.get_bot(bot_id)
        if not bot_data: return
        enabled = bot_data.get("enabled_plugins", [])
        for plugin_id in enabled:
            handler = self._plugin_handlers.get(plugin_id)
            if handler:
                try:
                    handler(app, bot_id)
                    log.info(f"✅ Plugin '{plugin_id}' enabled for bot {bot_id}")
                except Exception as e:
                    log.error(f"❌ Plugin '{plugin_id}' failed: {e}")
    
    def _setup_echo(self, app: Application, bot_id: str):
        async def echo_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if not update.message or not update.message.text: return
            if update.message.text.startswith("/"): return
            cloud.add_bot_user(bot_id, update.effective_user.id,
                update.effective_user.username or "",
                f"{update.effective_user.first_name or ''} {update.effective_user.last_name or ''}".strip())
            try:
                await update.message.reply_text(update.message.text)
                cloud.update_bot_stats(bot_id, messages_received=1, messages_sent=1)
            except: pass
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_handler), group=100)
    
    def _setup_welcome(self, app: Application, bot_id: str):
        async def welcome_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            settings = cloud.get_plugin_settings(bot_id, "welcome")
            message = settings.get("message", "👋 <b>سلام! خوش آمدید!</b>")
            photo_file_id = settings.get("photo_file_id")
            buttons_data = settings.get("buttons", [])
            
            cloud.add_bot_user(bot_id, update.effective_user.id,
                update.effective_user.username or "",
                f"{update.effective_user.first_name or ''} {update.effective_user.last_name or ''}".strip())
            
            btn_rows = []
            for row in buttons_data:
                current_row = []
                for btn in row:
                    text = btn.get("text", "")
                    btn_type = btn.get("type", "")
                    url = btn.get("url", "")
                    value = btn.get("value", "")
                    if btn_type == "url" and url:
                        current_row.append(InlineKeyboardButton(text, url=url))
                    elif btn_type == "support":
                        current_row.append(InlineKeyboardButton(text, callback_data=f"support_btn:{bot_id}"))
                    elif btn_type == "text":
                        encoded_value = hashlib.md5(value.encode()).hexdigest()[:8]
                        ctx.bot_data.setdefault("btn_texts", {})[encoded_value] = value
                        current_row.append(InlineKeyboardButton(text, callback_data=f"wbtn:{encoded_value}"))
                    elif btn_type == "callback":
                        cb_data = btn.get("callback", "")[:64]
                        current_row.append(InlineKeyboardButton(text, callback_data=cb_data))
                if current_row:
                    btn_rows.append(current_row)
            markup = InlineKeyboardMarkup(btn_rows) if btn_rows else None
            
            try:
                if photo_file_id:
                    await update.message.reply_photo(
                        photo=photo_file_id,
                        caption=message,
                        parse_mode=ParseMode.HTML,
                        reply_markup=markup,
                    )
                else:
                    await update.message.reply_text(
                        message,
                        parse_mode=ParseMode.HTML,
                        reply_markup=markup,
                    )
                cloud.update_bot_stats(bot_id, messages_received=1, messages_sent=1)
            except Exception as e:
                log.warning(f"Welcome failed for bot {bot_id}: {e}")
                try:
                    await update.message.reply_text(message, parse_mode=ParseMode.HTML)
                except: pass
        
        async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            q = update.callback_query
            data = q.data or ""
            if data == f"support_btn:{bot_id}":
                await q.answer()
                settings = cloud.get_plugin_settings(bot_id, "support")
                admin_msg = settings.get("admin_message", "📩 پیام خود را ارسال کنید.")
                user = q.from_user
                ticket = cloud.create_ticket(bot_id, user.id, user.username or "",
                    f"{user.first_name or ''} {user.last_name or ''}".strip())
                if not ticket:
                    await q.message.reply_text("❌ خطا")
                    return
                ctx.user_data[f"chat_{bot_id}"] = {"active": True, "ticket_id": ticket["ticket_id"]}
                cloud.save(force=True)
                await q.message.reply_text(
                    f"{admin_msg}\n\n🎫 <b>تیکت:</b> <code>{ticket['ticket_id']}</code>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔚 پایان چت", callback_data=f"end_chat:{bot_id}")]
                    ]))
            elif data.startswith("wbtn:"):
                encoded = data.replace("wbtn:", "")
                text = ctx.bot_data.get("btn_texts", {}).get(encoded, "")
                await q.answer()
                if text:
                    await q.message.reply_text(text, parse_mode=ParseMode.HTML)
        
        app.add_handler(CommandHandler("start", welcome_handler), group=5)
        app.add_handler(CallbackQueryHandler(button_handler, pattern=r"^support_btn:|^wbtn:"), group=5)
    
    def _setup_keyboard(self, app: Application, bot_id: str):
        async def send_keyboard_on_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            settings = cloud.get_plugin_settings(bot_id, "keyboard")
            buttons_data = settings.get("buttons", [])
            resize = settings.get("resize_keyboard", True)
            one_time = settings.get("one_time_keyboard", False)
            persistent = settings.get("persistent", True)
            
            if not buttons_data: return
            
            rows = []
            for row in buttons_data:
                current_row = []
                for btn in row:
                    text = btn.get("text", "")
                    if text:
                        current_row.append(KeyboardButton(text))
                if current_row:
                    rows.append(current_row)
            if not rows: return
            
            markup = ReplyKeyboardMarkup(
                rows,
                resize_keyboard=resize,
                one_time_keyboard=one_time,
                is_persistent=persistent,
            )
            
            user_id = update.effective_user.id
            shown_key = f"kb_shown_{bot_id}"
            
            if not ctx.user_data.get(shown_key):
                try:
                    await update.message.reply_text(
                        "⌨️ <b>کیبورد اختصاصی فعال شد!</b>\n\n"
                        "از دکمه‌های زیر برای تعامل با ربات استفاده کنید.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=markup,
                    )
                    ctx.user_data[shown_key] = True
                except Exception as e:
                    log.warning(f"Keyboard send failed for {bot_id}: {e}")
            else:
                temp_msg = await update.message.reply_text("⌨️", reply_markup=markup)
                await asyncio.sleep(0.5)
                try: await temp_msg.delete()
                except: pass
        
        async def keyboard_button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if not update.message or not update.message.text: return
            text = update.message.text
            if text.startswith("/"): return
            
            settings = cloud.get_plugin_settings(bot_id, "keyboard")
            buttons_data = settings.get("buttons", [])
            
            btn_action = btn_value = None
            for row in buttons_data:
                for btn in row:
                    if btn.get("text") == text:
                        btn_action = btn.get("action", "text")
                        btn_value = btn.get("value", "")
                        break
                if btn_action: break
            
            if not btn_action: return
            
            cloud.add_bot_user(bot_id, update.effective_user.id,
                update.effective_user.username or "",
                f"{update.effective_user.first_name or ''} {update.effective_user.last_name or ''}".strip())
            
            try:
                if btn_action == "text":
                    await update.message.reply_text(btn_value, parse_mode=ParseMode.HTML)
                    cloud.update_bot_stats(bot_id, messages_received=1, messages_sent=1)
                elif btn_action == "command" and btn_value == "/support":
                    settings_sup = cloud.get_plugin_settings(bot_id, "support")
                    admin_msg = settings_sup.get("admin_message", "📩 پیام خود را ارسال کنید.")
                    user = update.effective_user
                    ticket = cloud.create_ticket(bot_id, user.id, user.username or "",
                        f"{user.first_name or ''} {user.last_name or ''}".strip())
                    if ticket:
                        ctx.user_data[f"chat_{bot_id}"] = {"active": True, "ticket_id": ticket["ticket_id"]}
                        cloud.save(force=True)
                        await update.message.reply_text(
                            f"{admin_msg}\n\n🎫 <b>تیکت:</b> <code>{ticket['ticket_id']}</code>",
                            parse_mode=ParseMode.HTML,
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("🔚 پایان چت", callback_data=f"end_chat:{bot_id}")]
                            ]))
                elif btn_action == "url":
                    await update.message.reply_text(
                        f"🌐 <b>لینک:</b>",
                        parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔗 باز کردن", url=btn_value)]
                        ]))
                cloud.update_bot_stats(bot_id, messages_received=1, messages_sent=1)
            except Exception as e:
                log.warning(f"Keyboard action failed: {e}")
        
        app.add_handler(CommandHandler("start", send_keyboard_on_start), group=6)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, keyboard_button_handler), group=15)
    
    def _setup_support(self, app: Application, bot_id: str):
        async def support_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            settings = cloud.get_plugin_settings(bot_id, "support")
            admin_msg = settings.get("admin_message", "📩 پیام خود را ارسال کنید.")
            cloud.add_bot_user(bot_id, update.effective_user.id,
                update.effective_user.username or "",
                f"{update.effective_user.first_name or ''} {update.effective_user.last_name or ''}".strip())
            user = update.effective_user
            ticket = cloud.create_ticket(bot_id, user.id, user.username or "",
                f"{user.first_name or ''} {user.last_name or ''}".strip())
            if not ticket:
                await update.message.reply_text("❌ خطا در ایجاد چت.")
                return
            ctx.user_data[f"chat_{bot_id}"] = {"active": True, "ticket_id": ticket["ticket_id"]}
            cloud.save(force=True)
            await update.message.reply_text(
                f"{admin_msg}\n\n🎫 <b>شماره تیکت:</b> <code>{ticket['ticket_id']}</code>\n\n"
                f"💬 هر پیامی که بفرستید مستقیماً به پشتیبانی ارسال می‌شود.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔚 پایان چت", callback_data=f"end_chat:{bot_id}")]
                ]))
        
        async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            q = update.callback_query
            data = q.data or ""
            if data == f"end_chat:{bot_id}":
                state = ctx.user_data.get(f"chat_{bot_id}")
                if state and state.get("active"):
                    cloud.close_ticket(state.get("ticket_id"))
                    ctx.user_data.pop(f"chat_{bot_id}", None)
                    cloud.save(force=True)
                settings = cloud.get_plugin_settings(bot_id, "support")
                end_msg = settings.get("end_chat_message", "🔚 چت پایان یافت.")
                await q.answer("چت پایان یافت")
                try: await q.edit_message_text(end_msg, parse_mode=ParseMode.HTML)
                except: await q.message.reply_text(end_msg, parse_mode=ParseMode.HTML)
                return
            if data.startswith("reply_ticket:"):
                ticket_id = data.replace("reply_ticket:", "")
                ticket = cloud.get_ticket(ticket_id)
                if not ticket or ticket["bot_id"] != bot_id:
                    await q.answer("❌ تیکت یافت نشد"); return
                bot_data = cloud.get_bot(bot_id)
                if not bot_data or bot_data.get("owner_uid") != q.from_user.id:
                    await q.answer("❌ دسترسی ندارید"); return
                ctx.user_data[f"admin_reply_{bot_id}"] = {
                    "active": True, "ticket_id": ticket_id, "user_id": ticket["user_id"],
                }
                await q.answer()
                await q.message.reply_text(
                    f"💬 <b>در حال پاسخ به تیکت {ticket_id}</b>\n"
                    f"👤 کاربر: {ticket['full_name']}\n\n"
                    f"✍️ پاسخ خود را بنویسید و ارسال کنید:\n(/cancel برای لغو)",
                    parse_mode=ParseMode.HTML)
                return
            if data.startswith("close_ticket:"):
                ticket_id = data.replace("close_ticket:", "")
                ticket = cloud.get_ticket(ticket_id)
                if not ticket or ticket["bot_id"] != bot_id:
                    await q.answer("❌ تیکت یافت نشد"); return
                bot_data = cloud.get_bot(bot_id)
                if not bot_data or bot_data.get("owner_uid") != q.from_user.id:
                    await q.answer("❌ دسترسی ندارید"); return
                cloud.close_ticket(ticket_id)
                cloud.save(force=True)
                try:
                    temp_bot = Bot(token=bot_data["bot_token"])
                    await temp_bot.send_message(chat_id=ticket["user_id"],
                        text=f"🔚 <b>چت شما با پشتیبانی بسته شد.</b>\n🎫 تیکت: <code>{ticket_id}</code>",
                        parse_mode=ParseMode.HTML)
                except: pass
                await q.answer("تیکت بسته شد")
                try: await q.edit_message_text(f"✅ <b>تیکت {ticket_id} بسته شد</b>", parse_mode=ParseMode.HTML)
                except: pass
                ctx.user_data.pop(f"admin_reply_{bot_id}", None)
        
        app.add_handler(CommandHandler("support", support_command), group=5)
        app.add_handler(CallbackQueryHandler(callback_handler, pattern=r"^end_chat:|^reply_ticket:|^close_ticket:"), group=5)

plugin_manager = PluginManager()

# ══════════════════════════════════════════════════════════════════════
# BOT FACTORY
# ══════════════════════════════════════════════════════════════════════
class BotFactory:
    def __init__(self):
        self._running: Dict[str, asyncio.Task] = {}
        self._apps: Dict[str, Application] = {}
        self._lock = asyncio.Lock()
    
    async def start_bot(self, bot_id: str, bot_token: str):
        async with self._lock:
            if bot_id in self._running: return
            if cloud.check_subscription_expired(bot_id):
                cloud.deactivate_bot(bot_id); return
            try:
                app = Application.builder().token(bot_token).build()
                plugin_manager.setup_plugins_for_bot(app, bot_id)
                await app.initialize()
                me = await app.bot.get_me()
                cloud.update_bot_username(bot_id, me.username or "")
                cloud.save()
                task = asyncio.create_task(self._run(app, bot_id))
                self._running[bot_id] = task
                self._apps[bot_id] = app
                log.info(f"✅ Bot {bot_id} (@{me.username}) started")
            except InvalidToken:
                log.error(f"❌ Invalid token {bot_id}")
            except Exception as e:
                log.error(f"❌ Failed {bot_id}: {e}")
    
    async def stop_bot(self, bot_id: str):
        async with self._lock:
            if bot_id not in self._running: return
            task = self._running[bot_id]
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
            if bot_id in self._apps:
                try: await self._apps[bot_id].shutdown()
                except: pass
                del self._apps[bot_id]
            del self._running[bot_id]
    
    async def restart_bot(self, bot_id: str):
        bot_data = cloud.get_bot(bot_id)
        if not bot_data: return
        await self.stop_bot(bot_id)
        if bot_data.get("is_active"):
            await asyncio.sleep(1)
            await self.start_bot(bot_id, bot_data["bot_token"])
    
    async def _run(self, app: Application, bot_id: str):
        try:
            await app.start()
            await app.updater.start_polling(
                allowed_updates=["message", "callback_query"], drop_pending_updates=True)
            while True: await asyncio.sleep(60)
        except asyncio.CancelledError: pass
        except Exception as e: log.error(f"Bot {bot_id} error: {e}")
        finally:
            try:
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
            except: pass
    
    async def start_all_active_bots(self):
        for b in cloud.get_all_bots():
            if b.get("is_active") and not cloud.check_subscription_expired(b["bot_id"]):
                await self.start_bot(b["bot_id"], b["bot_token"])
    
    async def stop_all_bots(self):
        for bid in list(self._running.keys()):
            await self.stop_bot(bid)

bot_factory = BotFactory()

# ══════════════════════════════════════════════════════════════════════
# UI FUNCTIONS
# ══════════════════════════════════════════════════════════════════════
async def check_membership(bot, uid: int) -> bool:
    if not FORCE_JOIN_CHANNEL: return True
    try:
        member = await bot.get_chat_member(FORCE_JOIN_CHANNEL, uid)
        return member.status not in ("left", "kicked", "banned")
    except: return True

def kb_join():
    if not FORCE_JOIN_CHANNEL: return None
    url = f"https://t.me/{FORCE_JOIN_CHANNEL.lstrip('@')}" if FORCE_JOIN_CHANNEL.startswith("@") else f"https://t.me/c/{str(FORCE_JOIN_CHANNEL).replace('-100','')}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url=url)],
        [InlineKeyboardButton("✅ عضو شدم", callback_data="check_join")],
    ])

def kb_main_reply(uid: int = 0) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton("🤖 ربات‌های من"), KeyboardButton("👤 حساب کاربری")],
        [KeyboardButton("🎁 جایزه و دعوت"), KeyboardButton("📖 راهنما")],
    ]
    if uid and is_admin(uid):
        rows.append([KeyboardButton("👑 پنل ادمین")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)

REPLY_BUTTONS = {
    "🤖 ربات‌های من": "menu:mybots",
    "👤 حساب کاربری": "menu:account",
    "🎁 جایزه و دعوت": "menu:rewards",
    "📖 راهنما": "menu:help",
    "👑 پنل ادمین": "admin:panel:0",
}

def kb_back(dest="home"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"nav:{dest}")]])

def kb_bot_mgmt(bot_id, bot_data):
    expired = cloud.check_subscription_expired(bot_id)
    rows = []
    rows.append([InlineKeyboardButton("💎 خرید اشتراک" if expired else "🔄 تمدید اشتراک", callback_data=f"bot:sub:{bot_id}")])
    rows.append([InlineKeyboardButton("🧩 پلاگین‌ها", callback_data=f"bot:plugins:{bot_id}")])
    rows.append([InlineKeyboardButton("📊 آمار", callback_data=f"bot:stats:{bot_id}")])
    rows.append([InlineKeyboardButton("🗑 حذف ربات", callback_data=f"bot:del:{bot_id}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu:mybots")])
    return InlineKeyboardMarkup(rows)

def kb_plugins(bot_id):
    bot_data = cloud.get_bot(bot_id)
    if not bot_data: return kb_back("mybots")
    enabled = bot_data.get("enabled_plugins", [])
    purchased = bot_data.get("purchased_plugins", [])
    rows = [[InlineKeyboardButton("─── 🆓 پلاگین‌های پایه ───", callback_data="noop")]]
    for pid, plugin in AVAILABLE_PLUGINS.items():
        if plugin.get("is_premium"): continue
        is_en = pid in enabled
        status = "✅" if is_en else "⬜️"
        has_settings = plugin.get("has_settings", False)
        btn_row = [InlineKeyboardButton(f"{status} {plugin['name']}", callback_data=f"plg:toggle:{bot_id}:{pid}")]
        if has_settings and is_en:
            btn_row.append(InlineKeyboardButton("⚙️", callback_data=f"plg:set:{bot_id}:{pid}"))
        rows.append(btn_row)
    rows.append([InlineKeyboardButton("─── 💎 پلاگین‌های پرمیوم ───", callback_data="noop")])
    for pid, plugin in AVAILABLE_PLUGINS.items():
        if not plugin.get("is_premium"): continue
        is_pur = pid in purchased
        is_en = pid in enabled
        has_settings = plugin.get("has_settings", False)
        if is_pur:
            status = "✅" if is_en else "⏸️"
            btn_row = [InlineKeyboardButton(f"{status} {plugin['name']}", callback_data=f"plg:toggle:{bot_id}:{pid}")]
            if has_settings and is_en:
                btn_row.append(InlineKeyboardButton("⚙️", callback_data=f"plg:set:{bot_id}:{pid}"))
            rows.append(btn_row)
        else:
            rows.append([InlineKeyboardButton(f"🔒 {plugin['name']} ({plugin['cost_gc']} GC)", callback_data=f"plg:buy:{bot_id}:{pid}")])
    return InlineKeyboardMarkup(rows)

# ══════════════════════════════════════════════════════════════════════
# TEXT BUILDERS
# ══════════════════════════════════════════════════════════════════════
def build_welcome_text(uid):
    u = cloud.get_user(uid)
    signup_section = ""
    if u and not u.get("signup_bonus_claimed"):
        signup_section = f"\n🎁 <b>شما {SIGNUP_BONUS_GC} GC هدیه ثبت‌نام دریافت کردید!</b>\n\n"
    return (
        f"🤖✨ <b>به GramBotSaz خوش اومدی!</b> ✨🤖\n\n{signup_section}"
        "<blockquote expandable>"
        "پلتفرم حرفه‌ای ساخت ربات تلگرام بدون نیاز به کدنویسی.\n"
        "</blockquote>"
        "🔥 <b>ویژگی‌ها:</b>\n"
        "  🆓 ۳ روز اشتراک آزمایشی رایگان\n"
        "  🧩 پلاگین‌های پیشرفته (چت زنده، کیبورد، دکمه شیشه‌ای)\n"
        "  💰 سیستم اقتصادی GramCoin\n\n"
        "💡 <b>از دکمه‌های زیر شروع کن:</b>"
    )

def build_account_text(uid):
    u = cloud.get_user(uid)
    if not u: return ""
    full = u.get("full_name") or u.get("username") or "—"
    bots_count = len(u.get("bots_created", []))
    daily = "✅ قابل دریافت" if cloud.can_claim_daily(uid) else "⏳ فردا"
    refs = sum(1 for x in cloud.get_all_users() if x.get("referred_by") == str(uid))
    return (
        f"👤 <b>حساب کاربری</b>\n\n"
        f"📛 <b>نام:</b> {full}\n"
        f"🆔 <b>آیدی:</b> <code>{uid}</code>\n\n"
        f"💰 <b>موجودی:</b> <code>{u.get('gramcoin_balance', 0)} GC</code>\n"
        f"📈 <b>کل درآمد:</b> <code>{u.get('total_earned_gc', 0)} GC</code>\n\n"
        f"🤖 <b>تعداد ربات‌ها:</b> {bots_count}/{MAX_FREE_BOTS}\n"
        f"👥 <b>زیرمجموعه‌ها:</b> {refs}\n\n"
        f"🎁 <b>جایزه روزانه:</b> {daily}"
    )

def build_transactions(uid):
    txs = [t for t in cloud._data.get("transactions", []) if t.get("uid") == uid][-15:]
    if not txs: return "📊 <b>تراکنش‌ها:</b>\n\nهنوز تراکنشی ندارید."
    text = "📊 <b>تراکنش‌های اخیر:</b>\n\n"
    for t in reversed(txs):
        text += f"• {t['description']}\n  📅 {t['timestamp'][:16]}\n\n"
    return text

def build_mybots_text(uid):
    bots = cloud.get_user_bots(uid)
    if not bots:
        return (
            "🤖 <b>ربات‌های شما</b>\n\n"
            "هنوز رباتی نساخته‌اید.\n\n"
            f"💡 <b>اولین ربات شما {TRIAL_DAYS} روز آزمایشی رایگان دارد!</b>"
        ), InlineKeyboardMarkup([[InlineKeyboardButton("➕ ساخت ربات جدید", callback_data="create:bot")]])
    
    text = "🤖 <b>ربات‌های شما</b>\n\n"
    rows = []
    for bot in bots:
        expired = cloud.check_subscription_expired(bot["bot_id"])
        is_trial = bot.get("is_trial", False)
        if expired: status = "🔴 منقضی"
        elif is_trial: status = "🆓 آزمایشی"
        elif bot.get("is_active"): status = "🟢 فعال"
        else: status = "⏸️ غیرفعال"
        username = f"@{bot.get('bot_username')}" if bot.get("bot_username") else "—"
        text += f"• <b>{bot['bot_id'][:8]}</b> | {username}\n  {status} | 🧩 {len(bot.get('enabled_plugins', []))} پلاگین\n\n"
        rows.append([InlineKeyboardButton(f"⚙️ مدیریت {bot['bot_id'][:8]}", callback_data=f"bot:manage:{bot['bot_id']}")])
    if len(bots) < MAX_FREE_BOTS:
        rows.append([InlineKeyboardButton("➕ ساخت ربات جدید", callback_data="create:bot")])
    return text, InlineKeyboardMarkup(rows)

def build_bot_detail_text(bot_id, bot_data):
    username = f"@{bot_data.get('bot_username')}" if bot_data.get("bot_username") else "—"
    expired = cloud.check_subscription_expired(bot_id)
    is_trial = bot_data.get("is_trial", False)
    if expired:
        sub = "🔴 <b>منقضی شده</b>\n⚠️ برای فعال‌سازی مجدد اشتراک بخرید."
    elif is_trial:
        exp = datetime.fromisoformat(bot_data["subscription_expiry"])
        sub = f"🆓 <b>دوره آزمایشی</b>\n⏳ {_fmt_timedelta((exp - now_ir()).total_seconds())}\n📅 {exp.strftime('%Y-%m-%d')}"
    else:
        exp = datetime.fromisoformat(bot_data["subscription_expiry"])
        plan = "هفتگی" if bot_data.get("subscription_plan") == "weekly" else "ماهانه"
        sub = f"🟢 <b>فعال</b> ({plan})\n⏳ {_fmt_timedelta((exp - now_ir()).total_seconds())}"
    stats = bot_data.get("stats", {})
    return (
        f"⚙️ <b>مدیریت ربات</b>\n\n"
        f"🆔 <b>ID:</b> <code>{bot_id}</code>\n"
        f"📛 <b>یوزرنیم:</b> {username}\n\n"
        f"💎 <b>اشتراک:</b>\n{sub}\n\n"
        f"🧩 <b>پلاگین‌ها:</b> {len(bot_data.get('enabled_plugins', []))} فعال | {len(bot_data.get('purchased_plugins', []))} پرمیوم\n\n"
        f"📊 <b>آمار:</b>\n"
        f"• کاربران: {len(bot_data.get('bot_users', []))}\n"
        f"• پیام دریافتی: {stats.get('messages_received', 0)}\n"
        f"• پیام ارسالی: {stats.get('messages_sent', 0)}"
    )

def build_rewards_text(uid):
    u = cloud.ensure_user(uid)[0]
    link = f"https://t.me/{PLATFORM_BOT_USERNAME}?start=ref_{u['referral_code']}" if PLATFORM_BOT_USERNAME else "—"
    refs = sum(1 for x in cloud.get_all_users() if x.get("referred_by") == str(uid))
    can_daily = cloud.can_claim_daily(uid)
    return (
        f"🎁 <b>جایزه و دعوت</b>\n\n"
        f"<b>🎯 جایزه روزانه:</b> {'✅ دریافت کن' if can_daily else '⏳ فردا'}\n"
        f"هر روز {DAILY_REWARD_GC} GC رایگان!\n\n"
        f"<b>🔗 لینک دعوت شما:</b>\n<code>{link}</code>\n\n"
        f"✅ به ازای هر دعوت: <b>{REFERRAL_BONUS_GC} GC</b>\n\n"
        f"👥 <b>دعوت‌های موفق:</b> {refs}\n"
        f"💰 <b>کل درآمد از رفرال:</b> {refs * REFERRAL_BONUS_GC} GC"
    )

def build_help_text():
    return (
        "📖 <b>راهنمای GramBotSaz</b>\n\n"
        "<b>🤖 ساخت ربات:</b>\n"
        "۱. دکمه «🤖 ربات‌های من» را بزنید\n"
        "۲. «➕ ساخت ربات جدید» را بزنید\n"
        "۳. توکن را از @BotFather بگیرید\n"
        f"۴. ربات شما {TRIAL_DAYS} روز آزمایشی رایگان دارد!\n\n"
        "<b>💰 کسب GramCoin:</b>\n"
        f"• هدیه ثبت‌نام: {SIGNUP_BONUS_GC} GC\n"
        f"• روزانه: {DAILY_REWARD_GC} GC\n"
        f"• رفرال: {REFERRAL_BONUS_GC} GC\n\n"
        "<b>💎 اشتراک‌ها:</b>\n"
        f"• هفتگی: {SUBSCRIPTION_WEEKLY_GC} GC\n"
        f"• ماهانه: {SUBSCRIPTION_MONTHLY_GC} GC"
    )

# ══════════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════
async def _require_membership(update, bot):
    uid = update.effective_user.id
    if await check_membership(bot, uid): return True
    await update.effective_message.reply_text(
        "⛔️ <b>برای استفاده از ربات باید عضو کانال باشی!</b>",
        parse_mode=ParseMode.HTML, reply_markup=kb_join())
    return False

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    username = user.username or ""
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    u, is_new = cloud.ensure_user(uid, username, full_name)
    signup_bonus_given = False
    if is_new and cloud.claim_signup_bonus(uid):
        signup_bonus_given = True
        cloud.save(force=True)
    
    if ctx.args:
        arg = ctx.args[0]
        if is_new and arg.startswith("ref_"):
            ref_code = arg[4:]
            if not await check_membership(ctx.bot, uid):
                ctx.user_data["pending_ref"] = ref_code
                await ctx.bot.send_message(uid, "⛔️ ابتدا عضو کانال شو!", parse_mode=ParseMode.HTML, reply_markup=kb_join())
                return
            ref_uid = cloud.find_uid_by_referral(ref_code)
            if ref_uid and cloud.apply_referral(uid, ref_uid):
                cloud.add_gramcoin(ref_uid, REFERRAL_BONUS_GC, "referral")
                cloud.save(force=True)
    
    if not await check_membership(ctx.bot, uid):
        await update.effective_message.reply_text("⛔️ عضو کانال شو!", parse_mode=ParseMode.HTML, reply_markup=kb_join())
        return
    
    if signup_bonus_given:
        welcome_text = f"🎉 <b>به GramBotSaz خوش آمدید!</b>\n\n🎁 شما <b>{SIGNUP_BONUS_GC} GC</b> هدیه ثبت‌نام دریافت کردید!\n💰 موجودی: <code>{cloud.get_user(uid).get('gramcoin_balance', 0)} GC</code>\n\n" + build_welcome_text(uid)
    else:
        welcome_text = build_welcome_text(uid)
    
    await update.effective_message.reply_text(welcome_text, parse_mode=ParseMode.HTML, reply_markup=kb_main_reply(uid))
    cloud.save()

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keys_to_remove = ["awaiting", "pending_ref"]
    for k in keys_to_remove: ctx.user_data.pop(k, None)
    for k in list(ctx.user_data.keys()):
        if any(k.startswith(p) for p in ["welcome_", "support_", "keyboard_", "admin_reply_"]):
            ctx.user_data.pop(k, None)
    await update.message.reply_text("❌ لغو شد.", parse_mode=ParseMode.HTML, reply_markup=kb_main_reply(update.effective_user.id))

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _require_membership(update, ctx.bot): return
    await update.effective_message.reply_text(build_help_text(), parse_mode=ParseMode.HTML, reply_markup=kb_main_reply(update.effective_user.id))

async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _require_membership(update, ctx.bot): return
    user = update.effective_user
    u = cloud.ensure_user(user.id, user.username or "", f"{user.first_name or ''} {user.last_name or ''}".strip())[0]
    await update.effective_message.reply_text(build_account_text(user.id), parse_mode=ParseMode.HTML, reply_markup=kb_main_reply(user.id))

# ══════════════════════════════════════════════════════════════════════
# CALLBACK HANDLER
# ══════════════════════════════════════════════════════════════════════
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    uid = q.from_user.id
    cloud.ensure_user(uid, q.from_user.username or "", f"{q.from_user.first_name or ''} {q.from_user.last_name or ''}".strip())
    
    if data == "noop": await q.answer(); return
    
    if data != "check_join" and not await check_membership(q.get_bot(), uid):
        await q.answer()
        try: await q.edit_message_text("⛔️ عضو کانال شو!", parse_mode=ParseMode.HTML, reply_markup=kb_join())
        except: pass
        return
    
    if data == "check_join":
        if await check_membership(q.get_bot(), uid):
            await q.answer("✅ تایید شد!")
            pending_ref = ctx.user_data.pop("pending_ref", None)
            if pending_ref:
                ref_uid = cloud.find_uid_by_referral(pending_ref)
                if ref_uid and cloud.apply_referral(uid, ref_uid):
                    cloud.add_gramcoin(ref_uid, REFERRAL_BONUS_GC, "referral")
                cloud.save(force=True)
            await q.edit_message_text(build_welcome_text(uid), parse_mode=ParseMode.HTML, reply_markup=kb_main_reply(uid))
        else:
            await q.answer("⛔️ هنوز عضو نشدی!", show_alert=True)
        return
    
    if data.startswith("nav:"):
        dest = data.replace("nav:", "")
        await q.answer()
        if dest == "home":
            await q.edit_message_text(build_welcome_text(uid), parse_mode=ParseMode.HTML, reply_markup=kb_main_reply(uid))
        elif dest == "mybots":
            text, kb = build_mybots_text(uid)
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif dest == "account":
            await q.edit_message_text(build_account_text(uid), parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 تراکنش‌ها", callback_data="nav:txs")]]))
        elif dest == "rewards":
            await q.edit_message_text(build_rewards_text(uid), parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎁 دریافت جایزه", callback_data="daily:claim")]]))
        elif dest == "txs":
            await q.edit_message_text(build_transactions(uid), parse_mode=ParseMode.HTML, reply_markup=kb_back("account"))
        elif dest.startswith("bot:"):
            bot_id = dest.replace("bot:", "")
            bot = cloud.get_bot(bot_id)
            if bot and bot.get("owner_uid") == uid:
                await q.edit_message_text(build_bot_detail_text(bot_id, bot), parse_mode=ParseMode.HTML, reply_markup=kb_bot_mgmt(bot_id, bot))
        elif dest.startswith("plugins:"):
            bot_id = dest.replace("plugins:", "")
            await q.edit_message_text("🧩 <b>پلاگین‌ها</b>", parse_mode=ParseMode.HTML, reply_markup=kb_plugins(bot_id))
        elif dest.startswith("plugin:"):
            parts = dest.replace("plugin:", "").split(":")
            bot_id, pid = parts[0], parts[1]
            await _show_plugin_settings(q, bot_id, pid)
        return
    
    if data == "create:bot":
        user_bots = cloud.get_user_bots(uid)
        if len(user_bots) >= MAX_FREE_BOTS:
            await q.answer(f"⚠️ حداکثر {MAX_FREE_BOTS} ربات!", show_alert=True); return
        ctx.user_data["awaiting"] = "bot_token"
        await q.answer()
        await q.edit_message_text(
            f"🤖 <b>ساخت ربات جدید</b>\n\n🎁 {TRIAL_DAYS} روز آزمایشی رایگان!\n\n🔑 توکن ربات را از @BotFather بگیرید و ارسال کنید:",
            parse_mode=ParseMode.HTML)
        return
    
    if data == "daily:claim":
        if cloud.can_claim_daily(uid):
            cloud.claim_daily_reward(uid, DAILY_REWARD_GC)
            cloud.save(force=True)
            new_balance = cloud.get_user(uid).get("gramcoin_balance", 0)
            await q.answer(f"✅ +{DAILY_REWARD_GC} GC", show_alert=True)
            try: await q.edit_message_text(f"🎁 <b>جایزه روزانه دریافت شد!</b>\n\n⭐️ <b>+{DAILY_REWARD_GC} GC</b>\n💰 موجودی: <code>{new_balance} GC</code>", parse_mode=ParseMode.HTML)
            except: pass
        else:
            await q.answer("⏳ فردا!", show_alert=True)
        return
    
    if data.startswith("bot:manage:"):
        bot_id = data.split(":")[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        await q.answer()
        await q.edit_message_text(build_bot_detail_text(bot_id, bot), parse_mode=ParseMode.HTML, reply_markup=kb_bot_mgmt(bot_id, bot))
        return
    
    if data.startswith("bot:sub:"):
        bot_id = data.split(":")[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        await q.answer()
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🥉 هفتگی — {SUBSCRIPTION_WEEKLY_GC} GC", callback_data=f"sub:buy:{bot_id}:weekly")],
            [InlineKeyboardButton(f"🥈 ماهانه — {SUBSCRIPTION_MONTHLY_GC} GC", callback_data=f"sub:buy:{bot_id}:monthly")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")],
        ])
        await q.edit_message_text(f"💎 <b>اشتراک ربات {bot_id[:8]}</b>", parse_mode=ParseMode.HTML, reply_markup=kb)
        return
    
    if data.startswith("sub:buy:"):
        _, _, bot_id, plan = data.split(":")
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        cost = SUBSCRIPTION_WEEKLY_GC if plan == "weekly" else SUBSCRIPTION_MONTHLY_GC
        days = SUBSCRIPTION_WEEKLY_DAYS if plan == "weekly" else SUBSCRIPTION_MONTHLY_DAYS
        u = cloud.get_user(uid)
        if u.get("gramcoin_balance", 0) < cost:
            await q.answer(f"❌ نیاز به {cost} GC", show_alert=True); return
        await q.answer("در حال پردازش...")
        if cloud.deduct_gramcoin(uid, cost, f"sub_{plan}_{bot_id}"):
            new_exp = cloud.activate_subscription(bot_id, plan, days)
            cloud.save(force=True)
            await bot_factory.restart_bot(bot_id)
            plan_name = "هفتگی" if plan == "weekly" else "ماهانه"
            await q.edit_message_text(
                f"✅ <b>اشتراک {plan_name} فعال شد!</b>\n📅 {new_exp.strftime('%Y-%m-%d')}\n💰 {cost} GC کسر شد.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="nav:mybots")]]))
        return
    
    if data.startswith("bot:plugins:"):
        bot_id = data.split(":")[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        if cloud.check_subscription_expired(bot_id):
            await q.answer("⚠️ اشتراک منقضی!", show_alert=True); return
        await q.answer()
        await q.edit_message_text("🧩 <b>پلاگین‌ها</b>\n\n💡 ⚙️ = شخصی‌سازی", parse_mode=ParseMode.HTML, reply_markup=kb_plugins(bot_id))
        return
    
    if data.startswith("plg:toggle:"):
        _, _, bot_id, pid = data.split(":")
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        if cloud.check_subscription_expired(bot_id):
            await q.answer("⚠️ اشتراک منقضی!", show_alert=True); return
        plugin = AVAILABLE_PLUGINS.get(pid)
        if not plugin: await q.answer("پلاگین یافت نشد", show_alert=True); return
        if plugin.get("is_premium") and not cloud.is_plugin_purchased(bot_id, pid):
            await q.answer("⚠️ باید خریداری شود", show_alert=True); return
        await q.answer("در حال تغییر...")
        if cloud.is_plugin_enabled(bot_id, pid):
            cloud.disable_plugin(bot_id, pid); msg = f"⏸️ {plugin['name']} غیرفعال شد"
        else:
            cloud.enable_plugin(bot_id, pid); msg = f"✅ {plugin['name']} فعال شد"
        cloud.save(force=True)
        await bot_factory.restart_bot(bot_id)
        try: await q.edit_message_reply_markup(reply_markup=kb_plugins(bot_id))
        except: pass
        await q.answer(msg, show_alert=True)
        return
    
    if data.startswith("plg:buy:"):
        _, _, bot_id, pid = data.split(":")
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        plugin = AVAILABLE_PLUGINS.get(pid)
        if not plugin: await q.answer("پلاگین یافت نشد", show_alert=True); return
        cost = plugin.get("cost_gc", 0)
        u = cloud.get_user(uid)
        if u.get("gramcoin_balance", 0) < cost:
            await q.answer(f"❌ نیاز به {cost} GC", show_alert=True); return
        await q.answer("در حال خرید...")
        if cloud.deduct_gramcoin(uid, cost, f"plugin_{pid}_{bot_id}"):
            cloud.purchase_plugin(bot_id, pid)
            cloud.save(force=True)
            await bot_factory.restart_bot(bot_id)
            try:
                await q.edit_message_text(f"✅ <b>{plugin['name']} خریداری شد!</b>\n💰 {cost} GC کسر شد.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:plugins:{bot_id}")]]))
            except: pass
        return
    
    if data.startswith("plg:set:"):
        _, _, bot_id, pid = data.split(":")
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        await q.answer()
        await _show_plugin_settings(q, bot_id, pid)
        return
    
    if data.startswith("bot:stats:"):
        bot_id = data.split(":")[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        await q.answer()
        stats = bot.get("stats", {})
        await q.edit_message_text(
            f"📊 <b>آمار ربات {bot_id[:8]}</b>\n\n👥 کاربران: {len(bot.get('bot_users', []))}\n📨 دریافتی: {stats.get('messages_received', 0)}\n📤 ارسالی: {stats.get('messages_sent', 0)}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")]]))
        return
    
    if data.startswith("bot:del:"):
        bot_id = data.split(":")[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        await q.answer("در حال حذف...")
        await bot_factory.stop_bot(bot_id)
        cloud.delete_bot(bot_id, uid)
        cloud.save(force=True)
        text, kb = build_mybots_text(uid)
        await q.edit_message_text("🗑 ربات حذف شد.\n\n" + text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return
    
    # ═══ Plugin Settings Actions ═══
    if data.startswith("welcome:"):
        parts = data.split(":")
        action, bot_id = parts[1], parts[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        await q.answer()
        
        if action == "edit_text":
            ctx.user_data["awaiting"] = f"welcome_text_{bot_id}"
            settings = cloud.get_plugin_settings(bot_id, "welcome")
            await q.edit_message_text(
                f"✏️ <b>ویرایش متن خوش‌آمد</b>\n\n📝 <b>فعلی:</b>\n<blockquote expandable>{settings.get('message','')}</blockquote>\n\n📤 متن جدید را ارسال کنید:",
                parse_mode=ParseMode.HTML)
        elif action == "edit_photo":
            ctx.user_data["awaiting"] = f"welcome_photo_{bot_id}"
            settings = cloud.get_plugin_settings(bot_id, "welcome")
            current = "✅ دارد" if settings.get("photo_file_id") else "❌ ندارد"
            await q.edit_message_text(
                f"🖼 <b>تنظیم عکس خوش‌آمد</b>\n\n📊 وضعیت فعلی: {current}\n\n"
                f"📸 برای تنظیم: یک عکس را مستقیماً ارسال کنید\n"
                f"🗑 برای حذف: عبارت <code>none</code> را ارسال کنید",
                parse_mode=ParseMode.HTML)
        elif action == "edit_buttons":
            ctx.user_data["awaiting"] = f"welcome_buttons_{bot_id}"
            settings = cloud.get_plugin_settings(bot_id, "welcome")
            buttons = settings.get("buttons", [])
            btn_text = ""
            for i, row in enumerate(buttons, 1):
                btns = ", ".join([f"[{b.get('text','')}]" for b in row])
                btn_text += f"ردیف {i}: {btns}\n"
            if not btn_text: btn_text = "هیچ دکمه‌ای تعریف نشده"
            await q.edit_message_text(
                f"🔘 <b>دکمه‌های شیشه‌ای</b>\n\n📋 فعلی:\n{btn_text}\n\n"
                f"💡 فرمت: <code>متن|نوع|مقدار</code>\n"
                f"<code>🌐 وبسایت|url|https://example.com, 🎫 پشتیبانی|support</code>\n\n"
                f"📝 <code>none</code> برای حذف همه",
                parse_mode=ParseMode.HTML)
        elif action == "reset":
            cloud.update_plugin_settings(bot_id, "welcome", {
                "message": "👋 <b>سلام! به ربات ما خوش آمدید!</b>\n\n🤖 از اینکه ما را انتخاب کردید سپاسگزاریم.",
                "photo_file_id": None,
                "buttons": [[{"text": "🎫 پشتیبانی", "type": "support"}, {"text": "ℹ️ درباره ما", "type": "text", "value": "ما یک تیم حرفه‌ای هستیم!"}]],
            })
            cloud.save(force=True); await bot_factory.restart_bot(bot_id)
            await q.edit_message_text("✅ بازنشانی شد!", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"plg:set:{bot_id}:welcome")]]))
        return
    
    if data.startswith("support:"):
        parts = data.split(":")
        action, bot_id = parts[1], parts[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        await q.answer()
        
        if action in ("edit_admin", "edit_confirm", "edit_end"):
            ctx.user_data["awaiting"] = f"support_{action}_{bot_id}"
            settings = cloud.get_plugin_settings(bot_id, "support")
            field_map = {"edit_admin": "admin_message", "edit_confirm": "confirm_message", "edit_end": "end_chat_message"}
            field_name = {"edit_admin": "پیام خوش‌آمد چت", "edit_confirm": "پیام تایید ارسال", "edit_end": "پیام پایان چت"}
            current = settings.get(field_map[action], "")
            await q.edit_message_text(
                f"✏️ <b>{field_name[action]}</b>\n\n📝 فعلی:\n<blockquote>{current}</blockquote>\n\n📤 متن جدید را ارسال کنید:",
                parse_mode=ParseMode.HTML)
        elif action == "list_tickets":
            tickets = cloud.get_bot_tickets(bot_id)
            open_tickets = [t for t in tickets if t["status"] == "open"]
            if not open_tickets:
                await q.edit_message_text("📭 تیکت بازی وجود ندارد.", parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"plg:set:{bot_id}:support")]]))
                return
            text = f"📋 <b>تیکت‌های باز ({len(open_tickets)}):</b>\n\n"
            rows = []
            for t in open_tickets[-20:]:
                text += f"• <code>{t['ticket_id']}</code> | {t['full_name']}\n"
                rows.append([InlineKeyboardButton(f"💬 {t['ticket_id']}", callback_data=f"admin_reply:{t['ticket_id']}")])
            rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"plg:set:{bot_id}:support")])
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        elif action == "reset":
            cloud.update_plugin_settings(bot_id, "support", {
                "admin_message": "📩 پیام خود را ارسال کنید. در سریع‌ترین زمان پاسخ می‌دهیم.",
                "confirm_message": "✅ پیام شما ارسال شد.",
                "end_chat_message": "🔚 چت پایان یافت.",
            })
            cloud.save(force=True); await bot_factory.restart_bot(bot_id)
            await q.edit_message_text("✅ بازنشانی شد!", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"plg:set:{bot_id}:support")]]))
        return
    
    if data.startswith("admin_reply:"):
        ticket_id = data.replace("admin_reply:", "")
        ticket = cloud.get_ticket(ticket_id)
        if not ticket: await q.answer("❌ تیکت یافت نشد", show_alert=True); return
        bot = cloud.get_bot(ticket["bot_id"])
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        ctx.user_data[f"admin_reply_{ticket['bot_id']}"] = {"active": True, "ticket_id": ticket_id, "user_id": ticket["user_id"]}
        ctx.user_data["awaiting"] = f"admin_reply_{ticket['bot_id']}"
        await q.answer()
        await q.edit_message_text(f"💬 <b>پاسخ به تیکت {ticket_id}</b>\n👤 {ticket['full_name']}\n\n✍️ پاسخ را بنویسید:", parse_mode=ParseMode.HTML)
        return
    
    if data.startswith("keyboard:"):
        parts = data.split(":")
        action, bot_id = parts[1], parts[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        await q.answer()
        
        if action == "edit_buttons":
            ctx.user_data["awaiting"] = f"keyboard_buttons_{bot_id}"
            settings = cloud.get_plugin_settings(bot_id, "keyboard")
            buttons = settings.get("buttons", [])
            btn_text = ""
            for i, row in enumerate(buttons, 1):
                btns = ", ".join([f"[{b.get('text','')}]" for b in row])
                btn_text += f"ردیف {i}: {btns}\n"
            if not btn_text: btn_text = "هیچ دکمه‌ای تعریف نشده"
            await q.edit_message_text(
                f"⌨️ <b>دکمه‌های کیبورد</b>\n\n📋 فعلی:\n{btn_text}\n\n"
                f"💡 فرمت: <code>متن|عملکرد|مقدار</code>\n"
                f"<code>🎫 پشتیبانی|command|/support, ℹ️ درباره|text|ما حرفه‌ای هستیم</code>\n\n"
                f"📝 <code>none</code> برای حذف همه",
                parse_mode=ParseMode.HTML)
        elif action == "edit_settings":
            settings = cloud.get_plugin_settings(bot_id, "keyboard")
            resize = settings.get("resize_keyboard", True)
            one_time = settings.get("one_time_keyboard", False)
            persistent = settings.get("persistent", True)
            await q.edit_message_text(
                f"⚙️ <b>تنظیمات کیبورد</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"تغییر اندازه: {'✅' if resize else '❌'}", callback_data=f"keyboard:toggle_resize:{bot_id}")],
                    [InlineKeyboardButton(f"یکبار مصرف: {'✅' if one_time else '❌'}", callback_data=f"keyboard:toggle_onetime:{bot_id}")],
                    [InlineKeyboardButton(f"دائمی: {'✅' if persistent else '❌'}", callback_data=f"keyboard:toggle_persistent:{bot_id}")],
                    [InlineKeyboardButton("🔙 بازگشت", callback_data=f"plg:set:{bot_id}:keyboard")],
                ]))
        elif action in ("toggle_resize", "toggle_onetime", "toggle_persistent"):
            settings = cloud.get_plugin_settings(bot_id, "keyboard")
            key_map = {"toggle_resize": "resize_keyboard", "toggle_onetime": "one_time_keyboard", "toggle_persistent": "persistent"}
            key = key_map[action]
            settings[key] = not settings.get(key, True)
            cloud.update_plugin_settings(bot_id, "keyboard", settings)
            cloud.save(force=True); await bot_factory.restart_bot(bot_id)
            await q.answer("✅ ذخیره شد")
        elif action == "reset":
            cloud.update_plugin_settings(bot_id, "keyboard", {
                "buttons": [
                    [{"text": "🎫 پشتیبانی", "action": "command", "value": "/support"}],
                    [{"text": "ℹ️ درباره ما", "action": "text", "value": "ما یک تیم حرفه‌ای هستیم!"}],
                ],
                "resize_keyboard": True, "one_time_keyboard": False, "persistent": True,
            })
            cloud.save(force=True); await bot_factory.restart_bot(bot_id)
            await q.edit_message_text("✅ بازنشانی شد!", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"plg:set:{bot_id}:keyboard")]]))
        return
    
    # Admin
    if not is_admin(uid):
        await q.answer("⛔️ دسترسی ندارید.", show_alert=True); return
    
    if data == "admin:panel:0":
        await q.answer()
        stats = cloud.get_stats()
        await q.edit_message_text(
            f"👑 <b>پنل ادمین</b>\n\n👥 {stats['total_users']} | 🤖 {stats['total_bots']} | 🟢 {stats['active_bots']}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 کاربران", callback_data="admin:users:0")],
                [InlineKeyboardButton("🤖 ربات‌ها", callback_data="admin:bots:0")],
                [InlineKeyboardButton("📢 همگانی", callback_data="admin:broadcast")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="nav:home")],
            ]))
        return
    
    if data.startswith("admin:users:"):
        page = int(data.split(":")[-1])
        await q.answer()
        users = sorted(cloud.get_all_users(), key=lambda u: u.get("gramcoin_balance", 0), reverse=True)
        total = len(users); start = page * ADMIN_PAGE_SIZE; end = min(start+ADMIN_PAGE_SIZE, total)
        text = f"👥 <b>کاربران</b> ({page+1})\n\n"
        rows = []
        for u in users[start:end]:
            text += f"• <b>{u.get('full_name', '—')}</b> | <code>{u.get('uid')}</code>\n  💰 {u.get('gramcoin_balance', 0)} GC\n"
            rows.append([InlineKeyboardButton(f"🎁 +50", callback_data=f"admin:givegc:{u.get('uid')}:50")])
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"admin:users:{page-1}"))
        if end < total: nav.append(InlineKeyboardButton("▶️", callback_data=f"admin:users:{page+1}"))
        if nav: rows.append(nav)
        rows.append([InlineKeyboardButton("🔙", callback_data="admin:panel:0")])
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return
    
    if data.startswith("admin:givegc:"):
        _, _, u_id, amount = data.split(":")
        cloud.add_gramcoin(int(u_id), int(amount), "admin_grant")
        cloud.save(force=True)
        await q.answer(f"✅ +{amount} GC", show_alert=True)
        return
    
    if data.startswith("admin:bots:"):
        page = int(data.split(":")[-1])
        await q.answer()
        bots = cloud.get_all_bots(); total = len(bots); start = page * ADMIN_PAGE_SIZE; end = min(start+ADMIN_PAGE_SIZE, total)
        text = f"🤖 <b>ربات‌ها</b> ({page+1})\n\n"
        for b in bots[start:end]:
            exp = cloud.check_subscription_expired(b["bot_id"])
            status = "🔴" if exp else ("🆓" if b.get("is_trial") else ("🟢" if b.get("is_active") else "⏸️"))
            text += f"{status} <code>{b['bot_id'][:8]}</code> | Owner: <code>{b.get('owner_uid')}</code>\n\n"
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"admin:bots:{page-1}"))
        if end < total: nav.append(InlineKeyboardButton("▶️", callback_data=f"admin:bots:{page+1}"))
        rows = [nav] if nav else []
        rows.append([InlineKeyboardButton("🔙", callback_data="admin:panel:0")])
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return
    
    if data == "admin:broadcast":
        ctx.user_data["awaiting"] = "broadcast"
        await q.answer()
        await q.edit_message_text("📢 پیام را ارسال کنید:", parse_mode=ParseMode.HTML)
        return
    
    await q.answer()

async def _show_plugin_settings(q, bot_id, pid):
    if pid == "welcome":
        settings = cloud.get_plugin_settings(bot_id, "welcome")
        msg = settings.get("message", "")[:200]
        photo = "✅ دارد" if settings.get("photo_file_id") else "❌ ندارد"
        btns = sum(len(r) for r in settings.get("buttons", []))
        await q.edit_message_text(
            f"⚙️ <b>خوش‌آمد پیشرفته</b>\n\n📝 پیام: <blockquote expandable>{msg}...</blockquote>\n🖼 عکس: {photo}\n🔘 دکمه‌ها: {btns}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ متن", callback_data=f"welcome:edit_text:{bot_id}")],
                [InlineKeyboardButton("🖼 عکس", callback_data=f"welcome:edit_photo:{bot_id}")],
                [InlineKeyboardButton("🔘 دکمه‌ها", callback_data=f"welcome:edit_buttons:{bot_id}")],
                [InlineKeyboardButton("♻️ بازنشانی", callback_data=f"welcome:reset:{bot_id}")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:plugins:{bot_id}")],
            ]))
    elif pid == "support":
        tickets = cloud.get_bot_tickets(bot_id)
        open_c = sum(1 for t in tickets if t["status"] == "open")
        closed_c = sum(1 for t in tickets if t["status"] == "closed")
        await q.edit_message_text(
            f"⚙️ <b>پشتیبانی چت زنده</b>\n\n📊 باز: <b>{open_c}</b> | بسته: <b>{closed_c}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ پیام خوش‌آمد", callback_data=f"support:edit_admin:{bot_id}")],
                [InlineKeyboardButton("✏️ پیام تایید", callback_data=f"support:edit_confirm:{bot_id}")],
                [InlineKeyboardButton("✏️ پیام پایان", callback_data=f"support:edit_end:{bot_id}")],
                [InlineKeyboardButton("📋 تیکت‌ها", callback_data=f"support:list_tickets:{bot_id}")],
                [InlineKeyboardButton("♻️ بازنشانی", callback_data=f"support:reset:{bot_id}")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:plugins:{bot_id}")],
            ]))
    elif pid == "keyboard":
        settings = cloud.get_plugin_settings(bot_id, "keyboard")
        btns = sum(len(r) for r in settings.get("buttons", []))
        await q.edit_message_text(
            f"⌨️ <b>کیبورد اختصاصی</b>\n\n🔘 دکمه‌ها: {btns}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔘 مدیریت دکمه‌ها", callback_data=f"keyboard:edit_buttons:{bot_id}")],
                [InlineKeyboardButton("⚙️ تنظیمات", callback_data=f"keyboard:edit_settings:{bot_id}")],
                [InlineKeyboardButton("♻️ بازنشانی", callback_data=f"keyboard:reset:{bot_id}")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:plugins:{bot_id}")],
            ]))
    else:
        await q.edit_message_text(
            f"⚙️ <b>{AVAILABLE_PLUGINS.get(pid, {}).get('name','')}</b>\n\nدر نسخه‌های آینده...",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:plugins:{bot_id}")]]))

# ══════════════════════════════════════════════════════════════════════
# MESSAGE HANDLER
# ══════════════════════════════════════════════════════════════════════
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text_in = (update.message.text or "").strip() if update.message.text else ""
    awaiting = ctx.user_data.get("awaiting")
    
    if text_in == "/cancel":
        await cmd_cancel(update, ctx); return
    
    if text_in in REPLY_BUTTONS and not awaiting:
        action = REPLY_BUTTONS[text_in]
        if action == "menu:mybots":
            text, kb = build_mybots_text(uid)
            await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif action == "menu:account":
            await update.message.reply_text(build_account_text(uid), parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 تراکنش‌ها", callback_data="nav:txs")]]))
        elif action == "menu:rewards":
            await update.message.reply_text(build_rewards_text(uid), parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎁 دریافت جایزه", callback_data="daily:claim")]]))
        elif action == "menu:help":
            await update.message.reply_text(build_help_text(), parse_mode=ParseMode.HTML, reply_markup=kb_main_reply(uid))
        elif action == "admin:panel:0" and is_admin(uid):
            stats = cloud.get_stats()
            await update.message.reply_text(
                f"👑 <b>پنل ادمین</b>\n\n👥 {stats['total_users']} | 🤖 {stats['total_bots']}",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👥 کاربران", callback_data="admin:users:0")],
                    [InlineKeyboardButton("🤖 ربات‌ها", callback_data="admin:bots:0")],
                    [InlineKeyboardButton("📢 همگانی", callback_data="admin:broadcast")],
                ]))
        return
    
    if awaiting == "bot_token":
        ctx.user_data.pop("awaiting", None)
        bot_token = text_in.strip()
        if not bot_token or ":" not in bot_token or len(bot_token) < 40:
            await update.message.reply_text("❌ <b>توکن نامعتبر است!</b>", parse_mode=ParseMode.HTML, reply_markup=kb_main_reply(uid))
            return
        try:
            temp_bot = Bot(token=bot_token)
            me = await temp_bot.get_me()
        except InvalidToken:
            await update.message.reply_text("❌ توکن نامعتبر!", parse_mode=ParseMode.HTML, reply_markup=kb_main_reply(uid))
            return
        user_bots = cloud.get_user_bots(uid)
        if len(user_bots) >= MAX_FREE_BOTS:
            await update.message.reply_text(f"⚠️ حداکثر {MAX_FREE_BOTS} ربات!", parse_mode=ParseMode.HTML, reply_markup=kb_main_reply(uid))
            return
        bot_data = cloud.create_bot(uid, bot_token)
        cloud.save(force=True)
        await bot_factory.start_bot(bot_data["bot_id"], bot_token)
        await update.message.reply_text(
            f"✅ <b>ربات با موفقیت ساخته شد!</b>\n\n"
            f"🤖 @{me.username}\n🆔 <code>{bot_data['bot_id']}</code>\n"
            f"🆓 {TRIAL_DAYS} روز آزمایشی فعال شد!",
            parse_mode=ParseMode.HTML, reply_markup=kb_main_reply(uid))
        return
    
    if awaiting and awaiting.startswith("welcome_photo_") and update.message.photo:
        bot_id = awaiting.replace("welcome_photo_", "")
        ctx.user_data.pop("awaiting", None)
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await update.message.reply_text("❌ دسترسی ندارید.", reply_markup=kb_main_reply(uid))
            return
        photo = update.message.photo[-1]
        file_id = photo.file_id
        settings = cloud.get_plugin_settings(bot_id, "welcome")
        settings["photo_file_id"] = file_id
        cloud.update_plugin_settings(bot_id, "welcome", settings)
        cloud.save(force=True)
        await bot_factory.restart_bot(bot_id)
        await update.message.reply_text(
            f"✅ <b>عکس با موفقیت تنظیم شد!</b>\n🖼 File ID: <code>{file_id[:20]}...</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تنظیمات بیشتر", callback_data=f"plg:set:{bot_id}:welcome")]]))
        return
    
    if awaiting and awaiting.startswith("welcome_photo_"):
        bot_id = awaiting.replace("welcome_photo_", "")
        ctx.user_data.pop("awaiting", None)
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await update.message.reply_text("❌ دسترسی ندارید.", reply_markup=kb_main_reply(uid))
            return
        settings = cloud.get_plugin_settings(bot_id, "welcome")
        if text_in.lower() == "none":
            settings["photo_file_id"] = None
            cloud.update_plugin_settings(bot_id, "welcome", settings)
            cloud.save(force=True)
            await bot_factory.restart_bot(bot_id)
            await update.message.reply_text("🖼 <b>عکس حذف شد!</b>", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تنظیمات بیشتر", callback_data=f"plg:set:{bot_id}:welcome")]]))
        else:
            await update.message.reply_text("❌ برای حذف، <code>none</code> ارسال کنید. برای تنظیم، عکس را آپلود کنید.", parse_mode=ParseMode.HTML)
        return
    
    if awaiting and awaiting.startswith("welcome_text_"):
        bot_id = awaiting.replace("welcome_text_", "")
        ctx.user_data.pop("awaiting", None)
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await update.message.reply_text("❌ دسترسی ندارید.", reply_markup=kb_main_reply(uid))
            return
        settings = cloud.get_plugin_settings(bot_id, "welcome")
        has_photo = settings.get("photo_file_id")
        max_len = 1024 if has_photo else 4096
        if len(text_in) > max_len:
            await update.message.reply_text(f"❌ حداکثر {max_len} کاراکتر مجاز است.", parse_mode=ParseMode.HTML, reply_markup=kb_main_reply(uid))
            return
        settings["message"] = text_in
        cloud.update_plugin_settings(bot_id, "welcome", settings)
        cloud.save(force=True)
        await bot_factory.restart_bot(bot_id)
        await update.message.reply_text("✅ <b>متن پیام ذخیره شد!</b>", parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تنظیمات بیشتر", callback_data=f"plg:set:{bot_id}:welcome")]]))
        return
    
    if awaiting and awaiting.startswith("welcome_buttons_"):
        bot_id = awaiting.replace("welcome_buttons_", "")
        ctx.user_data.pop("awaiting", None)
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await update.message.reply_text("❌ دسترسی ندارید.", reply_markup=kb_main_reply(uid))
            return
        settings = cloud.get_plugin_settings(bot_id, "welcome")
        if text_in.lower() == "none":
            settings["buttons"] = []
            cloud.update_plugin_settings(bot_id, "welcome", settings)
            cloud.save(force=True)
            await bot_factory.restart_bot(bot_id)
            await update.message.reply_text("🗑 <b>همه دکمه‌ها حذف شدند!</b>", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تنظیمات بیشتر", callback_data=f"plg:set:{bot_id}:welcome")]]))
            return
        new_buttons = []
        for line in text_in.split("\n"):
            line = line.strip()
            if not line: continue
            btn_texts = [b.strip() for b in line.split(",")]
            current_row = []
            for btn_text_full in btn_texts:
                parts = btn_text_full.split("|")
                if len(parts) < 2: continue
                btn_text = parts[0].strip()
                btn_type = parts[1].strip().lower()
                btn_value = parts[2].strip() if len(parts) > 2 else ""
                btn = {"text": btn_text, "type": btn_type}
                if btn_type == "url": btn["url"] = btn_value
                elif btn_type == "text": btn["value"] = btn_value
                elif btn_type == "callback": btn["callback"] = btn_value[:64]
                elif btn_type == "support": pass
                current_row.append(btn)
            if current_row:
                new_buttons.append(current_row)
        if not new_buttons:
            await update.message.reply_text("❌ هیچ دکمه معتبری یافت نشد.", reply_markup=kb_main_reply(uid))
            return
        settings["buttons"] = new_buttons
        cloud.update_plugin_settings(bot_id, "welcome", settings)
        cloud.save(force=True)
        await bot_factory.restart_bot(bot_id)
        total_buttons = sum(len(r) for r in new_buttons)
        await update.message.reply_text(f"✅ <b>{total_buttons} دکمه در {len(new_buttons)} ردیف ذخیره شد!</b>", parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تنظیمات بیشتر", callback_data=f"plg:set:{bot_id}:welcome")]]))
        return
    
    if awaiting and awaiting.startswith("support_"):
        remainder = awaiting.replace("support_", "")
        parts = remainder.split("_", 1)
        if len(parts) != 2:
            ctx.user_data.pop("awaiting", None)
            return
        action, bot_id = parts[0], parts[1]
        ctx.user_data.pop("awaiting", None)
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await update.message.reply_text("❌ دسترسی ندارید.", reply_markup=kb_main_reply(uid))
            return
        settings = cloud.get_plugin_settings(bot_id, "support")
        field_map = {"edit_admin": "admin_message", "edit_confirm": "confirm_message", "edit_end": "end_chat_message"}
        field = field_map.get(action)
        if field:
            settings[field] = text_in
            cloud.update_plugin_settings(bot_id, "support", settings)
            cloud.save(force=True)
            await bot_factory.restart_bot(bot_id)
            await update.message.reply_text("✅ <b>پیام ذخیره شد!</b>", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تنظیمات بیشتر", callback_data=f"plg:set:{bot_id}:support")]]))
        return
    
    if awaiting and awaiting.startswith("keyboard_buttons_"):
        bot_id = awaiting.replace("keyboard_buttons_", "")
        ctx.user_data.pop("awaiting", None)
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await update.message.reply_text("❌ دسترسی ندارید.", reply_markup=kb_main_reply(uid))
            return
        settings = cloud.get_plugin_settings(bot_id, "keyboard")
        if text_in.lower() == "none":
            settings["buttons"] = []
            cloud.update_plugin_settings(bot_id, "keyboard", settings)
            cloud.save(force=True)
            await bot_factory.restart_bot(bot_id)
            await update.message.reply_text("🗑 <b>همه دکمه‌ها حذف شدند!</b>", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تنظیمات بیشتر", callback_data=f"plg:set:{bot_id}:keyboard")]]))
            return
        new_buttons = []
        for line in text_in.split("\n"):
            line = line.strip()
            if not line: continue
            btn_texts = [b.strip() for b in line.split(",")]
            current_row = []
            for btn_text_full in btn_texts:
                parts = btn_text_full.split("|")
                if len(parts) < 2: continue
                btn_text = parts[0].strip()
                btn_action = parts[1].strip().lower()
                btn_value = parts[2].strip() if len(parts) > 2 else ""
                btn = {"text": btn_text, "action": btn_action}
                if btn_action in ("text", "command", "url"):
                    btn["value"] = btn_value
                current_row.append(btn)
            if current_row:
                new_buttons.append(current_row)
        if not new_buttons:
            await update.message.reply_text("❌ هیچ دکمه معتبری یافت نشد.", reply_markup=kb_main_reply(uid))
            return
        settings["buttons"] = new_buttons
        cloud.update_plugin_settings(bot_id, "keyboard", settings)
        cloud.save(force=True)
        await bot_factory.restart_bot(bot_id)
        total_buttons = sum(len(r) for r in new_buttons)
        await update.message.reply_text(f"✅ <b>{total_buttons} دکمه در {len(new_buttons)} ردیف ذخیره شد!</b>", parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تنظیمات بیشتر", callback_data=f"plg:set:{bot_id}:keyboard")]]))
        return
    
    # پاسخ ادمین به تیکت
    for key in list(ctx.user_data.keys()):
        if key.startswith("admin_reply_"):
            reply_state = ctx.user_data.get(key)
            if reply_state and reply_state.get("active"):
                bot_id = key.replace("admin_reply_", "")
                ticket_id = reply_state.get("ticket_id")
                target_user_id = reply_state.get("user_id")
                ticket = cloud.get_ticket(ticket_id)
                if not ticket or ticket["status"] != "open":
                    ctx.user_data.pop(key, None)
                    await update.message.reply_text("⚠️ تیکت بسته شده است.", reply_markup=kb_main_reply(uid))
                    return
                bot = cloud.get_bot(bot_id)
                if not bot or bot.get("owner_uid") != uid:
                    ctx.user_data.pop(key, None)
                    return
                try:
                    temp_bot = Bot(token=bot["bot_token"])
                    await temp_bot.send_message(
                        chat_id=target_user_id,
                        text=f"💬 <b>پاسخ پشتیبانی</b>\n🎫 تیکت: <code>{ticket_id}</code>\n\n{text_in}",
                        parse_mode=ParseMode.HTML)
                    await update.message.reply_text(
                        f"✅ <b>پاسخ ارسال شد!</b>\n👤 به: {ticket['full_name']}\n💬 برای پاسخ دیگر، همینجا بنویسید.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ بستن تیکت", callback_data=f"close_ticket:{ticket_id}")]]))
                    cloud.update_bot_stats(bot_id, messages_sent=1)
                except Forbidden:
                    await update.message.reply_text("❌ کاربر ربات را بلاک کرده.")
                except Exception as e:
                    await update.message.reply_text(f"❌ خطا: {str(e)[:100]}")
                return
    
    if not is_admin(uid): return
    
    if awaiting == "broadcast":
        ctx.user_data.pop("awaiting", None)
        users = cloud.get_all_users()
        sent = 0
        for u in users:
            try:
                await ctx.bot.send_message(
                    chat_id=int(u.get("uid_str") or u.get("uid")),
                    text=text_in, parse_mode=ParseMode.HTML)
                sent += 1
                await asyncio.sleep(0.05)
            except: pass
        await update.message.reply_text(f"✅ ارسال شد: {sent}/{len(users)}", parse_mode=ParseMode.HTML, reply_markup=kb_main_reply(uid))
        return

# ══════════════════════════════════════════════════════════════════════
# BACKGROUND TASKS
# ══════════════════════════════════════════════════════════════════════
async def subscription_watcher(app: Application):
    while True:
        try:
            await asyncio.sleep(3600)
            for bot_data in cloud.get_all_bots():
                bot_id = bot_data["bot_id"]
                if not bot_data.get("is_active"): continue
                if cloud.check_subscription_expired(bot_id):
                    cloud.deactivate_bot(bot_id)
                    await bot_factory.stop_bot(bot_id)
                    cloud.save(force=True)
                    try:
                        await app.bot.send_message(
                            chat_id=bot_data["owner_uid"],
                            text=f"🔴 <b>اشتراک ربات {bot_id[:8]} منقضی شد!</b>",
                            parse_mode=ParseMode.HTML)
                    except: pass
        except asyncio.CancelledError: break
        except Exception as e: log.error(f"subscription_watcher error: {e}")

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
    await bot_factory.start_all_active_bots()
    asyncio.create_task(subscription_watcher(app))
    asyncio.create_task(autosave_loop(app))
    log.info("GramBotSaz v4.1 ready ✅")

async def on_shutdown(app: Application):
    await bot_factory.stop_all_bots()
    cloud.save(force=True)

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
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO) & ~filters.COMMAND & filters.ChatType.PRIVATE,
        on_message))
    
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
        def index(): return "🤖 GramBotSaz v4.1 Alive!", 200
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
            await app.bot.set_webhook(
                url=full_url,
                allowed_updates=["message", "callback_query"],
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
        app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)

if __name__ == "__main__":
    main()
