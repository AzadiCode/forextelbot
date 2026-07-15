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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from flask import Flask
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

CHANNEL_ID        = os.environ.get("CHANNEL_ID", "")
# کانالی که کاربر باید عضو آن باشد؛ اگر خالی باشد چک عضویت انجام نمی‌شود
FORCE_JOIN_CHANNEL = os.environ.get("FORCE_JOIN_CHANNEL", "@testerrrrrfg")

POINTS_PER_CORRECT   = int(os.environ.get("POINTS_PER_CORRECT", 130))
REF_POINTS_REFERRER  = int(os.environ.get("REF_POINTS_REFERRER", 90))
REF_POINTS_INVITEE   = int(os.environ.get("REF_POINTS_INVITEE", 60))
DEFAULT_ROUND_MIN    = int(os.environ.get("DEFAULT_ROUND_MINUTES", 60))
VOTE_WINDOW_MINUTES  = int(os.environ.get("VOTE_WINDOW_MINUTES", 10))
LIVE_UPDATE_SEC      = int(os.environ.get("LIVE_UPDATE_SEC", 9))
WATCHER_TICK_SEC     = 5
TOP_REPORT_COUNT     = 10
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
# CLOUD STORAGE
# ══════════════════════════════════════════════════════════════════════
class CloudStorage:
    def __init__(self):
        self._key  = JSONBIN_KEY
        self._bin  = JSONBIN_BIN
        self._url  = "https://api.jsonbin.io/v3/b"
        self._hdrs = {"X-Master-Key": self._key, "Content-Type": "application/json"}
        self._lock = threading.Lock()
        self._dirty = False
        self._data = {
            "users": {},
            "fake_users": {},            # ← کاربران فیک برای لیدربرد
            "active_round": None,
            "next_round_id": 1,
            "rounds_history": [],
            "leaderboard_history": [],
            "last_lb_reset": None,
            "last_updated": None,
        }

    # ---------- persistence ----------
    def load(self):
        if not (self._key and self._bin):
            log.warning("JSONBin تنظیم نشده — داده‌ها فقط در حافظه نگه داشته می‌شوند.")
            return
        try:
            r = requests.get(f"{self._url}/{self._bin}/latest", headers=self._hdrs, timeout=15)
            r.raise_for_status()
            remote = r.json().get("record", {})
            with self._lock:
                for k, v in remote.items():
                    self._data[k] = v
                self._data.setdefault("users", {})
                self._data.setdefault("fake_users", {})
                self._data.setdefault("active_round", None)
                self._data.setdefault("next_round_id", 1)
                self._data.setdefault("rounds_history", [])
                self._data.setdefault("leaderboard_history", [])
                self._data.setdefault("last_lb_reset", None)
            log.info("CloudStorage: داده‌ها با موفقیت بارگذاری شد.")
        except Exception as e:
            log.error(f"CloudStorage load error: {e}")

    def save(self, force: bool = False):
        if not self._key:
            return
        if not force and not self._dirty:
            return
        with self._lock:
            self._data["last_updated"] = now_ir().isoformat()
            payload = dict(self._data)
        try:
            if self._bin:
                r = requests.put(f"{self._url}/{self._bin}", headers=self._hdrs, json=payload, timeout=15)
            else:
                r = requests.post(self._url, headers={**self._hdrs, "X-Bin-Private": "false"},
                                   json=payload, timeout=15)
                bid = r.json().get("metadata", {}).get("id", "")
                if bid:
                    self._bin = bid
                    log.info(f"JSONBin جدید ساخته شد: {bid}")
            r.raise_for_status()
            self._dirty = False
        except Exception as e:
            log.error(f"CloudStorage save error: {e}")

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
                    "joined": now_ir().isoformat(),
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
                # اطمینان از وجود uid در پروفایل قدیمی
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
        """افزودن یا ویرایش کاربر فیک."""
        with self._lock:
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
            users[r_key]["points"] = users[r_key].get("points", 0) + REF_POINTS_REFERRER
            users[r_key]["total_points"] = users[r_key].get("total_points", 0) + REF_POINTS_REFERRER
            users[r_key]["invites"] = users[r_key].get("invites", 0) + 1
            users[n_key]["points"] = users[n_key].get("points", 0) + REF_POINTS_INVITEE
            users[n_key]["total_points"] = users[n_key].get("total_points", 0) + REF_POINTS_INVITEE
            self._dirty = True
            return True

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
        """تنظیم مستقیم امتیاز کاربر (توسط ادمین)."""
        with self._lock:
            if uid_str in self._data["users"]:
                self._data["users"][uid_str]["points"] = points
                if points > self._data["users"][uid_str].get("total_points", 0):
                    self._data["users"][uid_str]["total_points"] = points
                self._dirty = True
                return True
            return False

    def add_user_points(self, uid_str: str, delta: int):
        """افزودن/کاهش امتیاز کاربر (توسط ادمین)."""
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
        """لیدربرد ترکیبی از کاربران واقعی + فیک، مرتب‌شده بر اساس امتیاز."""
        with self._lock:
            combined = []
            # کاربران واقعی
            for uid_str, u in self._data["users"].items():
                if u.get("points", 0) > 0:
                    combined.append({
                        "uid": uid_str,
                        "full_name": u.get("full_name", "").strip() or u.get("username", "") or f"کاربر {uid_str[-4:]}",
                        "username": u.get("username", ""),
                        "points": u.get("points", 0),
                        "is_fake": False,
                    })
            # کاربران فیک
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


