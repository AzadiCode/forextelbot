#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GramSaz — پلتفرم حرفه‌ای ربات‌ساز تلگرام (v3.0)
════════════════════════════════════════════════════════════════════════════
فول async: python-telegram-bot (v20+) + Motor (MongoDB async، اختیاری)
════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import os
import sys
import json
import time
import html as html_lib
import hashlib
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.constants import ParseMode
from telegram.error import InvalidToken, Forbidden, RetryAfter, BadRequest
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters,
)

try:
    from motor.motor_asyncio import AsyncIOMotorClient
    MOTOR_AVAILABLE = True
except ImportError:
    MOTOR_AVAILABLE = False


# ══════════════════════════════════════════════════════════════════════
# لاگ‌گذاری
# ══════════════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO, stream=sys.stdout,
)
log = logging.getLogger("GramSaz")


# ══════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════
PLATFORM_BOT_TOKEN = os.getenv("PLATFORM_BOT_TOKEN", "8911189546:AAHJDYtsNZmc-UGi6PH2AYIMF1ePS-Q0Y14").strip()
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip().rstrip("/")
PORT = int(os.getenv("PORT", "10000"))

ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "605611554").replace(" ", "").split(",")
             if x.strip().lstrip("-").isdigit()}

# می‌تونه چند کانال باشه، با کاما جدا: "@channel1,-1001234567890"
FORCE_JOIN_CHANNELS = [c.strip() for c in os.getenv("FORCE_JOIN_CHANNEL", "@testchanellaz").split(",") if c.strip()]

COIN_SYMBOL = "GC"
SIGNUP_BONUS = 50
REFERRAL_BONUS_REFERRER = 80
REFERRAL_BONUS_NEWCOMER = 20
DOWNLINE_BONUS_PER_USER = 3
DOWNLINE_DAILY_CAP = 15
BROADCAST_DELAY_SEC = 0.045
ADMIN_PAGE_SIZE = 8
RESERVED_COMMANDS = {"start", "cancel", "help", "id"}
IRAN_TZ = timezone(timedelta(hours=3, minutes=30))

if not PLATFORM_BOT_TOKEN:
    log.critical("PLATFORM_BOT_TOKEN تنظیم نشده!")
    sys.exit(1)


# ══════════════════════════════════════════════════════════════════════
# توابع کمکی عمومی
# ══════════════════════════════════════════════════════════════════════
def esc(value) -> str:
    return html_lib.escape(str(value), quote=False)


def now_ir() -> datetime:
    return datetime.now(IRAN_TZ)


def today_str() -> str:
    return now_ir().strftime("%Y-%m-%d")


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def fmt_timedelta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d} روز")
    if h or d: parts.append(f"{h} ساعت")
    parts.append(f"{m} دقیقه")
    return " و ".join(parts)


def to_chat_id(channel: str):
    """تبدیل امن شناسه‌ی کانال: یوزرنیم (@x) دست‌نخورده می‌مونه، آیدی عددی
    (چه با -100 چه بدون) به int واقعی تبدیل می‌شه — تلگرام برای
    get_chat_member/get_chat فقط int یا @username قبول می‌کنه، رشته‌ی
    عددی رو رد می‌کنه و این باعث می‌شد قبلاً چک عضویت بی‌سروصدا خراب بشه."""
    c = str(channel).strip()
    if c.startswith("@"):
        return c
    if c.lstrip("-").isdigit():
        return int(c)
    return c


# ══════════════════════════════════════════════════════════════════════
# 💎 پلن‌های اشتراک (Starter / Professional / Enterprise)
# ══════════════════════════════════════════════════════════════════════
PLANS: dict[str, dict] = {
    "starter": {
        "order": 0, "label": "پایه", "badge": "🌱", "price": 0,
        "max_bots": 1, "max_commands": 5, "max_callbacks": 8,
        "broadcast": False, "scheduled_broadcast": False,
        "custom_force_join": False, "media_welcome": False, "analytics": "ساده",
        "tagline": "برای شروع و آشنایی با پلتفرم — یک ربات با امکانات پایه.",
    },
    "professional": {
        "order": 1, "label": "حرفه‌ای", "badge": "🚀", "price": 1200,
        "max_bots": 3, "max_commands": 30, "max_callbacks": 40,
        "broadcast": True, "scheduled_broadcast": True,
        "custom_force_join": True, "media_welcome": True, "analytics": "پیشرفته",
        "tagline": "ابزار کامل برای کسب‌وکارهای در حال رشد — پیام همگانی، زمان‌بندی و عضویت اجباری.",
    },
    "enterprise": {
        "order": 2, "label": "سازمانی", "badge": "💎", "price": 4500,
        "max_bots": 999, "max_commands": 999, "max_callbacks": 999,
        "broadcast": True, "scheduled_broadcast": True,
        "custom_force_join": True, "media_welcome": True,
        "analytics": "حرفه‌ای + پشتیبانی اختصاصی",
        "tagline": "راهکار نامحدود برای کسب‌وکارهای بزرگ — بدون سقف، با پشتیبانی اولویت‌دار.",
    },
}
PLAN_ORDER = sorted(PLANS.keys(), key=lambda p: PLANS[p]["order"])


def plan_rank(plan_key: str) -> int:
    return PLANS.get(plan_key, PLANS["free"])["order"]


def fmt_limit(n: int) -> str:
    return "نامحدود ♾" if n >= 900 else str(n)


def progress_bar(current: int, maximum: int, length: int = 10) -> str:
    if maximum >= 900:
        return f"♾ نامحدود  ({current} استفاده‌شده)"
    maximum = max(maximum, 1)
    filled = int(length * min(current, maximum) / maximum)
    return "▰" * filled + "▱" * (length - filled) + f"  {current}/{maximum}"


