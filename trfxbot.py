#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gold Prediction Tournament Bot
════════════════════════════════════════════════════════════════════
تورنومنت هفتگی پیش‌بینی قیمت انس جهانی طلا (XAU/USD)

قابلیت‌ها:
  • ادمین با دستور /newround یک دور پیش‌بینی باز می‌کند (پیش‌فرض ۱ ساعته)
    و پیام آن با دو دکمه «بالا / پایین» در کانال منتشر می‌شود.
  • کاربران با زدن دکمه، جهت حرکت قیمت طلا تا پایان دور را پیش‌بینی می‌کنند.
  • در پایان دور، ربات به‌صورت خودکار نتیجه را با قیمت لحظه‌ای می‌سنجد و
    به همه‌ی کسانی که جهت را درست حدس زده باشند امتیاز ثابت می‌دهد.
  • هر کاربر با دعوت دوستان (لینک رفرال اختصاصی) امتیاز می‌گیرد.
  • لیدربرد ۱۰ نفر برتر هر لحظه در دسترس است.
  • آخر هر هفته‌ی معاملاتی (جمعه) تورنومنت خودکار ریست می‌شود و گزارش
    ۱۰ نفر برتر (برای هماهنگی جایزه‌ی پراپ به ۳ نفر اول و جایزه‌ی نقدی
    به بقیه) مستقیم برای ادمین(ها) ارسال می‌شود.

هسته‌ی دریافت قیمت طلا (چند منبع با فال‌بک) و ساختار CloudStorage
(ذخیره‌سازی ابری روی JSONBin) از سورس اصلی کاربر حفظ شده است.
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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# می‌توان چند ادمین با کاما جدا کرد: ADMIN_IDS="605611554,123456789"
_admin_raw = os.environ.get("ADMIN_IDS", os.environ.get("ADMIN_ID", "605611554"))
ADMIN_IDS = {int(x) for x in _admin_raw.replace(" ", "").split(",") if x.strip()}

# آیدی عددی کانال (مثلاً -1001234567890) یا یوزرنیم آن (مثلاً @mychannel)
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")

POINTS_PER_CORRECT   = int(os.environ.get("POINTS_PER_CORRECT", 10))
REF_POINTS_REFERRER  = int(os.environ.get("REF_POINTS_REFERRER", 20))
REF_POINTS_INVITEE   = int(os.environ.get("REF_POINTS_INVITEE", 10))
DEFAULT_ROUND_MIN    = int(os.environ.get("DEFAULT_ROUND_MINUTES", 60))
TALLY_EDIT_EVERY_SEC = 25          # حداقل فاصله‌ی زمانی بین آپدیت‌های شمارش رأی
TOP_REPORT_COUNT     = 10          # تعداد نفراتی که در گزارش پایان‌هفته به ادمین می‌رود
PROP_PRIZE_RANKS     = 3           # چند نفر اول، جایزه‌ی پراپ می‌گیرند (بقیه تا رتبه ۱۰ نقدی)

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))


def now_ir() -> datetime:
    return datetime.now(IRAN_TZ)


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


# ══════════════════════════════════════════════════════════════════════
# GOLD PRICE FEED  (حفظ‌شده از هسته‌ی اصلی سورس قبلی — چند منبع + فال‌بک)
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

