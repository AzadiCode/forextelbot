#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gold Prediction Tournament Bot — نسخه‌ی ۲ (ارتقا یافته)
════════════════════════════════════════════════════════════════════
تورنومنت هفتگی پیش‌بینی قیمت انس جهانی طلا (XAU/USD)
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
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from flask import Flask, request  # request را اضافه کنید
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Chat
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
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
log = logging.getLogger("GoldPredictionBot")

# ══════════════════════════════════════════════════════════════════════
# CONFIG (همه از Environment Variable خوانده می‌شوند)
# ══════════════════════════════════════════════════════════════════════
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
PORT        = int(os.environ.get("PORT", 5000))
JSONBIN_KEY = os.environ.get("JSONBIN_API_KEY", "")
JSONBIN_BIN = os.environ.get("JSONBIN_BIN_ID", "")

_admin_raw = os.environ.get("ADMIN_IDS", os.environ.get("ADMIN_ID", "605611554"))
ADMIN_IDS = {int(x) for x in _admin_raw.replace(" ", "").split(",") if x.strip()}

CHANNEL_ID        = os.environ.get("CHANNEL_ID", "@GoldLiveHunters")
# کانالی که کاربر باید عضو آن باشد؛ اگر خالی باشد چک عضویت انجام نمی‌شود
FORCE_JOIN_CHANNEL = os.environ.get("FORCE_JOIN_CHANNEL", "@GoldLiveHunters")

POINTS_PER_CORRECT   = int(os.environ.get("POINTS_PER_CORRECT", 200))
REF_POINTS_REFERRER  = int(os.environ.get("REF_POINTS_REFERRER", 90))
REF_POINTS_INVITEE   = int(os.environ.get("REF_POINTS_INVITEE", 20))
REF_BONUS_STEP1      = int(os.environ.get("REF_BONUS_STEP1", 20))  # ورود اولیه
REF_BONUS_STEP2      = int(os.environ.get("REF_BONUS_STEP2", 30))  # اولین شرکت در دور
REF_BONUS_STEP3      = int(os.environ.get("REF_BONUS_STEP3", 40))  # اولین پیش‌بینی درست
DEFAULT_ROUND_MIN    = int(os.environ.get("DEFAULT_ROUND_MINUTES", 60))
VOTE_WINDOW_MINUTES  = int(os.environ.get("VOTE_WINDOW_MINUTES", 10))
LIVE_UPDATE_SEC      = int(os.environ.get("LIVE_UPDATE_SEC", 30))
WATCHER_TICK_SEC     = 5
TOP_REPORT_COUNT     = 30
PROP_PRIZE_RANKS     = 3
NOTIFY_SEND_DELAY    = 0.05

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))
BOT_USERNAME = ""

# تعداد کاربرانی که در هر صفحه‌ی پنل ادمین نمایش داده می‌شوند
ADMIN_PAGE_SIZE = 8


def now_ir() -> datetime:
    return datetime.now(IRAN_TZ)


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def next_weekly_reset_dt(now: datetime) -> datetime:
    days_ahead = (5 - now.weekday()) % 7
    reset_dt = (now + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
    if reset_dt <= now:
        reset_dt += timedelta(days=7)
    return reset_dt


def _fmt_mmss(seconds: float) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


def _fmt_timedelta_long(seconds: float) -> str:
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d} روز")
    if h or d:
        parts.append(f"{h} ساعت")
    parts.append(f"{m} دقیقه")
    return " و ".join(parts)


def _display_name(u: dict) -> str:
    """اسم نمایشی برای لیدربرد — اسم کامل اگر موجود باشد، وگرنه یوزرنیم یا ID."""
    full = u.get("full_name", "").strip()
    if full:
        return full
    uname = u.get("username", "").strip()
    return uname if uname else f"کاربر {str(u.get('uid', '?'))[-4:]}"


# ══════════════════════════════════════════════════════════════════════
# GOLD PRICE FEED
# ══════════════════════════════════════════════════════════════════════
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finance.yahoo.com/",
}


@dataclass
class Source:
    name: str
    url: str
    parser: object
    min_val: float
    max_val: float


GOLD_SOURCES = [
    Source(
        "GoldPrice", "https://data-asg.goldprice.org/dbXRates/USD",
        lambda d: d["items"][0]["xauPrice"], 1000, 12000,
    ),
    Source(
        "Swissquote",
        "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD",
        lambda d: (float(d[0]["spreadProfilePrices"][0]["ask"]) +
                   float(d[0]["spreadProfilePrices"][0]["bid"])) / 2,
        1000, 12000,
    ),
    Source(
        "Yahoo", "https://query1.finance.yahoo.com/v8/finance/chart/GC=F",
        lambda d: d["chart"]["result"][0]["meta"]["regularMarketPrice"], 1000, 12000,
    ),
]

_price_cache: Optional[tuple] = None
PRICE_CACHE_TTL = 10


def get_gold_price(force: bool = False) -> Optional[float]:
    global _price_cache
    if not force and _price_cache:
        p, ts = _price_cache
        if time.time() - ts < PRICE_CACHE_TTL:
            return p
    for src in GOLD_SOURCES:
        try:
            r = requests.get(src.url, headers=_HEADERS, timeout=10)
            r.raise_for_status()
            p = float(src.parser(r.json()))
            if src.min_val < p < src.max_val:
                p = round(p, 2)
                _price_cache = (p, time.time())
                return p
        except Exception as e:
            log.warning(f"price source {src.name} failed: {str(e)[:80]}")
    return None


# ══════════════════════════════════════════════════════════════════════
# CLOUD STORAGE (MongoDB Atlas - جایگزین JSONBin)
# ══════════════════════════════════════════════════════════════════════
try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
    MONGODB_AVAILABLE = True
except ImportError:
    MONGODB_AVAILABLE = False