# ══════════════════════════════════════════════════════════════════════
# لایه ذخیره‌سازی (Storage)
# ══════════════════════════════════════════════════════════════════════
class Storage:
    """دیتای زنده در حافظه + سینک اختیاری با MongoDB."""

    def __init__(self, mongo_uri: str = MONGODB_URI):
        self._col = None
        self._lock = asyncio.Lock()
        self._dirty = False
        self.data: dict = {"users": {}, "bots": {}}
        if mongo_uri and MOTOR_AVAILABLE:
            client = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=10000)
            self._col = client.get_default_database()["gramsaz_data"]

    async def load(self):
        if self._col is None:
            return
        doc = await self._col.find_one({"_id": "main"})
        if not doc:
            return
        doc.pop("_id", None)
        async with self._lock:
            self.data.update(doc)
            self.data.setdefault("users", {})
            self.data.setdefault("bots", {})
            for u in self.data["users"].values():
                self._user_defaults(u)
            for b in self.data["bots"].values():
                self._bot_defaults(b)

    @staticmethod
    def _user_defaults(u: dict):
        u.setdefault("signup_bonus_claimed", False)
        u.setdefault("plan", "starter")
        u.setdefault("referred_by", None)
        u.setdefault("referral_count", 0)
        u.setdefault("referral_earned_coins", 0)
        u.setdefault("downline_bonus_date", None)
        u.setdefault("downline_bonus_count", 0)
        u.setdefault("is_banned", False)

    @staticmethod
    def _bot_defaults(b: dict):
        b.setdefault("start_msg", "👋 سلام! به ربات ما خوش آمدید.")
        b.setdefault("start_buttons", None)
        b.setdefault("start_photo", None)
        b.setdefault("commands", {})
        b.setdefault("callbacks", {})
        b.setdefault("force_join_channel", None)
        b.setdefault("bot_users", [])
        b.setdefault("stats", {"messages_received": 0, "messages_sent": 0, "unique_users": 0, "broadcasts_sent": 0})
        b.setdefault("daily_stats", {})

    async def save(self, force: bool = False):
        if self._col is None or (not force and not self._dirty):
            return
        async with self._lock:
            payload = {"_id": "main", **self.data, "last_updated": now_ir().isoformat()}
            self._dirty = False
        await self._col.replace_one({"_id": "main"}, payload, upsert=True)

    def mark_dirty(self):
        self._dirty = True

    async def autosave_loop(self):
        while True:
            await asyncio.sleep(300)
            await self.save()

    # ---- کاربران ----
    async def ensure_user(self, uid: int, username: str = "", full_name: str = "",
                           ref_code: Optional[str] = None) -> tuple[dict, bool, Optional[int]]:
        key = str(uid)
        is_new = False
        referrer_uid: Optional[int] = None
        async with self._lock:
            if key not in self.data["users"]:
                is_new = True
                my_code = hashlib.md5(f"{uid}{time.time()}".encode()).hexdigest()[:8]
                if ref_code:
                    for other_key, u in self.data["users"].items():
                        if u.get("referral_code") == ref_code and int(other_key) != uid:
                            referrer_uid = int(other_key)
                            break
                self.data["users"][key] = {
                    "uid": uid, "username": username, "full_name": full_name,
                    "coin_balance": 0, "referral_code": my_code, "referred_by": referrer_uid,
                    "created_at": now_ir().isoformat(), "bots_created": [],
                    "signup_bonus_claimed": False, "plan": "starter",
                    "referral_count": 0, "referral_earned_coins": 0,
                    "downline_bonus_date": None, "downline_bonus_count": 0, "is_banned": False,
                }
                if referrer_uid is not None:
                    ref = self.data["users"][str(referrer_uid)]
                    ref["coin_balance"] += REFERRAL_BONUS_REFERRER
                    ref["referral_count"] += 1
                    ref["referral_earned_coins"] += REFERRAL_BONUS_REFERRER
                self.mark_dirty()
            else:
                u = self.data["users"][key]
                self._user_defaults(u)
                if username and u.get("username") != username:
                    u["username"] = username; self.mark_dirty()
                if full_name and u.get("full_name") != full_name:
                    u["full_name"] = full_name; self.mark_dirty()
        return dict(self.data["users"][key]), is_new, referrer_uid

    def get_user(self, uid: int) -> Optional[dict]:
        u = self.data["users"].get(str(uid))
        return dict(u) if u else None

    def get_plan(self, uid: int) -> dict:
        u = self.get_user(uid) or {}
        return PLANS.get(u.get("plan", "starter"), PLANS["starter"])

    async def claim_signup_bonus(self, uid: int) -> int:
        key = str(uid)
        async with self._lock:
            u = self.data["users"].get(key)
            if not u or u.get("signup_bonus_claimed"):
                return 0
            u["signup_bonus_claimed"] = True
            bonus = SIGNUP_BONUS + (REFERRAL_BONUS_NEWCOMER if u.get("referred_by") else 0)
            u["coin_balance"] += bonus
            self.mark_dirty()
        return bonus

    async def upgrade_plan(self, uid: int, target: str) -> tuple[bool, str]:
        if target not in PLANS:
            return False, "پلن نامعتبر."
        async with self._lock:
            u = self.data["users"].get(str(uid))
            if not u:
                return False, "کاربر یافت نشد."
            cur = u.get("plan", "starter")
            if plan_rank(target) <= plan_rank(cur):
                return False, "شما همین الان این پلن یا بالاتر را دارید."
            cost = PLANS[target]["price"] - PLANS[cur]["price"]
            if u.get("coin_balance", 0) < cost:
                return False, f"موجودی کافی نیست — {cost - u.get('coin_balance', 0)} {COIN_SYMBOL} دیگر لازم دارید."
            u["coin_balance"] -= cost
            u["plan"] = target
            self.mark_dirty()
        return True, f"پلن شما به {PLANS[target]['label']} ارتقا یافت!"

    async def admin_set_plan(self, uid: int, target: str) -> bool:
        if target not in PLANS:
            return False
        async with self._lock:
            u = self.data["users"].get(str(uid))
            if not u:
                return False
            u["plan"] = target
            self.mark_dirty()
        return True

    async def admin_grant_coins(self, uid: int, amount: int) -> Optional[int]:
        async with self._lock:
            u = self.data["users"].get(str(uid))
            if not u:
                return None
            u["coin_balance"] = max(0, u.get("coin_balance", 0) + amount)
            self.mark_dirty()
            return u["coin_balance"]

    async def set_banned(self, uid: int, banned: bool) -> bool:
        async with self._lock:
            u = self.data["users"].get(str(uid))
            if not u:
                return False
            u["is_banned"] = banned
            self.mark_dirty()
            return True

    def get_referral_leaderboard(self, top: int = 10) -> list[dict]:
        users = sorted(self.data["users"].values(), key=lambda u: u.get("referral_count", 0), reverse=True)
        return [dict(u) for u in users[:top] if u.get("referral_count", 0) > 0]

    def get_all_user_ids(self) -> list[int]:
        return [int(k) for k in self.data["users"].keys()]

    # ---- ربات‌ها ----
    async def create_bot(self, owner_uid: int, bot_token: str) -> dict:
        bot_id = hashlib.md5(f"{owner_uid}{bot_token}{time.time()}".encode()).hexdigest()[:12]
        async with self._lock:
            bot_rec = {
                "bot_id": bot_id, "owner_uid": owner_uid, "bot_token": bot_token,
                "bot_username": "", "is_active": True, "created_at": now_ir().isoformat(),
                "last_activity": None,
            }
            self._bot_defaults(bot_rec)
            self.data["bots"][bot_id] = bot_rec
            key = str(owner_uid)
            if key in self.data["users"]:
                self.data["users"][key]["bots_created"].append(bot_id)
            self.mark_dirty()
        return dict(self.data["bots"][bot_id])

    def get_bot(self, bot_id: str) -> Optional[dict]:
        b = self.data["bots"].get(bot_id)
        return dict(b) if b else None

    def get_user_bots(self, uid: int) -> list[dict]:
        return [dict(b) for b in self.data["bots"].values() if b.get("owner_uid") == uid]

    def get_all_bots(self) -> list[dict]:
        return [dict(v) for v in self.data["bots"].values()]

    async def update_bot_username(self, bot_id: str, username: str):
        async with self._lock:
            if bot_id in self.data["bots"]:
                self.data["bots"][bot_id]["bot_username"] = username
                self.mark_dirty()

    async def delete_bot(self, bot_id: str, owner_uid: int) -> bool:
        async with self._lock:
            info = self.data["bots"].pop(bot_id, None)
            if not info:
                return False
            u = self.data["users"].get(str(owner_uid))
            if u and bot_id in u.get("bots_created", []):
                u["bots_created"].remove(bot_id)
            self.mark_dirty()
        return True

    async def set_bot_active(self, bot_id: str, active: bool):
        async with self._lock:
            if bot_id in self.data["bots"]:
                self.data["bots"][bot_id]["is_active"] = active
                self.mark_dirty()

    def _bump_daily(self, b: dict, field: str, n: int = 1):
        day = today_str()
        d = b.setdefault("daily_stats", {})
        d.setdefault(day, {"received": 0, "sent": 0, "new_users": 0})
        d[day][field] = d[day].get(field, 0) + n
        if len(d) > 14:
            for old in sorted(d.keys())[:-14]:
                d.pop(old, None)

    async def update_bot_stats(self, bot_id: str, received: int = 0, sent: int = 0):
        async with self._lock:
            b = self.data["bots"].get(bot_id)
            if not b:
                return
            b["stats"]["messages_received"] += received
            b["stats"]["messages_sent"] += sent
            b["last_activity"] = now_ir().isoformat()
            if received: self._bump_daily(b, "received", received)
            if sent: self._bump_daily(b, "sent", sent)
            self.mark_dirty()

    async def add_bot_user(self, bot_id: str, uid: int) -> bool:
        is_new = False
        async with self._lock:
            b = self.data["bots"].get(bot_id)
            if not b:
                return False
            users = b.setdefault("bot_users", [])
            if uid not in users:
                is_new = True
                users.append(uid)
                b["stats"]["unique_users"] = len(users)
                self._bump_daily(b, "new_users", 1)
                owner = self.data["users"].get(str(b["owner_uid"]))
                if owner:
                    day = today_str()
                    if owner.get("downline_bonus_date") != day:
                        owner["downline_bonus_date"] = day
                        owner["downline_bonus_count"] = 0
                    if owner["downline_bonus_count"] < DOWNLINE_DAILY_CAP:
                        owner["coin_balance"] = owner.get("coin_balance", 0) + DOWNLINE_BONUS_PER_USER
                        owner["downline_bonus_count"] += 1
                self.mark_dirty()
        return is_new

    # ---- محتوای ربات: پیام شروع / دستورها / کال‌بک‌ها ----
    async def set_start_msg(self, bot_id: str, text: str):
        await self._patch_bot(bot_id, start_msg=text)

    async def set_start_buttons(self, bot_id: str, rows: Optional[list]):
        await self._patch_bot(bot_id, start_buttons=rows)

    async def set_start_photo(self, bot_id: str, file_id: Optional[str]):
        await self._patch_bot(bot_id, start_photo=file_id)

    async def _patch_bot(self, bot_id: str, **fields):
        async with self._lock:
            if bot_id in self.data["bots"]:
                self.data["bots"][bot_id].update(fields)
                self.mark_dirty()

    async def set_command(self, bot_id: str, name: str, response: str, buttons: Optional[list] = None):
        async with self._lock:
            b = self.data["bots"].get(bot_id)
            if b is not None:
                b.setdefault("commands", {})[name] = {"response": response, "buttons": buttons}
                self.mark_dirty()

    async def delete_command(self, bot_id: str, name: str):
        async with self._lock:
            b = self.data["bots"].get(bot_id)
            if b is not None:
                b.get("commands", {}).pop(name, None)
                self.mark_dirty()

    def get_commands(self, bot_id: str) -> dict:
        b = self.data["bots"].get(bot_id)
        return dict(b.get("commands", {})) if b else {}

    async def set_callback(self, bot_id: str, cb_id: str, response: str):
        async with self._lock:
            b = self.data["bots"].get(bot_id)
            if b is not None:
                b.setdefault("callbacks", {})[cb_id] = response
                self.mark_dirty()

    async def delete_callback(self, bot_id: str, cb_id: str):
        async with self._lock:
            b = self.data["bots"].get(bot_id)
            if b is not None:
                b.get("callbacks", {}).pop(cb_id, None)
                self.mark_dirty()

    def get_callbacks(self, bot_id: str) -> dict:
        b = self.data["bots"].get(bot_id)
        return dict(b.get("callbacks", {})) if b else {}

    async def set_bot_force_join(self, bot_id: str, channel: Optional[str]):
        await self._patch_bot(bot_id, force_join_channel=channel)

    async def record_broadcast(self, bot_id: str, sent: int):
        async with self._lock:
            b = self.data["bots"].get(bot_id)
            if b:
                b["stats"]["broadcasts_sent"] += 1
                b["stats"]["messages_sent"] += sent
                self.mark_dirty()

    def get_platform_stats(self) -> dict:
        plan_counts: dict[str, int] = {p: 0 for p in PLANS}
        total_coins = 0
        for u in self.data["users"].values():
            plan_counts[u.get("plan", "starter")] = plan_counts.get(u.get("plan", "starter"), 0) + 1
            total_coins += u.get("coin_balance", 0)
        bots = self.data["bots"]
        return {
            "total_users": len(self.data["users"]), "total_bots": len(bots),
            "active_bots": sum(1 for b in bots.values() if b.get("is_active")),
            "plan_counts": plan_counts, "total_coins": total_coins,
        }


storage = Storage()


# ══════════════════════════════════════════════════════════════════════
# BotFactory — مدیریت نمونه‌های Application ساب‌بات‌ها
# ══════════════════════════════════════════════════════════════════════
class BotFactory:
    def __init__(self, storage: Storage):
        self.storage = storage
        self._running: dict[str, asyncio.Task] = {}
        self._apps: dict[str, Application] = {}
        self._lock = asyncio.Lock()

    async def start_bot(self, bot_id: str, bot_token: str):
        async with self._lock:
            if bot_id in self._running:
                return
            try:
                app = Application.builder().token(bot_token).build()
                app.add_handler(CommandHandler("start", lambda u, c: handle_subbot_start(u, c, bot_id)))
                app.add_handler(CallbackQueryHandler(lambda u, c: handle_subbot_callback(u, c, bot_id)))
                app.add_handler(MessageHandler(filters.TEXT & filters.COMMAND,
                                                lambda u, c: handle_subbot_command(u, c, bot_id)))
                await app.initialize()
                me = await app.bot.get_me()
                await self.storage.update_bot_username(bot_id, me.username or "")
                self._running[bot_id] = asyncio.create_task(self._run(app, bot_id))
                self._apps[bot_id] = app
                log.info(f"🟢 ساب‌بات @{me.username} ({bot_id}) استارت شد")
            except InvalidToken:
                log.error(f"❌ توکن نامعتبر برای ربات {bot_id}")
            except Exception as e:
                log.error(f"❌ استارت ساب‌بات {bot_id} ناموفق: {e}")

    async def stop_bot(self, bot_id: str):
        async with self._lock:
            task = self._running.pop(bot_id, None)
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            app = self._apps.pop(bot_id, None)
            if app:
                try:
                    await app.shutdown()
                except Exception:
                    pass

    async def restart_bot(self, bot_id: str):
        bot_data = self.storage.get_bot(bot_id)
        if not bot_data:
            return
        await self.stop_bot(bot_id)
        if bot_data.get("is_active"):
            await asyncio.sleep(0.5)
            await self.start_bot(bot_id, bot_data["bot_token"])

    async def _run(self, app: Application, bot_id: str):
        try:
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            while True:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error(f"ساب‌بات {bot_id} با خطا متوقف شد: {e}")
        finally:
            try:
                await app.updater.stop()
                await app.stop()
            except Exception:
                pass

    def get_bot_instance(self, bot_id: str) -> Optional[Bot]:
        app = self._apps.get(bot_id)
        return app.bot if app else None

    async def start_all_active(self):
        for b in self.storage.get_all_bots():
            if b.get("is_active"):
                await self.start_bot(b["bot_id"], b["bot_token"])

    async def stop_all(self):
        for bot_id in list(self._running.keys()):
            await self.stop_bot(bot_id)


factory = BotFactory(storage)
mother_bot_ref: dict = {}


# ══════════════════════════════════════════════════════════════════════
# 🚫 عضویت اجباری — پلتفرم و اختصاصی هر ربات
# ══════════════════════════════════════════════════════════════════════
_invite_link_cache: dict[str, tuple[str, float]] = {}
_INVITE_TTL_SEC = 3600
force_join_broken = False  # پرچم سراسری برای هشدار در پنل ادمین


async def get_join_url(bot: Bot, channel: str) -> str:
    if channel.startswith("@"):
        return f"https://t.me/{channel.lstrip('@')}"
    chat_id = to_chat_id(channel)
    cached = _invite_link_cache.get(channel)
    now = time.time()
    if cached and now - cached[1] < _INVITE_TTL_SEC:
        return cached[0]
    link = None
    try:
        chat = await bot.get_chat(chat_id)
        link = chat.invite_link
    except Exception as e:
        log.warning(f"[ForceJoin] get_chat برای {channel} ناموفق: {e}")
    if not link:
        try:
            link = (await bot.create_chat_invite_link(chat_id=chat_id, creates_join_request=False)).invite_link
        except Exception as e:
            log.error(f"[ForceJoin] ساخت لینک دعوت برای {channel} ناموفق: {e}")
            link = f"https://t.me/c/{str(channel).replace('-100', '')}"
    _invite_link_cache[channel] = (link, now)
    return link