cloud = CloudStorage()


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
        "🎁✨ <b>تبریک! دوستت تو رو به تورنومنت طلایی دعوت کرده!</b> ✨🎁\n\n"
        "🥇 اینجا بزرگ‌ترین مسابقه‌ی پیش‌بینی قیمت انس جهانی طلاست که هر هفته "
        "جوایز <b>نقدی و پراپ‌ترید</b> به نفرات برتر اهدا می‌شه!\n\n"
        "💰 <b>برای دریافت پاداش دعوت و شرکت در تورنومنت:</b>\n"
        f"👉 ابتدا باید عضو {channel_link} اصلی ربات بشی\n"
        "📢 جایی که تمام دورهای مسابقه و اطلاعیه‌های مهم منتشر می‌شن\n\n"
        "✅ بعد از عضویت، روی دکمه‌ی زیر بزن تا:\n"
        "  • امتیاز رایگان دعوت به حسابت اضافه بشه\n"
        "  • بتونی توی دورهای بعدی تورنومنت شرکت کنی\n\n"
        "🔥 <b>همین الان عضو شو و برای ادامه روی عضو شدم بزن !</b>"
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
         InlineKeyboardButton("🏆 لیدربرد", callback_data="menu:leaderboard")],
        [InlineKeyboardButton("🎁 دعوت دوستان", callback_data="menu:invite")],
        [InlineKeyboardButton("ℹ️ راهنما", callback_data="menu:help")],
    ]
    if uid and is_admin(uid):
        rows.append([InlineKeyboardButton("👑 پنل ادمین", callback_data="admin:panel:0")])
    return InlineKeyboardMarkup(rows)


def kb_round_voting(round_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬆️ بالا", callback_data=f"vote:{round_id}:up"),
         InlineKeyboardButton("⬇️ پایین", callback_data=f"vote:{round_id}:down")],
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
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 لیست کاربران", callback_data="admin:users:0")],
        [InlineKeyboardButton("🤖 کاربران فیک", callback_data="admin:fakes:list")],
        [InlineKeyboardButton("➕ افزودن کاربر فیک", callback_data="admin:fake:add_prompt")],
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
def _fmt_price(p: float) -> str:
    return f"{p:,.2f}"


