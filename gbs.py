#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GramBotSaz v2.1 — پلتفرم ربات‌ساز با پلاگین پشتیبانی حرفه‌ای
════════════════════════════════════════════════════════════════════
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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, InvalidToken
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, filters, MessageHandler,
)

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

GRAMCOIN_SYMBOL = "GC"
REFERRAL_BONUS_GC = 50
DAILY_REWARD_GC = 5
SUBSCRIPTION_WEEKLY_GC = 50
SUBSCRIPTION_MONTHLY_GC = 150
SUBSCRIPTION_WEEKLY_DAYS = 7
SUBSCRIPTION_MONTHLY_DAYS = 30

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))
PLATFORM_BOT_USERNAME = ""
ADMIN_PAGE_SIZE = 8

AVAILABLE_PLUGINS = {
    "echo": {"name": "🤖 Echo", "description": "تکرار پیام", "cost_gc": 0, "is_premium": False, "has_settings": False},
    "welcome": {"name": "👋 خوش‌آمد", "description": "پیام خوش‌آمدگویی", "cost_gc": 0, "is_premium": False, "has_settings": True},
    "auto_reply": {"name": "💬 پاسخ خودکار", "description": "پاسخ به کلمات کلیدی", "cost_gc": 0, "is_premium": False, "has_settings": True},
    "broadcast": {"name": "📢 همگانی", "description": "ارسال به همه کاربران", "cost_gc": 0, "is_premium": False, "has_settings": False},
    "stats": {"name": "📊 آمار", "description": "آمار پایه", "cost_gc": 0, "is_premium": False, "has_settings": False},
    "support": {"name": "🎫 پشتیبانی", "description": "سیستم تیکت حرفه‌ای", "cost_gc": 100, "is_premium": True, "has_settings": True},
    "shop": {"name": "🛒 فروشگاه", "description": "فروشگاه آنلاین", "cost_gc": 150, "is_premium": True, "has_settings": True},
    "form_builder": {"name": "📝 فرم‌ساز", "description": "ساخت فرم", "cost_gc": 80, "is_premium": True, "has_settings": True},
    "signal": {"name": "📊 سیگنال", "description": "سیستم سیگنال VIP", "cost_gc": 200, "is_premium": True, "has_settings": True},
    "referral": {"name": "👥 زیرمجموعه", "description": "سیستم رفرال", "cost_gc": 120, "is_premium": True, "has_settings": True},
    "payment": {"name": "💳 پرداخت", "description": "درگاه پرداخت", "cost_gc": 180, "is_premium": True, "has_settings": True},
}