class CloudStorage:
    def __init__(self):
        self._uri = os.environ.get("MONGODB_URI", "")
        self._client = None
        self._db = None
        self._collection = None
        self._lock = threading.Lock()
        self._dirty = False
        self._data = {
            "users": {},
            "fake_users": {},
            "active_round": None,
            "next_round_id": 1,
            "rounds_history": [],
            "leaderboard_history": [],
            "last_lb_reset": None,
            "last_updated": None,
        }
        
        # اتصال اولیه به MongoDB
        if self._uri and MONGODB_AVAILABLE:
            try:
                self._client = MongoClient(
                    self._uri,
                    serverSelectionTimeoutMS=10000,
                    connectTimeoutMS=10000,
                )
                # تست اتصال
                self._client.admin.command('ping')
                self._db = self._client.get_database()
                self._collection = self._db["bot_data"]
                log.info("MongoDB: اتصال اولیه برقرار شد ✅")
            except Exception as e:
                log.error(f"MongoDB اتصال اولیه ناموفق: {e}")
                self._collection = None
        elif not MONGODB_AVAILABLE:
            log.critical("کتابخانه pymongo نصب نیست! لطفاً requirements1.txt را بررسی کنید.")
    
    # ---------- persistence ----------
    def load(self):
        if not self._uri or self._collection is None:
            log.warning("MongoDB تنظیم نشده — داده‌ها فقط در حافظه نگه داده می‌شوند.")
            return
        try:
            doc = self._collection.find_one({"_id": "main"})
            if doc:
                with self._lock:
                    doc.pop("_id", None)
                    for k, v in doc.items():
                        self._data[k] = v
                    # اطمینان از وجود کلیدهای ضروری
                    self._data.setdefault("users", {})
                    self._data.setdefault("fake_users", {})
                    self._data.setdefault("active_round", None)
                    self._data.setdefault("next_round_id", 1)
                    self._data.setdefault("rounds_history", [])
                    self._data.setdefault("leaderboard_history", [])
                    self._data.setdefault("last_lb_reset", None)
                log.info("MongoDB: داده‌ها با موفقیت بارگذاری شد.")
            else:
                log.info("MongoDB: دیتابیس خالی — شروع از صفر.")
        except Exception as e:
            log.error(f"MongoDB load error: {e}")

    def save(self, force: bool = False):
        if not self._uri or self._collection is None:
            return
        if not force and not self._dirty:
            return
        with self._lock:
            self._data["last_updated"] = now_ir().isoformat()
            payload = {"_id": "main"}
            payload.update(self._data)
        try:
            self._collection.replace_one({"_id": "main"}, payload, upsert=True)
            self._dirty = False
        except Exception as e:
            log.error(f"MongoDB save error: {e}")

    # ---------- users ----------
    def ensure_user(self, uid: int, username: str = "", full_name: str = "") -> dict:
        key = str(uid)
        with self._lock:
            if key not in self._data["users"]:
                ref_code = hashlib.md5((key + str(time.time())).encode()).hexdigest()[:8]
                self._data["users"][key] = {
                    "uid": uid,
                    "username": username or "",
                    "full_name": full_name or "",
                    "ref": ref_code, "referred_by": None,
                    "points": 0, "total_points": 0,
                    "correct": 0, "predictions": 0, "invites": 0,
                }
                self._dirty = True
            else:
                changed = False
                if username and self._data["users"][key].get("username") != username:
                    self._data["users"][key]["username"] = username
                    changed = True
                if full_name and self._data["users"][key].get("full_name") != full_name:
                    self._data["users"][key]["full_name"] = full_name
                    changed = True
                if "uid" not in self._data["users"][key]:
                    self._data["users"][key]["uid"] = uid
                    changed = True
                if changed:
                    self._dirty = True
            return dict(self._data["users"][key])

    def get_user(self, uid: int) -> Optional[dict]:
        with self._lock:
            u = self._data["users"].get(str(uid))
            return dict(u) if u else None

    def get_all_users(self) -> list:
        with self._lock:
            return [
                {"uid_str": k, **v}
                for k, v in self._data["users"].items()
            ]

    # ---------- fake users ----------
    def set_fake_user(self, fake_id: str, full_name: str, points: int):
        with self._lock:
            existing = self._data["fake_users"].get(fake_id)
            self._data["fake_users"][fake_id] = {
                "full_name": full_name,
                "username": "",
                "points": points,
                "total_points": points,
                "is_fake": True,
            }
            self._dirty = True

    def delete_fake_user(self, fake_id: str) -> bool:
        with self._lock:
            if fake_id in self._data["fake_users"]:
                del self._data["fake_users"][fake_id]
                
                # ✅ FIX: حذف خودکار فیک از لیست فیک‌های انتخاب‌شده (auto_fake_ids) در صورت وجود
                auto_ids = self._data.setdefault("auto_fake_ids", [])
                if fake_id in auto_ids:
                    auto_ids.remove(fake_id)
                    log.info(f"🗑 فیک '{fake_id}' از لیست فیک‌های انتخاب‌شده هم حذف شد.")
                
                self._dirty = True
                return True
            return False

    def get_fake_users(self) -> dict:
        with self._lock:
            return dict(self._data.get("fake_users", {}))

    # ---------- referral ----------
    def find_uid_by_ref(self, ref_code: str) -> Optional[int]:
        with self._lock:
            for uid, data in self._data["users"].items():
                if data.get("ref") == ref_code:
                    return int(uid)
        return None

    def apply_referral(self, new_uid: int, ref_uid: int) -> bool:
        if new_uid == ref_uid:
            return False
        n_key, r_key = str(new_uid), str(ref_uid)
        with self._lock:
            users = self._data["users"]
            if n_key not in users or r_key not in users:
                return False
            if users[n_key].get("referred_by"):
                return False
            users[n_key]["referred_by"] = r_key
            users[n_key]["ref_step1_done"] = True
            users[n_key]["ref_step2_done"] = False
            users[n_key]["ref_step3_done"] = False
            
            users[r_key]["points"] = users[r_key].get("points", 0) + REF_BONUS_STEP1
            users[r_key]["total_points"] = users[r_key].get("total_points", 0) + REF_BONUS_STEP1
            users[r_key]["invites"] = users[r_key].get("invites", 0) + 1
            
            users[n_key]["points"] = users[n_key].get("points", 0) + REF_POINTS_INVITEE
            users[n_key]["total_points"] = users[n_key].get("total_points", 0) + REF_POINTS_INVITEE
            self._dirty = True
            return True

    def check_referral_steps(self, uid: int, step: str) -> Optional[int]:
        key = str(uid)
        with self._lock:
            u = self._data["users"].get(key)
            if not u or not u.get("referred_by"):
                return None
            
            ref_uid_str = u["referred_by"]
            ref_user = self._data["users"].get(ref_uid_str)
            if not ref_user:
                return None
            
            if step == "step2" and not u.get("ref_step2_done"):
                u["ref_step2_done"] = True
                ref_user["points"] = ref_user.get("points", 0) + REF_BONUS_STEP2
                ref_user["total_points"] = ref_user.get("total_points", 0) + REF_BONUS_STEP2
                self._dirty = True
                return int(ref_uid_str)
            
            elif step == "step3" and not u.get("ref_step3_done"):
                u["ref_step3_done"] = True
                ref_user["points"] = ref_user.get("points", 0) + REF_BONUS_STEP3
                ref_user["total_points"] = ref_user.get("total_points", 0) + REF_BONUS_STEP3
                self._dirty = True
                return int(ref_uid_str)
            
            return None

    def count_referrals(self, uid_str: str) -> int:
        with self._lock:
            return sum(
                1 for u in self._data["users"].values()
                if u.get("referred_by") == uid_str
            )

    # ---------- points ----------
    def award_correct_prediction(self, uid: int):
        key = str(uid)
        with self._lock:
            u = self._data["users"].setdefault(key, {})
            u["points"] = u.get("points", 0) + POINTS_PER_CORRECT
            u["total_points"] = u.get("total_points", 0) + POINTS_PER_CORRECT
            u["correct"] = u.get("correct", 0) + 1
            self._dirty = True

    def set_user_points(self, uid_str: str, points: int):
        with self._lock:
            if uid_str in self._data["users"]:
                self._data["users"][uid_str]["points"] = points
                if points > self._data["users"][uid_str].get("total_points", 0):
                    self._data["users"][uid_str]["total_points"] = points
                self._dirty = True
                return True
            return False

    def add_user_points(self, uid_str: str, delta: int):
        with self._lock:
            if uid_str in self._data["users"]:
                cur = self._data["users"][uid_str].get("points", 0)
                new_pts = max(0, cur + delta)
                self._data["users"][uid_str]["points"] = new_pts
                if new_pts > self._data["users"][uid_str].get("total_points", 0):
                    self._data["users"][uid_str]["total_points"] = new_pts
                self._dirty = True
                return True
            return False

    def inc_prediction_count(self, uid: int):
        key = str(uid)
        with self._lock:
            u = self._data["users"].setdefault(key, {})
            u["predictions"] = u.get("predictions", 0) + 1
            self._dirty = True

    # ---------- leaderboard ----------
    def get_weekly_leaderboard(self, top: int = 10) -> list:
        with self._lock:
            combined = []
            for uid_str, u in self._data["users"].items():
                if u.get("points", 0) > 0:
                    combined.append({
                        "uid": uid_str,
                        "full_name": u.get("full_name", "").strip() or u.get("username", "") or f"کاربر {uid_str[-4:]}",
                        "username": u.get("username", ""),
                        "points": u.get("points", 0),
                        "is_fake": False,
                    })
            for fid, fu in self._data.get("fake_users", {}).items():
                if fu.get("points", 0) > 0:
                    combined.append({
                        "uid": fid,
                        "full_name": fu.get("full_name", fid),
                        "username": fu.get("username", ""),
                        "points": fu.get("points", 0),
                        "is_fake": True,
                    })
            ranked = sorted(combined, key=lambda x: x["points"], reverse=True)[:top]
            return [{"rank": i, **row} for i, row in enumerate(ranked, 1)]

    def get_active_participants_count(self) -> int:
        """تعداد کل شرکت‌کنندگانی که این هفته حداقل ۱ امتیاز کسب کرده‌اند (واقعی + فیک)"""
        with self._lock:
            count = 0
            # شمارش کاربران واقعی با امتیاز > 0
            for u in self._data["users"].values():
                if u.get("points", 0) > 0:
                    count += 1
            # شمارش کاربران فیک با امتیاز > 0
            for fu in self._data.get("fake_users", {}).values():
                if fu.get("points", 0) > 0:
                    count += 1
            return count

    def get_last_reset(self) -> Optional[str]:
        with self._lock:
            return self._data.get("last_lb_reset")

    def reset_weekly(self, date_key: str, snapshot: list):
        with self._lock:
            self._data.setdefault("leaderboard_history", []).append(
                {"week_end": date_key, "top": snapshot}
            )
            self._data["leaderboard_history"] = self._data["leaderboard_history"][-20:]
            for u in self._data["users"].values():
                u["points"] = 0
            for fu in self._data.get("fake_users", {}).values():
                fu["points"] = 0
                fu["auto_step"] = 0
            self._data["last_lb_reset"] = date_key
            self._dirty = True

    # ---------- rounds ----------
    def get_active_round(self) -> Optional[dict]:
        with self._lock:
            rnd = self._data.get("active_round")
            return dict(rnd) if rnd else None

    def create_round(self, start_price: float, duration_min: int, chat_id, message_id: int,
                      vote_minutes: Optional[int] = None) -> dict:
        with self._lock:
            rid = self._data.get("next_round_id", 1)
            self._data["next_round_id"] = rid + 1
            start = now_ir()
            vm = vote_minutes or VOTE_WINDOW_MINUTES
            vm = max(1, min(vm, duration_min))
            rnd = {
                "id": rid,
                "start_price": start_price,
                "start_time": start.isoformat(),
                "duration_min": duration_min,
                "vote_minutes": vm,
                "vote_deadline": (start + timedelta(minutes=vm)).isoformat(),
                "vote_closed": False,
                "lock_time": (start + timedelta(minutes=duration_min)).isoformat(),
                "chat_id": chat_id,
                "message_id": message_id,
                "status": "open",
                "predictions": {},
                "last_edit_ts": 0,
            }
            self._data["active_round"] = rnd
            self._dirty = True
            return dict(rnd)

    def record_vote(self, round_id: int, uid: int, direction: str, username: str) -> str:
        with self._lock:
            rnd = self._data.get("active_round")
            if not rnd or rnd["id"] != round_id or rnd["status"] != "open":
                return "invalid"
            if rnd.get("vote_closed"):
                return "closed"
            vote_deadline_dt = datetime.fromisoformat(rnd["vote_deadline"])
            if now_ir() >= vote_deadline_dt:
                rnd["vote_closed"] = True
                self._dirty = True
                return "closed"
            if str(uid) in rnd["predictions"]:
                return "already_voted"
            rnd["predictions"][str(uid)] = {
                "dir": direction, "username": username or "", "at": now_ir().isoformat(),
            }
            self._dirty = True
            return "ok"

    def mark_vote_closed(self, round_id: int):
        with self._lock:
            rnd = self._data.get("active_round")
            if rnd and rnd["id"] == round_id:
                rnd["vote_closed"] = True
                self._dirty = True

    def touch_round_edit(self, round_id: int):
        with self._lock:
            rnd = self._data.get("active_round")
            if rnd and rnd["id"] == round_id:
                rnd["last_edit_ts"] = time.time()
                self._dirty = True

    def set_round_status(self, round_id: int, status: str):
        with self._lock:
            rnd = self._data.get("active_round")
            if rnd and rnd["id"] == round_id:
                rnd["status"] = status
                self._dirty = True

    def postpone_round(self, round_id: int, minutes: int = 1):
        with self._lock:
            rnd = self._data.get("active_round")
            if rnd and rnd["id"] == round_id:
                rnd["lock_time"] = (now_ir() + timedelta(minutes=minutes)).isoformat()
                self._dirty = True

    def finish_round(self, round_id: int, end_price: float, direction: str, winners: list):
        with self._lock:
            rnd = self._data.get("active_round")
            if not rnd or rnd["id"] != round_id:
                return
            record = dict(rnd)
            record["end_price"] = end_price
            record["direction"] = direction
            record["winners"] = winners
            record["status"] = "resolved"
            record["participants_count"] = len(rnd.get("predictions", {}))
            record.pop("predictions", None)
            record.pop("last_edit_ts", None)
            self._data.setdefault("rounds_history", []).insert(0, record)
            self._data["rounds_history"] = self._data["rounds_history"][:50]
            self._data["active_round"] = None
            self._dirty = True

    def cancel_active_round(self) -> Optional[dict]:
        with self._lock:
            rnd = self._data.get("active_round")
            self._data["active_round"] = None
            self._dirty = True
            return dict(rnd) if rnd else None

    # ---------- auto fake referral ----------
    def get_auto_fake_ids(self) -> list:
        with self._lock:
            return list(self._data.get("auto_fake_ids", []))

    def toggle_auto_fake_id(self, fake_id: str) -> bool:
        with self._lock:
            ids = self._data.setdefault("auto_fake_ids", [])
            if fake_id in ids:
                ids.remove(fake_id)
                self._dirty = True
                return False
            else:
                ids.append(fake_id)
                self._dirty = True
                return True

    # ---------- auto score settings ----------
    def get_auto_score_settings(self) -> dict:
        """دریافت تنظیمات سیستم امتیاز خودکار"""
        with self._lock:
            return self._data.get("auto_score_settings", {
                "min_percentage": 30,  # حداقل درصد فیک‌ها
                "max_percentage": 70,  # حداکثر درصد فیک‌ها
                "base_points": 200,     # امتیاز پایه (پیش‌بینی درست)
            })
    
    def update_auto_score_settings(self, settings: dict) -> bool:
        """به‌روزرسانی تنظیمات سیستم امتیاز خودکار"""
        with self._lock:
            self._data["auto_score_settings"] = settings
            self._dirty = True
            return True
    
    def award_auto_score_to_fakes(self, base_points: int = 200) -> list:
        """
        اعطای امتیاز خودکار به فیک‌های انتخاب‌شده بعد از پایان دور
        امتیاز = base_points (200) + ترکیبی رندوم از مراحل رفرال (20, 30, 40)
        """
        auto_ids = self.get_auto_fake_ids()
        if not auto_ids:
            return []
        
        # دریافت تنظیمات
        settings = self.get_auto_score_settings()
        min_pct = settings.get("min_percentage", 30)
        max_pct = settings.get("max_percentage", 70)
        
        # انتخاب درصد رندوم بین min و max
        percentage = random.uniform(min_pct, max_pct) / 100
        count_to_award = max(1, int(len(auto_ids) * percentage))
        selected_fakes = random.sample(auto_ids, min(count_to_award, len(auto_ids)))
        
        # لیست امتیازهای رفرال که ممکن است اضافه شوند
        referral_bonuses = [0, 40, 0, 40, 80]  # هیچکدام، مرحله 1، مرحله 2، مرحله 3
        
        awarded = []
        with self._lock:
            for fid in selected_fakes:
                fu = self._data.get("fake_users", {}).get(fid)
                if fu:
                    # انتخاب رندوم از امتیازهای رفرال (می‌تواند 0، 1، 2 یا 3 تا باشد)
                    num_bonuses = random.choices([0, 1, 2, 3], weights=[40, 30, 20, 10])[0]
                    selected_bonuses = random.sample(referral_bonuses[1:], num_bonuses) if num_bonuses > 0 else []
                    
                    # محاسبه امتیاز کل
                    bonus_total = sum(selected_bonuses)
                    total_points = base_points + bonus_total
                    
                    # اضافه کردن امتیاز
                    fu["points"] = fu.get("points", 0) + total_points
                    fu["total_points"] = fu.get("total_points", 0) + total_points
                    fu["correct"] = fu.get("correct", 0) + 1  # افزایش تعداد پیش‌بینی‌های درست
                    
                    awarded.append({
                        "name": fu.get("full_name", fid),
                        "points": total_points,
                        "breakdown": f"{base_points}+{bonus_total}",
                    })
            
            self._dirty = True
        
        return awarded


cloud = CloudStorage()
fake_referral_enabled = False  # وضعیت سیستم رشد خودکار فیک‌ها
auto_score_enabled = False     # وضعیت سیستم امتیازدهی خودکار بعد از هر دور

# ══════════════════════════════════════════════════════════════════════
# MEMBERSHIP CHECK  (عضویت اجباری)
# ══════════════════════════════════════════════════════════════════════
async def check_membership(bot, uid: int) -> bool:
    """True اگر کاربر عضو FORCE_JOIN_CHANNEL است یا این ویژگی غیرفعال است."""
    if not FORCE_JOIN_CHANNEL:
        return True
    try:
        member = await bot.get_chat_member(chat_id=FORCE_JOIN_CHANNEL, user_id=uid)
        return member.status not in ("left", "kicked", "banned")
    except Exception as e:
        log.warning(f"membership check failed for {uid}: {e}")
        return True  # در صورت خطا، اجازه می‌دهیم تا ربات از کار نیفتد


def build_join_required_text() -> str:
    channel_link = FORCE_JOIN_CHANNEL if FORCE_JOIN_CHANNEL.startswith("@") else "کانال"
    return (
        "⛔️ <b>برای استفاده از ربات باید عضو کانال ما باشی!</b>\n\n"
        f"👉 ابتدا عضو {channel_link} شو، سپس دوباره امتحان کن.\n\n"
        "بعد از عضویت روی دکمه‌ی «✅ عضو شدم» بزن."
    )

def build_referral_join_text() -> str:
    """متن جذاب برای عضویت اجباری هنگام ورود با لینک دعوت"""
    channel_link = FORCE_JOIN_CHANNEL if FORCE_JOIN_CHANNEL.startswith("@") else "کانال"
    return (
        "🎁✨ <b>تبریک! دوستت تو رو به تورنومنت و جمع شکارچیان طلا دعوت کرده!</b> ✨🎁\n\n"
        "🔥 <b>توی این کانال چی می‌گذره؟</b>\n"
        "  🥇 <b>تورنومنت هفتگی طلا</b> با جوایز <b>نقدی دلاری</b>\n"
        "  💰 نفر اول <b>۸۰$</b> | دوم <b>۶۰$</b> | سوم <b>۴۰$</b>\n"
        "  🎲 قرعه‌کشی <b>۳۰ دلاری</b> بین نفرات ۴ تا ۱۰\n"
        "  ⚡️ <b>لایو تریدهای اسکلپ</b> روی طلا\n"
        "  📊 وین‌ریت مثبت، کاملاً <b>شفاف و لایو</b>، بدون ابهام\n\n"
        f"👉 همه چیز فقط در این چنل اختصاصی طلا {channel_link} \n\n"
        "🔥 <b>برای دریافت پاداش دعوت و شرکت در تورنومنت ها و استفاده از لایو ترید های ویژه همین الان عضو شو و برای ادامه روی عضو شدم بزن !</b>"
    )


def kb_join_required() -> InlineKeyboardMarkup:
    btns = []
    if FORCE_JOIN_CHANNEL:
        if FORCE_JOIN_CHANNEL.startswith("@"):
            channel_url = f"https://t.me/{FORCE_JOIN_CHANNEL.lstrip('@')}"
        else:
            channel_url = f"https://t.me/c/{str(FORCE_JOIN_CHANNEL).replace('-100', '')}"
        btns.append([InlineKeyboardButton("📢 عضویت در کانال", url=channel_url)])
    btns.append([InlineKeyboardButton("✅ عضو شدم — ادامه بده", callback_data="check_join")])
    return InlineKeyboardMarkup(btns)


# ══════════════════════════════════════════════════════════════════════
# KEYBOARDS
# ══════════════════════════════════════════════════════════════════════
def _profile_deeplink_url() -> str:
    if BOT_USERNAME:
        return f"https://t.me/{BOT_USERNAME}?start=profile"
    return "https://t.me/"


def kb_main(uid: int = 0) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("👤 پروفایل من", callback_data="menu:profile"),
         InlineKeyboardButton("🏆 لیدربرد هفتگی زنده", callback_data="menu:leaderboard")],
        [InlineKeyboardButton("🎁 پاداش و کسب امتیاز بیشتر ", callback_data="menu:invite")],
        [InlineKeyboardButton("ℹ️ قوانین و راهنما ", callback_data="menu:help")],
    ]
    if uid and is_admin(uid):
        rows.append([InlineKeyboardButton("👑 پنل ادمین", callback_data="admin:panel:0")])
    return InlineKeyboardMarkup(rows)


def kb_round_voting(round_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 صعودی", callback_data=f"vote:{round_id}:up"),
         InlineKeyboardButton("🔴 نزولی", callback_data=f"vote:{round_id}:down")],
        [InlineKeyboardButton("📍 پروفایل من در ربات", url=_profile_deeplink_url())],
    ])