def build_welcome_text() -> str:
    return (
        "🥇✨ <b>به بزرگ‌ترین تورنومنت پیش‌بینی قیمت طلا خوش اومدی!</b> ✨🥇\n\n"
        "اینجا هر دور در روز، فقط با یک لمس روی «بالا» یا «پایین»، نبض بازار جهانی "
        "انس طلا (XAU/USD) رو حدس می‌زنی و اگه درست پیش‌بینی کنی، امتیاز واقعی "
        "می‌گیری که در پایان هفته به جایزه واقعی تبدیل می‌شه!\n\n"
        "🎯 <b>قوانین طلایی تورنومنت:</b>\n"
        f"  • هر دور فقط <b>{VOTE_WINDOW_MINUTES} دقیقه‌ی اول</b> امکان ثبت رأی داری ⏳\n"
        "  • هر کاربر فقط <b>یک‌بار</b> در هر دور رأی می‌ده؛ رأی اول نهایی‌ست 🔒\n"
        f"  • پیش‌بینی درست = <b>{POINTS_PER_CORRECT} امتیاز</b> مستقیم به حسابت\n\n"
        "🎁 با دعوت دوستات هم امتیاز بیشتری می‌گیری و شانست برای برنده شدن بیشتر میشه!\n"
        "🏆 هر هفته لیدربرد ریست می‌شه و به نفرات برتر جایزه‌ی واقعی تعلق می‌گیره:\n"
        f"  🏅 رتبه‌ی 1 تا {PROP_PRIZE_RANKS}: جایزه‌ی دلاری\n"
        f"  💵 رتبه‌ی {PROP_PRIZE_RANKS + 1} تا {TOP_REPORT_COUNT}: جایزه‌ی اکانت پراپ بزودی ...\n\n"
        "همین حالا از منوی زیر شروع کن و منتظر باز شدن دور بعدی پیشبینی توی کانال بمون 👇"
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
            f"🥇 <b>دور #{rnd['id']} پیش‌بینی طلا </b>\n"
            f"💰 قیمت شروع در این لحضه : <code>{start_p}</code>\n"
            f"⏳ زمان باقی‌مانده برای ثبت رأی: <code>{countdown}</code> ⏳\n"
            f"⏱ زمان باقی‌مانده تا پایان دور و مشخص شدن نتیجه: <code>{total_countdown}</code>\n\n"
            "طلا تا پایان این دور، نسبت به قیمت شروع بالا می‌ره یا پایین؟ "
            "نظرت رو با یکی از دکمه‌های زیر ثبت کن 👇\n\n"
            "⚠️ <i>هر کاربر فقط یک‌بار می‌تونه رأی بده و بعدش امکان تغییرش نیست.</i>\n"
            f"✅ پیش‌بینی درست = <b>{POINTS_PER_CORRECT} امتیاز</b>\n\n"
            "📍 برای دیدن جزئیات، امتیاز و رتبه‌ات، روی دکمه‌ی «پروفایل من» بزن."
        )

    if phase == "voting_closed":
        lock_dt = datetime.fromisoformat(rnd["lock_time"])
        total_remaining = (lock_dt - now_ir()).total_seconds()
        total_countdown = _fmt_mmss(total_remaining)
        return (
            f"🥇 <b>دور #{rnd['id']} پیش‌بینی طلا </b>\n"
            f"💰 قیمت شروع: <code>{start_p}</code>\n"
            "⛔️ <b>زمان ثبت رأی به پایان رسید — دیگه امکان رأی دادن نیست.</b>\n"
            f"⏱ زمان باقی‌مانده تا پایان دور و مشخص شدن نتیجه: <code>{total_countdown}</code>\n\n"
            "نتیجه‌ی نهایی به‌محض بسته‌شدن دور اعلام می‌شه، منتظر بمون... ⏱\n"
            "📍 برای دیدن جزئیات و امتیازت، روی دکمه‌ی زیر بزن."
        )

    arrow = {"up": "📈 صعودی", "down": "📉 نزولی", "flat": "➖ بدون تغییر"}[direction]
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
        "👤 <b>پروفایل تو</b>\n\n"
        f"📛 نام: <b>{full}</b>\n"
        f"🥇 امتیاز این هفته: <b>{u.get('points', 0)}</b>\n"
        f"⭐️ امتیاز کل (تاریخچه): <b>{u.get('total_points', 0)}</b>\n"
        f"{rank_line}"
        f"🎯 پیش‌بینی درست: <b>{acc}</b>\n"
        f"🎁 تعداد دعوت موفق: <b>{u.get('invites', 0)}</b>"
    )


def build_leaderboard_text() -> str:
    lb = cloud.get_weekly_leaderboard(top=10)
    reset_dt = next_weekly_reset_dt(now_ir())
    remaining = (reset_dt - now_ir()).total_seconds()
    countdown_line = f"\n⏰ زمان باقی‌مانده تا پایان تورنومنت هفتگی: <b>{_fmt_timedelta_long(remaining)}</b>"

    if not lb:
        return (
            "🏆 <b>لیدربرد این هفته</b>\n\n"
            "هنوز کسی امتیازی کسب نکرده — اولین نفر باش! 🔥"
            f"{countdown_line}"
        )
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = ["🏆 <b>لیدربرد تورنومنت این هفته</b>\n"]
    for row in lb:
        tag = medals.get(row["rank"], f"{row['rank']}.")
        # اسم کامل نمایش داده می‌شود
        name = row["full_name"]
        lines.append(f"{tag} {name} — <b>{row['points']}</b> امتیاز")
    lines.append(countdown_line)
    lines.append(f"\n🔔 در پایان: رتبه ۱ تا {PROP_PRIZE_RANKS} 🏅 جایزه‌ی پراپ، رتبه {PROP_PRIZE_RANKS + 1} "
                 f"تا {TOP_REPORT_COUNT} 💵 جایزه‌ی نقدی.")
    return "\n".join(lines)