_price_cache: Optional[tuple] = None   # (price, ts)
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
# CLOUD STORAGE  (روی JSONBin — همان الگوی سورس اصلی)
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
            "users": {},                 # uid -> profile
            "active_round": None,        # دور در حال اجرا
            "next_round_id": 1,
            "rounds_history": [],        # آخرین دورهای برگزارشده (حداکثر ۵۰ تا)
            "leaderboard_history": [],   # آرشیو لیدربرد هفته‌های قبل
            "last_lb_reset": None,
            "last_updated": None,
        }

    # ---------- persistence ----------
    def load(self):
        if not (self._key and self._bin):
            log.warning("JSONBin تنظیم نشده — داده‌ها فقط در حافظه نگه داشته می‌شوند و با ریست سرور از بین می‌روند.")
            return
        try:
            r = requests.get(f"{self._url}/{self._bin}/latest", headers=self._hdrs, timeout=15)
            r.raise_for_status()
            remote = r.json().get("record", {})
            with self._lock:
                for k, v in remote.items():
                    self._data[k] = v
                self._data.setdefault("users", {})
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
                    log.info(f"JSONBin جدید ساخته شد — این مقدار را در JSONBIN_BIN_ID قرار بده: {bid}")
            r.raise_for_status()
            self._dirty = False
        except Exception as e:
            log.error(f"CloudStorage save error: {e}")

    # ---------- users ----------
    def ensure_user(self, uid: int, username: str = "") -> dict:
        key = str(uid)
        with self._lock:
            if key not in self._data["users"]:
                ref_code = hashlib.md5((key + str(time.time())).encode()).hexdigest()[:8]
                self._data["users"][key] = {
                    "username": username or "", "joined": now_ir().isoformat(),
                    "ref": ref_code, "referred_by": None,
                    "points": 0, "total_points": 0,
                    "correct": 0, "predictions": 0, "invites": 0,
                }
                self._dirty = True
            elif username and self._data["users"][key].get("username") != username:
                self._data["users"][key]["username"] = username
                self._dirty = True
            return dict(self._data["users"][key])

    def get_user(self, uid: int) -> Optional[dict]:
        with self._lock:
            u = self._data["users"].get(str(uid))
            return dict(u) if u else None

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

    # ---------- points ----------
    def award_correct_prediction(self, uid: int):
        key = str(uid)
        with self._lock:
            u = self._data["users"].setdefault(key, {})
            u["points"] = u.get("points", 0) + POINTS_PER_CORRECT
            u["total_points"] = u.get("total_points", 0) + POINTS_PER_CORRECT
            u["correct"] = u.get("correct", 0) + 1
            self._dirty = True

    def inc_prediction_count(self, uid: int):
        key = str(uid)
        with self._lock:
            u = self._data["users"].setdefault(key, {})
            u["predictions"] = u.get("predictions", 0) + 1
            self._dirty = True

    # ---------- leaderboard ----------
    def get_weekly_leaderboard(self, top: int = 10) -> list:
        with self._lock:
            users = self._data["users"]
            ranked = sorted(users.items(), key=lambda x: x[1].get("points", 0), reverse=True)
            ranked = [(uid, u) for uid, u in ranked if u.get("points", 0) > 0][:top]
            return [
                {"rank": i, "uid": uid, "username": u.get("username") or f"کاربر {uid[-4:]}",
                 "points": u.get("points", 0)}
                for i, (uid, u) in enumerate(ranked, 1)
            ]

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
            self._data["last_lb_reset"] = date_key
            self._dirty = True

    # ---------- rounds ----------
    def get_active_round(self) -> Optional[dict]:
        with self._lock:
            rnd = self._data.get("active_round")
            return dict(rnd) if rnd else None

    def create_round(self, start_price: float, duration_min: int, chat_id, message_id: int) -> dict:
        with self._lock:
            rid = self._data.get("next_round_id", 1)
            self._data["next_round_id"] = rid + 1
            start = now_ir()
            rnd = {
                "id": rid,
                "start_price": start_price,
                "start_time": start.isoformat(),
                "duration_min": duration_min,
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

    def record_vote(self, round_id: int, uid: int, direction: str, username: str) -> bool:
        with self._lock:
            rnd = self._data.get("active_round")
            if not rnd or rnd["id"] != round_id or rnd["status"] != "open":
                return False
            is_new = str(uid) not in rnd["predictions"]
            rnd["predictions"][str(uid)] = {
                "dir": direction, "username": username or "", "at": now_ir().isoformat(),
            }
            self._dirty = True
            return is_new

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
# KEYBOARDS
# ══════════════════════════════════════════════════════════════════════
def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 پروفایل من", callback_data="menu:profile"),
         InlineKeyboardButton("🏆 لیدربرد", callback_data="menu:leaderboard")],
        [InlineKeyboardButton("🎁 دعوت دوستان", callback_data="menu:invite")],
        [InlineKeyboardButton("ℹ️ راهنما", callback_data="menu:help")],
    ])