async def check_membership_one(bot: Bot, channel: str, uid: int) -> Optional[bool]:
    """True=عضو، False=قطعاً عضو نیست، None=خطای فنی (این حالت دیگه هیچ‌وقت
    بی‌سروصدا به‌عنوان «عضو است» تفسیر نمی‌شه)."""
    try:
        member = await bot.get_chat_member(to_chat_id(channel), uid)
        return member.status not in ("left", "kicked")
    except Exception as e:
        log.warning(f"[ForceJoin] بررسی عضویت {channel} برای {uid} ناموفق: {e}")
        return None


async def enforce_force_join(bot: Bot, uid: int) -> bool:
    global force_join_broken
    if not FORCE_JOIN_CHANNELS:
        return True
    any_error = False
    for ch in FORCE_JOIN_CHANNELS:
        result = await check_membership_one(bot, ch, uid)
        if result is False:
            force_join_broken = any_error
            return False
        if result is None:
            any_error = True
    force_join_broken = any_error
    return True


async def force_join_kb(bot: Bot) -> InlineKeyboardMarkup:
    rows = []
    for ch in FORCE_JOIN_CHANNELS:
        url = await get_join_url(bot, ch)
        label = ch if ch.startswith("@") else "کانال عضویت اجباری"
        rows.append([InlineKeyboardButton(f"📢 عضویت در {label}", url=url)])
    rows.append([InlineKeyboardButton("✅ عضو شدم، بررسی کن", callback_data="fj:check")])
    return InlineKeyboardMarkup(rows)


FORCE_JOIN_TEXT = (
    "🚫 <b>یک قدم مونده!</b>\n\n"
    "برای استفاده از GramSaz، اول باید عضو کانال(های) زیر بشی.\n"
    "بعدش روی «✅ عضو شدم، بررسی کن» بزن."
)


async def enforce_subbot_force_join(bot: Bot, uid: int, channel: Optional[str]) -> bool:
    if not channel:
        return True
    return await check_membership_one(bot, channel, uid) is not False


async def subbot_force_join_kb(bot: Bot, bot_id: str, channel: str) -> InlineKeyboardMarkup:
    url = await get_join_url(bot, channel)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url=url)],
        [InlineKeyboardButton("✅ عضو شدم، بررسی کن", callback_data=f"sfj:check:{bot_id}")],
    ])


# ══════════════════════════════════════════════════════════════════════
# UI — کیبوردها
# ══════════════════════════════════════════════════════════════════════
PLATFORM_USERNAME = ""


def parse_button_rows(raw_text: str) -> list:
    rows = []
    for line in raw_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        row = []
        for part in line.split(","):
            part = part.strip()
            if "|" not in part:
                raise ValueError(f"فرمت نادرست: «{part}» — باید «متن|شناسه» باشه")
            btext, bid = (p.strip() for p in part.rsplit("|", 1))
            if not bid or not btext:
                raise ValueError(f"فرمت نادرست: «{part}»")
            row.append({"id": bid, "text": btext})
        if row:
            rows.append(row)
    return rows


def build_inline_from_rows(rows: Optional[list]) -> Optional[InlineKeyboardMarkup]:
    if not rows:
        return None
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(btn["text"], callback_data=btn["id"]) for btn in row] for row in rows
    ])


def preview_buttons(rows: Optional[list]) -> str:
    if not rows:
        return "ندارد"
    return "، ".join(btn["text"] for row in rows for btn in row) or "ندارد"


def main_menu_kb(uid: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🤖 ربات‌های من", callback_data="nav:mybots"),
         InlineKeyboardButton("➕ ساخت ربات", callback_data="nav:create")],
        [InlineKeyboardButton("💎 پلن‌ها و ارتقا", callback_data="nav:plans"),
         InlineKeyboardButton("👤 حساب من", callback_data="nav:account")],
        [InlineKeyboardButton("🔗 دعوت دوستان", callback_data="nav:referral"),
         InlineKeyboardButton("📖 راهنما", callback_data="nav:help")],
    ]
    if is_admin(uid):
        rows.append([InlineKeyboardButton("🛠 پنل مدیریت", callback_data="admin:panel")])
    return InlineKeyboardMarkup(rows)


def back_kb(dest: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=dest)]])


def cancel_kb(dest: str = "nav:home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data=dest)]])


def confirm_kb(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ بله، مطمئنم", callback_data=yes_cb),
        InlineKeyboardButton("❌ انصراف", callback_data=no_cb),
    ]])


# ══════════════════════════════════════════════════════════════════════
# UI — متن‌ها (لحن فروشنده و جذاب، مشابه استایل نمونه)
# ══════════════════════════════════════════════════════════════════════
def build_welcome_text(user: dict, bonus_given: int) -> str:
    plan = PLANS.get(user.get("plan", "free"), PLANS["free"])
    bonus_line = f"\n🎁 <b>{bonus_given} {COIN_SYMBOL}</b> هدیهٔ خوش‌آمدگویی به کیفتون اضافه شد!\n" if bonus_given else ""
    return (
        "🤖 <b>به GramSaz خوش اومدی!</b>\n"
        f"{bonus_line}"
        "<blockquote expandable>"
        "بدون یک خط کدنویسی، در چند دقیقه یک ربات تلگرام کامل می‌سازی:\n"
        "• دستور دلخواه و دکمه‌های شیشه‌ای اختصاصی\n"
        "• خوش‌آمدگویی با متن و عکس\n"
        "• پیام همگانی برای اطلاع‌رسانی سریع به کاربرات\n"
        "• آنالیتیکس روزانه برای رصد رشد ربات\n"
        "هیچ رباتی هم به‌خاطر گذر زمان از کار نمی‌افته — فقط سقف پلن‌تون محدودتون می‌کنه."
        "</blockquote>\n"
        f"پلن فعلی شما: <b>{plan['label']}</b>\n\n"
        "👇 از منوی زیر شروع کن:"
    )


def build_dashboard_text(uid: int, user: dict) -> str:
    plan = PLANS.get(user.get("plan", "free"), PLANS["free"])
    bots = storage.get_user_bots(uid)
    ref_link = f"https://t.me/{PLATFORM_USERNAME}?start={user.get('referral_code', '')}" if PLATFORM_USERNAME else "—"
    return (
        "👤 <b>پنل حساب من</b>\n\n"
        f"🆔 آیدی: <code>{user['uid']}</code>\n"
        f"{plan['badge']} پلن فعلی: <b>{plan['label']}</b>\n"
        f"💰 موجودی: <b>{user.get('coin_balance', 0)} {COIN_SYMBOL}</b>\n\n"
        f"🤖 <b>مصرف ربات‌ها</b>\n{progress_bar(len(bots), plan['max_bots'])}\n\n"
        "🔗 <b>دعوت و درآمد</b>\n"
        f"کد دعوت: <code>{user.get('referral_code', '—')}</code>\n"
        f"لینک اختصاصی: {ref_link}\n"
        f"👥 {user.get('referral_count', 0)} نفر دعوت‌شده "
        f"(+{user.get('referral_earned_coins', 0)} {COIN_SYMBOL} تا این لحظه)"
    )


def _feature_line(icon: str, label: str, enabled: bool) -> str:
    return f"{icon} {label}: {'✅ فعال' if enabled else '⛔️ قفل'}"


def build_plan_card_text(plan_key: str, user: dict) -> str:
    d = PLANS[plan_key]
    cur = user.get("plan", "free")
    dots = "".join("◉" if p == cur else ("●" if p == plan_key else "○") for p in PLAN_ORDER)
    status_line = ("🟢 <b>پلن فعلی شماست</b>" if plan_key == cur else
                   "🔒 پایین‌تر از پلن فعلی شماست" if plan_rank(plan_key) < plan_rank(cur) else "⬆️ قابل ارتقا")
    price_line = "رایگان برای همیشه 🎉" if d["price"] == 0 else f"<b>{d['price']} {COIN_SYMBOL}</b>"
    return (
        f"{d['badge']}  <b>{d['label']}</b>\n{dots}\n\n"
        f"<blockquote>{d['tagline']}</blockquote>\n"
        f"💳 قیمت: {price_line}\n{status_line}\n\n"
        f"🤖 ربات هم‌زمان: <b>{fmt_limit(d['max_bots'])}</b>\n"
        f"🔧 دستور دلخواه: <b>{fmt_limit(d['max_commands'])}</b>\n"
        f"⚡ دکمهٔ سراسری: <b>{fmt_limit(d['max_callbacks'])}</b>\n\n"
        f"{_feature_line('📢', 'پیام همگانی', d['broadcast'])}\n"
        f"{_feature_line('⏰', 'زمان‌بندی پیام همگانی', d['scheduled_broadcast'])}\n"
        f"{_feature_line('🚫', 'عضویت اجباری اختصاصی هر ربات', d['custom_force_join'])}\n"
        f"{_feature_line('🖼', 'خوش‌آمد با عکس', d['media_welcome'])}\n"
        f"📊 آنالیتیکس: <b>{d['analytics']}</b>\n\n"
        f"💰 موجودی شما: <b>{user.get('coin_balance', 0)} {COIN_SYMBOL}</b>"
    )


def plan_card_kb(plan_key: str, user: dict) -> InlineKeyboardMarkup:
    cur = user.get("plan", "free")
    idx = PLAN_ORDER.index(plan_key)
    nav_row = []
    if idx > 0:
        nav_row.append(InlineKeyboardButton("◀️ ارزان‌تر", callback_data=f"plan:view:{PLAN_ORDER[idx - 1]}"))
    if idx < len(PLAN_ORDER) - 1:
        nav_row.append(InlineKeyboardButton("گران‌تر ▶️", callback_data=f"plan:view:{PLAN_ORDER[idx + 1]}"))
    rows = [nav_row] if nav_row else []
    if plan_rank(plan_key) > plan_rank(cur):
        cost = PLANS[plan_key]["price"] - PLANS[cur]["price"]
        rows.append([InlineKeyboardButton(f"🚀 ارتقا به {PLANS[plan_key]['label']} — {cost} {COIN_SYMBOL}",
                                           callback_data=f"plan:upgrade:{plan_key}")])
    elif plan_key == cur:
        rows.append([InlineKeyboardButton("✅ پلن فعلی شماست", callback_data="noop")])
    rows.append([InlineKeyboardButton("🔙 منوی اصلی", callback_data="nav:home")])
    return InlineKeyboardMarkup(rows)


def build_referral_text(user: dict) -> str:
    ref_link = f"https://t.me/{PLATFORM_USERNAME}?start={user.get('referral_code', '')}" if PLATFORM_USERNAME else "—"
    board = storage.get_referral_leaderboard(10)
    medals = ["🥇", "🥈", "🥉"]
    lines = [
        "🔗 <b>دعوت دوستان و کسب کوین</b>\n",
        "<blockquote expandable>"
        f"با هر دعوت موفق <b>{REFERRAL_BONUS_REFERRER} {COIN_SYMBOL}</b> می‌گیری و دوستت "
        f"<b>{REFERRAL_BONUS_NEWCOMER} {COIN_SYMBOL}</b> پاداش خوش‌آمد می‌گیره.\n"
        f"🎯 به‌ازای هر کاربر یکتای جدید در هر کدوم از ربات‌هات، "
        f"<b>{DOWNLINE_BONUS_PER_USER} {COIN_SYMBOL}</b> بیشتر (سقف {DOWNLINE_DAILY_CAP} در روز)."
        "</blockquote>\n",
        f"🔗 لینک اختصاصی شما:\n{ref_link}\n",
        f"👥 تا این لحظه <b>{user.get('referral_count', 0)}</b> نفر با لینک شما پیوستن.",
    ]
    if board:
        lines.append("\n🏆 <b>لیدربورد برترین دعوت‌کننده‌ها</b>")
        for i, u in enumerate(board, 1):
            name = esc(u.get("username") or u.get("full_name") or str(u["uid"]))
            rank = medals[i - 1] if i <= 3 else f"{i}."
            lines.append(f"{rank} {name} — {u.get('referral_count', 0)} دعوت")
    return "\n".join(lines)