DEFAULT_SUPPORT_SETTINGS = {
    "welcome_message": "👋 <b>به پشتیبانی خوش آمدید!</b>\n\nلطفاً پیام خود را بنویسید تا در سریع‌ترین زمان ممکن پاسخ بگیرید.",
    "ticket_received_message": "✅ <b>تیکت شما با موفقیت ثبت شد!</b>\n\n🎫 شماره تیکت: <code>{ticket_id}</code>\n⏱ تیم پشتیبانی به زودی پاسخ خواهد داد.",
    "ticket_closed_message": "✅ <b>تیکت شما بسته شد.</b>\n\n🎫 شماره: <code>{ticket_id}</code>\n\nبرای ارسال تیکت جدید، دوباره /support را بزنید.",
    "categories": ["عمومی", "فنی", "مالی", "پیشنهاد"],
    "anonymous_mode": False,
    "admin_id": None,  # اگر None باشد، از owner_uid استفاده می‌شود
}

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
# CLOUD STORAGE
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
            "tickets": {},  # سیستم تیکت: {ticket_id: {bot_id, user_id, status, messages, ...}}
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
                    for b in self._data["bots"].values():
                        b.setdefault("enabled_plugins", [])
                        b.setdefault("purchased_plugins", [])
                        b.setdefault("plugin_settings", {})
                        b.setdefault("subscription_expiry", None)
                        b.setdefault("subscription_plan", None)
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
    
    # ═══════ USER MANAGEMENT ═══════
    def ensure_user(self, uid: int, username: str = "", full_name: str = "") -> dict:
        key = str(uid)
        with self._lock:
            if key not in self._data["users"]:
                ref_code = hashlib.md5(f"{uid}{time.time()}".encode()).hexdigest()[:8]
                self._data["users"][key] = {
                    "uid": uid, "username": username or "", "full_name": full_name or "",
                    "gramcoin_balance": 0, "total_earned_gc": 0,
                    "referral_code": ref_code, "referred_by": None,
                    "created_at": now_ir().isoformat(),
                    "last_daily_claim": None, "bots_created": [],
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
    
    # ═══════ BOT MANAGEMENT ═══════
    def create_bot(self, owner_uid: int, bot_token: str) -> Optional[dict]:
        bot_id = hashlib.md5(f"{owner_uid}{bot_token}{time.time()}".encode()).hexdigest()[:12]
        with self._lock:
            self._data["bots"][bot_id] = {
                "bot_id": bot_id, "owner_uid": owner_uid, "bot_token": bot_token,
                "bot_username": "", "is_active": False,
                "created_at": now_ir().isoformat(), "last_activity": None,
                "enabled_plugins": [], "purchased_plugins": [],
                "plugin_settings": {},
                "subscription_expiry": None, "subscription_plan": None,
                "bot_users": [], "next_ticket_id": 1001,
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
    
    # ═══════ SUBSCRIPTION ═══════
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
    
    # ═══════ PLUGINS ═══════
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
            settings = bot.get("plugin_settings", {}).get(plugin_id, {})
            # merge with defaults
            if plugin_id == "support":
                merged = dict(DEFAULT_SUPPORT_SETTINGS)
                merged.update(settings)
                return merged
            return settings
    
    # ═══════ TICKET SYSTEM ═══════
    def create_ticket(self, bot_id: str, user_id: int, username: str, full_name: str, category: str = "عمومی") -> Optional[dict]:
        with self._lock:
            bot = self._data["bots"].get(bot_id)
            if not bot: return None
            
            ticket_num = bot.get("next_ticket_id", 1001)
            bot["next_ticket_id"] = ticket_num + 1
            
            ticket_id = f"T{ticket_num}"
            self._data["tickets"][ticket_id] = {
                "ticket_id": ticket_id,
                "bot_id": bot_id,
                "user_id": user_id,
                "username": username,
                "full_name": full_name,
                "category": category,
                "status": "open",
                "created_at": now_ir().isoformat(),
                "closed_at": None,
                "messages": [],
                "admin_message_id": None,  # ID پیام در PV ادمین
            }
            self._dirty = True
            return dict(self._data["tickets"][ticket_id])
    
    def add_ticket_message(self, ticket_id: str, sender_type: str, sender_id: int, text: str, message_type: str = "text"):
        with self._lock:
            ticket = self._data["tickets"].get(ticket_id)
            if not ticket: return
            ticket["messages"].append({
                "sender_type": sender_type,  # "user" or "admin"
                "sender_id": sender_id,
                "text": text,
                "message_type": message_type,
                "timestamp": now_ir().isoformat(),
            })
            self._dirty = True
    
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
    
    def get_bot_tickets(self, bot_id: str, status: str = None) -> list:
        with self._lock:
            result = []
            for t in self._data["tickets"].values():
                if t["bot_id"] != bot_id: continue
                if status and t["status"] != status: continue
                result.append(dict(t))
            return result
    
    def get_bot_ticket_stats(self, bot_id: str) -> dict:
        with self._lock:
            stats = {"total": 0, "open": 0, "closed": 0}
            for t in self._data["tickets"].values():
                if t["bot_id"] != bot_id: continue
                stats["total"] += 1
                stats[t["status"]] = stats.get(t["status"], 0) + 1
            return stats
    
    # ═══════ REFERRAL ═══════
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
        self._plugin_handlers["auto_reply"] = self._setup_auto_reply
        self._plugin_handlers["broadcast"] = self._setup_broadcast
        self._plugin_handlers["stats"] = self._setup_stats
        self._plugin_handlers["support"] = self._setup_support
    
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
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_handler), group=10)
    
    def _setup_welcome(self, app: Application, bot_id: str):
        async def welcome_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            settings = cloud.get_plugin_settings(bot_id, "welcome")
            msg = settings.get("message", "👋 <b>سلام! خوش آمدید!</b>")
            cloud.add_bot_user(bot_id, update.effective_user.id,
                update.effective_user.username or "",
                f"{update.effective_user.first_name or ''} {update.effective_user.last_name or ''}".strip())
            try:
                await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
                cloud.update_bot_stats(bot_id, messages_received=1, messages_sent=1)
            except: pass
        app.add_handler(CommandHandler("start", welcome_handler), group=5)
    
    def _setup_auto_reply(self, app: Application, bot_id: str):
        async def auto_reply_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if not update.message or not update.message.text: return
            settings = cloud.get_plugin_settings(bot_id, "auto_reply")
            replies = settings.get("replies", {})
            text_lower = update.message.text.lower()
            for keyword, reply in replies.items():
                if keyword.lower() in text_lower:
                    try:
                        await update.message.reply_text(reply, parse_mode=ParseMode.HTML)
                        cloud.update_bot_stats(bot_id, messages_sent=1)
                    except: pass
                    break
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, auto_reply_handler), group=20)
    
    def _setup_broadcast(self, app: Application, bot_id: str):
        async def broadcast_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            bot_data = cloud.get_bot(bot_id)
            if not bot_data or bot_data.get("owner_uid") != update.effective_user.id:
                await update.message.reply_text("⛔️ فقط مالک ربات دسترسی دارد.")
                return
            if not ctx.args:
                await update.message.reply_text(
                    "📢 <b>استفاده:</b>\n<code>/broadcast پیام شما</code>",
                    parse_mode=ParseMode.HTML)
                return
            message = " ".join(ctx.args)
            bot_users = bot_data.get("bot_users", [])
            sent = failed = 0
            for user in bot_users:
                try:
                    await ctx.bot.send_message(chat_id=user["user_id"], text=message, parse_mode=ParseMode.HTML)
                    sent += 1
                    await asyncio.sleep(0.05)
                except: failed += 1
            await update.message.reply_text(
                f"✅ <b>ارسال همگانی کامل شد!</b>\n\n✅ موفق: {sent}\n❌ ناموفق: {failed}",
                parse_mode=ParseMode.HTML)
        app.add_handler(CommandHandler("broadcast", broadcast_command), group=30)
    
    def _setup_stats(self, app: Application, bot_id: str):
        async def stats_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            bot_data = cloud.get_bot(bot_id)
            if not bot_data or bot_data.get("owner_uid") != update.effective_user.id:
                await update.message.reply_text("⛔️ فقط مالک ربات دسترسی دارد.")
                return
            stats = bot_data.get("stats", {})
            users_count = len(bot_data.get("bot_users", []))
            await update.message.reply_text(
                f"📊 <b>آمار ربات شما</b>\n\n"
                f"👥 <b>کاربران:</b> {users_count}\n"
                f"📨 <b>پیام دریافتی:</b> {stats.get('messages_received', 0)}\n"
                f"📤 <b>پیام ارسالی:</b> {stats.get('messages_sent', 0)}",
                parse_mode=ParseMode.HTML)
        app.add_handler(CommandHandler("stats", stats_command), group=30)
    
    # ═══════ SUPPORT PLUGIN (پیشرفته) ═══════
    def _setup_support(self, app: Application, bot_id: str):
        """پلاگین پشتیبانی حرفه‌ای با سیستم تیکت کامل"""
        
        async def support_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            """دستور /support یا /start برای شروع تیکت"""
            settings = cloud.get_plugin_settings(bot_id, "support")
            welcome_msg = settings.get("welcome_message", DEFAULT_SUPPORT_SETTINGS["welcome_message"])
            categories = settings.get("categories", DEFAULT_SUPPORT_SETTINGS["categories"])
            
            cloud.add_bot_user(bot_id, update.effective_user.id,
                update.effective_user.username or "",
                f"{update.effective_user.first_name or ''} {update.effective_user.last_name or ''}".strip())
            
            # نمایش دکمه‌های دسته‌بندی
            rows = []
            for cat in categories:
                rows.append([InlineKeyboardButton(f"📁 {cat}", callback_data=f"sup_cat:{bot_id}:{cat}")])
            rows.append([InlineKeyboardButton("❌ لغو", callback_data=f"sup_cancel:{bot_id}")])
            
            await update.message.reply_text(
                f"{welcome_msg}\n\n<b>لطفاً دسته‌بندی تیکت خود را انتخاب کنید:</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(rows))
            
            cloud.update_bot_stats(bot_id, messages_received=1, messages_sent=1)
        
        async def support_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            """مدیریت دکمه‌های تیکت"""
            q = update.callback_query
            data = q.data or ""
            
            if data.startswith("sup_cat:"):
                _, _, b_id, category = data.split(":", 3)
                if b_id != bot_id: return
                
                # ساخت تیکت جدید
                user = q.from_user
                ticket = cloud.create_ticket(
                    bot_id, user.id,
                    user.username or "",
                    f"{user.first_name or ''} {user.last_name or ''}".strip(),
                    category
                )
                
                if not ticket:
                    await q.answer("❌ خطا", show_alert=True)
                    return
                
                await q.answer()
                
                settings = cloud.get_plugin_settings(bot_id, "support")
                received_msg = settings.get("ticket_received_message", DEFAULT_SUPPORT_SETTINGS["ticket_received_message"])
                received_msg = received_msg.replace("{ticket_id}", ticket["ticket_id"])
                
                # پیام تأیید به کاربر
                try:
                    await q.edit_message_text(
                        f"{received_msg}\n\n💬 <b>حالا پیام خود را ارسال کنید:</b>",
                        parse_mode=ParseMode.HTML)
                except: pass
                
                # state برای دریافت پیام
                ctx.user_data[f"ticket_state_{bot_id}"] = {
                    "state": "awaiting_message",
                    "ticket_id": ticket["ticket_id"],
                }
                cloud.save(force=True)
                return
            
            if data.startswith("sup_cancel:"):
                await q.answer("❌ لغو شد")
                try: await q.edit_message_text("❌ عملیات لغو شد.", parse_mode=ParseMode.HTML)
                except: pass
                ctx.user_data.pop(f"ticket_state_{bot_id}", None)
                return
            
            # دکمه‌های ادمین برای پاسخ/بستن تیکت
            if data.startswith("sup_reply:"):
                ticket_id = data.split(":")[1]
                ticket = cloud.get_ticket(ticket_id)
                if not ticket or ticket["bot_id"] != bot_id: return
                
                ctx.user_data[f"admin_ticket_reply_{bot_id}"] = {
                    "state": "awaiting_admin_reply",
                    "ticket_id": ticket_id,
                }
                await q.answer()
                await q.message.reply_text(
                    f"💬 <b>در حال پاسخ به تیکت {ticket_id}</b>\n\n"
                    f"👤 کاربر: {ticket['full_name']}\n\n"
                    f"✍️ پاسخ خود را بنویسید:\n(برای لغو /cancel)",
                    parse_mode=ParseMode.HTML)
                return
            
            if data.startswith("sup_close:"):
                ticket_id = data.split(":")[1]
                ticket = cloud.get_ticket(ticket_id)
                if not ticket or ticket["bot_id"] != bot_id: return
                
                cloud.close_ticket(ticket_id)
                cloud.save(force=True)
                
                settings = cloud.get_plugin_settings(bot_id, "support")
                closed_msg = settings.get("ticket_closed_message", DEFAULT_SUPPORT_SETTINGS["ticket_closed_message"])
                closed_msg = closed_msg.replace("{ticket_id}", ticket_id)
                
                # اطلاع به کاربر
                try:
                    await ctx.bot.send_message(
                        chat_id=ticket["user_id"],
                        text=closed_msg,
                        parse_mode=ParseMode.HTML)
                except: pass
                
                await q.answer("✅ تیکت بسته شد", show_alert=True)
                try:
                    await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ تیکت بسته شد", callback_data="noop")]
                    ]))
                except: pass
                return
            
            if data.startswith("sup_history:"):
                ticket_id = data.split(":")[1]
                ticket = cloud.get_ticket(ticket_id)
                if not ticket or ticket["bot_id"] != bot_id: return
                
                await q.answer()
                history = _build_ticket_history(ticket)
                await q.message.reply_text(history, parse_mode=ParseMode.HTML)
                return
        
        async def support_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            """دریافت پیام کاربر برای تیکت یا پاسخ ادمین"""
            user = update.effective_user
            
            # بررسی پاسخ ادمین به تیکت
            admin_state = ctx.user_data.get(f"admin_ticket_reply_{bot_id}")
            if admin_state and admin_state.get("state") == "awaiting_admin_reply":
                ticket_id = admin_state.get("ticket_id")
                ctx.user_data.pop(f"admin_ticket_reply_{bot_id}", None)
                
                ticket = cloud.get_ticket(ticket_id)
                if not ticket or ticket["status"] != "open":
                    await update.message.reply_text("⚠️ تیکت موجود نیست یا بسته شده است.")
                    return
                
                # ذخیره پیام
                cloud.add_ticket_message(ticket_id, "admin", user.id, update.message.text or "[Media]")
                cloud.save(force=True)
                
                # ارسال پاسخ به کاربر
                try:
                    await ctx.bot.send_message(
                        chat_id=ticket["user_id"],
                        text=f"💬 <b>پاسخ پشتیبانی</b>\n\n"
                             f"🎫 تیکت: <code>{ticket_id}</code>\n\n"
                             f"{update.message.text}",
                        parse_mode=ParseMode.HTML)
                    await update.message.reply_text("✅ <b>پاسخ شما با موفقیت ارسال شد!</b>", parse_mode=ParseMode.HTML)
                except Exception as e:
                    await update.message.reply_text(f"❌ خطا در ارسال: {str(e)[:100]}")
                return
            
            # بررسی پیام کاربر برای تیکت
            user_state = ctx.user_data.get(f"ticket_state_{bot_id}")
            if not user_state or user_state.get("state") != "awaiting_message":
                return
            
            ticket_id = user_state.get("ticket_id")
            ticket = cloud.get_ticket(ticket_id)
            if not ticket or ticket["status"] != "open":
                ctx.user_data.pop(f"ticket_state_{bot_id}", None)
                await update.message.reply_text("⚠️ تیکت شما بسته شده است. برای تیکت جدید /support را بزنید.")
                return
            
            # ذخیره پیام کاربر
            cloud.add_ticket_message(ticket_id, "user", user.id, update.message.text or "[Media]")
            
            # ارسال به ادمین
            bot_data = cloud.get_bot(bot_id)
            settings = cloud.get_plugin_settings(bot_id, "support")
            admin_id = settings.get("admin_id") or bot_data.get("owner_uid")
            anonymous = settings.get("anonymous_mode", False)
            
            user_info = f"👤 {mention_html(user.id, user.full_name, user.username)}\n🆔 <code>{user.id}</code>" if not anonymous else "👤 <b>کاربر ناشناس</b>"
            
            # دکمه‌های مدیریت برای ادمین
            admin_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("💬 پاسخ", callback_data=f"sup_reply:{ticket_id}"),
                    InlineKeyboardButton("✅ بستن", callback_data=f"sup_close:{ticket_id}"),
                ],
                [InlineKeyboardButton("📜 تاریخچه", callback_data=f"sup_history:{ticket_id}")],
            ])
            
            try:
                await ctx.bot.send_message(
                    chat_id=admin_id,
                    text=f"🎫 <b>تیکت جدید</b>\n\n"
                         f"🆔 شماره: <code>{ticket_id}</code>\n"
                         f"📁 دسته: <b>{ticket['category']}</b>\n"
                         f"{user_info}\n\n"
                         f"📝 <b>پیام:</b>\n{update.message.text}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=admin_kb)
                
                await update.message.reply_text(
                    f"✅ <b>پیام شما ارسال شد!</b>\n\n"
                    f"🎫 تیکت: <code>{ticket_id}</code>\n"
                    f"⏱ در اسرع وقت پاسخ خواهیم داد.\n\n"
                    f"💬 برای ارسال پیام بیشتر، همینجا تایپ کنید.",
                    parse_mode=ParseMode.HTML)
                
                cloud.update_bot_stats(bot_id, messages_received=1, messages_sent=1)
            except Forbidden:
                await update.message.reply_text("❌ ادمین ربات را بلاک کرده است.")
            except Exception as e:
                log.error(f"Support forward failed: {e}")
                await update.message.reply_text("❌ خطا در ارسال پیام.")
            
            cloud.save(force=True)
        
        async def support_cancel_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            """لغو عملیات تیکت"""
            ctx.user_data.pop(f"ticket_state_{bot_id}", None)
            ctx.user_data.pop(f"admin_ticket_reply_{bot_id}", None)
            await update.message.reply_text("❌ عملیات لغو شد.")
        
        async def my_tickets_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            """لیست تیکت‌های کاربر"""
            tickets = cloud.get_bot_tickets(bot_id)
            user_tickets = [t for t in tickets if t["user_id"] == update.effective_user.id]
            
            if not user_tickets:
                await update.message.reply_text("📭 <b>شما تیکتی ندارید.</b>\n\nبرای ارسال تیکت جدید /support را بزنید.", parse_mode=ParseMode.HTML)
                return
            
            text = "🎫 <b>تیکت‌های شما:</b>\n\n"
            for t in user_tickets[-10:]:
                status = "🟢 باز" if t["status"] == "open" else "🔴 بسته"
                text += f"• <code>{t['ticket_id']}</code> | {t['category']} | {status}\n"
            
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        
        # ثبت handler ها
        app.add_handler(CommandHandler("start", support_start), group=5)
        app.add_handler(CommandHandler("support", support_start), group=5)
        app.add_handler(CommandHandler("mytickets", my_tickets_command), group=5)
        app.add_handler(CommandHandler("cancel", support_cancel_command), group=5)
        app.add_handler(CallbackQueryHandler(support_callback, pattern=r"^sup_"), group=5)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, support_message), group=25)