def kb_round(round_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬆️ بالا", callback_data=f"vote:{round_id}:up"),
        InlineKeyboardButton("⬇️ پایین", callback_data=f"vote:{round_id}:down"),
    ]])


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="menu:home")]])


# ══════════════════════════════════════════════════════════════════════
# TEXT BUILDERS
# ══════════════════════════════════════════════════════════════════════
def _fmt_price(p: float) -> str:
    return f"{p:,.2f}"


def build_welcome_text() -> str:
    return (
        "🥇 <b>به تورنومنت پیش‌بینی طلا خوش اومدی!</b>\n\n"
        "هر دور، قیمت انس جهانی طلا (XAU/USD) رو برای بازه‌ی مشخصی پیش‌بینی می‌کنی: "
        "<b>بالا میره یا پایین؟</b>\n"
        f"اگه درست حدس بزنی، <b>{POINTS_PER_CORRECT} امتیاز</b> می‌گیری.\n\n"
        "🎁 با دعوت دوستات هم امتیاز اضافه می‌گیری.\n"
        "🏆 هر هفته آخر روز جمعه، تورنومنت ریست می‌شه و به نفرات برتر جایزه تعلق می‌گیره:\n"
        f"  • رتبه ۱ تا {PROP_PRIZE_RANKS}: 🏅 جایزه پراپ\n"
        f"  • رتبه {PROP_PRIZE_RANKS + 1} تا {TOP_REPORT_COUNT}: 💵 جایزه نقدی\n\n"
        "منتظر باش تا دور بعدی پیش‌بینی باز بشه 👇"
    )


def build_round_text(rnd: dict, status: str, up: int, down: int,
                      end_price: Optional[float] = None, direction: Optional[str] = None,
                      winners_count: Optional[int] = None) -> str:
    a_emoji = "🥇"
    start_p = _fmt_price(rnd["start_price"])
    lock_dt = datetime.fromisoformat(rnd["lock_time"])
    total = up + down

    if status == "open":
        return (
            f"{a_emoji} <b>دور پیش‌بینی طلا #{rnd['id']}</b>\n\n"
            f"💰 قیمت شروع: <code>{start_p}</code>\n"
            f"⏰ بسته‌شدن رأی‌گیری: <code>{lock_dt.strftime('%H:%M')}</code> (به وقت ایران)\n\n"
            f"طلا تا اون موقع بالا می‌ره یا پایین؟ رأی بده 👇\n\n"
            f"📊 مشارکت تا این لحظه: {total} نفر (⬆️ {up} | ⬇️ {down})\n"
            f"✅ پیش‌بینی درست = <b>{POINTS_PER_CORRECT} امتیاز</b>"
        )
    if status == "locked":
        return (
            f"{a_emoji} <b>دور پیش‌بینی طلا #{rnd['id']} — رأی‌گیری بسته شد ⏳</b>\n\n"
            f"💰 قیمت شروع: <code>{start_p}</code>\n"
            f"📊 مجموع شرکت‌کننده: {total} نفر (⬆️ {up} | ⬇️ {down})\n\n"
            f"در انتظار قیمت نهایی برای اعلام نتیجه..."
        )
    # resolved
    arrow = {"up": "📈 بالا", "down": "📉 پایین", "flat": "➖ بدون تغییر"}[direction]
    end_p = _fmt_price(end_price)
    result_line = (
        f"نتیجه: <b>{arrow}</b>  ( {start_p} ← {end_p} )"
        if direction != "flat" else
        f"نتیجه: <b>{arrow}</b> — این دور باطل شد و امتیازی داده نشد."
    )
    return (
        f"{a_emoji} <b>دور پیش‌بینی طلا #{rnd['id']} — پایان یافت ✅</b>\n\n"
        f"💰 شروع: <code>{start_p}</code>   →   پایان: <code>{end_p}</code>\n"
        f"{result_line}\n\n"
        f"📊 مجموع شرکت‌کننده: {total} نفر (⬆️ {up} | ⬇️ {down})\n"
        f"🏆 برندگان این دور: <b>{winners_count or 0} نفر</b> (هرکدام +{POINTS_PER_CORRECT} امتیاز)\n\n"
        f"امتیاز و رتبه‌ات رو با /profile ببین."
    )


