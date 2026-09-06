#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GramSaz — پلتفرم ربات‌ساز تلگرام (v0.1 — جریان اصلی + دستورساز + عضویت اجباری)
════════════════════════════════════════════════════════════════════
فول async: python-telegram-bot (v20+) + Motor (MongoDB async، اختیاری)

معماری: دقیقاً مثل AZBotSaz (نسخه روبیکا) — مدیریت همه ربات‌های زیرمجموعه
از داخل ربات مادر انجام می‌شه؛ ربات‌های زیرمجموعه فقط تنظیمات ذخیره‌شده رو
«اجرا» می‌کنن. این جداسازی باعث می‌شه افزودن هر فیچر جدید (پلاگین، بازی،
فروشگاه، ...) فقط یک‌جا (بخش هندلرهای ربات مادر) دست بخوره، بدون لمس
منطق اجرای ربات‌های کاربر.

این نسخه عمداً «جمع‌وجور» نگه داشته شده — نسخهٔ کامل و شلوغ (با اقتصاد
سکه، پلاگین، تیکت پشتیبانی و ...) بعداً و روی همین اسکلت اضافه می‌شه؛
اینجا فقط هستهٔ محکم و ساده‌ست:
  • ساخت ربات با توکن، ۳ روز اشتراک آزمایشی
  • پیام شروع (/start) قابل‌ویرایش با دکمه
  • دستور دلخواه (مثل /info) با متن و دکمه اختصاصی
  • پاسخ دکمه‌های سراسری (callback)
  • عضویت اجباری در یک کانال، قبل از استفاده از منوی ربات‌ساز
  • پنل مدیریت ساده برای ادمین (آمار پلتفرم)

تصمیم‌های فنی نسبت به نمونه‌ای که فرستادی (GramBotSaz v4.1):
  • به‌جای هک Flask + Thread برای وب‌هوک، از قابلیت رسمی و تمیز خودِ
    python-telegram-bot استفاده شده (Application.run_webhook) — چند خط
    ساده‌تر و بدون نیاز به پل زدن دستی بین Flask و ایونت‌لوپ asyncio.
  • پیش‌فرض روی «polling» ست شده (نیازی به دامنه/وب‌هوک برای شروع نیست)؛
    اگه بعداً WEBHOOK_URL رو در env ست کنی، خودکار می‌ره روی وب‌هوک —
    یعنی زیرساخت از روز اول برای مقیاس آماده‌ست، بدون پیچیدگی اضافه در
    مرحلهٔ توسعه/تست.
  • Storage به‌صورت async با asyncio.Lock (نه threading.Lock) نوشته شده،
    چون همه‌چی روی asyncio (python-telegram-bot v20+) اجرا می‌شه.
