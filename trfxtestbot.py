#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ExchangeBot v6.0 — نسخه بهینه و بینقص
✅ باگ تشخیص لایک کاملاً فیکس شد
✅ pending_like موقع نمایش کارت ثبت میشه (نه موقع کلیک URL)
✅ مقایسه chat_id با نرمالسازی صحیح
✅ فیلد صحیح new_reaction (نه new_reaction_type)
✅ کارتهای حرفهای و UI تمیز
"""
import os, sys, re, time, json, signal, atexit, logging, hashlib
import asyncio, threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List
from flask import Flask, request

from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                       ReplyKeyboardMarkup, KeyboardButton)
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.error import BadRequest, Forbidden
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                           ContextTypes, filters, MessageHandler, MessageReactionHandler)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO, stream=sys.stdout
)
log = logging.getLogger("ExchangeBot")

# ═══════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════
BOT_TOKEN          = os.environ.get("BOT_TOKEN", "8649622614:AAFs3Hoz9EbjBGiFeua3wt8j_XJFRbtMNxA")
PORT               = int(os.environ.get("PORT", 5000))
ADMIN_IDS          = {int(x) for x in os.environ.get("ADMIN_IDS", "605611554").split(",") if x.strip()}
FORCE_JOIN_CHANNEL = os.environ.get("FORCE_JOIN_CHANNEL", "@testchanellaz")

COIN              = "💎"
SIGNUP_BONUS      = 100
REFERRAL_BONUS    = 50
DAILY_REWARD      = 10
JOIN_REWARD       = 5
JOIN_COST_MULT    = 2
REACTION_REWARD   = 2
REACTION_COST_MULT= 2
ALLOWED_REACTIONS = {"👍", "❤", "🔥", "👏", "🎉", "🤩"}
IRAN_TZ           = timezone(timedelta(hours=3, minutes=30))
PENDING_TIMEOUT   = 600          # ثانیه — ۱۰ دقیقه فرصت لایک
MAX_DESC_LENGTH   = 200

def now_ir():
    return datetime.now(IRAN_TZ)

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

# ═══════════════════════════════════════════
#  CLOUD STORAGE
# ═══════════════════════════════════════════
try:
    from pymongo import MongoClient
    MONGODB_AVAILABLE = True
except ImportError:
    MONGODB_AVAILABLE = False

class CloudStorage:
    """ذخیرهسازی MongoDB با fallback حافظه"""

    _EMPTY = lambda: {"users": {}, "orders": [], "tasks_completed": [],
                      "pending_likes": [], "transactions": [], "last_updated": None}

    def __init__(self):
        self._uri        = os.environ.get("MONGODB_URI", "")
        self._client     = None
        self._collection = None
        self._lock       = threading.Lock()
        self._dirty      = False
        self._data       = CloudStorage._EMPTY()

        if self._uri and MONGODB_AVAILABLE:
            try:
                self._client = MongoClient(self._uri, serverSelectionTimeoutMS=10_000)
                self._client.admin.command("ping")
                self._collection = self._client.get_database()["exchange_bot_data"]
                log.info("MongoDB ✅ متصل شد")
            except Exception as e:
                log.error(f"MongoDB اتصال ناموفق: {e}")

    # ── پایه ──────────────────────────────
    def load(self):
        if not self._collection:
            return
        try:
            doc = self._collection.find_one({"_id": "main"})
            if doc:
                doc.pop("_id", None)
                with self._lock:
                    for k, v in doc.items():
                        self._data[k] = v
                    for k, default in [("users", {}), ("orders", []),
                                       ("tasks_completed", []), ("pending_likes", []),
                                       ("transactions", [])]:
                        self._data.setdefault(k, default)
        except Exception as e:
            log.error(f"Load error: {e}")

    def save(self, force=False):
        if not self._collection:
            return
        if not force and not self._dirty:
            return
        with self._lock:
            self._data["last_updated"] = now_ir().isoformat()
            payload = {"_id": "main", **self._data}
            try:
                self._collection.replace_one({"_id": "main"}, payload, upsert=True)
                self._dirty = False
            except Exception as e:
                log.error(f"Save error: {e}")

    # ── کاربران ───────────────────────────
    def ensure_user(self, uid: int, username="", full_name=""):
        key = str(uid)
        with self._lock:
            is_new = key not in self._data["users"]
            if is_new:
                self._data["users"][key] = {
                    "uid": uid,
                    "username": username or "",
                    "full_name": full_name or "",
                    "balance": SIGNUP_BONUS,
                    "total_earned": SIGNUP_BONUS,
                    "referral_code": hashlib.md5(f"{uid}{time.time()}".encode()).hexdigest()[:8],
                    "referred_by": None,
                    "created_at": now_ir().isoformat(),
                    "last_daily_claim": None,
                    "tasks_done": 0,
                }
                self._dirty = True
            else:
                u = self._data["users"][key]
                if username and u.get("username") != username:
                    u["username"] = username; self._dirty = True
                if full_name and u.get("full_name") != full_name:
                    u["full_name"] = full_name; self._dirty = True
            return dict(self._data["users"][key]), is_new

    def get_user(self, uid: int):
        with self._lock:
            u = self._data["users"].get(str(uid))
            return dict(u) if u else None

    def get_all_users(self):
        with self._lock:
            return [{"uid_str": k, **v} for k, v in self._data["users"].items()]

    # ── مالی ──────────────────────────────
    def add_coin(self, uid: int, amount: int, reason: str) -> bool:
        key = str(uid)
        with self._lock:
            if key not in self._data["users"]:
                return False
            self._data["users"][key]["balance"]      += amount
            self._data["users"][key]["total_earned"] += amount
            self._data["transactions"].append({
                "uid": uid, "amount": amount,
                "reason": reason, "timestamp": now_ir().isoformat()
            })
            self._data["transactions"] = self._data["transactions"][-2000:]
            self._dirty = True
            return True

    def deduct_coin(self, uid: int, amount: int, reason: str) -> bool:
        key = str(uid)
        with self._lock:
            u = self._data["users"].get(key)
            if not u or u.get("balance", 0) < amount:
                return False
            u["balance"] -= amount
            self._data["transactions"].append({
                "uid": uid, "amount": -amount,
                "reason": reason, "timestamp": now_ir().isoformat()
            })
            self._dirty = True
            return True

    # ── سفارشها ─────────────────────────
    def create_order(self, uid, order_type, target, count, cost_per,
                     post_id=None, chat_id=None, description=""):
        total = cost_per * count
        if not self.deduct_coin(uid, total, f"create_{order_type}"):
            return None
        order_id = hashlib.md5(f"{uid}{target}{time.time()}".encode()).hexdigest()[:12]
        with self._lock:
            order = {
                "order_id":       order_id,
                "owner_uid":      uid,
                "type":           order_type,
                "target":         target,
                "post_id":        post_id,
                "chat_id":        chat_id,          # int یا None
                "total_count":    count,
                "remaining_count":count,
                "cost_per_unit":  cost_per,
                "description":    description[:MAX_DESC_LENGTH],
                "created_at":     now_ir().isoformat(),
                "status":         "active",
            }
            self._data["orders"].append(order)
            self._dirty = True
            log.info(f"✅ Order {order_id} | type={order_type} | chat={chat_id} | post={post_id}")
            return dict(order)

    def get_active_orders(self, order_type=None):
        with self._lock:
            return [dict(o) for o in self._data["orders"]
                    if o.get("status") == "active" and o.get("remaining_count", 0) > 0
                    and (not order_type or o.get("type") == order_type)]

    def get_order_by_id(self, order_id: str):
        with self._lock:
            return next((dict(o) for o in self._data["orders"]
                         if o["order_id"] == order_id), None)

    def get_user_orders(self, uid: int):
        with self._lock:
            return [dict(o) for o in self._data["orders"] if o.get("owner_uid") == uid]

    def is_task_done(self, order_id: str, worker_uid: int) -> bool:
        key = f"{order_id}_{worker_uid}"
        with self._lock:
            return key in self._data["tasks_completed"]

    def complete_order_unit(self, order_id: str, worker_uid: int):
        """تکمیل یک واحد از سفارش — برای join و reaction هر دو"""
        with self._lock:
            order = next((o for o in self._data["orders"]
                          if o["order_id"] == order_id), None)
            if not order or order["status"] != "active" or order["remaining_count"] <= 0:
                return False, "❌ تسک نامعتبر یا تمامشده"
            if order["owner_uid"] == worker_uid:
                return False, "⛔ نمیتوانید تسک خودتان را انجام دهید"
            key = f"{order_id}_{worker_uid}"
            if key in self._data["tasks_completed"]:
                return False, "✅ قبلاً انجام دادهاید"
            order["remaining_count"] -= 1
            if order["remaining_count"] <= 0:
                order["status"] = "completed"
            self._data["tasks_completed"].append(key)
            u = self._data["users"].get(str(worker_uid))
            if u:
                u["tasks_done"] = u.get("tasks_done", 0) + 1
            self._dirty = True

        reward = JOIN_REWARD if order["type"] == "join" else REACTION_REWARD
        self.add_coin(worker_uid, reward, f"task_{order_id}")
        return True, reward

    # ── pending_likes ─────────────────────
    def add_pending_like(self, order_id: str, worker_uid: int):
        """ثبت انتظار لایک — هنگام نمایش کارت لایک صدا زده میشه"""
        with self._lock:
            # حذف نسخه قبلی همین ترکیب
            self._data["pending_likes"] = [
                p for p in self._data["pending_likes"]
                if not (p["order_id"] == order_id and p["worker_uid"] == worker_uid)
            ]
            self._data["pending_likes"].append({
                "order_id":   order_id,
                "worker_uid": worker_uid,
                "timestamp":  time.time(),
                "expires":    time.time() + PENDING_TIMEOUT,
            })
            self._dirty = True
        log.info(f"📌 Pending like: order={order_id} user={worker_uid}")

    def consume_pending_like(self, order_id: str, worker_uid: int) -> bool:
        """حذف pending پس از تایید لایک"""
        with self._lock:
            before = len(self._data["pending_likes"])
            self._data["pending_likes"] = [
                p for p in self._data["pending_likes"]
                if not (p["order_id"] == order_id and p["worker_uid"] == worker_uid)
            ]
            changed = len(self._data["pending_likes"]) < before
            if changed:
                self._dirty = True
            return changed

    def get_pending_for_user(self, worker_uid: int):
        now = time.time()
        with self._lock:
            return [dict(p) for p in self._data["pending_likes"]
                    if p["worker_uid"] == worker_uid and p["expires"] > now]

    def find_pending_by_post(self, worker_uid: int, chat_id: int, message_id: int):
        """
        ✅ کلیدیترین تابع فیکسشده:
        پیدا کردن pending بر اساس (user, chat, message)
        chat_id را نرمالسازی میکنه — هم عدد مستقیم هم فرم -100xxx
        """
        now = time.time()
        with self._lock:
            for p in self._data["pending_likes"]:
                if p["worker_uid"] != worker_uid or p["expires"] <= now:
                    continue
                order = next((o for o in self._data["orders"]
                              if o["order_id"] == p["order_id"]), None)
                if not order or order.get("status") != "active":
                    continue
                o_chat = order.get("chat_id")
                o_post = order.get("post_id")
                if o_chat is None or o_post is None:
                    continue
                # نرمالسازی: برخی کانالها با -100 ذخیره میشن
                if _normalize_chat_id(int(o_chat)) == _normalize_chat_id(int(chat_id)) \
                        and int(o_post) == int(message_id):
                    return dict(p)
        return None

    def cleanup_expired_pendings(self):
        now = time.time()
        with self._lock:
            before = len(self._data["pending_likes"])
            self._data["pending_likes"] = [
                p for p in self._data["pending_likes"] if p["expires"] > now
            ]
            if len(self._data["pending_likes"]) < before:
                self._dirty = True

    # ── روزانه + رفرال ────────────────────
    def can_claim_daily(self, uid: int) -> bool:
        u = self.get_user(uid)
        if not u or not u.get("last_daily_claim"):
            return True
        try:
            return (now_ir() - datetime.fromisoformat(u["last_daily_claim"])).days >= 1
        except:
            return True

    def claim_daily(self, uid: int) -> bool:
        if not self.can_claim_daily(uid):
            return False
        with self._lock:
            k = str(uid)
            if k in self._data["users"]:
                self._data["users"][k]["last_daily_claim"] = now_ir().isoformat()
                self._dirty = True
        return self.add_coin(uid, DAILY_REWARD, "daily")

    def find_uid_by_referral(self, ref_code: str):
        with self._lock:
            for uid_str, u in self._data["users"].items():
                if u.get("referral_code") == ref_code:
                    return int(uid_str)
        return None

    def apply_referral(self, new_uid: int, ref_uid: int) -> bool:
        if new_uid == ref_uid:
            return False
        with self._lock:
            nk, rk = str(new_uid), str(ref_uid)
            users = self._data["users"]
            if nk not in users or rk not in users:
                return False
            if users[nk].get("referred_by"):
                return False
            users[nk]["referred_by"] = rk
            self._dirty = True
            return True

    def get_stats(self):
        with self._lock:
            return {
                "total_users":   len(self._data["users"]),
                "active_orders": sum(1 for o in self._data["orders"]
                                     if o.get("status") == "active"),
            }


# ── utils ──────────────────────────────────
def _normalize_chat_id(chat_id: int) -> int:
    """
    کانالهای عمومی تلگرام گاهی به شکل -1001234567890 و گاهی 1234567890 ذخیره میشن.
    این تابع هر دو رو به فرم مطلق تبدیل میکنه تا مقایسه درست باشه.
    """
    s = str(abs(chat_id))
    if s.startswith("100"):
        s = s[3:]
    return int(s)


cloud = CloudStorage()


# ═══════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════
async def check_membership(bot, uid: int) -> bool:
    if not FORCE_JOIN_CHANNEL:
        return True
    try:
        m = await bot.get_chat_member(FORCE_JOIN_CHANNEL, uid)
        return m.status not in ("left", "kicked", "banned")
    except:
        return True

async def check_bot_admin(bot, channel: str) -> bool:
    try:
        me = await bot.get_me()
        m  = await bot.get_chat_member(channel, me.id)
        return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except:
        return False


def kb_join():
    if not FORCE_JOIN_CHANNEL:
        return None
    ch = FORCE_JOIN_CHANNEL.lstrip("@")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/{ch}")],
        [InlineKeyboardButton("✅ عضو شدم", callback_data="check_join")],
    ])

def kb_main(uid=0):
    rows = [
        [KeyboardButton("🎯 کسب درآمد"), KeyboardButton("➕ ثبت سفارش")],
        [KeyboardButton("📦 سفارشهای من"), KeyboardButton("👥 زیرمجموعه")],
        [KeyboardButton("👤 پروفایل"), KeyboardButton("📖 راهنما")],
    ]
    if uid and is_admin(uid):
        rows.append([KeyboardButton("👑 پنل ادمین")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)

REPLY_MAP = {
    "🎯 کسب درآمد":    "menu:earn",
    "➕ ثبت سفارش":    "menu:new_order",
    "📦 سفارشهای من": "menu:my_orders",
    "👥 زیرمجموعه":   "menu:referral",
    "👤 پروفایل":      "menu:profile",
    "📖 راهنما":       "menu:help",
    "👑 پنل ادمین":   "admin:panel",
}


# ═══════════════════════════════════════════
#  TEXT BUILDERS
# ═══════════════════════════════════════════
def build_welcome(uid: int) -> str:
    u = cloud.get_user(uid)
    bal = u.get("balance", 0) if u else 0
    return (
        f"🎉 <b>به ExchangeBot خوش اومدی!</b>\n\n"
        f"💰 <b>موجودی:</b> <code>{bal} {COIN}</code>\n\n"
        f"🎯 کسب درآمد با عضویت و لایک\n"
        f"➕ ثبت سفارش برای کانال خودت\n"
        f"👥 رفرال و دریافت <b>{REFERRAL_BONUS} {COIN}</b> جایزه"
    )

def build_profile(uid: int) -> str:
    u = cloud.get_user(uid)
    if not u:
        return "❌ کاربر یافت نشد."
    refs = sum(1 for x in cloud.get_all_users() if x.get("referred_by") == str(uid))
    name = u.get("full_name") or u.get("username") or "—"
    daily = "✅ قابل دریافت" if cloud.can_claim_daily(uid) else "⏳ فردا"
    return (
        f"👤 <b>پروفایل</b>\n\n"
        f"📛 <b>نام:</b> {name}\n"
        f"🆔 <b>آیدی:</b> <code>{uid}</code>\n\n"
        f"💰 <b>موجودی:</b> <code>{u.get('balance', 0)} {COIN}</code>\n"
        f"📈 <b>کل درآمد:</b> <code>{u.get('total_earned', 0)} {COIN}</code>\n"
        f"✅ <b>تسکهای انجام شده:</b> {u.get('tasks_done', 0)}\n"
        f"👥 <b>زیرمجموعهها:</b> {refs}\n"
        f"🎁 <b>جایزه روزانه:</b> {daily}"
    )

def build_referral(uid: int, bot_username: str) -> str:
    u = cloud.ensure_user(uid)[0]
    link = f"https://t.me/{bot_username}?start=ref_{u['referral_code']}"
    refs = sum(1 for x in cloud.get_all_users() if x.get("referred_by") == str(uid))
    return (
        f"👥 <b>زیرمجموعهگیری</b>\n\n"
        f"🔗 لینک اختصاصی شما:\n<code>{link}</code>\n\n"
        f"✅ <b>پاداش هر رفرال:</b> {REFERRAL_BONUS} {COIN}\n"
        f"👥 <b>تعداد زیرمجموعهها:</b> {refs}"
    )

def build_help() -> str:
    return (
        f"📖 <b>راهنمای ExchangeBot</b>\n\n"
        f"<b>درآمدها:</b>\n"
        f"  🎯 عضویت کانال: {JOIN_REWARD} {COIN}\n"
        f"  ❤ لایک پست: {REACTION_REWARD} {COIN}\n"
        f"  🎁 جایزه روزانه: {DAILY_REWARD} {COIN}\n"
        f"  👥 معرفی دوست: {REFERRAL_BONUS} {COIN}\n\n"
        f"<b>هزینه سفارش:</b>\n"
        f"  👥 هر ممبر: {JOIN_REWARD * JOIN_COST_MULT} {COIN}\n"
        f"  ❤ هر لایک: {REACTION_REWARD * REACTION_COST_MULT} {COIN}\n\n"
        f"⚠ برای سفارش لایک، ربات باید <b>ادمین</b> کانال شما باشد\n"
        f"✅ لایکها فقط پس از راستیآزمایی واقعی پرداخت میشوند"
    )

def build_my_orders(uid: int) -> str:
    orders = cloud.get_user_orders(uid)
    if not orders:
        return "📦 سفارشی ندارید.\n\n💡 از دکمه «➕ ثبت سفارش» شروع کنید."
    text = "📦 <b>سفارشهای شما</b>\n\n"
    for o in orders[-10:]:
        icon   = "🟢" if o["status"] == "active" else "🔴"
        t      = "👥 ممبر" if o["type"] == "join" else "❤ لایک"
        done   = o["total_count"] - o["remaining_count"]
        post   = f"📝 پست: <code>{o['post_id']}</code>\n" if o.get("post_id") else ""
        desc   = (f"💬 <i>{o['description'][:50]}{'...' if len(o['description'])>50 else ''}</i>\n"
                  if o.get("description") else "")
        text  += (f"{icon} <b>{t}</b>\n"
                  f"🎯 <code>{o['target']}</code>\n"
                  f"{post}{desc}"
                  f"📊 {done}/{o['total_count']}\n\n")
    return text


# ═══════════════════════════════════════════
#  TASK CARD
# ═══════════════════════════════════════════
def build_task_card(order: dict, uid: int, index: int, total: int, filter_type: str):
    """
    کارت نمایش تسک.

    ✅ FIX: برای سفارشهای reaction، pending_like اینجا ثبت میشه
    (نه موقع کلیک لینک URL که ربات دسترسی نداره).
    """
    otype = order["type"]
    if otype == "join":
        emoji      = "👥"
        type_name  = "عضویت در کانال"
        reward     = JOIN_REWARD
        target_url = f"https://t.me/{order['target'].lstrip('@')}"
        target_disp= f"📢 <b>کانال:</b> <code>{order['target']}</code>"
    else:
        emoji      = "❤"
        type_name  = "لایک پست"
        reward     = REACTION_REWARD
        post_id    = order.get("post_id")
        target_url = (f"https://t.me/{order['target'].lstrip('@')}/{post_id}"
                      if post_id else f"https://t.me/{order['target'].lstrip('@')}")
        target_disp= (f"📢 <b>کانال:</b> <code>{order['target']}</code>\n"
                      f"📝 <b>شماره پست:</b> <code>{post_id}</code>")

    already_done = cloud.is_task_done(order["order_id"], uid)
    is_pending   = bool(cloud.get_pending_for_user(uid) and
                        any(p["order_id"] == order["order_id"]
                            for p in cloud.get_pending_for_user(uid)))

    # ── ✅ FIX: ثبت pending_like هنگام نمایش کارت لایک ──────────
    if otype == "reaction" and not already_done and not is_pending:
        cloud.add_pending_like(order["order_id"], uid)
        is_pending = True
    # ─────────────────────────────────────────────────────────────

    done_count = order["total_count"] - order["remaining_count"]

    # متن کارت
    desc = order.get("description", "").strip()
    card = (
        f"╔══════════════════════╗\n"
        f"║ {emoji} <b>{type_name}</b>\n"
        f"╚══════════════════════╝\n\n"
        f"🎁 <b>پاداش:</b> <code>{reward} {COIN}</code>\n"
        f"📊 <b>پیشرفت:</b> <code>{done_count}/{order['total_count']}</code>\n"
        f"{target_disp}\n\n"
    )
    if desc:
        card += f"┌─ 📋 <b>توضیحات:</b>\n│ <i>{desc}</i>\n└──────────────────\n\n"

    if already_done:
        card += "✅ <b>وضعیت:</b> انجام شده — پاداش دریافت شد"
    elif otype == "join":
        card += (
            "📌 <b>راهنما:</b>\n"
            "۱. عضو کانال شو\n"
            "۲. دکمه «✅ تایید» را بزن"
        )
    elif is_pending:
        card += (
            "⏳ <b>وضعیت:</b> منتظر لایک شما...\n\n"
            f"✨ با یکی از {', '.join(list(ALLOWED_REACTIONS)[:4])} لایک کن\n"
            "💡 پاداش <b>خودکار</b> واریز میشود!"
        )
    else:
        card += (
            "📌 <b>راهنما:</b>\n"
            f"۱. پست را باز کن\n"
            f"۲. با {', '.join(list(ALLOWED_REACTIONS)[:3])} لایک کن\n"
            "۳. پاداش خودکار واریز میشود!"
        )

    # کیبورد
    rows = []

    # فیلتر
    def filt_btn(label, ft, active=False):
        pfx = "✅ " if active else ""
        return InlineKeyboardButton(pfx + label, callback_data=f"tasks:filter:{ft}:0")

    rows.append([
        filt_btn("همه",  "all",      filter_type == "all"),
        filt_btn("ممبر", "join",     filter_type == "join"),
        filt_btn("لایک", "reaction", filter_type == "reaction"),
    ])
    rows.append([InlineKeyboardButton(
        f"─── 🎁 {index+1} از {total} ───", callback_data="noop"
    )])

    # دکمههای عملیاتی
    if already_done:
        rows.append([InlineKeyboardButton("✅ انجام شده", callback_data="noop")])
    elif otype == "join":
        rows.append([
            InlineKeyboardButton("🔗 عضویت", url=target_url),
            InlineKeyboardButton("✅ تایید عضویت",
                callback_data=f"tasks:verify_join:{order['order_id']}:{filter_type}:{index}"),
        ])
    else:
        # برای لایک فقط لینک — تشخیص خودکار توسط on_reaction
        rows.append([InlineKeyboardButton("🔗 باز کردن پست", url=target_url)])

    # ناوبری
    nav = []
    nav.append(InlineKeyboardButton("◀",
        callback_data=f"tasks:page:{index-1}:{filter_type}") if index > 0
        else InlineKeyboardButton("⏮", callback_data="noop"))
    nav.append(InlineKeyboardButton("▶",
        callback_data=f"tasks:page:{index+1}:{filter_type}") if index < total - 1
        else InlineKeyboardButton("⏭", callback_data="noop"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="nav:home")])

    return card, InlineKeyboardMarkup(rows)


async def show_task_card(q, uid: int, index: int, filter_type: str):
    orders = [o for o in cloud.get_active_orders(
        filter_type if filter_type != "all" else None)
        if o["owner_uid"] != uid]

    if not orders:
        await q.edit_message_text(
            "📭 <b>تسکی موجود نیست!</b>\n\nفعلاً سفارش فعالی وجود ندارد.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 بارگذاری مجدد", callback_data=f"tasks:filter:{filter_type}:0")],
                [InlineKeyboardButton("🔙 بازگشت",       callback_data="nav:home")],
            ]))
        return

    index = max(0, min(index, len(orders) - 1))
    card_text, card_kb = build_task_card(orders[index], uid, index, len(orders), filter_type)

    try:
        await q.edit_message_text(card_text, parse_mode=ParseMode.HTML, reply_markup=card_kb)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


# ═══════════════════════════════════════════
#  HANDLERS
# ═══════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    uname = update.effective_user.username or ""
    fname = f"{update.effective_user.first_name or ''} {update.effective_user.last_name or ''}".strip()
    u, is_new = cloud.ensure_user(uid, uname, fname)

    if ctx.args and is_new and ctx.args[0].startswith("ref_"):
        ref_code = ctx.args[0][4:]
        ref_uid  = cloud.find_uid_by_referral(ref_code)
        if ref_uid and cloud.apply_referral(uid, ref_uid):
            cloud.add_coin(ref_uid, REFERRAL_BONUS, "referral")
            cloud.save(force=True)

    if not await check_membership(ctx.bot, uid):
        await update.effective_message.reply_text(
            "⛔ برای استفاده از ربات ابتدا عضو کانال شو!",
            parse_mode=ParseMode.HTML, reply_markup=kb_join())
        return

    await update.effective_message.reply_text(
        build_welcome(uid), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
    cloud.save()


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    for k in ["awaiting", "temp_order"]:
        ctx.user_data.pop(k, None)
    await update.message.reply_text(
        "❌ عملیات لغو شد.", parse_mode=ParseMode.HTML,
        reply_markup=kb_main(update.effective_user.id))


async def on_reaction(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    ✅ FIX COMPLETE — تشخیص خودکار لایک

    مشکلات قبلی:
    1. فیلد اشتباه: new_reaction_type → درست: new_reaction
    2. pending_like هیچوقت ثبت نمیشد (الان در build_task_card ثبت میشه)
    3. مقایسه chat_id بدون نرمالسازی (الان _normalize_chat_id استفاده میشه)
    """
    reaction = update.message_reaction
    if not reaction or not reaction.user:
        return

    user_id    = reaction.user.id
    chat_id    = reaction.chat.id
    message_id = reaction.message_id

    log.info(f"🔔 Reaction event: user={user_id} chat={chat_id} msg={message_id}")

    # ✅ فیلد صحیح: new_reaction (نه new_reaction_type)
    new_reactions = getattr(reaction, "new_reaction", None)
    if not new_reactions:
        log.info("❌ No new reactions")
        return

    has_valid = any(
        getattr(r, "emoji", None) in ALLOWED_REACTIONS
        for r in new_reactions
    )
    if not has_valid:
        log.info(f"❌ Emoji not in allowed list")
        return

    # ✅ جستجوی pending با نرمالسازی chat_id
    pending = cloud.find_pending_by_post(user_id, chat_id, message_id)
    if not pending:
        log.info(f"❌ No pending found for user={user_id} chat={chat_id} msg={message_id}")
        return

    order_id = pending["order_id"]
    log.info(f"✅ MATCH — order={order_id} user={user_id}")

    # جلوگیری از double-reward در صورت race condition
    cloud.consume_pending_like(order_id, user_id)

    success, result = cloud.complete_order_unit(order_id, user_id)
    if not success:
        log.info(f"complete_order_unit failed: {result}")
        return

    reward = result  # عدد سکه
    cloud.save(force=True)

    u = cloud.get_user(user_id)
    bal = u.get("balance", 0) if u else "?"
    log.info(f"💰 +{reward} {COIN} → user={user_id} | new balance={bal}")

    try:
        await ctx.bot.send_message(
            user_id,
            f"🎉 <b>لایک تأیید شد!</b>\n\n"
            f"✅ <b>+{reward} {COIN}</b> واریز شد\n"
            f"💰 <b>موجودی:</b> <code>{bal} {COIN}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎯 تسک بعدی", callback_data="menu:earn")],
            ]))
    except Exception as e:
        log.error(f"Send reward message error: {e}")


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    data = q.data or ""
    uid  = q.from_user.id
    cloud.ensure_user(uid, q.from_user.username or "",
                      f"{q.from_user.first_name or ''} {q.from_user.last_name or ''}".strip())

    if data == "noop":
        await q.answer(); return

    # بررسی عضویت — به جز check_join
    if data != "check_join" and not await check_membership(q.get_bot(), uid):
        await q.answer()
        try:
            await q.edit_message_text(
                "⛔ ابتدا عضو کانال شو!",
                parse_mode=ParseMode.HTML, reply_markup=kb_join())
        except: pass
        return

    # ── check_join ────────────────────────
    if data == "check_join":
        if await check_membership(q.get_bot(), uid):
            await q.answer("✅ تایید شد!")
            await q.edit_message_text(
                build_welcome(uid), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        else:
            await q.answer("⛔ هنوز عضو نشدی!", show_alert=True)
        return

    # ── nav ───────────────────────────────
    if data.startswith("nav:"):
        dest = data[4:]
        await q.answer()
        if dest == "home":
            await q.edit_message_text(
                build_welcome(uid), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        elif dest == "profile":
            await q.edit_message_text(
                build_profile(uid), parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎁 دریافت روزانه", callback_data="daily:claim")],
                    [InlineKeyboardButton("🔙 بازگشت",       callback_data="nav:home")],
                ]))
        elif dest == "referral":
            me = await q.get_bot().get_me()
            await q.edit_message_text(
                build_referral(uid, me.username), parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 بازگشت", callback_data="nav:home")]]))
        elif dest == "help":
            await q.edit_message_text(
                build_help(), parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 بازگشت", callback_data="nav:home")]]))
        elif dest == "my_orders":
            await q.edit_message_text(
                build_my_orders(uid), parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ ثبت جدید", callback_data="menu:new_order")],
                    [InlineKeyboardButton("🔙 بازگشت",   callback_data="nav:home")],
                ]))
        return

    # ── menu shortcuts ────────────────────
    menu_map = {
        "menu:profile":   lambda: q.edit_message_text(
            build_profile(uid), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎁 دریافت روزانه", callback_data="daily:claim")],
                [InlineKeyboardButton("🔙 بازگشت",       callback_data="nav:home")],
            ])),
        "menu:my_orders": lambda: q.edit_message_text(
            build_my_orders(uid), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ ثبت جدید", callback_data="menu:new_order")],
                [InlineKeyboardButton("🔙 بازگشت",   callback_data="nav:home")],
            ])),
        "menu:help": lambda: q.edit_message_text(
            build_help(), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="nav:home")]])),
    }
    if data in menu_map:
        await q.answer()
        await menu_map[data]()
        return

    if data == "menu:referral":
        await q.answer()
        me = await q.get_bot().get_me()
        await q.edit_message_text(
            build_referral(uid, me.username), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="nav:home")]]))
        return

    # ── daily claim ───────────────────────
    if data == "daily:claim":
        if cloud.can_claim_daily(uid):
            cloud.claim_daily(uid); cloud.save(force=True)
            u = cloud.get_user(uid)
            await q.answer(f"✅ +{DAILY_REWARD} {COIN}", show_alert=True)
            try:
                await q.edit_message_text(
                    f"🎉 <b>جایزه روزانه دریافت شد!</b>\n\n"
                    f"+{DAILY_REWARD} {COIN}\n"
                    f"💰 موجودی: <code>{u.get('balance', 0)} {COIN}</code>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 بازگشت", callback_data="nav:home")]]))
            except: pass
        else:
            await q.answer("⏳ جایزه روزانه را فردا دریافت کنید!", show_alert=True)
        return

    # ── earn / tasks ──────────────────────
    if data == "menu:earn":
        await q.answer()
        await show_task_card(q, uid, 0, "all")
        return

    if data.startswith("tasks:page:"):
        _, _, idx, ft = data.split(":")
        await q.answer()
        await show_task_card(q, uid, int(idx), ft)
        return

    if data.startswith("tasks:filter:"):
        parts = data.split(":")
        ft  = parts[2]
        idx = int(parts[3]) if len(parts) > 3 else 0
        await q.answer()
        await show_task_card(q, uid, idx, ft)
        return

    if data.startswith("tasks:verify_join:"):
        parts = data.split(":")
        order_id   = parts[2]
        filter_type= parts[3] if len(parts) > 3 else "all"
        cur_idx    = int(parts[4]) if len(parts) > 4 else 0

        order = cloud.get_order_by_id(order_id)
        if not order:
            await q.answer("❌ سفارش یافت نشد", show_alert=True); return
        try:
            m = await q.get_bot().get_chat_member(order["target"], uid)
            is_member = m.status not in ("left", "kicked", "banned")
        except:
            await q.answer("⚠ کانال نامعتبر", show_alert=True); return
        if not is_member:
            await q.answer("⛔ هنوز عضو نشدی!", show_alert=True); return

        success, result = cloud.complete_order_unit(order_id, uid)
        cloud.save(force=True)
        if success:
            await q.answer(f"✅ +{result} {COIN} دریافت کردید!", show_alert=True)
            await show_task_card(q, uid, cur_idx, filter_type)
        else:
            await q.answer(str(result), show_alert=True)
        return

    # ── new order ─────────────────────────
    if data == "menu:new_order":
        await q.answer()
        u = cloud.get_user(uid)
        await q.edit_message_text(
            f"➕ <b>ثبت سفارش جدید</b>\n\n"
            f"💰 موجودی: <code>{u.get('balance', 0)} {COIN}</code>\n\nنوع سفارش:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 سفارش ممبر",   callback_data="order:type:join")],
                [InlineKeyboardButton("❤ سفارش لایک",  callback_data="order:type:reaction")],
                [InlineKeyboardButton("🔙 بازگشت",       callback_data="nav:home")],
            ]))
        return

    if data.startswith("order:type:"):
        ot = data.split(":")[2]
        await q.answer()
        if ot == "join":
            ctx.user_data["awaiting"] = "order_join"
            cost_per = JOIN_REWARD * JOIN_COST_MULT
            await q.edit_message_text(
                f"👥 <b>سفارش ممبر</b>\n\n"
                f"💰 هزینه هر ممبر: <b>{cost_per} {COIN}</b>\n\n"
                f"فرمت: <code>@channel | count</code>\n"
                f"مثال: <code>@mychannel | 100</code>\n\n"
                f"/cancel برای لغو",
                parse_mode=ParseMode.HTML)
        else:
            ctx.user_data["awaiting"] = "order_reaction_channel"
            cost_per = REACTION_REWARD * REACTION_COST_MULT
            await q.edit_message_text(
                f"❤ <b>سفارش لایک</b>\n\n"
                f"💰 هزینه هر لایک: <b>{cost_per} {COIN}</b>\n\n"
                f"⚠ ربات باید <b>ادمین</b> کانال شما باشد.\n\n"
                f"یوزرنیم کانال را ارسال کنید:\n"
                f"مثال: <code>@mychannel</code>\n\n"
                f"/cancel برای لغو",
                parse_mode=ParseMode.HTML)
        return

    if data.startswith("order:check_admin:"):
        channel = data.split(":", 2)[2]
        await q.answer("در حال بررسی...")
        if await check_bot_admin(q.get_bot(), channel):
            ctx.user_data["awaiting"] = f"order_reaction_post_{channel}"
            await q.edit_message_text(
                f"✅ ربات در <code>{channel}</code> ادمین است!\n\n"
                f"حالا لینک پست را ارسال کنید:\n"
                f"مثال: <code>https://t.me/{channel.lstrip('@')}/123</code>\n\n"
                f"/cancel برای لغو",
                parse_mode=ParseMode.HTML)
        else:
            await q.edit_message_text(
                f"❌ ربات هنوز در <code>{channel}</code> ادمین نیست!",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 بررسی مجدد",
                                          callback_data=f"order:check_admin:{channel}")],
                    [InlineKeyboardButton("🔙 بازگشت",
                                          callback_data="menu:new_order")],
                ]))
        return

    # ── admin ─────────────────────────────
    if not is_admin(uid):
        await q.answer("⛔ دسترسی ندارید", show_alert=True); return

    if data == "admin:panel":
        await q.answer()
        stats = cloud.get_stats()
        await q.edit_message_text(
            f"👑 <b>پنل ادمین</b>\n\n"
            f"👥 کاربران: {stats['total_users']}\n"
            f"📦 سفارشات فعال: {stats['active_orders']}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 پیام همگانی", callback_data="admin:broadcast")],
                [InlineKeyboardButton("🔙 بازگشت",     callback_data="nav:home")],
            ]))
        return

    if data == "admin:broadcast":
        ctx.user_data["awaiting"] = "broadcast"
        await q.answer()
        await q.edit_message_text(
            "📢 پیام همگانی را ارسال کنید:", parse_mode=ParseMode.HTML)
        return

    await q.answer()


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uid     = update.effective_user.id
    text    = (update.message.text or "").strip()
    awaiting= ctx.user_data.get("awaiting")

    if text == "/cancel":
        await cmd_cancel(update, ctx); return

    # دکمههای ریپلای
    if text in REPLY_MAP and not awaiting:
        action = REPLY_MAP[text]

        class _FakeQ:
            from_user = update.effective_user
            data      = action
            async def answer(self, *a, **kw): pass
            async def edit_message_text(self, t, **kw):
                await update.message.reply_text(t, **kw)
            def get_bot(self): return ctx.bot

        class _FakeUpd:
            callback_query = _FakeQ()
            effective_user = update.effective_user

        await on_callback(_FakeUpd(), ctx)
        return

    # ── order_join ────────────────────────
    if awaiting == "order_join":
        ctx.user_data.pop("awaiting", None)
        parts = [p.strip() for p in text.split("|")]
        if len(parts) != 2:
            await update.message.reply_text(
                "❌ فرمت اشتباه.\nمثال: <code>@mychannel | 100</code>",
                parse_mode=ParseMode.HTML); return
        target, count_str = parts
        try:
            count = int(count_str)
        except:
            await update.message.reply_text("❌ تعداد باید عدد باشد."); return
        if not target.startswith("@") or count < 1:
            await update.message.reply_text("❌ مقادیر نامعتبر."); return
        try:
            chat = await ctx.bot.get_chat(target)
            if chat.type not in ("channel", "supergroup"): raise ValueError
        except:
            await update.message.reply_text("❌ کانال نامعتبر یا خصوصی!"); return

        cost_per = JOIN_REWARD * JOIN_COST_MULT
        total    = cost_per * count
        u = cloud.get_user(uid)
        if u.get("balance", 0) < total:
            await update.message.reply_text(
                f"❌ موجودی کافی نیست.\nنیاز: <b>{total} {COIN}</b>",
                parse_mode=ParseMode.HTML); return

        ctx.user_data["temp_order"] = {
            "type": "join", "target": target,
            "count": count, "cost_per": cost_per, "chat_id": chat.id
        }
        ctx.user_data["awaiting"] = "order_description"
        await update.message.reply_text(
            f"✅ دریافت شد!\n\n📝 توضیحات (اختیاری):\n"
            f"حداکثر {MAX_DESC_LENGTH} کاراکتر\n\n"
            f"بنویس یا <code>skip</code> برای رد کردن.",
            parse_mode=ParseMode.HTML); return

    # ── order_reaction_channel ────────────
    if awaiting == "order_reaction_channel":
        ctx.user_data.pop("awaiting", None)
        channel = text
        if not channel.startswith("@"):
            await update.message.reply_text("❌ یوزرنیم باید با @ شروع شود."); return
        try:
            chat = await ctx.bot.get_chat(channel)
            if chat.type not in ("channel", "supergroup"): raise ValueError
        except:
            await update.message.reply_text("❌ کانال نامعتبر!"); return
        if not await check_bot_admin(ctx.bot, channel):
            await update.message.reply_text(
                f"❌ ربات در <code>{channel}</code> ادمین نیست!",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 بررسی مجدد",
                                          callback_data=f"order:check_admin:{channel}")],
                    [InlineKeyboardButton("🔙 بازگشت",
                                          callback_data="menu:new_order")],
                ])); return
        ctx.user_data["awaiting"] = f"order_reaction_post_{channel}"
        await update.message.reply_text(
            f"✅ ربات در <code>{channel}</code> ادمین است!\n\n"
            f"لینک پست را ارسال کنید:\n"
            f"مثال: <code>https://t.me/{channel.lstrip('@')}/123</code>",
            parse_mode=ParseMode.HTML); return

    # ── order_reaction_post_{channel} ─────
    if awaiting and awaiting.startswith("order_reaction_post_"):
        channel = awaiting[len("order_reaction_post_"):]
        ctx.user_data.pop("awaiting", None)
        post_id = None

        if update.message.text:
            m = re.search(r"t\.me/[^/]+/(\d+)", update.message.text)
            if m:
                post_id = int(m.group(1))
        if post_id is None and update.message.forward_from_message_id:
            fwd_chat = getattr(update.message.forward_from_chat, "username", "")
            if fwd_chat and fwd_chat.lower() == channel.lstrip("@").lower():
                post_id = update.message.forward_from_message_id

        if not post_id:
            await update.message.reply_text(
                "❌ لینک پست نامعتبر!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 دوباره",
                                          callback_data=f"order:check_admin:{channel}")]])); return

        try:
            chat = await ctx.bot.get_chat(channel)
        except:
            await update.message.reply_text("❌ خطا در دسترسی به کانال!"); return

        cost_per = REACTION_REWARD * REACTION_COST_MULT
        ctx.user_data["awaiting"] = f"order_reaction_count_{channel}_{post_id}_{chat.id}"
        await update.message.reply_text(
            f"✅ پست <code>{post_id}</code> شناسایی شد!\n\n"
            f"💰 هزینه هر لایک: <b>{cost_per} {COIN}</b>\n\n"
            f"تعداد لایک مورد نظر:\nمثال: <code>50</code>",
            parse_mode=ParseMode.HTML); return

    # ── order_reaction_count ──────────────
    if awaiting and awaiting.startswith("order_reaction_count_"):
        raw = awaiting[len("order_reaction_count_"):]
        parts = raw.rsplit("_", 2)          # channel_postid_chatid
        if len(parts) < 3:
            ctx.user_data.pop("awaiting", None); return
        channel, post_id_str, chat_id_str = parts
        ctx.user_data.pop("awaiting", None)

        try:
            count = int(text)
        except:
            await update.message.reply_text("❌ تعداد باید عدد باشد."); return
        if count < 1:
            await update.message.reply_text("❌ حداقل ۱ لایک."); return

        cost_per = REACTION_REWARD * REACTION_COST_MULT
        total    = cost_per * count
        u = cloud.get_user(uid)
        if u.get("balance", 0) < total:
            await update.message.reply_text(
                f"❌ موجودی کافی نیست.\nنیاز: <b>{total} {COIN}</b>",
                parse_mode=ParseMode.HTML); return

        ctx.user_data["temp_order"] = {
            "type": "reaction", "target": channel,
            "count": count, "cost_per": cost_per,
            "chat_id": int(chat_id_str), "post_id": int(post_id_str)
        }
        ctx.user_data["awaiting"] = "order_description"
        await update.message.reply_text(
            f"✅ دریافت شد!\n\n📝 توضیحات (اختیاری):\n"
            f"حداکثر {MAX_DESC_LENGTH} کاراکتر\n\n"
            f"بنویس یا <code>skip</code> برای رد کردن.",
            parse_mode=ParseMode.HTML); return

    # ── order_description ─────────────────
    if awaiting == "order_description":
        ctx.user_data.pop("awaiting", None)
        temp = ctx.user_data.pop("temp_order", None)
        if not temp:
            await update.message.reply_text("❌ اطلاعات سفارش از دست رفته!"); return

        description = "" if text.lower() == "skip" else text[:MAX_DESC_LENGTH]
        order = cloud.create_order(
            uid, temp["type"], temp["target"], temp["count"], temp["cost_per"],
            post_id=temp.get("post_id"), chat_id=temp.get("chat_id"),
            description=description)

        if order:
            cloud.save(force=True)
            type_text = "ممبر" if temp["type"] == "join" else "لایک"
            total     = temp["cost_per"] * temp["count"]
            post_info = f"\n📝 پست: <code>{temp['post_id']}</code>" if temp.get("post_id") else ""
            desc_info = (f"\n💬 <i>{description[:80]}{'...' if len(description)>80 else ''}</i>"
                         if description else "")
            await update.message.reply_text(
                f"✅ <b>سفارش ثبت شد!</b>\n\n"
                f"🎯 نوع: <b>{type_text}</b>\n"
                f"📢 کانال: <code>{temp['target']}</code>{post_info}\n"
                f"👥 تعداد: {temp['count']}\n"
                f"💰 کسر شد: <b>{total} {COIN}</b>{desc_info}",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📦 سفارشهای من",
                                          callback_data="menu:my_orders")]]))
        else:
            await update.message.reply_text("❌ خطا در ثبت سفارش (موجودی کافی نیست؟)")
        return

    # ── broadcast (admin) ─────────────────
    if is_admin(uid) and awaiting == "broadcast":
        ctx.user_data.pop("awaiting", None)
        users = cloud.get_all_users()
        sent  = 0
        for u in users:
            try:
                await ctx.bot.send_message(
                    int(u.get("uid_str") or u.get("uid")),
                    text, parse_mode=ParseMode.HTML)
                sent += 1
                await asyncio.sleep(0.05)
            except: pass
        await update.message.reply_text(f"✅ ارسال: {sent}/{len(users)}")