def build_profile_text(uid: int, u: dict) -> str:
    rank_line = ""
    lb = cloud.get_weekly_leaderboard(top=1000)
    for row in lb:
        if row["uid"] == str(uid):
            rank_line = f"📍 رتبه‌ات در تورنومنت این هفته: <b>#{row['rank']}</b>\n"
            break
    acc = f"{u.get('correct', 0)}/{u.get('predictions', 0)}" if u.get("predictions") else "—"
    return (
        "👤 <b>پروفایل تو</b>\n\n"
        f"🥇 امتیاز این هفته: <b>{u.get('points', 0)}</b>\n"
        f"⭐️ امتیاز کل (تاریخچه): <b>{u.get('total_points', 0)}</b>\n"
        f"{rank_line}"
        f"🎯 پیش‌بینی درست: <b>{acc}</b>\n"
        f"🎁 تعداد دعوت موفق: <b>{u.get('invites', 0)}</b>"
    )


def build_leaderboard_text() -> str:
    lb = cloud.get_weekly_leaderboard(top=10)
    if not lb:
        return "🏆 <b>لیدربرد این هفته</b>\n\nهنوز کسی امتیازی کسب نکرده — اولین نفر باش! 🔥"
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = ["🏆 <b>لیدربرد تورنومنت این هفته</b>\n"]
    for row in lb:
        tag = medals.get(row["rank"], f"{row['rank']}.")
        lines.append(f"{tag} {row['username']} — <b>{row['points']}</b> امتیاز")
    lines.append(f"\n🔔 جمعه پایان هفته: رتبه ۱ تا {PROP_PRIZE_RANKS} 🏅 جایزه پراپ، مابقی تا ۱۰ 💵 جایزه نقدی.")
    return "\n".join(lines)


def build_referral_text(uid: int, bot_username: str) -> str:
    u = cloud.ensure_user(uid)
    link = f"https://t.me/{bot_username}?start=ref_{u['ref']}"
    return (
        "🎁 <b>دعوت دوستان</b>\n\n"
        f"با لینک اختصاصی زیر دوستاتو دعوت کن:\n<code>{link}</code>\n\n"
        f"✅ به ازای هر دعوت موفق: <b>{REF_POINTS_REFERRER} امتیاز</b> برای تو\n"
        f"🎉 دوستت هم با ورود از لینکت: <b>{REF_POINTS_INVITEE} امتیاز</b> هدیه می‌گیره\n\n"
        f"👥 تعداد دعوت‌های موفق تو: <b>{u.get('invites', 0)}</b>"
    )


def build_help_text(admin: bool) -> str:
    txt = (
        "ℹ️ <b>راهنمای ربات</b>\n\n"
        "/start — شروع و ثبت‌نام\n"
        "/profile — مشاهده امتیاز و رتبه‌ات\n"
        "/leaderboard — لیدربرد ۱۰ نفر برتر\n"
        "/invite — لینک دعوت دوستان\n"
        "/help — همین راهنما\n\n"
        "طرز بازی: هروقت یک دور پیش‌بینی در کانال باز بشه، با زدن دکمه‌ی «بالا» یا «پایین» "
        "پیش‌بینی‌ات رو ثبت کن. اگه جهت حرکت قیمت طلا رو درست حدس بزنی، امتیاز می‌گیری."
    )
    if admin:
        txt += (
            "\n\n👑 <b>دستورات ادمین</b>\n"
            "/newround [دقیقه] — باز کردن دور جدید (پیش‌فرض ۶۰ دقیقه)\n"
            "/cancelround — باطل کردن دور فعال بدون اعطای امتیاز\n"
            "/status — وضعیت دور فعال"
        )
    return txt


# ══════════════════════════════════════════════════════════════════════
# ROUND HELPERS
# ══════════════════════════════════════════════════════════════════════
def _vote_counts(rnd: dict) -> tuple:
    up = sum(1 for v in rnd["predictions"].values() if v["dir"] == "up")
    down = len(rnd["predictions"]) - up
    return up, down