def kb_round_teaser() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📍 برای دیدن جزئیات و امتیازت کلیک کن", url=_profile_deeplink_url())],
    ])


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="menu:home")]])


# ══════════════════════════════════════════════════════════════════════
# ADMIN PANEL KEYBOARDS & TEXTS
# ══════════════════════════════════════════════════════════════════════
def kb_admin_main() -> InlineKeyboardMarkup:
    status_fake = "🟢 فعال" if fake_referral_enabled else "🔴 غیرفعال"
    status_score = "🟢 فعال" if auto_score_enabled else "🔴 غیرفعال"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 لیست کاربران", callback_data="admin:users:0")],
        [InlineKeyboardButton("🤖 کاربران فیک", callback_data="admin:fakes:list")],
        [InlineKeyboardButton("➕ افزودن کاربر فیک", callback_data="admin:fake:add_prompt")],
        [InlineKeyboardButton("📢 ارسال همگانی", callback_data="admin:broadcast:prompt")],
        [InlineKeyboardButton("📩 ارسال به کاربر خاص", callback_data="admin:senduser:uid_prompt")],
        [InlineKeyboardButton(f"🔄 رشد تدریجی فیک‌ها: {status_fake}", callback_data="admin:fake_auto:toggle")],
        [InlineKeyboardButton("📋 مدیریت لیست فیک‌ها", callback_data="admin:fake_auto:list")],
        [InlineKeyboardButton("🎯 افزایش دستی امتیاز فیک‌ها", callback_data="admin:fake_auto:boost_prompt")],
        [InlineKeyboardButton(f"🏆 امتیاز خودکار بعد دور: {status_score}", callback_data="admin:auto_score:toggle")],
        [InlineKeyboardButton("⚙️ تنظیمات امتیاز خودکار", callback_data="admin:auto_score:settings")],
        [InlineKeyboardButton("🔙 بازگشت به منو", callback_data="menu:home")],
    ])


def build_admin_panel_text() -> str:
    users = cloud.get_all_users()
    fakes = cloud.get_fake_users()
    return (
        "👑 <b>پنل مدیریت ادمین</b>\n\n"
        f"👥 تعداد کاربران واقعی: <b>{len(users)}</b>\n"
        f"🤖 تعداد کاربران فیک: <b>{len(fakes)}</b>\n\n"
        "از منوی زیر یک گزینه انتخاب کن:"
    )


def build_users_page(page: int) -> tuple:
    """(متن، کیبورد) صفحه‌ی page از لیست کاربران."""
    users = sorted(cloud.get_all_users(), key=lambda u: u.get("points", 0), reverse=True)
    total = len(users)
    start = page * ADMIN_PAGE_SIZE
    end = min(start + ADMIN_PAGE_SIZE, total)
    page_users = users[start:end]

    lines = [f"👥 <b>لیست کاربران</b> (صفحه {page + 1} از {(total - 1) // ADMIN_PAGE_SIZE + 1})\n"]
    rows = []
    for u in page_users:
        uid_str = u.get("uid_str") or str(u.get("uid", "?"))
        fname = u.get("full_name", "").strip() or "—"
        uname = f"@{u['username']}" if u.get("username") else "—"
        pts = u.get("points", 0)
        refs = cloud.count_referrals(uid_str)
        lines.append(
            f"• <b>{fname}</b> | {uname}\n"
            f"  🆔 <code>{uid_str}</code> | ⭐️ {pts} امتیاز | 👥 {refs} زیرمجموعه"
        )
        rows.append([InlineKeyboardButton(
            f"✏️ تنظیم امتیاز: {fname[:12]}",
            callback_data=f"admin:setpts:{uid_str}"
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"admin:users:{page - 1}"))
    if end < total:
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"admin:users:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 پنل ادمین", callback_data="admin:panel:0")])

    return "\n".join(lines), InlineKeyboardMarkup(rows)

def build_auto_fake_list_page() -> tuple:
    """پنل مدیریت لیست فیک‌های رشد خودکار."""
    fakes = cloud.get_fake_users()
    auto_ids = cloud.get_auto_fake_ids()
    
    text = "📋 <b>مدیریت لیست رشد خودکار فیک‌ها</b>\n\n"
    text += f"تعداد فیک‌های انتخاب‌شده: <b>{len(auto_ids)}</b>\n\n"
    text += "روی هر فیک بزنید تا به لیست اضافه/حذف شود:\n\n"
    
    if not fakes:
        text += "⚠️ هنوز کاربر فیکی نساخته‌اید."
    
    rows = []
    for fid, fu in fakes.items():
        marker = "✅" if fid in auto_ids else "⬜️"
        rows.append([InlineKeyboardButton(
            f"{marker} {fu.get('full_name', fid)[:20]}",
            callback_data=f"admin:fake_auto:toggle_id:{fid}"
        )])
    
    rows.append([InlineKeyboardButton("🔙 پنل ادمین", callback_data="admin:panel:0")])
    
    return text, InlineKeyboardMarkup(rows)


def build_fakes_page() -> tuple:
    fakes = cloud.get_fake_users()
    if not fakes:
        text = "🤖 <b>کاربران فیک</b>\n\nهنوز کاربر فیکی اضافه نشده."
    else:
        lines = ["🤖 <b>کاربران فیک (در لیدربرد نمایش داده می‌شوند)</b>\n"]
        for fid, fu in fakes.items():
            lines.append(f"• <b>{fu.get('full_name', fid)}</b> — ⭐️ {fu.get('points', 0)} امتیاز (ID: <code>{fid}</code>)")
        text = "\n".join(lines)

    rows = []
    for fid in fakes:
        fu = fakes[fid]
        rows.append([
            InlineKeyboardButton(f"✏️ {fu.get('full_name', fid)[:15]}", callback_data=f"admin:fake:edit:{fid}"),
            InlineKeyboardButton("🗑 حذف", callback_data=f"admin:fake:del:{fid}"),
        ])
    rows.append([InlineKeyboardButton("➕ افزودن فیک جدید", callback_data="admin:fake:add_prompt")])
    rows.append([InlineKeyboardButton("🔙 پنل ادمین", callback_data="admin:panel:0")])
    return text, InlineKeyboardMarkup(rows)


# ══════════════════════════════════════════════════════════════════════
# TEXT BUILDERS
# ══════════════════════════════════════════════════════════════════════

def mention_html(uid: int, name: str, username: str = "") -> str:
    """ساخت HTML mention برای کاربر - ۱۰۰٪ در تلگرام کار می‌کند (حتی بدون یوزرنیم)"""
    # Escape کردن کاراکترهای خاص HTML در نام
    safe_name = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if not safe_name.strip():
        safe_name = f"کاربر {uid}"
    if username:
        # اگر یوزرنیم دارد، لینک به پروفایل عمومی
        return f'<a href="https://t.me/{username}">{safe_name}</a>'
    else:
        # اگر یوزرنیم ندارد، لینک مستقیم به ID (mention خصوصی - ۱۰۰٪ کار می‌کند)
        return f'<a href="tg://user?id={uid}">{safe_name}</a>'




def _fmt_price(p: float) -> str:
    return f"{p:,.2f}"


def build_welcome_text() -> str:
    return (
        
        "🥇✨ <b>به بزرگ‌ترین تورنومنت پیش‌بینی قیمت طلا خوش اومدی!</b> ✨🥇\n\n"
        "<blockquote expandable>"
        "اینجا هر روز علاوه بر ارعه لایو ترید ها و سیگنال های اسکلپ در کانال ویژه طلا، هر روز طی ساعت مشخصی با پیشبینی 1 ساعت اینده روند طلا ، امتیاز دریافت میکنی که در اخر هر هفته به جوایز واقعی تبدیل میشه !! "
        "قیمت طلا بالا می‌ره یا پایین؟ پیش‌بینی کن، امتیاز بگیر، جایزه ببر!\n\n"
        "</blockquote>"
        "🔥 <b>توی کانال Azadi چی می‌گذره؟</b>\n"
        "  🥇 <b>تورنومنت هفتگی طلا</b> با جوایز نقدی\n"
        "  💰 نفر اول <b>۸۰$</b> | دوم <b>۶۰$</b> | سوم <b>۴۰$</b>\n"
        "  🎲 قرعه‌کشی <b>۳۰ دلاری</b> بین نفرات ۴ تا ۱۰\n"
        "  ⚡️ <b>لایو ترید و سیگنال های اسکلپ</b> ویژه طلا\n"
        "  📊 برآیند کل کانال همیشه مثبت، کاملاً <b>شفاف و لایو</b>\n\n"

        "همین حالا از منوی زیر شروع کن 👇"
    )


def build_round_text(rnd: dict, phase: str,
                      end_price: Optional[float] = None, direction: Optional[str] = None) -> str:
    start_p = _fmt_price(rnd["start_price"])

    if phase == "voting":
        vote_deadline_dt = datetime.fromisoformat(rnd["vote_deadline"])
        remaining = (vote_deadline_dt - now_ir()).total_seconds()
        countdown = _fmt_mmss(remaining)
        
        # محاسبه زمان باقی‌مانده تا پایان کل دور و مشخص شدن نتیجه
        lock_dt = datetime.fromisoformat(rnd["lock_time"])
        total_remaining = (lock_dt - now_ir()).total_seconds()
        total_countdown = _fmt_mmss(total_remaining)
        
        return (
            f"🥇 <b>دور #{rnd['id']} تورنومنت هفتگی پیشبینی طلا </b>\n"
            f"💰 قیمت در این لحضه شروع : <code>{start_p}</code>\n"
            f"⏳ زمان باقی‌مانده برای ثبت رأی: <code>{countdown}</code> ⏳\n"
            f"⏱ زمان باقی‌مانده تا پایان دور و مشخص شدن نتیجه: <code>{total_countdown}</code>\n\n"
            "طلا تا پایان این دور، نسبت به قیمت شروع صعودی میشه یا نزولی؟ "
            "⚠️ <i>هر کاربر فقط یک‌بار می‌تونه رأی بده و بعدش امکان تغییرش نیست.</i>\n"
            f"✅ پیش‌بینی درست = <b>{POINTS_PER_CORRECT} امتیاز</b>\n"
            "نظرت رو با یکی از دکمه‌های زیر ثبت کن 👇"
        )

    if phase == "voting_closed":
        lock_dt = datetime.fromisoformat(rnd["lock_time"])
        total_remaining = (lock_dt - now_ir()).total_seconds()
        total_countdown = _fmt_mmss(total_remaining)
        return (
            f"🥇 <b>دور #{rnd['id']} تورنومنت هفتگی پیشبینی طلا </b>\n"
            f"💰 قیمت شروع: <code>{start_p}</code>\n"
            "⛔️ <b>زمان ثبت رأی به پایان رسیده — امکان رأی دادن نیست.</b>\n"
            f"⏱ زمان باقی‌مانده تا پایان دور و مشخص شدن نتیجه: <code>{total_countdown}</code>\n\n"
            "نتیجه‌ی نهایی به‌محض بسته‌شدن دور اعلام می‌شه... ⏱\n"
            "📍 برای دیدن جزئیات و امتیازت، روی دکمه‌ی زیر بزن."
        )

    arrow = {"up": "🟢 صعودی", "down": "🔴 نزولی", "flat": "➖ بدون تغییر"}[direction]
    end_p = _fmt_price(end_price)
    if direction == "flat":
        result_line = f"نتیجه: <b>{arrow}</b> — این دور به‌دلیل عدم تغییر قیمت باطل شد و امتیازی اعطا نشد."
    else:
        result_line = f"نتیجه‌ی این دور: <b>{arrow}</b>"
    return (
        f"🥇 <b>دور #{rnd['id']} پیش‌بینی طلا  — پایان یافت ✅</b>\n\n"
        f"💰 قیمت در شروع دور: <code>{start_p}</code>   →   در پایان: <code>{end_p}</code>\n"
        f"{result_line}\n\n"
        "📍 برای دیدن اینکه پیش‌بینی تو درست بوده یا نه، امتیاز کسب‌شده و رتبه‌ی "
        "فعلیت، روی دکمه‌ی زیر بزن 👇"
    )


def build_profile_text(uid: int, u: dict) -> str:
    rank_line = ""
    lb = cloud.get_weekly_leaderboard(top=1000)
    for row in lb:
        if not row.get("is_fake") and row["uid"] == str(uid):
            rank_line = f"📍 رتبه‌ات در تورنومنت این هفته: <b>#{row['rank']}</b>\n"
            break
    acc = f"{u.get('correct', 0)}/{u.get('predictions', 0)}" if u.get("predictions") else "—"
    full = u.get("full_name", "").strip() or u.get("username", "") or "—"
    return (
        "👤 <b>پروفایل شما</b>\n\n"
        f"📛 نام: <b>{full}</b>\n"
        f"🥇 امتیاز این هفته: <b>{u.get('points', 0)}</b>\n"
        f"⭐️ امتیاز کل (تاریخچه): <b>{u.get('total_points', 0)}</b>\n"
        f"{rank_line}"
        f"🎯 پیش‌بینی درست: <b>{acc}</b>\n"
        f"🎁 تعداد دعوت موفق: <b>{u.get('invites', 0)}</b>"
    )


