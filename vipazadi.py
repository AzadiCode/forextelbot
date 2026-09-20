#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Azadi VIP Trading Bot — WebApp Edition
════════════════════════════════════════════════════════════════════
تک فایل: Telegram Bot + Flask API + MongoDB
"""

from __future__ import annotations
import os, sys, json, time, hmac, hashlib, asyncio, logging, threading, signal, atexit
import json
from io import BytesIO
from pywebpush import webpush, WebPushException
from datetime import datetime, timedelta, timezone
from typing import Optional
from functools import wraps
import urllib.parse
import re

from flask import Flask, request, jsonify, g
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, WebAppInfo
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter
import html as _html
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters,
)
from pymongo import MongoClient

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO, stream=sys.stdout,
)
log = logging.getLogger("AzadiVIP")



# ══════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
MONGODB_URI = os.environ.get("MONGODB_URI", "").strip()
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://vebapp.onrender.com").strip().rstrip("/")
API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("PORT", os.environ.get("API_PORT", "8000")))

_admin_raw = os.environ.get("ADMIN_IDS", os.environ.get("ADMIN_ID", "605611554"))
ADMIN_IDS = {int(x) for x in _admin_raw.replace(" ", "").split(",") if x.strip().isdigit()}

FORCE_JOIN_CHANNEL = os.environ.get("FORCE_JOIN_CHANNEL", "@GoldLiveHunters")
VIP_CHANNEL_ID = os.environ.get("VIP_CHANNEL_ID", "-1003988077931", "-1004296206176")

def get_vip_channel_id_full() -> Optional[int]:
    """آیدی عددی کامل کانال VIP (با پیشوند -100) را برمی‌گرداند، یا None اگر نامعتبر باشد."""
    raw = str(VIP_CHANNEL_ID).strip()
    if not raw.lstrip("-").isdigit():
        return None
    vip_id = int(raw)
    if not raw.startswith("-100"):
        vip_id = int("-100" + str(abs(vip_id)))
    return vip_id

PLAN_WEEKLY_USD = float(os.environ.get("PLAN_WEEKLY_USD", "20"))
PLAN_MONTHLY_USD = float(os.environ.get("PLAN_MONTHLY_USD", "60"))
PLAN_WEEKLY_DAYS = 7
PLAN_MONTHLY_DAYS = 30

DISCOUNT_CODE = os.environ.get("DISCOUNT_CODE", "AZADI30")
DISCOUNT_PERCENT = float(os.environ.get("DISCOUNT_PERCENT", "30"))
# مدت جشنواره (ساعت) — از لحظه‌ای که ادمین آن را فعال می‌کند
PROMO_HOURS = float(os.environ.get("PROMO_HOURS", "24"))
# پلنی که کد جشنواره فقط روی آن کار می‌کند
PROMO_PLAN = "monthly"

VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "-3445WNET5gQwfQt0upQ3r_PH9iGHIm6OyEB1J9Ue8g")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "BF1laYA2pDxRak_LkTZnA7VgotZWEibqHueGwyl73zk0PBP8BjK_wIXGfjZ78I0_bussAAkVOKoq3tTUpSbdHDs")
VAPID_EMAIL = os.environ.get("VAPID_EMAIL", "mailto:azadimybusiness@gmail.com")

PAYMENT_INFO = os.environ.get("PAYMENT_INFO",
    "💳 <b>اطلاعات پرداخت:</b>\n"
    "USDT BEP20(BSC):\n"
    "<code>0x64a20067a62852b26556c87b5082aaa0c2359640</code>")

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))

if not BOT_TOKEN:
    log.critical("❌ BOT_TOKEN تنظیم نشده!")
    sys.exit(1)


# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════
def now_ir() -> datetime:
    return datetime.now(IRAN_TZ)

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
    return " و ".join(parts) if parts else "کمتر از یک دقیقه"

def get_discounted_price(price: float) -> float:
    if DISCOUNT_PERCENT <= 0 or DISCOUNT_PERCENT > 100:
        return price
    return price * (100 - DISCOUNT_PERCENT) / 100

def verify_telegram_init_data(init_data: str, bot_token: str) -> Optional[dict]:
    try:
        parsed_pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
        parsed = {k: v for k, v in parsed_pairs}
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected_hash = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_hash, received_hash):
            return None
        user_str = parsed.get("user", "{}")
        return json.loads(user_str)
    except Exception as e:
        log.error(f"initData verify error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
# STORAGE (MongoDB + Memory fallback)
# ══════════════════════════════════════════════════════════════════════
class Storage:
    def __init__(self):
        self._is_memory = False
        self._lock = threading.Lock()  # ← جلوگیری از race condition روی دیکشنری‌های حافظه (Flask threaded + bot thread)
        if MONGODB_URI:
            try:
                self._client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000)
                self._client.admin.command('ping')
                db = self._client.get_database()
                self._users = db["users"]
                self._payments = db["payments"]
                self._subscriptions = db["subscriptions"]
                self._meta = db["meta"]
                self._users.create_index("uid", unique=True)
                self._payments.create_index("payment_id", unique=True)
                log.info("✅ MongoDB متصل شد")
            except Exception as e:
                log.warning(f"⚠️ MongoDB خطا: {e}. Memory mode")
                self._use_memory()
        else:
            log.warning("⚠️ MONGODB_URI تنظیم نشده. Memory mode")
            self._use_memory()
    
    def _use_memory(self):
        self._is_memory = True
        self._users = {}
        self._payments = {}
        self._subscriptions = {}
        self._meta = {"archive_text": "Coming Soon ..."}
    
    # ── Users ──
    def ensure_user(self, uid: int, username: str = "", full_name: str = "") -> dict:
        if self._is_memory:
            with self._lock:
                if uid in self._users:
                    u = self._users[uid]
                    if username: u["username"] = username
                    if full_name: u["full_name"] = full_name
                    # ✅ اضافه کردن default برای used_discount (اگر وجود ندارد)
                    u.setdefault("used_discount", False)
                    return u
                new = {
                    "uid": uid, "username": username, "full_name": full_name,
                    "vip_expiry": None, "vip_plan": None,
                    "is_blocked": False, "last_link": None,
                    "warned_expired": False, "grace_until": None,
                    "created_at": now_ir().isoformat(),
                }
                self._users[uid] = new
                return new
        else:
            doc = self._users.find_one({"uid": uid})
            if doc:
                update = {}
                if username and doc.get("username") != username: update["username"] = username
                if full_name and doc.get("full_name") != full_name: update["full_name"] = full_name
                
                # ✅ اگر used_discount در دیتابیس نیست، اضافه کن
                if "used_discount" not in doc:
                    update["used_discount"] = False
                
                if update:
                    self._users.update_one({"uid": uid}, {"$set": update})
                    doc.update(update)
                doc.pop("_id", None)
                return doc
            new = {
                "uid": uid, "username": username, "full_name": full_name,
                "vip_expiry": None, "vip_plan": None,
                "is_blocked": False, "last_link": None,
                "warned_expired": False, "grace_until": None,
                "created_at": now_ir().isoformat(),
            }
            self._users.insert_one(new)
            return new
    
    def get_user(self, uid: int) -> Optional[dict]:
        if self._is_memory:
            return self._users.get(uid)
        doc = self._users.find_one({"uid": uid})
        if doc: doc.pop("_id", None)
        return doc
    
    def get_all_users(self) -> list:
        if self._is_memory:
            return list(self._users.values())
        return list(self._users.find({}, {"_id": 0}))
    
    def get_vip_users(self) -> list:
        now = now_ir()
        result = []
        users = self.get_all_users()
        for u in users:
            if u.get("grace_until"):
                try:
                    if datetime.fromisoformat(u["grace_until"]) > now:
                        result.append(u); continue
                except: pass
            if u.get("vip_expiry"):
                try:
                    if datetime.fromisoformat(u["vip_expiry"]) > now:
                        result.append(u)
                except: pass
        return result
    
    def activate_vip(self, uid: int, plan: str, days: int) -> Optional[datetime]:
        if self._is_memory:
            with self._lock:
                user = self._users.get(uid)
                if not user: return None
                if user.get("vip_expiry"):
                    try:
                        current = datetime.fromisoformat(user["vip_expiry"])
                        base = current if current > now_ir() else now_ir()
                    except:
                        base = now_ir()
                else:
                    base = now_ir()
                new_expiry = base + timedelta(days=days)
                user["vip_expiry"] = new_expiry.isoformat()
                user["vip_plan"] = plan
                user["is_blocked"] = False
                user["warned_expired"] = False
                user["grace_until"] = None
                return new_expiry
        
        user = self.get_user(uid)
        if not user: return None
        if user.get("vip_expiry"):
            try:
                current = datetime.fromisoformat(user["vip_expiry"])
                base = current if current > now_ir() else now_ir()
            except:
                base = now_ir()
        else:
            base = now_ir()
        new_expiry = base + timedelta(days=days)
        self._users.update_one({"uid": uid}, {"$set": {
            "vip_expiry": new_expiry.isoformat(),
            "vip_plan": plan,
            "is_blocked": False,
            "warned_expired": False,
            "grace_until": None,
        }})
        return new_expiry
    
    def save_invite_link(self, uid: int, link: str):
        if self._is_memory:
            if uid in self._users: self._users[uid]["last_link"] = link
        else:
            self._users.update_one({"uid": uid}, {"$set": {"last_link": link}})
    
    # ── Payments ──
    def create_payment(self, uid: int, plan: str, amount: float) -> str:
        payment_id = hashlib.md5(f"{uid}{time.time()}".encode()).hexdigest()[:12]
        payment = {
            "payment_id": payment_id,
            "uid": uid, "plan": plan, "amount_usd": amount,
            "created_at": now_ir().isoformat(), "status": "pending",
        }
        if self._is_memory:
            self._payments[payment_id] = payment
        else:
            self._payments.insert_one(payment)
        return payment_id
    
    def get_payment(self, payment_id: str) -> Optional[dict]:
        if self._is_memory:
            return self._payments.get(payment_id)
        doc = self._payments.find_one({"payment_id": payment_id})
        if doc: doc.pop("_id", None)
        return doc
    
    def get_user_pending_payment(self, uid: int) -> Optional[dict]:
        if self._is_memory:
            for p in self._payments.values():
                if p.get("uid") == uid and p.get("status") == "pending":
                    return p
            return None
        doc = self._payments.find_one({"uid": uid, "status": "pending"})
        if doc: doc.pop("_id", None)
        return doc
    
    def approve_payment(self, payment_id: str) -> bool:
        if self._is_memory:
            with self._lock:
                p = self._payments.get(payment_id)
                if p and p["status"] == "pending":
                    p["status"] = "approved"
                    p["approved_at"] = now_ir().isoformat()
                    return True
                return False
        else:
            result = self._payments.update_one(
                {"payment_id": payment_id, "status": "pending"},
                {"$set": {"status": "approved", "approved_at": now_ir().isoformat()}}
            )
            return result.modified_count > 0
    
    def reject_payment(self, payment_id: str) -> bool:
        if self._is_memory:
            with self._lock:
                p = self._payments.get(payment_id)
                if p and p["status"] == "pending":
                    p["status"] = "rejected"
                    return True
                return False
        else:
            result = self._payments.update_one(
                {"payment_id": payment_id, "status": "pending"},
                {"$set": {"status": "rejected"}}
            )
            return result.modified_count > 0
    
    def cancel_payment(self, payment_id: str) -> bool:
        if self._is_memory:
            with self._lock:
                p = self._payments.get(payment_id)
                if p and p["status"] == "pending":
                    p["status"] = "cancelled"
                    return True
                return False
        else:
            result = self._payments.update_one(
                {"payment_id": payment_id, "status": "pending"},
                {"$set": {"status": "cancelled"}}
            )
            return result.modified_count > 0

    def claim_payment_activation(self, payment_id: str) -> Optional[dict]:
        """
        اتمی: یک پرداخت approved-و-هنوز-فعال‌نشده را «claim» می‌کند — یعنی فقط
        دقیقاً یک فراخوانی همزمان می‌تواند موفق شود (چه دو تب مرورگر، چه دابل‌کلیک،
        چه ربات و وب‌اپ هم‌زمان). بدون این قفل/فیلتر، دو درخواست هم‌زمان می‌توانستند
        هر دو از چک «approved and not activated» رد شوند و VIP را دوبار تمدید کنند.
        در صورت موفقیت خودِ payment را برمی‌گرداند، وگرنه None.
        """
        if self._is_memory:
            with self._lock:
                p = self._payments.get(payment_id)
                if not p or p.get("status") != "approved" or p.get("activated"):
                    return None
                p["activated"] = True
                p["activated_at"] = now_ir().isoformat()
                return dict(p)
        else:
            doc = self._payments.find_one_and_update(
                {"payment_id": payment_id, "status": "approved", "activated": {"$ne": True}},
                {"$set": {"activated": True, "activated_at": now_ir().isoformat()}},
            )
            if doc:
                doc.pop("_id", None)
            return doc
    
    @staticmethod
    def is_receipt_submitted(p: dict) -> bool:
        """پرداخت فقط وقتی برای ادمین قابل‌نمایش است که کاربر رسید (tx_id) را ثبت کرده باشد"""
        return bool(p.get("submitted_at")) and bool((p.get("tx_id") or "").strip())

    def get_all_pending_payments(self) -> list:
        if self._is_memory:
            return [p for p in self._payments.values() if p.get("status") == "pending"]
        return list(self._payments.find({"status": "pending"}, {"_id": 0}))
    
    def get_user_payments(self, uid: int) -> list:
        if self._is_memory:
            return [p for p in self._payments.values() if p.get("uid") == uid]
        return list(self._payments.find({"uid": uid}, {"_id": 0}))
    
    # ── Meta (Archive) ──
    def get_archive_text(self) -> str:
        if self._is_memory:
            return self._meta.get("archive_text", "Coming Soon ...")
        doc = self._meta.find_one({"_id": "archive"})
        return doc.get("text", "Coming Soon ...") if doc else "Coming Soon ..."
    
    def set_archive_text(self, text: str):
        if self._is_memory:
            self._meta["archive_text"] = text
        else:
            self._meta.update_one({"_id": "archive"}, {"$set": {"text": text}}, upsert=True)

    # ── Promo (جشنواره‌ی تخفیف اولین عضویت) ──
    def get_promo(self) -> dict:
        """{'active': bool, 'started_at': epoch|None}؛ پیش‌فرض: غیرفعال"""
        default = {"active": False, "started_at": None}
        if self._is_memory:
            return dict(self._meta.get("promo") or default)
        doc = self._meta.find_one({"_id": "promo"})
        if not doc:
            return default
        return {"active": bool(doc.get("active")), "started_at": doc.get("started_at")}

    def set_promo(self, active: bool) -> dict:
        """فعال‌سازی = شروع ۲۴ ساعت جدید؛ غیرفعال‌سازی = خاموش فوری"""
        cur = self.get_promo()
        new = {"active": bool(active), "started_at": time.time() if active else cur.get("started_at")}
        if self._is_memory:
            self._meta["promo"] = new
        else:
            self._meta.update_one({"_id": "promo"}, {"$set": new}, upsert=True)
        return new

    # ── Notifications (Internal WebApp) ──
    def _ensure_notifications_collection(self):
        """مطمئن شو collection notifications وجود دارد"""
        if not hasattr(self, '_notifications_col'):
            if self._is_memory:
                self._notifications_col = []
            else:
                self._notifications_col = self._client.get_database()["notifications"]
        return self._notifications_col

    def create_notification(self, title: str, body: str, icon: str = "chart",
                        target: str = "all", user_ids: list = None) -> str:
        """ایجاد اعلان داخلی"""
        col = self._ensure_notifications_collection()
        
        notif_id = hashlib.md5(f"{title}{time.time()}".encode()).hexdigest()[:16]
        notification = {
            "notif_id": notif_id,
            "title": title,
            "body": body,
            "icon": icon,
            "target": target,  # all, vip, specific
            "user_ids": user_ids or [],
            "created_at": now_ir().isoformat(),
        }
        
        if self._is_memory:
            col.append(notification)
        else:
            col.insert_one(notification)
        
        return notif_id

    def get_notifications_after(self, since_iso: str, uid: int, is_vip: bool) -> list:
        """گرفتن اعلان‌های جدید برای کاربر خاص"""
        col = self._ensure_notifications_collection()
        
        if self._is_memory:
            result = []
            for n in col:
                if n.get("created_at", "") <= since_iso:
                    continue
                
                target = n.get("target", "all")
                if target == "all":
                    result.append(n)
                elif target == "vip" and is_vip:
                    result.append(n)
                elif target == "specific" and uid in n.get("user_ids", []):
                    result.append(n)
            
            result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return result[:20]
        else:
            query = {"created_at": {"$gt": since_iso}}
            cursor = col.find(query, {"_id": 0}).sort("created_at", -1).limit(50)
            
            result = []
            for n in cursor:
                target = n.get("target", "all")
                if target == "all":
                    result.append(n)
                elif target == "vip" and is_vip:
                    result.append(n)
                elif target == "specific" and uid in n.get("user_ids", []):
                    result.append(n)
            
            return result[:20]

    # ── Stats ──
    def get_stats(self) -> dict:
        users = self.get_all_users()
        payments = []
        if self._is_memory:
            payments = list(self._payments.values())
        else:
            payments = list(self._payments.find({}, {"_id": 0}))
        
        vip_users = self.get_vip_users()
        approved = [p for p in payments if p.get("status") == "approved"]
        total_revenue = sum(p.get("amount_usd", 0) for p in approved)
        
        return {
            "total_users": len(users),
            "vip_users": len(vip_users),
            "total_revenue": total_revenue,
            "pending_payments": len([p for p in payments
                                     if p.get("status") == "pending" and self.is_receipt_submitted(p)]),
        }
    # ── Push Subscriptions ──
    def _ensure_push_subs_collection(self):
        if not hasattr(self, '_push_subs_col'):
            if self._is_memory:
                self._push_subs_col = []
            else:
                self._push_subs_col = self._client.get_database()["push_subscriptions"]
                self._push_subs_col.create_index("endpoint", unique=True)
        return self._push_subs_col

    def save_push_subscription(self, uid: int, subscription: dict) -> bool:
        """ذخیره اشتراک push کاربر"""
        col = self._ensure_push_subs_collection()
        endpoint = subscription.get("endpoint")
        
        doc = {
            "endpoint": endpoint,
            "uid": uid,
            "keys": subscription.get("keys", {}),
            "created_at": now_ir().isoformat(),
        }
        
        try:
            if self._is_memory:
                with self._lock:
                    # Remove existing
                    self._push_subs_col = [s for s in self._push_subs_col if s.get("endpoint") != endpoint]
                    self._push_subs_col.append(doc)
            else:
                col.update_one(
                    {"endpoint": endpoint},
                    {"$set": doc},
                    upsert=True
                )
            return True
        except Exception as e:
            log.error(f"Save push sub error: {e}")
            return False

    def get_user_push_subscriptions(self, uid: int) -> list:
        col = self._ensure_push_subs_collection()
        if self._is_memory:
            return [s for s in self._push_subs_col if s.get("uid") == uid]
        else:
            return list(col.find({"uid": uid}, {"_id": 0}))

    def get_all_push_subscriptions(self, target: str = "all", user_ids: list = None) -> list:
        """گرفتن همه اشتراک‌ها بر اساس target"""
        col = self._ensure_push_subs_collection()
        
        if self._is_memory:
            all_subs = self._push_subs_col
        else:
            all_subs = list(col.find({}, {"_id": 0}))
        
        if target == "all":
            return all_subs
        
        # نیاز به فیلتر بر اساس کاربران
        all_users = self.get_all_users()
        now = now_ir()
        
        valid_uids = set()
        for u in all_users:
            if target == "vip":
                if u.get("vip_expiry"):
                    try:
                        exp = datetime.fromisoformat(u["vip_expiry"])
                        if exp > now:
                            valid_uids.add(u.get("uid"))
                    except: pass
            elif target == "specific":
                if u.get("uid") in (user_ids or []):
                    valid_uids.add(u.get("uid"))
        
        return [s for s in all_subs if s.get("uid") in valid_uids]

    def remove_push_subscription(self, endpoint: str):
        col = self._ensure_push_subs_collection()
        if self._is_memory:
            with self._lock:
                self._push_subs_col = [s for s in self._push_subs_col if s.get("endpoint") != endpoint]
        else:
            col.delete_one({"endpoint": endpoint})



storage = Storage()


# ══════════════════════════════════════════════════════════════════════
# LEGACY MIGRATION — انتقال کاربران/اشتراک‌های ربات قدیمی (تک‌داکیومنتی) به ساختار جدید
# ══════════════════════════════════════════════════════════════════════
# ربات قدیمی همه‌چیز را در یک داکیومنت واحد نگه می‌داشت:
#   collection "azadi_bot_data" ← document {_id: "main", users: {"<uid>": {...}},
#                                          pending_payments: {...}, pending_subscriptions: {...}}
# ساختار جدید: collectionهای جدا (users / payments / meta). شناسه‌ی کاربر (uid تلگرام) در هر دو
# یکی است، پس کاربر قدیمی بدون هیچ کاری از سمت خودش اشتراکش را در وب‌اپ می‌بیند.
LEGACY_COLLECTION = os.environ.get("LEGACY_COLLECTION", "azadi_bot_data")

def _parse_dt(s):
    try:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=IRAN_TZ)
    except Exception:
        return None

def migrate_legacy_data(force: bool = False) -> dict:
    """
    یک‌بار اجرا می‌شود (با فلگ در collection meta) و idempotent است:
      • کاربران: اگر نبودند ساخته می‌شوند؛ اگر بودند، فقط «انقضای دیرتر» و فیلدهای خالی پر می‌شود
        (هیچ‌وقت اشتراک کاربر جدید کم نمی‌شود)
      • مهلت (grace) فعال قدیمی به زمان اشتراک واقعی تبدیل می‌شود
      • اشتراک‌های «آماده فعال‌سازی» قدیمی → پرداخت approved با activated=False (دکمه فعال‌سازی می‌آید)
      • پرداخت‌های approved قدیمی → تاریخچه (activated=True)؛ pending قدیمی → در پنل ادمین می‌آید
    """
    if storage._is_memory:
        return {"skipped": "memory-mode"}
    try:
        flag = storage._meta.find_one({"_id": "legacy_migration"})
        if flag and not force:
            return {"skipped": "already-done", **{k: v for k, v in flag.items() if k != "_id"}}

        legacy_uri = os.environ.get("LEGACY_MONGODB_URI", "").strip()
        if legacy_uri and legacy_uri != MONGODB_URI:
            legacy_db = MongoClient(legacy_uri, serverSelectionTimeoutMS=10000).get_database()
        else:
            legacy_db = storage._client.get_database()

        doc = legacy_db[LEGACY_COLLECTION].find_one({"_id": "main"})
        if not doc:
            log.info("ℹ️ Legacy migration: داده‌ی ربات قدیمی پیدا نشد (فلگ ثبت نشد)")
            return {"skipped": "no-legacy-doc"}

        now = now_ir()
        stats = {"users_created": 0, "users_merged": 0, "active_carried": 0,
                 "ready_subs": 0, "payments": 0}

        # ── کاربران ──
        for key, lu in (doc.get("users") or {}).items():
            try:
                uid = int(lu.get("uid") or key)
            except Exception:
                continue

            # زمان مؤثر = دیرتر بین انقضای اشتراک و مهلت فعال
            cands = [d for d in (_parse_dt(lu.get("vip_expiry")), _parse_dt(lu.get("grace_until"))) if d]
            grace = _parse_dt(lu.get("grace_until"))
            eff = max(cands) if cands else None
            if grace and grace <= now:
                eff = _parse_dt(lu.get("vip_expiry"))

            cur = storage._users.find_one({"uid": uid})
            if not cur:
                new = {
                    "uid": uid,
                    "username": lu.get("username") or "",
                    "full_name": lu.get("full_name") or "",
                    "vip_expiry": eff.isoformat() if eff else None,
                    "vip_plan": lu.get("vip_plan"),
                    "is_blocked": bool(lu.get("is_blocked")),
                    "last_link": lu.get("last_link"),
                    "warned_expired": bool(lu.get("warned_expired")),
                    "grace_until": None,
                    "used_discount": False,
                    "created_at": lu.get("created_at") or now.isoformat(),
                    "migrated_from_legacy": True,
                }
                storage._users.insert_one(new)
                stats["users_created"] += 1
            else:
                upd = {}
                cur_exp = _parse_dt(cur.get("vip_expiry"))
                if eff and (not cur_exp or eff > cur_exp):
                    upd.update({"vip_expiry": eff.isoformat(), "vip_plan": lu.get("vip_plan") or cur.get("vip_plan"),
                                "is_blocked": False, "warned_expired": bool(eff <= now and lu.get("warned_expired"))})
                for f in ("username", "full_name", "last_link"):
                    if not cur.get(f) and lu.get(f): upd[f] = lu[f]
                if upd:
                    upd["migrated_from_legacy"] = True
                    storage._users.update_one({"uid": uid}, {"$set": upd})
                    stats["users_merged"] += 1
            if eff and eff > now:
                stats["active_carried"] += 1

        # ── اشتراک‌های آماده فعال‌سازی (پرداخت تاییدشده‌ای که هنوز فعال نشده) ──
        for sub_id, sub in (doc.get("pending_subscriptions") or {}).items():
            if sub.get("status") != "ready":
                continue
            r = storage._payments.update_one(
                {"payment_id": sub_id},
                {"$setOnInsert": {
                    "payment_id": sub_id, "uid": int(sub["uid"]), "plan": sub.get("plan", "monthly"),
                    "amount_usd": 0, "status": "approved", "activated": False,
                    "created_at": sub.get("created_at") or now.isoformat(),
                    "approved_at": sub.get("created_at") or now.isoformat(),
                    "tx_id": "legacy-ready-subscription", "submitted_at": sub.get("created_at") or now.isoformat(),
                    "migrated_from_legacy": True}},
                upsert=True)
            if r.upserted_id is not None:
                stats["ready_subs"] += 1

        # ── پرداخت‌های قدیمی: approved = تاریخچه، pending = برای بررسی ادمین ──
        for pid, lp in (doc.get("pending_payments") or {}).items():
            st = lp.get("status")
            if st not in ("approved", "pending"):
                continue
            base = {"payment_id": pid, "uid": int(lp["uid"]), "plan": lp.get("plan", "monthly"),
                    "amount_usd": lp.get("amount_usd", 0), "status": st,
                    "created_at": lp.get("created_at") or now.isoformat(),
                    "migrated_from_legacy": True}
            if st == "approved":
                base.update({"activated": True, "approved_at": lp.get("approved_at"),
                             "tx_id": "legacy", "submitted_at": lp.get("created_at")})
            else:
                base.update({"tx_id": "legacy-receipt-in-old-bot-chat", "submitted_at": lp.get("created_at") or now.isoformat()})
            r = storage._payments.update_one({"payment_id": pid}, {"$setOnInsert": base}, upsert=True)
            if r.upserted_id is not None:
                stats["payments"] += 1

        stats["done_at"] = now.isoformat()
        storage._meta.update_one({"_id": "legacy_migration"}, {"$set": stats}, upsert=True)
        log.info(f"✅ Legacy migration انجام شد: {stats}")
        return stats
    except Exception as e:
        log.error(f"❌ Legacy migration خطا: {e}")
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════════
# PROMO HELPERS — تنها مرجع اعتبار کد تخفیف جشنواره
# ══════════════════════════════════════════════════════════════════════
def promo_seconds_left(promo: dict) -> int:
    if not promo.get("active") or not promo.get("started_at"):
        return 0
    return max(0, int(promo["started_at"] + PROMO_HOURS * 3600 - time.time()))

def is_first_membership(user: dict) -> bool:
    """اولین عضویت: نه اشتراکی داشته و نه پرداخت تاییدشده‌ای"""
    if user.get("vip_expiry"):
        return False
    return not any(p.get("status") == "approved" for p in storage.get_user_payments(user["uid"]))

def promo_state_for(user: dict) -> dict:
    promo = storage.get_promo()
    left = promo_seconds_left(promo)
    # جشنواره برای همه کاربران (چه اشتراک داشته باشند چه نه) در بازه‌ی فعال قابل استفاده است
    eligible = True
    live = bool(promo["active"]) and left > 0
    return {
        "active": bool(promo["active"]),
        "percent": DISCOUNT_PERCENT,
        # کد فقط وقتی برای کاربر فاش می‌شود که واقعاً قابل استفاده است
        "code": DISCOUNT_CODE if (live and DISCOUNT_CODE) else "",
        "seconds_left": left,
        "eligible": eligible,
    }


# ══════════════════════════════════════════════════════════════════════
# GLOBAL BOT INSTANCE (برای استفاده در Flask endpoints)
# ══════════════════════════════════════════════════════════════════════
bot_instance = None
bot_loop = None
bot_ready_event = threading.Event()

def get_bot(timeout=5):
    """گرفتن bot instance با صبر کردن تا آماده شود"""
    if bot_instance and bot_loop:
        return bot_instance, bot_loop
    
    if bot_ready_event.wait(timeout=timeout):
        return bot_instance, bot_loop
    
    return None, None

# ══════════════════════════════════════════════════════════════════════
# VIP LINK GENERATION
# ══════════════════════════════════════════════════════════════════════
async def generate_vip_link(bot: Bot, uid: int, plan: str, expiry: datetime) -> Optional[str]:
    try:
        vip_id_full = get_vip_channel_id_full()
        if not vip_id_full: return None
        
        user = storage.get_user(uid)
        if user and user.get("last_link"):
            try:
                await bot.revoke_chat_invite_link(chat_id=vip_id_full, invite_link=user["last_link"])
            except: pass
        
        link_expiry = int((expiry + timedelta(days=1)).timestamp())
        link = await bot.create_chat_invite_link(
            chat_id=vip_id_full,
            name=f"VIP_{uid}_{plan[:3]}",
            expire_date=link_expiry,
            member_limit=1,
        )
        storage.save_invite_link(uid, link.invite_link)
        return link.invite_link
    except Exception as e:
        log.error(f"VIP link error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
# TELEGRAM BOT HANDLERS
# ══════════════════════════════════════════════════════════════════════
async def check_membership(bot: Bot, uid: int) -> bool:
    if not FORCE_JOIN_CHANNEL: return True
    try:
        member = await bot.get_chat_member(chat_id=FORCE_JOIN_CHANNEL, user_id=uid)
        return member.status not in ("left", "kicked", "banned")
    except: return True

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    storage.ensure_user(user.id, user.username or "", 
                       f"{user.first_name or ''} {user.last_name or ''}".strip())
    
    if not await check_membership(ctx.bot, user.id):
        url = f"https://t.me/{FORCE_JOIN_CHANNEL.lstrip('@')}" if FORCE_JOIN_CHANNEL.startswith("@") else ""
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 عضویت در کانال", url=url)],
            [InlineKeyboardButton("✅ عضو شدم", callback_data="check_join")]
        ])
        await update.effective_message.reply_text(
            "⛔️ <b>برای استفاده از ربات باید عضو کانال ما باشی!</b>",
            parse_mode=ParseMode.HTML, reply_markup=kb)
        return
    
    rows = [[InlineKeyboardButton(" VIP داشبورد 🕴️", web_app=WebAppInfo(url=WEBAPP_URL))]]

    text = (
        "🟢 <b>SYSTEM ONLINE</b>\n\n"
        "<b>Azadi VIP Trading Terminal</b>\n"
        "Server infrastructure, live signal engine and your private dashboard are fully operational.\n\n"

        "داشبورد اختصاصی  (vpn برای بار اول حتما روشن)👇🏻"
    )

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    if q.data == "check_join":
        if await check_membership(q.get_bot(), q.from_user.id):
            await cmd_start(update, ctx)
        else:
            await q.answer("⛔️ هنوز عضو نشدی!", show_alert=True)
    elif q.data == "activate:subscription":
        uid = q.from_user.id
        await q.answer("در حال فعال‌سازی...")
        
        # Find approved but not activated payment
        payments = storage.get_user_payments(uid)
        approved = [p for p in payments if p.get("status") == "approved" and not p.get("activated")]
        
        if not approved:
            await q.edit_message_text(
                "❌ اشتراک آماده‌ای وجود ندارد.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 بازگشت", callback_data="home")]])
            )
            return
        
        payment = approved[0]
        
        # 🔒 claim اتمی — اگر همزمان از وب‌اپ هم فعال شده باشد، اینجا None برمی‌گردد
        claimed = storage.claim_payment_activation(payment["payment_id"])
        if not claimed:
            await q.edit_message_text(
                "✅ این اشتراک قبلاً فعال شده است.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 بازگشت", callback_data="home")]])
            )
            return
        
        plan = claimed["plan"]
        days = PLAN_WEEKLY_DAYS if plan == "weekly" else PLAN_MONTHLY_DAYS
        
        new_expiry = storage.activate_vip(uid, plan, days)
        
        # Generate VIP link
        link = None
        if new_expiry:
            link = await generate_vip_link(ctx.bot, uid, plan, new_expiry)
        
        plan_names = {"weekly": "هفتگی", "monthly": "ماهانه"}
        plan_name = plan_names.get(plan, "VIP")
        
        text = (
            f"🎉 <b>اشتراک فعال شد!</b>\n\n"
            f"📦 پلن: <b>{plan_name}</b>\n"
            f"⏳ مدت: <b>{days} روز</b>\n"
            f"📅 انقضا: <b>{new_expiry.strftime('%Y-%m-%d')}</b>\n"
        )
        
        if link:
            text += f"\n🔐 <b>لینک اختصاصی VIP:</b>\n<code>{link}</code>\n"
            text += f"\n<i>⚠️ این لینک یکبار مصرف و اختصاصی توست</i>"
        else:
            text += "\n⚠️ تولید لینک VIP با خطا مواجه شد. از پنل وب دوباره تلاش کن."
        
        rows = []
        if WEBAPP_URL:
            rows.append([InlineKeyboardButton("🌐 باز کردن پنل کاربری", web_app=WebAppInfo(url=WEBAPP_URL))])
        rows.append([InlineKeyboardButton("🏠 منوی اصلی", callback_data="home")])
        
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, 
                                  reply_markup=InlineKeyboardMarkup(rows))
    elif q.data == "help":
        await q.edit_message_text(
            "📖 <b>راهنما</b>\n\n"
            "۱. دکمه «پنل کاربری» را بزن\n"
            "۲. پلن مورد نظر را انتخاب کن\n"
            "۳. مبلغ را به آدرس USDT واریز کن\n"
            "۴. عکس رسید را آپلود کن\n"
            "۵. بعد از تایید، اشتراک فعال می‌شود\n\n"
            "🔐 لینک VIP یکبار مصرف و اختصاصی توست",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 بازگشت", callback_data="home")]])
        )
    elif q.data == "home":
        await cmd_start(update, ctx)

async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Handle receipts sent to bot (optional - can be done in WebApp only)
    pass


# ══════════════════════════════════════════════════════════════════════
# BACKGROUND TASKS
# ══════════════════════════════════════════════════════════════════════
async def expiration_watcher(bot: Bot):
    await asyncio.sleep(10)
    while True:
        try:
            now = now_ir()
            users = storage.get_all_users()
            for u in users:
                if u.get("warned_expired") or u.get("is_blocked"): continue
                if not u.get("vip_expiry"): continue
                try:
                    exp = datetime.fromisoformat(u["vip_expiry"])
                except: continue
                
                if exp <= now:
                    uid = u["uid"]
                    try:
                        kb = InlineKeyboardMarkup([
                            [InlineKeyboardButton("💎 تمدید اشتراک", web_app=WebAppInfo(url=WEBAPP_URL))]
                        ])
                        await bot.send_message(
                            chat_id=uid, 
                            parse_mode=ParseMode.HTML,
                            text="🔴 <b>اشتراک VIP شما به پایان رسید</b>\n\n"
                                 f"👤 {u.get('full_name', '')} عزیز\n"
                                 "متاسفانه اشتراک شما منقضی شده است.\n\n"
                                 "💎 برای تمدید:",
                            reply_markup=kb
                        )
                        if storage._is_memory:
                            storage._users[uid]["warned_expired"] = True
                        else:
                            storage._users.update_one({"uid": uid}, {"$set": {"warned_expired": True}})
                        log.info(f"✅ Expiry notification sent to {uid}")
                    except Exception as e:
                        log.error(f"Expiry notification error: {e}")
            await asyncio.sleep(600)
        except asyncio.CancelledError: break
        except Exception as e: log.error(f"Watcher error: {e}")


# ══════════════════════════════════════════════════════════════════════
# SIGNAL FORWARDING — فوروارد پست‌های کانال VIP برای همه اعضای فعال
# ══════════════════════════════════════════════════════════════════════
async def on_channel_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    if not post:
        return

    log.info(f"📥 channel_post received from chat.id={post.chat.id} (title={post.chat.title!r})")

    vip_id_full = get_vip_channel_id_full()
    log.info(f"🔧 VIP_CHANNEL_ID env='{VIP_CHANNEL_ID}' → resolved vip_id_full={vip_id_full}")
    if vip_id_full is not None and post.chat.id != vip_id_full:
        log.warning(f"⛔️ Ignored: post.chat.id ({post.chat.id}) != VIP_CHANNEL_ID ({vip_id_full})")
        return  # پست از کانال دیگری است؛ نادیده بگیر

    vip_users = storage.get_vip_users()
    log.info(f"👥 Active VIP users right now: {len(vip_users)}")
    if not vip_users:
        log.warning("⛔️ No active VIP users found — nothing to forward. "
                    "(کاربر باید vip_expiry فعال داشته باشد؛ صرفاً عضو کانال بودن کافی نیست)")
        return

    sent_count = 0
    for u in vip_users:
        uid = u.get("uid")
        if not uid:
            continue
        try:
            await ctx.bot.copy_message(chat_id=uid, from_chat_id=post.chat.id, message_id=post.message_id)
            sent_count += 1
            await asyncio.sleep(0.05)
        except Forbidden:
            # کاربر ربات را بلاک کرده
            if storage._is_memory:
                with storage._lock:
                    if uid in storage._users:
                        storage._users[uid]["is_blocked"] = True
            else:
                storage._users.update_one({"uid": uid}, {"$set": {"is_blocked": True}})
        except Exception as e:
            log.warning(f"Signal forward failed for {uid}: {e}")

    log.info(f"📡 Signal forwarded to {sent_count}/{len(vip_users)} VIP users.")

    # ═══ اعلان داخل وب‌اپ (زنگوله / چک دوره‌ای در داشبورد) ═══
    signal_text = (getattr(post, "text", None) or getattr(post, "caption", None) or "یک سیگنال جدید منتشر شد").strip()
    notif_body = signal_text if len(signal_text) <= 150 else signal_text[:147] + "..."
    try:
        storage.create_notification(
            title="🔔 سیگنال جدید VIP",
            body=notif_body,
            icon="chart",
            target="vip",
        )
    except Exception as e:
        log.error(f"In-app notification create failed: {e}")

    # ═══ Push واقعی مرورگر برای کاربرانی که اعلان را فعال کرده‌اند ═══
    # (کار می‌کند حتی وقتی کاربر تلگرام را باز نکرده — چون از Service Worker مرورگر می‌آید)
    if VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY:
        subscriptions = []
        for u in vip_users:
            uid = u.get("uid")
            if not uid:
                continue
            try:
                subscriptions.extend(storage.get_user_push_subscriptions(uid))
            except Exception as e:
                log.error(f"get_user_push_subscriptions failed for {uid}: {e}")

        if subscriptions:
            push_payload = {
                "title": "🔔 سیگنال جدید VIP",
                "body": notif_body,
                "icon": "chart",
                "url": WEBAPP_URL,
                "timestamp": int(time.time() * 1000),
            }
            push_sent = 0
            for sub in subscriptions:
                sub_info = {"endpoint": sub.get("endpoint"), "keys": sub.get("keys", {})}
                # webpush یک فراخوانی شبکه‌ای بلاک‌کننده است؛ در ترد جدا اجرا می‌شود
                # تا event loop اصلی ربات هنگ نکند
                success, _ = await asyncio.to_thread(_send_push_to_subscription, sub_info, push_payload)
                if success:
                    push_sent += 1
                await asyncio.sleep(0.02)
            log.info(f"📱 Browser push sent for new signal: {push_sent}/{len(subscriptions)}")



flask_app = Flask(__name__)

def get_current_user():
    """احراز هویت کاربر (از initData یا browser token)"""
    
    # ✅ اولویت ۱: بررسی browser token (از X-Browser-Token)
    browser_token = request.headers.get("X-Browser-Token", "")
    if browser_token:
        # پیدا کردن کاربر با این token
        if storage._is_memory:
            user = None
            for u in storage._users.values():
                if u.get("browser_token") == browser_token:
                    user = u
                    break
        else:
            user = storage._users.find_one({"browser_token": browser_token})
            if user:
                user = dict(user)
                user.pop("_id", None)
        
        if user and not user.get("is_blocked"):
            return user, None
        return None, (jsonify({"error": "Token نامعتبر یا منقضی شده"}), 401)
    
    # ✅ اولویت ۲: بررسی initData (از تلگرام)
    init_data = request.headers.get("X-Init-Data", "")
    if init_data:
        user = verify_telegram_init_data(init_data, BOT_TOKEN)
        if user:
            uid = user.get("id")
            if uid:
                db_user = storage.get_user(int(uid))
                if not db_user:
                    db_user = storage.ensure_user(
                        int(uid), user.get("username", ""), user.get("first_name", "")
                    )
                if not db_user.get("is_blocked"):
                    return db_user, None
        return None, (jsonify({"error": "initData نامعتبر است"}), 401)
    
    # ✅ اولویت ۳: بررسی Authorization header (fallback)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.replace("Bearer ", "")
        
        if storage._is_memory:
            user = None
            for u in storage._users.values():
                if u.get("browser_token") == token:
                    user = u
                    break
        else:
            user = storage._users.find_one({"browser_token": token})
            if user:
                user = dict(user)
                user.pop("_id", None)
        
        if user and not user.get("is_blocked"):
            return user, None
        return None, (jsonify({"error": "Token نامعتبر"}), 401)
    
    return None, (jsonify({"error": "احراز هویت نشده"}), 401)

def auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user, err = get_current_user()
        if err: return err
        g.current_user = user
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user, err = get_current_user()
        if err: return err
        if not is_admin(user["uid"]):
            return jsonify({"error": "دسترسی ادمین لازم است"}), 403
        g.current_user = user
        return f(*args, **kwargs)
    return decorated

@flask_app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,X-Init-Data,X-Browser-Token"  # ✅ اضافه شد
    return response

@flask_app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@flask_app.route("/<path:path>", methods=["OPTIONS"])
def options_handler(path):
    return "", 204

# ── User Endpoints ──
@flask_app.get("/api/me")
@auth_required
def api_me():
    u = g.current_user
    now = now_ir()
    
    status = "inactive"
    time_left = None
    expiry_str = None
    
    if u.get("grace_until"):
        try:
            grace = datetime.fromisoformat(u["grace_until"])
            if grace > now:
                status = "grace"
                time_left = (grace - now).total_seconds()
                expiry_str = grace.isoformat()
        except: pass
    
    if status == "inactive" and u.get("vip_expiry"):
        try:
            exp = datetime.fromisoformat(u["vip_expiry"])
            if exp > now:
                status = "active"
                time_left = (exp - now).total_seconds()
                expiry_str = exp.isoformat()
            else:
                status = "expired"
        except: pass
    
    return jsonify({
        "uid": u["uid"],
        "username": u.get("username", ""),
        "full_name": u.get("full_name", ""),
        "status": status,
        "plan": u.get("vip_plan"),
        "expiry": expiry_str,
        "time_left_seconds": int(time_left) if time_left else None,
        "time_left_text": fmt_timedelta(time_left) if time_left else None,
        "vip_link": u.get("last_link"),
        "is_admin": is_admin(u["uid"]),
    })

@flask_app.post("/api/auth/login-uid")
def api_login_by_uid():
    """ورود با UID (برای دسترسی از مرورگر)"""
    data = request.get_json(force=True) or {}
    uid_str = (data.get("uid") or "").strip()
    
    if not uid_str:
        return jsonify({"error": "UID لازم است"}), 400
    
    try:
        uid = int(uid_str)
    except ValueError:
        return jsonify({"error": "UID باید عدد باشد"}), 400
    
    user = storage.get_user(uid)
    if not user:
        return jsonify({"error": "کاربر یافت نشد. اول در ربات /start بزنید"}), 404
    
    # تولید token موقت
    import secrets
    token = secrets.token_urlsafe(32)
    
    # ✅ ذخیره token در user (کلید صحیح)
    if storage._is_memory:
        storage._users[uid]["browser_token"] = token  # ← اصلاح شد
    else:
        storage._users.update_one(
            {"uid": uid},
            {"$set": {"browser_token": token}}
        )
    
    return jsonify({
        "token": token,
        "uid": uid,
        "username": user.get("username", ""),
        "full_name": user.get("full_name", ""),
    })


@flask_app.get("/api/me-by-token")
def api_me_by_token():
    """گرفتن اطلاعات کاربر با browser token"""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "Token لازم است"}), 401
    
    token = auth_header.replace("Bearer ", "")
    
    # پیدا کردن کاربر با این token
    if storage._is_memory:
        user = None
        for u in storage._users.values():
            if u.get("browser_token") == token:
                user = u
                break
    else:
        user = storage._users.find_one({"browser_token": token})
        if user:
            user = dict(user)
            user.pop("_id", None)
    
    if not user:
        return jsonify({"error": "Token نامعتبر"}), 401
    
    
    # ساخت response مشابه /api/me
    now = now_ir()
    status = "inactive"
    time_left = None
    expiry_str = None
    
    if user.get("vip_expiry"):
        try:
            exp = datetime.fromisoformat(user["vip_expiry"])
            if exp > now:
                status = "active"
                time_left = (exp - now).total_seconds()
                expiry_str = exp.isoformat()
            else:
                status = "expired"
        except: pass
    
    is_admin = user.get("uid") in ADMIN_IDS
    
    return jsonify({
        "uid": user.get("uid"),
        "username": user.get("username", ""),
        "full_name": user.get("full_name", ""),
        "status": status,
        "plan": user.get("vip_plan"),
        "expiry": expiry_str,
        "time_left_seconds": int(time_left) if time_left else None,
        "vip_link": user.get("last_link"),
        "is_admin": is_admin,
    })

@flask_app.get("/api/plans")
@auth_required
def api_plans():
    _promo = promo_state_for(g.current_user)
    _promo_live = bool(_promo["code"])
    return jsonify({
        "plans": [
            {
                "id": "weekly",
                "name": "هفتگی",
                "days": PLAN_WEEKLY_DAYS,
                "price": PLAN_WEEKLY_USD,
                "features": ["دسترسی 7 روز اشتراک پرمیوم هوشمند", "قابلیت فعالسازی در هر زمان ", "عضویت آنی و لینک اختصاصی"]
            },
            {
                "id": "monthly",
                "name": "ماهانه",
                "days": PLAN_MONTHLY_DAYS,
                "price": PLAN_MONTHLY_USD,
                "features": ["دسترسی 30 روز اشتراک پرمیوم هوشمند", "تمام امکانات پرمیوم", "پشتیبانی اختصاصی"]
            }
        ],
        # کد تخفیف فقط وقتی جشنواره فعال و در بازه‌ی زمانی باشد برمی‌گردد
        "discount_code": _promo["code"] if _promo_live else None,
        "discount_percent": DISCOUNT_PERCENT if _promo_live else 0,
        "payment_info": PAYMENT_INFO,
    })

@flask_app.post("/api/subscribe")
@auth_required
def api_subscribe():
    data = request.get_json(force=True) or {}
    plan = data.get("plan")
    
    if plan not in ("weekly", "monthly"):
        return jsonify({"error": "پلن نامعتبر"}), 400
    
    # ✅ Cancel کردن همه payment های pending قبلی این کاربر
    existing_payments = storage.get_user_payments(g.current_user["uid"])
    for p in existing_payments:
        if p.get("status") == "pending":
            storage.cancel_payment(p["payment_id"])
    
    price = PLAN_WEEKLY_USD if plan == "weekly" else PLAN_MONTHLY_USD
    payment_id = storage.create_payment(g.current_user["uid"], plan, price)
    
    return jsonify({
        "payment_id": payment_id,
        "plan": plan,
        "amount": price,
    })

@flask_app.post("/api/apply-discount")
@auth_required
def api_apply_discount():
    data = request.get_json(force=True) or {}
    code = (data.get("code") or "").strip()
    
    # بررسی کد تخفیف (بدون چک کردن used_discount)
    if not DISCOUNT_CODE or code != DISCOUNT_CODE:
        return jsonify({"error": "کد تخفیف نامعتبر یا غیرفعال"}), 400

    # ✅ کد تخفیف در طول جشنواره‌ی فعال برای همه‌ی کاربران معتبر است
    promo = promo_state_for(g.current_user)
    if not promo["active"]:
        return jsonify({"error": "کد تخفیف نامعتبر یا غیرفعال"}), 400
    if promo["seconds_left"] <= 0:
        return jsonify({"error": "زمان جشنواره به پایان رسیده است"}), 400
    
    payment_id = data.get("payment_id")
    if not payment_id:
        return jsonify({"error": "payment_id لازم است"}), 400
    
    payment = storage.get_payment(payment_id)
    if not payment or payment.get("status") != "pending":
        return jsonify({"error": "پرداخت یافت نشد"}), 404
    
    if payment["uid"] != g.current_user["uid"]:
        return jsonify({"error": "دسترسی ندارید"}), 403
    
    # ✅ کد جشنواره فقط برای پلن ماهانه
    if payment.get("plan") != PROMO_PLAN:
        return jsonify({"error": "این تخفیف فقط برای پلن ماهانه است"}), 400

    # بررسی اینکه قبلاً تخفیف اعمال نشده باشد (فقط روی همین payment)
    if payment.get("discount_applied"):
        return jsonify({"error": "تخفیف قبلاً روی این پرداخت اعمال شده"}), 400
    
    new_price = get_discounted_price(payment["amount_usd"])
    
    # اعمال تخفیف به پرداخت — با گارد اتمی روی discount_applied تا دو درخواست هم‌زمان
    # نتوانند تخفیف را دوبار روی یک پرداخت اعمال کنند
    if storage._is_memory:
        with storage._lock:
            p = storage._payments.get(payment_id)
            if not p or p.get("status") != "pending" or p.get("discount_applied"):
                return jsonify({"error": "تخفیف قبلاً روی این پرداخت اعمال شده"}), 400
            p["amount_usd"] = new_price
            p["discount_applied"] = True
    else:
        result = storage._payments.update_one(
            {"payment_id": payment_id, "status": "pending", "discount_applied": {"$ne": True}},
            {"$set": {"amount_usd": new_price, "discount_applied": True}}
        )
        if result.modified_count == 0:
            return jsonify({"error": "تخفیف قبلاً روی این پرداخت اعمال شده"}), 400
    
    return jsonify({
        "new_price": new_price,
        "discount_percent": DISCOUNT_PERCENT,
    })

@flask_app.post("/api/payment/submit")
@auth_required
def api_submit_payment():
    data = request.get_json(force=True) or {}
    payment_id = data.get("payment_id")
    tx_id = (data.get("tx_id") or "").strip()
    
    if not payment_id:
        return jsonify({"error": "payment_id لازم است"}), 400
    if not tx_id:
        return jsonify({"error": "شناسه تراکنش / رسید لازم است"}), 400
    
    payment = storage.get_payment(payment_id)
    if not payment or payment.get("status") != "pending":
        return jsonify({"error": "پرداخت یافت نشد"}), 404
    
    if payment["uid"] != g.current_user["uid"]:
        return jsonify({"error": "دسترسی ندارید"}), 403
    
    if storage._is_memory:
        storage._payments[payment_id]["tx_id"] = tx_id
        storage._payments[payment_id]["submitted_at"] = now_ir().isoformat()
    else:
        storage._payments.update_one(
            {"payment_id": payment_id},
            {"$set": {"tx_id": tx_id, "submitted_at": now_ir().isoformat()}}
        )
    
    return jsonify({"message": "رسید ثبت شد، منتظر تایید باشید"})

@flask_app.get("/api/payment/status")
@auth_required
def api_payment_status():
    payment_id = request.args.get("payment_id")
    if not payment_id:
        return jsonify({"error": "payment_id لازم است"}), 400
    
    payment = storage.get_payment(payment_id)
    if not payment:
        return jsonify({"error": "پرداخت یافت نشد"}), 404
    
    if payment["uid"] != g.current_user["uid"] and not is_admin(g.current_user["uid"]):
        return jsonify({"error": "دسترسی ندارید"}), 403
    
    return jsonify(payment)

@flask_app.post("/api/activate")
@auth_required
def api_activate():
    """فعال‌سازی اشتراک (بعد از تایید ادمین)"""
    payments = storage.get_user_payments(g.current_user["uid"])
    approved = [p for p in payments if p.get("status") == "approved" and not p.get("activated")]
    
    if not approved:
        return jsonify({"error": "اشتراک آماده‌ای وجود ندارد"}), 404
    
    payment = approved[0]
    
    # 🔒 claim اتمی — جلوگیری از فعال‌سازی دوبل (دابل‌کلیک، دو تب، یا هم‌زمانی با ربات)
    claimed = storage.claim_payment_activation(payment["payment_id"])
    if not claimed:
        return jsonify({"error": "این اشتراک قبلاً فعال شده است"}), 409
    
    plan = claimed["plan"]
    days = PLAN_WEEKLY_DAYS if plan == "weekly" else PLAN_MONTHLY_DAYS
    
    new_expiry = storage.activate_vip(g.current_user["uid"], plan, days)
    
    # ✅ Generate VIP link
    vip_link = None
    bot, loop = get_bot(timeout=10)
    if bot and loop and new_expiry:
        try:
            future = asyncio.run_coroutine_threadsafe(
                generate_vip_link(bot, g.current_user["uid"], plan, new_expiry),
                loop
            )
            vip_link = future.result(timeout=15)
        except Exception as e:
            log.error(f"VIP link generation error: {e}")
    
    return jsonify({
        "message": "اشتراک فعال شد!",
        "expiry": new_expiry.isoformat() if new_expiry else None,
        "plan": plan,
        "days": days,
        "vip_link": vip_link,
    })

@flask_app.post("/api/vip-link")
@auth_required
def api_regenerate_vip_link():
    """Generate یا regenerate لینک VIP"""
    u = g.current_user
    now = now_ir()
    
    # Check if user has active subscription
    is_active = False
    expiry_dt = None
    
    if u.get("vip_expiry"):
        try:
            exp = datetime.fromisoformat(u["vip_expiry"])
            if exp > now:
                is_active = True
                expiry_dt = exp
        except: pass
    
    if not is_active:
        return jsonify({"error": "اشتراک فعال ندارید"}), 400
    
    bot, loop = get_bot(timeout=10)
    if not bot or not loop:
        return jsonify({"error": "Bot not ready"}), 503
    
    try:
        future = asyncio.run_coroutine_threadsafe(
            generate_vip_link(bot, u["uid"], u.get("vip_plan", "VIP"), expiry_dt),
            loop
        )
        vip_link = future.result(timeout=15)
        return jsonify({"vip_link": vip_link})
    except Exception as e:
        log.error(f"VIP link error: {e}")
        return jsonify({"error": str(e)}), 500

@flask_app.get("/api/archive")
@auth_required
def api_archive():
    return jsonify({"text": storage.get_archive_text()})

@flask_app.get("/api/my-payments")
@auth_required
def api_my_payments():
    payments = storage.get_user_payments(g.current_user["uid"])
    return jsonify({"payments": payments})

# ── Admin Endpoints ──
@flask_app.get("/api/promo")
@auth_required
def api_promo():
    return jsonify(promo_state_for(g.current_user))

@flask_app.post("/api/admin/promo")
@admin_required
def api_admin_promo():
    data = request.get_json(force=True) or {}
    if "active" not in data:
        return jsonify({"error": "active لازم است"}), 400
    storage.set_promo(bool(data["active"]))
    log.info(f"🎁 جشنواره {'فعال' if data['active'] else 'غیرفعال'} شد (admin={g.current_user.get('uid')})")
    return jsonify(promo_state_for(g.current_user))

@flask_app.get("/api/admin/stats")
@admin_required
def api_admin_stats():
    return jsonify(storage.get_stats())

@flask_app.get("/api/admin/users")
@admin_required
def api_admin_users():
    return jsonify({"users": storage.get_all_users()})

@flask_app.get("/api/admin/active-subscribers")
@admin_required
def api_admin_active_subscribers():
    """کاربران با اشتراک فعال + زمان باقی‌مانده (برای لیست ریز پنل ادمین)"""
    now = now_ir()
    rows = []
    for u in storage.get_all_users():
        if not u.get("vip_expiry"):
            continue
        try:
            exp = datetime.fromisoformat(u["vip_expiry"])
            if exp.tzinfo is None and now.tzinfo is not None:
                exp = exp.replace(tzinfo=now.tzinfo)
        except Exception:
            continue
        left = (exp - now).total_seconds()
        if left <= 0:
            continue
        rows.append({
            "uid": u.get("uid"),
            "full_name": u.get("full_name") or "",
            "username": u.get("username") or "",
            "plan": u.get("vip_plan"),
            "seconds_left": int(left),
        })
    rows.sort(key=lambda r: r["seconds_left"])  # نزدیک‌ترین انقضا بالاتر
    return jsonify({"users": rows})

@flask_app.get("/api/admin/payments")
@admin_required
def api_admin_payments():
    # ✅ فقط پرداخت‌هایی که کاربر رسید فرستاده؛ درخواست‌های بدون رسید (مثلاً فقط کد تخفیف زده) نمایش داده نمی‌شوند
    payments = [p for p in storage.get_all_pending_payments() if storage.is_receipt_submitted(p)]
    payments.sort(key=lambda p: p.get("submitted_at") or "")
    enriched = []
    for p in payments:
        user = storage.get_user(p["uid"])
        enriched.append({**p, "user_name": user.get("full_name", "—") if user else "—"})
    return jsonify({"payments": enriched})

@flask_app.post("/api/admin/payments/<payment_id>/approve")
@admin_required
def api_admin_approve(payment_id):
    _p = storage.get_payment(payment_id)
    if not _p or not storage.is_receipt_submitted(_p):
        return jsonify({"error": "کاربر هنوز رسید ارسال نکرده است"}), 400
    if storage.approve_payment(payment_id):
        payment = storage.get_payment(payment_id)
        
        # ✅ Send notification to user
        bot, loop = get_bot(timeout=10)
        if payment and bot and loop:
            plan_names = {"weekly": "هفتگی", "monthly": "ماهانه"}
            plan_name = plan_names.get(payment.get("plan"), "VIP")
            
            async def notify():
                try:
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("🚀 فعال‌سازی اشتراک", callback_data="activate:subscription")],
                        [InlineKeyboardButton("🌐 پنل کاربری", web_app=WebAppInfo(url=WEBAPP_URL))]
                    ])
                    await bot.send_message(
                        chat_id=payment["uid"],
                        parse_mode=ParseMode.HTML,
                        text=f"🎉 <b>پرداخت شما تایید شد!</b>\n\n"
                             f"اشتراک <b>{plan_name}</b> شما آماده فعال‌سازی است.\n\n"
                             f"💰 مبلغ: <b>{payment['amount_usd']}$</b>\n\n"
                             f"👇 برای فعال‌سازی و دریافت لینک VIP:",
                        reply_markup=kb
                    )
                except Exception as e:
                    log.error(f"Notify user error: {e}")
            
            asyncio.run_coroutine_threadsafe(notify(), loop)
        
        return jsonify({"message": "پرداخت تایید شد"})
    return jsonify({"error": "خطا در تایید"}), 400

# ══════════════════════════════════════════════════════════════════════
# Internal Notifications (WebApp Push)
# ══════════════════════════════════════════════════════════════════════
@flask_app.get("/api/notifications/check")
@auth_required
def api_check_notifications():
    """چک کردن اعلان‌های جدید (polling توسط Service Worker)"""
    uid = g.current_user["uid"]
    last_check = request.args.get("last_check", "1970-01-01T00:00:00")
    
    # بررسی VIP بودن
    is_vip = False
    if g.current_user.get("vip_expiry"):
        try:
            exp = datetime.fromisoformat(g.current_user["vip_expiry"])
            if exp > now_ir():
                is_vip = True
        except: pass
    
    notifications = storage.get_notifications_after(last_check, uid, is_vip)
    
    return jsonify({
        "notifications": notifications,
        "count": len(notifications),
    })


@flask_app.post("/api/admin/notifications/create")
@admin_required
def api_admin_create_notification():
    """ساخت اعلان داخلی برای کاربران"""
    data = request.get_json(force=True) or {}
    title = (data.get("title") or "").strip()
    body = (data.get("body") or "").strip()
    icon = data.get("icon", "chart")
    target = data.get("target", "all")
    user_ids = data.get("user_ids", [])
    
    if not title or not body:
        return jsonify({"error": "عنوان و متن لازم است"}), 400
    
    if target not in ("all", "vip", "specific"):
        return jsonify({"error": "target نامعتبر"}), 400
    
    if target == "specific" and not user_ids:
        return jsonify({"error": "حداقل یک کاربر انتخاب کنید"}), 400
    
    # شمارش کاربران تحت تاثیر
    all_users = storage.get_all_users()
    affected = 0
    now = now_ir()
    
    if target == "all":
        affected = len(all_users)
    elif target == "vip":
        for u in all_users:
            if u.get("vip_expiry"):
                try:
                    exp = datetime.fromisoformat(u["vip_expiry"])
                    if exp > now:
                        affected += 1
                except: pass
    elif target == "specific":
        affected = len(user_ids)
    
    if affected == 0:
        return jsonify({"error": "هیچ کاربری برای دریافت اعلان یافت نشد"}), 404
    
    # ایجاد اعلان
    notif_id = storage.create_notification(
        title=title,
        body=body,
        icon=icon,
        target=target,
        user_ids=user_ids if target == "specific" else [],
    )
    
    log.info(f"🔔 Internal notification created: {notif_id} for {affected} users")
    
    return jsonify({
        "message": f"✅ اعلان برای {affected} کاربر ارسال شد",
        "notif_id": notif_id,
        "affected": affected,
        "total": affected,
    })

@flask_app.post("/api/admin/push")
@admin_required
def api_admin_push_notification():
    """ارسال Push Notification از طریق Telegram Bot"""
    data = request.get_json(force=True) or {}
    title = (data.get("title") or "").strip()
    body = (data.get("body") or "").strip()
    target = data.get("target", "all")
    user_ids = data.get("user_ids", [])
    with_link = data.get("with_link", False)
    
    if not title or not body:
        return jsonify({"error": "عنوان و متن لازم است"}), 400
    
    bot, loop = get_bot(timeout=10)
    if not bot or not loop:
        return jsonify({"error": "ربات آماده نیست. لطفاً چند ثانیه صبر کنید"}), 503
    
    # Get target users
    all_users = storage.get_all_users()
    target_users = []
    now = now_ir()
    
    if target == "all":
        target_users = all_users
    elif target == "vip":
        for u in all_users:
            if u.get("vip_expiry"):
                try:
                    exp = datetime.fromisoformat(u["vip_expiry"])
                    if exp > now:
                        target_users.append(u)
                except: pass
    elif target == "specific":
        if not user_ids:
            return jsonify({"error": "حداقل یک کاربر انتخاب کنید"}), 400
        for u in all_users:
            if u.get("uid") in user_ids:
                target_users.append(u)
    else:
        return jsonify({"error": "target نامعتبر"}), 400
    
    if not target_users:
        return jsonify({"error": "کاربری یافت نشد"}), 404
    
    message_text = (
        f"🔔 <b>{title}</b>\n\n"
        f"{body}"
    )
    
    kb = None
    if with_link and WEBAPP_URL:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 باز کردن پنل", web_app=WebAppInfo(url=WEBAPP_URL))]
        ])
    
    sent = 0
    failed = 0
    failed_users = []
    
    async def send_all():
        nonlocal sent, failed
        for u in target_users:
            uid = u.get("uid")
            try:
                await bot.send_message(
                    chat_id=uid,
                    text=message_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                    disable_web_page_preview=True,
                )
                sent += 1
                await asyncio.sleep(0.05)
            except Forbidden:
                failed += 1
                failed_users.append({"uid": uid, "reason": "blocked"})
                log.warning(f"User {uid} blocked the bot")
            except Exception as e:
                failed += 1
                failed_users.append({"uid": uid, "reason": str(e)[:50]})
                log.error(f"Push failed for {uid}: {e}")
    
    future = asyncio.run_coroutine_threadsafe(send_all(), loop)
    try:
        future.result(timeout=180)
    except Exception as e:
        log.error(f"Push timeout: {e}")
        return jsonify({"error": f"Timeout: {str(e)}"}), 500
    
    target_names = {
        "all": "همه کاربران",
        "vip": "کاربران VIP",
        "specific": "کاربران انتخاب شده"
    }
    
    log.info(f"🔔 Push: {sent} sent, {failed} failed, target={target_names[target]}")
    
    return jsonify({
        "message": f"📱 {sent} اعلان ارسال شد",
        "sent": sent,
        "failed": failed,
        "total": len(target_users),
        "failed_users": failed_users[:10],
    })


@flask_app.post("/api/admin/broadcast")
@admin_required
def api_admin_broadcast():
    """ارسال پیام همگانی"""
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    target = data.get("target", "all")
    user_ids = data.get("user_ids", [])
    
    if not text:
        return jsonify({"error": "متن پیام لازم است"}), 400
    
    bot, loop = get_bot(timeout=10)
    if not bot or not loop:
        return jsonify({"error": "ربات آماده نیست. لطفاً چند ثانیه صبر کنید"}), 503
    
    all_users = storage.get_all_users()
    target_users = []
    
    if target == "all":
        target_users = all_users
    elif target == "vip":
        now = now_ir()
        for u in all_users:
            if u.get("vip_expiry"):
                try:
                    exp = datetime.fromisoformat(u["vip_expiry"])
                    if exp > now:
                        target_users.append(u)
                except: pass
    elif target == "specific":
        if not user_ids:
            return jsonify({"error": "حداقل یک کاربر انتخاب کنید"}), 400
        for u in all_users:
            if u.get("uid") in user_ids:
                target_users.append(u)
    else:
        return jsonify({"error": "target نامعتبر"}), 400
    
    if not target_users:
        return jsonify({"error": "هیچ کاربری برای ارسال یافت نشد"}), 404
    
    sent = 0
    failed = 0
    
    async def send_all():
        nonlocal sent, failed
        for u in target_users:
            uid = u.get("uid")
            try:
                await bot.send_message(
                    chat_id=uid,
                    text=text,
                    parse_mode=ParseMode.HTML
                )
                sent += 1
                await asyncio.sleep(0.05)
            except Exception as e:
                log.error(f"Broadcast failed for {uid}: {e}")
                failed += 1
    
    future = asyncio.run_coroutine_threadsafe(send_all(), loop)
    try:
        future.result(timeout=120)
    except Exception as e:
        log.error(f"Broadcast timeout: {e}")
        return jsonify({"error": f"Timeout: {str(e)}"}), 500
    
    log.info(f"📢 Broadcast: {sent} sent, {failed} failed, target={target}")
    
    return jsonify({
        "message": f"ارسال انجام شد: {sent} موفق، {failed} ناموفق",
        "sent": sent,
        "failed": failed,
        "total": len(target_users),
    })


@flask_app.post("/api/admin/users/<int:uid>/message")
@admin_required
def api_admin_send_to_user(uid):
    """ارسال پیام به کاربر خاص"""
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    
    if not text:
        return jsonify({"error": "متن پیام لازم است"}), 400
    
    bot, loop = get_bot(timeout=10)
    if not bot or not loop:
        return jsonify({"error": "ربات آماده نیست. لطفاً چند ثانیه صبر کنید"}), 503
    
    async def send():
        await bot.send_message(
            chat_id=uid,
            text=text,
            parse_mode=ParseMode.HTML
        )
    
    try:
        future = asyncio.run_coroutine_threadsafe(send(), loop)
        future.result(timeout=10)
        log.info(f"📨 Message sent to user {uid}")
        return jsonify({"message": "پیام ارسال شد"})
    except Exception as e:
        log.error(f"Send message failed: {e}")
        return jsonify({"error": str(e)}), 500

@flask_app.post("/api/admin/payments/<payment_id>/reject")
@admin_required
def api_admin_reject(payment_id):
    _p = storage.get_payment(payment_id)
    if not _p or not storage.is_receipt_submitted(_p):
        return jsonify({"error": "کاربر هنوز رسید ارسال نکرده است"}), 400
    if storage.reject_payment(payment_id):
        return jsonify({"message": "پرداخت رد شد"})
    return jsonify({"error": "خطا"}), 400

@flask_app.post("/api/admin/archive")
@admin_required
def api_admin_set_archive():
    data = request.get_json(force=True) or {}
    text = data.get("text", "")
    if not text:
        return jsonify({"error": "متن لازم است"}), 400
    storage.set_archive_text(text)
    return jsonify({"message": "آرشیو به‌روزرسانی شد"})


# ══════════════════════════════════════════════════════════════════════
# Web Push Notifications
# ══════════════════════════════════════════════════════════════════════

@flask_app.get("/api/push/vapid-key")
@auth_required
def api_vapid_key():
    """برگرداندن VAPID public key برای کلاینت"""
    if not VAPID_PUBLIC_KEY:
        return jsonify({"error": "VAPID تنظیم نشده"}), 500
    return jsonify({"publicKey": VAPID_PUBLIC_KEY})


@flask_app.post("/api/push/subscribe")
@auth_required
def api_push_subscribe():
    """ذخیره اشتراک push کاربر"""
    data = request.get_json(force=True) or {}
    subscription = data.get("subscription")
    
    if not subscription or not subscription.get("endpoint"):
        return jsonify({"error": "subscription نامعتبر"}), 400
    
    uid = g.current_user["uid"]
    success = storage.save_push_subscription(uid, subscription)
    
    if success:
        log.info(f"✅ Push subscription saved for user {uid}")
        return jsonify({"message": "اشتراک push ذخیره شد"})
    else:
        return jsonify({"error": "خطا در ذخیره"}), 500


@flask_app.post("/api/push/unsubscribe")
@auth_required
def api_push_unsubscribe():
    data = request.get_json(force=True) or {}
    endpoint = data.get("endpoint")
    if endpoint:
        storage.remove_push_subscription(endpoint)
    return jsonify({"message": "لغو شد"})


def _send_push_to_subscription(sub, payload):
    """ارسال push به یک اشتراک"""
    try:
        response = webpush(
            subscription_info=sub,
            data=json.dumps(payload),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={
                "sub": VAPID_EMAIL,
            },
            timeout=10,
        )
        return True, response.status_code
    except WebPushException as e:
        # اگر اشتراک نامعتبر بود، حذف کن
        if e.response and e.response.status_code in (404, 410):
            storage.remove_push_subscription(sub.get("endpoint"))
        log.warning(f"Push failed: {e}")
        return False, str(e)
    except Exception as e:
        log.error(f"Push error: {e}")
        return False, str(e)


# ══════════════════════════════════════════════════════════════════════
# ارسال اعلان یکپارچه ادمین: نوتیف مرورگر (Web Push) + پیام در ربات تلگرام
# ══════════════════════════════════════════════════════════════════════
# ارسال در پس‌زمینه انجام می‌شود (درخواست HTTP معطل نمی‌ماند) و پنل ادمین پیشرفت را زنده می‌بیند.
_push_jobs = {}
_push_jobs_lock = threading.Lock()


def _resolve_push_targets(target: str, user_ids) -> list:
    """کاربران هدف بر اساس گزینه انتخابی. کاربران بلاک‌شده در حالت all/vip حذف می‌شوند."""
    now = now_ir()
    ids = set()
    for x in (user_ids or []):
        try: ids.add(int(x))
        except Exception: pass
    out = []
    for u in storage.get_all_users():
        uid = u.get("uid")
        if uid is None:
            continue
        if target == "specific":
            if int(uid) in ids:
                out.append(u)
            continue
        if u.get("is_blocked"):
            continue
        if target == "vip":
            try:
                if u.get("vip_expiry") and datetime.fromisoformat(u["vip_expiry"]) > now:
                    out.append(u)
            except Exception:
                pass
        else:
            out.append(u)
    return out


def _push_job_new(target: str, channels: dict) -> dict:
    job_id = hashlib.md5(f"{time.time()}{target}".encode()).hexdigest()[:10]
    job = {
        "id": job_id, "created_at": now_ir().isoformat(), "target": target, "done": False,
        "web": {"enabled": channels["web"], "total": 0, "sent": 0, "failed": 0, "done": not channels["web"], "note": ""},
        "telegram": {"enabled": channels["telegram"], "total": 0, "sent": 0, "failed": 0, "blocked": 0,
                     "done": not channels["telegram"], "note": ""},
    }
    with _push_jobs_lock:
        _push_jobs[job_id] = job
        while len(_push_jobs) > 20:
            _push_jobs.pop(next(iter(_push_jobs)))
    return job


def _push_job_bump(job: dict, ch: str, key: str, n: int = 1):
    with _push_jobs_lock:
        job[ch][key] = job[ch].get(key, 0) + n


def _push_job_finish(job: dict, ch: str):
    with _push_jobs_lock:
        job[ch]["done"] = True
        job["done"] = job["web"]["done"] and job["telegram"]["done"]


def _push_job_snapshot(job: dict) -> dict:
    with _push_jobs_lock:
        return json.loads(json.dumps(job))


def _run_web_push(job: dict, subs: list, payload: dict):
    try:
        for sub in subs:
            ok, _ = _send_push_to_subscription(
                {"endpoint": sub.get("endpoint"), "keys": sub.get("keys", {})}, payload)
            _push_job_bump(job, "web", "sent" if ok else "failed")
            time.sleep(0.02)
    except Exception as e:
        log.error(f"Web push job error: {e}")
        with _push_jobs_lock:
            job["web"]["note"] = str(e)[:80]
    finally:
        _push_job_finish(job, "web")
        log.info(f"📱 Web push done: {job['web']['sent']} sent / {job['web']['failed']} failed")


def _strip_html_tags(text: str) -> str:
    """حذف تگ‌های HTML از متن، برای نمایش تمیز در نوتیف مرورگر (که HTML را رندر نمی‌کند)."""
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()


async def _run_telegram_push(job: dict, bot, users: list, text: str, kb, media: Optional[dict] = None):
    """ارسال پیام تلگرام به لیست کاربران. اگر media داده شود (dict با type/bytes/filename)
    به‌جای متن ساده، عکس/ویدیو/فایل همراه با کپشن ارسال می‌شود. برای سرعت و پایداری،
    بعد از اولین ارسال موفق، file_id تلگرام کش می‌شود و برای بقیه کاربران دوباره آپلود نمی‌شود."""
    send_fn = None
    field_name = None
    file_id_cache = None
    caption = None
    if media:
        field_name = media["type"]  # 'photo' | 'video' | 'document'
        send_fn = getattr(bot, {"photo": "send_photo", "video": "send_video", "document": "send_document"}[field_name])
        caption = text[:1024] if text else None

    try:
        for u in users:
            try:
                chat_id = int(u.get("uid"))
            except Exception:
                _push_job_bump(job, "telegram", "failed")
                continue
            attempts = 0
            while True:
                try:
                    if media:
                        media_arg = file_id_cache if file_id_cache else BytesIO(media["bytes"])
                        kwargs = {field_name: media_arg, "caption": caption, "parse_mode": ParseMode.HTML,
                                  "reply_markup": kb}
                        if field_name == "document" and not file_id_cache:
                            kwargs["filename"] = media.get("filename") or "file"
                        msg = await send_fn(chat_id=chat_id, **kwargs)
                        if not file_id_cache:
                            if field_name == "photo" and msg.photo:
                                file_id_cache = msg.photo[-1].file_id
                            else:
                                media_obj = getattr(msg, field_name, None)
                                if media_obj is not None:
                                    file_id_cache = media_obj.file_id
                    else:
                        await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML,
                                               reply_markup=kb, disable_web_page_preview=True)
                    _push_job_bump(job, "telegram", "sent")
                    break
                except RetryAfter as e:          # محدودیت نرخ تلگرام: صبر و تلاش مجدد
                    attempts += 1
                    if attempts > 2:
                        _push_job_bump(job, "telegram", "failed")
                        break
                    ra = getattr(e, "retry_after", 1)
                    ra = ra.total_seconds() if hasattr(ra, "total_seconds") else float(ra)
                    await asyncio.sleep(ra + 1)
                except Forbidden:                 # کاربر ربات را بلاک کرده
                    _push_job_bump(job, "telegram", "failed")
                    _push_job_bump(job, "telegram", "blocked")
                    break
                except Exception as e:
                    log.warning(f"Telegram push failed for {chat_id}: {str(e)[:80]}")
                    _push_job_bump(job, "telegram", "failed")
                    break
            await asyncio.sleep(0.05)             # ~۲۰ پیام در ثانیه، زیر سقف تلگرام
    except Exception as e:
        log.error(f"Telegram push job error: {e}")
        with _push_jobs_lock:
            job["telegram"]["note"] = str(e)[:80]
    finally:
        _push_job_finish(job, "telegram")
        log.info(f"✈️ Telegram push done: {job['telegram']['sent']} sent / {job['telegram']['failed']} failed")


async def _send_to_vip_channel(bot, text: str, button_text: Optional[str], media: Optional[dict] = None) -> dict:
    """ارسال یک اطلاعیه/رسانه به کانال VIP. برای پست تکی در کانال، جدا از حلقه‌ی کاربران انجام می‌شود."""
    vip_id_full = get_vip_channel_id_full()
    if not vip_id_full:
        return {"sent": False, "error": "VIP_CHANNEL_ID تنظیم نشده"}
    # دکمه‌ی web_app در کانال‌ها مجاز نیست؛ به‌جایش دکمه‌ی لینک عمیق ربات: با زدن آن ربات استارت می‌شود
    # و پیام /start همراه دکمه‌ی «داشبورد VIP» (مینی‌اپ) نمایش داده می‌شود. یوزرنیم ربات خودکار خوانده می‌شود.
    kb = None
    if button_text:
        try:
            uname = os.environ.get("BOT_USERNAME", "").strip().lstrip("@") or (await bot.get_me()).username
            if uname:
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(button_text, url=f"https://t.me/{uname}?start=dashboard")]])
        except Exception as e:
            log.warning(f"Could not build channel button: {e}")
    last_err = ""
    for markup in ((kb, None) if kb else (None,)):
        try:
            if media:
                field_name = media["type"]
                send_fn = getattr(bot, {"photo": "send_photo", "video": "send_video", "document": "send_document"}[field_name])
                kwargs = {field_name: BytesIO(media["bytes"]), "caption": text[:1024] if text else None,
                          "parse_mode": ParseMode.HTML, "reply_markup": markup}
                if field_name == "document":
                    kwargs["filename"] = media.get("filename") or "file"
                await send_fn(chat_id=vip_id_full, **kwargs)
            else:
                await bot.send_message(chat_id=vip_id_full, text=text, parse_mode=ParseMode.HTML,
                                        reply_markup=markup, disable_web_page_preview=True)
            if markup is None and kb:
                return {"sent": True, "error": "پست شد ولی بدون دکمه (دکمه توسط تلگرام رد شد: " + last_err[:80] + ")"}
            return {"sent": True, "error": None}
        except Exception as e:
            last_err = str(e)
            log.error(f"Send to VIP channel failed (button={'yes' if markup else 'no'}): {e}")
            if not (markup is not None and "button" in last_err.lower()):
                break                                  # خطای غیرمرتبط با دکمه → تلاش دوباره فایده ندارد
    return {"sent": False, "error": last_err[:200]}


@flask_app.post("/api/admin/push/send")
@admin_required
def api_admin_send_push():
    """ارسال اعلان به کاربران از هر مسیر انتخابی: نوتیف مرورگر + پیام/رسانه در ربات تلگرام + پست در کانال VIP.
    multipart/form-data: title, body, icon, target, user_ids(JSON), with_link('1'/'0'), button_text,
    channels_web('1'/'0'), channels_telegram('1'/'0'), also_channel('1'/'0'),
    media_type('photo'|'video'|'document', اختیاری), media(فایل، اختیاری)"""
    form = request.form
    title = (form.get("title") or "").strip()[:100]
    body = (form.get("body") or "").strip()[:1000]
    icon = form.get("icon", "chart")
    target = form.get("target", "all")
    try:
        user_ids = json.loads(form.get("user_ids") or "[]")
    except Exception:
        user_ids = []
    with_link = (form.get("with_link") or "0") == "1"
    button_text = (form.get("button_text") or "").strip()[:64] or "🏆 داشبورد VIP"
    also_channel = (form.get("also_channel") or "0") == "1"
    channels = {
        "web": (form.get("channels_web") or "0") == "1",
        "telegram": (form.get("channels_telegram") or "0") == "1",
    }

    media = None
    media_type = (form.get("media_type") or "").strip().lower()
    up_file = request.files.get("media")
    if up_file and up_file.filename:
        if media_type not in ("photo", "video", "document"):
            return jsonify({"error": "نوع رسانه نامعتبر است"}), 400
        file_bytes = up_file.read()
        if not file_bytes:
            return jsonify({"error": "فایل رسانه خالی است"}), 400
        media = {"type": media_type, "bytes": file_bytes, "filename": up_file.filename}

    if not body:
        return jsonify({"error": "متن اعلان لازم است"}), 400
    if not (channels["web"] or channels["telegram"] or also_channel):
        return jsonify({"error": "حداقل یک روش ارسال را انتخاب کنید"}), 400
    if target not in ("all", "vip", "specific"):
        return jsonify({"error": "target نامعتبر"}), 400
    if target == "specific" and not user_ids:
        return jsonify({"error": "حداقل یک کاربر انتخاب کنید"}), 400

    targets = _resolve_push_targets(target, user_ids) if (channels["web"] or channels["telegram"]) else []
    if (channels["web"] or channels["telegram"]) and not targets:
        return jsonify({"error": "کاربری برای دریافت اعلان یافت نشد"}), 404
    uid_set = {int(u["uid"]) for u in targets}

    # متن تلگرام: قالب‌بندی HTML تلگرام (مثل <b>، <i>، <u>، <a>، <code>) همون‌طور که تایپ شده حفظ می‌شود.
    # اگه عنوان خالی باشه، فقط خودِ متن اعلان (کامل) ارسال می‌شود؛ در غیر این صورت عنوان به‌صورت بولد بالای متن اضافه می‌شود.
    text = f"🔔 <b>{title}</b>\n\n{body}" if title else body
    # برای نوتیف مرورگر که HTML را رندر نمی‌کند، تگ‌ها حذف می‌شوند تا متن خام و تمیز نمایش داده شود.
    web_title = _strip_html_tags(title) or "AZADI VIP"
    web_body = _strip_html_tags(body)
    kb = None
    if with_link and WEBAPP_URL:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(button_text, web_app=WebAppInfo(url=WEBAPP_URL))]])

    bot, loop = get_bot(timeout=5) if (channels["telegram"] or also_channel) else (None, None)

    job = _push_job_new(target, channels)

    # ── مسیر ۱: نوتیف مرورگر (فقط متن؛ رسانه در نوتیف مرورگر پشتیبانی نمی‌شود) ──
    if channels["web"]:
        if not (VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY):
            job["web"]["note"] = "کلیدهای VAPID تنظیم نشده‌اند"
            _push_job_finish(job, "web")
        else:
            subs = [s for s in storage.get_all_push_subscriptions("all") if s.get("uid") in uid_set]
            job["web"]["total"] = len(subs)
            if not subs:
                job["web"]["note"] = "هیچ‌کدام از این کاربران نوتیف مرورگر را فعال نکرده‌اند"
                _push_job_finish(job, "web")
            else:
                payload = {"title": web_title, "body": web_body, "icon": icon, "url": WEBAPP_URL,
                           "timestamp": int(time.time() * 1000)}
                threading.Thread(target=_run_web_push, args=(job, subs, payload), daemon=True).start()

    # ── مسیر ۲: پیام/رسانه در ربات تلگرام ──
    if channels["telegram"]:
        if not bot or not loop:
            job["telegram"]["note"] = "ربات هنوز آماده نیست"
            _push_job_finish(job, "telegram")
        else:
            job["telegram"]["total"] = len(targets)
            asyncio.run_coroutine_threadsafe(_run_telegram_push(job, bot, targets, text, kb, media), loop)

    # ── مسیر ۳: پست در کانال VIP (تکی، همزمان با درخواست) ──
    channel_result = None
    if also_channel:
        if not bot or not loop:
            channel_result = {"sent": False, "error": "ربات هنوز آماده نیست"}
        else:
            fut = asyncio.run_coroutine_threadsafe(_send_to_vip_channel(bot, text, (button_text if (with_link and WEBAPP_URL) else None), media), loop)
            try:
                channel_result = fut.result(timeout=30)
            except Exception as e:
                channel_result = {"sent": False, "error": str(e)[:120]}

    snap = _push_job_snapshot(job)
    if snap["web"]["total"] == 0 and snap["telegram"]["total"] == 0 and not (also_channel and channel_result and channel_result.get("sent")):
        notes = " | ".join(n for n in (snap["web"]["note"], snap["telegram"]["note"]) if n) or "ارسالی انجام نشد"
        if not also_channel:
            return jsonify({"error": notes}), 404

    log.info(f"🔔 Admin push started: job={job['id']} target={target} web={snap['web']['total']} tg={snap['telegram']['total']} channel={channel_result}")
    return jsonify({"job_id": job["id"], "job": snap, "recipients": len(targets), "channel": channel_result})


@flask_app.get("/api/admin/push/job/<job_id>")
@admin_required
def api_admin_push_job(job_id):
    with _push_jobs_lock:
        job = _push_jobs.get(job_id)
    if not job:
        return jsonify({"error": "کار ارسال یافت نشد"}), 404
    return jsonify(_push_job_snapshot(job))


@flask_app.get("/api/admin/users/list")
@admin_required
def api_admin_users_list():
    """لیست سبک کاربران برای انتخاب گیرنده‌ها (بدون فیلدهای حساس مثل توکن‌ها)"""
    now = now_ir()
    rows = []
    for u in storage.get_all_users():
        uid = u.get("uid")
        if uid is None:
            continue
        is_vip = False
        if u.get("vip_expiry"):
            try: is_vip = datetime.fromisoformat(u["vip_expiry"]) > now
            except Exception: pass
        rows.append({"uid": uid, "full_name": u.get("full_name") or "",
                     "username": u.get("username") or "", "is_vip": is_vip})
    rows.sort(key=lambda r: (not r["is_vip"], (r["full_name"] or r["username"] or "").lower()))
    return jsonify({"users": rows})


# ══════════════════════════════════════════════════════════════════════
# PRESENCE — کاربران آنلاین (مینی‌اپ + مرورگر)
# ══════════════════════════════════════════════════════════════════════
# کلاینت هر ~۱۵ ثانیه (فقط وقتی صفحه دیده می‌شود) ping می‌زند؛ سرور هر کسی را که در ۳۵ ثانیه‌ی اخیر
# ping داشته «آنلاین» می‌داند. با بستن/مخفی‌شدن صفحه، کلاینت بلافاصله offline می‌فرستد.
# (برنامه تک‌پروسه است، پس نگهداری در حافظه کافی و بدون بار روی دیتابیس است.)
PRESENCE_TTL = 35
_presence = {}
_presence_lock = threading.Lock()


def _presence_platform() -> str:
    if request.headers.get("X-Browser-Token") or request.headers.get("Authorization", "").startswith("Bearer "):
        return "web"
    return "tg"


@flask_app.post("/api/presence/ping")
@auth_required
def api_presence_ping():
    u = g.current_user
    try:
        uid = int(u["uid"])
    except Exception:
        return jsonify({"ok": False}), 400
    data = request.get_json(silent=True) or {}
    plat = _presence_platform()
    now = time.time()
    with _presence_lock:
        ent = _presence.get(uid)
        if data.get("offline"):
            if ent:
                ent[plat] = 0
                if not (ent.get("tg") or ent.get("web")):
                    _presence.pop(uid, None)
        else:
            if not ent:
                ent = _presence[uid] = {"tg": 0, "web": 0}
            ent[plat] = now
            ent["name"] = (u.get("full_name") or "").strip()
            ent["username"] = u.get("username") or ""
            try:
                ent["is_vip"] = bool(u.get("vip_expiry") and datetime.fromisoformat(u["vip_expiry"]) > now_ir())
            except Exception:
                ent["is_vip"] = False
    return jsonify({"ok": True})


@flask_app.get("/api/admin/online")
@admin_required
def api_admin_online():
    now = time.time()
    rows = []
    n_tg = n_web = 0
    with _presence_lock:
        for uid, ent in list(_presence.items()):
            plats = [p for p in ("tg", "web") if ent.get(p) and now - ent[p] <= PRESENCE_TTL]
            if not plats:
                _presence.pop(uid, None)
                continue
            n_tg += 1 if "tg" in plats else 0
            n_web += 1 if "web" in plats else 0
            rows.append({"uid": uid, "name": ent.get("name") or "", "username": ent.get("username") or "",
                         "is_vip": bool(ent.get("is_vip")), "platforms": plats})
    rows.sort(key=lambda r: (not r["is_vip"], (r["name"] or r["username"] or "").lower(), r["uid"]))
    return jsonify({"count": len(rows), "telegram": n_tg, "web": n_web, "users": rows[:200]})


@flask_app.get("/api/admin/push/stats")
@admin_required
def api_admin_push_stats():
    """آمار اشتراک‌های push"""
    col = storage._ensure_push_subs_collection()
    
    if storage._is_memory:
        total = len(col)
    else:
        total = col.count_documents({})
    
    return jsonify({
        "total_subscriptions": total,
        "vapid_configured": bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY),
    })

# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════
def main():
    global bot_instance, bot_loop

    # انتقال داده‌ی ربات قدیمی (فقط بار اول؛ با  --migrate-legacy  می‌توان دستی و مجدد اجرا کرد)
    _res = migrate_legacy_data(force="--migrate-legacy" in sys.argv)
    if "--migrate-legacy" in sys.argv:
        print(_res)
        return
    
    def run_bot():
        global bot_instance, bot_loop
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        bot_loop = loop
        
        async def _run():
            global bot_instance
            
            app = Application.builder().token(BOT_TOKEN).build()
            app.add_handler(CommandHandler("start", cmd_start))
            app.add_handler(CallbackQueryHandler(on_callback))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
            app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, on_channel_post))
            
            log.info("🤖 ربات تلگرام در حال آماده‌سازی...")
            await app.initialize()
            await app.start()
            bot_instance = app.bot
            await app.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=["message", "callback_query", "channel_post"],
            )
            
            # ✅ علامت‌گذاری که bot آماده است
            bot_ready_event.set()
            log.info("✅ ربات تلگرام آماده شد")
            
            asyncio.create_task(expiration_watcher(app.bot))
            
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
            finally:
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
        
        loop.run_until_complete(_run())
    
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # ✅ صبر کن تا bot آماده شود (حداکثر 30 ثانیه)
    log.info("⏳ منتظر آماده شدن ربات...")
    if bot_ready_event.wait(timeout=30):
        log.info("✅ ربات آماده، Flask شروع می‌شود")
    else:
        log.warning("⚠️ Timeout در آماده‌سازی ربات - Flask بدون ربات شروع می‌شود")
    
    log.info(f"🌐 API روی http://{API_HOST}:{API_PORT}")
    flask_app.run(host=API_HOST, port=API_PORT, threaded=True)


if __name__ == "__main__":
    main()