async def _edit_round_message(app: Application, rnd: dict, status: str,
                               end_price=None, direction=None, winners_count=None):
    up, down = _vote_counts(rnd)
    text = build_round_text(rnd, status, up, down, end_price, direction, winners_count)
    markup = kb_round(rnd["id"]) if status == "open" else None
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


async def _resolve_round(app: Application, rnd: dict):
    price_now = get_gold_price(force=True)
    if price_now is None:
        # قیمت در دسترس نیست — یک دقیقه دیگر دوباره تلاش می‌شود
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

    cloud.finish_round(rnd["id"], price_now, direction, winners)
    await _edit_round_message(app, rnd, "resolved", price_now, direction, len(winners))

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
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    username = user.username or user.first_name or ""
    is_new = cloud.get_user(uid) is None
    cloud.ensure_user(uid, username)

    if is_new and ctx.args:
        arg = ctx.args[0]
        if arg.startswith("ref_"):
            ref_code = arg[4:]
            ref_uid = cloud.find_uid_by_ref(ref_code)
            if ref_uid:
                ok = cloud.apply_referral(uid, ref_uid)
                if ok:
                    try:
                        await ctx.bot.send_message(
                            chat_id=ref_uid, parse_mode=ParseMode.HTML,
                            text=f"🎉 یک نفر با لینک دعوت تو وارد شد! +{REF_POINTS_REFERRER} امتیاز گرفتی.")
                    except Exception:
                        pass

    await update.effective_message.reply_text(
        build_welcome_text(), parse_mode=ParseMode.HTML, reply_markup=kb_main())
    cloud.save()


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        build_help_text(is_admin(update.effective_user.id)),
        parse_mode=ParseMode.HTML, reply_markup=kb_back())


async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = cloud.ensure_user(uid, update.effective_user.username or update.effective_user.first_name or "")
    await update.effective_message.reply_text(
        build_profile_text(uid, u), parse_mode=ParseMode.HTML, reply_markup=kb_back())


async def cmd_leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        build_leaderboard_text(), parse_mode=ParseMode.HTML, reply_markup=kb_back())