HELP_TEXT = (
    "📖 <b>راهنمای GramSaz</b>\n\n"
    "🚀 <b>۱. ساخت اولین ربات</b>\n"
    "<blockquote expandable>"
    "روی «➕ ساخت ربات» بزن، به @BotFather برو، با /newbot یک ربات تازه بساز "
    "و توکنی که می‌ده رو همین‌جا بفرست. تمام — ربات‌ت بالاست."
    "</blockquote>\n"
    "⚙️ <b>۲. مدیریت هر ربات</b>\n"
    "<blockquote expandable>"
    "از «🤖 ربات‌های من» → «⚙️ مدیریت» به همه‌چیز دسترسی داری:\n"
    "• پیام /start (متن + دکمه + در پلن‌های بالاتر عکس)\n"
    "• دستور دلخواه مثل /info با پاسخ و دکمهٔ اختصاصی\n"
    "• پاسخ اختصاصی برای هر دکمه\n"
    "• پیام همگانی به کاربرهای همون ربات (پلن حرفه‌ای به بالا)\n"
    "• عضویت اجباری مخصوص همون ربات (پلن حرفه‌ای به بالا)"
    "</blockquote>\n"
    "⏳ <b>۳. بدون محدودیت زمانی</b>\n"
    "<blockquote expandable>"
    "هیچ رباتی به‌خاطر گذر زمان خاموش نمی‌شه؛ فقط سقف تعداد ربات/دستور/دکمهٔ "
    "پلن‌تون محدودتون می‌کنه — هر وقت خواستی ارتقا بده."
    "</blockquote>\n"
    "💎 <b>۴. کوین و ارتقای پلن</b>\n"
    "<blockquote expandable>"
    "با دعوت دوستان و رشد ربات‌هات کوین جمع می‌کنی و از «💎 پلن‌ها و ارتقا» "
    "خرج ارتقا می‌کنی — کارت‌های پلن رو با دکمه‌های ◀️ ▶️ ورق بزن."
    "</blockquote>"
)


def build_bot_detail(bot: dict) -> tuple[str, InlineKeyboardMarkup]:
    bot_id = bot["bot_id"]
    owner_plan = storage.get_plan(bot["owner_uid"])
    stats = bot.get("stats", {})
    fj = bot.get("force_join_channel")
    status_badge = "🟢 روشن" if bot.get("is_active") else "🔴 خاموش"
    text = (
        f"⚙️ <b>مدیریت @{esc(bot.get('bot_username', '—'))}</b>  ·  {status_badge}\n\n"
        f"🔧 دستورها\n{progress_bar(len(bot.get('commands', {})), owner_plan['max_commands'])}\n"
        f"⚡ دکمه‌های سراسری\n{progress_bar(len(bot.get('callbacks', {})), owner_plan['max_callbacks'])}\n\n"
        f"👥 کاربران: <b>{len(bot.get('bot_users', []))}</b>   "
        f"📩 دریافتی: {stats.get('messages_received', 0)}   "
        f"📤 ارسالی: {stats.get('messages_sent', 0)}\n"
        f"🚫 عضویت اجباری اختصاصی: <b>{esc(fj) if fj else 'غیرفعال'}</b>"
    )
    toggle_label = "🔴 خاموش کردن" if bot.get("is_active") else "🟢 روشن کردن"
    rows = [
        [InlineKeyboardButton("📝 پیام شروع (/start)", callback_data=f"bot:start:{bot_id}")],
        [InlineKeyboardButton("🔧 دستورهای دلخواه", callback_data=f"bot:commands:{bot_id}"),
         InlineKeyboardButton("⚡ پاسخ دکمه‌ها", callback_data=f"bot:callbacks:{bot_id}")],
        [InlineKeyboardButton("📢 پیام همگانی", callback_data=f"bot:bc_menu:{bot_id}"),
         InlineKeyboardButton("📊 آنالیتیکس", callback_data=f"bot:stats:{bot_id}")],
        [InlineKeyboardButton("🚫 عضویت اجباری اختصاصی", callback_data=f"bot:fj_menu:{bot_id}")],
        [InlineKeyboardButton("💾 پشتیبان‌گیری (Export)", callback_data=f"bot:export:{bot_id}")],
        [InlineKeyboardButton(toggle_label, callback_data=f"bot:toggle:{bot_id}")],
        [InlineKeyboardButton("🗑 حذف ربات", callback_data=f"bot:delete:{bot_id}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="nav:mybots")],
    ]
    return text, InlineKeyboardMarkup(rows)


def build_mybots_text_kb(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    bots = storage.get_user_bots(uid)
    plan = storage.get_plan(uid)
    if not bots:
        return ("🤖 <b>ربات‌های شما</b>\n\nهنوز رباتی نساختی. همین الان اولین ربات‌ت رو بساز 👇",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ ساخت ربات جدید", callback_data="nav:create")],
                    [InlineKeyboardButton("🔙 منوی اصلی", callback_data="nav:home")],
                ]))
    text = f"🤖 <b>ربات‌های شما</b>  ({progress_bar(len(bots), plan['max_bots'])})\n\n"
    rows = []
    for b in bots:
        badge = "🟢" if b.get("is_active") else "🔴"
        uname = f"@{b['bot_username']}" if b.get("bot_username") else "—"
        text += f"{badge} {uname}\n"
        rows.append([InlineKeyboardButton(f"⚙️ مدیریت {uname}", callback_data=f"bot:manage:{b['bot_id']}")])
    if len(bots) < plan["max_bots"]:
        rows.append([InlineKeyboardButton("➕ ساخت ربات جدید", callback_data="nav:create")])
    rows.append([InlineKeyboardButton("🔙 منوی اصلی", callback_data="nav:home")])
    return text, InlineKeyboardMarkup(rows)


def build_stats_text(bot: dict, owner_plan: dict) -> str:
    stats = bot.get("stats", {})
    text = (
        f"📊 <b>آنالیتیکس @{bot.get('bot_username', '—')}</b>\n\n"
        f"📩 کل پیام دریافتی: {stats.get('messages_received', 0)}\n"
        f"📤 کل پیام ارسالی: {stats.get('messages_sent', 0)}\n"
        f"👥 کل کاربران یکتا: {stats.get('unique_users', 0)}\n"
        f"📢 پیام‌های همگانی ارسال‌شده: {stats.get('broadcasts_sent', 0)}\n"
    )
    if owner_plan.get("analytics") in ("پیشرفته", "حرفه‌ای + پشتیبانی ویژه"):
        daily = bot.get("daily_stats", {})
        if daily:
            text += "\n📈 <b>روند ۷ روز اخیر</b>\n"
            for day in sorted(daily.keys())[-7:]:
                d = daily[day]
                text += f"  {day} → دریافتی: {d.get('received', 0)}  |  کاربر جدید: {d.get('new_users', 0)}\n"
        else:
            text += "\n📈 هنوز داده‌ای برای نمودار روزانه ثبت نشده."
    else:
        text += "\n🔒 آنالیتیکس پیشرفته (روند روزانه) مخصوص پلن حرفه‌ای به بالاست."
    return text


def build_admin_panel_text() -> str:
    stats = storage.get_platform_stats()
    plan_lines = "\n".join(
        f"   {PLANS[p]['badge']} {PLANS[p]['label']}: {stats['plan_counts'].get(p, 0)} کاربر" for p in PLAN_ORDER)
    fj_status = ("✅ فعال روی: " + "، ".join(FORCE_JOIN_CHANNELS)) if FORCE_JOIN_CHANNELS else "❌ غیرفعال"
    warn = ("\n⚠️ هشدار: بررسی عضویت اجباری با خطای فنی مواجه شده (احتمالاً ربات ادمین کانال نیست) — چک کن!"
            if force_join_broken else "")
    return (
        "🛠 <b>پنل مدیریت</b>\n\n"
        f"👥 تعداد کاربران: {stats['total_users']}\n"
        f"🤖 تعداد کل ربات‌ها: {stats['total_bots']}\n"
        f"🟢 ربات‌های فعال: {stats['active_bots']}\n"
        f"🪙 مجموع کوین در گردش: {stats['total_coins']} {COIN_SYMBOL}\n\n"
        f"📊 <b>توزیع پلن‌ها</b>\n{plan_lines}\n\n"
        f"🚫 عضویت اجباری پلتفرم: {fj_status}{warn}"
    )


def admin_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 اعطای کوین", callback_data="admin:grantcoin"),
         InlineKeyboardButton("⭐ اعطای پلن", callback_data="admin:grantplan")],
        [InlineKeyboardButton("🚫 مسدودسازی کاربر", callback_data="admin:ban"),
         InlineKeyboardButton("✅ رفع مسدودی", callback_data="admin:unban")],
        [InlineKeyboardButton("📢 پیام همگانی پلتفرم", callback_data="admin:broadcast")],
        [InlineKeyboardButton("🔙 منوی اصلی", callback_data="nav:home")],
    ])


# ══════════════════════════════════════════════════════════════════════
# هندلرهای ربات مادر — نمایش منو و /start
# ══════════════════════════════════════════════════════════════════════
async def show_main_menu(msg_target, uid: int, edit: bool = False):
    user = storage.get_user(uid)
    bonus_given = 0
    if user and not user.get("signup_bonus_claimed"):
        bonus_given = await storage.claim_signup_bonus(uid)
        if bonus_given:
            user = storage.get_user(uid)
    text, kb = build_welcome_text(user, bonus_given), main_menu_kb(uid)
    if edit:
        try:
            await msg_target.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        except Exception:
            pass
    await msg_target.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ref_code = ctx.args[0].strip() if ctx.args else None
    _, is_new, referrer_uid = await storage.ensure_user(
        user.id, user.username or "", (user.full_name or "").strip(), ref_code)
    u = storage.get_user(user.id)
    if u and u.get("is_banned"):
        await update.effective_message.reply_text("🚫 حساب شما در این پلتفرم مسدود شده است.")
        return
    if is_new and referrer_uid:
        try:
            await ctx.bot.send_message(
                referrer_uid,
                f"🎉 یکی از دعوت‌شدگان شما به GramSaz پیوست!\n💰 {REFERRAL_BONUS_REFERRER} {COIN_SYMBOL} به موجودی شما اضافه شد.")
        except Exception:
            pass
    if not await enforce_force_join(ctx.bot, user.id):
        await update.effective_message.reply_text(FORCE_JOIN_TEXT, parse_mode=ParseMode.HTML,
                                                    reply_markup=await force_join_kb(ctx.bot))
        return
    ctx.user_data.clear()
    await show_main_menu(update.effective_message, user.id)


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.effective_message.reply_text("❌ لغو شد.")
    await show_main_menu(update.effective_message, update.effective_user.id)


# ══════════════════════════════════════════════════════════════════════
# هندلر پیام آزاد — همه‌ی جریان‌های چندمرحله‌ای از اینجا با context.user_data
# مدیریت می‌شن (بدون هیچ کلاس FlowManager جداگانه)
# ══════════════════════════════════════════════════════════════════════
async def main_message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.effective_message
    text = (msg.text or msg.caption or "").strip()
    await storage.ensure_user(uid, update.effective_user.username or "", (update.effective_user.full_name or "").strip())

    u = storage.get_user(uid)
    if u and u.get("is_banned"):
        await msg.reply_text("🚫 حساب شما مسدود شده است.")
        return
    if not await enforce_force_join(ctx.bot, uid):
        await msg.reply_text(FORCE_JOIN_TEXT, parse_mode=ParseMode.HTML, reply_markup=await force_join_kb(ctx.bot))
        return

    awaiting = ctx.user_data.get("awaiting")
    if not awaiting:
        await show_main_menu(msg, uid)
        return
    await handle_awaited_input(update, ctx, uid, text, awaiting)