def build_leaderboard_text(uid: int = 0) -> str:
    lb = cloud.get_weekly_leaderboard(top=TOP_REPORT_COUNT)
    reset_dt = next_weekly_reset_dt(now_ir())
    remaining = (reset_dt - now_ir()).total_seconds()
    countdown_line = f"⏰ زمان باقی‌مانده تا پایان تورنومنت هفتگی: <b>{_fmt_timedelta_long(remaining)}</b>"
    
    # ── NEW: شمارش کل شرکت‌کنندگان فعال این هفته ──
    total_participants = cloud.get_active_participants_count()

    if not lb:
        return (
            "🏆 <b>لیدربرد این هفته</b>\n"
            "هنوز کسی امتیازی کسب نکرده — اولین نفر باش! 🔥"
            f"\n\n{countdown_line}"
        )

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}

    # ── ساخت خطوط رتبه‌بندی (داخل blockquote قرار می‌گیرن) ──
    rank_lines = []
    for row in lb:
        tag = medals.get(row["rank"], f"{row['rank']}.")
        name = row["full_name"]
        marker = "🔹 " if uid and row["uid"] == str(uid) else ""
        rank_lines.append(f"{tag} {marker}{name} — <b>{row['points']}</b> امتیاز")

    # ── NEW: خط نمایش تعداد کل شرکت‌کنندگان ──
    participants_line = f"👥 <b>تعداد کل شرکت‌کنندگان این هفته:</b> <b>{total_participants}</b> نفر"

    # ── ترکیب نهایی ──
    text = (
        "🏆 <b> #30 لیدربرد برتر تورنومنت هفته جاری :</b>\n\n"
        f"<blockquote expandable>{chr(10).join(rank_lines)}</blockquote>\n\n"
        f"{participants_line}\n\n"
        f"{countdown_line}\n\n"
        "🏆 <b>جوایز نقدی پایان هفته:</b>\n"
        "🥇 نفر اول: <b>۸۰$</b> | 🥈 دوم: <b>۶۰$</b> | 🥉 سوم: <b>۴۰$</b>\n"
        "🎲 نفرات ۴ تا ۱۰: قرعه‌کشی <b>۳۰ دلاری</b> بین ۱ الی ۲ نفر"
    )
    return text


def build_referral_text(uid: int, bot_username: str) -> str:
    u = cloud.ensure_user(uid)
    link = f"https://t.me/{bot_username}?start=ref_{u['ref']}"
    total_bonus = REF_BONUS_STEP1 + REF_BONUS_STEP2 + REF_BONUS_STEP3
    return (
        "🎁 <b>دعوت دوستان و کسب امتیاز</b>\n\n"
        f"🔗 لینک اختصاصی شما:\n<code>{link}</code>\n\n"
        "<blockquote expandable>"
        f"📋 <b>سیستم پاداش تدریجی ({total_bonus} امتیاز به ازای هر دعوت):</b>\n"
        f"✅ مرحله ۱: ورود دوستت → <b>+{REF_BONUS_STEP1} امتیاز</b> (فوری)\n"
        f"✅ مرحله ۲: حداقل 2 بار شرکت دوستت در مسابقات → <b>+{REF_BONUS_STEP2} امتیاز</b>\n"
        f"✅ مرحله ۳: اولین پیش‌بینی درست دوستت → <b>+{REF_BONUS_STEP3} امتیاز</b>\n\n"
        "</blockquote>"
        f"🎉 دوستت هم با ورود از لینکت: <b>{REF_POINTS_INVITEE} امتیاز</b> هدیه می‌گیره\n\n"
        f"💡 <b>نکته مهم:</b> فقط <b>تریدرهای فعال بازار فارکس</b> را دعوت کن تا امتیاز کامل ({total_bonus} امتیاز) را دریافت کنی!\n\n"
        f"👥 تعداد دعوت‌های موفق تو: <b>{u.get('invites', 0)}</b>"
    )


def build_help_text(admin: bool) -> str:
    txt = (
        
        "📖 <b>بخش اول: راهنمای مسابقات</b>\n\n"
        "<blockquote expandable>"
        "🥇 <b>تورنومنت هفتگی پیش‌بینی انس جهانی طلا (XAU/USD)</b>\n\n"
        
        "⏰ <b>زمان‌بندی برگزاری دور ها:</b>\n"
        "🔸 <b>دور اول:</b> هر روز ساعت <b>۱۲:۰۰ ظهر</b>\n"
        "🔸 <b>دور دوم:</b> هر روز ساعت <b>۱۸:۰۰ عصر</b>\n"
        "🔸 هر دور <b>۶۰ دقیقه</b> طول می‌کشد تا تمام شود و نتیجه مشخص شود\n"
        "🔸 فقط در <b>۱۰ دقیقه‌ی اول</b> فرصت ثبت پیشبینی داری!! ⏳\n\n"
        
        "🎯 <b>نحوه شرکت:</b>\n"
        "۱. در ساعات مشخص شده ربات یک پیشبینی زنده در کانال منتشر میکند\n"
        "۲. با دکمه <b>⬇️ صعودی</b> یا <b>⬇️ نزولی</b> روند یک ساعتآینده طلا رو حدس بزن\n"
        "۳. پیش‌بینی درست = <b>+200 امتیاز</b> 💰\n\n"
        
        "🏆 <b>جوایز نقدی هر هفته:</b>\n"
        "🥇 نفر اول: <b>۸۰ دلار</b>\n"
        "🥈 نفر دوم: <b>۶۰ دلار</b>\n"
        "🥉 نفر سوم: <b>۴۰ دلار</b>\n"
        "🎲 نفرات ۴ تا ۱۰: <b>قرعه‌کشی ۳۰ دلاری</b> بین ۱ الی ۲ نفر\n\n"
        "</blockquote>"
        "⚖️ <b>بخش دوم: قوانین و مقررات</b>\n"
        "<blockquote expandable>"
        "🎁 <b>سیستم پاداش دعوت (۳ مرحله‌ای):</b>\n"
        "برای دریافت امتیاز کامل از دعوت دوستان فارکسیتون، مراحل زیر باید طی شود:\n\n"
        "✅ <b>مرحله ۱:</b> ورود دوستت → <b>+۲۰ امتیاز</b> (فوری)\n"
        "✅ <b>مرحله ۲:</b> شرکت دوستت در ۲ دور مسابقه → <b>+۳۰ امتیاز</b>\n"
        "✅ <b>مرحله ۳:</b> اولین پیش‌بینی درست دوستت → <b>+۴۰ امتیاز</b>\n\n"
        
        "💡 <b>نکته مهم:</b>\n"
        "فقط <b>تریدرها و تحلیلگران فعال بازار فارکس</b> را دعوت کن! 📊\n"
        "دوستان عادی که علاقه‌ای به بازارهای مالی ندارند، در مسابقات شرکت نمی‌کنند و امتیاز کامل به تو تعلق نمی‌گیرد.\n\n"
        "این سیستم تماما چیده شده فقط افراد حقیقی که در بازار های مالی فعالیت میکنن رو به عنوان شرکت در مسابقات دعوت نمایید و در کنارش امتیازی هم به عنوان پاداش دریافت کنید توجه کنید که اگر غیر از این مثل دور های قبل سراغ افراد و یا تیم هایی برید که فقط با طی کردن و دور زدن این مراحل بدنبال امتیاز باشید سیستم ضد تخلف ربات دیر یا زود تشخیص خواهد داد !  \n\n"
        
        "🛡 <b>سیستم ضدتقلب هوشمند:</b>\n"
        "ربات به‌صورت خودکار رفتار کاربران را رصد می‌کند و موارد زیر را <b>تقلب</b> محسوب می‌کند:\n"
        "⛔ دعوت اکانت‌های فیک یا ربات\n"
        "⛔ استفاده از اکانت‌های متعدد با IP مشترک\n"
        "⛔ دعوت کاربرانی که در هیچ مسابقه‌ای شرکت نمی‌کنند\n"
        "⛔ الگوی نام‌گذاری مشابه در اکانت‌های دعوت‌شده\n"
        "⛔ استفاده از تیم های زیر مجموعه گیری که فقط یکبار برای ثبت رفرال و کسب امتیاز اقدام میکنند\n"
        "⛔ فعال نبودن رفرال ها در چنل و لفت دادن پس از کسب امتیاز یا بعد از اتمام تورنومنت\n"
        "⛔ فعالیت همزمان زیرمجموعه‌ها در ساعات غیرمعمول\n\n"
        
        "⚠️ <b>جرایم متخلفان:</b>\n"
        "🔸 <b>تخلف برای بار اول:</b> امتیازهای کسب‌شده از روش غیرمجاز <b>حذف</b> می‌شود\n"
        "🔸 <b>تخلف برای بار دوم:</b> کاربر برای همیشه از تورنومنت <b>حذف</b> می‌شود 🚫\n\n"
        
        "📌 <b>نکات پایانی:</b>\n"
        "🔹 هر کاربر در هر دور فقط <b>یک رأی</b> می‌تواند بدهد\n"
        "🔹 رأی اول <b>نهایی</b> است و قابل تغییر نیست\n"
        "🔹 نتیجه هر دور <b>پیوی ربات</b> پس از اتمام به شما اطلاع داده می‌شود\n"
        "🔹 لیدربرد هر <b>شنبه ساعت ۰۰:۰۰</b> ریست می‌شود\n"
        "🔹 برندگان باید ظرف <b>۴۸ ساعت</b> برای دریافت جایزه اقدام کنند\n\n"
        "</blockquote>"
        "💎 <b>فرصت طلایی:</b>\n"
        "هرچه بیشتر در مسابقات شرکت کنی و حدست درست باشه و دوستان واقعی فارکسی دعوت کنی به مسابقه، شانس برنده شدنت در جوایز هفتگی بیشتر می‌شود! 🔥\n\n"
        
        "🎯 <b>هدف ما:</b>\n"
        "ایجاد یک جامعه‌ی حرفه‌ای از تریدرها که با دانش و تحلیل واقعی و حدس مسابقات ، جوایز نقدی کسب کنند ودر کنار لایو ترید ها به سود دهی مستمر برسند.\n"
    )
    
    if admin:
        txt += (
            "\n═══════════════════════════════\n"
            "👑 <b>دستورات ادمین</b>\n"
            "═══════════════════════════════\n\n"
            "/newround [دقیقه کل] [دقیقه رأی‌گیری]\n"
            f"   (پیش‌فرض: {DEFAULT_ROUND_MIN} دقیقه کل، {VOTE_WINDOW_MINUTES} دقیقه رأی‌گیری)\n\n"
            "/cancelround — باطل کردن دور فعال\n"
            "/status — وضعیت دور فعال\n"
            "/adminpanel — پنل مدیریت کاربران\n\n"
            "🤖 <b>سیستم‌های خودکار:</b>\n"
            "• رشد تدریجی فیک‌ها (قابل تنظیم در پنل)\n"
            "• امتیاز خودکار بعد از هر دور (قابل تنظیم در پنل)\n"
        )
    
    return txt


# ══════════════════════════════════════════════════════════════════════
# ROUND HELPERS
# ══════════════════════════════════════════════════════════════════════
def _vote_counts(rnd: dict) -> tuple:
    up = sum(1 for v in rnd["predictions"].values() if v["dir"] == "up")
    down = len(rnd["predictions"]) - up
    return up, down


async def _edit_round_message(app: Application, rnd: dict, phase: str,
                               end_price=None, direction=None):
    text = build_round_text(rnd, phase, end_price, direction)
    markup = kb_round_voting(rnd["id"]) if phase == "voting" else kb_round_teaser()
    
    # جلوگیری از ویرایش اگر متن تغییر نکرده
    last_text = rnd.get("_last_edit_text", "")
    if text == last_text:
        return
    
    try:
        await app.bot.edit_message_text(
            chat_id=rnd["chat_id"], message_id=rnd["message_id"],
            text=text, parse_mode=ParseMode.HTML, reply_markup=markup,
        )
        # ذخیره آخرین متن ویرایش شده
        rnd["_last_edit_text"] = text
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            log.warning(f"edit round message failed: {e}")
    except Exception as e:
        log.warning(f"edit round message failed: {e}")


async def _send_to_admins(app: Application, text: str):
    for admin_id in ADMIN_IDS:
        try:
            await app.bot.send_message(chat_id=admin_id, text=text, parse_mode=ParseMode.HTML)
        except Forbidden:
            log.warning(f"admin {admin_id} has not started the bot yet")
        except Exception as e:
            log.warning(f"send to admin {admin_id} failed: {e}")


async def _notify_participants(app: Application, rnd: dict, price_now: float,
                                direction: str, winners: set):
    start_p = _fmt_price(rnd["start_price"])
    end_p = _fmt_price(price_now)
    for uid_str, pred in rnd["predictions"].items():
        try:
            uid = int(uid_str)
        except ValueError:
            continue
        choice_fa = "بالا ⬆️" if pred["dir"] == "up" else "پایین ⬇️"
        u = cloud.get_user(uid) or {}
        try:
            if direction == "flat":
                text = (
                    f"➖ <b>دور #{rnd['id']} باطل شد</b>\n\n"
                    f"قیمت شروع و پایان این دور برابر بود (<code>{start_p}</code>).\n"
                    f"🎯 پیش‌بینی تو: <b>{choice_fa}</b>"
                )
            elif uid_str in winners:
                text = (
                    f"🎉 <b>تبریک! توی دور #{rnd['id']} برنده شدی</b>\n\n"
                    f"💰 قیمت شروع دور: <code>{start_p}</code>\n"
                    f"🎯 پیش‌بینی تو: <b>{choice_fa}</b>\n"
                    f"💰 قیمت پایان دور: <code>{end_p}</code>\n"
                    "✅ پیش‌بینی‌ات کاملاً درست بود!\n"
                    f"⭐️ <b>+{POINTS_PER_CORRECT} امتیاز</b> به حسابت اضافه شد.\n"
                    f"🥇 امتیاز این هفته‌ات الان: <b>{u.get('points', 0)}</b>"
                )
            else:
                text = (
                    f"😔 <b>دور #{rnd['id']} این‌بار به نفعت تموم نشد</b>\n\n"
                    f"💰 قیمت شروع دور: <code>{start_p}</code>\n"
                    f"🎯 پیش‌بینی تو: <b>{choice_fa}</b>\n"
                    f"💰 قیمت پایان دور: <code>{end_p}</code>\n"
                    "❌ این‌بار درست از آب در نیومد — توی دور بعدی دوباره شانستو امتحان کن!\n"
                    f"🥇 امتیاز این هفته‌ات الان: <b>{u.get('points', 0)}</b>"
                )
            await app.bot.send_message(chat_id=uid, text=text, parse_mode=ParseMode.HTML)
            await asyncio.sleep(NOTIFY_SEND_DELAY)
        except Forbidden:
            pass
        except Exception as e:
            log.warning(f"notify participant {uid_str} failed: {e}")