def _build_ticket_history(ticket: dict) -> str:
    """ساخت تاریخچه تیکت برای نمایش"""
    text = f"📜 <b>تاریخچه تیکت {ticket['ticket_id']}</b>\n\n"
    text += f"📁 دسته: <b>{ticket['category']}</b>\n"
    text += f"👤 کاربر: {ticket['full_name']}\n"
    text += f"📅 ایجاد: {ticket['created_at'][:16]}\n"
    status = "🟢 باز" if ticket["status"] == "open" else "🔴 بسته"
    text += f"📊 وضعیت: {status}\n\n"
    text += "═══════════════════\n\n"
    
    for msg in ticket.get("messages", []):
        sender = "👤 کاربر" if msg["sender_type"] == "user" else "🛠 پشتیبانی"
        text += f"{sender}:\n{msg['text']}\n"
        text += f"<i>📅 {msg['timestamp'][:16]}</i>\n\n"
    
    return text

plugin_manager = PluginManager()

# ══════════════════════════════════════════════════════════════════════
# BOT FACTORY
# ══════════════════════════════════════════════════════════════════════
class BotFactory:
    def __init__(self):
        self._running_bots: Dict[str, asyncio.Task] = {}
        self._bot_apps: Dict[str, Application] = {}
        self._lock = asyncio.Lock()
    
    async def start_bot(self, bot_id: str, bot_token: str):
        async with self._lock:
            if bot_id in self._running_bots: return
            if cloud.check_subscription_expired(bot_id):
                cloud.deactivate_bot(bot_id)
                return
            try:
                bot_app = Application.builder().token(bot_token).build()
                plugin_manager.setup_plugins_for_bot(bot_app, bot_id)
                await bot_app.initialize()
                me = await bot_app.bot.get_me()
                cloud.update_bot_username(bot_id, me.username or "")
                cloud.save()
                task = asyncio.create_task(self._run_bot_polling(bot_app, bot_id))
                self._running_bots[bot_id] = task
                self._bot_apps[bot_id] = bot_app
                log.info(f"✅ Bot {bot_id} (@{me.username}) started")
            except InvalidToken:
                log.error(f"❌ Invalid token for bot {bot_id}")
            except Exception as e:
                log.error(f"❌ Failed to start bot {bot_id}: {e}")
    
    async def stop_bot(self, bot_id: str):
        async with self._lock:
            if bot_id in self._running_bots:
                task = self._running_bots[bot_id]
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass
                if bot_id in self._bot_apps:
                    try: await self._bot_apps[bot_id].shutdown()
                    except: pass
                    del self._bot_apps[bot_id]
                del self._running_bots[bot_id]
    
    async def restart_bot(self, bot_id: str):
        bot_data = cloud.get_bot(bot_id)
        if not bot_data: return
        await self.stop_bot(bot_id)
        if bot_data.get("is_active"):
            await asyncio.sleep(1)
            await self.start_bot(bot_id, bot_data["bot_token"])
    
    async def _run_bot_polling(self, app: Application, bot_id: str):
        try:
            await app.start()
            await app.updater.start_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)
            while True: await asyncio.sleep(60)
        except asyncio.CancelledError: pass
        except Exception as e: log.error(f"Bot {bot_id} polling error: {e}")
        finally:
            try:
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
            except: pass
    
    async def start_all_active_bots(self):
        for bot_data in cloud.get_all_bots():
            if bot_data.get("is_active") and not cloud.check_subscription_expired(bot_data["bot_id"]):
                await self.start_bot(bot_data["bot_id"], bot_data["bot_token"])
    
    async def stop_all_bots(self):
        for bot_id in list(self._running_bots.keys()):
            await self.stop_bot(bot_id)