async def handle_awaited_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE, uid: int, text: str, awaiting: str):
    msg = update.effective_message
    d = ctx.user_data
    stripped = text.strip()

    # ── ساخت ربات: منتظر توکن ──
    if awaiting == "token":
        plan = storage.get_plan(uid)
        if len(storage.get_user_bots(uid)) >= plan["max_bots"]:
            ctx.user_data.clear()
            await msg.reply_text(f"⚠️ حداکثر {fmt_limit(plan['max_bots'])} ربات برای پلن {plan['label']} مجاز است.")
            await show_main_menu(msg, uid)
            return
        if ":" not in stripped or len(stripped) < 30:
            await msg.reply_text("❌ توکن نامعتبر است. دوباره بفرست یا /cancel:")
            return
        try:
            me = await Bot(token=stripped).get_me()
        except InvalidToken:
            await msg.reply_text("❌ توکن معتبر نیست. دوباره بفرست یا /cancel:")
            return
        bot_rec = await storage.create_bot(uid, stripped)
        await storage.update_bot_username(bot_rec["bot_id"], me.username or "")
        await factory.start_bot(bot_rec["bot_id"], stripped)
        ctx.user_data.clear()
        await msg.reply_text(
            f"✅ <b>ربات شما آماده شد!</b>\n\n🤖 @{me.username}\n\nبرای مدیریت، از «🤖 ربات‌های من» استفاده کن.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🤖 ربات‌های من", callback_data="nav:mybots")]]))
        return

    # ── پیام شروع (متن/دکمه/عکس) ──
    if awaiting == "start_text":
        await storage.set_start_msg(d["bot_id"], text)
        bot_id = d["bot_id"]; ctx.user_data.clear()
        await msg.reply_text("✅ پیام شروع ذخیره شد.")
        await show_bot_start_section(msg, bot_id)
        return

    if awaiting == "start_buttons":
        bot_id = d["bot_id"]
        if stripped.lower() == "/skip":
            await storage.set_start_buttons(bot_id, None)
            await msg.reply_text("✅ دکمه‌ها حذف شدند.")
        else:
            try:
                rows = parse_button_rows(text)
            except ValueError as e:
                await msg.reply_text(f"❌ {e}\nدوباره بفرست یا /cancel:")
                return
            await storage.set_start_buttons(bot_id, rows)
            await msg.reply_text("✅ دکمه‌ها ذخیره شدند.")
        ctx.user_data.clear()
        await show_bot_start_section(msg, bot_id)
        return

    if awaiting == "start_photo":
        bot_id = d["bot_id"]
        if stripped.lower() == "/skip":
            await storage.set_start_photo(bot_id, None)
            ctx.user_data.clear()
            await msg.reply_text("✅ عکس خوش‌آمد حذف شد.")
            await show_bot_start_section(msg, bot_id)
            return
        if not msg.photo:
            await msg.reply_text("❌ یک عکس بفرست، یا /skip برای حذف عکس فعلی:")
            return
        await storage.set_start_photo(bot_id, msg.photo[-1].file_id)
        ctx.user_data.clear()
        await msg.reply_text("✅ عکس خوش‌آمد ذخیره شد.")
        await show_bot_start_section(msg, bot_id)
        return

    # ── افزودن دستور جدید (سه‌مرحله‌ای: نام → متن → دکمه) ──
    if awaiting == "cmd_add":
        step, bot_id = d["step"], d["bot_id"]
        owner_plan = storage.get_plan(uid)
        if step == "name":
            name = stripped.lower().lstrip("/")
            if not name or " " in name:
                await msg.reply_text("❌ نام نامعتبر است. دوباره بفرست یا /cancel:")
                return
            if name in RESERVED_COMMANDS or name in storage.get_commands(bot_id):
                await msg.reply_text("❌ این نام رزرو شده یا از قبل وجود دارد. نام دیگری بفرست:")
                return
            if len(storage.get_commands(bot_id)) >= owner_plan["max_commands"]:
                ctx.user_data.clear()
                await msg.reply_text(f"❌ حداکثر {fmt_limit(owner_plan['max_commands'])} دستور برای پلن {owner_plan['label']} مجاز است.")
                await show_bot_commands_section(msg, bot_id)
                return
            d["name"] = name; d["step"] = "text"
            await msg.reply_text(f"✅ نام: /{name}\n\nمتن پاسخ این دستور را بفرست:")
            return
        if step == "text":
            d["response"] = text; d["step"] = "buttons"
            await msg.reply_text("🔘 دکمه‌ها را با فرمت زیر بفرست (هر خط = یک ردیف):\nمتن۱|id1, متن۲|id2\n\nیا /skip برای رد کردن:")
            return
        if step == "buttons":
            rows = None
            if stripped.lower() != "/skip":
                try:
                    rows = parse_button_rows(text)
                except ValueError as e:
                    await msg.reply_text(f"❌ {e}\nدوباره بفرست یا /skip:")
                    return
            await storage.set_command(bot_id, d["name"], d["response"], rows)
            name = d["name"]; ctx.user_data.clear()
            await msg.reply_text(f"✅ دستور /{name} ذخیره شد.")
            await show_bot_commands_section(msg, bot_id)
            return

    if awaiting == "cmd_edit_text":
        bot_id, name = d["bot_id"], d["name"]
        cmd = storage.get_commands(bot_id).get(name, {})
        await storage.set_command(bot_id, name, text, cmd.get("buttons"))
        ctx.user_data.clear()
        await msg.reply_text(f"✅ متن دستور /{name} به‌روز شد.")
        await show_cmd_detail(msg, bot_id, name)
        return

    if awaiting == "cmd_edit_buttons":
        bot_id, name = d["bot_id"], d["name"]
        cmd = storage.get_commands(bot_id).get(name, {})
        rows = None
        if stripped.lower() != "/skip":
            try:
                rows = parse_button_rows(text)
            except ValueError as e:
                await msg.reply_text(f"❌ {e}\nدوباره بفرست یا /skip:")
                return
        await storage.set_command(bot_id, name, cmd.get("response", ""), rows)
        ctx.user_data.clear()
        await msg.reply_text(f"✅ دکمه‌های دستور /{name} به‌روز شدند.")
        await show_cmd_detail(msg, bot_id, name)
        return

    # ── افزودن پاسخ دکمه سراسری (دو‌مرحله‌ای: آیدی → متن) ──
    if awaiting == "cb_add":
        step, bot_id = d["step"], d["bot_id"]
        owner_plan = storage.get_plan(uid)
        if step == "id":
            cb_id = stripped
            if not cb_id or " " in cb_id or cb_id in storage.get_callbacks(bot_id):
                await msg.reply_text("❌ آیدی نامعتبر یا تکراری است. آیدی دیگری بفرست یا /cancel:")
                return
            if len(storage.get_callbacks(bot_id)) >= owner_plan["max_callbacks"]:
                ctx.user_data.clear()
                await msg.reply_text(f"❌ حداکثر {fmt_limit(owner_plan['max_callbacks'])} برای پلن {owner_plan['label']} مجاز است.")
                await show_bot_callbacks_section(msg, bot_id)
                return
            d["cb_id"] = cb_id; d["step"] = "text"
            await msg.reply_text("✅ متن پاسخ این دکمه را بفرست:")
            return
        if step == "text":
            await storage.set_callback(bot_id, d["cb_id"], text)
            cb_id = d["cb_id"]; ctx.user_data.clear()
            await msg.reply_text(f"✅ پاسخ دکمه «{cb_id}» ذخیره شد.")
            await show_bot_callbacks_section(msg, bot_id)
            return

    if awaiting == "cb_edit_text":
        bot_id, cb_id = d["bot_id"], d["cb_id"]
        await storage.set_callback(bot_id, cb_id, text)
        ctx.user_data.clear()
        await msg.reply_text(f"✅ پاسخ دکمه «{cb_id}» به‌روز شد.")
        await show_cb_detail(msg, bot_id, cb_id)
        return

    # ── عضویت اجباری اختصاصی ربات ──
    if awaiting == "subbot_forcejoin":
        bot_id = d["bot_id"]
        if stripped.lower() == "/skip":
            await storage.set_bot_force_join(bot_id, None)
            await msg.reply_text("✅ عضویت اجباری اختصاصی این ربات غیرفعال شد.")
        else:
            channel = stripped
            if not (channel.startswith("@") or channel.lstrip("-").isdigit()):
                await msg.reply_text("❌ فرمت نامعتبر. یوزرنیم (@channel) یا آیدی عددی (-100...) بفرست، یا /skip:")
                return
            await storage.set_bot_force_join(bot_id, channel)
            await msg.reply_text(f"✅ عضویت اجباری این ربات روی «{channel}» تنظیم شد.")
        ctx.user_data.clear()
        b = storage.get_bot(bot_id)
        if b:
            t, kb = build_bot_detail(b)
            await msg.reply_text(t, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    # ── پیام همگانی فوری/زمان‌بندی‌شده ──
    if awaiting == "broadcast_now":
        bot_id = d["bot_id"]; ctx.user_data.clear()
        await run_broadcast(msg, bot_id, text)
        return

    if awaiting == "broadcast_sched_minutes":
        if not stripped.isdigit() or int(stripped) <= 0:
            await msg.reply_text("❌ یک عدد صحیح مثبت (دقیقه) بفرست یا /cancel:")
            return
        d["minutes"] = int(stripped)
        ctx.user_data["awaiting"] = "broadcast_sched_text"
        await msg.reply_text(f"⏰ بعد از {d['minutes']} دقیقه ارسال می‌شود.\nمتن پیام همگانی را بفرست:")
        return

    if awaiting == "broadcast_sched_text":
        bot_id, minutes = d["bot_id"], d["minutes"]
        ctx.user_data.clear()
        await msg.reply_text(f"⏰ پیام همگانی شما برای {minutes} دقیقه دیگر زمان‌بندی شد.")
        asyncio.create_task(scheduled_broadcast_task(uid, bot_id, text, minutes))
        return

    # ── پنل ادمین ──
    if awaiting == "admin_grantcoin_uid":
        if not stripped.lstrip("-").isdigit():
            await msg.reply_text("❌ یک آیدی عددی معتبر بفرست یا /cancel:")
            return
        d["target_uid"] = int(stripped); ctx.user_data["awaiting"] = "admin_grantcoin_amount"
        await msg.reply_text("💰 چند کوین اعطا شود؟ (عدد؛ منفی برای کسر) یا /cancel:")
        return

    if awaiting == "admin_grantcoin_amount":
        if not stripped.lstrip("-").isdigit():
            await msg.reply_text("❌ یک عدد معتبر بفرست یا /cancel:")
            return
        target_uid = d["target_uid"]
        new_balance = await storage.admin_grant_coins(target_uid, int(stripped))
        ctx.user_data.clear()
        if new_balance is None:
            await msg.reply_text("❌ کاربر یافت نشد.")
        else:
            await msg.reply_text(f"✅ انجام شد. موجودی جدید کاربر {target_uid}: {new_balance} {COIN_SYMBOL}")
            try:
                await ctx.bot.send_message(target_uid, f"💰 موجودی کوین شما توسط مدیریت به‌روزرسانی شد: {new_balance} {COIN_SYMBOL}")
            except Exception:
                pass
        return

    if awaiting == "admin_grantplan_uid":
        if not stripped.lstrip("-").isdigit():
            await msg.reply_text("❌ یک آیدی عددی معتبر بفرست یا /cancel:")
            return
        d["target_uid"] = int(stripped); ctx.user_data["awaiting"] = "admin_grantplan_choice"
        await msg.reply_text(f"⭐ نام پلن را بفرست ({'، '.join(PLAN_ORDER)}) یا /cancel:")
        return

    if awaiting == "admin_grantplan_choice":
        target_uid, target_plan = d["target_uid"], stripped.lower()
        if target_plan not in PLANS:
            await msg.reply_text("❌ نام پلن نامعتبر. دوباره بفرست یا /cancel:")
            return
        ok = await storage.admin_set_plan(target_uid, target_plan)
        ctx.user_data.clear()
        if ok:
            await msg.reply_text(f"✅ پلن کاربر {target_uid} روی {PLANS[target_plan]['label']} تنظیم شد.")
            try:
                await ctx.bot.send_message(target_uid, f"🎉 پلن شما توسط مدیریت به {PLANS[target_plan]['label']} ارتقا یافت!")
            except Exception:
                pass
        else:
            await msg.reply_text("❌ کاربر یافت نشد.")
        return

    if awaiting in ("admin_ban_uid", "admin_unban_uid"):
        if not stripped.lstrip("-").isdigit():
            await msg.reply_text("❌ یک آیدی عددی معتبر بفرست یا /cancel:")
            return
        ok = await storage.set_banned(int(stripped), awaiting == "admin_ban_uid")
        ctx.user_data.clear()
        await msg.reply_text("✅ انجام شد." if ok else "❌ کاربر یافت نشد.")
        return

    if awaiting == "admin_broadcast":
        ctx.user_data.clear()
        await msg.reply_text("📢 در حال ارسال پیام همگانی پلتفرم...")
        sent, failed = 0, 0
        for target_uid in storage.get_all_user_ids():
            try:
                await ctx.bot.send_message(target_uid, text)
                sent += 1
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except Exception:
                failed += 1
            await asyncio.sleep(BROADCAST_DELAY_SEC)
        await msg.reply_text(f"✅ پیام همگانی تمام شد.\nموفق: {sent}   ناموفق: {failed}")
        return


# ── نمایش زیربخش‌های مدیریت یک ربات (استفاده مشترک از پیام و کال‌بک) ──
async def show_bot_start_section(msg, bot_id: str):
    b = storage.get_bot(bot_id)
    if not b:
        return
    owner_plan = storage.get_plan(b["owner_uid"])
    text = (f"📝 پیام شروع (/start):\n{b.get('start_msg', '')}\n\n"
            f"🔘 دکمه‌ها: {preview_buttons(b.get('start_buttons'))}\n"
            f"🖼 عکس خوش‌آمد: {'دارد' if b.get('start_photo') else 'ندارد'}")
    rows = [
        [InlineKeyboardButton("✏️ ویرایش متن", callback_data=f"bot:start_edit_text:{bot_id}")],
        [InlineKeyboardButton("🔘 ویرایش دکمه‌ها", callback_data=f"bot:start_edit_buttons:{bot_id}")],
    ]
    if owner_plan["media_welcome"]:
        rows.append([InlineKeyboardButton("🖼 ویرایش عکس خوش‌آمد", callback_data=f"bot:start_edit_photo:{bot_id}")])
    else:
        rows.append([InlineKeyboardButton("🔒 عکس خوش‌آمد (نیاز به پلن حرفه‌ای)", callback_data="noop")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")])
    await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def show_bot_commands_section(msg, bot_id: str):
    b = storage.get_bot(bot_id)
    if not b:
        return
    owner_plan = storage.get_plan(b["owner_uid"])
    cmds = storage.get_commands(bot_id)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔧 /{name}", callback_data=f"bot:cmd_detail:{bot_id}:{name}")] for name in cmds
    ] + ([[InlineKeyboardButton("➕ دستور جدید", callback_data=f"bot:cmd_add:{bot_id}")]]
         if len(cmds) < owner_plan["max_commands"] else [])
      + [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")]])
    text = f"🔧 <b>دستورهای دلخواه</b> ({progress_bar(len(cmds), owner_plan['max_commands'])}):\n" + \
        ("روی هر دستور بزن:" if cmds else "هنوز دستوری نساختی.")
    await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def show_cmd_detail(msg, bot_id: str, name: str):
    cmd = storage.get_commands(bot_id).get(name)
    if not cmd:
        await show_bot_commands_section(msg, bot_id)
        return
    text = f"🔧 دستور: /{name}\n\n💬 پاسخ:\n{cmd.get('response', '')}\n\n🔘 دکمه‌ها: {preview_buttons(cmd.get('buttons'))}"
    await msg.reply_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ ویرایش متن", callback_data=f"bot:cmd_edit_text:{bot_id}:{name}"),
         InlineKeyboardButton("🔘 ویرایش دکمه‌ها", callback_data=f"bot:cmd_edit_buttons:{bot_id}:{name}")],
        [InlineKeyboardButton("🗑 حذف دستور", callback_data=f"bot:cmd_delete:{bot_id}:{name}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:commands:{bot_id}")],
    ]))


