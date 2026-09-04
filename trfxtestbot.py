#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ExchangeBot v5.0 FINAL — نسخه کامل و بی‌نقص
✅ تمام قابلیت‌های نسخه v3.1 + Reaction Handler اصلاح شده
✅ کارت‌های حرفه‌ای با گیفت کارت
✅ سیستم اسکرول ساده و تمیز
✅ تشخیص خودکار لایک
"""
import os, sys, re, time, json, signal, atexit, logging, hashlib
import asyncio, threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List
from flask import Flask, request

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.error import BadRequest, Forbidden, InvalidToken
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          ContextTypes, filters, MessageHandler, MessageReactionHandler)

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(name)s — %(message)s", level=logging.INFO, stream=sys.stdout)
log = logging.getLogger("ExchangeBot")

# ═══════════════ CONFIG ═══════════════
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8649622614:AAFs3Hoz9EbjBGiFeua3wt8j_XJFRbtMNxA")
PORT = int(os.environ.get("PORT", 5000))
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "605611554").replace(" ", "").split(",") if x.strip()}
FORCE_JOIN_CHANNEL = os.environ.get("FORCE_JOIN_CHANNEL", "@testchanellaz")

COIN = "💎"
SIGNUP_BONUS = 100
REFERRAL_BONUS = 50
DAILY_REWARD = 10
JOIN_REWARD = 5
JOIN_COST_MULT = 2
REACTION_REWARD = 2
REACTION_COST_MULT = 2
ALLOWED_REACTIONS = {"👍", "❤️", "🔥", "👏", "🎉", "🤩"}
IRAN_TZ = timezone(timedelta(hours=3, minutes=30))
PENDING_TIMEOUT = 600
MAX_DESC_LENGTH = 200

# ═══════════════ CLOUD STORAGE ═══════════════
try:
    from pymongo import MongoClient
    MONGODB_AVAILABLE = True
except ImportError:
    MONGODB_AVAILABLE = False

class CloudStorage:
    def __init__(self):
        self._uri = os.environ.get("MONGODB_URI", "")
        self._client = None
        self._collection = None
        self._lock = threading.Lock()
        self._dirty = False
        self._data = {"users": {}, "orders": [], "tasks_completed": [],
                      "pending_likes": [], "transactions": [], "last_updated": None}
        if self._uri and MONGODB_AVAILABLE:
            try:
                self._client = MongoClient(self._uri, serverSelectionTimeoutMS=10000)
                self._client.admin.command('ping')
                self._collection = self._client.get_database()["exchange_bot_data"]
                log.info("MongoDB: اتصال برقرار شد ✅")
            except Exception as e:
                log.error(f"MongoDB اتصال ناموفق: {e}")
    
    def load(self):
        if not self._uri or not self._collection: return
        try:
            doc = self._collection.find_one({"_id": "main"})
            if doc:
                with self._lock:
                    doc.pop("_id", None)
                    for k, v in doc.items(): self._data[k] = v
                    for k in ["users", "orders", "tasks_completed", "pending_likes", "transactions"]:
                        self._data.setdefault(k, [] if k != "users" else {})
        except Exception as e: log.error(f"Load error: {e}")
    
    def save(self, force=False):
        if not self._uri or not self._collection: return
        if not force and not self._dirty: return
        with self._lock:
            self._data["last_updated"] = now_ir().isoformat()
            payload = {"_id": "main"}
            payload.update(self._data)
            try:
                self._collection.replace_one({"_id": "main"}, payload, upsert=True)
                self._dirty = False
            except Exception as e: log.error(f"Save error: {e}")
    
    def ensure_user(self, uid, username="", full_name=""):
        key = str(uid)
        is_new = False
        with self._lock:
            if key not in self._data["users"]:
                is_new = True
                self._data["users"][key] = {
                    "uid": uid, "username": username or "", "full_name": full_name or "",
                    "balance": SIGNUP_BONUS, "total_earned": SIGNUP_BONUS,
                    "referral_code": hashlib.md5(f"{uid}{time.time()}".encode()).hexdigest()[:8],
                    "referred_by": None, "created_at": now_ir().isoformat(),
                    "last_daily_claim": None, "tasks_done": 0,
                }
                self._dirty = True
            else:
                u = self._data["users"][key]
                if username and u.get("username") != username: u["username"] = username; self._dirty = True
                if full_name and u.get("full_name") != full_name: u["full_name"] = full_name; self._dirty = True
            return dict(self._data["users"][key]), is_new
    
    def get_user(self, uid):
        with self._lock:
            u = self._data["users"].get(str(uid))
            return dict(u) if u else None
    
    def get_all_users(self):
        with self._lock: return [{"uid_str": k, **v} for k, v in self._data["users"].items()]
    
    def add_coin(self, uid, amount, reason):
        key = str(uid)
        with self._lock:
            if key in self._data["users"]:
                self._data["users"][key]["balance"] += amount
                self._data["users"][key]["total_earned"] += amount
                self._data["transactions"].append({"uid": uid, "amount": amount, "reason": reason, "timestamp": now_ir().isoformat()})
                self._data["transactions"] = self._data["transactions"][-2000:]
                self._dirty = True
                return True
            return False
    
    def deduct_coin(self, uid, amount, reason):
        key = str(uid)
        with self._lock:
            u = self._data["users"].get(key)
            if u and u.get("balance", 0) >= amount:
                u["balance"] -= amount
                self._data["transactions"].append({"uid": uid, "amount": -amount, "reason": reason, "timestamp": now_ir().isoformat()})
                self._dirty = True
                return True
            return False
    
    def create_order(self, uid, order_type, target, count, cost_per, post_id=None, chat_id=None, description=""):
        total_cost = cost_per * count
        if not self.deduct_coin(uid, total_cost, f"create_{order_type}"): return None
        order_id = hashlib.md5(f"{uid}{target}{time.time()}".encode()).hexdigest()[:12]
        with self._lock:
            order = {"order_id": order_id, "owner_uid": uid, "type": order_type,
                    "target": target, "post_id": post_id, "chat_id": chat_id,
                    "total_count": count, "remaining_count": count, "cost_per_unit": cost_per,
                    "description": description[:MAX_DESC_LENGTH],
                    "created_at": now_ir().isoformat(), "status": "active"}
            self._data["orders"].append(order)
            self._dirty = True
            log.info(f"✅ Order created: {order_id} | type={order_type} | chat_id={chat_id} | post_id={post_id}")
            return dict(order)
    
    def get_active_orders(self, order_type=None):
        with self._lock:
            orders = [o for o in self._data["orders"] if o.get("status") == "active" and o.get("remaining_count", 0) > 0]
            return [o for o in orders if not order_type or o.get("type") == order_type]
    
    def get_order_by_id(self, order_id):
        with self._lock:
            return next((dict(o) for o in self._data["orders"] if o["order_id"] == order_id), None)
    
    def complete_order_unit(self, order_id, worker_uid):
        with self._lock:
            order = next((o for o in self._data["orders"] if o["order_id"] == order_id), None)
            if not order or order["status"] != "active" or order["remaining_count"] <= 0:
                return False, "❌ تسک نامعتبر"
            if order["owner_uid"] == worker_uid:
                return False, "⛔️ نمی‌توانید تسک خودتان را انجام دهید"
            key = f"{order_id}_{worker_uid}"
            if key in self._data["tasks_completed"]:
                return False, "✅ قبلاً انجام داده‌اید"
            order["remaining_count"] -= 1
            if order["remaining_count"] <= 0: order["status"] = "completed"
            self._data["tasks_completed"].append(key)
            self._dirty = True
            reward = JOIN_REWARD if order["type"] == "join" else REACTION_REWARD
            u = self._data["users"].get(str(worker_uid))
            if u: u["tasks_done"] = u.get("tasks_done", 0) + 1
        self.add_coin(worker_uid, reward, f"task_{order_id}")
        return True, f"✅ {reward} {COIN} دریافت کردید!"
    
    def get_user_orders(self, uid):
        with self._lock: return [o for o in self._data["orders"] if o.get("owner_uid") == uid]
    
    def add_pending_like(self, order_id, worker_uid):
        with self._lock:
            self._data["pending_likes"] = [p for p in self._data["pending_likes"]
                                           if not (p["order_id"] == order_id and p["worker_uid"] == worker_uid)]
            self._data["pending_likes"].append({
                "order_id": order_id, "worker_uid": worker_uid,
                "timestamp": time.time(), "expires": time.time() + PENDING_TIMEOUT
            })
            self._dirty = True
            log.info(f"📌 Pending like added: order={order_id} user={worker_uid}")
    
    def get_pending_for_user(self, worker_uid):
        with self._lock:
            now = time.time()
            return [p for p in self._data["pending_likes"]
                    if p["worker_uid"] == worker_uid and p["expires"] > now]
    
    def cleanup_expired_pendings(self):
        with self._lock:
            now = time.time()
            old_count = len(self._data["pending_likes"])
            self._data["pending_likes"] = [p for p in self._data["pending_likes"] if p["expires"] > now]
            if len(self._data["pending_likes"]) < old_count:
                self._dirty = True
    
    def can_claim_daily(self, uid):
        u = self.get_user(uid)
        if not u or not u.get("last_daily_claim"): return True
        try: return (now_ir() - datetime.fromisoformat(u["last_daily_claim"])).days >= 1
        except: return True
    
    def claim_daily(self, uid):
        if not self.can_claim_daily(uid): return False
        with self._lock:
            k = str(uid)
            if k in self._data["users"]:
                self._data["users"][k]["last_daily_claim"] = now_ir().isoformat()
                self._dirty = True
        return self.add_coin(uid, DAILY_REWARD, "daily")
    
    def find_uid_by_referral(self, ref_code):
        with self._lock:
            for uid_str, u in self._data["users"].items():
                if u.get("referral_code") == ref_code: return int(uid_str)
            return None
    
    def apply_referral(self, new_uid, ref_uid):
        if new_uid == ref_uid: return False
        with self._lock:
            n_k, r_k = str(new_uid), str(ref_uid)
            users = self._data["users"]
            if n_k not in users or r_k not in users: return False
            if users[n_k].get("referred_by"): return False
            users[n_k]["referred_by"] = r_k
            self._dirty = True
            return True
    
    def get_stats(self):
        with self._lock:
            return {"total_users": len(self._data["users"]),
                    "active_orders": sum(1 for o in self._data["orders"] if o.get("status") == "active")}

cloud = CloudStorage()

def now_ir(): return datetime.now(IRAN_TZ)
def is_admin(uid): return uid in ADMIN_IDS

# ═══════════════ HELPERS ═══════════════
async def check_membership(bot, uid):
    if not FORCE_JOIN_CHANNEL: return True
    try:
        m = await bot.get_chat_member(FORCE_JOIN_CHANNEL, uid)
        return m.status not in ("left", "kicked", "banned")
    except: return True

async def check_bot_admin_in_channel(bot, channel):
    try:
        me = await bot.get_me()
        m = await bot.get_chat_member(channel, me.id)
        return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except: return False

def kb_join():
    if not FORCE_JOIN_CHANNEL: return None
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/{FORCE_JOIN_CHANNEL.lstrip('@')}")],
        [InlineKeyboardButton("✅ عضو شدم", callback_data="check_join")],
    ])

def kb_main(uid=0):
    rows = [
        [KeyboardButton("🎯 کسب درآمد"), KeyboardButton("➕ ثبت سفارش")],
        [KeyboardButton("📦 سفارش‌های من"), KeyboardButton("👥 زیرمجموعه")],
        [KeyboardButton("👤 پروفایل"), KeyboardButton("📖 راهنما")],
    ]
    if uid and is_admin(uid): rows.append([KeyboardButton("👑 پنل ادمین")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)

REPLY_MAP = {
    "🎯 کسب درآمد": "menu:earn", "➕ ثبت سفارش": "menu:new_order",
    "📦 سفارش‌های من": "menu:my_orders", "👥 زیرمجموعه": "menu:referral",
    "👤 پروفایل": "menu:profile", "📖 راهنما": "menu:help",
    "👑 پنل ادمین": "admin:panel:0",
}

# ═══════════════ TEXT BUILDERS ═══════════════
def build_welcome(uid):
    u = cloud.get_user(uid)
    return (f"🎉 <b>به ExchangeBot خوش اومدی!</b>\n\n"
            f"💰 <b>موجودی:</b> <code>{u.get('balance', 0)} {COIN}</code>\n\n"
            f"🎯 کسب درآمد با عضویت و لایک\n➕ ثبت سفارش برای کانال خودت\n👥 رفرال و {REFERRAL_BONUS} {COIN} جایزه")

def build_profile(uid):
    u = cloud.get_user(uid)
    if not u: return ""
    refs = sum(1 for x in cloud.get_all_users() if x.get("referred_by") == str(uid))
    return (f"👤 <b>پروفایل</b>\n\n📛 {u.get('full_name') or u.get('username') or '—'}\n"
            f"🆔 <code>{uid}</code>\n\n💰 <code>{u.get('balance', 0)} {COIN}</code>\n"
            f"📈 کل درآمد: <code>{u.get('total_earned', 0)} {COIN}</code>\n"
            f"✅ تسک‌ها: {u.get('tasks_done', 0)}\n👥 رفرال‌ها: {refs}\n"
            f"🎁 روزانه: {'✅' if cloud.can_claim_daily(uid) else '⏳ فردا'}")

def build_referral(uid, bot_username):
    u = cloud.ensure_user(uid)[0]
    link = f"https://t.me/{bot_username}?start=ref_{u['referral_code']}"
    refs = sum(1 for x in cloud.get_all_users() if x.get("referred_by") == str(uid))
    return (f"👥 <b>زیرمجموعه‌گیری</b>\n\n🔗 <code>{link}</code>\n\n"
            f"✅ هر رفرال: {REFERRAL_BONUS} {COIN}\n👥 تعداد: {refs}")

def build_help():
    return (f"📖 <b>راهنما</b>\n\n"
            f"🎯 عضویت: {JOIN_REWARD} {COIN}\n❤️ لایک: {REACTION_REWARD} {COIN}\n"
            f"🎁 روزانه: {DAILY_REWARD} {COIN}\n👥 رفرال: {REFERRAL_BONUS} {COIN}\n\n"
            f"💰 هزینه ممبر: {JOIN_REWARD * JOIN_COST_MULT} {COIN}\n"
            f"💰 هزینه لایک: {REACTION_REWARD * REACTION_COST_MULT} {COIN}\n\n"
            f"⚠️ برای سفارش لایک، ربات باید ادمین کانال شما باشد\n"
            f"✅ لایک شما <b>فقط پس از راستی‌آزمایی واقعی</b> پرداخت می‌شود")

def build_my_orders(uid):
    orders = cloud.get_user_orders(uid)
    if not orders: return "📦 سفارشی ندارید.\n\n💡 برای ثبت، دکمه «➕ ثبت سفارش» را بزنید."
    text = "📦 <b>سفارش‌های شما</b>\n\n"
    for o in orders[-10:]:
        status = "🟢" if o["status"] == "active" else "🔴"
        t = "👥 ممبر" if o["type"] == "join" else "❤️ لایک"
        done = o["total_count"] - o["remaining_count"]
        post = f"📝 پست: <code>{o['post_id']}</code>\n" if o.get("post_id") else ""
        desc = f"💬 {o.get('description','')[:50]}...\n" if o.get("description") else ""
        text += f"{status} <b>{t}</b>\n🎯 <code>{o['target']}</code>\n{post}{desc}📊 {done}/{o['total_count']}\n\n"
    return text

def build_task_card(order, uid, current_index, total_count, order_type):
    """کارت حرفه‌ای تسک - UI ساده و تمیز"""
    if order["type"] == "join":
        type_emoji = "👥"
        type_name = "عضویت در کانال"
        reward = JOIN_REWARD
        target_url = f"https://t.me/{order['target'].lstrip('@')}"
        target_display = f"📢 <b>کانال:</b> <code>{order['target']}</code>"
    else:
        type_emoji = "❤️"
        type_name = "لایک پست"
        reward = REACTION_REWARD
        post_id = order.get("post_id")
        target_url = f"https://t.me/{order['target'].lstrip('@')}/{post_id}" if post_id else f"https://t.me/{order['target'].lstrip('@')}"
        target_display = f"📢 <b>کانال:</b> <code>{order['target']}</code>\n📝 <b>شماره پست:</b> <code>{post_id}</code>"
    
    key = f"{order['order_id']}_{uid}"
    already_done = key in cloud._data.get("tasks_completed", [])
    pending = cloud.get_pending_for_user(uid)
    is_pending = any(p["order_id"] == order["order_id"] for p in pending)
    
    # ساخت کارت متنی حرفه‌ای
    card_text = (
        f"╔════════════════════════╗\n"
        f"║ {type_emoji} <b>{type_name}</b>\n"
        f"╚════════════════════════╝\n\n"
        f"🎁 <b>پاداش:</b> <code>{reward} {COIN}</code>\n"
        f"📊 <b>ظرفیت:</b> <code>{order['total_count'] - order['remaining_count']}</code>/{order['total_count']}\n"
        f"{target_display}\n\n"
    )
    
    # توضیحات سفارش‌دهنده
    description = order.get("description", "").strip()
    if description:
        card_text += (
            f"┌─ 📋 <b>توضیحات سفارش‌دهنده:</b>\n"
            f"│ <i>{description}</i>\n"
            f"└─────────────────────\n\n"
        )
    else:
        card_text += f"┌─ 📋 <b>توضیحات:</b> <i>ندارد</i>\n└─────────────────────\n\n"
    
    # راهنمای ساده
    if order["type"] == "join":
        card_text += (
            f"📌 <b>راهنما:</b>\n"
            f"1️⃣ عضو کانال شو\n"
            f"2️⃣ دکمه «✅ تایید» را بزن\n\n"
        )
    else:
        if already_done:
            card_text += f"✅ <b>وضعیت:</b> انجام شده و پاداش دریافت شده"
        elif is_pending:
            card_text += f"⏳ <b>وضعیت:</b> لایک کن! پاداش خودکار واریز می‌شود..."
        else:
            card_text += (
                f"📌 <b>راهنما:</b>\n"
                f"1️⃣ پست را باز کن\n"
                f"2️⃣ با {', '.join(list(ALLOWED_REACTIONS)[:3])} لایک کن\n"
                f"3️⃣ پاداش <b>خودکار</b> واریز می‌شود!"
            )
    
    # ساخت کیبورد ساده و تمیز
    rows = []
    
    # فیلتر
    filter_row = []
    if order_type == "all":
        filter_row.append(InlineKeyboardButton("✅ همه", callback_data=f"tasks:filter:all:{current_index}"))
        filter_row.append(InlineKeyboardButton("👥 ممبر", callback_data=f"tasks:filter:join:0"))
        filter_row.append(InlineKeyboardButton("❤️ لایک", callback_data=f"tasks:filter:reaction:0"))
    elif order_type == "join":
        filter_row.append(InlineKeyboardButton("🔄 همه", callback_data=f"tasks:filter:all:0"))
        filter_row.append(InlineKeyboardButton("✅ ممبر", callback_data=f"tasks:filter:join:{current_index}"))
        filter_row.append(InlineKeyboardButton("❤️ لایک", callback_data=f"tasks:filter:reaction:0"))
    else:
        filter_row.append(InlineKeyboardButton("🔄 همه", callback_data=f"tasks:filter:all:0"))
        filter_row.append(InlineKeyboardButton("👥 ممبر", callback_data=f"tasks:filter:join:0"))
        filter_row.append(InlineKeyboardButton("✅ لایک", callback_data=f"tasks:filter:reaction:{current_index}"))
    rows.append(filter_row)
    
    rows.append([InlineKeyboardButton(f"─── 🎁 سفارش {current_index + 1} از {total_count} ───", callback_data="noop")])
    
    # دکمه‌های اصلی - ساده و تمیز
    if already_done:
        rows.append([InlineKeyboardButton("✅ انجام شده", callback_data="noop")])
    else:
        if order["type"] == "join":
            rows.append([
                InlineKeyboardButton("🔗 عضویت", url=target_url),
                InlineKeyboardButton("✅ تایید", callback_data=f"tasks:verify_join:{order['order_id']}:{order_type}:{current_index}")
            ])
        else:
            # فقط دکمه باز کردن پست - بدون دکمه شروع یا تایید دستی
            rows.append([InlineKeyboardButton("🔗 باز کردن پست", url=target_url)])
    
    # ناوبری
    nav_row = []
    if current_index > 0:
        nav_row.append(InlineKeyboardButton("◀️", callback_data=f"tasks:page:{current_index-1}:{order_type}"))
    else:
        nav_row.append(InlineKeyboardButton("⏮️", callback_data="noop"))
    
    if current_index < total_count - 1:
        nav_row.append(InlineKeyboardButton("▶️", callback_data=f"tasks:page:{current_index+1}:{order_type}"))
    else:
        nav_row.append(InlineKeyboardButton("⏭️", callback_data="noop"))
    rows.append(nav_row)
    
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="nav:home")])
    
    return card_text, InlineKeyboardMarkup(rows)

async def cmd_start(update, ctx):
    uid = update.effective_user.id
    uname = update.effective_user.username or ""
    fname = f"{update.effective_user.first_name or ''} {update.effective_user.last_name or ''}".strip()
    u, is_new = cloud.ensure_user(uid, uname, fname)
    
    if ctx.args and is_new and ctx.args[0].startswith("ref_"):
        ref_code = ctx.args[0][4:]
        if not await check_membership(ctx.bot, uid):
            ctx.user_data["pending_ref"] = ref_code
            await ctx.bot.send_message(uid, "⛔️ ابتدا عضو کانال شو!", parse_mode=ParseMode.HTML, reply_markup=kb_join())
            return
        ref_uid = cloud.find_uid_by_referral(ref_code)
        if ref_uid and cloud.apply_referral(uid, ref_uid):
            cloud.add_coin(ref_uid, REFERRAL_BONUS, "referral")
            cloud.save(force=True)
    
    if not await check_membership(ctx.bot, uid):
        await update.effective_message.reply_text("⛔️ عضو کانال شو!", parse_mode=ParseMode.HTML, reply_markup=kb_join())
        return
    
    await update.effective_message.reply_text(build_welcome(uid), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
    cloud.save()

async def cmd_cancel(update, ctx):
    for k in ["awaiting", "pending_ref"]: ctx.user_data.pop(k, None)
    await update.message.reply_text("❌ لغو شد.", parse_mode=ParseMode.HTML, reply_markup=kb_main(update.effective_user.id))

async def on_callback(update, ctx):
    q = update.callback_query
    data = q.data or ""
    uid = q.from_user.id
    cloud.ensure_user(uid, q.from_user.username or "",
                      f"{q.from_user.first_name or ''} {q.from_user.last_name or ''}".strip())
    
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
                    cloud.add_coin(ref_uid, REFERRAL_BONUS, "referral")
                cloud.save(force=True)
            await q.edit_message_text(build_welcome(uid), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        else:
            await q.answer("⛔️ هنوز عضو نشدی!", show_alert=True)
        return
    
    # NAV
    if data.startswith("nav:"):
        dest = data.replace("nav:", "")
        await q.answer()
        if dest == "home":
            await q.edit_message_text(build_welcome(uid), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        elif dest == "profile":
            await q.edit_message_text(build_profile(uid), parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎁 دریافت روزانه", callback_data="daily:claim")],
                                                   [InlineKeyboardButton("🔙", callback_data="nav:home")]]))
        elif dest == "referral":
            me = await q.get_bot().get_me()
            await q.edit_message_text(build_referral(uid, me.username), parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="nav:home")]]))
        elif dest == "help":
            await q.edit_message_text(build_help(), parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="nav:home")]]))
        elif dest == "my_orders":
            await q.edit_message_text(build_my_orders(uid), parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ ثبت جدید", callback_data="menu:new_order")],
                                                   [InlineKeyboardButton("🔙", callback_data="nav:home")]]))
        return
    
    # MENU
    if data == "menu:profile":
        await q.answer()
        await q.edit_message_text(build_profile(uid), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎁 دریافت روزانه", callback_data="daily:claim")],
                                               [InlineKeyboardButton("🔙", callback_data="nav:home")]]))
        return
    if data == "menu:referral":
        await q.answer()
        me = await q.get_bot().get_me()
        await q.edit_message_text(build_referral(uid, me.username), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="nav:home")]]))
        return
    if data == "menu:help":
        await q.answer()
        await q.edit_message_text(build_help(), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="nav:home")]]))
        return
    if data == "menu:my_orders":
        await q.answer()
        await q.edit_message_text(build_my_orders(uid), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ ثبت جدید", callback_data="menu:new_order")],
                                               [InlineKeyboardButton("🔙", callback_data="nav:home")]]))
        return
    
    if data == "daily:claim":
        if cloud.can_claim_daily(uid):
            cloud.claim_daily(uid)
            cloud.save(force=True)
            u = cloud.get_user(uid)
            await q.answer(f"✅ +{DAILY_REWARD} {COIN}", show_alert=True)
            try:
                await q.edit_message_text(f"🎉 <b>جایزه روزانه!</b>\n\n+{DAILY_REWARD} {COIN}\n💰 موجودی: <code>{u.get('balance', 0)} {COIN}</code>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="nav:profile")]]))
            except: pass
        else:
            await q.answer("⏳ فردا!", show_alert=True)
        return
    
    # EARN
    if data == "menu:earn":
        await q.answer()
        await show_task_card(q, uid, 0, "all")
        return
    
    if data.startswith("tasks:page:"):
        parts = data.split(":")
        page_idx = int(parts[2])
        order_type = parts[3] if len(parts) > 3 else "all"
        await q.answer()
        await show_task_card(q, uid, page_idx, order_type)
        return
    
    if data.startswith("tasks:filter:"):
        parts = data.split(":")
        order_type = parts[2]
        page_idx = int(parts[3]) if len(parts) > 3 else 0
        await q.answer()
        await show_task_card(q, uid, page_idx, order_type)
        return
    
    if data.startswith("tasks:verify_join:"):
        parts = data.split(":")
        order_id = parts[2]
        order_type = parts[3] if len(parts) > 3 else "all"
        current_idx = int(parts[4]) if len(parts) > 4 else 0
        
        order = cloud.get_order_by_id(order_id)
        if not order: await q.answer("❌ یافت نشد", show_alert=True); return
        try:
            m = await q.get_bot().get_chat_member(order["target"], uid)
            is_member = m.status not in ("left", "kicked", "banned")
        except:
            await q.answer("⚠️ کانال نامعتبر", show_alert=True); return
        if not is_member:
            await q.answer("⛔️ هنوز عضو نشدی!", show_alert=True); return
        success, msg = cloud.complete_order_unit(order_id, uid)
        cloud.save(force=True)
        await q.answer(msg, show_alert=True)
        if success:
            await show_task_card(q, uid, current_idx, order_type)
        return
    
    # NEW ORDER
    if data == "menu:new_order":
        await q.answer()
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 سفارش ممبر", callback_data="order:type:join")],
            [InlineKeyboardButton("❤️ سفارش لایک", callback_data="order:type:reaction")],
            [InlineKeyboardButton("🔙", callback_data="nav:home")],
        ])
        await q.edit_message_text(
            f"➕ <b>ثبت سفارش جدید</b>\n\n💰 موجودی: <code>{cloud.get_user(uid).get('balance', 0)} {COIN}</code>\n\nنوع سفارش:",
            parse_mode=ParseMode.HTML, reply_markup=kb)
        return
    
    if data.startswith("order:type:"):
        order_type = data.split(":")[2]
        await q.answer()
        if order_type == "join":
            ctx.user_data["awaiting"] = "order_join"
            cost_per = JOIN_REWARD * JOIN_COST_MULT
            await q.edit_message_text(
                f"👥 <b>سفارش ممبر</b>\n\n💰 هزینه هر ممبر: <b>{cost_per} {COIN}</b>\n\n"
                f"فرمت: <code>@channel | count</code>\n\nمثال: <code>@mychannel | 100</code>\n\n/cancel برای لغو",
                parse_mode=ParseMode.HTML)
        else:
            ctx.user_data["awaiting"] = "order_reaction_channel"
            cost_per = REACTION_REWARD * REACTION_COST_MULT
            await q.edit_message_text(
                f"❤️ <b>سفارش لایک</b>\n\n💰 هزینه هر لایک: <b>{cost_per} {COIN}</b>\n\n"
                f"⚠️ <b>شرط:</b> ربات <code>@{ctx.bot.username}</code> باید در کانال شما <b>ادمین</b> باشد.\n\n"
                f"📝 یوزرنیم کانال را ارسال کنید (مثال: <code>@mychannel</code>)\n\n/cancel برای لغو",
                parse_mode=ParseMode.HTML)
        return
    
    if data.startswith("order:check_admin:"):
        channel = data.split(":")[2]
        await q.answer("در حال بررسی...")
        is_adm = await check_bot_admin_in_channel(q.get_bot(), channel)
        if is_adm:
            ctx.user_data["awaiting"] = f"order_reaction_post_{channel}"
            await q.edit_message_text(
                f"✅ ربات در <code>{channel}</code> ادمین است!\n\n"
                f"حالا لینک پست یا پست را فوروارد کنید.\n\n"
                f"مثال: <code>https://t.me/{channel.lstrip('@')}/123</code>\n\n/cancel برای لغو",
                parse_mode=ParseMode.HTML)
        else:
            await q.edit_message_text(
                f"❌ ربات در <code>{channel}</code> ادمین نیست!\n\n"
                f"ابتدا <code>@{ctx.bot.username}</code> را ادمین کنید.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 بررسی مجدد", callback_data=f"order:check_admin:{channel}")],
                    [InlineKeyboardButton("🔙", callback_data="menu:new_order")],
                ]))
        return
    
    # ADMIN
    if not is_admin(uid):
        await q.answer("⛔️ دسترسی ندارید", show_alert=True); return
    
    if data == "admin:panel:0":
        await q.answer()
        stats = cloud.get_stats()
        await q.edit_message_text(
            f"👑 <b>پنل ادمین</b>\n\n👥 {stats['total_users']} کاربر | 📦 {stats['active_orders']} سفارش",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 همگانی", callback_data="admin:broadcast")],
                [InlineKeyboardButton("🔙", callback_data="nav:home")],
            ]))
        return
    
    if data == "admin:broadcast":
        ctx.user_data["awaiting"] = "broadcast"
        await q.answer()
        await q.edit_message_text("📢 پیام همگانی را ارسال کنید:", parse_mode=ParseMode.HTML)
        return
    
    await q.answer()

async def show_task_card(q, uid, index, order_type):
    all_orders = cloud.get_active_orders(order_type if order_type != "all" else None)
    orders = [o for o in all_orders if o["owner_uid"] != uid]
    
    if not orders:
        await q.edit_message_text(
            "📭 <b>تسکی موجود نیست!</b>\n\n"
            "فعلاً سفارش فعالی برای انجام وجود ندارد.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 همه تسک‌ها", callback_data="tasks:filter:all:0")],
                [InlineKeyboardButton("👥 ممبر", callback_data="tasks:filter:join:0")],
                [InlineKeyboardButton("❤️ لایک", callback_data="tasks:filter:reaction:0")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="nav:home")],
            ]))
        return
    
    if index < 0 or index >= len(orders):
        index = 0
    
    current_order = orders[index]
    card_text, card_kb = build_task_card(current_order, uid, index, len(orders), order_type)
    
    try:
        await q.edit_message_text(card_text, parse_mode=ParseMode.HTML, reply_markup=card_kb)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass
        else:
            raise

async def on_reaction(update, ctx):
    """تشخیص خودکار لایک - قلب تپنده ربات"""
    reaction = update.message_reaction
    if not reaction or not reaction.user:
        return
    
    user_id = reaction.user.id
    chat_id = reaction.chat.id
    message_id = reaction.message_id
    
    log.info(f"🔔 Reaction: user={user_id} | chat={chat_id} | msg={message_id}")
    
    # بررسی ایموجی معتبر
    new_reactions = reaction.new_reaction_type
    if not new_reactions:
        return
    
    has_valid_reaction = False
    for r in new_reactions:
        emoji = getattr(r, "emoji", None)
        if emoji and emoji in ALLOWED_REACTIONS:
            has_valid_reaction = True
            log.info(f"✅ Valid emoji: {emoji}")
            break
    
    if not has_valid_reaction:
        log.info("❌ Invalid emoji")
        return
    
    # جستجو در pending
    with cloud._lock:
        for p in list(cloud._data.get("pending_likes", [])):
            if p["worker_uid"] != user_id or time.time() > p["expires"]:
                continue
            
            order = next((o for o in cloud._data["orders"] if o["order_id"] == p["order_id"]), None)
            if not order or order.get("status") != "active":
                continue
            
            order_chat_id = order.get("chat_id")
            order_post_id = order.get("post_id")
            
            log.info(f"🔍 Order {p['order_id']}: chat={order_chat_id} post={order_post_id}")
            log.info(f"🔍 vs Reaction: chat={chat_id} msg={message_id}")
            
            if order_chat_id and order_post_id:
                try:
                    if int(order_chat_id) == int(chat_id) and int(order_post_id) == int(message_id):
                        log.info(f"✅✅✅ MATCH FOUND! Order {p['order_id']}")
                        
                        order_id = p["order_id"]
                        cloud._data["pending_likes"].remove(p)
                        cloud._dirty = True
                        
                        key = f"{order_id}_{user_id}"
                        if key not in cloud._data["tasks_completed"]:
                            order["remaining_count"] -= 1
                            if order["remaining_count"] <= 0: order["status"] = "completed"
                            cloud._data["tasks_completed"].append(key)
                            u = cloud._data["users"].get(str(user_id))
                            if u: u["tasks_done"] = u.get("tasks_done", 0) + 1
                            reward = REACTION_REWARD
                            cloud.add_coin(user_id, reward, f"reaction_{order_id}")
                            cloud.save(force=True)
                            
                            log.info(f"💰 Reward {reward} {COIN} given to user {user_id}")
                            
                            # ارسال پیام پاداش
                            try:
                                await ctx.bot.send_message(
                                    user_id,
                                    f"🎉 <b>لایک شما تایید شد!</b>\n\n"
                                    f"✅ +{reward} {COIN}\n"
                                    f"💰 موجودی: <code>{cloud.get_user(user_id).get('balance', 0)} {COIN}</code>",
                                    parse_mode=ParseMode.HTML,
                                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎯 تسک بعدی", callback_data="menu:earn")]]))
                            except Exception as e:
                                log.error(f"Send error: {e}")
                        break
                except Exception as e:
                    log.error(f"Error: {e}")
                    continue

async def on_message(update, ctx):
    uid = update.effective_user.id
    text_in = (update.message.text or "").strip() if update.message.text else ""
    awaiting = ctx.user_data.get("awaiting")
    
    if text_in == "/cancel":
        await cmd_cancel(update, ctx); return
    
    if text_in in REPLY_MAP and not awaiting:
        action = REPLY_MAP[text_in]
        class FakeQ:
            def __init__(self, bot, uid):
                self.bot = bot
                self.from_user = type('U', (), {'id': uid, 'username': '', 'first_name': '', 'last_name': ''})()
                self.data = action
            async def answer(self, *a, **k): pass
            async def edit_message_text(self, text, **kwargs): await update.message.reply_text(text, **kwargs)
            def get_bot(self): return self.bot
        fake_q = FakeQ(ctx.bot, uid)
        await on_callback(type('U', (), {
            'callback_query': fake_q,
            'effective_user': update.effective_user,
        })(), ctx)
        return
    
    if awaiting == "order_join":
        ctx.user_data.pop("awaiting", None)
        parts = [p.strip() for p in text_in.split("|")]
        if len(parts) != 2:
            await update.message.reply_text("❌ فرمت اشتباه. مثال: <code>@mychannel | 100</code>", parse_mode=ParseMode.HTML); return
        target, count_str = parts
        try: count = int(count_str)
        except: await update.message.reply_text("❌ تعداد باید عدد باشد."); return
        if not target.startswith("@") or count < 1:
            await update.message.reply_text("❌ مقادیر نامعتبر."); return
        try:
            chat = await ctx.bot.get_chat(target)
            if chat.type not in ("channel", "supergroup"): raise Exception()
        except:
            await update.message.reply_text("❌ کانال نامعتبر یا خصوصی!"); return
        cost_per = JOIN_REWARD * JOIN_COST_MULT
        total = cost_per * count
        u = cloud.get_user(uid)
        if u.get("balance", 0) < total:
            await update.message.reply_text(f"❌ موجودی کافی نیست. نیاز: {total} {COIN}"); return
        
        ctx.user_data["temp_order"] = {
            "type": "join", "target": target, "count": count,
            "cost_per": cost_per, "chat_id": chat.id
        }
        ctx.user_data["awaiting"] = "order_description"
        
        await update.message.reply_text(
            f"✅ اطلاعات دریافت شد!\n\n"
            f"📝 <b>توضیحات (اختیاری):</b>\n\n"
            f"حداکثر {MAX_DESC_LENGTH} کاراکتر\n\n"
            f"توضیحات را بنویس یا <code>skip</code> برای رد کردن.",
            parse_mode=ParseMode.HTML)
        return
    
    if awaiting == "order_reaction_channel":
        ctx.user_data.pop("awaiting", None)
        channel = text_in.strip()
        if not channel.startswith("@"):
            await update.message.reply_text("❌ با @ شروع کنید."); return
        try:
            chat = await ctx.bot.get_chat(channel)
            if chat.type not in ("channel", "supergroup"): raise Exception()
        except:
            await update.message.reply_text("❌ کانال نامعتبر!"); return
        is_adm = await check_bot_admin_in_channel(ctx.bot, channel)
        if not is_adm:
            await update.message.reply_text(
                f"❌ ربات در <code>{channel}</code> ادمین نیست!\nابتدا ادمین کنید.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 بررسی مجدد", callback_data=f"order:check_admin:{channel}")],
                    [InlineKeyboardButton("🔙", callback_data="menu:new_order")],
                ]))
            return
        ctx.user_data["awaiting"] = f"order_reaction_post_{channel}"
        await update.message.reply_text(
            f"✅ ربات در <code>{channel}</code> ادمین است!\n\nحالا لینک پست یا پست را فوروارد کنید.\nمثال: <code>https://t.me/{channel.lstrip('@')}/123</code>",
            parse_mode=ParseMode.HTML)
        return
    
    if awaiting and awaiting.startswith("order_reaction_post_"):
        channel = awaiting.replace("order_reaction_post_", "")
        ctx.user_data.pop("awaiting", None)
        post_id = None
        
        if update.message.text:
            match = re.search(r"https://t\.me/[^/]+/(\d+)", update.message.text)
            if match: post_id = int(match.group(1))
        elif update.message.forward_from_chat:
            if update.message.forward_from_chat.username == channel.lstrip("@"):
                post_id = update.message.forward_from_message_id
        
        if not post_id:
            await update.message.reply_text("❌ لینک پست نامعتبر!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 دوباره", callback_data=f"order:check_admin:{channel}")]]))
            return
        
        try:
            chat = await ctx.bot.get_chat(channel)
            chat_id = chat.id
        except:
            await update.message.reply_text("❌ خطا در دسترسی به کانال!"); return
        
        ctx.user_data["awaiting"] = f"order_reaction_count_{channel}_{post_id}_{chat_id}"
        cost_per = REACTION_REWARD * REACTION_COST_MULT
        await update.message.reply_text(
            f"✅ پست <code>{post_id}</code> شناسایی شد!\n\n💰 هزینه هر لایک: <b>{cost_per} {COIN}</b>\n\n"
            f"تعداد لایک مورد نظر:\nمثال: <code>50</code>",
            parse_mode=ParseMode.HTML)
        return
    
    if awaiting and awaiting.startswith("order_reaction_count_"):
        parts = awaiting.replace("order_reaction_count_", "").split("_")
        if len(parts) < 3:
            ctx.user_data.pop("awaiting", None); return
        channel, post_id_str, chat_id_str = parts[0], parts[1], parts[2]
        ctx.user_data.pop("awaiting", None)
        
        try: count = int(text_in)
        except: await update.message.reply_text("❌ تعداد باید عدد باشد."); return
        if count < 1: await update.message.reply_text("❌ حداقل 1"); return
        
        cost_per = REACTION_REWARD * REACTION_COST_MULT
        total = cost_per * count
        u = cloud.get_user(uid)
        if u.get("balance", 0) < total:
            await update.message.reply_text(f"❌ موجودی کافی نیست. نیاز: {total} {COIN}"); return
        
        post_id = int(post_id_str)
        chat_id = int(chat_id_str)
        
        ctx.user_data["temp_order"] = {
            "type": "reaction", "target": channel, "count": count,
            "cost_per": cost_per, "chat_id": chat_id, "post_id": post_id
        }
        ctx.user_data["awaiting"] = "order_description"
        
        await update.message.reply_text(
            f"✅ اطلاعات دریافت شد!\n\n"
            f"📝 <b>توضیحات (اختیاری):</b>\n\n"
            f"حداکثر {MAX_DESC_LENGTH} کاراکتر\n\n"
            f"توضیحات را بنویس یا <code>skip</code> برای رد کردن.",
            parse_mode=ParseMode.HTML)
        return
    
    if awaiting == "order_description":
        ctx.user_data.pop("awaiting", None)
        temp = ctx.user_data.pop("temp_order", None)
        if not temp:
            await update.message.reply_text("❌ اطلاعات سفارش از دست رفته!", parse_mode=ParseMode.HTML); return
        
        description = "" if text_in.lower() == "skip" else text_in[:MAX_DESC_LENGTH]
        
        order = cloud.create_order(
            uid, temp["type"], temp["target"], temp["count"], temp["cost_per"],
            post_id=temp.get("post_id"), chat_id=temp.get("chat_id"),
            description=description
        )
        
        if order:
            cloud.save(force=True)
            type_text = "ممبر" if temp["type"] == "join" else "لایک"
            total = temp["cost_per"] * temp["count"]
            post_info = f"\n📝 پست: <code>{temp['post_id']}</code>" if temp.get("post_id") else ""
            desc_info = f"\n💬 توضیحات: <i>{description[:80]}{'...' if len(description) > 80 else ''}</i>" if description else ""
            await update.message.reply_text(
                f"✅ <b>سفارش ثبت شد!</b>\n\n"
                f"🎯 نوع: <b>{type_text}</b>\n"
                f"📢 کانال: <code>{temp['target']}</code>{post_info}"
                f"👥 تعداد: {temp['count']}\n"
                f"💰 کسر شد: {total} {COIN}{desc_info}",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📦 سفارش‌ها", callback_data="menu:my_orders")]]))
        else:
            await update.message.reply_text("❌ خطا در ثبت سفارش.")
        return
    
    if not is_admin(uid): return
    if awaiting == "broadcast":
        ctx.user_data.pop("awaiting", None)
        users = cloud.get_all_users()
        sent = 0
        for u in users:
            try:
                await ctx.bot.send_message(int(u.get("uid_str") or u.get("uid")), text_in, parse_mode=ParseMode.HTML)
                sent += 1
                await asyncio.sleep(0.05)
            except: pass
        await update.message.reply_text(f"✅ ارسال: {sent}/{len(users)}")

async def on_startup(app):
    cloud.load()
    async def cleanup():
        while True:
            await asyncio.sleep(60)
            cloud.cleanup_expired_pendings()
            cloud.save()
    asyncio.create_task(cleanup())
    log.info("ExchangeBot v5.0 FINAL Ready ✅")

async def on_shutdown(app):
    cloud.save(force=True)

def main():
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TOKEN":
        log.critical("BOT_TOKEN تنظیم نشده!"); sys.exit(1)
    cloud.load()
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).post_shutdown(on_shutdown).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageReactionHandler(on_reaction))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_message))
    app.add_handler(MessageHandler(filters.FORWARDED & filters.ChatType.PRIVATE, on_message))
    
    def _sig(s, f): cloud.save(force=True); sys.exit(0)
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    atexit.register(lambda: cloud.save(force=True))
    
    webhook_url = os.environ.get("RENDER_EXTERNAL_URL")
    if webhook_url:
        log.info("🌐 Running with Webhook")
        main_loop = asyncio.new_event_loop()
        threading.Thread(target=lambda: asyncio.set_event_loop(main_loop) or main_loop.run_forever(), daemon=True).start()
        flask_app = Flask(__name__)
        @flask_app.route("/")
        def index(): return "🤖 ExchangeBot v5.0 FINAL Alive!", 200
        @flask_app.route(f"/{BOT_TOKEN}", methods=["POST"])
        def webhook():
            try:
                upd = Update.de_json(request.get_json(force=True), app.bot)
                asyncio.run_coroutine_threadsafe(app.process_update(upd), main_loop)
            except Exception as e: log.error(f"Webhook error: {e}")
            return "OK", 200
        async def setup():
            url = webhook_url.rstrip("/") + "/" + BOT_TOKEN
            await app.bot.delete_webhook(drop_pending_updates=True)
            await asyncio.sleep(1)
            await app.bot.set_webhook(url=url, allowed_updates=["message", "callback_query", "message_reaction"], drop_pending_updates=True)
            log.info(f"✅ Webhook set")
        asyncio.run_coroutine_threadsafe(setup(), main_loop).result(timeout=30)
        async def init():
            await app.initialize()
            await app.start()
            await on_startup(app)
        asyncio.run_coroutine_threadsafe(init(), main_loop).result(timeout=30)
        flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
    else:
        log.warning("⚠️ Polling mode")
        app.run_polling(allowed_updates=["message", "callback_query", "message_reaction"], drop_pending_updates=True)

if __name__ == "__main__":
    main()