bot_factory = BotFactory()

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
         InlineKeyboardButton("🤖 ربات‌های من", callback_data="menu:mybots")],
        [InlineKeyboardButton("➕ ساخت ربات جدید", callback_data="menu:create_bot"),
         InlineKeyboardButton("🎁 جایزه روزانه", callback_data="menu:daily")],
        [InlineKeyboardButton("👥 دعوت دوستان", callback_data="menu:invite"),
         InlineKeyboardButton("📊 تراکنش‌ها", callback_data="menu:transactions")],
        [InlineKeyboardButton("ℹ️ راهنما", callback_data="menu:help")],
    ]
    if uid and is_admin(uid):
        rows.append([InlineKeyboardButton("👑 پنل ادمین", callback_data="admin:panel:0")])
    return InlineKeyboardMarkup(rows)

def kb_back(dest: str = "home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"menu:{dest}")]])

def kb_bot_management(bot_id: str, bot_data: dict) -> InlineKeyboardMarkup:
    is_expired = cloud.check_subscription_expired(bot_id)
    rows = []
    if is_expired:
        rows.append([InlineKeyboardButton("💎 خرید اشتراک", callback_data=f"bot:subscribe:{bot_id}")])
    else:
        rows.append([InlineKeyboardButton("🔄 تمدید اشتراک", callback_data=f"bot:subscribe:{bot_id}")])
    rows.append([InlineKeyboardButton("🧩 مدیریت پلاگین‌ها", callback_data=f"bot:plugins:{bot_id}")])
    rows.append([InlineKeyboardButton("📊 آمار ربات", callback_data=f"bot:stats:{bot_id}")])
    rows.append([InlineKeyboardButton("🗑 حذف ربات", callback_data=f"bot:delete:{bot_id}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu:mybots")])
    return InlineKeyboardMarkup(rows)

def kb_plugins(bot_id: str) -> InlineKeyboardMarkup:
    bot_data = cloud.get_bot(bot_id)
    if not bot_data: return kb_back("mybots")
    enabled = bot_data.get("enabled_plugins", [])
    purchased = bot_data.get("purchased_plugins", [])
    rows = []
    
    rows.append([InlineKeyboardButton("─── 🆓 پلاگین‌های پایه ───", callback_data="noop")])
    for pid, plugin in AVAILABLE_PLUGINS.items():
        if plugin.get("is_premium"): continue
        is_enabled = pid in enabled
        has_settings = plugin.get("has_settings", False)
        
        btns = [InlineKeyboardButton(
            f"{'✅' if is_enabled else '⬜️'} {plugin['name']}",
            callback_data=f"plugin:toggle:{bot_id}:{pid}"
        )]
        if has_settings and is_enabled:
            btns.append(InlineKeyboardButton("⚙️", callback_data=f"plugin:settings:{bot_id}:{pid}"))
        rows.append(btns)
    
    rows.append([InlineKeyboardButton("─── 💎 پلاگین‌های پرمیوم ───", callback_data="noop")])
    for pid, plugin in AVAILABLE_PLUGINS.items():
        if not plugin.get("is_premium"): continue
        is_purchased = pid in purchased
        is_enabled = pid in enabled
        has_settings = plugin.get("has_settings", False)
        
        if is_purchased:
            btns = [InlineKeyboardButton(
                f"{'✅' if is_enabled else '⏸️'} {plugin['name']}",
                callback_data=f"plugin:toggle:{bot_id}:{pid}"
            )]
            if has_settings and is_enabled:
                btns.append(InlineKeyboardButton("⚙️", callback_data=f"plugin:settings:{bot_id}:{pid}"))
            rows.append(btns)
        else:
            rows.append([InlineKeyboardButton(
                f"🔒 {plugin['name']} ({plugin['cost_gc']} GC)",
                callback_data=f"plugin:buy:{bot_id}:{pid}"
            )])
    
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")])
    return InlineKeyboardMarkup(rows)

# ══════════════════════════════════════════════════════════════════════
# SUPPORT SETTINGS KEYBOARDS & TEXTS
# ══════════════════════════════════════════════════════════════════════
def kb_support_settings(bot_id: str, settings: dict) -> InlineKeyboardMarkup:
    anonymous = "✅ فعال" if settings.get("anonymous_mode") else "⬜️ غیرفعال"
    stats = cloud.get_bot_ticket_stats(bot_id)
    
    rows = [
        [InlineKeyboardButton("📊 آمار تیکت‌ها", callback_data=f"sup_set:stats:{bot_id}")],
        [InlineKeyboardButton("📋 لیست تیکت‌های باز", callback_data=f"sup_set:open_list:{bot_id}")],
        [InlineKeyboardButton("📋 لیست تیکت‌های بسته", callback_data=f"sup_set:closed_list:{bot_id}")],
        [InlineKeyboardButton("─── ⚙️ تنظیمات پیام‌ها ───", callback_data="noop")],
        [InlineKeyboardButton("✏️ پیام خوش‌آمد", callback_data=f"sup_set:edit:welcome:{bot_id}")],
        [InlineKeyboardButton("✏️ پیام دریافت تیکت", callback_data=f"sup_set:edit:received:{bot_id}")],
        [InlineKeyboardButton("✏️ پیام بسته شدن تیکت", callback_data=f"sup_set:edit:closed:{bot_id}")],
        [InlineKeyboardButton("─── 🗂 تنظیمات دسته‌بندی ───", callback_data="noop")],
        [InlineKeyboardButton("📁 مدیریت دسته‌بندی‌ها", callback_data=f"sup_set:categories:{bot_id}")],
        [InlineKeyboardButton("─── 🔐 تنظیمات حریم خصوصی ───", callback_data="noop")],
        [InlineKeyboardButton(f"{anonymous} حالت ناشناس", callback_data=f"sup_set:toggle_anon:{bot_id}")],
        [InlineKeyboardButton("─── 🔄 بازنشانی ───", callback_data="noop")],
        [InlineKeyboardButton("♻️ بازنشانی به پیش‌فرض", callback_data=f"sup_set:reset:{bot_id}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:plugins:{bot_id}")],
    ]
    return InlineKeyboardMarkup(rows)

def build_support_settings_text(bot_id: str, settings: dict) -> str:
    stats = cloud.get_bot_ticket_stats(bot_id)
    categories = settings.get("categories", [])
    cat_list = "، ".join(categories) if categories else "—"
    
    return (
        f"🎫 <b>تنظیمات پلاگین پشتیبانی</b>\n\n"
        f"📊 <b>آمار کلی:</b>\n"
        f"• کل تیکت‌ها: <b>{stats['total']}</b>\n"
        f"• باز: <b>{stats['open']}</b> | بسته: <b>{stats['closed']}</b>\n\n"
        f"📁 <b>دسته‌بندی‌ها:</b>\n<code>{cat_list}</code>\n\n"
        f"🔐 <b>حالت ناشناس:</b> {'✅ فعال' if settings.get('anonymous_mode') else '⬜️ غیرفعال'}\n\n"
        f"💡 <b>راهنما:</b>\n"
        f"• از دکمه‌های زیر برای شخصی‌سازی استفاده کنید\n"
        f"• در پیام‌ها می‌توانید از <code>{{ticket_id}}</code> برای نمایش شماره تیکت استفاده کنید\n"
        f"• HTML در پیام‌ها پشتیبانی می‌شود"
    )

def build_ticket_list_text(bot_id: str, status: str) -> tuple:
    """(متن، کیبورد) لیست تیکت‌ها"""
    tickets = cloud.get_bot_tickets(bot_id, status=status)
    
    if not tickets:
        return f"📭 هیچ تیکت {('بازی' if status == 'open' else 'بسته‌ای')} وجود ندارد.", InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت", callback_data=f"sup_set:main:{bot_id}")]
        ])
    
    title = "🟢 تیکت‌های باز" if status == "open" else "🔴 تیکت‌های بسته"
    text = f"<b>{title}</b>\n\n"
    rows = []
    for t in tickets[-20:]:  # ۲۰ تیکت آخر
        text += f"• <code>{t['ticket_id']}</code> | {t['category']} | {t['full_name'][:15]}\n"
        rows.append([InlineKeyboardButton(
            f"👁 {t['ticket_id']} - {t['full_name'][:15]}",
            callback_data=f"sup_set:view:{t['ticket_id']}"
        )])
    
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"sup_set:main:{bot_id}")])
    return text, InlineKeyboardMarkup(rows)