async def show_bot_callbacks_section(msg, bot_id: str):
    b = storage.get_bot(bot_id)
    if not b:
        return
    owner_plan = storage.get_plan(b["owner_uid"])
    cbs = storage.get_callbacks(bot_id)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"⚡ {cid}", callback_data=f"bot:cb_detail:{bot_id}:{cid}")] for cid in cbs
    ] + ([[InlineKeyboardButton("➕ پاسخ جدید", callback_data=f"bot:cb_add:{bot_id}")]]
         if len(cbs) < owner_plan["max_callbacks"] else [])
      + [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")]])
    text = f"⚡ <b>پاسخ دکمه‌های سراسری</b> ({progress_bar(len(cbs), owner_plan['max_callbacks'])}):\n" + \
        ("روی هر آیتم بزن:" if cbs else "هنوز آیتمی نساختی.")
    await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def show_cb_detail(msg, bot_id: str, cb_id: str):
    resp = storage.get_callbacks(bot_id).get(cb_id)
    if resp is None:
        await show_bot_callbacks_section(msg, bot_id)
        return
    await msg.reply_text(f"⚡ دکمه: {cb_id}\n\nپاسخ:\n{resp}", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ ویرایش", callback_data=f"bot:cb_edit:{bot_id}:{cb_id}")],
        [InlineKeyboardButton("🗑 حذف", callback_data=f"bot:cb_delete:{bot_id}:{cb_id}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:callbacks:{bot_id}")],
    ]))


# ══════════════════════════════════════════════════════════════════════
# 📢 پیام همگانی
# ══════════════════════════════════════════════════════════════════════
async def run_broadcast(msg, bot_id: str, text: str):
    b = storage.get_bot(bot_id)
    if not b:
        return
    sub_bot = factory.get_bot_instance(bot_id)
    if not sub_bot:
        await msg.reply_text("❌ ربات در حال حاضر خاموش است. اول آن را روشن کن.")
        return
    users = b.get("bot_users", [])
    await msg.reply_text(f"📢 شروع ارسال پیام همگانی به {len(users)} کاربر...")
    sent, failed = 0, 0
    for target_uid in users:
        try:
            await sub_bot.send_message(target_uid, text)
            sent += 1
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except (Forbidden, BadRequest):
            failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(BROADCAST_DELAY_SEC)
    await storage.record_broadcast(bot_id, sent)
    await msg.reply_text(f"✅ پیام همگانی تمام شد.\nموفق: {sent}   ناموفق: {failed}")


async def scheduled_broadcast_task(uid: int, bot_id: str, text: str, minutes: int):
    await asyncio.sleep(minutes * 60)
    b = storage.get_bot(bot_id)
    sub_bot = factory.get_bot_instance(bot_id)
    if not b or not sub_bot:
        return
    sent, failed = 0, 0
    for target_uid in b.get("bot_users", []):
        try:
            await sub_bot.send_message(target_uid, text)
            sent += 1
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except Exception:
            failed += 1
        await asyncio.sleep(BROADCAST_DELAY_SEC)
    await storage.record_broadcast(bot_id, sent)
    try:
        await mother_bot_ref["bot"].send_message(
            uid, f"⏰ پیام همگانی زمان‌بندی‌شدهٔ ربات @{b.get('bot_username', '')} ارسال شد.\nموفق: {sent}   ناموفق: {failed}")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# هندلر دکمه‌های ربات مادر