════════════════════════════════════════════════════════════════════
"""

# ════════════════════════════════════════════════════════════════════
# ایمپورت‌ها
# ════════════════════════════════════════════════════════════════════
from __future__ import annotations
import os
import sys
import time
import hashlib
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot,
)
from telegram.error import InvalidToken, Forbidden
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters,
)

try:
    from motor.motor_asyncio import AsyncIOMotorClient
    MOTOR_AVAILABLE = True
except ImportError:
    MOTOR_AVAILABLE = False


# ════════════════════════════════════════════════════════════════════
# لاگ‌گذاری
# ════════════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO, stream=sys.stdout,
)
log = logging.getLogger("GramSaz")


# ════════════════════════════════════════════════════════════════════
# تنظیمات و ثابت‌ها
# ════════════════════════════════════════════════════════════════════
PLATFORM_BOT_TOKEN = os.getenv("PLATFORM_BOT_TOKEN", "8649622614:AAFs3Hoz9EbjBGiFeua3wt8j_XJFRbtMNxA").strip()
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip().rstrip("/")  # خالی = polling
PORT = int(os.getenv("PORT", "10000"))

ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "605611554").replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()}

# 🚫 عضویت اجباری روی خودِ ربات‌ساز (ربات مادر) — یک کانال ثابت.
# می‌تونه یوزرنیم (@channel) یا آیدی عددی کانال (-100...) باشه.
FORCE_JOIN_CHANNEL = os.getenv("FORCE_JOIN_CHANNEL", "@testchanellaz").strip()

COIN_SYMBOL = "GC"
SIGNUP_BONUS = 50
TRIAL_DAYS = 3
MAX_FREE_BOTS = 1
MAX_COMMANDS_PER_BOT = 40
MAX_CALLBACKS_PER_BOT = 60
RESERVED_COMMANDS = {"start", "cancel", "help", "id"}

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))


def now_ir() -> datetime:
    return datetime.now(IRAN_TZ)


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


if not PLATFORM_BOT_TOKEN:
    log.critical("PLATFORM_BOT_TOKEN تنظیم نشده!")
    sys.exit(1)


# ════════════════════════════════════════════════════════════════════
# لایه ذخیره‌سازی (Storage) — async
# ════════════════════════════════════════════════════════════════════
class Storage:
    """
    دیتای زنده در حافظه (dict) + سینک با MongoDB (اختیاری — اگه
    MONGODB_URI ست نشه، فقط در حافظه کار می‌کنه و با ری‌استارت پاک می‌شه).
    کالکشن‌ها:
      users: {uid_str: {...}}
      bots:  {bot_id:  {...}}   — شامل start_msg/start_buttons/commands/callbacks
    """

    def __init__(self, mongo_uri: str = MONGODB_URI):
        self._uri = mongo_uri
        self._col = None
        self._lock = asyncio.Lock()
        self._dirty = False
        self.data: dict = {"users": {}, "bots": {}}
        if self._uri and MOTOR_AVAILABLE:
            client = AsyncIOMotorClient(self._uri, serverSelectionTimeoutMS=10000)
            self._col = client.get_default_database()["gramsaz_data"]

    # ---- بارگذاری / ذخیره ----
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
                u.setdefault("signup_bonus_claimed", False)
            for b in self.data["bots"].values():
                self._apply_bot_defaults(b)

    @staticmethod
    def _apply_bot_defaults(b: dict):
        b.setdefault("start_msg", "👋 سلام! خوش آمدید.")
        b.setdefault("start_buttons", None)
        b.setdefault("commands", {})
        b.setdefault("callbacks", {})
        b.setdefault("subscription_expiry", None)
        b.setdefault("subscription_plan", None)
        b.setdefault("is_trial", False)
        b.setdefault("bot_users", [])
        b.setdefault("stats", {"messages_received": 0, "messages_sent": 0, "unique_users": 0})

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
    async def ensure_user(self, uid: int, username: str = "", full_name: str = "") -> tuple[dict, bool]:
        key = str(uid)
        is_new = False
        async with self._lock:
            if key not in self.data["users"]:
                is_new = True
                ref_code = hashlib.md5(f"{uid}{time.time()}".encode()).hexdigest()[:8]
                self.data["users"][key] = {
                    "uid": uid, "username": username, "full_name": full_name,
                    "coin_balance": 0, "referral_code": ref_code, "referred_by": None,
                    "created_at": now_ir().isoformat(), "bots_created": [],
                    "signup_bonus_claimed": False,
                }
                self.mark_dirty()
            else:
                u = self.data["users"][key]
                if username and u.get("username") != username:
                    u["username"] = username; self.mark_dirty()
                if full_name and u.get("full_name") != full_name:
                    u["full_name"] = full_name; self.mark_dirty()
        return dict(self.data["users"][key]), is_new

    def get_user(self, uid: int) -> Optional[dict]:
        u = self.data["users"].get(str(uid))
        return dict(u) if u else None

    async def claim_signup_bonus(self, uid: int) -> bool:
        key = str(uid)
        async with self._lock:
            u = self.data["users"].get(key)
            if not u or u.get("signup_bonus_claimed"):
                return False
            u["signup_bonus_claimed"] = True
            u["coin_balance"] += SIGNUP_BONUS
            self.mark_dirty()
        return True

    # ---- ربات‌ها ----
    async def create_bot(self, owner_uid: int, bot_token: str) -> dict:
        bot_id = hashlib.md5(f"{owner_uid}{bot_token}{time.time()}".encode()).hexdigest()[:12]
        trial_expiry = (now_ir() + timedelta(days=TRIAL_DAYS)).isoformat()
        async with self._lock:
            bot_rec = {
                "bot_id": bot_id, "owner_uid": owner_uid, "bot_token": bot_token,
                "bot_username": "", "is_active": True, "created_at": now_ir().isoformat(),
                "last_activity": None,
                "subscription_expiry": trial_expiry, "subscription_plan": "trial", "is_trial": True,
            }
            self._apply_bot_defaults(bot_rec)
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
            key = str(owner_uid)
            u = self.data["users"].get(key)
            if u and bot_id in u.get("bots_created", []):
                u["bots_created"].remove(bot_id)
            self.mark_dirty()
        return True

    async def set_bot_active(self, bot_id: str, active: bool):
        async with self._lock:
            if bot_id in self.data["bots"]:
                self.data["bots"][bot_id]["is_active"] = active
                self.mark_dirty()

    def check_subscription_expired(self, bot_id: str) -> bool:
        b = self.data["bots"].get(bot_id)
        if not b or not b.get("subscription_expiry"):
            return True
        try:
            return now_ir() >= datetime.fromisoformat(b["subscription_expiry"])
        except Exception:
            return True

    async def update_bot_stats(self, bot_id: str, received: int = 0, sent: int = 0):
        async with self._lock:
            b = self.data["bots"].get(bot_id)
            if b:
                b["stats"]["messages_received"] += received
                b["stats"]["messages_sent"] += sent
                b["last_activity"] = now_ir().isoformat()
                self.mark_dirty()

    async def add_bot_user(self, bot_id: str, uid: int):
        async with self._lock:
            b = self.data["bots"].get(bot_id)
            if not b:
                return
            users = b.setdefault("bot_users", [])
            if uid not in users:
                users.append(uid)
                b["stats"]["unique_users"] = len(users)
                self.mark_dirty()

    # ---- پیام شروع ----
    async def set_start_msg(self, bot_id: str, text: str):
        async with self._lock:
            if bot_id in self.data["bots"]:
                self.data["bots"][bot_id]["start_msg"] = text
                self.mark_dirty()

    async def set_start_buttons(self, bot_id: str, rows: Optional[list]):
        async with self._lock:
            if bot_id in self.data["bots"]:
                self.data["bots"][bot_id]["start_buttons"] = rows
                self.mark_dirty()

    # ---- دستورهای دلخواه ----
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

    # ---- دکمه‌های سراسری (callback) ----
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

    # ---- آمار کلی ----
    def get_platform_stats(self) -> dict:
        bots = self.data["bots"]
        return {
            "total_users": len(self.data["users"]),
            "total_bots": len(bots),
            "active_bots": sum(1 for b in bots.values() if b.get("is_active")),
        }


storage = Storage()


# ════════════════════════════════════════════════════════════════════
# FlowManager — گفتگوی چندمرحله‌ای عمومی (قابل استفاده برای هر فیچر آینده)
# ════════════════════════════════════════════════════════════════════
class FlowManager:
    def __init__(self):
        self._flows: dict[int, dict] = {}

    def start(self, uid: int, kind: str, **data):
        self._flows[uid] = {"kind": kind, "data": data}

    def get(self, uid: int) -> Optional[dict]:
        return self._flows.get(uid)

    def end(self, uid: int):
        self._flows.pop(uid, None)


main_flows = FlowManager()


# ════════════════════════════════════════════════════════════════════
# BotFactory — مدیریت نمونه‌های Application ساب‌بات‌ها (پولینگ)
# ════════════════════════════════════════════════════════════════════
class BotFactory:
    """
    هر ساب‌بات یک Application جدا با polling خودش داره که در قالب یک
    asyncio.Task در پس‌زمینه اجرا می‌شه. جدا نگه‌داشتن هر ربات یعنی
    خاموش/روشن/حذف کردن یکی، بقیه رو تحت‌تأثیر قرار نمی‌ده.
    """

    def __init__(self, storage: Storage):
        self.storage = storage
        self._running: dict[str, asyncio.Task] = {}
        self._apps: dict[str, Application] = {}
        self._lock = asyncio.Lock()

    async def start_bot(self, bot_id: str, bot_token: str):
        async with self._lock:
            if bot_id in self._running:
                return
            if self.storage.check_subscription_expired(bot_id):
                await self.storage.set_bot_active(bot_id, False)
                return
            try:
                app = Application.builder().token(bot_token).build()
                app.add_handler(CommandHandler("start", lambda u, c: handle_subbot_start(u, c, bot_id)))
                app.add_handler(CallbackQueryHandler(lambda u, c: handle_subbot_callback(u, c, bot_id)))
                app.add_handler(MessageHandler(
                    filters.TEXT & filters.COMMAND,
                    lambda u, c: handle_subbot_command(u, c, bot_id)))
                await app.initialize()
                me = await app.bot.get_me()
                await self.storage.update_bot_username(bot_id, me.username or "")
                task = asyncio.create_task(self._run(app, bot_id))
                self._running[bot_id] = task
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

    async def start_all_active(self):
        for b in self.storage.get_all_bots():
            if b.get("is_active") and not self.storage.check_subscription_expired(b["bot_id"]):
                await self.start_bot(b["bot_id"], b["bot_token"])

    async def stop_all(self):
        for bot_id in list(self._running.keys()):
            await self.stop_bot(bot_id)


factory = BotFactory(storage)


# ════════════════════════════════════════════════════════════════════
# 🚫 Force Join Helpers — عضویت اجباری روی ربات مادر
# ════════════════════════════════════════════════════════════════════
async def check_membership(bot: Bot, uid: int) -> bool:
    if not FORCE_JOIN_CHANNEL:
        return True
    try:
        member = await bot.get_chat_member(FORCE_JOIN_CHANNEL, uid)
        return member.status not in ("left", "kicked")
    except Exception as e:
        log.warning(f"[ForceJoin] خطا در بررسی عضویت: {e}")
        return True  # اگه چک ممکن نبود (مثلاً ربات ادمین کانال نیست)، کاربر رو بلاک نکن


def force_join_kb() -> InlineKeyboardMarkup:
    if FORCE_JOIN_CHANNEL.startswith("@"):
        url = f"https://t.me/{FORCE_JOIN_CHANNEL.lstrip('@')}"
    else:
        url = f"https://t.me/c/{str(FORCE_JOIN_CHANNEL).replace('-100', '')}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url=url)],
        [InlineKeyboardButton("✅ عضو شدم، بررسی کن", callback_data="fj:check")],
    ])


FORCE_JOIN_TEXT = (
    "🚫 برای استفاده از ربات‌ساز، ابتدا باید در کانال زیر عضو شوید.\n\n"
    "بعد از عضویت، روی «✅ عضو شدم، بررسی کن» بزنید."
)


async def enforce_force_join(bot: Bot, uid: int) -> bool:
    """True یعنی اجازهٔ ادامه داره؛ False یعنی هنوز عضو نیست."""
    return await check_membership(bot, uid)


# ════════════════════════════════════════════════════════════════════
# UI Helpers — منو و متن‌سازها
# ════════════════════════════════════════════════════════════════════
def main_menu_kb(uid: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🤖 ربات‌های من", callback_data="nav:mybots")],
        [InlineKeyboardButton("➕ ساخت ربات جدید", callback_data="nav:create")],
        [InlineKeyboardButton("👤 حساب کاربری", callback_data="nav:account")],
        [InlineKeyboardButton("📖 راهنما", callback_data="nav:help")],
    ]
    if is_admin(uid):
        rows.append([InlineKeyboardButton("🛠 پنل مدیریت (ادمین)", callback_data="admin:panel")])
    return InlineKeyboardMarkup(rows)


def back_kb(dest: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=dest)]])


def confirm_kb(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ بله، مطمئنم", callback_data=yes_cb),
        InlineKeyboardButton("❌ انصراف", callback_data=no_cb),
    ]])


def parse_button_rows(raw_text: str) -> list:
    """فرمت ورودی: هر خط یک ردیف، دکمه‌ها با کاما جدا: متن۱|id1, متن۲|id2"""
    rows = []
    for line in raw_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        row = []
        for part in line.split(","):
            part = part.strip()
            if "|" not in part:
                raise ValueError(f"فرمت نادرست: «{part}»")
            btext, bid = part.rsplit("|", 1)
            btext, bid = btext.strip(), bid.strip()
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
        [InlineKeyboardButton(btn["text"], callback_data=btn["id"]) for btn in row]
        for row in rows
    ])


def preview_buttons(rows: Optional[list]) -> str:
    if not rows:
        return "ندارد"
    names = [btn["text"] for row in rows for btn in row]
    return "، ".join(names) if names else "ندارد"


def build_welcome_text(user: dict, signup_bonus_given: bool) -> str:
    bonus_line = f"\n🎁 شما {SIGNUP_BONUS} {COIN_SYMBOL} هدیه ثبت‌نام دریافت کردید!\n" if signup_bonus_given else ""
    return (
        "🤖✨ به GramSaz خوش آمدید! ✨\n"
        f"{bonus_line}\n"
        "پلتفرم ساخت ربات تلگرام بدون نیاز به کدنویسی.\n\n"
        "از دکمه‌های زیر شروع کنید:"
    )


def build_account_text(user: dict) -> str:
    return (
        "👤 حساب کاربری\n\n"
        f"🆔 آیدی: {user['uid']}\n"
        f"💰 موجودی: {user.get('coin_balance', 0)} {COIN_SYMBOL}\n"
        f"🤖 تعداد ربات‌ها: {len(user.get('bots_created', []))}/{MAX_FREE_BOTS}\n"
        f"🔗 کد دعوت: {user.get('referral_code','—')}"
    )


def build_mybots_text_kb(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    bots = storage.get_user_bots(uid)
    if not bots:
        return ("🤖 ربات‌های شما\n\nهنوز رباتی نساخته‌اید.",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ ساخت ربات جدید", callback_data="nav:create")],
                    [InlineKeyboardButton("🔙 منوی اصلی", callback_data="nav:home")],
                ]))
    text = "🤖 ربات‌های شما\n\n"
    rows = []
    for b in bots:
        expired = storage.check_subscription_expired(b["bot_id"])
        status = "🔴 منقضی" if expired else ("🆓 آزمایشی" if b.get("is_trial") else "🟢 فعال")
        uname = f"@{b['bot_username']}" if b.get("bot_username") else "—"
        text += f"• {uname}  {status}\n"
        rows.append([InlineKeyboardButton(f"⚙️ مدیریت {uname}", callback_data=f"bot:manage:{b['bot_id']}")])
    if len(bots) < MAX_FREE_BOTS:
        rows.append([InlineKeyboardButton("➕ ساخت ربات جدید", callback_data="nav:create")])
    rows.append([InlineKeyboardButton("🔙 منوی اصلی", callback_data="nav:home")])
    return text, InlineKeyboardMarkup(rows)


def build_bot_detail(bot: dict) -> tuple[str, InlineKeyboardMarkup]:
    bot_id = bot["bot_id"]
    expired = storage.check_subscription_expired(bot_id)
    if expired:
        sub = "🔴 منقضی شده"
    elif bot.get("is_trial"):
        exp = datetime.fromisoformat(bot["subscription_expiry"])
        sub = f"🆓 آزمایشی — تا {exp.strftime('%Y-%m-%d')}"
    else:
        exp = datetime.fromisoformat(bot["subscription_expiry"])
        sub = f"🟢 فعال — تا {exp.strftime('%Y-%m-%d')}"
    stats = bot.get("stats", {})
    text = (
        f"⚙️ مدیریت @{bot.get('bot_username','—')}\n\n"
        f"📊 وضعیت: {'🟢 روشن' if bot.get('is_active') else '🔴 خاموش'}\n"
        f"💳 اشتراک: {sub}\n"
        f"🔧 دستورها: {len(bot.get('commands', {}))}\n"
        f"👥 کاربران: {len(bot.get('bot_users', []))}\n"
        f"📩 پیام دریافتی: {stats.get('messages_received', 0)}"
    )
    toggle_label = "🔴 خاموش کردن" if bot.get("is_active") else "🟢 روشن کردن"
    rows = [
        [InlineKeyboardButton("📝 پیام شروع (/start)", callback_data=f"bot:start:{bot_id}")],
        [InlineKeyboardButton("🔧 دستورهای دلخواه", callback_data=f"bot:commands:{bot_id}")],
        [InlineKeyboardButton("⚡ پاسخ دکمه‌ها", callback_data=f"bot:callbacks:{bot_id}")],
        [InlineKeyboardButton(toggle_label, callback_data=f"bot:toggle:{bot_id}")],
        [InlineKeyboardButton("🗑 حذف ربات", callback_data=f"bot:delete:{bot_id}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="nav:mybots")],
    ]
    return text, InlineKeyboardMarkup(rows)


def build_admin_panel_text() -> str:
    stats = storage.get_platform_stats()
    return (
        "🛠 پنل مدیریت\n\n"
        f"👥 تعداد کاربران: {stats['total_users']}\n"
        f"🤖 تعداد کل ربات‌ها: {stats['total_bots']}\n"
        f"🟢 ربات‌های فعال: {stats['active_bots']}\n\n"
        f"🚫 عضویت اجباری: {'✅ فعال روی ' + FORCE_JOIN_CHANNEL if FORCE_JOIN_CHANNEL else '❌ غیرفعال'}"
    )


HELP_TEXT = (
    "📖 راهنما\n\n"
    "1) «➕ ساخت ربات جدید» بزنید و توکن ربات‌تون رو از @BotFather بگیرید و بفرستید.\n"
    "2) از بخش «🤖 ربات‌های من» → «⚙️ مدیریت» می‌تونید:\n"
    "   • پیام /start ربات‌تون رو تغییر بدید\n"
    "   • دستور دلخواه بسازید (مثلاً /info) با متن و دکمه اختصاصی\n"
    "   • برای دکمه‌ها پاسخ اختصاصی تعریف کنید\n"
    f"3) هر ربات تازه‌ساخته‌شده {TRIAL_DAYS} روز اشتراک آزمایشی رایگان داره."
)


# ════════════════════════════════════════════════════════════════════
# هندلرهای ربات مادر (Main Platform Bot)
# ════════════════════════════════════════════════════════════════════
async def show_main_menu(msg_target, uid: int, edit: bool = False):
    main_flows.end(uid)
    user = storage.get_user(uid)
    signup_bonus_given = False
    if user and not user.get("signup_bonus_claimed"):
        if await storage.claim_signup_bonus(uid):
            signup_bonus_given = True
            user = storage.get_user(uid)
    text = build_welcome_text(user, signup_bonus_given)
    kb = main_menu_kb(uid)
    if edit:
        try:
            await msg_target.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await msg_target.reply_text(text, reply_markup=kb)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await storage.ensure_user(user.id, user.username or "", (user.full_name or "").strip())
    if not await enforce_force_join(ctx.bot, user.id):
        await update.effective_message.reply_text(FORCE_JOIN_TEXT, reply_markup=force_join_kb())
        return
    await show_main_menu(update.effective_message, user.id)


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    main_flows.end(update.effective_user.id)
    await update.effective_message.reply_text("❌ لغو شد.")
    await show_main_menu(update.effective_message, update.effective_user.id)


async def main_message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """پیام‌های متنی آزاد به ربات مادر — یا ادامهٔ یک فلوی فعال، یا نمایش منو."""
    uid = update.effective_user.id
    text = (update.effective_message.text or "").strip()
    await storage.ensure_user(uid, update.effective_user.username or "", (update.effective_user.full_name or "").strip())

    if not await enforce_force_join(ctx.bot, uid):
        await update.effective_message.reply_text(FORCE_JOIN_TEXT, reply_markup=force_join_kb())
        return

    flow = main_flows.get(uid)
    if flow:
        await handle_main_flow_text(update, ctx, uid, text, flow)
        return

    await show_main_menu(update.effective_message, uid)


async def handle_main_flow_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE, uid: int, text: str, flow: dict):
    msg = update.effective_message
    kind, data = flow["kind"], flow["data"]
    stripped = text.strip()

    # ── ساخت ربات: منتظر توکن ──
    if kind == "awaiting_token":
        user_bots = storage.get_user_bots(uid)
        if len(user_bots) >= MAX_FREE_BOTS:
            main_flows.end(uid)
            await msg.reply_text(f"⚠️ حداکثر {MAX_FREE_BOTS} ربات مجاز است.")
            await show_main_menu(msg, uid)
            return
        token = stripped
        if ":" not in token or len(token) < 30:
            await msg.reply_text("❌ توکن نامعتبر است. دوباره بفرستید یا /cancel:")
            return
        try:
            temp_bot = Bot(token=token)
            me = await temp_bot.get_me()
        except InvalidToken:
            await msg.reply_text("❌ توکن معتبر نیست. دوباره بفرستید یا /cancel:")
            return
        bot_rec = await storage.create_bot(uid, token)
        await storage.update_bot_username(bot_rec["bot_id"], me.username or "")
        await factory.start_bot(bot_rec["bot_id"], token)
        main_flows.end(uid)
        await msg.reply_text(
            f"✅ ربات شما با موفقیت ساخته شد!\n\n🤖 @{me.username}\n"
            f"🆓 {TRIAL_DAYS} روز اشتراک آزمایشی فعال شد.\n\n"
            "برای مدیریت، از «🤖 ربات‌های من» استفاده کنید.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🤖 ربات‌های من", callback_data="nav:mybots")]]))
        return

    # ── ویرایش پیام شروع ──
    if kind == "edit_start_text":
        bot_id = data["bot_id"]
        await storage.set_start_msg(bot_id, text)
        main_flows.end(uid)
        await msg.reply_text("✅ پیام شروع ذخیره شد.")
        await show_bot_start_section(msg, bot_id)
        return

    if kind == "edit_start_buttons":
        bot_id = data["bot_id"]
        if stripped.lower() == "/skip":
            await storage.set_start_buttons(bot_id, None)
            main_flows.end(uid)
            await msg.reply_text("✅ دکمه‌ها حذف شدند.")
        else:
            try:
                rows = parse_button_rows(text)
            except ValueError as e:
                await msg.reply_text(f"❌ {e}\nدوباره بفرستید یا /cancel:")
                return
            await storage.set_start_buttons(bot_id, rows)
            main_flows.end(uid)
            await msg.reply_text("✅ دکمه‌ها ذخیره شدند.")
        await show_bot_start_section(msg, bot_id)
        return

    # ── افزودن دستور جدید ──
    if kind == "add_command":
        step = data["step"]
        bot_id = data["bot_id"]
        if step == "name":
            name = stripped.lower().lstrip("/")
            if not name or " " in name:
                await msg.reply_text("❌ نام نامعتبر است. دوباره بفرستید یا /cancel:")
                return
            if name in RESERVED_COMMANDS:
                await msg.reply_text("❌ این نام رزرو شده. نام دیگری بفرستید:")
                return
            existing = storage.get_commands(bot_id)
            if name in existing:
                await msg.reply_text("❌ این دستور از قبل وجود دارد. نام دیگری بفرستید:")
                return
            if len(existing) >= MAX_COMMANDS_PER_BOT:
                main_flows.end(uid)
                await msg.reply_text(f"❌ حداکثر {MAX_COMMANDS_PER_BOT} دستور مجاز است.")
                await show_bot_commands_section(msg, bot_id)
                return
            data["name"] = name
            data["step"] = "text"
            await msg.reply_text(f"✅ نام: /{name}\n\nمتن پاسخ این دستور را بفرستید:")
            return
        if step == "text":
            data["response"] = text
            data["step"] = "buttons"
            await msg.reply_text(
                "🔘 دکمه‌ها را با فرمت زیر بفرستید (هر خط = یک ردیف):\n"
                "متن۱|id1, متن۲|id2\n\nیا /skip برای رد کردن:")
            return
        if step == "buttons":
            rows = None
            if stripped.lower() != "/skip":
                try:
                    rows = parse_button_rows(text)
                except ValueError as e:
                    await msg.reply_text(f"❌ {e}\nدوباره بفرستید یا /skip:")
                    return
            await storage.set_command(bot_id, data["name"], data["response"], rows)
            name = data["name"]
            main_flows.end(uid)
            await msg.reply_text(f"✅ دستور /{name} ذخیره شد.")
            await show_bot_commands_section(msg, bot_id)
            return

    # ── ویرایش متن دستور موجود ──
    if kind == "edit_cmd_text":
        bot_id, name = data["bot_id"], data["name"]
        cmd = storage.get_commands(bot_id).get(name, {})
        await storage.set_command(bot_id, name, text, cmd.get("buttons"))
        main_flows.end(uid)
        await msg.reply_text(f"✅ متن دستور /{name} به‌روز شد.")
        await show_cmd_detail(msg, bot_id, name)
        return

    if kind == "edit_cmd_buttons":
        bot_id, name = data["bot_id"], data["name"]
        cmd = storage.get_commands(bot_id).get(name, {})
        rows = None
        if stripped.lower() != "/skip":
            try:
                rows = parse_button_rows(text)
            except ValueError as e:
                await msg.reply_text(f"❌ {e}\nدوباره بفرستید یا /skip:")
                return
        await storage.set_command(bot_id, name, cmd.get("response", ""), rows)
        main_flows.end(uid)
        await msg.reply_text(f"✅ دکمه‌های دستور /{name} به‌روز شدند.")
        await show_cmd_detail(msg, bot_id, name)
        return

    # ── افزودن پاسخ دکمه سراسری (callback) ──
    if kind == "add_callback":
        step = data["step"]
        bot_id = data["bot_id"]
        if step == "id":
            cb_id = stripped
            if not cb_id or " " in cb_id:
                await msg.reply_text("❌ آیدی نامعتبر. دوباره بفرستید یا /cancel:")
                return
            existing = storage.get_callbacks(bot_id)
            if cb_id in existing:
                await msg.reply_text("❌ این آیدی قبلاً وجود دارد. آیدی دیگری:")
                return
            if len(existing) >= MAX_CALLBACKS_PER_BOT:
                main_flows.end(uid)
                await msg.reply_text(f"❌ حداکثر {MAX_CALLBACKS_PER_BOT} مجاز است.")
                await show_bot_callbacks_section(msg, bot_id)
                return
            data["cb_id"] = cb_id
            data["step"] = "text"
            await msg.reply_text("✅ متن پاسخ این دکمه را بفرستید:")
            return
        if step == "text":
            await storage.set_callback(bot_id, data["cb_id"], text)
            main_flows.end(uid)
            await msg.reply_text(f"✅ پاسخ دکمه «{data['cb_id']}» ذخیره شد.")
            await show_bot_callbacks_section(msg, bot_id)
            return

    if kind == "edit_callback_text":
        bot_id, cb_id = data["bot_id"], data["cb_id"]
        await storage.set_callback(bot_id, cb_id, text)
        main_flows.end(uid)
        await msg.reply_text("✅ پاسخ دکمه به‌روز شد.")
        await show_bot_callbacks_section(msg, bot_id)
        return


# ---- بخش‌های نمایشی مدیریت هر ربات (پیام جدید، نه ادیت) ----
async def show_bot_start_section(msg, bot_id: str):
    b = storage.get_bot(bot_id)
    if not b:
        return
    text = (
        f"📝 پیام شروع (/start):\n{b.get('start_msg','')}\n\n"
        f"🔘 دکمه‌ها: {preview_buttons(b.get('start_buttons'))}"
    )
    await msg.reply_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ ویرایش متن", callback_data=f"bot:start_edit_text:{bot_id}")],
        [InlineKeyboardButton("🔘 ویرایش دکمه‌ها", callback_data=f"bot:start_edit_buttons:{bot_id}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")],
    ]))


async def show_bot_commands_section(msg, bot_id: str):
    cmds = storage.get_commands(bot_id)
    rows = [[InlineKeyboardButton(f"/{name}", callback_data=f"bot:cmd_detail:{bot_id}:{name}")] for name in cmds]
    rows.append([InlineKeyboardButton("➕ دستور جدید", callback_data=f"bot:cmd_add:{bot_id}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")])
    text = f"🔧 دستورهای دلخواه ({len(cmds)}):\n" + ("روی هر دستور بزنید:" if cmds else "هنوز دستوری نساختید.")
    await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def show_cmd_detail(msg, bot_id: str, name: str):
    cmd = storage.get_commands(bot_id).get(name)
    if not cmd:
        await show_bot_commands_section(msg, bot_id)
        return
    text = (
        f"🔧 دستور: /{name}\n\n"
        f"💬 پاسخ:\n{cmd.get('response','')}\n\n"
        f"🔘 دکمه‌ها: {preview_buttons(cmd.get('buttons'))}"
    )
    await msg.reply_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ ویرایش متن", callback_data=f"bot:cmd_edit_text:{bot_id}:{name}"),
         InlineKeyboardButton("🔘 ویرایش دکمه‌ها", callback_data=f"bot:cmd_edit_buttons:{bot_id}:{name}")],
        [InlineKeyboardButton("🗑 حذف دستور", callback_data=f"bot:cmd_delete:{bot_id}:{name}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:commands:{bot_id}")],
    ]))


async def show_bot_callbacks_section(msg, bot_id: str):
    cbs = storage.get_callbacks(bot_id)
    rows = [[InlineKeyboardButton(f"⚡ {cid}", callback_data=f"bot:cb_detail:{bot_id}:{cid}")] for cid in cbs]
    rows.append([InlineKeyboardButton("➕ پاسخ جدید", callback_data=f"bot:cb_add:{bot_id}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")])
    text = (
        f"⚡ پاسخ دکمه‌های سراسری ({len(cbs)}):\n"
        "وقتی کاربر روی دکمه‌ای با این آیدی کلیک کنه (مثلاً داخل پیام شروع یا یه دستور)، "
        "متن اینجا نمایش داده می‌شه.\n\n" + ("روی هر آیتم بزنید:" if cbs else "هنوز آیتمی نساختید.")
    )
    await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def show_cb_detail(msg, bot_id: str, cb_id: str):
    resp = storage.get_callbacks(bot_id).get(cb_id)
    if resp is None:
        await show_bot_callbacks_section(msg, bot_id)
        return
    text = f"⚡ دکمه: {cb_id}\n\nپاسخ:\n{resp}"
    await msg.reply_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ ویرایش", callback_data=f"bot:cb_edit:{bot_id}:{cb_id}")],
        [InlineKeyboardButton("🗑 حذف", callback_data=f"bot:cb_delete:{bot_id}:{cb_id}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:callbacks:{bot_id}")],
    ]))


# ════════════════════════════════════════════════════════════════════
# هندلر دکمه‌های ربات مادر (CallbackQuery)
# ════════════════════════════════════════════════════════════════════
async def main_callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    uid = q.from_user.id
    await storage.ensure_user(uid, q.from_user.username or "", (q.from_user.full_name or "").strip())

    # ── عضویت اجباری ──
    if data == "fj:check":
        if await enforce_force_join(ctx.bot, uid):
            await q.answer("✅ تایید شد!")
            await show_main_menu(q.message, uid, edit=True)
        else:
            await q.answer("❌ هنوز عضو نشدید!", show_alert=True)
        return

    if not await enforce_force_join(ctx.bot, uid):
        await q.answer()
        try:
            await q.edit_message_text(FORCE_JOIN_TEXT, reply_markup=force_join_kb())
        except Exception:
            pass
        return

    await q.answer()

    # ── ناوبری ──
    if data == "nav:home":
        await show_main_menu(q.message, uid, edit=True)
        return

    if data == "nav:mybots":
        text, kb = build_mybots_text_kb(uid)
        await q.edit_message_text(text, reply_markup=kb)
        return

    if data == "nav:account":
        user = storage.get_user(uid)
        await q.edit_message_text(build_account_text(user), reply_markup=back_kb("nav:home"))
        return

    if data == "nav:help":
        await q.edit_message_text(HELP_TEXT, reply_markup=back_kb("nav:home"))
        return

    if data == "admin:panel":
        if not is_admin(uid):
            await q.edit_message_text("❌ دسترسی ندارید.", reply_markup=back_kb("nav:home"))
            return
        await q.edit_message_text(build_admin_panel_text(), reply_markup=back_kb("nav:home"))
        return

    if data == "nav:create":
        user_bots = storage.get_user_bots(uid)
        if len(user_bots) >= MAX_FREE_BOTS:
            await q.edit_message_text(
                f"⚠️ شما در حال حاضر {len(user_bots)}/{MAX_FREE_BOTS} ربات دارید.",
                reply_markup=back_kb("nav:mybots"))
            return
        main_flows.start(uid, "awaiting_token")
        await q.edit_message_text(
            "🔑 توکن ربات‌تون رو از @BotFather بگیرید و همینجا بفرستید:\n(/cancel برای انصراف)")
        return

    # ── مدیریت یک ربات ──
    if data.startswith("bot:manage:"):
        bot_id = data.split(":")[2]
        b = storage.get_bot(bot_id)
        if not b or b["owner_uid"] != uid:
            await q.edit_message_text("❌ دسترسی ندارید.", reply_markup=back_kb("nav:mybots"))
            return
        text, kb = build_bot_detail(b)
        await q.edit_message_text(text, reply_markup=kb)
        return

    if data.startswith("bot:toggle:"):
        bot_id = data.split(":")[2]
        b = storage.get_bot(bot_id)
        if not b or b["owner_uid"] != uid:
            return
        new_state = not b.get("is_active", True)
        await storage.set_bot_active(bot_id, new_state)
        if new_state:
            await factory.start_bot(bot_id, b["bot_token"])
        else:
            await factory.stop_bot(bot_id)
        b = storage.get_bot(bot_id)
        text, kb = build_bot_detail(b)
        await q.edit_message_text(text, reply_markup=kb)
        return

    if data.startswith("bot:delete:") and not data.startswith("bot:delete_confirm:"):
        bot_id = data.split(":")[2]
        b = storage.get_bot(bot_id)
        if not b or b["owner_uid"] != uid:
            return
        await q.edit_message_text(
            f"❗️ حذف @{b.get('bot_username','')}؟ غیرقابل بازگشت است.",
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
        await q.edit_message_text("🗑 ربات حذف شد.\n\n" + text, reply_markup=kb)
        return

    # ── پیام شروع ──
    if data.startswith("bot:start_edit_text:"):
        bot_id = data.split(":")[2]
        main_flows.start(uid, "edit_start_text", bot_id=bot_id)
        await q.edit_message_text("✏️ متن جدید پیام شروع را بفرستید:\n(/cancel برای انصراف)")
        return

    if data.startswith("bot:start_edit_buttons:"):
        bot_id = data.split(":")[2]
        main_flows.start(uid, "edit_start_buttons", bot_id=bot_id)
        await q.edit_message_text(
            "🔘 دکمه‌ها را بفرستید (هر خط = یک ردیف):\nمتن۱|id1, متن۲|id2\n"
            "یا /skip برای حذف دکمه‌ها\n(/cancel برای انصراف)")
        return

    if data.startswith("bot:start:"):
        bot_id = data.split(":")[2]
        b = storage.get_bot(bot_id)
        if not b or b["owner_uid"] != uid:
            return
        text = (
            f"📝 پیام شروع (/start):\n{b.get('start_msg','')}\n\n"
            f"🔘 دکمه‌ها: {preview_buttons(b.get('start_buttons'))}"
        )
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ ویرایش متن", callback_data=f"bot:start_edit_text:{bot_id}")],
            [InlineKeyboardButton("🔘 ویرایش دکمه‌ها", callback_data=f"bot:start_edit_buttons:{bot_id}")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")],
        ]))
        return

    # ── دستورهای دلخواه ──
    if data.startswith("bot:commands:"):
        bot_id = data.split(":")[2]
        cmds = storage.get_commands(bot_id)
        rows = [[InlineKeyboardButton(f"/{name}", callback_data=f"bot:cmd_detail:{bot_id}:{name}")] for name in cmds]
        rows.append([InlineKeyboardButton("➕ دستور جدید", callback_data=f"bot:cmd_add:{bot_id}")])
        rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")])
        text = f"🔧 دستورهای دلخواه ({len(cmds)}):\n" + ("روی هر دستور بزنید:" if cmds else "هنوز دستوری نساختید.")
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("bot:cmd_add:"):
        bot_id = data.split(":")[2]
        main_flows.start(uid, "add_command", bot_id=bot_id, step="name")
        await q.edit_message_text("➕ نام دستور جدید را بدون / بفرستید (مثلاً info):\n(/cancel برای انصراف)")
        return

    if data.startswith("bot:cmd_detail:"):
        _, _, bot_id, name = data.split(":", 3)
        cmd = storage.get_commands(bot_id).get(name)
        if not cmd:
            return
        text = (
            f"🔧 دستور: /{name}\n\n💬 پاسخ:\n{cmd.get('response','')}\n\n"
            f"🔘 دکمه‌ها: {preview_buttons(cmd.get('buttons'))}"
        )
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ ویرایش متن", callback_data=f"bot:cmd_edit_text:{bot_id}:{name}"),
             InlineKeyboardButton("🔘 ویرایش دکمه‌ها", callback_data=f"bot:cmd_edit_buttons:{bot_id}:{name}")],
            [InlineKeyboardButton("🗑 حذف دستور", callback_data=f"bot:cmd_delete:{bot_id}:{name}")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:commands:{bot_id}")],
        ]))
        return

    if data.startswith("bot:cmd_edit_text:"):
        _, _, bot_id, name = data.split(":", 3)
        main_flows.start(uid, "edit_cmd_text", bot_id=bot_id, name=name)
        await q.edit_message_text(f"✏️ متن جدید برای /{name}:\n(/cancel برای انصراف)")
        return

    if data.startswith("bot:cmd_edit_buttons:"):
        _, _, bot_id, name = data.split(":", 3)
        main_flows.start(uid, "edit_cmd_buttons", bot_id=bot_id, name=name)
        await q.edit_message_text(
            f"🔘 دکمه‌های جدید برای /{name} را بفرستید:\nمتن۱|id1, متن۲|id2\n"
            "یا /skip برای حذف\n(/cancel برای انصراف)")
        return

    if data.startswith("bot:cmd_delete:") and "confirm" not in data:
        _, _, bot_id, name = data.split(":", 3)
        await q.edit_message_text(
            f"🗑 حذف دستور /{name}؟",
            reply_markup=confirm_kb(f"bot:cmd_delete_confirm:{bot_id}:{name}", f"bot:cmd_detail:{bot_id}:{name}"))
        return

    if data.startswith("bot:cmd_delete_confirm:"):
        parts = data.split(":", 3)
        bot_id, name = parts[2], parts[3]
        await storage.delete_command(bot_id, name)
        cmds = storage.get_commands(bot_id)
        rows = [[InlineKeyboardButton(f"/{n}", callback_data=f"bot:cmd_detail:{bot_id}:{n}")] for n in cmds]
        rows.append([InlineKeyboardButton("➕ دستور جدید", callback_data=f"bot:cmd_add:{bot_id}")])
        rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")])
        text = f"🔧 دستورهای دلخواه ({len(cmds)}):\n" + ("روی هر دستور بزنید:" if cmds else "هنوز دستوری نساختید.")
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
        return

    # ── پاسخ دکمه‌های سراسری ──
    if data.startswith("bot:callbacks:"):
        bot_id = data.split(":")[2]
        cbs = storage.get_callbacks(bot_id)
        rows = [[InlineKeyboardButton(f"⚡ {cid}", callback_data=f"bot:cb_detail:{bot_id}:{cid}")] for cid in cbs]
        rows.append([InlineKeyboardButton("➕ پاسخ جدید", callback_data=f"bot:cb_add:{bot_id}")])
        rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")])
        text = f"⚡ پاسخ دکمه‌های سراسری ({len(cbs)}):\n" + ("روی هر آیتم بزنید:" if cbs else "هنوز آیتمی نساختید.")
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("bot:cb_add:"):
        bot_id = data.split(":")[2]
        main_flows.start(uid, "add_callback", bot_id=bot_id, step="id")
        await q.edit_message_text(
            "➕ آیدی دکمه را بفرستید (همون callback_data که در دکمه‌های /start یا دستورها استفاده کردید):\n"
            "(/cancel برای انصراف)")
        return

    if data.startswith("bot:cb_detail:"):
        _, _, bot_id, cid = data.split(":", 3)
        resp = storage.get_callbacks(bot_id).get(cid)
        if resp is None:
            return
        await q.edit_message_text(f"⚡ دکمه: {cid}\n\nپاسخ:\n{resp}", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ ویرایش", callback_data=f"bot:cb_edit:{bot_id}:{cid}")],
            [InlineKeyboardButton("🗑 حذف", callback_data=f"bot:cb_delete:{bot_id}:{cid}")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:callbacks:{bot_id}")],
        ]))
        return

    if data.startswith("bot:cb_edit:"):
        _, _, bot_id, cid = data.split(":", 3)
        main_flows.start(uid, "edit_callback_text", bot_id=bot_id, cb_id=cid)
        await q.edit_message_text(f"✏️ پاسخ جدید برای «{cid}»:\n(/cancel برای انصراف)")
        return

    if data.startswith("bot:cb_delete:") and "confirm" not in data:
        _, _, bot_id, cid = data.split(":", 3)
        await q.edit_message_text(
            f"🗑 حذف پاسخ دکمه «{cid}»؟",
            reply_markup=confirm_kb(f"bot:cb_delete_confirm:{bot_id}:{cid}", f"bot:cb_detail:{bot_id}:{cid}"))
        return

    if data.startswith("bot:cb_delete_confirm:"):
        parts = data.split(":", 3)
        bot_id, cid = parts[2], parts[3]
        await storage.delete_callback(bot_id, cid)
        cbs = storage.get_callbacks(bot_id)
        rows = [[InlineKeyboardButton(f"⚡ {c}", callback_data=f"bot:cb_detail:{bot_id}:{c}")] for c in cbs]
        rows.append([InlineKeyboardButton("➕ پاسخ جدید", callback_data=f"bot:cb_add:{bot_id}")])
        rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"bot:manage:{bot_id}")])
        text = f"⚡ پاسخ دکمه‌های سراسری ({len(cbs)}):\n" + ("روی هر آیتم بزنید:" if cbs else "هنوز آیتمی نساختید.")
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
        return


# ════════════════════════════════════════════════════════════════════
# هندلرهای ربات‌های زیرمجموعه (Sub-Bot) — فقط اجرا، بدون منطق نویسندگی
# ════════════════════════════════════════════════════════════════════
async def handle_subbot_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE, bot_id: str):
    uid = update.effective_user.id
    b = storage.get_bot(bot_id)
    if not b:
        return
    await storage.update_bot_stats(bot_id, received=1)
    await storage.add_bot_user(bot_id, uid)
    if storage.check_subscription_expired(bot_id):
        await update.effective_message.reply_text("⚠️ اشتراک این ربات منقضی شده است.")
        return
    kb = build_inline_from_rows(b.get("start_buttons"))
    await update.effective_message.reply_text(b.get("start_msg", "سلام 👋"), reply_markup=kb)
    await storage.update_bot_stats(bot_id, sent=1)


async def handle_subbot_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE, bot_id: str):
    uid = update.effective_user.id
    text = (update.effective_message.text or "").strip()
    b = storage.get_bot(bot_id)
    if not b:
        return
    await storage.update_bot_stats(bot_id, received=1)
    await storage.add_bot_user(bot_id, uid)
    if storage.check_subscription_expired(bot_id):
        await update.effective_message.reply_text("⚠️ اشتراک این ربات منقضی شده است.")
        return
    cmd_name = text.lstrip("/").split()[0].lower() if text.startswith("/") else ""
    commands = b.get("commands", {})
    if cmd_name in commands:
        cmd = commands[cmd_name]
        kb = build_inline_from_rows(cmd.get("buttons"))
        await update.effective_message.reply_text(cmd.get("response", ""), reply_markup=kb)
        await storage.update_bot_stats(bot_id, sent=1)


async def handle_subbot_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE, bot_id: str):
    q = update.callback_query
    await q.answer()
    cbs = storage.get_callbacks(bot_id)
    if q.data in cbs:
        await q.message.reply_text(cbs[q.data])
        await storage.update_bot_stats(bot_id, sent=1)


# ════════════════════════════════════════════════════════════════════
# استارتاپ / شات‌داون
# ════════════════════════════════════════════════════════════════════
async def on_startup(app: Application):
    await storage.load()
    await factory.start_all_active()
    asyncio.create_task(storage.autosave_loop())
    log.info(f"🟢 GramSaz آماده — {len(factory._running)} ربات فعال")


async def on_shutdown(app: Application):
    await factory.stop_all()
    await storage.save(force=True)


# ════════════════════════════════════════════════════════════════════
# main
# ════════════════════════════════════════════════════════════════════
def main():
    app = (
        Application.builder()
        .token(PLATFORM_BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(main_callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, main_message_handler))

    if WEBHOOK_URL:
        log.info(f"🌐 اجرا با وب‌هوک: {WEBHOOK_URL}/{PLATFORM_BOT_TOKEN}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=PLATFORM_BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{PLATFORM_BOT_TOKEN}",
            drop_pending_updates=True,
        )
    else:
        log.info("📡 اجرا با polling (برای وب‌هوک، WEBHOOK_URL رو در env ست کن)")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
