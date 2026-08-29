#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GramBotSaz v5.0 — نسخه نهایی، تمیز و بدون باگ
════════════════════════════════════════════════════════════════════
✅ منوی ۴ دکمه‌ای حرفه‌ای
✅ کیبورد ReplyKeyboard یکبار هنگام /start
✅ عکس welcome با file_id معتبر
✅ بدون دکمه بازگشت الکی
"""
import os
import sys
import time
import logging
import hashlib
import asyncio
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Callable
from flask import Flask, request

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot,
    ReplyKeyboardMarkup, KeyboardButton,
)
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
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "605611554").replace(" ", "").split(",") if x.strip()}
FORCE_JOIN_CHANNEL = os.environ.get("FORCE_JOIN_CHANNEL", "@testchanellaz")

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

# ══════════════════════════════════════════════════════════════════════
# PLUGINS
# ══════════════════════════════════════════════════════════════════════
AVAILABLE_PLUGINS = {
    "welcome": {"name": "👋 خوش‌آمد پیشرفته", "cost_gc": 0, "is_premium": False, "has_settings": True},
    "support": {"name": "🎫 پشتیبانی چت زنده", "cost_gc": 0, "is_premium": False, "has_settings": True},
    "keyboard": {"name": "⌨️ کیبورد اختصاصی", "cost_gc": 0, "is_premium": False, "has_settings": True},
    "echo": {"name": "🤖 Echo", "cost_gc": 0, "is_premium": False, "has_settings": False},
    "auto_reply": {"name": "💬 پاسخ خودکار", "cost_gc": 80, "is_premium": True, "has_settings": True},
    "broadcast": {"name": "📢 ارسال همگانی", "cost_gc": 100, "is_premium": True, "has_settings": False},
    "shop": {"name": "🛒 فروشگاه", "cost_gc": 150, "is_premium": True, "has_settings": True},
}

TRIAL_DEFAULT_PLUGINS = ["welcome", "support", "keyboard"]

def now_ir(): return datetime.now(IRAN_TZ)
def is_admin(uid: int): return uid in ADMIN_IDS
def _fmt_timedelta(s):
    s = max(0, int(s)); d, r = divmod(s, 86400); h, r = divmod(r, 3600); m, _ = divmod(r, 60)
    p = []
    if d: p.append(f"{d} روز")
    if h or d: p.append(f"{h} ساعت")
    p.append(f"{m} دقیقه")
    return " و ".join(p) if p else "کمتر از یک دقیقه"
def mention_html(uid, name, username=""):
    n = name.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;") or f"کاربر {uid}"
    return f'<a href="https://t.me/{username}">{n}</a>' if username else f'<a href="tg://user?id={uid}">{n}</a>'

# ══════════════════════════════════════════════════════════════════════
# STORAGE
# ══════════════════════════════════════════════════════════════════════
try:
    from pymongo import MongoClient
    MONGODB_AVAILABLE = True
except ImportError:
    MONGODB_AVAILABLE = False

class CloudStorage:
    def __init__(self):
        self._uri = os.environ.get("MONGODB_URI", "")
        self._client = self._collection = None
        self._lock = threading.Lock()
        self._dirty = False
        self._data = {"users": {}, "bots": {}, "tickets": {}, "transactions": []}
        if self._uri and MONGODB_AVAILABLE:
            try:
                self._client = MongoClient(self._uri, serverSelectionTimeoutMS=10000)
                self._client.admin.command('ping')
                self._collection = self._client.get_database()["grambotsaz_data"]
            except Exception as e:
                log.error(f"MongoDB error: {e}")
    
    def load(self):
        if not self._uri or not self._collection: return
        try:
            doc = self._collection.find_one({"_id": "main"})
            if doc:
                with self._lock:
                    doc.pop("_id", None)
                    for k, v in doc.items(): self._data[k] = v
                    for k in ("users", "bots", "tickets", "transactions"):
                        self._data.setdefault(k, {} if k != "transactions" else [])
                    for b in self._data["bots"].values():
                        for f in ("enabled_plugins","purchased_plugins","plugin_settings","bot_users"):
                            b.setdefault(f, [] if f != "plugin_settings" else {})
                        b.setdefault("subscription_expiry", None)
                        b.setdefault("is_trial", False)
                        b.setdefault("next_ticket_id", 1001)
        except Exception as e: log.error(f"Load error: {e}")
    
    def save(self, force=False):
        if not self._uri or not self._collection or (not force and not self._dirty): return
        with self._lock:
            payload = {"_id": "main", "last_updated": now_ir().isoformat()}
            payload.update(self._data)
            try: self._collection.replace_one({"_id": "main"}, payload, upsert=True); self._dirty = False
            except Exception as e: log.error(f"Save error: {e}")
    
    def ensure_user(self, uid, username="", full_name=""):
        key = str(uid); is_new = False
        with self._lock:
            if key not in self._data["users"]:
                is_new = True
                self._data["users"][key] = {
                    "uid": uid, "username": username, "full_name": full_name,
                    "gramcoin_balance": 0, "total_earned_gc": 0,
                    "referral_code": hashlib.md5(f"{uid}{time.time()}".encode()).hexdigest()[:8],
                    "referred_by": None, "created_at": now_ir().isoformat(),
                    "last_daily_claim": None, "bots_created": [], "signup_bonus_claimed": False,
                }; self._dirty = True
            else:
                u = self._data["users"][key]
                if username and u.get("username") != username: u["username"] = username; self._dirty = True
                if full_name and u.get("full_name") != full_name: u["full_name"] = full_name; self._dirty = True
            return dict(self._data["users"][key]), is_new
    
    def claim_signup_bonus(self, uid):
        k = str(uid)
        with self._lock:
            u = self._data["users"].get(k)
            if not u or u.get("signup_bonus_claimed"): return False
            u["signup_bonus_claimed"] = True; self._dirty = True
        return self.add_gramcoin(uid, SIGNUP_BONUS_GC, "signup_bonus")
    
    def get_user(self, uid):
        with self._lock:
            u = self._data["users"].get(str(uid))
            return dict(u) if u else None
    
    def get_all_users(self):
        with self._lock: return [{"uid_str": k, **v} for k, v in self._data["users"].items()]
    
    def add_gramcoin(self, uid, amount, reason):
        k = str(uid)
        with self._lock:
            if k not in self._data["users"]: return False
            self._data["users"][k]["gramcoin_balance"] += amount
            self._data["users"][k]["total_earned_gc"] += amount
            self._data["transactions"].append({
                "uid": uid, "amount_gc": amount, "tx_type": reason,
                "description": f"+{amount} GC ({reason})", "timestamp": now_ir().isoformat(),
            })
            self._data["transactions"] = self._data["transactions"][-1000:]
            self._dirty = True; return True
    
    def deduct_gramcoin(self, uid, amount, reason):
        k = str(uid)
        with self._lock:
            u = self._data["users"].get(k)
            if not u or u.get("gramcoin_balance", 0) < amount: return False
            u["gramcoin_balance"] -= amount
            self._data["transactions"].append({
                "uid": uid, "amount_gc": -amount, "tx_type": reason,
                "description": f"-{amount} GC ({reason})", "timestamp": now_ir().isoformat(),
            })
            self._dirty = True; return True
    
    def can_claim_daily(self, uid):
        u = self.get_user(uid)
        if not u or not u.get("last_daily_claim"): return True
        try: return (now_ir() - datetime.fromisoformat(u["last_daily_claim"])).days >= 1
        except: return True
    
    def claim_daily_reward(self, uid, amount):
        if not self.can_claim_daily(uid): return False
        k = str(uid)
        with self._lock:
            if k in self._data["users"]:
                self._data["users"][k]["last_daily_claim"] = now_ir().isoformat(); self._dirty = True
        return self.add_gramcoin(uid, amount, "daily_reward")
    
    def create_bot(self, owner_uid, bot_token):
        bot_id = hashlib.md5(f"{owner_uid}{bot_token}{time.time()}".encode()).hexdigest()[:12]
        with self._lock:
            trial_expiry = (now_ir() + timedelta(days=TRIAL_DAYS)).isoformat()
            self._data["bots"][bot_id] = {
                "bot_id": bot_id, "owner_uid": owner_uid, "bot_token": bot_token,
                "bot_username": "", "is_active": True, "is_trial": True,
                "created_at": now_ir().isoformat(), "last_activity": None,
                "enabled_plugins": list(TRIAL_DEFAULT_PLUGINS),
                "purchased_plugins": [],
                "plugin_settings": {
                    "welcome": {
                        "message": "👋 <b>سلام! به ربات ما خوش آمدید!</b>\n\n🤖 از اینکه ما را انتخاب کردید سپاسگزاریم.\n\n💡 از دکمه‌های زیر صفحه برای شروع استفاده کنید.",
                        "photo_file_id": None,
                        "buttons": [[{"text": "🎫 پشتیبانی", "type": "support"}, {"text": "ℹ️ درباره ما", "type": "text", "value": "ما یک تیم حرفه‌ای هستیم!"}]],
                    },
                    "support": {
                        "admin_message": "📩 پیام خود را ارسال کنید. در سریع‌ترین زمان پاسخ می‌دهیم.",
                        "confirm_message": "✅ پیام شما ارسال شد.",
                        "end_chat_message": "🔚 چت پایان یافت.",
                    },
                    "keyboard": {
                        "buttons": [
                            [{"text": "🎫 پشتیبانی", "action": "command", "value": "/support"}],
                            [{"text": "ℹ️ درباره ما", "action": "text", "value": "ما یک تیم حرفه‌ای هستیم!"}],
                        ],
                        "resize_keyboard": True, "persistent": True,
                    },
                },
                "subscription_expiry": trial_expiry, "subscription_plan": "trial",
                "bot_users": [], "next_ticket_id": 1001,
                "stats": {"messages_received": 0, "messages_sent": 0, "unique_users": 0},
            }
            uk = str(owner_uid)
            if uk in self._data["users"]:
                self._data["users"][uk]["bots_created"].append(bot_id)
            self._dirty = True
            return dict(self._data["bots"][bot_id])
    
    def get_bot(self, bot_id):
        with self._lock:
            b = self._data["bots"].get(bot_id)
            return dict(b) if b else None
    
    def get_all_bots(self):
        with self._lock: return [{"bot_id": k, **v} for k, v in self._data["bots"].items()]
    
    def get_user_bots(self, uid):
        with self._lock:
            u = self._data["users"].get(str(uid))
            if not u: return []
            return [dict(self._data["bots"][bid]) for bid in u.get("bots_created", []) if bid in self._data["bots"]]
    
    def update_bot_username(self, bot_id, username):
        with self._lock:
            if bot_id in self._data["bots"]:
                self._data["bots"][bot_id]["bot_username"] = username; self._dirty = True
    
    def delete_bot(self, bot_id, owner_uid):
        with self._lock:
            if bot_id in self._data["bots"]:
                del self._data["bots"][bot_id]
                uk = str(owner_uid)
                if uk in self._data["users"] and bot_id in self._data["users"][uk]["bots_created"]:
                    self._data["users"][uk]["bots_created"].remove(bot_id)
                self._dirty = True; return True
            return False
    
    def update_bot_stats(self, bot_id, mr=0, ms=0):
        with self._lock:
            if bot_id in self._data["bots"]:
                self._data["bots"][bot_id]["stats"]["messages_received"] += mr
                self._data["bots"][bot_id]["stats"]["messages_sent"] += ms
                self._data["bots"][bot_id]["last_activity"] = now_ir().isoformat()
                self._dirty = True
    
    def add_bot_user(self, bot_id, user_id, username="", full_name=""):
        with self._lock:
            if bot_id not in self._data["bots"]: return
            bu = self._data["bots"][bot_id].setdefault("bot_users", [])
            if any(x["user_id"] == user_id for x in bu): return
            bu.append({"user_id": user_id, "username": username, "full_name": full_name, "joined_at": now_ir().isoformat()})
            self._data["bots"][bot_id]["stats"]["unique_users"] = len(bu); self._dirty = True
    
    def activate_subscription(self, bot_id, plan, days):
        with self._lock:
            if bot_id not in self._data["bots"]: return None
            bot = self._data["bots"][bot_id]
            try:
                cur = datetime.fromisoformat(bot["subscription_expiry"]) if bot.get("subscription_expiry") else now_ir()
                base = cur if cur > now_ir() else now_ir()
            except: base = now_ir()
            new_expiry = base + timedelta(days=days)
            bot["subscription_expiry"] = new_expiry.isoformat()
            bot["subscription_plan"] = plan; bot["is_active"] = True; bot["is_trial"] = False
            for pid, p in AVAILABLE_PLUGINS.items():
                if not p["is_premium"] and pid not in bot["enabled_plugins"]:
                    bot["enabled_plugins"].append(pid)
            self._dirty = True; return new_expiry
    
    def check_subscription_expired(self, bot_id):
        with self._lock:
            b = self._data["bots"].get(bot_id)
            if not b or not b.get("subscription_expiry"): return True
            try: return now_ir() >= datetime.fromisoformat(b["subscription_expiry"])
            except: return True
    
    def deactivate_bot(self, bot_id):
        with self._lock:
            if bot_id in self._data["bots"]:
                self._data["bots"][bot_id]["is_active"] = False; self._dirty = True
    
    def enable_plugin(self, bot_id, pid):
        with self._lock:
            b = self._data["bots"].get(bot_id)
            if not b or pid in b["enabled_plugins"]: return False
            b["enabled_plugins"].append(pid); self._dirty = True; return True
    
    def disable_plugin(self, bot_id, pid):
        with self._lock:
            b = self._data["bots"].get(bot_id)
            if not b or pid not in b["enabled_plugins"]: return False
            b["enabled_plugins"].remove(pid); self._dirty = True; return True
    
    def purchase_plugin(self, bot_id, pid):
        with self._lock:
            b = self._data["bots"].get(bot_id)
            if not b or pid in b["purchased_plugins"]: return False
            b["purchased_plugins"].append(pid)
            if pid not in b["enabled_plugins"]: b["enabled_plugins"].append(pid)
            self._dirty = True; return True
    
    def is_plugin_purchased(self, bot_id, pid):
        with self._lock:
            b = self._data["bots"].get(bot_id)
            return b and pid in b.get("purchased_plugins", []) if b else False
    
    def is_plugin_enabled(self, bot_id, pid):
        with self._lock:
            b = self._data["bots"].get(bot_id)
            return b and pid in b.get("enabled_plugins", []) if b else False
    
    def update_plugin_settings(self, bot_id, pid, settings):
        with self._lock:
            if bot_id in self._data["bots"]:
                self._data["bots"][bot_id].setdefault("plugin_settings", {})[pid] = settings
                self._dirty = True
    
    def get_plugin_settings(self, bot_id, pid):
        with self._lock:
            b = self._data["bots"].get(bot_id)
            return b.get("plugin_settings", {}).get(pid, {}) if b else {}
    
    def create_ticket(self, bot_id, user_id, username, full_name):
        with self._lock:
            b = self._data["bots"].get(bot_id)
            if not b: return None
            num = b.get("next_ticket_id", 1001)
            b["next_ticket_id"] = num + 1
            tid = f"T{num}"
            self._data["tickets"][tid] = {
                "ticket_id": tid, "bot_id": bot_id, "user_id": user_id,
                "username": username, "full_name": full_name, "status": "open",
                "created_at": now_ir().isoformat(), "closed_at": None,
            }
            self._dirty = True
            return dict(self._data["tickets"][tid])
    
    def close_ticket(self, tid):
        with self._lock:
            t = self._data["tickets"].get(tid)
            if t and t["status"] == "open":
                t["status"] = "closed"; t["closed_at"] = now_ir().isoformat(); self._dirty = True
    
    def get_ticket(self, tid):
        with self._lock:
            t = self._data["tickets"].get(tid)
            return dict(t) if t else None
    
    def get_bot_tickets(self, bot_id):
        with self._lock:
            return [dict(t) for t in self._data["tickets"].values() if t["bot_id"] == bot_id]
    
    def find_uid_by_referral(self, ref_code):
        with self._lock:
            for uid_str, u in self._data["users"].items():
                if u.get("referral_code") == ref_code: return int(uid_str)
            return None
    
    def apply_referral(self, new_uid, ref_uid):
        if new_uid == ref_uid: return False
        nk, rk = str(new_uid), str(ref_uid)
        with self._lock:
            users = self._data["users"]
            if nk not in users or rk not in users or users[nk].get("referred_by"): return False
            users[nk]["referred_by"] = rk; self._dirty = True; return True
    
    def get_stats(self):
        with self._lock:
            return {
                "total_users": len(self._data["users"]),
                "total_bots": len(self._data["bots"]),
                "active_bots": sum(1 for b in self._data["bots"].values() if b.get("is_active")),
            }

cloud = CloudStorage()

# ══════════════════════════════════════════════════════════════════════
# PLUGIN MANAGER (رفع باگ‌های قبلی)
# ══════════════════════════════════════════════════════════════════════
class PluginManager:
    def __init__(self):
        self._handlers: Dict[str, Callable] = {}
        self._register_all()
    
    def _register_all(self):
        self._handlers["echo"] = self._setup_echo
        self._handlers["welcome"] = self._setup_welcome  # ✅ شامل کیبورد هم می‌شود
        self._handlers["support"] = self._setup_support
        # ✅ keyboard plugin دیگر جداگانه ثبت نمی‌شود - در welcome ادغام شده
    
    def setup_plugins_for_bot(self, app, bot_id):
        bot_data = cloud.get_bot(bot_id)
        if not bot_data: return
        for pid in bot_data.get("enabled_plugins", []):
            h = self._handlers.get(pid)
            if h:
                try: h(app, bot_id); log.info(f"✅ {pid} enabled for {bot_id}")
                except Exception as e: log.error(f"❌ {pid} failed: {e}")
    
    def _setup_echo(self, app, bot_id):
        async def handler(update, ctx):
            if not update.message or not update.message.text or update.message.text.startswith("/"): return
            cloud.add_bot_user(bot_id, update.effective_user.id, update.effective_user.username or "",
                f"{update.effective_user.first_name or ''} {update.effective_user.last_name or ''}".strip())
            try: await update.message.reply_text(update.message.text)
            except: pass
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handler), group=100)
    
    # ═══════ WELCOME PLUGIN (شامل کیبورد - رفع باگ تکرار) ═══════
    def _setup_welcome(self, app, bot_id):
        """
        ✅ رفع باگ: کیبورد ReplyKeyboard فقط همراه welcome ارسال می‌شود
        ✅ رفع باگ: عکس با file_id معتبر
        ✅ رفع باگ: دکمه‌های شیشه‌ای چندتایی در هر ردیف
        """
        def build_keyboard_markup(bot_id):
            """ساخت ReplyKeyboard از تنظیمات پلاگین keyboard"""
            settings = cloud.get_plugin_settings(bot_id, "keyboard")
            buttons_data = settings.get("buttons", [])
            bot_data = cloud.get_bot(bot_id)
            # اگر پلاگین keyboard فعال نباشد، کیبوردی نساز
            if not bot_data or "keyboard" not in bot_data.get("enabled_plugins", []):
                return None
            if not buttons_data: return None
            rows = []
            for row in buttons_data:
                current_row = []
                for btn in row:
                    text = btn.get("text", "")
                    if text: current_row.append(KeyboardButton(text))
                if current_row: rows.append(current_row)
            if not rows: return None
            return ReplyKeyboardMarkup(
                rows,
                resize_keyboard=settings.get("resize_keyboard", True),
                one_time_keyboard=settings.get("one_time_keyboard", False),
                is_persistent=settings.get("persistent", True),
            )
        
        def build_inline_buttons(bot_id, ctx=None):
            """ساخت InlineKeyboard (دکمه‌های شیشه‌ای)"""
            settings = cloud.get_plugin_settings(bot_id, "welcome")
            buttons_data = settings.get("buttons", [])
            if not buttons_data: return None
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
                        current_row.append(InlineKeyboardButton(text, callback_data=f"sup_btn:{bot_id}"))
                    elif btn_type == "text":
                        enc = hashlib.md5(value.encode()).hexdigest()[:8]
                        if ctx: ctx.bot_data.setdefault("btn_texts", {})[enc] = value
                        current_row.append(InlineKeyboardButton(text, callback_data=f"wbtn:{enc}"))
                if current_row: btn_rows.append(current_row)
            return InlineKeyboardMarkup(btn_rows) if btn_rows else None
        
        async def welcome_handler(update, ctx):
            settings = cloud.get_plugin_settings(bot_id, "welcome")
            message = settings.get("message", "👋 <b>سلام! خوش آمدید!</b>")
            photo_file_id = settings.get("photo_file_id")
            
            cloud.add_bot_user(bot_id, update.effective_user.id, update.effective_user.username or "",
                f"{update.effective_user.first_name or ''} {update.effective_user.last_name or ''}".strip())
            
            # ساخت کیبوردها
            reply_kb = build_keyboard_markup(bot_id)
            inline_kb = build_inline_buttons(bot_id, ctx)
            
            try:
                if photo_file_id:
                    # ✅ عکس + caption + دکمه شیشه‌ای + کیبورد زیر صفحه
                    await update.message.reply_photo(
                        photo=photo_file_id,
                        caption=message,
                        parse_mode=ParseMode.HTML,
                        reply_markup=inline_kb,  # دکمه‌های شیشه‌ای زیر عکس
                    )
                    # ✅ ارسال کیبورد ReplyKeyboard با یک پیام موقت که سریع حذف می‌شود
                    # چون reply_photo نمی‌تواند همزمان InlineKeyboard و ReplyKeyboard داشته باشد
                    if reply_kb:
                        temp_msg = await update.message.reply_text(
                            "⌨️",  # پیام کوتاه
                            reply_markup=reply_kb,
                        )
                        # حذف پیام موقت بعد از ۱ ثانیه (کیبورد باقی می‌ماند)
                        await asyncio.sleep(1)
                        try: await temp_msg.delete()
                        except: pass
                else:
                    # متن تنها
                    await update.message.reply_text(
                        message,
                        parse_mode=ParseMode.HTML,
                        reply_markup=inline_kb,
                    )
                    if reply_kb:
                        temp_msg = await update.message.reply_text("⌨️", reply_markup=reply_kb)
                        await asyncio.sleep(1)
                        try: await temp_msg.delete()
                        except: pass
                cloud.update_bot_stats(bot_id, mr=1, ms=1)
            except Exception as e:
                log.warning(f"Welcome failed for {bot_id}: {e}")
                # اگر عکس خراب بود، متن ساده بفرست
                try:
                    await update.message.reply_text(message, parse_mode=ParseMode.HTML)
                except: pass
        
        async def button_handler(update, ctx):
            q = update.callback_query
            data = q.data or ""
            if data.startswith("wbtn:"):
                enc = data.replace("wbtn:", "")
                text = ctx.bot_data.get("btn_texts", {}).get(enc, "")
                await q.answer()
                if text: await q.message.reply_text(text, parse_mode=ParseMode.HTML)
            elif data == f"sup_btn:{bot_id}":
                await q.answer()
                settings = cloud.get_plugin_settings(bot_id, "support")
                admin_msg = settings.get("admin_message", "📩 پیام خود را ارسال کنید.")
                user = q.from_user
                ticket = cloud.create_ticket(bot_id, user.id, user.username or "",
                    f"{user.first_name or ''} {user.last_name or ''}".strip())
                if not ticket:
                    await q.message.reply_text("❌ خطا"); return
                ctx.user_data[f"chat_{bot_id}"] = {"active": True, "ticket_id": ticket["ticket_id"]}
                cloud.save(force=True)
                await q.message.reply_text(
                    f"{admin_msg}\n\n🎫 <b>تیکت:</b> <code>{ticket['ticket_id']}</code>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔚 پایان چت", callback_data=f"end_chat:{bot_id}")]]))
        
        async def keyboard_action_handler(update, ctx):
            """مدیریت کلیک روی دکمه‌های ReplyKeyboard"""
            if not update.message or not update.message.text: return
            text = update.message.text
            if text.startswith("/"): return  # دستورات را رد کن
            
            settings = cloud.get_plugin_settings(bot_id, "keyboard")
            buttons_data = settings.get("buttons", [])
            
            # پیدا کردن دکمه
            btn_action = btn_value = None
            for row in buttons_data:
                for btn in row:
                    if btn.get("text") == text:
                        btn_action = btn.get("action", "text")
                        btn_value = btn.get("value", "")
                        break
                if btn_action: break
            
            if not btn_action: return  # دکمه‌ای یافت نشد
            
            cloud.add_bot_user(bot_id, update.effective_user.id, update.effective_user.username or "",
                f"{update.effective_user.first_name or ''} {update.effective_user.last_name or ''}".strip())
            
            try:
                if btn_action == "text":
                    await update.message.reply_text(btn_value, parse_mode=ParseMode.HTML)
                    cloud.update_bot_stats(bot_id, mr=1, ms=1)
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
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔚 پایان چت", callback_data=f"end_chat:{bot_id}")]]))
                elif btn_action == "url":
                    await update.message.reply_text(
                        f"🌐 <b>لینک:</b>",
                        parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 باز کردن", url=btn_value)]]))
            except Exception as e:
                log.warning(f"Keyboard action failed: {e}")
        
        # ثبت handler ها - فقط /start برای welcome (group=5)
        app.add_handler(CommandHandler("start", welcome_handler), group=5)
        app.add_handler(CallbackQueryHandler(button_handler, pattern=r"^wbtn:|^sup_btn:"), group=5)
        # handler برای کلیک روی دکمه‌های ReplyKeyboard (group بالاتر = اولویت کمتر)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, keyboard_action_handler), group=50)
    
    # ═══════ SUPPORT PLUGIN ═══════
    def _setup_support(self, app, bot_id):
        async def support_command(update, ctx):
            settings = cloud.get_plugin_settings(bot_id, "support")
            admin_msg = settings.get("admin_message", "📩 پیام خود را ارسال کنید.")
            cloud.add_bot_user(bot_id, update.effective_user.id, update.effective_user.username or "",
                f"{update.effective_user.first_name or ''} {update.effective_user.last_name or ''}".strip())
            user = update.effective_user
            ticket = cloud.create_ticket(bot_id, user.id, user.username or "",
                f"{user.first_name or ''} {user.last_name or ''}".strip())
            if not ticket:
                await update.message.reply_text("❌ خطا"); return
            ctx.user_data[f"chat_{bot_id}"] = {"active": True, "ticket_id": ticket["ticket_id"]}
            cloud.save(force=True)
            await update.message.reply_text(
                f"{admin_msg}\n\n🎫 <b>تیکت:</b> <code>{ticket['ticket_id']}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔚 پایان چت", callback_data=f"end_chat:{bot_id}")]]))
        
        async def callback_handler(update, ctx):
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
                tid = data.replace("reply_ticket:", "")
                ticket = cloud.get_ticket(tid)
                if not ticket or ticket["bot_id"] != bot_id:
                    await q.answer("❌ تیکت یافت نشد"); return
                bot_data = cloud.get_bot(bot_id)
                if not bot_data or bot_data.get("owner_uid") != q.from_user.id:
                    await q.answer("❌ دسترسی ندارید"); return
                ctx.user_data[f"admin_reply_{bot_id}"] = {
                    "active": True, "ticket_id": tid, "user_id": ticket["user_id"],
                }
                await q.answer()
                await q.message.reply_text(
                    f"💬 <b>پاسخ به تیکت {tid}</b>\n👤 {ticket['full_name']}\n\n✍️ پاسخ خود را بنویسید:",
                    parse_mode=ParseMode.HTML)
                return
            if data.startswith("close_ticket:"):
                tid = data.replace("close_ticket:", "")
                ticket = cloud.get_ticket(tid)
                if not ticket or ticket["bot_id"] != bot_id: return
                bot_data = cloud.get_bot(bot_id)
                if not bot_data or bot_data.get("owner_uid") != q.from_user.id: return
                cloud.close_ticket(tid); cloud.save(force=True)
                try:
                    temp_bot = Bot(token=bot_data["bot_token"])
                    await temp_bot.send_message(chat_id=ticket["user_id"],
                        text=f"🔚 <b>چت شما بسته شد.</b>\n🎫 تیکت: <code>{tid}</code>",
                        parse_mode=ParseMode.HTML)
                except: pass
                await q.answer("تیکت بسته شد")
                try: await q.edit_message_text(f"✅ <b>تیکت {tid} بسته شد</b>", parse_mode=ParseMode.HTML)
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
    
    async def start_bot(self, bot_id, bot_token):
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
                self._running[bot_id] = task; self._apps[bot_id] = app
            except InvalidToken: log.error(f"❌ Invalid token {bot_id}")
            except Exception as e: log.error(f"❌ Failed {bot_id}: {e}")
    
    async def stop_bot(self, bot_id):
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
    
    async def restart_bot(self, bot_id):
        bot_data = cloud.get_bot(bot_id)
        if not bot_data: return
        await self.stop_bot(bot_id)
        if bot_data.get("is_active"):
            await asyncio.sleep(1)
            await self.start_bot(bot_id, bot_data["bot_token"])
    
    async def _run(self, app, bot_id):
        try:
            await app.start()
            await app.updater.start_polling(allowed_updates=["message","callback_query"], drop_pending_updates=True)
            while True: await asyncio.sleep(60)
        except asyncio.CancelledError: pass
        except Exception as e: log.error(f"Bot {bot_id} error: {e}")
        finally:
            try:
                await app.updater.stop(); await app.stop(); await app.shutdown()
            except: pass
    
    async def start_all(self):
        for b in cloud.get_all_bots():
            if b.get("is_active") and not cloud.check_subscription_expired(b["bot_id"]):
                await self.start_bot(b["bot_id"], b["bot_token"])
    
    async def stop_all(self):
        for bid in list(self._running.keys()): await self.stop_bot(bid)

bot_factory = BotFactory()

# ══════════════════════════════════════════════════════════════════════
# UI - ReplyKeyboard تمیز ۴ دکمه‌ای
# ══════════════════════════════════════════════════════════════════════
async def check_membership(bot, uid):
    if not FORCE_JOIN_CHANNEL: return True
    try:
        m = await bot.get_chat_member(FORCE_JOIN_CHANNEL, uid)
        return m.status not in ("left","kicked","banned")
    except: return True

def kb_join():
    if not FORCE_JOIN_CHANNEL: return None
    url = f"https://t.me/{FORCE_JOIN_CHANNEL.lstrip('@')}" if FORCE_JOIN_CHANNEL.startswith("@") else f"https://t.me/c/{str(FORCE_JOIN_CHANNEL).replace('-100','')}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url=url)],
        [InlineKeyboardButton("✅ عضو شدم", callback_data="check_join")],
    ])

def kb_main(uid=0):
    """منوی اصلی ۴ دکمه‌ای تمیز"""
    rows = [
        [KeyboardButton("🤖 ربات‌های من"), KeyboardButton("👤 حساب کاربری")],
        [KeyboardButton("🎁 جایزه و دعوت"), KeyboardButton("📖 راهنما")],
    ]
    if uid and is_admin(uid):
        rows.append([KeyboardButton("👑 پنل ادمین")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)

REPLY_BUTTONS = {
    "🤖 ربات‌های من": "mybots",
    "👤 حساب کاربری": "account",
    "🎁 جایزه و دعوت": "rewards",
    "📖 راهنما": "help",
    "👑 پنل ادمین": "admin",
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
        btn_row = [InlineKeyboardButton(f"{'✅' if is_en else '⬜️'} {plugin['name']}", callback_data=f"plg:toggle:{bot_id}:{pid}")]
        if plugin.get("has_settings") and is_en:
            btn_row.append(InlineKeyboardButton("⚙️", callback_data=f"plg:set:{bot_id}:{pid}"))
        rows.append(btn_row)
    rows.append([InlineKeyboardButton("─── 💎 پلاگین‌های پرمیوم ───", callback_data="noop")])
    for pid, plugin in AVAILABLE_PLUGINS.items():
        if not plugin.get("is_premium"): continue
        is_pur = pid in purchased
        is_en = pid in enabled
        if is_pur:
            btn_row = [InlineKeyboardButton(f"{'✅' if is_en else '⏸️'} {plugin['name']}", callback_data=f"plg:toggle:{bot_id}:{pid}")]
            if plugin.get("has_settings") and is_en:
                btn_row.append(InlineKeyboardButton("⚙️", callback_data=f"plg:set:{bot_id}:{pid}"))
            rows.append(btn_row)
        else:
            rows.append([InlineKeyboardButton(f"🔒 {plugin['name']} ({plugin['cost_gc']} GC)", callback_data=f"plg:buy:{bot_id}:{pid}")])
    return InlineKeyboardMarkup(rows)

# ══════════════════════════════════════════════════════════════════════
# TEXT BUILDERS
# ══════════════════════════════════════════════════════════════════════
def build_welcome(uid):
    u = cloud.get_user(uid)
    if u and not u.get("signup_bonus_claimed"):
        bonus = f"\n🎁 <b>شما {SIGNUP_BONUS_GC} GC هدیه ثبت‌نام دریافت کردید!</b>\n\n"
    else: bonus = ""
    return (
        f"🤖✨ <b>به GramBotSaz خوش اومدی!</b> ✨🤖\n\n{bonus}"
        "پلتفرم حرفه‌ای ساخت ربات تلگرام بدون نیاز به کدنویسی.\n\n"
        "🔥 <b>ویژگی‌ها:</b>\n"
        f"  🆓 ۳ روز اشتراک آزمایشی رایگان\n"
        "  🧩 پلاگین‌های پیشرفته (چت زنده، کیبورد، دکمه شیشه‌ای)\n"
        "  💰 سیستم اقتصادی GramCoin\n\n"
        "💡 <b>از دکمه‌های زیر شروع کن:</b>"
    )

def build_account(uid):
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

def build_mybots(uid):
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
        uname = f"@{bot.get('bot_username')}" if bot.get("bot_username") else "—"
        text += f"• <b>{bot['bot_id'][:8]}</b> | {uname}\n  {status} | 🧩 {len(bot.get('enabled_plugins', []))} پلاگین\n\n"
        rows.append([InlineKeyboardButton(f"⚙️ مدیریت {bot['bot_id'][:8]}", callback_data=f"bot:manage:{bot['bot_id']}")])
    if len(bots) < MAX_FREE_BOTS:
        rows.append([InlineKeyboardButton("➕ ساخت ربات جدید", callback_data="create:bot")])
    return text, InlineKeyboardMarkup(rows)

def build_bot_detail(bot_id, bot_data):
    uname = f"@{bot_data.get('bot_username')}" if bot_data.get("bot_username") else "—"
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
        f"📛 <b>یوزرنیم:</b> {uname}\n\n"
        f"💎 <b>اشتراک:</b>\n{sub}\n\n"
        f"🧩 <b>پلاگین‌ها:</b> {len(bot_data.get('enabled_plugins', []))} فعال | {len(bot_data.get('purchased_plugins', []))} پرمیوم\n\n"
        f"📊 <b>آمار:</b>\n"
        f"• کاربران: {len(bot_data.get('bot_users', []))}\n"
        f"• پیام دریافتی: {stats.get('messages_received', 0)}\n"
        f"• پیام ارسالی: {stats.get('messages_sent', 0)}"
    )

def build_rewards(uid):
    u = cloud.ensure_user(uid)[0]
    bot_username = PLATFORM_BOT_USERNAME
    link = f"https://t.me/{bot_username}?start=ref_{u['referral_code']}" if bot_username else "—"
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

def build_help():
    return (
        "📖 <b>راهنمای GramBotSaz</b>\n\n"
        "<b>🤖 ساخت ربات:</b>\n"
        "۱. دکمه «🤖 ربات‌های من» را بزنید\n"
        "۲. «➕ ساخت ربات جدید» را بزنید\n"
        "۳. توکن را از @BotFather بگیرید و ارسال کنید\n"
        f"۴. ربات شما {TRIAL_DAYS} روز آزمایشی رایگان دارد!\n"
        "۵. از بخش پلاگین‌ها، قابلیت‌ها را شخصی‌سازی کنید\n\n"
        "<b>🆓 دوره آزمایشی:</b>\n"
        f"• {TRIAL_DAYS} روز اشتراک رایگان\n"
        "• پلاگین‌های welcome, support, keyboard فعال\n\n"
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

async def cmd_start(update, ctx):
    user = update.effective_user
    uid = user.id
    u, is_new = cloud.ensure_user(uid, user.username or "", f"{user.first_name or ''} {user.last_name or ''}".strip())
    
    if is_new and cloud.claim_signup_bonus(uid):
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
    
    await update.effective_message.reply_text(build_welcome(uid), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
    cloud.save()

async def cmd_cancel(update, ctx):
    for k in list(ctx.user_data.keys()):
        if k.startswith(("await_","welcome_","support_","keyboard_","admin_reply_","pending_","setpts_")):
            ctx.user_data.pop(k, None)
    await update.message.reply_text("❌ لغو شد.", parse_mode=ParseMode.HTML, reply_markup=kb_main(update.effective_user.id))

# ══════════════════════════════════════════════════════════════════════
# CALLBACK HANDLERS
# ══════════════════════════════════════════════════════════════════════
async def on_callback(update, ctx):
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
            pref = ctx.user_data.pop("pending_ref", None)
            if pref:
                ref_uid = cloud.find_uid_by_referral(pref)
                if ref_uid and cloud.apply_referral(uid, ref_uid):
                    cloud.add_gramcoin(ref_uid, REFERRAL_BONUS_GC, "referral")
                cloud.save(force=True)
            await q.edit_message_text(build_welcome(uid), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        else: await q.answer("⛔️ هنوز عضو نشدی!", show_alert=True)
        return
    
    # ═══ Navigation (بازگشت) ═══
    if data.startswith("nav:"):
        dest = data.replace("nav:", "")
        await q.answer()
        if dest == "home":
            await q.edit_message_text(build_welcome(uid), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        elif dest == "mybots":
            text, kb = build_mybots(uid)
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif dest == "account":
            await q.edit_message_text(build_account(uid), parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 تراکنش‌ها", callback_data="nav:txs")]]))
        elif dest == "rewards":
            await q.edit_message_text(build_rewards(uid), parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎁 دریافت جایزه روزانه", callback_data="daily:claim")]]))
        elif dest == "txs":
            await q.edit_message_text(build_transactions(uid), parse_mode=ParseMode.HTML, reply_markup=kb_back("account"))
        elif dest.startswith("bot:"):
            bot_id = dest.replace("bot:", "")
            bot = cloud.get_bot(bot_id)
            if bot and bot.get("owner_uid") == uid:
                await q.edit_message_text(build_bot_detail(bot_id, bot), parse_mode=ParseMode.HTML, reply_markup=kb_bot_mgmt(bot_id, bot))
        elif dest.startswith("plugins:"):
            bot_id = dest.replace("plugins:", "")
            await q.edit_message_text("🧩 <b>پلاگین‌ها</b>\n\n💡 ⚙️ = شخصی‌سازی", parse_mode=ParseMode.HTML, reply_markup=kb_plugins(bot_id))
        elif dest.startswith("plugin:"):
            parts = dest.replace("plugin:", "").split(":")
            bot_id, pid = parts[0], parts[1]
            await _show_plugin_settings(q, bot_id, pid)
        return
    
    # ═══ My Bots ═══
    if data == "mybots":
        await q.answer()
        text, kb = build_mybots(uid)
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return
    
    if data == "create:bot":
        user_bots = cloud.get_user_bots(uid)
        if len(user_bots) >= MAX_FREE_BOTS:
            await q.answer(f"⚠️ حداکثر {MAX_FREE_BOTS} ربات!", show_alert=True); return
        ctx.user_data["await_"] = "bot_token"
        await q.answer()
        await q.edit_message_text(
            f"🤖 <b>ساخت ربات جدید</b>\n\n"
            f"🎁 {TRIAL_DAYS} روز آزمایشی رایگان!\n\n"
            "🔑 توکن ربات را از @BotFather بگیرید و ارسال کنید:",
            parse_mode=ParseMode.HTML)
        return
    
    # ═══ Account / Rewards ═══
    if data == "account":
        await q.answer()
        await q.edit_message_text(build_account(uid), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 تراکنش‌ها", callback_data="nav:txs")],
                [InlineKeyboardButton("🎁 دریافت جایزه روزانه", callback_data="daily:claim")],
            ]))
        return
    
    if data == "rewards":
        await q.answer()
        await q.edit_message_text(build_rewards(uid), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎁 دریافت جایزه روزانه", callback_data="daily:claim")]]))
        return
    
    if data == "daily:claim":
        if cloud.can_claim_daily(uid):
            cloud.claim_daily_reward(uid, DAILY_REWARD_GC)
            cloud.save(force=True)
            new_balance = cloud.get_user(uid).get("gramcoin_balance", 0)
            await q.answer(f"✅ +{DAILY_REWARD_GC} GC", show_alert=True)
            try:
                await q.edit_message_text(
                    f"🎁 <b>جایزه روزانه دریافت شد!</b>\n\n"
                    f"⭐️ <b>+{DAILY_REWARD_GC} GC</b>\n"
                    f"💰 موجودی: <code>{new_balance} GC</code>",
                    parse_mode=ParseMode.HTML)
            except: pass
        else:
            await q.answer("⏳ فردا!", show_alert=True)
        return
    
    if data == "help":
        await q.answer()
        await q.edit_message_text(build_help(), parse_mode=ParseMode.HTML, reply_markup=kb_back("home"))
        return
    
    # ═══ Bot Management ═══
    if data.startswith("bot:manage:"):
        bot_id = data.split(":")[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        await q.answer()
        await q.edit_message_text(build_bot_detail(bot_id, bot), parse_mode=ParseMode.HTML, reply_markup=kb_bot_mgmt(bot_id, bot))
        return
    
    if data.startswith("bot:sub:"):
        bot_id = data.split(":")[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        await q.answer()
        await q.edit_message_text(
            f"💎 <b>اشتراک ربات {bot_id[:8]}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🥉 هفتگی — {SUBSCRIPTION_WEEKLY_GC} GC", callback_data=f"sub:buy:{bot_id}:weekly")],
                [InlineKeyboardButton(f"🥈 ماهانه — {SUBSCRIPTION_MONTHLY_GC} GC", callback_data=f"sub:buy:{bot_id}:monthly")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data=f"nav:bot:{bot_id}")],
            ]))
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
                f"✅ <b>اشتراک {plan_name} فعال شد!</b>\n\n📅 {new_exp.strftime('%Y-%m-%d')}\n💰 {cost} GC کسر شد.",
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
            try: await q.edit_message_text(f"✅ <b>{plugin['name']} خریداری شد!</b>\n💰 {cost} GC", parse_mode=ParseMode.HTML,
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
            f"📊 <b>آمار ربات {bot_id[:8]}</b>\n\n"
            f"👥 کاربران: {len(bot.get('bot_users', []))}\n"
            f"📨 دریافتی: {stats.get('messages_received', 0)}\n"
            f"📤 ارسالی: {stats.get('messages_sent', 0)}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"nav:bot:{bot_id}")]]))
        return
    
    if data.startswith("bot:del:"):
        bot_id = data.split(":")[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        await q.answer("در حال حذف...")
        await bot_factory.stop_bot(bot_id)
        cloud.delete_bot(bot_id, uid); cloud.save(force=True)
        text, kb = build_mybots(uid)
        await q.edit_message_text("🗑 ربات حذف شد.\n\n" + text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return
    
    # ═══ Plugin Settings Actions ═══
    # Welcome
    if data.startswith("welcome:"):
        parts = data.split(":")
        action, bot_id = parts[1], parts[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        await q.answer()
        
        if action == "edit_text":
            ctx.user_data["await_"] = f"welcome_text:{bot_id}"
            settings = cloud.get_plugin_settings(bot_id, "welcome")
            await q.edit_message_text(
                f"✏️ <b>ویرایش متن خوش‌آمد</b>\n\n"
                f"📝 <b>فعلی:</b>\n<blockquote expandable>{settings.get('message','')}</blockquote>\n\n"
                f"💡 HTML پشتیبانی می‌شود. متن جدید را ارسال کنید:",
                parse_mode=ParseMode.HTML)
        elif action == "edit_photo":
            ctx.user_data["await_"] = f"welcome_photo:{bot_id}"
            settings = cloud.get_plugin_settings(bot_id, "welcome")
            current = "✅ دارد" if settings.get("photo_file_id") else "❌ ندارد"
            await q.edit_message_text(
                f"🖼 <b>تنظیم عکس خوش‌آمد</b>\n\n📊 وضعیت فعلی: {current}\n\n"
                f"📸 برای تنظیم: <b>یک عکس را مستقیماً ارسال کنید</b>\n"
                f"🗑 برای حذف: عبارت <code>none</code> را ارسال کنید",
                parse_mode=ParseMode.HTML)
        elif action == "edit_buttons":
            ctx.user_data["await_"] = f"welcome_buttons:{bot_id}"
            settings = cloud.get_plugin_settings(bot_id, "welcome")
            buttons = settings.get("buttons", [])
            btn_text = ""
            for i, row in enumerate(buttons, 1):
                btns = ", ".join([f"[{b.get('text','')}]" for b in row])
                btn_text += f"ردیف {i}: {btns}\n"
            if not btn_text: btn_text = "هیچ دکمه‌ای تعریف نشده"
            await q.edit_message_text(
                f"🔘 <b>دکمه‌های شیشه‌ای</b>\n\n📋 فعلی:\n{btn_text}\n\n"
                f"💡 فرمت (هر خط یک ردیف، کاما برای چند دکمه):\n"
                f"<code>🌐 وبسایت|url|https://example.com, 📱 تماس|url|https://t.me/user</code>\n"
                f"<code>🎫 پشتیبانی|support, ℹ️ درباره|text|ما حرفه‌ای هستیم</code>\n\n"
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
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"nav:plugin:{bot_id}:welcome")]]))
        return
    
    # Support
    if data.startswith("support:"):
        parts = data.split(":")
        action, bot_id = parts[1], parts[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        await q.answer()
        
        if action in ("edit_admin", "edit_confirm", "edit_end"):
            ctx.user_data["await_"] = f"support_{action}:{bot_id}"
            settings = cloud.get_plugin_settings(bot_id, "support")
            field_map = {"edit_admin": "admin_message", "edit_confirm": "confirm_message", "edit_end": "end_chat_message"}
            field_name = {"edit_admin": "پیام خوش‌آمد چت", "edit_confirm": "پیام تایید ارسال", "edit_end": "پیام پایان چت"}
            current = settings.get(field_map[action], "")
            await q.edit_message_text(
                f"✏️ <b>{field_name[action]}</b>\n\n📝 فعلی:\n<blockquote>{current}</blockquote>\n\nمتن جدید را ارسال کنید:",
                parse_mode=ParseMode.HTML)
        elif action == "list_tickets":
            tickets = cloud.get_bot_tickets(bot_id)
            open_t = [t for t in tickets if t["status"] == "open"]
            if not open_t:
                await q.edit_message_text("📭 تیکت بازی وجود ندارد.", parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"nav:plugin:{bot_id}:support")]]))
                return
            text = f"📋 <b>تیکت‌های باز ({len(open_t)}):</b>\n\n"
            rows = []
            for t in open_t[-20:]:
                text += f"• <code>{t['ticket_id']}</code> | {t['full_name']}\n"
                rows.append([InlineKeyboardButton(f"💬 {t['ticket_id']}", callback_data=f"admin_reply:{t['ticket_id']}")])
            rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"nav:plugin:{bot_id}:support")])
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        elif action == "reset":
            cloud.update_plugin_settings(bot_id, "support", {
                "admin_message": "📩 پیام خود را ارسال کنید. در سریع‌ترین زمان پاسخ می‌دهیم.",
                "confirm_message": "✅ پیام شما ارسال شد.",
                "end_chat_message": "🔚 چت پایان یافت.",
            })
            cloud.save(force=True); await bot_factory.restart_bot(bot_id)
            await q.edit_message_text("✅ بازنشانی شد!", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"nav:plugin:{bot_id}:support")]]))
        return
    
    if data.startswith("admin_reply:"):
        tid = data.replace("admin_reply:", "")
        ticket = cloud.get_ticket(tid)
        if not ticket: await q.answer("❌ تیکت یافت نشد", show_alert=True); return
        bot = cloud.get_bot(ticket["bot_id"])
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        ctx.user_data[f"admin_reply_{ticket['bot_id']}"] = {
            "active": True, "ticket_id": tid, "user_id": ticket["user_id"],
        }
        ctx.user_data["await_"] = f"admin_reply:{ticket['bot_id']}"
        await q.answer()
        await q.edit_message_text(f"💬 <b>پاسخ به تیکت {tid}</b>\n👤 {ticket['full_name']}\n\n✍️ پاسخ را بنویسید:", parse_mode=ParseMode.HTML)
        return
    
    # Keyboard
    if data.startswith("keyboard:"):
        parts = data.split(":")
        action, bot_id = parts[1], parts[2]
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await q.answer("❌ دسترسی ندارید", show_alert=True); return
        await q.answer()
        
        if action == "edit_buttons":
            ctx.user_data["await_"] = f"keyboard_buttons:{bot_id}"
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
        elif action in ("toggle_resize", "toggle_onetime", "toggle_persistent"):
            settings = cloud.get_plugin_settings(bot_id, "keyboard")
            key_map = {"toggle_resize": "resize_keyboard", "toggle_onetime": "one_time_keyboard", "toggle_persistent": "persistent"}
            key = key_map[action]
            settings[key] = not settings.get(key, True)
            cloud.update_plugin_settings(bot_id, "keyboard", settings)
            cloud.save(force=True); await bot_factory.restart_bot(bot_id)
            await q.answer("✅ ذخیره شد")
            # نمایش مجدد صفحه
            resize = settings.get("resize_keyboard", True)
            one_time = settings.get("one_time_keyboard", False)
            persistent = settings.get("persistent", True)
            try:
                await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"تغییر اندازه: {'✅' if resize else '❌'}", callback_data=f"keyboard:toggle_resize:{bot_id}")],
                    [InlineKeyboardButton(f"یکبار مصرف: {'✅' if one_time else '❌'}", callback_data=f"keyboard:toggle_onetime:{bot_id}")],
                    [InlineKeyboardButton(f"دائمی: {'✅' if persistent else '❌'}", callback_data=f"keyboard:toggle_persistent:{bot_id}")],
                    [InlineKeyboardButton("🔙 بازگشت", callback_data=f"nav:plugin:{bot_id}:keyboard")],
                ]))
            except: pass
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
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"nav:plugin:{bot_id}:keyboard")]]))
        return
    
    # Admin
    if not is_admin(uid):
        await q.answer("⛔️ دسترسی ندارید.", show_alert=True); return
    
    if data == "admin":
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
        total = len(users); start = page * 8; end = min(start+8, total)
        text = f"👥 <b>کاربران</b> ({page+1})\n\n"
        rows = []
        for u in users[start:end]:
            text += f"• {u.get('full_name','—')} | <code>{u.get('uid')}</code>\n  💰 {u.get('gramcoin_balance', 0)} GC\n"
            rows.append([InlineKeyboardButton(f"🎁 +50", callback_data=f"admin:givegc:{u.get('uid')}:50")])
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"admin:users:{page-1}"))
        if end < total: nav.append(InlineKeyboardButton("▶️", callback_data=f"admin:users:{page+1}"))
        if nav: rows.append(nav)
        rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin")])
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return
    
    if data.startswith("admin:givegc:"):
        _, _, uid_str, amount = data.split(":")
        cloud.add_gramcoin(int(uid_str), int(amount), "admin_grant")
        cloud.save(force=True)
        await q.answer(f"✅ +{amount} GC", show_alert=True)
        return
    
    if data.startswith("admin:bots:"):
        page = int(data.split(":")[-1])
        await q.answer()
        bots = cloud.get_all_bots(); total = len(bots); start = page * 8; end = min(start+8, total)
        text = f"🤖 <b>ربات‌ها</b> ({page+1})\n\n"
        for b in bots[start:end]:
            exp = cloud.check_subscription_expired(b["bot_id"])
            status = "🔴" if exp else ("🆓" if b.get("is_trial") else ("🟢" if b.get("is_active") else "⏸️"))
            text += f"{status} <code>{b['bot_id'][:8]}</code> | Owner: <code>{b.get('owner_uid')}</code>\n\n"
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"admin:bots:{page-1}"))
        if end < total: nav.append(InlineKeyboardButton("▶️", callback_data=f"admin:bots:{page+1}"))
        rows = [nav] if nav else []
        rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin")])
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return
    
    if data == "admin:broadcast":
        ctx.user_data["await_"] = "broadcast"
        await q.answer()
        await q.edit_message_text("📢 پیام را ارسال کنید:", parse_mode=ParseMode.HTML)
        return
    
    await q.answer()

# ══════════════════════════════════════════════════════════════════════
# PLUGIN SETTINGS PAGES
# ══════════════════════════════════════════════════════════════════════
async def _show_plugin_settings(q, bot_id, pid):
    """نمایش صفحه تنظیمات پلاگین"""
    if pid == "welcome":
        settings = cloud.get_plugin_settings(bot_id, "welcome")
        msg = settings.get("message", "")[:200]
        photo = "✅ دارد" if settings.get("photo_file_id") else "❌ ندارد"
        btns = sum(len(r) for r in settings.get("buttons", []))
        await q.edit_message_text(
            f"⚙️ <b>خوش‌آمد پیشرفته</b>\n\n"
            f"📝 پیام: <blockquote expandable>{msg}...</blockquote>\n"
            f"🖼 عکس: {photo}\n🔘 دکمه‌ها: {btns}",
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
            f"⚙️ <b>پشتیبانی چت زنده</b>\n\n📊 باز: <b>{open_c}</b> | بسته: <b>{closed_c}</b>\n\n"
            f"💡 کاربران با /support یا دکمه شیشه‌ای چت را شروع می‌کنند.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ پیام خوش‌آمد", callback_data=f"support:edit_admin:{bot_id}")],
                [InlineKeyboardButton("✏️ پیام تایید", callback_data=f"support:edit_confirm:{bot_id}")],
                [InlineKeyboardButton("✏️ پیام پایان", callback_data=f"support:edit_end:{bot_id}")],
                [InlineKeyboardButton("📋 تیکت‌های باز", callback_data=f"support:list_tickets:{bot_id}")],
                [InlineKeyboardButton("♻️ بازنشانی", callback_data=f"support:reset:{bot_id}")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:plugins:{bot_id}")],
            ]))
    elif pid == "keyboard":
        settings = cloud.get_plugin_settings(bot_id, "keyboard")
        btns = sum(len(r) for r in settings.get("buttons", []))
        resize = settings.get("resize_keyboard", True)
        one_time = settings.get("one_time_keyboard", False)
        persistent = settings.get("persistent", True)
        await q.edit_message_text(
            f"⌨️ <b>کیبورد اختصاصی</b>\n\n🔘 دکمه‌ها: {btns}\n\n"
            f"💡 این دکمه‌ها زیر صفحه چت ربات شما نمایش داده می‌شوند.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔘 مدیریت دکمه‌ها", callback_data=f"keyboard:edit_buttons:{bot_id}")],
                [InlineKeyboardButton(f"تغییر اندازه: {'✅' if resize else '❌'}", callback_data=f"keyboard:toggle_resize:{bot_id}")],
                [InlineKeyboardButton(f"یکبار مصرف: {'✅' if one_time else '❌'}", callback_data=f"keyboard:toggle_onetime:{bot_id}")],
                [InlineKeyboardButton(f"دائمی: {'✅' if persistent else '❌'}", callback_data=f"keyboard:toggle_persistent:{bot_id}")],
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
async def on_message(update, ctx):
    uid = update.effective_user.id
    text_in = (update.message.text or "").strip() if update.message.text else ""
    awaiting = ctx.user_data.get("await_")
    
    if text_in == "/cancel":
        await cmd_cancel(update, ctx); return
    
    # ═══ ReplyKeyboard handlers ═══
    if text_in in REPLY_BUTTONS and not awaiting:
        action = REPLY_BUTTONS[text_in]
        if action == "mybots":
            text, kb = build_mybots(uid)
            await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif action == "account":
            await update.message.reply_text(build_account(uid), parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📊 تراکنش‌ها", callback_data="nav:txs")],
                    [InlineKeyboardButton("🎁 جایزه روزانه", callback_data="daily:claim")],
                ]))
        elif action == "rewards":
            await update.message.reply_text(build_rewards(uid), parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎁 دریافت جایزه", callback_data="daily:claim")]]))
        elif action == "help":
            await update.message.reply_text(build_help(), parse_mode=ParseMode.HTML)
        elif action == "admin" and is_admin(uid):
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
    
    # ═══ Bot token ═══
    if awaiting == "bot_token":
        ctx.user_data.pop("await_", None)
        bot_token = text_in.strip()
        if not bot_token or ":" not in bot_token or len(bot_token) < 40:
            await update.message.reply_text("❌ توکن نامعتبر!", parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
            return
        try:
            temp = Bot(token=bot_token)
            me = await temp.get_me()
        except:
            await update.message.reply_text("❌ توکن نامعتبر!", parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
            return
        user_bots = cloud.get_user_bots(uid)
        if len(user_bots) >= MAX_FREE_BOTS:
            await update.message.reply_text(f"⚠️ حداکثر {MAX_FREE_BOTS} ربات!", parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
            return
        bot_data = cloud.create_bot(uid, bot_token)
        cloud.save(force=True)
        await bot_factory.start_bot(bot_data["bot_id"], bot_token)
        await update.message.reply_text(
            f"✅ <b>ربات ساخته شد!</b>\n\n"
            f"🤖 @{me.username}\n🆔 <code>{bot_data['bot_id']}</code>\n\n"
            f"🆓 {TRIAL_DAYS} روز آزمایشی فعال شد!\n"
            f"📅 انقضا: {datetime.fromisoformat(bot_data['subscription_expiry']).strftime('%Y-%m-%d')}",
            parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        return
    
    # ═══ Welcome text ═══
    if awaiting and awaiting.startswith("welcome_text:"):
        bot_id = awaiting.replace("welcome_text:", "")
        ctx.user_data.pop("await_", None)
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await update.message.reply_text("❌ دسترسی ندارید.", reply_markup=kb_main(uid)); return
        settings = cloud.get_plugin_settings(bot_id, "welcome")
        max_len = 1024 if settings.get("photo_file_id") else 4096
        if len(text_in) > max_len:
            await update.message.reply_text(f"❌ حداکثر {max_len} کاراکتر!", parse_mode=ParseMode.HTML); return
        settings["message"] = text_in
        cloud.update_plugin_settings(bot_id, "welcome", settings)
        cloud.save(force=True)
        await bot_factory.restart_bot(bot_id)
        await update.message.reply_text(
            "✅ <b>متن ذخیره شد!</b>\n🔄 ربات ری‌استارت شد.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تنظیمات بیشتر", callback_data=f"nav:plugin:{bot_id}:welcome")]]))
        return
    
    # ═══ Welcome photo ═══
    if awaiting and awaiting.startswith("welcome_photo:") and update.message.photo:
        bot_id = awaiting.replace("welcome_photo:", "")
        ctx.user_data.pop("await_", None)
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await update.message.reply_text("❌ دسترسی ندارید."); return
        # ✅ گرفتن بزرگترین سایز عکس
        photo = update.message.photo[-1]
        file_id = photo.file_id
        
        # ✅ اعتبارسنجی file_id با دانلود تستی
        try:
            file = await ctx.bot.get_file(file_id)
            # تست معتبر بودن (اگر خطا دهد، catch می‌شود)
            if not file.file_id:
                raise Exception("Invalid file_id")
        except Exception as e:
            await update.message.reply_text(
                f"❌ <b>عکس معتبر نیست!</b>\n\nلطفاً دوباره عکس را ارسال کنید.",
                parse_mode=ParseMode.HTML); return
        
        settings = cloud.get_plugin_settings(bot_id, "welcome")
        settings["photo_file_id"] = file_id
        cloud.update_plugin_settings(bot_id, "welcome", settings)
        cloud.save(force=True)
        await bot_factory.restart_bot(bot_id)
        
        await update.message.reply_text(
            f"✅ <b>عکس با موفقیت تنظیم شد!</b>\n\n"
            f"🖼 <b>File ID:</b> <code>{file_id[:30]}...</code>\n"
            f"🔄 ربات ری‌استارت شد.\n\n"
            f"💡 حالا کاربران با /start این عکس را می‌بینند.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تنظیمات بیشتر", callback_data=f"nav:plugin:{bot_id}:welcome")]]))
        return
    
    # ═══ Welcome photo (none for remove) ═══
    if awaiting and awaiting.startswith("welcome_photo:"):
        bot_id = awaiting.replace("welcome_photo:", "")
        ctx.user_data.pop("await_", None)
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await update.message.reply_text("❌ دسترسی ندارید."); return
        if text_in.lower() == "none":
            settings = cloud.get_plugin_settings(bot_id, "welcome")
            settings["photo_file_id"] = None
            cloud.update_plugin_settings(bot_id, "welcome", settings)
            cloud.save(force=True)
            await bot_factory.restart_bot(bot_id)
            await update.message.reply_text("🖼 <b>عکس حذف شد!</b>", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تنظیمات بیشتر", callback_data=f"nav:plugin:{bot_id}:welcome")]]))
        else:
            await update.message.reply_text("❌ برای حذف، <code>none</code> ارسال کنید. برای تنظیم، عکس را آپلود کنید.", parse_mode=ParseMode.HTML)
        return
    
    # ═══ Welcome buttons ═══
    if awaiting and awaiting.startswith("welcome_buttons:"):
        bot_id = awaiting.replace("welcome_buttons:", "")
        ctx.user_data.pop("await_", None)
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await update.message.reply_text("❌ دسترسی ندارید."); return
        settings = cloud.get_plugin_settings(bot_id, "welcome")
        if text_in.lower() == "none":
            settings["buttons"] = []
            cloud.update_plugin_settings(bot_id, "welcome", settings)
            cloud.save(force=True); await bot_factory.restart_bot(bot_id)
            await update.message.reply_text("🗑 همه دکمه‌ها حذف شدند!", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تنظیمات", callback_data=f"nav:plugin:{bot_id}:welcome")]]))
            return
        new_buttons = []
        for line in text_in.split("\n"):
            line = line.strip()
            if not line: continue
            btn_texts = [b.strip() for b in line.split(",")]
            current_row = []
            for btn_full in btn_texts:
                parts = btn_full.split("|")
                if len(parts) < 2: continue
                btn = {"text": parts[0].strip(), "type": parts[1].strip().lower()}
                if len(parts) > 2:
                    v = parts[2].strip()
                    if btn["type"] == "url": btn["url"] = v
                    elif btn["type"] == "text": btn["value"] = v
                    elif btn["type"] == "callback": btn["callback"] = v[:64]
                current_row.append(btn)
            if current_row: new_buttons.append(current_row)
        if not new_buttons:
            await update.message.reply_text("❌ هیچ دکمه معتبری یافت نشد."); return
        settings["buttons"] = new_buttons
        cloud.update_plugin_settings(bot_id, "welcome", settings)
        cloud.save(force=True); await bot_factory.restart_bot(bot_id)
        total = sum(len(r) for r in new_buttons)
        await update.message.reply_text(f"✅ <b>{total} دکمه ذخیره شد!</b>", parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تنظیمات", callback_data=f"nav:plugin:{bot_id}:welcome")]]))
        return
    
    # ═══ Support settings ═══
    if awaiting and awaiting.startswith("support_"):
        parts = awaiting.replace("support_", "").split(":")
        if len(parts) != 2: ctx.user_data.pop("await_", None); return
        action, bot_id = parts
        ctx.user_data.pop("await_", None)
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await update.message.reply_text("❌ دسترسی ندارید."); return
        settings = cloud.get_plugin_settings(bot_id, "support")
        field_map = {"edit_admin": "admin_message", "edit_confirm": "confirm_message", "edit_end": "end_chat_message"}
        field = field_map.get(action)
        if field:
            settings[field] = text_in
            cloud.update_plugin_settings(bot_id, "support", settings)
            cloud.save(force=True); await bot_factory.restart_bot(bot_id)
            await update.message.reply_text("✅ ذخیره شد!", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تنظیمات", callback_data=f"nav:plugin:{bot_id}:support")]]))
        return
    
    # ═══ Keyboard buttons ═══
    if awaiting and awaiting.startswith("keyboard_buttons:"):
        bot_id = awaiting.replace("keyboard_buttons:", "")
        ctx.user_data.pop("await_", None)
        bot = cloud.get_bot(bot_id)
        if not bot or bot.get("owner_uid") != uid:
            await update.message.reply_text("❌ دسترسی ندارید."); return
        settings = cloud.get_plugin_settings(bot_id, "keyboard")
        if text_in.lower() == "none":
            settings["buttons"] = []
            cloud.update_plugin_settings(bot_id, "keyboard", settings)
            cloud.save(force=True); await bot_factory.restart_bot(bot_id)
            await update.message.reply_text("🗑 همه دکمه‌ها حذف شدند!", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تنظیمات", callback_data=f"nav:plugin:{bot_id}:keyboard")]]))
            return
        new_buttons = []
        for line in text_in.split("\n"):
            line = line.strip()
            if not line: continue
            btn_texts = [b.strip() for b in line.split(",")]
            current_row = []
            for btn_full in btn_texts:
                parts = btn_full.split("|")
                if len(parts) < 2: continue
                btn = {"text": parts[0].strip(), "action": parts[1].strip().lower()}
                if len(parts) > 2: btn["value"] = parts[2].strip()
                current_row.append(btn)
            if current_row: new_buttons.append(current_row)
        if not new_buttons:
            await update.message.reply_text("❌ هیچ دکمه معتبری یافت نشد."); return
        settings["buttons"] = new_buttons
        cloud.update_plugin_settings(bot_id, "keyboard", settings)
        cloud.save(force=True); await bot_factory.restart_bot(bot_id)
        total = sum(len(r) for r in new_buttons)
        await update.message.reply_text(f"✅ <b>{total} دکمه ذخیره شد!</b>\n💡 این دکمه‌ها زیر صفحه چت ربات شما نمایش داده می‌شوند.", parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تنظیمات", callback_data=f"nav:plugin:{bot_id}:keyboard")]]))
        return
    
    # ═══ Admin reply to ticket (رفع باگ اصلی!) ═══
    for key in list(ctx.user_data.keys()):
        if key.startswith("admin_reply_"):
            state = ctx.user_data.get(key)
            if not state or not state.get("active"): continue
            bot_id = key.replace("admin_reply_", "")
            tid = state.get("ticket_id")
            target_uid = state.get("user_id")
            ticket = cloud.get_ticket(tid)
            if not ticket or ticket["status"] != "open":
                ctx.user_data.pop(key, None)
                ctx.user_data.pop("await_", None)
                await update.message.reply_text("⚠️ تیکت بسته شده است."); return
            bot = cloud.get_bot(bot_id)
            if not bot or bot.get("owner_uid") != uid:
                ctx.user_data.pop(key, None); return
            try:
                temp_bot = Bot(token=bot["bot_token"])
                await temp_bot.send_message(
                    chat_id=target_uid,
                    text=f"💬 <b>پاسخ پشتیبانی</b>\n\n🎫 تیکت: <code>{tid}</code>\n\n{text_in}",
                    parse_mode=ParseMode.HTML)
                await update.message.reply_text(
                    f"✅ <b>پاسخ ارسال شد!</b>\n\n👤 به: {ticket['full_name']}\n"
                    f"💬 برای پاسخ دیگر، همینجا بنویسید.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ بستن تیکت", callback_data=f"close_ticket:{tid}")]]))
                cloud.update_bot_stats(bot_id, ms=1)
            except Forbidden:
                await update.message.reply_text("❌ کاربر ربات را بلاک کرده.")
            except Exception as e:
                await update.message.reply_text(f"❌ خطا: {str(e)[:100]}")
            return
    
    # ═══ Admin broadcast ═══
    if awaiting == "broadcast" and is_admin(uid):
        ctx.user_data.pop("await_", None)
        users = cloud.get_all_users()
        sent = 0
        for u in users:
            try:
                await ctx.bot.send_message(int(u.get("uid_str") or u.get("uid")), text=text_in, parse_mode=ParseMode.HTML)
                sent += 1; await asyncio.sleep(0.05)
            except: pass
        await update.message.reply_text(f"✅ ارسال شد: {sent}/{len(users)}", parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        return

# ══════════════════════════════════════════════════════════════════════
# BACKGROUND
# ══════════════════════════════════════════════════════════════════════
async def subscription_watcher(app):
    while True:
        try:
            await asyncio.sleep(3600)
            for b in cloud.get_all_bots():
                if not b.get("is_active"): continue
                if cloud.check_subscription_expired(b["bot_id"]):
                    cloud.deactivate_bot(b["bot_id"])
                    await bot_factory.stop_bot(b["bot_id"])
                    cloud.save(force=True)
                    try:
                        await app.bot.send_message(b["owner_uid"],
                            f"🔴 <b>اشتراک ربات {b['bot_id'][:8]} منقضی شد!</b>", parse_mode=ParseMode.HTML)
                    except: pass
        except asyncio.CancelledError: break
        except Exception as e: log.error(f"Watcher error: {e}")

async def autosave(app):
    while True:
        try: await asyncio.sleep(300); cloud.save()
        except asyncio.CancelledError: break
        except Exception as e: log.error(f"Autosave error: {e}")

async def on_startup(app):
    global PLATFORM_BOT_USERNAME
    try:
        me = await app.bot.get_me()
        PLATFORM_BOT_USERNAME = me.username or ""
    except: pass
    await bot_factory.start_all()
    asyncio.create_task(subscription_watcher(app))
    asyncio.create_task(autosave(app))
    log.info("GramBotSaz v5.0 (Clean Professional) ready ✅")

async def on_shutdown(app):
    await bot_factory.stop_all()
    cloud.save(force=True)

def main():
    if not PLATFORM_BOT_TOKEN:
        log.critical("PLATFORM_BOT_TOKEN تنظیم نشده!"); sys.exit(1)
    cloud.load()
    app = (Application.builder().token(PLATFORM_BOT_TOKEN)
           .post_init(on_startup).post_shutdown(on_shutdown).build())
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND & filters.ChatType.PRIVATE, on_message))
    
    def _sig(s, f): cloud.save(force=True); sys.exit(0)
    signal.signal(signal.SIGTERM, _sig); signal.signal(signal.SIGINT, _sig)
    atexit.register(lambda: cloud.save(force=True))
    
    webhook_url = os.environ.get("RENDER_EXTERNAL_URL")
    if webhook_url:
        log.info("🌐 Flask Webhook")
        main_loop = asyncio.new_event_loop()
        def run_loop(loop):
            asyncio.set_event_loop(loop); loop.run_forever()
        threading.Thread(target=run_loop, args=(main_loop,), daemon=True).start()
        flask_app = Flask(__name__)
        @flask_app.route("/")
        def index(): return "🤖 GramBotSaz v5.0 Alive!", 200
        @flask_app.route(f"/{PLATFORM_BOT_TOKEN}", methods=["POST"])
        def webhook():
            try:
                update = Update.de_json(request.get_json(force=True), app.bot)
                asyncio.run_coroutine_threadsafe(app.process_update(update), main_loop)
            except Exception as e: log.error(f"Webhook error: {e}")
            return "OK", 200
        async def setup():
            url = webhook_url.rstrip("/") + "/" + PLATFORM_BOT_TOKEN
            await app.bot.delete_webhook(drop_pending_updates=True)
            await asyncio.sleep(1)
            await app.bot.set_webhook(url=url, allowed_updates=["message","callback_query"], drop_pending_updates=True)
        asyncio.run_coroutine_threadsafe(setup(), main_loop).result(timeout=30)
        async def init():
            await app.initialize(); await app.start(); await on_startup(app)
        asyncio.run_coroutine_threadsafe(init(), main_loop).result(timeout=30)
        flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
    else:
        log.warning("⚠️ Polling mode")
        app.run_polling(allowed_updates=["message","callback_query"], drop_pending_updates=True)
#
if __name__ == "__main__":
    main()