async def _resolve_round(app: Application, rnd: dict):
    price_now = get_gold_price(force=True)
    if price_now is None:
        cloud.postpone_round(rnd["id"], minutes=1)
        log.warning(f"round #{rnd['id']}: price unavailable, postponed 1 min")
        return

    start_price = rnd["start_price"]
    if price_now > start_price:
        direction = "up"
    elif price_now < start_price:
        direction = "down"
    else:
        direction = "flat"

    winners = []
    for uid_str, pred in rnd["predictions"].items():
        cloud.inc_prediction_count(int(uid_str))
        if direction != "flat" and pred["dir"] == direction:
            winners.append(uid_str)
            cloud.award_correct_prediction(int(uid_str))
            
            # بررسی اینکه آیا اولین برد کاربر است و دعوت‌شده است
            u = cloud.get_user(int(uid_str))
            if u and u.get("correct", 0) == 1:  # چون刚刚 increment شده
                ref_uid = cloud.check_referral_steps(int(uid_str), "step3")
                if ref_uid:
                    try:
                        await app.bot.send_message(
                            chat_id=ref_uid, parse_mode=ParseMode.HTML,
                            text=(
                                f"🏆 <b>مرحله ۳ تکمیل شد!</b>\n\n"
                                f"دوستت اولین پیش‌بینی درستش رو داشت.\n"
                                f"⭐️ <b>+{REF_BONUS_STEP3} امتیاز</b> به حسابت اضافه شد.\n\n"
                                f"✅ پاداش کامل دعوت تکمیل شد!"
                            )
                        )
                    except Exception:
                        pass
    winners_set = set(winners)

    cloud.finish_round(rnd["id"], price_now, direction, winners)
    await _edit_round_message(app, rnd, "resolved", price_now, direction)
    await _notify_participants(app, rnd, price_now, direction, winners_set)

    up, down = _vote_counts(rnd)
    winners_list = ", ".join(
        (rnd["predictions"][w].get("username") or f"#{w}") for w in winners[:20]
    ) or "—"
    more = f" و {len(winners) - 20} نفر دیگر" if len(winners) > 20 else ""
    await _send_to_admins(app, (
        f"✅ <b>دور #{rnd['id']} به پایان رسید</b>\n"
        f"شروع: <code>{_fmt_price(start_price)}</code> → پایان: <code>{_fmt_price(price_now)}</code>\n"
        f"جهت: <b>{direction}</b>\n"
        f"شرکت‌کننده: {up + down} (⬆️{up} / ⬇️{down})\n"
        f"برندگان ({len(winners)}): {winners_list}{more}"
    ))
    
    # 🆕 سیستم امتیاز خودکار به فیک‌ها
    # فقط وقتی direction "up" یا "down" است (یعنی برندگان واقعی وجود دارند)
    # اگر direction "flat" باشد (دور باطل)، فیک‌ها هم امتیاز نمی‌گیرند
    if auto_score_enabled and direction != "flat":
        settings = cloud.get_auto_score_settings()
        base_points = settings.get("base_points", 200)
        awarded = cloud.award_auto_score_to_fakes(base_points)
        
        if awarded:
            awarded_list = "\n".join([
                f"• {a['name']}: <b>+{a['points']}</b> ({a['breakdown']})"
                for a in awarded
            ])
            await _send_to_admins(app, (
                f"🏆 <b>امتیاز خودکار به فیک‌ها</b>\n\n"
                f"🤖 تعداد فیک‌های به‌روزشده: <b>{len(awarded)}</b>\n\n"
                f"📊 <b>جزئیات:</b>\n{awarded_list}"
            ))
            log.info(f"🏆 Auto score: +{base_points} به {len(awarded)} فیک")
    elif auto_score_enabled and direction == "flat":
        await _send_to_admins(app, (
            f"⏭️ <b>امتیاز خودکار به فیک‌ها اجرا نشد</b>\n\n"
            f"دلیل: دور باطل شد (قیمت تغییر نکرد)\n"
            f"هیچ کاربری امتیاز نگرفت، پس فیک‌ها هم امتیاز نمی‌گیرند."
        ))
        log.info("⏭️ Auto score: skipped (flat round)")
    
    cloud.save(force=True)


async def broadcast_message(app: Application, text: str, admin_uid: int):
    """ارسال پیام همگانی به همه کاربران در پس‌زمینه"""
    users = cloud.get_all_users()
    sent = 0
    failed = 0
    blocked = 0
    
    await app.bot.send_message(
        chat_id=admin_uid,
        text=f"📢 <b>شروع ارسال همگانی...</b>\n\n"
             f"👥 تعداد کل کاربران: <b>{len(users)}</b>\n"
             f"⏳ لطفاً صبر کنید...",
        parse_mode=ParseMode.HTML,
    )
    
    for u in users:
        uid_str = u.get("uid_str") or str(u.get("uid"))
        if not uid_str.isdigit():
            continue
        
        try:
            await app.bot.send_message(
                chat_id=int(uid_str),
                text=text,
                parse_mode=ParseMode.HTML,
            )
            sent += 1
            await asyncio.sleep(NOTIFY_SEND_DELAY)
        except Forbidden:
            blocked += 1  # کاربر ربات را بلاک کرده
        except BadRequest:
            failed += 1  # پیام نامعتبر بود
        except Exception:
            failed += 1
    
    # ارسال گزارش نهایی به ادمین
    await app.bot.send_message(
        chat_id=admin_uid,
        text=f"✅ <b>ارسال همگانی به پایان رسید!</b>\n\n"
             f"📊 <b>آمار ارسال:</b>\n"
             f"✅ ارسال موفق: <b>{sent}</b>\n"
             f"⛔️ بلاک‌کرده: <b>{blocked}</b>\n"
             f"❌ خطا: <b>{failed}</b>\n\n"
             f"🎯 <b>نرخ موفقیت:</b> {round(sent / len(users) * 100, 1)}%",
        parse_mode=ParseMode.HTML,
    )

# ══════════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════