def build_referral_text(uid: int, bot_username: str) -> str:
    u = cloud.ensure_user(uid)
    link = f"https://t.me/{bot_username}?start=ref_{u['ref']}"
    return (
        "🎁 <b>دعوت دوستان</b>\n\n"
        f"با لینک اختصاصی زیر دوستاتو دعوت کن کافیه بزنی رو لینک تا کپی بشه :\n<code>{link}</code>\n\n"
        f"✅ به ازای هر دعوت موفق: <b>{REF_POINTS_REFERRER} امتیاز</b> برای تو\n"
        f"🎉 دوستت هم با ورود از لینکت: <b>{REF_POINTS_INVITEE} امتیاز</b> هدیه می‌گیره\n\n"
        f"👥 تعداد دعوت‌های موفق تو: <b>{u.get('invites', 0)}</b>"
    )


def build_help_text(admin: bool) -> str:
    txt = (
        "ℹ️ <b>راهنمای کامل تورنومنت</b>\n\n"
        "🥇 هر دور، یک قیمت شروع برای انس جهانی طلا (XAU/USD) در کانال اعلام می‌شه. "
        f"تو فقط <b>{VOTE_WINDOW_MINUTES} دقیقه‌ی اول</b> فرصت داری با زدن دکمه‌ی «⬆️ بالا» یا "
        "«⬇️ پایین» پیش‌بینی کنی.\n\n"
        "⏳ شمارش معکوس زنده روی پست کانال بروزرسانی می‌شه — وقتی تمام شد، رأی‌گیری بسته می‌شه.\n\n"
        "🔒 هر کاربر فقط یک‌بار در هر دور می‌تونه رأی بده.\n\n"
        f"✅ پیش‌بینی درست = <b>{POINTS_PER_CORRECT} امتیاز</b> — نتیجه خصوصی اطلاع‌رسانی می‌شه.\n\n"
        "🎁 <b>دعوت دوستان:</b> لینک اختصاصی از /invite — هم تو هم دوستت امتیاز بیشتری می‌گیرید وشانس برنده شدنتون بیشتر میشه.\n\n"
        "🏆 <b>تورنومنت هفتگی:</b> آخر هفته لیدربرد ریست می‌شه و جوایز تعلق می‌گیره.\n\n"
        "📋 <b>جوایز توسط مدیر در کانال اطلاع رسانی و مشخص میشود </b>\n"
        "📋 <b>دستورات:</b>\n"
        "/start — شروع و ثبت‌نام\n"
        "/profile — مشاهده امتیاز و رتبه‌ات\n"
        "/leaderboard — لیدربرد و شمارش معکوس تورنومنت\n"
        "/invite — لینک دعوت دوستان\n"
        "/help — همین راهنما"
    )
    if admin:
        txt += (
            "\n\n👑 <b>دستورات ادمین</b>\n"
            "/newround [دقیقه کل] [دقیقه رأی‌گیری] — باز کردن دور جدید "
            f"(پیش‌فرض {DEFAULT_ROUND_MIN} دقیقه‌ی کل، {VOTE_WINDOW_MINUTES} دقیقه رأی‌گیری)\n"
            "/cancelround — باطل کردن دور فعال\n"
            "/status — وضعیت دور فعال\n"
            "/adminpanel — پنل مدیریت کاربران"
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
    try:
        await app.bot.edit_message_text(
            chat_id=rnd["chat_id"], message_id=rnd["message_id"],
            text=text, parse_mode=ParseMode.HTML, reply_markup=markup,
        )
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
    cloud.save(force=True)


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
                await update.effective_message.reply_text(
                    build_join_required_text(), parse_mode=ParseMode.HTML,
                    reply_markup=kb_join_required())
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
                await update.effective_message.reply_text(
                    build_referral_join_text(), parse_mode=ParseMode.HTML,
                    reply_markup=kb_join_required())
                return
            
            ref_uid = cloud.find_uid_by_ref(ref_code)
            if ref_uid:
                ok = cloud.apply_referral(uid, ref_uid)
                if ok:
                    try:
                        await ctx.bot.send_message(
                            chat_id=ref_uid, parse_mode=ParseMode.HTML,
                            text=("🎉 <b>خبر خوب!</b> یک نفر با لینک دعوت اختصاصی تو وارد ربات شد.\n"
                                  f"⭐️ +{REF_POINTS_REFERRER} امتیاز به حسابت اضافه شد."))
                    except Exception:
                        pass
                    try:
                        await ctx.bot.send_message(
                            chat_id=uid, parse_mode=ParseMode.HTML,
                            text=("🎁 <b>خوش اومدی!</b> چون از طریق لینک دعوت یک دوست وارد شدی، "
                                  f"⭐️ +{REF_POINTS_INVITEE} امتیاز رایگان بهت تعلق گرفت."))
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
    await update.effective_message.reply_text(
        build_leaderboard_text(), parse_mode=ParseMode.HTML, reply_markup=kb_back())


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

    placeholder = await ctx.bot.send_message(chat_id=CHANNEL_ID, text="⏳ در حال باز کردن دور جدید...")
    rnd = cloud.create_round(price, minutes, CHANNEL_ID, placeholder.message_id, vote_minutes)
    await _edit_round_message(ctx.application, rnd, "voting")
    await update.effective_message.reply_text(
        f"✅ دور #{rnd['id']} با قیمت شروع {_fmt_price(price)} برای {minutes} دقیقه در کانال باز شد "
        f"(بازه‌ی رأی‌گیری: {rnd['vote_minutes']} دقیقه‌ی اول).")
    cloud.save(force=True)


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
            await q.edit_message_text(
                build_join_required_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=kb_join_required(),
            )
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
                    # تابع apply_referral خودش چک می‌کند که کاربر از قبل زیرمجموعه نباشد
                    ok = cloud.apply_referral(uid, ref_uid)
                    if ok:
                        try:
                            await ctx.bot.send_message(
                                chat_id=ref_uid, parse_mode=ParseMode.HTML,
                                text=("🎉 <b>خبر خوب!</b> یک نفر با لینک دعوت اختصاصی تو وارد ربات شد.\n"
                                      f"⭐️ +{REF_POINTS_REFERRER} امتیاز به حسابت اضافه شد."))
                        except Exception:
                            pass
                        try:
                            await ctx.bot.send_message(
                                chat_id=uid, parse_mode=ParseMode.HTML,
                                text=("🎁 <b>خوش اومدی!</b> چون از طریق لینک دعوت یک دوست وارد شدی، "
                                      f"⭐️ +{REF_POINTS_INVITEE} امتیاز رایگان بهت تعلق گرفت."))
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
        await q.edit_message_text(build_leaderboard_text(), parse_mode=ParseMode.HTML, reply_markup=kb_back())
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
                lines = ["🏁 <b>پایان هفته‌ی تورنومنت — گزارش برای هماهنگی جوایز</b>\n"]
                for row in snapshot:
                    tag = "🏅 پراپ" if row["rank"] <= PROP_PRIZE_RANKS else "💵 نقدی"
                    lines.append(f"{row['rank']}. {row['full_name']} — {row['points']} امتیاز ({tag})")
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


async def autosave_loop(app: Application):
    while True:
        try:
            await asyncio.sleep(300)
            cloud.save(force=True)
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
    threading.Thread(target=_run_flask, daemon=True).start()
    asyncio.create_task(round_watcher(app))
    asyncio.create_task(weekly_reset_watcher(app))
    asyncio.create_task(autosave_loop(app))
    log.info("Gold Prediction Bot v2 ready ✅")


async def on_shutdown(app: Application):
    cloud.save(force=True)
    log.info("shutdown")


# ══════════════════════════════════════════════════════════════════════
# FLASK KEEP-ALIVE
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)


@flask_app.route("/")
def root():
    n_users = len(cloud._data.get("users", {}))
    return f"Gold Prediction Bot v2 | users={n_users} | {now_ir().strftime('%Y-%m-%d %H:%M:%S')}"


def _run_flask():
    flask_app.run(host="0.0.0.0", port=PORT)


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════
def main():
    if not BOT_TOKEN:
        log.critical("BOT_TOKEN تنظیم نشده!")
        sys.exit(1)

    cloud.load()
    app = (Application.builder().token(BOT_TOKEN)
           .post_init(on_startup).post_shutdown(on_shutdown).build())

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
    # هندلر متنی فقط برای پیام‌های خصوصی (برای مکالمه‌ی ادمین)
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, on_message))
    app.add_error_handler(on_error)

    def _sig(s, f):
        cloud.save(force=True)
        sys.exit(0)
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    atexit.register(lambda: cloud.save(force=True))

    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)


if __name__ == "__main__":
    main()