# ══════════════════════════════════════════════════════════════════════
async def main_callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    uid = q.from_user.id
    await storage.ensure_user(uid, q.from_user.username or "", (q.from_user.full_name or "").strip())

    u = storage.get_user(uid)
    if u and u.get("is_banned"):
        await q.answer("🚫 حساب شما مسدود شده است.", show_alert=True)
        return

    if data == "noop":
        await q.answer("🔒 این قابلیت مخصوص پلن‌های بالاتر است.", show_alert=True)
        return

    if data == "fj:check":
        if await enforce_force_join(ctx.bot, uid):
            await q.answer("✅ تایید شد!")
            await show_main_menu(q.message, uid, edit=True)
        else:
            await q.answer("❌ هنوز عضو نشدی!", show_alert=True)
        return

    if not await enforce_force_join(ctx.bot, uid):
        await q.answer()
        try:
            await q.edit_message_text(FORCE_JOIN_TEXT, parse_mode=ParseMode.HTML, reply_markup=await force_join_kb(ctx.bot))
        except Exception:
            pass
        return

    await q.answer()

    # ── ناوبری اصلی ──
    if data == "nav:home":
        ctx.user_data.clear()
        await show_main_menu(q.message, uid, edit=True)
        return
    if data == "nav:mybots":
        text, kb = build_mybots_text_kb(uid)
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return
    if data == "nav:account":
        await q.edit_message_text(build_dashboard_text(uid, storage.get_user(uid)), parse_mode=ParseMode.HTML,
                                   reply_markup=back_kb("nav:home"))
        return
    if data == "nav:plans":
        user = storage.get_user(uid)
        plan_key = user.get("plan", "free")
        await q.edit_message_text(build_plan_card_text(plan_key, user), parse_mode=ParseMode.HTML,
                                   reply_markup=plan_card_kb(plan_key, user))
        return
    if data.startswith("plan:view:"):
        plan_key = data.split(":")[2]
        if plan_key not in PLANS:
            return
        user = storage.get_user(uid)
        await q.edit_message_text(build_plan_card_text(plan_key, user), parse_mode=ParseMode.HTML,
                                   reply_markup=plan_card_kb(plan_key, user))
        return
    if data == "nav:referral":
        await q.edit_message_text(build_referral_text(storage.get_user(uid)), parse_mode=ParseMode.HTML,
                                   reply_markup=back_kb("nav:home"))
        return
    if data == "nav:help":
        await q.edit_message_text(HELP_TEXT, parse_mode=ParseMode.HTML, reply_markup=back_kb("nav:home"))
        return
    if data.startswith("plan:upgrade:"):
        target = data.split(":")[2]
        ok, message = await storage.upgrade_plan(uid, target)
        user = storage.get_user(uid)
        shown_plan = target if ok else user.get("plan", "free")
        head = f"✅ <b>{esc(message)}</b>\n\n" if ok else f"⚠️ {esc(message)}\n\n"
        await q.edit_message_text(head + build_plan_card_text(shown_plan, user), parse_mode=ParseMode.HTML,
                                   reply_markup=plan_card_kb(shown_plan, user))
        return

    # ── پنل ادمین ──
    if data == "admin:panel":
        if not is_admin(uid):
            await q.edit_message_text("❌ دسترسی نداری.", reply_markup=back_kb("nav:home")); return
        await q.edit_message_text(build_admin_panel_text(), parse_mode=ParseMode.HTML, reply_markup=admin_panel_kb())
        return
    if data.startswith("admin:") and not is_admin(uid):
        await q.edit_message_text("❌ دسترسی نداری.", reply_markup=back_kb("nav:home")); return
    if data == "admin:grantcoin":
        ctx.user_data.clear(); ctx.user_data["awaiting"] = "admin_grantcoin_uid"
        await q.edit_message_text("💰 آیدی عددی کاربر را بفرست:\n(/cancel برای انصراف)"); return
    if data == "admin:grantplan":
        ctx.user_data.clear(); ctx.user_data["awaiting"] = "admin_grantplan_uid"
        await q.edit_message_text("⭐ آیدی عددی کاربر را بفرست:\n(/cancel برای انصراف)"); return
    if data == "admin:ban":
        ctx.user_data.clear(); ctx.user_data["awaiting"] = "admin_ban_uid"
        await q.edit_message_text("🚫 آیدی عددی کاربر برای مسدودسازی را بفرست:\n(/cancel برای انصراف)"); return
    if data == "admin:unban":
        ctx.user_data.clear(); ctx.user_data["awaiting"] = "admin_unban_uid"
        await q.edit_message_text("✅ آیدی عددی کاربر برای رفع مسدودی را بفرست:\n(/cancel برای انصراف)"); return
    if data == "admin:broadcast":
        ctx.user_data.clear(); ctx.user_data["awaiting"] = "admin_broadcast"
        await q.edit_message_text("📢 متن پیام همگانی پلتفرم (برای همهٔ کاربران) را بفرست:\n(/cancel برای انصراف)"); return

    # ── ساخت ربات ──
    if data == "nav:create":
        plan = storage.get_plan(uid)
        user_bots = storage.get_user_bots(uid)
        if len(user_bots) >= plan["max_bots"]:
            await q.edit_message_text(
                f"⚠️ در حال حاضر {len(user_bots)}/{fmt_limit(plan['max_bots'])} ربات داری (پلن {plan['label']}).\n"
                "برای ساخت ربات بیشتر، پلن‌ت رو ارتقا بده.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💎 پلن‌ها و ارتقا", callback_data="nav:plans")],
                    [InlineKeyboardButton("🔙 بازگشت", callback_data="nav:mybots")]]))
            return
        ctx.user_data.clear(); ctx.user_data["awaiting"] = "token"
        await q.edit_message_text("🔑 توکن ربات‌ت رو از @BotFather بگیر و همینجا بفرست:\n(/cancel برای انصراف)")
        return

    # ── مدیریت یک ربات ──
    if data.startswith("bot:manage:"):
        bot_id = data.split(":")[2]
        b = storage.get_bot(bot_id)
        if not b or b["owner_uid"] != uid:
            await q.edit_message_text("❌ دسترسی نداری.", reply_markup=back_kb("nav:mybots")); return
        text, kb = build_bot_detail(b)
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data.startswith("bot:toggle:"):
        bot_id = data.split(":")[2]
        b = storage.get_bot(bot_id)
        if not b or b["owner_uid"] != uid:
            return
        new_state = not b.get("is_active", True)
        await storage.set_bot_active(bot_id, new_state)
        await (factory.start_bot(bot_id, b["bot_token"]) if new_state else factory.stop_bot(bot_id))
        text, kb = build_bot_detail(storage.get_bot(bot_id))
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data.startswith("bot:delete:") and not data.startswith("bot:delete_confirm:"):
        bot_id = data.split(":")[2]
        b = storage.get_bot(bot_id)
        if not b or b["owner_uid"] != uid:
            return
        await q.edit_message_text(f"❗️ حذف @{b.get('bot_username', '')}؟ غیرقابل بازگشت است.",
                                   reply_markup=confirm_kb(f"bot:delete_confirm:{bot_id}", f"bot:manage:{bot_id}"))
        return

    if data.startswith("bot:delete_confirm:"):
        bot_id = data.split(":")[2]
        b = storage.get_bot(bot_id)
        if not b or b["owner_uid"] != uid:
            return
        await factory.stop_bot(bot_id)
        await storage.delete_bot(bot_id, uid)
        text, kb = build_mybots_text_kb(uid)
        await q.edit_message_text("🗑 ربات حذف شد.\n\n" + text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    # ── پیام شروع ──
    if data.startswith("bot:start_edit_text:"):
        bot_id = data.split(":")[2]
        ctx.user_data.clear(); ctx.user_data.update(awaiting="start_text", bot_id=bot_id)
        await q.edit_message_text("✏️ متن جدید پیام شروع را بفرست:\n(/cancel برای انصراف)"); return
    if data.startswith("bot:start_edit_buttons:"):
        bot_id = data.split(":")[2]
        ctx.user_data.clear(); ctx.user_data.update(awaiting="start_buttons", bot_id=bot_id)
        await q.edit_message_text("🔘 دکمه‌ها را بفرست (هر خط = یک ردیف):\nمتن۱|id1, متن۲|id2\nیا /skip\n(/cancel برای انصراف)")
        return
    if data.startswith("bot:start_edit_photo:"):
        bot_id = data.split(":")[2]
        b = storage.get_bot(bot_id)
        if not b or b["owner_uid"] != uid:
            return
        if not storage.get_plan(uid)["media_welcome"]:
            await q.answer("🔒 این قابلیت مخصوص پلن حرفه‌ای به بالاست.", show_alert=True); return
        ctx.user_data.clear(); ctx.user_data.update(awaiting="start_photo", bot_id=bot_id)
        await q.edit_message_text("🖼 یک عکس برای پیام خوش‌آمد بفرست، یا /skip برای حذف عکس فعلی:\n(/cancel برای انصراف)")
        return
    if data.startswith("bot:start:"):
        bot_id = data.split(":")[2]
        b = storage.get_bot(bot_id)
        if not b or b["owner_uid"] != uid:
            return
        owner_plan = storage.get_plan(uid)
        text = (f"📝 پیام شروع (/start):\n{b.get('start_msg', '')}\n\n"
                f"🔘 دکمه‌ها: {preview_buttons(b.get('start_buttons'))}\n"
                f"🖼 عکس خوش‌آمد: {'دارد' if b.get('start_photo') else 'ندارد'}")
        rows = [
            [InlineKeyboardButton("✏️ ویرایش متن", callback_data=f"bot:start_edit_text:{bot_id}")],
            [InlineKeyboardButton("🔘 ویرایش دکمه‌ها", callback_data=f"bot:start_edit_buttons:{bot_id}")],
        ]
        if owner_plan["media_welcome"]:
            rows.append([InlineKeyboardButton("🖼 ویرایش عکس خوش‌آمد", callback_data=f"bot:start_edit_photo:{bot_id}")])
        else:
            rows.append([InlineKeyboardButton("🔒 عکس خوش‌آمد (نیاز به پلن برنزی+)", callback_data="noop")])
        rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
        return

    # ── دستورهای دلخواه ──
    if data.startswith("bot:commands:"):
        bot_id = data.split(":")[2]
        b = storage.get_bot(bot_id)
        if not b or b["owner_uid"] != uid:
            return
        owner_plan = storage.get_plan(uid)
        cmds = storage.get_commands(bot_id)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🔧 /{n}", callback_data=f"bot:cmd_detail:{bot_id}:{n}")] for n in cmds
        ] + ([[InlineKeyboardButton("➕ دستور جدید", callback_data=f"bot:cmd_add:{bot_id}")]]
             if len(cmds) < owner_plan["max_commands"] else [])
          + [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")]])
        text = f"🔧 <b>دستورهای دلخواه</b> ({progress_bar(len(cmds), owner_plan['max_commands'])}):\n" + \
            ("روی هر دستور بزن:" if cmds else "هنوز دستوری نساختی.")
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return
    if data.startswith("bot:cmd_add:"):
        bot_id = data.split(":")[2]
        ctx.user_data.clear(); ctx.user_data.update(awaiting="cmd_add", bot_id=bot_id, step="name")
        await q.edit_message_text("➕ نام دستور جدید را بدون / بفرست (مثلاً info):\n(/cancel برای انصراف)"); return
    if data.startswith("bot:cmd_detail:"):
        _, _, bot_id, name = data.split(":", 3)
        cmd = storage.get_commands(bot_id).get(name)
        if not cmd:
            return
        text = f"🔧 دستور: /{name}\n\n💬 پاسخ:\n{cmd.get('response', '')}\n\n🔘 دکمه‌ها: {preview_buttons(cmd.get('buttons'))}"
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ ویرایش متن", callback_data=f"bot:cmd_edit_text:{bot_id}:{name}"),
             InlineKeyboardButton("🔘 ویرایش دکمه‌ها", callback_data=f"bot:cmd_edit_buttons:{bot_id}:{name}")],
            [InlineKeyboardButton("🗑 حذف دستور", callback_data=f"bot:cmd_delete:{bot_id}:{name}")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:commands:{bot_id}")]]))
        return
    if data.startswith("bot:cmd_edit_text:"):
        _, _, bot_id, name = data.split(":", 3)
        ctx.user_data.clear(); ctx.user_data.update(awaiting="cmd_edit_text", bot_id=bot_id, name=name)
        await q.edit_message_text(f"✏️ متن جدید برای /{name}:\n(/cancel برای انصراف)"); return
    if data.startswith("bot:cmd_edit_buttons:"):
        _, _, bot_id, name = data.split(":", 3)
        ctx.user_data.clear(); ctx.user_data.update(awaiting="cmd_edit_buttons", bot_id=bot_id, name=name)
        await q.edit_message_text(f"🔘 دکمه‌های جدید برای /{name}:\nمتن۱|id1, متن۲|id2\nیا /skip\n(/cancel برای انصراف)")
        return
    if data.startswith("bot:cmd_delete:") and "confirm" not in data:
        _, _, bot_id, name = data.split(":", 3)
        await q.edit_message_text(f"🗑 حذف دستور /{name}؟",
                                   reply_markup=confirm_kb(f"bot:cmd_delete_confirm:{bot_id}:{name}", f"bot:cmd_detail:{bot_id}:{name}"))
        return
    if data.startswith("bot:cmd_delete_confirm:"):
        _, _, bot_id, name = data.split(":", 3)
        await storage.delete_command(bot_id, name)
        owner_plan = storage.get_plan(uid)
        cmds = storage.get_commands(bot_id)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🔧 /{n}", callback_data=f"bot:cmd_detail:{bot_id}:{n}")] for n in cmds
        ] + ([[InlineKeyboardButton("➕ دستور جدید", callback_data=f"bot:cmd_add:{bot_id}")]]
             if len(cmds) < owner_plan["max_commands"] else [])
          + [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")]])
        text = f"🔧 <b>دستورهای دلخواه</b> ({progress_bar(len(cmds), owner_plan['max_commands'])}):\n" + \
            ("روی هر دستور بزن:" if cmds else "هنوز دستوری نساختی.")
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    # ── پاسخ دکمه‌های سراسری ──
    if data.startswith("bot:callbacks:"):
        bot_id = data.split(":")[2]
        b = storage.get_bot(bot_id)
        if not b or b["owner_uid"] != uid:
            return
        owner_plan = storage.get_plan(uid)
        cbs = storage.get_callbacks(bot_id)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"⚡ {c}", callback_data=f"bot:cb_detail:{bot_id}:{c}")] for c in cbs
        ] + ([[InlineKeyboardButton("➕ پاسخ جدید", callback_data=f"bot:cb_add:{bot_id}")]]
             if len(cbs) < owner_plan["max_callbacks"] else [])
          + [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")]])
        text = f"⚡ <b>پاسخ دکمه‌های سراسری</b> ({progress_bar(len(cbs), owner_plan['max_callbacks'])}):\n" + \
            ("روی هر آیتم بزن:" if cbs else "هنوز آیتمی نساختی.")
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return
    if data.startswith("bot:cb_add:"):
        bot_id = data.split(":")[2]
        ctx.user_data.clear(); ctx.user_data.update(awaiting="cb_add", bot_id=bot_id, step="id")
        await q.edit_message_text(
            "➕ آیدی دکمه را بفرست (همون callback_data که در دکمه‌های /start یا دستورها استفاده کردی):\n(/cancel برای انصراف)")
        return
    if data.startswith("bot:cb_detail:"):
        _, _, bot_id, cid = data.split(":", 3)
        resp = storage.get_callbacks(bot_id).get(cid)
        if resp is None:
            return
        await q.edit_message_text(f"⚡ دکمه: {cid}\n\nپاسخ:\n{resp}", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ ویرایش", callback_data=f"bot:cb_edit:{bot_id}:{cid}")],
            [InlineKeyboardButton("🗑 حذف", callback_data=f"bot:cb_delete:{bot_id}:{cid}")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:callbacks:{bot_id}")]]))
        return
    if data.startswith("bot:cb_edit:"):
        _, _, bot_id, cid = data.split(":", 3)
        ctx.user_data.clear(); ctx.user_data.update(awaiting="cb_edit_text", bot_id=bot_id, cb_id=cid)
        await q.edit_message_text(f"✏️ پاسخ جدید برای «{cid}»:\n(/cancel برای انصراف)"); return
    if data.startswith("bot:cb_delete:") and "confirm" not in data:
        _, _, bot_id, cid = data.split(":", 3)
        await q.edit_message_text(f"🗑 حذف پاسخ دکمه «{cid}»؟",
                                   reply_markup=confirm_kb(f"bot:cb_delete_confirm:{bot_id}:{cid}", f"bot:cb_detail:{bot_id}:{cid}"))
        return
    if data.startswith("bot:cb_delete_confirm:"):
        _, _, bot_id, cid = data.split(":", 3)
        await storage.delete_callback(bot_id, cid)
        owner_plan = storage.get_plan(uid)
        cbs = storage.get_callbacks(bot_id)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"⚡ {c}", callback_data=f"bot:cb_detail:{bot_id}:{c}")] for c in cbs
        ] + ([[InlineKeyboardButton("➕ پاسخ جدید", callback_data=f"bot:cb_add:{bot_id}")]]
             if len(cbs) < owner_plan["max_callbacks"] else [])
          + [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")]])
        text = f"⚡ <b>پاسخ دکمه‌های سراسری</b> ({progress_bar(len(cbs), owner_plan['max_callbacks'])}):\n" + \
            ("روی هر آیتم بزن:" if cbs else "هنوز آیتمی نساختی.")
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    # ── آنالیتیکس ──
    if data.startswith("bot:stats:"):
        bot_id = data.split(":")[2]
        b = storage.get_bot(bot_id)
        if not b or b["owner_uid"] != uid:
            return
        await q.edit_message_text(build_stats_text(b, storage.get_plan(uid)), parse_mode=ParseMode.HTML,
                                   reply_markup=back_kb(f"bot:manage:{bot_id}"))
        return

    # ── پیام همگانی ──
    if data.startswith("bot:bc_menu:"):
        bot_id = data.split(":")[2]
        b = storage.get_bot(bot_id)
        if not b or b["owner_uid"] != uid:
            return
        owner_plan = storage.get_plan(uid)
        if not owner_plan["broadcast"]:
            await q.edit_message_text("🔒 پیام همگانی مخصوص پلن حرفه‌ای به بالاست.",
                                       reply_markup=InlineKeyboardMarkup([
                                           [InlineKeyboardButton("💎 پلن‌ها و ارتقا", callback_data="nav:plans")],
                                           [InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")]]))
            return
        rows = [[InlineKeyboardButton("🚀 ارسال فوری", callback_data=f"bot:bc_now:{bot_id}")]]
        if owner_plan["scheduled_broadcast"]:
            rows.append([InlineKeyboardButton("⏰ زمان‌بندی ارسال", callback_data=f"bot:bc_sched:{bot_id}")])
        else:
            rows.append([InlineKeyboardButton("🔒 زمان‌بندی (نیاز به پلن حرفه‌ای)", callback_data="noop")])
        rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")])
        await q.edit_message_text(f"📢 پیام همگانی\n\n👥 تعداد کاربران ربات: {len(b.get('bot_users', []))}",
                                   reply_markup=InlineKeyboardMarkup(rows))
        return
    if data.startswith("bot:bc_now:"):
        bot_id = data.split(":")[2]
        ctx.user_data.clear(); ctx.user_data.update(awaiting="broadcast_now", bot_id=bot_id)
        await q.edit_message_text("🚀 متن پیام همگانی را بفرست:\n(/cancel برای انصراف)"); return
    if data.startswith("bot:bc_sched:"):
        bot_id = data.split(":")[2]
        ctx.user_data.clear(); ctx.user_data.update(awaiting="broadcast_sched_minutes", bot_id=bot_id)
        await q.edit_message_text("⏰ بعد از چند دقیقه ارسال شود؟ (فقط عدد):\n(/cancel برای انصراف)"); return

    # ── عضویت اجباری اختصاصی ربات ──
    if data.startswith("bot:fj_menu:"):
        bot_id = data.split(":")[2]
        b = storage.get_bot(bot_id)
        if not b or b["owner_uid"] != uid:
            return
        owner_plan = storage.get_plan(uid)
        if not owner_plan["custom_force_join"]:
            await q.edit_message_text("🔒 عضویت اجباری اختصاصی مخصوص پلن حرفه‌ای به بالاست.",
                                       reply_markup=InlineKeyboardMarkup([
                                           [InlineKeyboardButton("💎 پلن‌ها و ارتقا", callback_data="nav:plans")],
                                           [InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")]]))
            return
        cur = b.get("force_join_channel") or "غیرفعال"
        ctx.user_data.clear(); ctx.user_data.update(awaiting="subbot_forcejoin", bot_id=bot_id)
        await q.edit_message_text(
            f"🚫 عضویت اجباری فعلی این ربات: {cur}\n\n"
            "یوزرنیم کانال (@channel) یا آیدی عددی (-100...) را بفرست، یا /skip برای غیرفعال‌کردن:\n(/cancel برای انصراف)")
        return

    # ── پشتیبان‌گیری (Export) ──
    if data.startswith("bot:export:"):
        bot_id = data.split(":")[2]
        b = storage.get_bot(bot_id)
        if not b or b["owner_uid"] != uid:
            return
        export_data = {
            "bot_username": b.get("bot_username"), "start_msg": b.get("start_msg"),
            "start_buttons": b.get("start_buttons"), "commands": b.get("commands"),
            "callbacks": b.get("callbacks"), "force_join_channel": b.get("force_join_channel"),
        }
        payload = json.dumps(export_data, ensure_ascii=False, indent=2)
        if len(payload) > 3500:
            payload = payload[:3500] + "\n… (بریده‌شده)"
        await q.edit_message_text(f"💾 پشتیبان تنظیمات @{b.get('bot_username', '')}:\n\n<pre>{esc(payload)}</pre>",
                                   parse_mode=ParseMode.HTML, reply_markup=back_kb(f"bot:manage:{bot_id}"))
        return


# ══════════════════════════════════════════════════════════════════════
# هندلرهای ربات‌های زیرمجموعه — فقط اجرا، بدون منطق نویسندگی
# ══════════════════════════════════════════════════════════════════════
async def handle_subbot_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE, bot_id: str):
    uid = update.effective_user.id
    b = storage.get_bot(bot_id)
    if not b:
        return
    await storage.add_bot_user(bot_id, uid)
    await storage.update_bot_stats(bot_id, received=1)
    fj = b.get("force_join_channel")
    if fj and not await enforce_subbot_force_join(ctx.bot, uid, fj):
        await update.effective_message.reply_text(
            "🚫 برای استفاده از این ربات ابتدا باید در کانال زیر عضو شوی.",
            reply_markup=await subbot_force_join_kb(ctx.bot, bot_id, fj))
        return
    kb = build_inline_from_rows(b.get("start_buttons"))
    photo = b.get("start_photo")
    if photo:
        await update.effective_message.reply_photo(photo, caption=b.get("start_msg", "سلام 👋"), reply_markup=kb)
    else:
        await update.effective_message.reply_text(b.get("start_msg", "سلام 👋"), reply_markup=kb)
    await storage.update_bot_stats(bot_id, sent=1)


async def handle_subbot_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE, bot_id: str):
    uid = update.effective_user.id
    text = (update.effective_message.text or "").strip()
    b = storage.get_bot(bot_id)
    if not b:
        return
    await storage.add_bot_user(bot_id, uid)
    await storage.update_bot_stats(bot_id, received=1)
    fj = b.get("force_join_channel")
    if fj and not await enforce_subbot_force_join(ctx.bot, uid, fj):
        await update.effective_message.reply_text(
            "🚫 برای استفاده از این ربات ابتدا باید در کانال زیر عضو شوی.",
            reply_markup=await subbot_force_join_kb(ctx.bot, bot_id, fj))
        return
    cmd_name = text.lstrip("/").split()[0].lower() if text.startswith("/") else ""
    cmd = b.get("commands", {}).get(cmd_name)
    if cmd:
        await update.effective_message.reply_text(cmd.get("response", ""), reply_markup=build_inline_from_rows(cmd.get("buttons")))
        await storage.update_bot_stats(bot_id, sent=1)


async def handle_subbot_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE, bot_id: str):
    q = update.callback_query
    uid = q.from_user.id
    data = q.data or ""

    if data == f"sfj:check:{bot_id}":
        b = storage.get_bot(bot_id)
        fj = b.get("force_join_channel") if b else None
        if await enforce_subbot_force_join(ctx.bot, uid, fj):
            await q.answer("✅ تایید شد!")
            await handle_subbot_start(update, ctx, bot_id)
        else:
            await q.answer("❌ هنوز عضو نشدی!", show_alert=True)
        return

    await q.answer()
    b = storage.get_bot(bot_id)
    if not b:
        return
    fj = b.get("force_join_channel")
    if fj and not await enforce_subbot_force_join(ctx.bot, uid, fj):
        try:
            await q.message.reply_text("🚫 برای استفاده از این ربات ابتدا باید در کانال زیر عضو شوی.",
                                        reply_markup=await subbot_force_join_kb(ctx.bot, bot_id, fj))
        except Exception:
            pass
        return
    resp = storage.get_callbacks(bot_id).get(data)
    if resp is not None:
        await q.message.reply_text(resp)
        await storage.update_bot_stats(bot_id, sent=1)


# ══════════════════════════════════════════════════════════════════════
# استارتاپ / شات‌داون / main
# ══════════════════════════════════════════════════════════════════════
async def on_startup(app: Application):
    global PLATFORM_USERNAME
    mother_bot_ref["bot"] = app.bot
    await storage.load()
    # 🔄 مهاجرت خودکار کاربران قدیمی از پلن‌های منسوخ‌شده
    _MIGRATION = {
        "free": "starter",
        "bronze": "professional",
        "silver": "professional",
        "gold": "enterprise",
        "diamond": "enterprise",
    }
    for u in storage.data["users"].values():
        old_plan = u.get("plan")
        if old_plan in _MIGRATION:
            u["plan"] = _MIGRATION[old_plan]
            storage.mark_dirty()
    log.info("🔄 مهاجرت پلن‌های قدیمی انجام شد")
    me = await app.bot.get_me()
    PLATFORM_USERNAME = me.username or ""
    await factory.start_all_active()
    asyncio.create_task(storage.autosave_loop())
    log.info(f"🟢 GramSaz v3 آماده — @{PLATFORM_USERNAME} — {len(factory._running)} ربات فعال")


async def on_shutdown(app: Application):
    await factory.stop_all()
    await storage.save(force=True)


def main():
    app = (Application.builder().token(PLATFORM_BOT_TOKEN)
           .post_init(on_startup).post_shutdown(on_shutdown).build())

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(main_callback_handler))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, main_message_handler))

    if WEBHOOK_URL:
        log.info(f"🌐 اجرا با وب‌هوک: {WEBHOOK_URL}/{PLATFORM_BOT_TOKEN}")
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=PLATFORM_BOT_TOKEN,
                         webhook_url=f"{WEBHOOK_URL}/{PLATFORM_BOT_TOKEN}", drop_pending_updates=True)
    else:
        log.info("📡 اجرا با polling (برای وب‌هوک، WEBHOOK_URL رو در env ست کن)")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