# ═══════════════════════════════════════════
#  LIFECYCLE
# ═══════════════════════════════════════════
async def on_startup(app: Application):
    cloud.load()

    async def _cleanup():
        while True:
            await asyncio.sleep(60)
            cloud.cleanup_expired_pendings()
            cloud.save()

    asyncio.create_task(_cleanup())
    log.info("🚀 ExchangeBot v6.0 آماده است")


async def on_shutdown(app: Application):
    cloud.save(force=True)


# ═══════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════
def main():
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TOKEN_HERE":
        log.critical("BOT_TOKEN تنظیم نشده!"); sys.exit(1)

    app = (Application.builder()
           .token(BOT_TOKEN)
           .post_init(on_startup)
           .post_shutdown(on_shutdown)
           .build())

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageReactionHandler(on_reaction))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_message))
    app.add_handler(MessageHandler(
        filters.FORWARDED & filters.ChatType.PRIVATE, on_message))

    def _sig(s, f):
        cloud.save(force=True); sys.exit(0)
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT,  _sig)
    atexit.register(lambda: cloud.save(force=True))

    webhook_url = os.environ.get("RENDER_EXTERNAL_URL")
    if webhook_url:
        log.info("🌐 حالت Webhook")
        main_loop = asyncio.new_event_loop()
        threading.Thread(
            target=lambda: (asyncio.set_event_loop(main_loop), main_loop.run_forever()),
            daemon=True).start()

        flask_app = Flask(__name__)

        @flask_app.route("/")
        def index():
            return "🤖 ExchangeBot v6.0 Alive!", 200

        @flask_app.route(f"/{BOT_TOKEN}", methods=["POST"])
        def webhook():
            try:
                upd = Update.de_json(request.get_json(force=True), app.bot)
                asyncio.run_coroutine_threadsafe(app.process_update(upd), main_loop)
            except Exception as e:
                log.error(f"Webhook error: {e}")
            return "OK", 200

        async def _setup():
            url = webhook_url.rstrip("/") + "/" + BOT_TOKEN
            await app.bot.delete_webhook(drop_pending_updates=True)
            await asyncio.sleep(1)
            await app.bot.set_webhook(
                url=url,
                allowed_updates=["message", "callback_query", "message_reaction"],
                drop_pending_updates=True)
            log.info(f"✅ Webhook فعال شد: {url}")

        async def _init():
            await app.initialize()
            await app.start()
            await on_startup(app)

        asyncio.run_coroutine_threadsafe(_setup(), main_loop).result(timeout=30)
        asyncio.run_coroutine_threadsafe(_init(),  main_loop).result(timeout=30)

        flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
    else:
        log.warning("⚠ حالت Polling")
        app.run_polling(
            allowed_updates=["message", "callback_query", "message_reaction"],
            drop_pending_updates=True)


if __name__ == "__main__":
    main()