async def _require_membership(update: Update, bot) -> bool:
    """بررسی عضویت؛ اگر عضو نبود پیام می‌فرستد و False برمی‌گرداند."""
    uid = update.effective_user.id
    if await check_membership(bot, uid):
        return True
    await update.effective_message.reply_text(
        build_join_required_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_join_required(),
    )
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
        if arg == "profile":
            # چک عضویت برای دیپ‌لینک پروفایل
            if not await check_membership(ctx.bot, uid):
                # همیشه در PV کاربر ارسال شود (نه در کانال)
                await ctx.bot.send_message(
                    chat_id=uid,
                    text=build_join_required_text(),
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb_join_required(),
                )
                return
            u = cloud.ensure_user(uid, username, full_name)
            await update.effective_message.reply_text(
                build_profile_text(uid, u), parse_mode=ParseMode.HTML, reply_markup=kb_back())
            return
        if is_new and arg.startswith("ref_"):
            ref_code = arg[4:]
            
            # 🛡 چک عضویت اجباری قبل از ثبت رفرال
            if not await check_membership(ctx.bot, uid):
                # ذخیره کد رفرال برای پردازش بعد از عضویت
                ctx.user_data["pending_referral"] = ref_code
                # همیشه در PV کاربر ارسال شود (نه در کانال)
                await ctx.bot.send_message(
                    chat_id=uid,
                    text=build_referral_join_text(),
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb_join_required(),
                )
                return
            
            ref_uid = cloud.find_uid_by_ref(ref_code)
            if ref_uid:
                ok = cloud.apply_referral(uid, ref_uid)
                if ok:
                    # دریافت اطلاعات کاربران
                    new_user = cloud.get_user(uid)
                    referrer_user = cloud.get_user(ref_uid)
                    new_user_name = new_user.get("full_name", "") or new_user.get("username", "") or f"کاربر {uid}"
                    referrer_name = referrer_user.get("full_name", "") or referrer_user.get("username", "") or f"کاربر {ref_uid}"
                    
                    # پیام به دعوت‌کننده
                    try:
                        await ctx.bot.send_message(
                            chat_id=ref_uid, parse_mode=ParseMode.HTML,
                            text=(
                                f"🎉 <b>یک دعوت موفق ثبت شد!</b>\n\n"
                                f"👤 کاربر جدید: <b>{new_user_name}</b>\n"
                                f"⭐️ <b>+{REF_BONUS_STEP1} امتیاز</b> (مرحله ۱ از ۳) به حسابت اضافه شد.\n\n"
                                f"📋 <b>سیستم پاداش تدریجی:</b>\n"
                                f"✅ مرحله ۱: ورود دوستت → <b>{REF_BONUS_STEP1} امتیاز</b> (دریافت شد)\n"
                                f"⏳ مرحله ۲: شرکت دوستت در ۲ دور مسابقه → <b>{REF_BONUS_STEP2} امتیاز</b>\n"
                                f"⏳ مرحله ۳: اولین پیش‌بینی درست دوستت → <b>{REF_BONUS_STEP3} امتیاز</b>\n\n"
                                f"💡 <b>نکته مهم:</b>\n"
                                f"برای دریافت امتیاز کامل، دوستت باید به‌عنوان یک شخص حقیقی و فعال در "
                                f"حوزه بازارهای مالی در مسابقات شرکت کند و پیش‌بینی‌های واقعی داشته باشد.\n"
                                f"⚠️ امتیازهای کسب‌شده از روش‌های غیرمجاز (ممبر فیک، ربات و...) در پایان "
                                f"هفته توسط سیستم ضدتقلب شناسایی و کسر خواهند شد."
                            )
                        )
                    except Exception:
                        pass
                    
                    # پیام به دعوت‌شونده
                    try:
                        await ctx.bot.send_message(
                            chat_id=uid, parse_mode=ParseMode.HTML,
                            text=("🎁 <b>خوش اومدی!</b> چون از طریق لینک دعوت یک دوست وارد شدی، "
                                  f"⭐️ +{REF_POINTS_INVITEE} امتیاز رایگان بهت تعلق گرفت."))
                    except Exception:
                        pass
                    
                    # پیام به ادمین‌ها
                    ref_uname = referrer_user.get("username", "") or ""
                    new_uname = new_user.get("username", "") or ""
                    
                    # ساخت mention قابل کلیک برای هر دو کاربر (۱۰۰٪ کار می‌کند)
                    ref_mention = mention_html(ref_uid, referrer_name, ref_uname)
                    new_mention = mention_html(uid, new_user_name, new_uname)
                    
                    for admin_id in ADMIN_IDS:
                        try:
                            await ctx.bot.send_message(
                                chat_id=admin_id, parse_mode=ParseMode.HTML,
                                text=(
                                    f"🆕 <b>دعوت موفق جدید</b>\n\n"
                                    f"👤 <b>دعوت‌کننده:</b> {ref_mention}\n"
                                    f"🆔 آیدی: <code>{ref_uid}</code>\n"
                                    f"📛 یوزرنیم: {'@' + ref_uname if ref_uname else '—'}\n\n"
                                    f"👤 <b>دعوت‌شده:</b> {new_mention}\n"
                                    f"🆔 آیدی: <code>{uid}</code>\n"
                                    f"📛 یوزرنیم: {'@' + new_uname if new_uname else '—'}\n\n"
                                    f"📅 زمان: {now_ir().strftime('%H:%M:%S')}"
                                )
                            )
                        except Exception:
                            pass

    await update.effective_message.reply_text(
        build_welcome_text(), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
    cloud.save()


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _require_membership(update, ctx.bot):
        return
    await update.effective_message.reply_text(
        build_help_text(is_admin(update.effective_user.id)),
        parse_mode=ParseMode.HTML, reply_markup=kb_back())


async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _require_membership(update, ctx.bot):
        return
    user = update.effective_user
    uid = user.id
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    u = cloud.ensure_user(uid, user.username or "", full_name)
    await update.effective_message.reply_text(
        build_profile_text(uid, u), parse_mode=ParseMode.HTML, reply_markup=kb_back())


async def cmd_leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _require_membership(update, ctx.bot):
        return
    uid = update.effective_user.id
    await update.effective_message.reply_text(
        build_leaderboard_text(uid), parse_mode=ParseMode.HTML, reply_markup=kb_back())


async def cmd_invite(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _require_membership(update, ctx.bot):
        return
    bot_username = (await ctx.bot.get_me()).username
    await update.effective_message.reply_text(
        build_referral_text(update.effective_user.id, bot_username),
        parse_mode=ParseMode.HTML, reply_markup=kb_back())


async def cmd_adminpanel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.effective_message.reply_text(
        build_admin_panel_text(), parse_mode=ParseMode.HTML, reply_markup=kb_admin_main())


# ---------- admin round commands ----------
async def cmd_newround(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    if not CHANNEL_ID:
        await update.effective_message.reply_text("⚠️ CHANNEL_ID تنظیم نشده.")
        return
    if cloud.get_active_round():
        await update.effective_message.reply_text(
            "⚠️ یک دور در حال اجراست. اول با /status وضعیتش رو ببین یا با /cancelround باطلش کن.")
        return

    minutes = DEFAULT_ROUND_MIN
    vote_minutes = VOTE_WINDOW_MINUTES
    if ctx.args:
        try:
            minutes = max(1, int(ctx.args[0]))
            if len(ctx.args) > 1:
                vote_minutes = max(1, int(ctx.args[1]))
        except ValueError:
            await update.effective_message.reply_text(
                "⚠️ فرمت درست: /newround 60  یا  /newround 60 30")
            return

    price = get_gold_price(force=True)
    if price is None:
        await update.effective_message.reply_text("❌ قیمت طلا در دسترس نیست، دوباره تلاش کن.")
        return

    # ارسال پیام اولیه با محتوای کامل (نه placeholder)
    rnd = cloud.create_round(price, minutes, CHANNEL_ID, 0, vote_minutes)
    text = build_round_text(rnd, "voting")
    markup = kb_round_voting(rnd["id"])
    
    try:
        msg = await ctx.bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
        # به‌روزرسانی message_id در دیتابیس
        with cloud._lock:
            cloud._data["active_round"]["message_id"] = msg.message_id
            cloud._data["active_round"]["_last_edit_text"] = text
            cloud._dirty = True
        
        await update.effective_message.reply_text(
            f"✅ دور #{rnd['id']} با قیمت شروع {_fmt_price(price)} برای {minutes} دقیقه در کانال باز شد "
            f"(بازه‌ی رأی‌گیری: {rnd['vote_minutes']} دقیقه‌ی اول).")
        cloud.save(force=True)
    except Exception as e:
        cloud.cancel_active_round()
        await update.effective_message.reply_text(f"❌ خطا در ارسال پیام به کانال: {e}")


async def cmd_cancelround(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    rnd = cloud.cancel_active_round()
    if not rnd:
        await update.effective_message.reply_text("دور فعالی وجود نداره.")
        return
    try:
        await ctx.bot.edit_message_text(
            chat_id=rnd["chat_id"], message_id=rnd["message_id"],
            text=f"🚫 دور #{rnd['id']} توسط ادمین باطل شد.", parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await update.effective_message.reply_text(f"دور #{rnd['id']} باطل شد.")
    cloud.save(force=True)


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    rnd = cloud.get_active_round()
    if not rnd:
        await update.effective_message.reply_text("در حال حاضر دور فعالی وجود نداره.")
        return
    up, down = _vote_counts(rnd)
    vote_deadline_dt = datetime.fromisoformat(rnd["vote_deadline"])
    lock_dt = datetime.fromisoformat(rnd["lock_time"])
    vote_state = "بسته" if rnd.get("vote_closed") or now_ir() >= vote_deadline_dt else "باز"
    await update.effective_message.reply_text(
        f"دور #{rnd['id']} فعال است.\n"
        f"شروع: {_fmt_price(rnd['start_price'])}\n"
        f"وضعیت رأی‌گیری: {vote_state} (بسته‌شدن: {vote_deadline_dt.strftime('%H:%M:%S')})\n"
        f"بسته‌شدن نهایی: {lock_dt.strftime('%H:%M:%S')}\n"
        f"رأی‌ها: ⬆️ {up} | ⬇️ {down}")


# ══════════════════════════════════════════════════════════════════════
# CALLBACK QUERY HANDLER
# ══════════════════════════════════════════════════════════════════════

# برای مکالمه‌ی چند مرحله‌ای افزودن فیک / تنظیم امتیاز از طریق callback
# وضعیت موقت در ctx.user_data ذخیره می‌شود
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    uid = q.from_user.id
    user = q.from_user
    username = user.username or ""
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    cloud.ensure_user(uid, username, full_name)

    # ── بررسی عضویت برای همه‌ی callbackها به جز check_join ──────────
    if data != "check_join":
        if not await check_membership(q.get_bot(), uid):
            await q.answer()
            # اگر callback از کانال آمده، پیام را در PV ارسال کن (نه در کانال!)
            try:
                if q.message.chat.type == "channel":
                    await q.get_bot().send_message(
                        chat_id=uid,
                        text=build_join_required_text(),
                        parse_mode=ParseMode.HTML,
                        reply_markup=kb_join_required(),
                    )
                else:
                    await q.edit_message_text(
                        build_join_required_text(),
                        parse_mode=ParseMode.HTML,
                        reply_markup=kb_join_required(),
                    )
            except Exception as e:
                log.warning(f"ارسال پیام عضویت اجباری با خطا مواجه شد: {e}")
            return

    # ── check_join (کاربر ادعا می‌کند عضو شده) ──────────────────────
    if data == "check_join":
        if await check_membership(q.get_bot(), uid):
            await q.answer("✅ عضویت تأیید شد!")
            
            # بررسی اینکه آیا کاربر از طریق لینک دعوت وارد شده بود و منتظر تایید عضویت بود؟
            pending_ref = ctx.user_data.get("pending_referral")
            if pending_ref:
                ref_uid = cloud.find_uid_by_ref(pending_ref)
                if ref_uid:
                    ok = cloud.apply_referral(uid, ref_uid)
                    if ok:
                        # دریافت اطلاعات کاربران
                        new_user = cloud.get_user(uid)
                        referrer_user = cloud.get_user(ref_uid)
                        new_user_name = new_user.get("full_name", "") or new_user.get("username", "") or f"کاربر {uid}"
                        referrer_name = referrer_user.get("full_name", "") or referrer_user.get("username", "") or f"کاربر {ref_uid}"
                        
                        # پیام به دعوت‌کننده (مرحله ۱)
                        try:
                            await ctx.bot.send_message(
                                chat_id=ref_uid, parse_mode=ParseMode.HTML,
                                text=(
                                    f"🎉 <b>یک دعوت موفق ثبت شد!</b>\n\n"
                                    f"👤 کاربر جدید: <b>{new_user_name}</b>\n"
                                    f"⭐️ <b>+{REF_BONUS_STEP1} امتیاز</b> (مرحله ۱ از ۳) به حسابت اضافه شد.\n\n"
                                    f"📋 <b>سیستم پاداش تدریجی:</b>\n"
                                    f"✅ مرحله ۱: ورود دوستت → <b>{REF_BONUS_STEP1} امتیاز</b> (دریافت شد)\n"
                                    f"⏳ مرحله ۲: شرکت دوستت در ۲ دور مسابقه → <b>{REF_BONUS_STEP2} امتیاز</b>\n"
                                    f"⏳ مرحله ۳: اولین پیش‌بینی درست دوستت → <b>{REF_BONUS_STEP3} امتیاز</b>\n\n"
                                    f"💡 <b>نکته مهم:</b>\n"
                                    f"برای دریافت امتیاز کامل، دوستت باید به‌عنوان یک شخص حقیقی و فعال در "
                                    f"حوزه بازارهای مالی در مسابقات شرکت کند و پیش‌بینی‌های واقعی داشته باشد.\n"
                                    f"⚠️ امتیازهای کسب‌شده از روش‌های غیرمجاز (ممبر فیک، ربات و...) در پایان "
                                    f"هفته توسط سیستم ضدتقلب شناسایی و کسر خواهند شد."
                                )
                            )
                        except Exception:
                            pass
                        
                        # پیام به دعوت‌شونده
                        try:
                            await ctx.bot.send_message(
                                chat_id=uid, parse_mode=ParseMode.HTML,
                                text=("🎁 <b>خوش اومدی!</b> چون از طریق لینک دعوت یک دوست وارد شدی، "
                                      f"⭐️ +{REF_POINTS_INVITEE} امتیاز رایگان بهت تعلق گرفت."))
                        except Exception:
                            pass
                        
                        # پیام به ادمین‌ها
                        ref_uname = referrer_user.get("username", "") or ""
                        new_uname = new_user.get("username", "") or ""
                        
                        # ساخت mention قابل کلیک برای هر دو کاربر (۱۰۰٪ کار می‌کند)
                        ref_mention = mention_html(ref_uid, referrer_name, ref_uname)
                        new_mention = mention_html(uid, new_user_name, new_uname)
                        
                        for admin_id in ADMIN_IDS:
                            try:
                                await ctx.bot.send_message(
                                    chat_id=admin_id, parse_mode=ParseMode.HTML,
                                    text=(
                                        f"🆕 <b>دعوت موفق جدید</b>\n\n"
                                        f"👤 <b>دعوت‌کننده:</b> {ref_mention}\n"
                                        f"🆔 آیدی: <code>{ref_uid}</code>\n"
                                        f"📛 یوزرنیم: {'@' + ref_uname if ref_uname else '—'}\n\n"
                                        f"👤 <b>دعوت‌شده:</b> {new_mention}\n"
                                        f"🆔 آیدی: <code>{uid}</code>\n"
                                        f"📛 یوزرنیم: {'@' + new_uname if new_uname else '—'}\n\n"
                                        f"📅 زمان: {now_ir().strftime('%H:%M:%S')}"
                                    )
                                )
                            except Exception:
                                pass
                
                # پاک کردن state موقت
                ctx.user_data.pop("pending_referral", None)
                cloud.save(force=True)
            
            await q.edit_message_text(
                build_welcome_text(), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        else:
            await q.answer("⛔️ هنوز عضو کانال نشدی!", show_alert=True)
        return

    # ── رأی‌گیری ────────────────────────────────────────────────────
    if data.startswith("vote:"):
        try:
            _, rid_s, direction = data.split(":")
            rid = int(rid_s)
        except Exception:
            await q.answer(); return

        result = cloud.record_vote(rid, uid, direction, username)
        if result == "ok":
            dir_fa = "بالا ⬆️" if direction == "up" else "پایین ⬇️"
            await q.answer(f"✅ پیش‌بینی‌ات ثبت شد: {dir_fa}\nرأی اول و نهایی‌ست.", show_alert=True)
            
            # بررسی اینکه آیا کاربر قبلاً در ۱ دور شرکت کرده و الان در دور دوم است
            u = cloud.get_user(uid)
            if u and u.get("predictions", 0) == 1:  # یعنی یک دور را کامل شرکت کرده
                ref_uid = cloud.check_referral_steps(uid, "step2")
                if ref_uid:
                    try:
                        await ctx.bot.send_message(
                            chat_id=ref_uid, parse_mode=ParseMode.HTML,
                            text=(
                                f"🎯 <b>مرحله ۲ تکمیل شد!</b>\n\n"
                                f"دوستت در دومین دور مسابقه شرکت کرد.\n"
                                f"⭐️ <b>+{REF_BONUS_STEP2} امتیاز</b> به حسابت اضافه شد."
                            )
                        )
                    except Exception:
                        pass
        elif result == "already_voted":
            await q.answer("⚠️ قبلاً رأی دادی — امکان تغییر نیست.", show_alert=True)
        elif result == "closed":
            await q.answer("⛔️ زمان ثبت رأی تمام شده.", show_alert=True)
        else:
            await q.answer("⛔️ این دور دیگه فعال نیست.", show_alert=True)
        return

    # ── منوی اصلی ────────────────────────────────────────────────────
    if data == "menu:profile":
        u = cloud.ensure_user(uid, username, full_name)
        await q.answer()
        await q.edit_message_text(build_profile_text(uid, u), parse_mode=ParseMode.HTML, reply_markup=kb_back())
        return
    if data == "menu:leaderboard":
        await q.answer()
        await q.edit_message_text(build_leaderboard_text(uid), parse_mode=ParseMode.HTML, reply_markup=kb_back())
        return
    if data == "menu:invite":
        bot_username = (await ctx.bot.get_me()).username
        await q.answer()
        await q.edit_message_text(build_referral_text(uid, bot_username), parse_mode=ParseMode.HTML, reply_markup=kb_back())
        return
    if data == "menu:help":
        await q.answer()
        await q.edit_message_text(build_help_text(is_admin(uid)), parse_mode=ParseMode.HTML, reply_markup=kb_back())
        return
    if data == "menu:home":
        await q.answer()
        await q.edit_message_text(build_welcome_text(), parse_mode=ParseMode.HTML, reply_markup=kb_main(uid))
        return

    # ── پنل ادمین (فقط ادمین‌ها) ─────────────────────────────────────
    if not is_admin(uid):
        await q.answer("⛔️ دسترسی ندارید.", show_alert=True)
        return

    if data.startswith("admin:panel:"):
        await q.answer()
        await q.edit_message_text(build_admin_panel_text(), parse_mode=ParseMode.HTML, reply_markup=kb_admin_main())
        return

    if data.startswith("admin:users:"):
        page = int(data.split(":")[-1])
        text, markup = build_users_page(page)
        await q.answer()
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return

    if data == "admin:fakes:list":
        text, markup = build_fakes_page()
        await q.answer()
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return

    # ── toggle رشد خودکار فیک‌ها ──────────────────────────────────
    if data == "admin:fake_auto:toggle":
        global fake_referral_enabled
        fake_referral_enabled = not fake_referral_enabled
        status = "🟢 فعال شد" if fake_referral_enabled else "🔴 غیرفعال شد"
        await q.answer(f"رشد خودکار فیک‌ها {status}", show_alert=True)
        await q.edit_message_text(
            build_admin_panel_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=kb_admin_main(),
        )
        return
    
    if data == "admin:fake_auto:list":
        text, markup = build_auto_fake_list_page()
        await q.answer()
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return
    
    if data.startswith("admin:fake_auto:toggle_id:"):
        fid = data[len("admin:fake_auto:toggle_id:"):]
        added = cloud.toggle_auto_fake_id(fid)
        cloud.save(force=True)
        status = "اضافه شد ✅" if added else "حذف شد ❌"
        await q.answer(status, show_alert=True)
        text, markup = build_auto_fake_list_page()
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return

    if data == "admin:fake_auto:boost_prompt":
        auto_ids = cloud.get_auto_fake_ids()
        if not auto_ids:
            await q.answer("⚠️ هنوز هیچ فیکی در لیست انتخاب نشده!", show_alert=True)
            return
        
        ctx.user_data["awaiting"] = "fake_boost_amount"
        await q.answer()
        await q.edit_message_text(
            f"🎯 <b>افزایش تصادفی امتیاز فیک‌ها</b>\n\n"
            f"🤖 تعداد فیک‌های انتخاب‌شده: <b>{len(auto_ids)}</b>\n\n"
            f"📊 <b>فرمت ورودی:</b>\n"
            f"<code>مقدار امتیاز تعداد فیک</code>\n\n"
            f"💡 <b>مثال‌ها:</b>\n"
            f"• <code>200 5</code> → ۲۰۰ امتیاز به ۵ فیک رندوم\n"
            f"• <code>100 3</code> → ۱۰۰ امتیاز به ۳ فیک رندوم\n"
            f"• <code>500 8</code> → ۵۰۰ امتیاز به ۸ فیک رندوم\n\n"
            f"❌ برای لغو <code>/cancel</code> بزنید.",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── سیستم امتیاز خودکار ────────────────────────────────────────
    if data == "admin:auto_score:toggle":
        global auto_score_enabled
        auto_score_enabled = not auto_score_enabled
        status = "🟢 فعال شد" if auto_score_enabled else "🔴 غیرفعال شد"
        await q.answer(f"امتیاز خودکار بعد از دور {status}", show_alert=True)
        await q.edit_message_text(
            build_admin_panel_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=kb_admin_main(),
        )
        return
    
    if data == "admin:auto_score:settings":
        settings = cloud.get_auto_score_settings()
        await q.answer()
        await q.edit_message_text(
            f"⚙️ <b>تنظیمات سیستم امتیاز خودکار</b>\n\n"
            f"📊 <b>تنظیمات فعلی:</b>\n"
            f"• حداقل درصد فیک‌ها: <b>{settings.get('min_percentage', 30)}%</b>\n"
            f"• حداکثر درصد فیک‌ها: <b>{settings.get('max_percentage', 70)}%</b>\n"
            f"• امتیاز پایه (پیش‌بینی درست): <b>{settings.get('base_points', 200)}</b>\n\n"
            f"💡 <b>نحوه کار:</b>\n"
            f"بعد از پایان هر دور، سیستم به صورت رندوم بین <b>{settings.get('min_percentage', 30)}%</b> "
            f"تا <b>{settings.get('max_percentage', 70)}%</b> از فیک‌های انتخاب‌شده را انتخاب می‌کند "
            f"و به هر کدام امتیاز پایه (<b>{settings.get('base_points', 200)}</b>) + ترکیبی رندوم از "
            f"امتیازهای رفرال (۲۰، ۳۰، ۴۰) اضافه می‌کند.\n\n"
            f"📝 <b>برای تغییر تنظیمات:</b>\n"
            f"به این شکل وارد کنید: <code>min max base</code>\n"
            f"مثال: <code>40 80 200</code>\n"
            f"(حداقل ۴۰٪، حداکثر ۸۰٪، امتیاز پایه ۲۰۰)\n\n"
            f"❌ برای لغو <code>/cancel</code> بزنید.",
            parse_mode=ParseMode.HTML,
        )
        ctx.user_data["awaiting"] = "auto_score_settings"
        return


    # ── تنظیم امتیاز کاربر واقعی ─────────────────────────────────────
    if data.startswith("admin:setpts:"):
        uid_str = data[len("admin:setpts:"):]
        ctx.user_data["setpts_uid"] = uid_str
        ctx.user_data["awaiting"] = "setpts"
        await q.answer()
        u = cloud.get_user(int(uid_str)) if uid_str.isdigit() else None
        name = u.get("full_name", "") or u.get("username", uid_str) if u else uid_str
        await q.edit_message_text(
            f"✏️ <b>تنظیم امتیاز برای: {name}</b>\n\n"
            f"امتیاز فعلی: <b>{u.get('points', 0) if u else '?'}</b>\n\n"
            "مقدار جدید امتیاز را تایپ کن (عدد صحیح).\n"
            "یا برای اضافه/کم‌کردن: <code>+50</code> یا <code>-20</code>\n"
            "برای لغو /cancel بزن.",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── افزودن/ویرایش کاربر فیک ─────────────────────────────────────
    if data == "admin:fake:add_prompt":
        ctx.user_data["awaiting"] = "fake_add_name"
        await q.answer()
        await q.edit_message_text(
            "🤖 <b>افزودن کاربر فیک</b>\n\n"
            "نام کامل کاربر فیک را بنویس:\n(برای لغو /cancel بزن)",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── ارسال همگانی ───────────────────────────────────────────────
    if data == "admin:broadcast:prompt":
        ctx.user_data["awaiting"] = "broadcast_text"
        await q.answer()
        await q.edit_message_text(
            "📢 <b>ارسال همگانی به همه کاربران</b>\n\n"
            "پیام خود را بنویسید (می‌توانید از فرمت HTML استفاده کنید):\n"
            "مثال: <code>&lt;b&gt;متن بولد&lt;/b&gt;</code>\n\n"
            "⚠️ این پیام به پی‌وی تمام کاربران ارسال می‌شود.\n"
            "برای لغو <code>/cancel</code> بزنید.",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── ارسال به کاربر خاص (مرحله ۱ - آیدی) ──────────────────────
    if data == "admin:senduser:uid_prompt":
        ctx.user_data["awaiting"] = "senduser_uid"
        await q.answer()
        await q.edit_message_text(
            "📩 <b>ارسال پیام به کاربر خاص</b>\n\n"
            "آیدی عددی کاربر مورد نظر را وارد کنید:\n"
            "مثال: <code>123456789</code>\n\n"
            "💡 آیدی عددی کاربران در لیست کاربران قابل مشاهده است.\n"
            "برای لغو <code>/cancel</code> بزنید.",
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith("admin:fake:edit:"):
        fid = data[len("admin:fake:edit:"):]
        ctx.user_data["awaiting"] = "fake_edit_pts"
        ctx.user_data["fake_edit_id"] = fid
        fakes = cloud.get_fake_users()
        fu = fakes.get(fid, {})
        await q.answer()
        await q.edit_message_text(
            f"✏️ <b>ویرایش کاربر فیک: {fu.get('full_name', fid)}</b>\n\n"
            f"امتیاز فعلی: <b>{fu.get('points', 0)}</b>\n\n"
            "امتیاز جدید را وارد کن (عدد صحیح):\n(برای لغو /cancel بزن)",
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith("admin:fake:del:"):
        fid = data[len("admin:fake:del:"):]
        deleted = cloud.delete_fake_user(fid)
        cloud.save(force=True)
        await q.answer("🗑 حذف شد." if deleted else "یافت نشد.", show_alert=True)
        text, markup = build_fakes_page()
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return

    await q.answer()


# ══════════════════════════════════════════════════════════════════════
# MESSAGE HANDLER (برای مکالمه‌ی چند مرحله‌ای ادمین)
# ══════════════════════════════════════════════════════════════════════
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """پیام‌های متنی ادمین را برای تکمیل مکالمه‌ی چند مرحله‌ای پردازش می‌کند."""
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    text_in = (update.message.text or "").strip()
    awaiting = ctx.user_data.get("awaiting")

    if text_in == "/cancel":
        ctx.user_data.pop("awaiting", None)
        await update.message.reply_text("❌ لغو شد.", reply_markup=kb_admin_main())
        return

    # ارسال همگانی
    if awaiting == "broadcast_text":
        ctx.user_data.pop("awaiting", None)
        await update.message.reply_text(
            "🚀 <b>در حال شروع ارسال همگانی...</b>\n\n"
            "پیام شما در پس‌زمینه به همه کاربران ارسال می‌شود.\n"
            "پس از اتمام، گزارش کامل را دریافت خواهید کرد.",
            parse_mode=ParseMode.HTML,
        )
        # اجرای ارسال در پس‌زمینه
        asyncio.create_task(broadcast_message(ctx.application, text_in, uid))
        return

    # ارسال به کاربر خاص - مرحله ۱: دریافت آیدی
    if awaiting == "senduser_uid":
        if not text_in.isdigit():
            await update.message.reply_text(
                "⚠️ آیدی عددی باید فقط شامل اعداد باشد.\n"
                "مثال: <code>123456789</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        
        target_uid = int(text_in)
        ctx.user_data["senduser_target"] = target_uid
        ctx.user_data["awaiting"] = "senduser_text"
        
        await update.message.reply_text(
            f"✅ آیدی <code>{target_uid}</code> ثبت شد.\n\n"
            "حالا متن پیام خود را بنویسید (پشتیبانی از HTML):\n"
            "برای لغو <code>/cancel</code> بزنید.",
            parse_mode=ParseMode.HTML,
        )
        return
    
    # ارسال به کاربر خاص - مرحله ۲: دریافت و ارسال متن
    if awaiting == "senduser_text":
        target_uid = ctx.user_data.get("senduser_target")
        ctx.user_data.pop("awaiting", None)
        ctx.user_data.pop("senduser_target", None)
        
        if not target_uid:
            await update.message.reply_text("❌ خطا: آیدی مقصد یافت نشد.", reply_markup=kb_admin_main())
            return
        
        try:
            await ctx.bot.send_message(
                chat_id=target_uid,
                text=text_in,
                parse_mode=ParseMode.HTML,
            )
            await update.message.reply_text(
                f"✅ <b>پیام با موفقیت ارسال شد!</b>\n\n"
                f"👤 مقصد: <code>{target_uid}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_admin_main(),
            )
        except Forbidden:
            await update.message.reply_text(
                f"❌ <b>ارسال ناموفق!</b>\n\n"
                f"👤 کاربر <code>{target_uid}</code> ربات را بلاک کرده یا استارت نکرده است.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_admin_main(),
            )
        except Exception as e:
            await update.message.reply_text(
                f"❌ <b>خطا در ارسال پیام:</b>\n\n"
                f"<code>{str(e)[:100]}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_admin_main(),
            )
        return

    # افزایش تصادفی امتیاز فیک‌ها
    if awaiting == "fake_boost_amount":
        # فرمت ورودی: "مقدار تعداد" مثلاً "200 5"
        parts = text_in.strip().split()
        if len(parts) != 2:
            await update.message.reply_text(
                "⚠️ <b>فرمت نادرست!</b>\n\n"
                "لطفاً به این شکل وارد کنید:\n"
                "<code>مقدار امتیاز تعداد فیک</code>\n\n"
                "مثال: <code>200 5</code>\n"
                "(۲۰۰ امتیاز به ۵ فیک رندوم)",
                parse_mode=ParseMode.HTML,
            )
            return
        
        try:
            boost_amount = int(parts[0])
            boost_count = int(parts[1])
            if boost_amount <= 0 or boost_count <= 0:
                await update.message.reply_text("⚠️ هر دو مقدار باید بزرگتر از صفر باشند.")
                return
        except ValueError:
            await update.message.reply_text(
                "⚠️ لطفاً فقط اعداد صحیح وارد کنید.\n"
                "مثال: <code>200 5</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        
        ctx.user_data.pop("awaiting", None)
        
        auto_ids = cloud.get_auto_fake_ids()
        if not auto_ids:
            await update.message.reply_text("❌ لیست فیک‌های انتخاب‌شده خالی است.", reply_markup=kb_admin_main())
            return
        
        # بررسی اینکه تعداد درخواستی بیشتر از فیک‌های موجود نباشد
        actual_count = min(boost_count, len(auto_ids))
        if boost_count > len(auto_ids):
            await update.message.reply_text(
                f"⚠️ شما {boost_count} فیک درخواست کردید ولی فقط {len(auto_ids)} فیک در لیست دارید.\n"
                f"به همه‌ی {actual_count} فیک اضافه می‌شود.",
                parse_mode=ParseMode.HTML,
            )
        
        # انتخاب تصادفی فیک‌ها
        selected_fakes = random.sample(auto_ids, actual_count)
        
        # اعمال امتیاز
        boosted_names = []
        with cloud._lock:
            for fid in selected_fakes:
                fu = cloud._data.get("fake_users", {}).get(fid)
                if fu:
                    fu["points"] = fu.get("points", 0) + boost_amount
                    fu["total_points"] = fu.get("total_points", 0) + boost_amount
                    boosted_names.append(fu.get("full_name", fid))
            cloud._dirty = True
        
        cloud.save(force=True)
        
        # نمایش نتیجه
        names_list = "\n".join([f"• {name}" for name in boosted_names])
        await update.message.reply_text(
            f"✅ <b>افزایش امتیاز با موفقیت انجام شد!</b>\n\n"
            f"💰 مقدار اضافه شده: <b>{boost_amount} امتیاز</b>\n"
            f"🎯 تعداد فیک‌های به‌روزشده: <b>{actual_count} از {len(auto_ids)}</b>\n\n"
            f"👥 <b>فیک‌های به‌روزشده:</b>\n{names_list}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_admin_main(),
        )
        
        log.info(f"🎯 Boost: +{boost_amount} به {actual_count} فیک از {len(auto_ids)}")
        return

    # تنظیمات سیستم امتیاز خودکار
    if awaiting == "auto_score_settings":
        parts = text_in.strip().split()
        if len(parts) != 3:
            await update.message.reply_text(
                "⚠️ <b>فرمت نادرست!</b>\n\n"
                "لطفاً به این شکل وارد کنید:\n"
                "<code>min max base</code>\n\n"
                "مثال: <code>40 80 200</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        
        try:
            min_pct = int(parts[0])
            max_pct = int(parts[1])
            base_points = int(parts[2])
            
            if not (0 < min_pct <= max_pct <= 100):
                await update.message.reply_text(
                    "⚠️ مقادیر درصد باید بین ۰ تا ۱۰۰ باشند و min ≤ max باشد.",
                    parse_mode=ParseMode.HTML,
                )
                return
            
            if base_points <= 0:
                await update.message.reply_text("⚠️ امتیاز پایه باید بزرگتر از صفر باشد.")
                return
        except ValueError:
            await update.message.reply_text(
                "⚠️ لطفاً فقط اعداد صحیح وارد کنید.\n"
                "مثال: <code>40 80 200</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        
        ctx.user_data.pop("awaiting", None)
        
        # به‌روزرسانی تنظیمات
        settings = {
            "min_percentage": min_pct,
            "max_percentage": max_pct,
            "base_points": base_points,
        }
        cloud.update_auto_score_settings(settings)
        cloud.save(force=True)
        
        await update.message.reply_text(
            f"✅ <b>تنظیمات با موفقیت به‌روز شد!</b>\n\n"
            f"📊 <b>تنظیمات جدید:</b>\n"
            f"• حداقل درصد فیک‌ها: <b>{min_pct}%</b>\n"
            f"• حداکثر درصد فیک‌ها: <b>{max_pct}%</b>\n"
            f"• امتیاز پایه: <b>{base_points}</b>\n\n"
            f"💡 حالا بعد از هر دور، سیستم به صورت خودکار به فیک‌ها امتیاز می‌دهد.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_admin_main(),
        )
        
        log.info(f"⚙️ تنظیمات امتیاز خودکار به‌روز شد: min={min_pct}%, max={max_pct}%, base={base_points}")
        return




    # مرحله‌ی ۱ افزودن فیک — نام
    if awaiting == "fake_add_name":
        ctx.user_data["fake_add_name"] = text_in
        ctx.user_data["awaiting"] = "fake_add_pts"
        await update.message.reply_text(
            f"✅ نام ثبت شد: <b>{text_in}</b>\n\nحالا امتیاز اولیه را وارد کن:",
            parse_mode=ParseMode.HTML)
        return

    # مرحله‌ی ۲ افزودن فیک — امتیاز
    if awaiting == "fake_add_pts":
        try:
            pts = int(text_in)
        except ValueError:
            await update.message.reply_text("⚠️ عدد صحیح وارد کن.")
            return
        name = ctx.user_data.get("fake_add_name", "فیک")
        fid = f"fake_{hashlib.md5(name.encode()).hexdigest()[:8]}"
        cloud.set_fake_user(fid, name, pts)
        cloud.save(force=True)
        ctx.user_data.pop("awaiting", None)
        ctx.user_data.pop("fake_add_name", None)
        await update.message.reply_text(
            f"✅ کاربر فیک <b>{name}</b> با {pts} امتیاز اضافه شد.",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_main())
        return

    # ویرایش امتیاز فیک
    if awaiting == "fake_edit_pts":
        try:
            pts = int(text_in)
        except ValueError:
            await update.message.reply_text("⚠️ عدد صحیح وارد کن.")
            return
        fid = ctx.user_data.get("fake_edit_id")
        fakes = cloud.get_fake_users()
        if fid and fid in fakes:
            cloud.set_fake_user(fid, fakes[fid]["full_name"], pts)
            cloud.save(force=True)
            ctx.user_data.pop("awaiting", None)
            await update.message.reply_text(
                f"✅ امتیاز <b>{fakes[fid]['full_name']}</b> به {pts} تغییر یافت.",
                parse_mode=ParseMode.HTML, reply_markup=kb_admin_main())
        else:
            await update.message.reply_text("❌ کاربر فیک یافت نشد.")
            ctx.user_data.pop("awaiting", None)
        return

    # تنظیم امتیاز کاربر واقعی
    if awaiting == "setpts":
        uid_str = ctx.user_data.get("setpts_uid", "")
        delta_mode = text_in.startswith(("+", "-")) and len(text_in) > 1
        try:
            val = int(text_in)
        except ValueError:
            await update.message.reply_text("⚠️ عدد صحیح وارد کن. مثلاً 100 یا +50 یا -20")
            return
        if delta_mode:
            ok = cloud.add_user_points(uid_str, val)
        else:
            ok = cloud.set_user_points(uid_str, val)
        cloud.save(force=True)
        ctx.user_data.pop("awaiting", None)
        ctx.user_data.pop("setpts_uid", None)
        if ok:
            u = cloud.get_user(int(uid_str)) if uid_str.isdigit() else None
            new_pts = u.get("points", "؟") if u else "؟"
            await update.message.reply_text(
                f"✅ امتیاز کاربر <code>{uid_str}</code> به <b>{new_pts}</b> تغییر یافت.",
                parse_mode=ParseMode.HTML, reply_markup=kb_admin_main())
        else:
            await update.message.reply_text("❌ کاربر یافت نشد.")
        return


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    log.error(f"error: {ctx.error}", exc_info=ctx.error)


# ══════════════════════════════════════════════════════════════════════
# BACKGROUND LOOPS
# ══════════════════════════════════════════════════════════════════════
async def round_watcher(app: Application):
    while True:
        try:
            await asyncio.sleep(WATCHER_TICK_SEC)
            rnd = cloud.get_active_round()
            if not rnd or rnd["status"] != "open":
                continue

            vote_deadline_dt = datetime.fromisoformat(rnd["vote_deadline"])
            lock_dt = datetime.fromisoformat(rnd["lock_time"])
            now = now_ir()

            if not rnd.get("vote_closed") and now < vote_deadline_dt:
                last_edit = rnd.get("last_edit_ts", 0)
                if time.time() - last_edit >= LIVE_UPDATE_SEC:
                    cloud.touch_round_edit(rnd["id"])
                    await _edit_round_message(app, rnd, "voting")
                    
            elif not rnd.get("vote_closed") and now >= vote_deadline_dt:
                cloud.mark_vote_closed(rnd["id"])
                rnd = cloud.get_active_round() or rnd
                cloud.touch_round_edit(rnd["id"])  # ریست کردن تایمر برای آپدیت‌های بعدی
                await _edit_round_message(app, rnd, "voting_closed")
                
            elif rnd.get("vote_closed") and now < lock_dt:
                # آپدیت زنده‌ی پیام در فاز بسته شدن رأی‌گیری تا زمان پایان کل دور
                last_edit = rnd.get("last_edit_ts", 0)
                if time.time() - last_edit >= LIVE_UPDATE_SEC:
                    cloud.touch_round_edit(rnd["id"])
                    await _edit_round_message(app, rnd, "voting_closed")
                    
            if now >= lock_dt:
                cloud.set_round_status(rnd["id"], "locked")
                await _resolve_round(app, rnd)

        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"round_watcher error: {e}")


async def weekly_reset_watcher(app: Application):
    while True:
        try:
            await asyncio.sleep(600)
            today = now_ir().date()
            if today.weekday() != 5:
                continue
            if cloud.get_last_reset() == today.isoformat():
                continue
            snapshot = cloud.get_weekly_leaderboard(top=TOP_REPORT_COUNT)
            if snapshot:
                lines = ["🏁 <b>پایان هفته‌ی تورنومنت — گزارش برندگان</b>\n"]
                
                # رتبه‌های 1، 2، 3 با مدال ویژه
                medals = {1: "🥇", 2: "🥈", 3: "🥉"}
                for row in snapshot[:3]:
                    medal = medals.get(row["rank"], f"{row['rank']}.")
                    lines.append(f"{medal} <b>{row['full_name']}</b> — {row['points']} امتیاز")
                
                # رتبه‌های 4 تا 10 ساده
                for row in snapshot[3:10]:
                    lines.append(f"{row['rank']}. {row['full_name']} — {row['points']} امتیاز")
                
                # توضیحات سیستم ضد تقلب
                lines.append("\n⚠️ <b>توجه:</b> این لیست اولیه تا لحضه اخر اتمام تایم میباشد. سیستم ضد تقلب در حال بررسی دقیق امتیازهای کسب‌شده است.")
                lines.append("⏳ به دلیل محدودیت‌های ریت لیمیت تلگرام، بررسی نهایی و اعلام برندگان قطعی ممکن است <b>چندین ساعت</b> طول بکشد.")
                lines.append("🔍 پس از اتمام بررسی، برندگان نهایی اعلام خواهند شد.")
                
                await _send_to_admins(app, "\n".join(lines))
            else:
                await _send_to_admins(app, "🏁 پایان هفته‌ی تورنومنت — این هفته کسی امتیازی کسب نکرد.")
            cloud.reset_weekly(today.isoformat(), snapshot)
            cloud.save(force=True)
            log.info("Weekly leaderboard reset done.")
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"weekly_reset_watcher error: {e}")


async def fake_referral_watcher(app: Application):
    """رشد طبیعی و هوشمند فیک‌های انتخاب‌شده با Weighted Random"""
    while True:
        try:
            # تایم رندوم بین ۸ تا ۱۵ دقیقه (طبیعی‌تر)
            sleep_time = random.randint(480, 900)
            await asyncio.sleep(sleep_time)
            
            if not fake_referral_enabled:
                continue
            
            auto_ids = cloud.get_auto_fake_ids()
            if not auto_ids:
                continue
            
            # گرفتن لیست فیک‌های معتبر با امتیازهاشون
            valid_fakes = []
            with cloud._lock:
                for fid in auto_ids:
                    fu = cloud._data.get("fake_users", {}).get(fid)
                    if fu:
                        valid_fakes.append({
                            "fid": fid,
                            "full_name": fu.get("full_name", fid),
                            "points": fu.get("points", 0),
                        })
            
            if not valid_fakes:
                continue
            
            # ═══════════════════════════════════════════════════════
            # الگوریتم Weighted Random: شانس بیشتر برای فیک‌های برتر
            # ═══════════════════════════════════════════════════════
            # Weight = ۱۰۰ (پایه) + امتیاز فعلی
            # مثال: فیک با ۵۰۰ امتیاز → وزن ۶۰۰
            #       فیک با ۲۰۰ امتیاز → وزن ۳۰۰
            # شانس فیک اول ۲ برابر فیک دوم هست!
            base_weight = 100
            weights = [base_weight + fake["points"] for fake in valid_fakes]
            total_weight = sum(weights)
            probabilities = [w / total_weight for w in weights]
            
            # انتخاب ۱ تا ۳ فیک به صورت رندوم (طبیعی‌تر)
            # ۵۰٪ شانس ۱ فیک، ۳۵٪ شانس ۲ فیک، ۱۵٪ شانس ۳ فیک
            num_to_award = random.choices([1, 2, 3], weights=[50, 35, 15])[0]
            num_to_award = min(num_to_award, len(valid_fakes))
            
            # انتخاب فیک‌ها با weighted random (بدون تکرار)
            selected = []
            available_fakes = valid_fakes.copy()
            available_probs = probabilities.copy()
            
            for _ in range(num_to_award):
                if not available_fakes:
                    break
                
                # نرمال‌سازی احتمالات
                total_prob = sum(available_probs)
                normalized_probs = [p / total_prob for p in available_probs]
                
                # انتخاب یک فیک با weighted random
                selected_idx = random.choices(range(len(available_fakes)), weights=normalized_probs)[0]
                selected.append(available_fakes[selected_idx])
                
                # حذف فیک انتخاب‌شده از لیست (برای جلوگیری از تکرار)
                available_fakes.pop(selected_idx)
                available_probs.pop(selected_idx)
            
            # ═══════════════════════════════════════════════════════
            # اعطای ۲۰ امتیاز به فیک‌های انتخاب‌شده
            # ═══════════════════════════════════════════════════════
            awarded = []
            with cloud._lock:
                for fake in selected:
                    fu = cloud._data.get("fake_users", {}).get(fake["fid"])
                    if fu:
                        fu["points"] = fu.get("points", 0) + REF_BONUS_STEP1  # ۲۰ امتیاز
                        fu["total_points"] = fu.get("total_points", 0) + REF_BONUS_STEP1
                        fu["invites"] = fu.get("invites", 0) + 1
                        awarded.append({
                            "name": fake["full_name"],
                            "old_points": fake["points"],
                            "new_points": fake["points"] + REF_BONUS_STEP1,
                        })
                cloud._dirty = True
            
            if awarded:
                log.info(
                    f"🤖 رشد طبیعی: +{REF_BONUS_STEP1} امتیاز به {len(awarded)} فیک "
                    f"({', '.join([a['name'] for a in awarded])})"
                )
        
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"fake_referral_watcher error: {e}")


async def autosave_loop(app: Application):
    while True:
        try:
            await asyncio.sleep(300)
            # بدون force=True تا فقط در صورتی که تغییری (_dirty) ایجاد شده بود ذخیره کند
            cloud.save()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"autosave_loop error: {e}")


async def on_startup(app: Application):
    global BOT_USERNAME
    try:
        me = await app.bot.get_me()
        BOT_USERNAME = me.username or ""
    except Exception as e:
        log.warning(f"could not fetch bot username at startup: {e}")
    asyncio.create_task(round_watcher(app))
    asyncio.create_task(weekly_reset_watcher(app))
    asyncio.create_task(fake_referral_watcher(app))
    asyncio.create_task(autosave_loop(app))
    log.info("Gold Prediction Bot v2 ready ✅")


async def on_shutdown(app: Application):
    cloud.save(force=True)
    log.info("shutdown")



# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════
def main():
    global app  # دسترسی از Flask handlers
    
    if not BOT_TOKEN:
        log.critical("BOT_TOKEN تنظیم نشده!")
        sys.exit(1)

    cloud.load()
    
    # ساخت Application (هنوز اجرا نمی‌کنیم)
    app = (Application.builder().token(BOT_TOKEN)
           .post_init(on_startup).post_shutdown(on_shutdown).build())

    # ثبت handlerها
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("invite", cmd_invite))
    app.add_handler(CommandHandler("myref", cmd_invite))
    app.add_handler(CommandHandler("adminpanel", cmd_adminpanel))
    app.add_handler(CommandHandler("newround", cmd_newround))
    app.add_handler(CommandHandler("cancelround", cmd_cancelround))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, on_message))
    app.add_error_handler(on_error)

    # ── Signal Handlers ──────────────────────────────────────────
    def _sig(s, f):
        cloud.save(force=True)
        sys.exit(0)
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    atexit.register(lambda: cloud.save(force=True))

    # ── Webhook vs Polling ──────────────────────────────────────
    webhook_url = os.environ.get("RENDER_EXTERNAL_URL")
    
    if webhook_url:
        log.info("🌐 Running with Flask Webhook server")
        
        # ──── ساخت حلقه‌ی asyncio اختصاصی برای processing ────
        main_loop = asyncio.new_event_loop()
        
        def run_event_loop(loop):
            asyncio.set_event_loop(loop)
            loop.run_forever()
        
        # اجرای حلقه در thread جداگانه
        loop_thread = threading.Thread(target=run_event_loop, args=(main_loop,), daemon=True)
        loop_thread.start()
        
        # ──── ساخت Flask App ────
        flask_app = Flask(__name__)
        
        # ✅ Endpoint برای UptimeRobot (پاسخ به GET /)
        @flask_app.route("/")
        def index():
            return "🤖 Bot is alive and running!", 200
        
        # ✅ Health check endpoint (اختیاری)
        @flask_app.route("/health")
        def health():
            return {"status": "ok", "bot": BOT_USERNAME}, 200
        
        # ✅ Endpoint اصلی تلگرام (POST /{BOT_TOKEN})
        @flask_app.route(f"/{BOT_TOKEN}", methods=["POST"])
        def telegram_webhook():
            try:
                json_data = request.get_json(force=True)
                update = Update.de_json(json_data, app.bot)
                # ارسال update به حلقه‌ی asyncio برای پردازش
                asyncio.run_coroutine_threadsafe(
                    app.process_update(update), 
                    main_loop
                )
            except Exception as e:
                log.error(f"Webhook processing error: {e}")
            return "OK", 200
        
        # ──── ست کردن Webhook در تلگرام ────
        async def setup_webhook():
            full_webhook_url = webhook_url.rstrip("/") + "/" + BOT_TOKEN
            await app.bot.set_webhook(
                url=full_webhook_url,
                allowed_updates=["message", "callback_query"],
                drop_pending_updates=True,
            )
            log.info(f"✅ Webhook set to: {full_webhook_url[:60]}...")
            return full_webhook_url
        
        # اجرای setup_webhook در حلقه‌ی asyncio
        future = asyncio.run_coroutine_threadsafe(setup_webhook(), main_loop)
        future.result(timeout=30)  # صبر برای تکمیل
        
        # ──── راه‌اندازی Bot (initialize) ────
        async def initialize_bot():
            await app.initialize()
            await app.start()
            await on_startup(app)  # ✅ این خط را اضافه کنید!

        asyncio.run_coroutine_threadsafe(initialize_bot(), main_loop).result(timeout=30)
        
        # ──── اجرای Flask در thread اصلی ────
        log.info(f"🚀 Starting Flask on port {PORT}")
        flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
    
    else:
        # ── Fallback به Polling (برای تست محلی) ──
        log.warning("⚠️ RENDER_EXTERNAL_URL not set, falling back to polling")
        app.run_polling(
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )


if __name__ == "__main__":
    main()