# ══════════════════════════════════════════════════════════════════════
# TEXT BUILDERS
# ══════════════════════════════════════════════════════════════════════
def build_welcome_text() -> str:
    return (
        "🤖✨ <b>به GramBotSaz خوش اومدی!</b> ✨🤖\n\n"
        "<blockquote expandable>"
        "اینجا می‌تونی بدون نیاز به کدنویسی، ربات تلگرام خودت رو بسازی و با پلاگین‌های متنوع توسعه بدی!\n"
        "</blockquote>"
        "🔥 <b>ویژگی‌های پلتفرم:</b>\n"
        "  ⚡️ ساخت ربات در کمتر از ۱ دقیقه\n"
        "  🧩 ۱۵+ پلاگین حرفه‌ای\n"
        "  ⚙️ شخصی‌سازی کامل هر پلاگین\n"
        "  💰 سیستم اقتصادی با GramCoin\n\n"
        "از منوی زیر شروع کن 👇"
    )

def build_profile_text(uid: int, u: dict) -> str:
    full = u.get("full_name", "").strip() or u.get("username", "") or "—"
    bots_count = len(u.get("bots_created", []))
    daily_status = "✅ قابل دریافت" if cloud.can_claim_daily(uid) else "⏳ فردا"
    
    return (
        f"👤 <b>پروفایل شما</b>\n\n"
        f"📛 نام: <b>{full}</b>\n"
        f"🆔 آیدی: <code>{uid}</code>\n\n"
        f"💰 <b>موجودی:</b> <code>{u.get('gramcoin_balance', 0)} GC</code>\n"
        f"📈 <b>کل درآمد:</b> <code>{u.get('total_earned_gc', 0)} GC</code>\n\n"
        f"🤖 <b>تعداد ربات‌ها:</b> {bots_count}\n"
        f"🎁 <b>جایزه روزانه:</b> {daily_status}"
    )