async def cmd_invite(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bot_username = (await ctx.bot.get_me()).username
    await update.effective_message.reply_text(
        build_referral_text(update.effective_user.id, bot_username),
        parse_mode=ParseMode.HTML, reply_markup=kb_back())


# ---------- admin ----------
async def cmd_newround(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    if not CHANNEL_ID:
        await update.effective_message.reply_text("⚠️ CHANNEL_ID تنظیم نشده. آیدی کانال رو در تنظیمات ربات وارد کن.")
        return
    if cloud.get_active_round():
        await update.effective_message.reply_text("⚠️ یک دور در حال اجراست. اول با /status وضعیتش رو ببین یا با /cancelround باطلش کن.")
        return

    minutes = DEFAULT_ROUND_MIN
    if ctx.args:
        try:
            minutes = max(1, int(ctx.args[0]))
        except ValueError:
            await update.effective_message.reply_text("⚠️ فرمت درست: /newround 60")
            return

    price = get_gold_price(force=True)
    if price is None:
        await update.effective_message.reply_text("❌ در حال حاضر قیمت طلا در دسترس نیست، دوباره تلاش کن.")
        return

    placeholder = await ctx.bot.send_message(
        chat_id=CHANNEL_ID, text="⏳ در حال باز کردن دور جدید...")
    rnd = cloud.create_round(price, minutes, CHANNEL_ID, placeholder.message_id)
    await _edit_round_message(ctx.application, rnd, "open")
    await update.effective_message.reply_text(
        f"✅ دور #{rnd['id']} با قیمت شروع {_fmt_price(price)} برای {minutes} دقیقه در کانال باز شد.")
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
            text=f"🚫 دور #{rnd['id']} توسط ادمین باطل شد. امتیازی اعطا نشد.",
            parse_mode=ParseMode.HTML)
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
    lock_dt = datetime.fromisoformat(rnd["lock_time"])
    await update.effective_message.reply_text(
        f"دور #{rnd['id']} فعال است.\n"
        f"شروع: {_fmt_price(rnd['start_price'])}\n"
        f"بسته‌شدن: {lock_dt.strftime('%H:%M:%S')}\n"
        f"رأی‌ها: ⬆️ {up} | ⬇️ {down}")


# ══════════════════════════════════════════════════════════════════════
# CALLBACK QUERY HANDLER
# ══════════════════════════════════════════════════════════════════════
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    uid = q.from_user.id
    username = q.from_user.username or q.from_user.first_name or ""
    cloud.ensure_user(uid, username)

    if data.startswith("vote:"):
        try:
            _, rid_s, direction = data.split(":")
            rid = int(rid_s)
        except Exception:
            await q.answer(); return
        rnd = cloud.get_active_round()
        if not rnd or rnd["id"] != rid or rnd["status"] != "open":
            await q.answer("⛔️ این دور دیگه بسته شده.", show_alert=True)
            return
        cloud.record_vote(rid, uid, direction, username)
        dir_fa = "بالا ⬆️" if direction == "up" else "پایین ⬇️"
        await q.answer(f"✅ پیش‌بینی‌ات ثبت شد: {dir_fa}")
        return

    if data == "menu:profile":
        u = cloud.ensure_user(uid, username)
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
        await q.edit_message_text(build_welcome_text(), parse_mode=ParseMode.HTML, reply_markup=kb_main())
        return

    await q.answer()


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    log.error(f"error: {ctx.error}", exc_info=ctx.error)


# ══════════════════════════════════════════════════════════════════════
# BACKGROUND LOOPS
# ══════════════════════════════════════════════════════════════════════
async def round_watcher(app: Application):
    while True:
        try:
            await asyncio.sleep(15)
            rnd = cloud.get_active_round()
            if not rnd or rnd["status"] != "open":
                continue
            lock_dt = datetime.fromisoformat(rnd["lock_time"])
            if now_ir() >= lock_dt:
                cloud.set_round_status(rnd["id"], "locked")
                await _edit_round_message(app, rnd, "locked")
                await _resolve_round(app, rnd)
            else:
                last_edit = rnd.get("last_edit_ts", 0)
                if time.time() - last_edit >= TALLY_EDIT_EVERY_SEC:
                    cloud.touch_round_edit(rnd["id"])
                    await _edit_round_message(app, rnd, "open")
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"round_watcher error: {e}")


async def weekly_reset_watcher(app: Application):
    while True:
        try:
            await asyncio.sleep(600)  # هر ۱۰ دقیقه بررسی می‌شود
            today = now_ir().date()
            if today.weekday() != 5:  # ۵ = شنبه (یعنی جمعه تازه تمام شده)
                continue
            if cloud.get_last_reset() == today.isoformat():
                continue
            snapshot = cloud.get_weekly_leaderboard(top=TOP_REPORT_COUNT)
            if snapshot:
                lines = ["🏁 <b>پایان هفته‌ی تورنومنت — گزارش برای هماهنگی جوایز</b>\n"]
                for row in snapshot:
                    tag = "🏅 پراپ" if row["rank"] <= PROP_PRIZE_RANKS else "💵 نقدی"
                    lines.append(f"{row['rank']}. {row['username']} — {row['points']} امتیاز ({tag})")
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
    threading.Thread(target=_run_flask, daemon=True).start()
    asyncio.create_task(round_watcher(app))
    asyncio.create_task(weekly_reset_watcher(app))
    asyncio.create_task(autosave_loop(app))
    log.info("Gold Prediction Bot ready ✅")


async def on_shutdown(app: Application):
    cloud.save(force=True)
    log.info("shutdown")


# ══════════════════════════════════════════════════════════════════════
# FLASK KEEP-ALIVE (برای هاست‌هایی مثل Railway/Render که نیاز به پورت باز دارند)
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)


@flask_app.route("/")
def root():
    n_users = len(cloud._data.get("users", {}))
    return f"Gold Prediction Bot | users={n_users} | {now_ir().strftime('%Y-%m-%d %H:%M:%S')}"


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
    app.add_handler(CommandHandler("newround", cmd_newround))
    app.add_handler(CommandHandler("cancelround", cmd_cancelround))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(on_callback))
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