def build_mybots_text(uid: int) -> tuple:
    bots = cloud.get_user_bots(uid)
    if not bots:
        text = "🤖 <b>ربات‌های شما</b>\n\nهنوز رباتی نساخته‌اید."
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ ساخت ربات جدید", callback_data="menu:create_bot")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="menu:home")],
        ])
        return text, kb
    
    text = "🤖 <b>ربات‌های شما</b>\n\n"
    rows = []
    for bot in bots:
        is_expired = cloud.check_subscription_expired(bot["bot_id"])
        status = "🔴 منقضی" if is_expired else ("🟢 فعال" if bot.get("is_active") else "⏸️ غیرفعال")
        username = f"@{bot.get('bot_username')}" if bot.get("bot_username") else "—"
        enabled_plugins = len(bot.get("enabled_plugins", []))
        text += f"• <b>{bot['bot_id'][:8]}</b> | {username}\n  {status} | 🧩 {enabled_plugins} پلاگین\n\n"
        rows.append([InlineKeyboardButton(f"⚙️ مدیریت {bot['bot_id'][:8]}", callback_data=f"bot:manage:{bot['bot_id']}")])
    rows.append([InlineKeyboardButton("➕ ساخت ربات جدید", callback_data="menu:create_bot")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu:home")])
    return text, InlineKeyboardMarkup(rows)

def build_bot_detail_text(bot_id: str, bot_data: dict) -> str:
    username = f"@{bot_data.get('bot_username')}" if bot_data.get("bot_username") else "—"
    is_expired = cloud.check_subscription_expired(bot_id)
    
    if is_expired:
        sub_status = "🔴 <b>منقضی شده</b>\n⚠️ برای فعال‌سازی اشتراک بخرید."
    elif bot_data.get("is_active"):
        exp = datetime.fromisoformat(bot_data["subscription_expiry"])
        remaining = (exp - now_ir()).total_seconds()
        plan_name = "هفتگی" if bot_data.get("subscription_plan") == "weekly" else "ماهانه"
        sub_status = f"🟢 <b>فعال</b> ({plan_name})\n⏳ {_fmt_timedelta(remaining)}"
    else:
        sub_status = "⏸️ <b>غیرفعال</b>"
    
    enabled = len(bot_data.get("enabled_plugins", []))
    purchased = len(bot_data.get("purchased_plugins", []))
    stats = bot_data.get("stats", {})
    users_count = len(bot_data.get("bot_users", []))
    
    return (
        f"⚙️ <b>مدیریت ربات</b>\n\n"
        f"🆔 <b>ID:</b> <code>{bot_id}</code>\n"
        f"📛 <b>یوزرنیم:</b> {username}\n\n"
        f"💎 <b>اشتراک:</b>\n{sub_status}\n\n"
        f"🧩 <b>پلاگین‌ها:</b> {enabled} فعال | {purchased} پرمیوم\n\n"
        f"📊 <b>آمار:</b>\n"
        f"• کاربران: {users_count}\n"
        f"• پیام دریافتی: {stats.get('messages_received', 0)}\n"
        f"• پیام ارسالی: {stats.get('messages_sent', 0)}"
    )

def build_referral_text(uid: int, bot_username: str) -> str:
    u = cloud.ensure_user(uid)
    link = f"https://t.me/{bot_username}?start=ref_{u['referral_code']}"
    referrals_count = sum(1 for x in cloud.get_all_users() if x.get("referred_by") == str(uid))
    return (
        "🎁 <b>دعوت دوستان و کسب GramCoin</b>\n\n"
        f"🔗 <b>لینک شما:</b>\n<code>{link}</code>\n\n"
        f"✅ به ازای هر دعوت: <b>{REFERRAL_BONUS_GC} GC</b>\n\n"
        f"👥 دعوت‌های موفق: <b>{referrals_count}</b>"
    )

def build_help_text() -> str:
    return (
        "📖 <b>راهنمای GramBotSaz</b>\n\n"
        "<blockquote expandable>"
        "🤖 <b>مراحل ساخت ربات:</b>\n"
        "۱. ساخت ربات جدید\n"
        "۲. دریافت توکن از BotFather\n"
        "۳. خرید اشتراک\n"
        "۴. فعال‌سازی پلاگین‌ها\n"
        "۵. شخصی‌سازی هر پلاگین با دکمه ⚙️\n\n"
        "💰 <b>GramCoin (GC):</b>\n"
        f"• روزانه: <b>{DAILY_REWARD_GC} GC</b>\n"
        f"• رفرال: <b>{REFERRAL_BONUS_GC} GC</b>\n\n"
        "💎 <b>اشتراک‌ها:</b>\n"
        f"• هفتگی: <b>{SUBSCRIPTION_WEEKLY_GC} GC</b>\n"
        f"• ماهانه: <b>{SUBSCRIPTION_MONTHLY_GC} GC</b>\n"
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
    
    if not await check_membership(ctx.bot, uid):
        await update.effective_message.reply_text(
            "⛔️ <b>برای استفاده از ربات باید عضو کانال ما باشی!</b>",
            parse_mode=ParseMode.HTML, reply_markup=kb_join_required())
        return
    
    await update.effective_message.reply_text(build_welcome_text(), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
    cloud.save()

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keys_to_remove = [k for k in ctx.user_data.keys() if k.startswith("awaiting") or k.startswith("pending_") or "sup_set" in k]
    for k in keys_to_remove: ctx.user_data.pop(k, None)
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
    
    if data == "noop":
        await q.answer()
        return
    
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
                ctx.user_data.pop("pending_referral", None)
                cloud.save(force=True)
            await q.edit_message_text(build_welcome_text(), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        else:
            await q.answer("⛔️ هنوز عضو نشدی!", show_alert=True)
        return
    
    # ═══════ MAIN MENU ═══════
    if data.startswith("menu:"):
        await q.answer()
        u = cloud.ensure_user(uid)
        if data == "menu:home": await q.edit_message_text(build_welcome_text(), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        elif data == "menu:profile": await q.edit_message_text(build_profile_text(uid, u), parse_mode=ParseMode.HTML, reply_markup=kb_back())
        elif data == "menu:mybots":
            text, kb = build_mybots_text(uid)
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif data == "menu:create_bot":
            ctx.user_data["awaiting"] = "bot_token"
            await q.edit_message_text(
                "🤖 <b>ساخت ربات جدید</b>\n\n"
                "🔑 <b>لطفاً توکن ربات را از BotFather دریافت و ارسال کنید:</b>\n"
                "(برای لغو /cancel)",
                parse_mode=ParseMode.HTML)
        elif data == "menu:daily":
            if cloud.can_claim_daily(uid):
                cloud.claim_daily_reward(uid, DAILY_REWARD_GC)
                cloud.save(force=True)
                await q.edit_message_text(
                    f"🎁 <b>جایزه روزانه!</b>\n\n"
                    f"⭐️ <b>+{DAILY_REWARD_GC} GC</b> اضافه شد.\n"
                    f"💰 موجودی: <code>{cloud.get_user(uid).get('gramcoin_balance', 0)} GC</code>",
                    parse_mode=ParseMode.HTML, reply_markup=kb_back())
            else:
                await q.answer("⏳ فردا!", show_alert=True)
        elif data == "menu:invite":
            bot_username = (await ctx.bot.get_me()).username
            await q.edit_message_text(build_referral_text(uid, bot_username), parse_mode=ParseMode.HTML, reply_markup=kb_back())
        elif data == "menu:transactions":
            transactions = [tx for tx in cloud._data.get("transactions", []) if tx.get("uid") == uid][-10:]
            text = "📊 <b>تراکنش‌ها:</b>\n\n" + ("\n".join([f"• {tx['description']}\n  {tx['timestamp'][:16]}" for tx in reversed(transactions)]) if transactions else "هنوز تراکنشی ندارید.")
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_back())
        elif data == "menu:help": await q.edit_message_text(build_help_text(), parse_mode=ParseMode.HTML, reply_markup=kb_back())
        return
    
    # ═══════ BOT MANAGEMENT ═══════
    if data.startswith("bot:manage:"):
        bot_id = data.split(":")[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        await q.answer()
        await q.edit_message_text(build_bot_detail_text(bot_id, bot), parse_mode=ParseMode.HTML, reply_markup=kb_bot_management(bot_id, bot))
        return
    
    if data.startswith("bot:subscribe:"):
        bot_id = data.split(":")[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        await q.answer()
        is_expired = cloud.check_subscription_expired(bot_id)
        title = "💎 خرید اشتراک" if is_expired else "🔄 تمدید اشتراک"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🥉 هفتگی — {SUBSCRIPTION_WEEKLY_GC} GC", callback_data=f"sub:buy:{bot_id}:weekly")],
            [InlineKeyboardButton(f"🥈 ماهانه — {SUBSCRIPTION_MONTHLY_GC} GC", callback_data=f"sub:buy:{bot_id}:monthly")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")],
        ])
        await q.edit_message_text(f"<b>{title}</b>", parse_mode=ParseMode.HTML, reply_markup=kb)
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
            await q.answer(f"❌ موجودی کافی نیست! نیاز به {cost} GC", show_alert=True); return
        await q.answer("در حال پردازش...")
        if cloud.deduct_gramcoin(uid, cost, f"subscription_{plan}_{bot_id}"):
            new_expiry = cloud.activate_subscription(bot_id, plan, days)
            cloud.save(force=True)
            await bot_factory.restart_bot(bot_id)
            plan_name = "هفتگی" if plan == "weekly" else "ماهانه"
            await q.edit_message_text(
                f"✅ <b>اشتراک {plan_name} فعال شد!</b>\n\n"
                f"📅 انقضا: {new_expiry.strftime('%Y-%m-%d')}\n"
                f"💰 {cost} GC کسر شد.",
                parse_mode=ParseMode.HTML, reply_markup=kb_back("mybots"))
        return
    
    if data.startswith("bot:plugins:"):
        bot_id = data.split(":")[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        if cloud.check_subscription_expired(bot_id):
            await q.answer("⚠️ اشتراک منقضی شده!", show_alert=True); return
        await q.answer()
        await q.edit_message_text(
            "🧩 <b>مدیریت پلاگین‌ها</b>\n\n"
            "💡 دکمه ⚙️ برای <b>شخصی‌سازی</b> هر پلاگین است.",
            parse_mode=ParseMode.HTML, reply_markup=kb_plugins(bot_id))
        return
    
    if data.startswith("plugin:toggle:"):
        _, _, bot_id, plugin_id = data.split(":")
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        if cloud.check_subscription_expired(bot_id):
            await q.answer("⚠️ اشتراک منقضی!", show_alert=True); return
        plugin_info = AVAILABLE_PLUGINS.get(plugin_id)
        if not plugin_info:
            await q.answer("پلاگین یافت نشد", show_alert=True); return
        if plugin_info.get("is_premium") and not cloud.is_plugin_purchased(bot_id, plugin_id):
            await q.answer("⚠️ باید خریداری شود", show_alert=True); return
        await q.answer("در حال تغییر...")
        if cloud.is_plugin_enabled(bot_id, plugin_id):
            cloud.disable_plugin(bot_id, plugin_id)
            msg = f"⏸️ {plugin_info['name']} غیرفعال شد"
        else:
            cloud.enable_plugin(bot_id, plugin_id)
            msg = f"✅ {plugin_info['name']} فعال شد"
        cloud.save(force=True)
        await bot_factory.restart_bot(bot_id)
        try: await q.edit_message_reply_markup(reply_markup=kb_plugins(bot_id))
        except: pass
        await q.answer(msg, show_alert=True)
        return
    
    if data.startswith("plugin:buy:"):
        _, _, bot_id, plugin_id = data.split(":")
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        plugin_info = AVAILABLE_PLUGINS.get(plugin_id)
        if not plugin_info:
            await q.answer("پلاگین یافت نشد", show_alert=True); return
        cost = plugin_info.get("cost_gc", 0)
        u = cloud.get_user(uid)
        if u.get("gramcoin_balance", 0) < cost:
            await q.answer(f"❌ نیاز به {cost} GC", show_alert=True); return
        await q.answer("در حال خرید...")
        if cloud.deduct_gramcoin(uid, cost, f"plugin_{plugin_id}_{bot_id}"):
            cloud.purchase_plugin(bot_id, plugin_id)
            cloud.save(force=True)
            await bot_factory.restart_bot(bot_id)
            await q.edit_message_text(
                f"✅ <b>پلاگین {plugin_info['name']} خریداری شد!</b>\n\n💰 {cost} GC کسر شد",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:plugins:{bot_id}")]]))
        return
    
    # ═══════ PLUGIN SETTINGS (NEW) ═══════
    if data.startswith("plugin:settings:"):
        _, _, bot_id, plugin_id = data.split(":")
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        
        await q.answer()
        
        if plugin_id == "support":
            settings = cloud.get_plugin_settings(bot_id, "support")
            await q.edit_message_text(
                build_support_settings_text(bot_id, settings),
                parse_mode=ParseMode.HTML,
                reply_markup=kb_support_settings(bot_id, settings))
        else:
            await q.edit_message_text(
                f"⚙️ <b>تنظیمات {AVAILABLE_PLUGINS[plugin_id]['name']}</b>\n\n"
                f"در نسخه‌های آینده...",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:plugins:{bot_id}")]]))
        return
    
    # ═══════ SUPPORT PLUGIN SETTINGS HANDLERS ═══════
    if data.startswith("sup_set:"):
        parts = data.split(":")
        action = parts[1]
        
        # بررسی دسترسی - همه sup_set ها نیاز به مالکیت ربات دارند
        if action in ("main", "stats", "open_list", "closed_list", "categories", "edit", "toggle_anon", "reset"):
            bot_id = parts[-1]
            bot = cloud.get_bot(bot_id)
            if not bot or bot.get("owner_uid") != uid:
                await q.answer("❌ دسترسی ندارید", show_alert=True); return
        
        await q.answer()
        
        if action == "main":
            settings = cloud.get_plugin_settings(bot_id, "support")
            await q.edit_message_text(
                build_support_settings_text(bot_id, settings),
                parse_mode=ParseMode.HTML,
                reply_markup=kb_support_settings(bot_id, settings))
            return
        
        if action == "stats":
            stats = cloud.get_bot_ticket_stats(bot_id)
            await q.edit_message_text(
                f"📊 <b>آمار تیکت‌های ربات</b>\n\n"
                f"• کل تیکت‌ها: <b>{stats['total']}</b>\n"
                f"• باز: <b>{stats['open']}</b>\n"
                f"• بسته: <b>{stats['closed']}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"sup_set:main:{bot_id}")]]))
            return
        
        if action == "open_list":
            text, kb = build_ticket_list_text(bot_id, "open")
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        
        if action == "closed_list":
            text, kb = build_ticket_list_text(bot_id, "closed")
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        
        if action == "view":
            ticket_id = parts[2]
            ticket = cloud.get_ticket(ticket_id)
            if not ticket:
                await q.answer("تیکت یافت نشد", show_alert=True); return
            bot_id = ticket["bot_id"]
            bot = cloud.get_bot(bot_id)
            if not bot or bot.get("owner_uid") != uid:
                await q.answer("❌ دسترسی ندارید", show_alert=True); return
            
            history = _build_ticket_history(ticket)
            rows = []
            if ticket["status"] == "open":
                rows.append([
                    InlineKeyboardButton("💬 پاسخ سریع", callback_data=f"sup_set:quick_reply:{ticket_id}"),
                    InlineKeyboardButton("✅ بستن", callback_data=f"sup_set:quick_close:{ticket_id}"),
                ])
            rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"sup_set:main:{bot_id}")])
            
            try:
                await q.edit_message_text(history, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
            except BadRequest:
                await q.message.reply_text(history, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
            return
        
        if action == "quick_reply":
            ticket_id = parts[2]
            ticket = cloud.get_ticket(ticket_id)
            if not ticket: return
            bot_id = ticket["bot_id"]
            ctx.user_data[f"sup_quick_reply_{bot_id}"] = {"ticket_id": ticket_id}
            ctx.user_data["awaiting"] = f"sup_quick_reply_{bot_id}"
            await q.edit_message_text(
                f"💬 <b>پاسخ سریع به تیکت {ticket_id}</b>\n\n"
                f"👤 کاربر: {ticket['full_name']}\n\n"
                f"✍️ پاسخ را بنویسید:\n(برای لغو /cancel)",
                parse_mode=ParseMode.HTML)
            return
        
        if action == "quick_close":
            ticket_id = parts[2]
            ticket = cloud.get_ticket(ticket_id)
            if not ticket: return
            bot_id = ticket["bot_id"]
            cloud.close_ticket(ticket_id)
            settings = cloud.get_plugin_settings(bot_id, "support")
            closed_msg = settings.get("ticket_closed_message", DEFAULT_SUPPORT_SETTINGS["ticket_closed_message"])
            closed_msg = closed_msg.replace("{ticket_id}", ticket_id)
            try:
                bot_data = cloud.get_bot(bot_id)
                temp_bot = Bot(token=bot_data["bot_token"])
                await temp_bot.send_message(chat_id=ticket["user_id"], text=closed_msg, parse_mode=ParseMode.HTML)
            except Exception as e:
                log.warning(f"Close notify failed: {e}")
            cloud.save(force=True)
            await q.edit_message_text(
                f"✅ <b>تیکت {ticket_id} بسته شد</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"sup_set:main:{bot_id}")]]))
            return
        
        if action == "categories":
            settings = cloud.get_plugin_settings(bot_id, "support")
            categories = settings.get("categories", [])
            cat_list = "\n".join([f"• {c}" for c in categories]) if categories else "—"
            ctx.user_data[f"sup_categories_{bot_id}"] = True
            ctx.user_data["awaiting"] = f"sup_categories_{bot_id}"
            await q.edit_message_text(
                f"📁 <b>مدیریت دسته‌بندی‌ها</b>\n\n"
                f"📋 <b>دسته‌بندی‌های فعلی:</b>\n{cat_list}\n\n"
                f"✍️ <b>دسته‌بندی‌های جدید را بنویسید</b> (هر کدام در یک خط):\n\n"
                f"<b>مثال:</b>\n<code>عمومی\nفنی\nمالی\nپیشنهاد\nفروش</code>\n\n"
                f"(برای لغو /cancel)",
                parse_mode=ParseMode.HTML)
            return
        
        if action == "edit":
            setting_type = parts[2]
            ctx.user_data[f"sup_edit_{bot_id}"] = {"type": setting_type}
            ctx.user_data["awaiting"] = f"sup_edit_{bot_id}"
            
            settings = cloud.get_plugin_settings(bot_id, "support")
            titles = {
                "welcome": ("پیام خوش‌آمد", settings.get("welcome_message")),
                "received": ("پیام دریافت تیکت", settings.get("ticket_received_message")),
                "closed": ("پیام بسته شدن", settings.get("ticket_closed_message")),
            }
            title, current = titles.get(setting_type, ("نامشخص", ""))
            
            await q.edit_message_text(
                f"✏️ <b>ویرایش {title}</b>\n\n"
                f"📝 <b>مقدار فعلی:</b>\n<blockquote>{current}</blockquote>\n\n"
                f"✍️ <b>متن جدید را بنویسید:</b>\n"
                f"💡 می‌توانید از <code>{{ticket_id}}</code> برای شماره تیکت استفاده کنید\n"
                f"💡 HTML پشتیبانی می‌شود\n\n"
                f"(برای لغو /cancel)",
                parse_mode=ParseMode.HTML)
            return
        
        if action == "toggle_anon":
            settings = cloud.get_plugin_settings(bot_id, "support")
            settings["anonymous_mode"] = not settings.get("anonymous_mode", False)
            cloud.update_plugin_settings(bot_id, "support", settings)
            cloud.save(force=True)
            await q.edit_message_text(
                build_support_settings_text(bot_id, settings),
                parse_mode=ParseMode.HTML,
                reply_markup=kb_support_settings(bot_id, settings))
            return
        
        if action == "reset":
            cloud.update_plugin_settings(bot_id, "support", dict(DEFAULT_SUPPORT_SETTINGS))
            cloud.save(force=True)
            settings = cloud.get_plugin_settings(bot_id, "support")
            await q.edit_message_text(
                f"♻️ <b>تنظیمات به پیش‌فرض بازنشانی شد!</b>\n\n" + build_support_settings_text(bot_id, settings),
                parse_mode=ParseMode.HTML,
                reply_markup=kb_support_settings(bot_id, settings))
            return
    
    if data.startswith("bot:stats:"):
        bot_id = data.split(":")[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        await q.answer()
        stats = bot.get("stats", {})
        users_count = len(bot.get("bot_users", []))
        await q.edit_message_text(
            f"📊 <b>آمار ربات {bot_id[:8]}</b>\n\n"
            f"👥 کاربران: {users_count}\n"
            f"📨 پیام دریافتی: {stats.get('messages_received', 0)}\n"
            f"📤 پیام ارسالی: {stats.get('messages_sent', 0)}",
            parse_mode=ParseMode.HTML, reply_markup=kb_back(f"bot:manage:{bot_id}"))
        return
    
    if data.startswith("bot:delete:"):
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
    
    # ═══════ ADMIN ═══════
    if not is_admin(uid):
        await q.answer("⛔️ دسترسی ندارید.", show_alert=True)
        return
    
    if data.startswith("admin:panel:"):
        await q.answer()
        stats = cloud.get_stats()
        await q.edit_message_text(
            f"👑 <b>پنل ادمین</b>\n\n"
            f"👥 کاربران: {stats['total_users']}\n"
            f"🤖 ربات‌ها: {stats['total_bots']}\n"
            f"🟢 فعال: {stats['active_bots']}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 لیست کاربران", callback_data="admin:users:0")],
                [InlineKeyboardButton("🤖 لیست ربات‌ها", callback_data="admin:bots:0")],
                [InlineKeyboardButton("📢 ارسال همگانی", callback_data="admin:broadcast:prompt")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="menu:home")],
            ]))
        return
    
    if data.startswith("admin:users:"):
        page = int(data.split(":")[-1])
        await q.answer()
        users = sorted(cloud.get_all_users(), key=lambda u: u.get("gramcoin_balance", 0), reverse=True)
        total = len(users)
        start = page * ADMIN_PAGE_SIZE
        end = min(start + ADMIN_PAGE_SIZE, total)
        text = f"👥 <b>کاربران</b> ({page + 1})\n\n"
        rows = []
        for u in users[start:end]:
            text += f"• <b>{u.get('full_name', '—')}</b> | <code>{u.get('uid')}</code>\n  💰 {u.get('gramcoin_balance', 0)} GC\n"
            rows.append([InlineKeyboardButton(f"🎁 +50 GC", callback_data=f"admin:givegc:{u.get('uid')}:50")])
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"admin:users:{page - 1}"))
        if end < total: nav.append(InlineKeyboardButton("▶️", callback_data=f"admin:users:{page + 1}"))
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
        bots = cloud.get_all_bots()
        total = len(bots)
        start = page * ADMIN_PAGE_SIZE
        end = min(start + ADMIN_PAGE_SIZE, total)
        text = f"🤖 <b>ربات‌ها</b> ({page + 1})\n\n"
        for b in bots[start:end]:
            status = "🔴" if cloud.check_subscription_expired(b["bot_id"]) else ("🟢" if b.get("is_active") else "⏸️")
            text += f"{status} <code>{b['bot_id'][:8]}</code> | Owner: <code>{b.get('owner_uid')}</code>\n\n"
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"admin:bots:{page - 1}"))
        if end < total: nav.append(InlineKeyboardButton("▶️", callback_data=f"admin:bots:{page + 1}"))
        rows = [nav] if nav else []
        rows.append([InlineKeyboardButton("🔙", callback_data="admin:panel:0")])
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return
    
    if data == "admin:broadcast:prompt":
        ctx.user_data["awaiting"] = "broadcast_text"
        await q.answer()
        await q.edit_message_text("📢 پیام را ارسال کنید:\n(/cancel)", parse_mode=ParseMode.HTML)
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
    
    # ساخت ربات
    if awaiting == "bot_token":
        ctx.user_data.pop("awaiting", None)
        bot_token = text_in.strip()
        if not bot_token or ":" not in bot_token or len(bot_token) < 40:
            await update.message.reply_text("❌ توکن نامعتبر!", parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
            return
        try:
            temp_bot = Bot(token=bot_token)
            me = await temp_bot.get_me()
        except InvalidToken:
            await update.message.reply_text("❌ توکن نامعتبر!", parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
            return
        
        user_bots = cloud.get_user_bots(uid)
        if len(user_bots) >= 3:
            await update.message.reply_text("⚠️ حداکثر ۳ ربات!", parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
            return
        
        bot_data = cloud.create_bot(uid, bot_token)
        cloud.save(force=True)
        await update.message.reply_text(
            f"✅ <b>ربات ساخته شد!</b>\n\n"
            f"🤖 @{me.username}\n"
            f"🆔 <code>{bot_data['bot_id']}</code>\n\n"
            f"⚠️ برای فعال‌سازی، اشتراک بخرید.",
            parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        return
    
    # ═══════ SUPPORT SETTINGS MESSAGE HANDLERS ═══════
    if awaiting and awaiting.startswith("sup_edit_"):
        bot_id = awaiting.replace("sup_edit_", "")
        edit_info = ctx.user_data.get(f"sup_edit_{bot_id}", {})
        setting_type = edit_info.get("type")
        ctx.user_data.pop("awaiting", None)
        ctx.user_data.pop(f"sup_edit_{bot_id}", None)
        
        settings = cloud.get_plugin_settings(bot_id, "support")
        field_map = {
            "welcome": "welcome_message",
            "received": "ticket_received_message",
            "closed": "ticket_closed_message",
        }
        field = field_map.get(setting_type)
        if field:
            settings[field] = text_in
            cloud.update_plugin_settings(bot_id, "support", settings)
            cloud.save(force=True)
            await bot_factory.restart_bot(bot_id)
            await update.message.reply_text(
                f"✅ <b>تنظیمات ذخیره شد!</b>\n\n🔄 ربات ری‌استارت شد.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تنظیمات بیشتر", callback_data=f"plugin:settings:{bot_id}:support")]]))
        return
    
    if awaiting and awaiting.startswith("sup_categories_"):
        bot_id = awaiting.replace("sup_categories_", "")
        ctx.user_data.pop("awaiting", None)
        ctx.user_data.pop(f"sup_categories_{bot_id}", None)
        
        categories = [c.strip() for c in text_in.split("\n") if c.strip()]
        if not categories:
            await update.message.reply_text("❌ حداقل یک دسته‌بندی وارد کنید.", reply_markup=kb_main(uid))
            return
        
        settings = cloud.get_plugin_settings(bot_id, "support")
        settings["categories"] = categories
        cloud.update_plugin_settings(bot_id, "support", settings)
        cloud.save(force=True)
        await bot_factory.restart_bot(bot_id)
        
        cat_list = "\n".join([f"• {c}" for c in categories])
        await update.message.reply_text(
            f"✅ <b>دسته‌بندی‌ها ذخیره شد!</b>\n\n{cat_list}\n\n🔄 ربات ری‌استارت شد.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تنظیمات بیشتر", callback_data=f"plugin:settings:{bot_id}:support")]]))
        return
    
    if awaiting and awaiting.startswith("sup_quick_reply_"):
        bot_id = awaiting.replace("sup_quick_reply_", "")
        reply_info = ctx.user_data.get(f"sup_quick_reply_{bot_id}", {})
        ticket_id = reply_info.get("ticket_id")
        ctx.user_data.pop("awaiting", None)
        ctx.user_data.pop(f"sup_quick_reply_{bot_id}", None)
        
        ticket = cloud.get_ticket(ticket_id)
        if not ticket:
            await update.message.reply_text("❌ تیکت یافت نشد.")
            return
        
        cloud.add_ticket_message(ticket_id, "admin", uid, text_in)
        cloud.save(force=True)
        
        try:
            bot_data = cloud.get_bot(bot_id)
            temp_bot = Bot(token=bot_data["bot_token"])
            await temp_bot.send_message(
                chat_id=ticket["user_id"],
                text=f"💬 <b>پاسخ پشتیبانی</b>\n\n🎫 <code>{ticket_id}</code>\n\n{text_in}",
                parse_mode=ParseMode.HTML)
            await update.message.reply_text(
                f"✅ <b>پاسخ ارسال شد!</b>\n\n🎫 تیکت: <code>{ticket_id}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 به تنظیمات", callback_data=f"plugin:settings:{bot_id}:support")]]))
        except Exception as e:
            await update.message.reply_text(f"❌ خطا: {str(e)[:100]}")
        return
    
    if not is_admin(uid): return
    
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
        await update.message.reply_text(f"✅ ارسال شد: {sent}/{len(users)}", parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
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
                            text=f"🔴 <b>اشتراک ربات {bot_id[:8]} منقضی شد!</b>\n\nبرای فعال‌سازی مجدد، اشتراک بخرید.",
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
    log.info("GramBotSaz v2.1 (Advanced Support) ready ✅")

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
        def index(): return "🤖 GramBotSaz v2.1 Alive!", 200
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
