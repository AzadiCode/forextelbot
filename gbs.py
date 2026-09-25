#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GramSaz WebApp Edition — پلتفرم ربات‌ساز با رابط وب
════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import os, sys, json, time, hmac, hashlib, asyncio, logging, threading, copy, secrets, re
import random
from datetime import datetime, timedelta, timezone
from typing import Optional
from functools import wraps
from html import escape as h_escape
import urllib.parse

# ── Flask ──
from flask import Flask, request, jsonify, g

# ── HTTP client ──
import requests as req_lib

# ── python-telegram-bot ──
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, WebAppInfo, BotCommand
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters,
)

# ── MongoDB ──
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError


# ══════════════════════════════════════════════════════════════════════
# لاگ‌گذاری
# ══════════════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO, stream=sys.stdout,
)
log = logging.getLogger("GramSazWA")


# ══════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════
PLATFORM_BOT_TOKEN = os.getenv("PLATFORM_BOT_TOKEN", "").strip()
MONGODB_URI        = os.getenv("MONGODB_URI", "").strip()
WEBAPP_URL         = os.getenv("WEBAPP_URL", "https://botsazgram.onrender.com").strip().rstrip("/")
WEBHOOK_URL        = os.getenv("WEBHOOK_URL", "").strip().rstrip("/")
API_HOST           = os.getenv("API_HOST", "0.0.0.0")
API_PORT           = int(os.getenv("PORT", os.getenv("API_PORT", "8000")))

ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "605611554").replace(" ", "").split(",")
    if x.strip().lstrip("-").isdigit()
}

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))
INIT_DATA_MAX_AGE = int(os.getenv("INIT_DATA_MAX_AGE", "86400"))

# ربات مادر — بعداً در thread خودش راه‌اندازی می‌شود
mother_bot_instance = None
mother_bot_loop = None

MEDIA_TYPES = ("photo", "video", "audio", "animation", "document")

if not PLATFORM_BOT_TOKEN:
    log.critical("PLATFORM_BOT_TOKEN تنظیم نشده!")
    sys.exit(1)

if not WEBAPP_URL:
    log.warning("WEBAPP_URL تنظیم نشده — دکمه WebApp کار نخواهد کرد")


# ══════════════════════════════════════════════════════════════════════
# توابع کمکی
# ══════════════════════════════════════════════════════════════════════
def now_ir() -> datetime:
    return datetime.now(IRAN_TZ)

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def verify_telegram_init_data(init_data: str, bot_token: str) -> Optional[dict]:
    try:
        parsed_pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
        parsed = {k: v for k, v in parsed_pairs}
        
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            log.warning("❌ Hash در initData یافت نشد")
            return None
            
        auth_date = parsed.get("auth_date")
        if auth_date:
            try:
                if time.time() - int(auth_date) > INIT_DATA_MAX_AGE:
                    log.warning("❌ initData قدیمی است — دوباره پنل را باز کنید")
                    return None
            except (TypeError, ValueError):
                return None

        data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected_hash = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
        
        if not hmac.compare_digest(expected_hash, received_hash):
            log.warning("❌ هش‌ها مطابقت ندارند!")
            return None
            
        user_str = parsed.get("user", "{}")
        return json.loads(user_str)
        
    except Exception as e:
        log.error(f"❌ خطا در پردازش initData: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
# Storage — MongoDB sync (pymongo) + In-Memory Fallback
# ══════════════════════════════════════════════════════════════════════
class Storage:
    def __init__(self):
        self._is_memory = False
        if MONGODB_URI:
            try:
                self._client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=8000)
                db = self._client.get_default_database()
                self._users = db["users"]
                self._bots  = db["bots"]
                self._users.create_index("uid", unique=True)
                self._bots.create_index("bot_id", unique=True)
                self._bots.create_index("owner_uid")
                log.info("✅ MongoDB متصل شد")
            except Exception as e:
                log.warning(f"⚠️ خطا در اتصال به MongoDB: {e}. از حافظه موقت استفاده می‌شود.")
                self._use_memory()
        else:
            log.warning("⚠️ MONGODB_URI تنظیم نشده است! از حافظه موقت استفاده می‌شود.")
            self._use_memory()

    def _use_memory(self):
        self._is_memory = True
        self._users = {}  # uid -> user_dict
        self._bots = {}   # bot_id -> bot_dict

    def _find_user_by(self, query: dict) -> Optional[dict]:
        """جستجوی کاربر با query در حالت Memory یا MongoDB"""
        if self._is_memory:
            for user in self._users.values():
                match = all(user.get(k) == v for k, v in query.items())
                if match:
                    return user.copy()
            return None
        else:
            doc = self._users.find_one(query)
            if doc:
                doc = dict(doc)
                doc.pop("_id", None)
            return doc

    # ── کاربران ──────────────────────────────────────────────────────
    def ensure_user(self, uid: int, username: str = "", full_name: str = "") -> dict:
        if self._is_memory:
            if uid in self._users:
                doc = self._users[uid]
                if username and doc.get("username") != username:
                    doc["username"] = username
                if full_name and doc.get("full_name") != full_name:
                    doc["full_name"] = full_name
                return copy.deepcopy(doc)
            referral_code = secrets.token_urlsafe(8)
            new_user = {
                "uid": uid,
                "username": username,
                "full_name": full_name,
                "is_admin": uid in ADMIN_IDS,
                "is_banned": False,
                "created_at": now_ir().isoformat(),
                "bots_created": [],
                "coins": 0,
                "referral_code": referral_code,
                "referred_by": None,
                "referral_count": 0,
            }
            self._users[uid] = new_user
            return new_user
        else:
            doc = self._users.find_one({"uid": uid})
            if doc:
                update = {}
                if username and doc.get("username") != username:
                    update["username"] = username
                if full_name and doc.get("full_name") != full_name:
                    update["full_name"] = full_name
                if "coins" not in doc:
                    update["coins"] = 0
                if "referral_code" not in doc:
                    update["referral_code"] = secrets.token_urlsafe(8)
                if "referred_by" not in doc:
                    update["referred_by"] = None
                if "referral_count" not in doc:
                    update["referral_count"] = 0
                if update:
                    self._users.update_one({"uid": uid}, {"$set": update})
                    doc.update(update)
                doc.pop("_id", None)
                return doc
            referral_code = secrets.token_urlsafe(8)
            new_user = {
                "uid": uid,
                "username": username,
                "full_name": full_name,
                "is_admin": uid in ADMIN_IDS,
                "is_banned": False,
                "created_at": now_ir().isoformat(),
                "bots_created": [],
                "coins": 0,
                "referral_code": referral_code,
                "referred_by": None,
                "referral_count": 0,
            }
            try:
                self._users.insert_one(new_user)
            except DuplicateKeyError:
                return self.get_user(uid)
            new_user.pop("_id", None)
            return new_user

    def get_user(self, uid: int) -> Optional[dict]:
        if self._is_memory:
            user = self._users.get(uid)
            return copy.deepcopy(user) if user else None
        doc = self._users.find_one({"uid": uid})
        if doc:
            doc.pop("_id", None)
        return doc

    def get_all_users(self) -> list:
        if self._is_memory:
            return [copy.deepcopy(u) for u in self._users.values()]
        return list(self._users.find({}, {"_id": 0}))

    # ── ربات‌ها ──────────────────────────────────────────────────────
    @staticmethod
    def _bot_defaults(b: dict) -> dict:
        b.setdefault("is_active", True)
        b.setdefault("bot_username", "")
        b.setdefault("commands", {
            "start": {
                "response": "👋 سلام! به ربات ما خوش آمدید.",
                "buttons": None,
                "photo": None,
                "callbacks": {},
            }
        })
        b.setdefault("force_join_channel", None)
        b.setdefault("bot_users", [])
        # ── تنظیمات جدید (سازگار با ربات‌های قبلی) ──
        b.setdefault("welcome_message", "")   # پیام خوش‌آمد برای عضو جدید
        b.setdefault("unknown_reply", "")      # پاسخ خودکار به دستور ناشناخته
        b.setdefault("auto_menu", True)        # همگام‌سازی منوی تلگرام
        b.setdefault("rules", [])              # قوانین پاسخ خودکار با کلمه کلیدی
        b.setdefault("flow", None)             # گراف جریان (نودها + سیم‌ها) — موتور واقعی
        b.setdefault("ticket_admins", [])      # پذیرندگان تیکت (UID)
        b.setdefault("pending_tickets", {})    # uid -> {node, expires, admin_input}
        b.setdefault("tickets", {})            # tid  -> {uid, at, text}
        b.setdefault("bot_user_info", {})      # uid -> {name, username, joined}
        b.setdefault("stats", {
            "messages_received": 0,
            "messages_sent": 0,
            "unique_users": 0,
            "broadcasts_sent": 0,
        })
        b.setdefault("daily_stats", {})
        return b

    def create_bot(self, owner_uid: int, bot_token: str, bot_username: str) -> dict:
        bot_id = hashlib.md5(f"{owner_uid}{bot_token}{time.time()}".encode()).hexdigest()[:12]
        bot_rec = {
            "bot_id": bot_id,
            "owner_uid": owner_uid,
            "bot_token": bot_token,
            "bot_username": bot_username,
            "created_at": now_ir().isoformat(),
            "last_activity": None,
        }
        self._bot_defaults(bot_rec)
        
        if self._is_memory:
            self._bots[bot_id] = copy.deepcopy(bot_rec)
            if owner_uid in self._users:
                if bot_id not in self._users[owner_uid].get("bots_created", []):
                    self._users[owner_uid]["bots_created"].append(bot_id)
        else:
            self._bots.insert_one(bot_rec)
            self._users.update_one(
                {"uid": owner_uid},
                {"$addToSet": {"bots_created": bot_id}}
            )
        bot_rec.pop("_id", None)
        return bot_rec

    def get_bot(self, bot_id: str) -> Optional[dict]:
        if self._is_memory:
            bot = self._bots.get(bot_id)
            return copy.deepcopy(bot) if bot else None
        doc = self._bots.find_one({"bot_id": bot_id})
        if doc:
            doc.pop("_id", None)
        return doc

    def get_user_bots(self, uid: int) -> list:
        if self._is_memory:
            return [copy.deepcopy(b) for b in self._bots.values() if b.get("owner_uid") == uid]
        return list(self._bots.find({"owner_uid": uid}, {"_id": 0}))

    def get_all_bots(self) -> list:
        if self._is_memory:
            return [copy.deepcopy(b) for b in self._bots.values()]
        return list(self._bots.find({}, {"_id": 0}))

    def delete_bot(self, bot_id: str, owner_uid: int) -> bool:
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if bot and bot.get("owner_uid") == owner_uid:
                del self._bots[bot_id]
                if owner_uid in self._users and bot_id in self._users[owner_uid].get("bots_created", []):
                    self._users[owner_uid]["bots_created"].remove(bot_id)
                return True
            return False
            
        res = self._bots.delete_one({"bot_id": bot_id, "owner_uid": owner_uid})
        if res.deleted_count:
            self._users.update_one(
                {"uid": owner_uid},
                {"$pull": {"bots_created": bot_id}}
            )
            return True
        return False

    def set_bot_active(self, bot_id: str, active: bool):
        if self._is_memory:
            if bot_id in self._bots:
                self._bots[bot_id]["is_active"] = active
        else:
            self._bots.update_one({"bot_id": bot_id}, {"$set": {"is_active": active}})

    def update_bot_username(self, bot_id: str, username: str):
        if self._is_memory:
            if bot_id in self._bots:
                self._bots[bot_id]["bot_username"] = username
        else:
            self._bots.update_one({"bot_id": bot_id}, {"$set": {"bot_username": username}})

    def set_command(self, bot_id: str, cmd_name: str, response: str,
                    buttons=None, photo=None, media=None, description: str = ""):
        """ذخیره دستور با پشتیبانی از media جدید و photo قدیمی + توضیح"""

        # اگر photo قدیمی ارسال شد و media نبود، تبدیل کنیم
        if photo and not media:
            media = {"type": "photo", "url": photo}
        elif media and not photo:
            # media جدید
            pass
        elif media and photo:
            # هر دو ارسال شده، media اولویت دارد
            pass

        if self._is_memory:
            bot = self._bots.get(bot_id)
            if bot:
                if "commands" not in bot:
                    bot["commands"] = {}
                existing = bot["commands"].get(cmd_name, {})
                bot["commands"][cmd_name] = {
                    "response": response,
                    "buttons": buttons,
                    "photo": photo if photo is not None else existing.get("photo"),
                    "media": media if media is not None else existing.get("media"),
                    "callbacks": existing.get("callbacks", {}),
                    "description": description if description != "" else existing.get("description", ""),
                }
        else:
            doc = self._bots.find_one({"bot_id": bot_id}, {f"commands.{cmd_name}": 1})
            existing = {}
            if doc and "commands" in doc:
                existing = doc["commands"].get(cmd_name, {})
            new_cmd = {
                "response": response,
                "buttons": buttons,
                "photo": photo if photo is not None else existing.get("photo"),
                "media": media if media is not None else existing.get("media"),
                "callbacks": existing.get("callbacks", {}),
                "description": description if description != "" else existing.get("description", ""),
            }
            self._bots.update_one({"bot_id": bot_id}, {"$set": {f"commands.{cmd_name}": new_cmd}})

    def set_bot_config(self, bot_id: str, cfg: dict):
        """تنظیمات عمومی ربات: welcome_message / unknown_reply / auto_menu / rules"""
        allowed = ("welcome_message", "unknown_reply", "auto_menu", "rules",
                   "flow", "ticket_admins")
        patch = {k: cfg[k] for k in allowed if k in cfg}
        if not patch:
            return
        if self._is_memory:
            if bot_id in self._bots:
                self._bots[bot_id].update(patch)
        else:
            self._bots.update_one({"bot_id": bot_id}, {"$set": patch})

    @staticmethod
    def match_rule(rules, text: str):
        """اولین قانونی که با پیام کاربر تطبیق دارد را برمی‌گرداند"""
        t = (text or "").strip().lower()
        if not t:
            return None
        for r in rules or []:
            mode = r.get("mode", "contains")
            for kw in r.get("keywords", []):
                k = (kw or "").strip().lower()
                if not k:
                    continue
                if mode == "exact" and t == k:
                    return r
                if mode == "contains" and k in t:
                    return r
                if mode == "regex":
                    try:
                        if re.search(kw, text or "", re.IGNORECASE):
                            return r
                    except re.error:
                        pass
        return None

    def set_bot_user_info(self, bot_id: str, uid: int, full_name: str = "", username: str = ""):
        key = str(uid)
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if not bot:
                return
            info = bot.setdefault("bot_user_info", {})
            if key in info:
                if full_name: info[key]["name"] = full_name
                if username: info[key]["username"] = username
            else:
                info[key] = {"name": full_name, "username": username,
                             "joined": now_ir().isoformat()}
        else:
            set_fields = {}
            if full_name:
                set_fields[f"bot_user_info.{key}.name"] = full_name
            if username:
                set_fields[f"bot_user_info.{key}.username"] = username
            set_fields.setdefault(f"bot_user_info.{key}.joined", now_ir().isoformat())
            self._bots.update_one({"bot_id": bot_id}, {"$set": set_fields})

    def get_bot_users(self, bot_id: str) -> list:
        """لیست کاربران ربات با اطلاعات (id, name, username)"""
        if self._is_memory:
            bot = self._bots.get(bot_id, {})
            uids = bot.get("bot_users", [])
            info = bot.get("bot_user_info", {})
        else:
            doc = self._bots.find_one({"bot_id": bot_id},
                                      {"bot_users": 1, "bot_user_info": 1}) or {}
            uids = doc.get("bot_users", [])
            info = doc.get("bot_user_info", {})
        out = []
        for uid in uids:
            meta = info.get(str(uid), {})
            out.append({"uid": uid, "name": meta.get("name", ""),
                        "username": meta.get("username", ""),
                        "joined": meta.get("joined", "")})
        out.sort(key=lambda u: u.get("joined") or "", reverse=True)
        return out

    def record_daily(self, bot_id: str, key: str, amount: int = 1):
        """ثبت آمار روزانه: new_users / messages_in / messages_out"""
        day = now_ir().strftime("%Y-%m-%d")
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if not bot: return
            ds = bot.setdefault("daily_stats", {})
            day_data = ds.setdefault(day, {})
            day_data[key] = day_data.get(key, 0) + amount
        else:
            self._bots.update_one(
                {"bot_id": bot_id},
                {"$inc": {f"daily_stats.{day}.{key}": amount}}
            )

    def delete_command(self, bot_id: str, cmd_name: str):
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if bot and cmd_name in bot.get("commands", {}):
                del bot["commands"][cmd_name]
        else:
            self._bots.update_one({"bot_id": bot_id}, {"$unset": {f"commands.{cmd_name}": ""}})

    def set_command_callback(self, bot_id: str, cmd_name: str, cb_id: str, response: str):
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if bot:
                if cmd_name in bot.get("commands", {}):
                    if "callbacks" not in bot["commands"][cmd_name]:
                        bot["commands"][cmd_name]["callbacks"] = {}
                    bot["commands"][cmd_name]["callbacks"][cb_id] = response
        else:
            field = f"commands.{cmd_name}.callbacks.{cb_id}"
            self._bots.update_one({"bot_id": bot_id}, {"$set": {field: response}})

    def delete_command_callback(self, bot_id: str, cmd_name: str, cb_id: str):
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if bot and cmd_name in bot.get("commands", {}):
                if cb_id in bot["commands"][cmd_name].get("callbacks", {}):
                    del bot["commands"][cmd_name]["callbacks"][cb_id]
        else:
            field = f"commands.{cmd_name}.callbacks.{cb_id}"
            self._bots.update_one({"bot_id": bot_id}, {"$unset": {field: ""}})

    # ── موتور جریان (Flow) و تیکت پشتیبانی ─────────────────────────
    def set_flow(self, bot_id: str, flow: Optional[dict]):
        if self._is_memory:
            if bot_id in self._bots:
                self._bots[bot_id]["flow"] = flow
        else:
            self._bots.update_one({"bot_id": bot_id}, {"$set": {"flow": flow}})

    def ticket_set_pending(self, bot_id: str, uid: int, node_id: str, ttl: int = 900):
        key = str(uid)
        entry = {"node": node_id, "expires": time.time() + ttl}
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if bot:
                bot.setdefault("pending_tickets", {})[key] = entry
        else:
            self._bots.update_one({"bot_id": bot_id},
                                  {"$set": {f"pending_tickets.{key}": entry}})

    def ticket_pop_pending(self, bot_id: str, uid: int) -> Optional[dict]:
        key = str(uid)
        if self._is_memory:
            bot = self._bots.get(bot_id)
            p = (bot or {}).get("pending_tickets", {}).pop(key, None)
            return p if p and p.get("expires", 0) > time.time() else None
        doc = self._bots.find_one_and_update(
            {"bot_id": bot_id}, {"$unset": {f"pending_tickets.{key}": ""}},
            projection={f"pending_tickets.{key}": 1},
        )
        p = (doc or {}).get("pending_tickets", {}).get(key)
        return p if p and p.get("expires", 0) > time.time() else None

    def ticket_add(self, bot_id: str, tid: str, rec: dict):
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if bot:
                tk = bot.setdefault("tickets", {})
                tk[tid] = rec
                if len(tk) > 200:
                    for old in sorted(tk, key=lambda k: tk[k].get("t", 0))[:-200]:
                        del tk[old]
        else:
            self._bots.update_one({"bot_id": bot_id}, {"$set": {f"tickets.{tid}": rec}})

    def ticket_list(self, bot_id: str, limit: int = 50) -> list:
        if self._is_memory:
            tks = list(self._bots.get(bot_id, {}).get("tickets", {}).values())
        else:
            doc = self._bots.find_one({"bot_id": bot_id}, {"tickets": 1}) or {}
            tks = list(doc.get("tickets", {}).values())
        tks.sort(key=lambda t: t.get("t", 0), reverse=True)
        return tks[:limit]

    def ticket_reply(self, bot_id: str, tid: str, text: str) -> bool:
        stamp = now_ir().isoformat()
        if self._is_memory:
            tk = self._bots.get(bot_id, {}).get("tickets", {}).get(tid)
            if not tk:
                return False
            tk["reply"] = text
            tk["reply_at"] = stamp
            return True
        res = self._bots.update_one(
            {"bot_id": bot_id, f"tickets.{tid}": {"$exists": True}},
            {"$set": {f"tickets.{tid}.reply": text, f"tickets.{tid}.reply_at": stamp}},
        )
        return res.modified_count > 0

    def set_ticket_admins(self, bot_id: str, uids: list):
        if self._is_memory:
            if bot_id in self._bots:
                self._bots[bot_id]["ticket_admins"] = uids
        else:
            self._bots.update_one({"bot_id": bot_id}, {"$set": {"ticket_admins": uids}})

    # ── آمار ──────────────────────────────────────────────────────────
    def add_bot_user(self, bot_id: str, uid: int) -> bool:
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if not bot: return False
            if uid in bot.get("bot_users", []): return False
            bot.setdefault("bot_users", []).append(uid)
            bot.setdefault("stats", {})["unique_users"] = bot["stats"].get("unique_users", 0) + 1
            day = now_ir().strftime("%Y-%m-%d")
            dd = bot.setdefault("daily_stats", {}).setdefault(day, {})
            dd["new_users"] = dd.get("new_users", 0) + 1
            bot["last_activity"] = now_ir().isoformat()
            return True
            
        doc = self._bots.find_one({"bot_id": bot_id}, {"bot_users": 1})
        if not doc:
            return False
        if uid in doc.get("bot_users", []):
            return False
        day = now_ir().strftime("%Y-%m-%d")
        self._bots.update_one(
            {"bot_id": bot_id},
            {
                "$addToSet": {"bot_users": uid},
                "$inc": {"stats.unique_users": 1, f"daily_stats.{day}.new_users": 1},
                "$set": {"last_activity": now_ir().isoformat()},
            }
        )
        return True

    def update_bot_stats(self, bot_id: str, received: int = 0, sent: int = 0):
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if bot:
                if received:
                    bot.setdefault("stats", {})["messages_received"] = bot["stats"].get("messages_received", 0) + received
                if sent:
                    bot.setdefault("stats", {})["messages_sent"] = bot["stats"].get("messages_sent", 0) + sent
                bot["last_activity"] = now_ir().isoformat()
        else:
            inc = {}
            if received: inc["stats.messages_received"] = received
            if sent:     inc["stats.messages_sent"] = sent
            if inc:
                self._bots.update_one({"bot_id": bot_id}, {
                    "$inc": inc,
                    "$set": {"last_activity": now_ir().isoformat()},
                })

    def record_broadcast(self, bot_id: str, sent: int):
        if self._is_memory:
            bot = self._bots.get(bot_id)
            if bot:
                bot.setdefault("stats", {})["broadcasts_sent"] = bot["stats"].get("broadcasts_sent", 0) + 1
                bot["stats"]["messages_sent"] = bot["stats"].get("messages_sent", 0) + sent
        else:
            self._bots.update_one({"bot_id": bot_id}, {
                "$inc": {"stats.broadcasts_sent": 1, "stats.messages_sent": sent}
            })

    # ══════════════════════════════════════════════════════════════════
    # سیستم کوین و رفرال
    # ══════════════════════════════════════════════════════════════════
    def add_coins(self, uid: int, amount: int, reason: str = "") -> bool:
        if self._is_memory:
            if uid in self._users:
                self._users[uid]["coins"] = self._users[uid].get("coins", 0) + amount
                return True
            return False
        else:
            result = self._users.update_one(
                {"uid": uid},
                {"$inc": {"coins": amount}}
            )
            return result.modified_count > 0

    def get_user_referral_info(self, uid: int) -> dict:
        user = self.get_user(uid)
        if not user:
            return {}
        return {
            "referral_code": user.get("referral_code", ""),
            "referred_by": user.get("referred_by"),
            "referral_count": user.get("referral_count", 0),
            "coins": user.get("coins", 0),
        }

    def find_user_by_referral_code(self, code: str) -> Optional[dict]:
        return self._find_user_by({"referral_code": code})

    # ══════════════════════════════════════════════════════════════════
    # سیستم محدودیت ثبت‌نام
    # ══════════════════════════════════════════════════════════════════
    def __init_registration_tracker(self):
        if not hasattr(self, '_registration_tracker'):
            self._registration_tracker = {
                'ips': {},
                'devices': {},
            }
    
    def check_registration_limit(self, ip: str, device_id: str) -> tuple:
        self.__init_registration_tracker()
        
        now = time.time()
        day_ago = now - 86400
        
        for ip_key in list(self._registration_tracker['ips'].keys()):
            self._registration_tracker['ips'][ip_key] = [
                t for t in self._registration_tracker['ips'][ip_key] if t > day_ago
            ]
        
        for dev_key in list(self._registration_tracker['devices'].keys()):
            self._registration_tracker['devices'][dev_key] = [
                t for t in self._registration_tracker['devices'][dev_key] if t > day_ago
            ]
        
        ip_records = self._registration_tracker['ips'].get(ip, [])
        if len(ip_records) >= 2:
            return False, "حداکثر ۲ حساب از هر IP مجاز است"
        
        if ip_records:
            last_time = max(ip_records)
            if now - last_time < 600:
                remaining = int((600 - (now - last_time)) / 60)
                return False, f"لطفاً {remaining} دقیقه دیگر تلاش کنید"
        
        device_records = self._registration_tracker['devices'].get(device_id, [])
        if len(device_records) >= 1:
            return False, "این دستگاه قبلاً ثبت‌نام کرده است"
        
        return True, "OK"
    
    def record_registration(self, ip: str, device_id: str):
        self.__init_registration_tracker()
        now = time.time()
        
        if ip not in self._registration_tracker['ips']:
            self._registration_tracker['ips'][ip] = []
        self._registration_tracker['ips'][ip].append(now)
        
        if device_id not in self._registration_tracker['devices']:
            self._registration_tracker['devices'][device_id] = []
        self._registration_tracker['devices'][device_id].append(now)

    def set_force_join(self, bot_id: str, channel: Optional[str]):
        if self._is_memory:
            if bot_id in self._bots:
                self._bots[bot_id]["force_join_channel"] = channel
        else:
            self._bots.update_one({"bot_id": bot_id}, {"$set": {"force_join_channel": channel}})

    # ══════════════════════════════════════════════════════════════════
    # سیستم Auth
    # ══════════════════════════════════════════════════════════════════
    @staticmethod
    def _hash_pw(password: str, salt: str = None) -> str:
        salt = salt or secrets.token_hex(16)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
        return f"pbkdf2${salt}${dk.hex()}"

    @classmethod
    def _verify_pw(cls, password: str, stored: str) -> bool:
        if not stored:
            return False
        if stored.startswith("pbkdf2$"):
            _, salt, digest = stored.split("$", 2)
            cand = cls._hash_pw(password, salt)
            return hmac.compare_digest(cand, stored)
        # هش قدیمی (sha256 ساده) — مقایسه و اجازهٔ ارتقا
        return hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(), stored)

    def register_user(self, username: str, password: str, full_name: str = "", 
                      referral_code: str = None, 
                      ip: str = "", device_id: str = "") -> tuple:
        """ثبت‌نام کاربر جدید"""
        
        existing = self._find_user_by({"username": username})
        if existing:
            raise ValueError("این نام کاربری قبلاً ثبت شده است")
        
        referrer_uid = None
        if referral_code:
            referrer = self.find_user_by_referral_code(referral_code)
            if referrer:
                referrer_uid = referrer["uid"]
        
        if self._is_memory:
            all_uids = [u["uid"] for u in self._users.values()]
            uid = max(all_uids) + 1 if all_uids else 1000000
        else:
            top = self._users.find_one(sort=[("uid", -1)], projection={"uid": 1})
            uid = max(int((top or {}).get("uid") or 1000000),
                      self._users.count_documents({}) + 1000000) + 1
        
        password_hash = self._hash_pw(password)
        token = secrets.token_urlsafe(32)
        user_referral_code = secrets.token_urlsafe(8)
        initial_coins = 50 if referrer_uid else 0
        
        new_user = {
            "uid": uid,
            "username": username,
            "password_hash": password_hash,
            "full_name": full_name or username,
            "is_admin": False,
            "is_banned": False,
            "created_at": now_ir().isoformat(),
            "bots_created": [],
            "auth_token": token,
            "coins": initial_coins,
            "referral_code": user_referral_code,
            "referred_by": referrer_uid,
            "referral_count": 0,
            "registration_ip": ip,
            "device_id": device_id,
        }
        
        if self._is_memory:
            self._users[uid] = new_user
        else:
            self._users.insert_one(new_user)
        
        if referrer_uid:
            self.add_coins(referrer_uid, 100, "referral_reward")
            if self._is_memory:
                self._users[referrer_uid]["referral_count"] = \
                    self._users[referrer_uid].get("referral_count", 0) + 1
            else:
                self._users.update_one(
                    {"uid": referrer_uid},
                    {"$inc": {"referral_count": 1}}
                )
            log.info(f"🎉 کاربر {uid} با username {username} و رفرال {referral_code} ثبت‌نام کرد")
        
        self.record_registration(ip, device_id)
        
        user_copy = copy.deepcopy(new_user)
        user_copy.pop("password_hash", None)
        user_copy.pop("auth_token", None)
        user_copy.pop("_id", None)
        
        return user_copy, token
    
    def login_user(self, username: str, password: str) -> tuple:
        user = self._find_user_by({"username": username})
        
        if not user:
            raise ValueError("نام کاربری یا رمز عبور اشتباه است")
        
        if not self._verify_pw(password, user.get("password_hash") or ""):
            raise ValueError("نام کاربری یا رمز عبور اشتباه است")
        
        if user.get("is_banned"):
            raise ValueError("حساب شما مسدود شده است")
        
        token = secrets.token_urlsafe(32)

        patch = {"auth_token": token}
        if not (user.get("password_hash") or "").startswith("pbkdf2$"):
            patch["password_hash"] = self._hash_pw(password)   # ارتقا خاموش هش قدیمی
        if self._is_memory:
            self._users[user["uid"]].update(patch)
        else:
            self._users.update_one({"uid": user["uid"]}, {"$set": patch})
        
        user_copy = copy.deepcopy(user)
        user_copy["auth_token"] = token
        user_copy.pop("password_hash", None)
        user_copy.pop("_id", None)
        
        return user_copy, token
    
    def get_user_by_token(self, token: str) -> Optional[dict]:
        user = self._find_user_by({"auth_token": token})
        if user:
            user_copy = copy.deepcopy(user)
            user_copy.pop("password_hash", None)
            user_copy.pop("auth_token", None)
            user_copy.pop("_id", None)
            return user_copy
        return None
    
    def logout_user(self, token: str) -> bool:
        if self._is_memory:
            for uid, user in self._users.items():
                if user.get("auth_token") == token:
                    self._users[uid].pop("auth_token", None)
                    return True
            return False
        else:
            result = self._users.update_one(
                {"auth_token": token},
                {"$unset": {"auth_token": ""}}
            )
            return result.modified_count > 0


storage = Storage()


# ══════════════════════════════════════════════════════════════════════
# BotFactory
# ══════════════════════════════════════════════════════════════════════
class BotRunner:
    def __init__(self, bot_id: str, bot_token: str):
        self.bot_id = bot_id
        self.bot_token = bot_token
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._app: Optional[Application] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._stopped = threading.Event()

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        
        self._ready.clear()
        self._stopped.clear()
        
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=15)
        return not self._stopped.is_set()

    def stop(self):
        if self._loop and self._app and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        self._stopped.wait(timeout=10)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._start())
        except Exception as e:
            log.error(f"ساب‌بات {self.bot_id} خطا: {e}")
        finally:
            self._stopped.set()

    async def _start(self):
        self._app = Application.builder().token(self.bot_token).build()
        self._app.add_handler(CommandHandler(
            "start", lambda u, c: _subbot_start(u, c, self.bot_id)))
        self._app.add_handler(CallbackQueryHandler(
            lambda u, c: _subbot_callback(u, c, self.bot_id)))
        self._app.add_handler(MessageHandler(
            filters.TEXT & filters.COMMAND,
            lambda u, c: _subbot_command(u, c, self.bot_id)))
        self._app.add_handler(MessageHandler(
            filters.TEXT & (~filters.COMMAND),
            lambda u, c: _subbot_text(u, c, self.bot_id)))

        await self._app.initialize()
        me = await self._app.bot.get_me()
        storage.update_bot_username(self.bot_id, me.username or "")

        self._ready.set()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        
        log.info(f"🟢 ساب‌بات @{me.username} ({self.bot_id}) روشن شد")
        
        while not self._stopped.is_set():
            await asyncio.sleep(1)

    async def _shutdown(self):
        try:
            if self._app.updater.running:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        except Exception as e:
            log.warning(f"خطا در shutdown {self.bot_id}: {e}")
        finally:
            self._stopped.set()

    def get_bot(self) -> Optional[Bot]:
        return self._app.bot if self._app else None


class BotFactory:
    def __init__(self):
        self._runners: dict = {}
        self._lock = threading.Lock()

    def start_bot(self, bot_id: str, bot_token: str) -> bool:
        with self._lock:
            runner = self._runners.get(bot_id)
            if runner:
                runner.stop()
                del self._runners[bot_id]
            
            time.sleep(1)
            
            runner = BotRunner(bot_id, bot_token)
            ok = runner.start()
            if ok:
                self._runners[bot_id] = runner
            return ok

    def stop_bot(self, bot_id: str):
        with self._lock:
            runner = self._runners.pop(bot_id, None)
        if runner:
            runner.stop()
        log.info(f"🔴 ساب‌بات {bot_id} خاموش شد")

    def is_running(self, bot_id: str) -> bool:
        runner = self._runners.get(bot_id)
        return runner is not None and runner._thread is not None and runner._thread.is_alive()

    def get_bot_instance(self, bot_id: str) -> Optional[Bot]:
        runner = self._runners.get(bot_id)
        return runner.get_bot() if runner else None

    def start_all_active(self):
        for b in storage.get_all_bots():
            if b.get("is_active"):
                self.start_bot(b["bot_id"], b["bot_token"])


factory = BotFactory()

# ══════════════════════════════════════════════════════════════════════
# موتور جریان (Flow Engine) — گراف نود/سیم واقعاً روی سرور اجرا می‌شود
# ══════════════════════════════════════════════════════════════════════
FLOW_VERSION = 2
TICKET_TTL = 900                 # ۱۵ دقیقه مهلت نوشتن متن تیکت
CB_PREFIX = "gs:"                # پیشوند callbackهای زندهٔ جریان
KINDS = {"cmd", "reply", "image", "link", "ticket", "trigger"}


def _html(t: str) -> str:
    """متن کاربر را امن می‌کند؛ اگر تگ معتبر داشته باشد دست نمی‌زند"""
    t = t or ""
    if re.search(r"</?[a-zA-Z][^>]*>", t):
        return t
    return h_escape(t)


class Flow:
    """نمای امن و سریعِ گراف جریان یک ربات"""

    def __init__(self, raw):
        raw = raw if isinstance(raw, dict) else {}
        self.nodes = {}
        for nd in raw.get("nodes") or []:
            if isinstance(nd, dict) and nd.get("id") and nd.get("kind") in KINDS:
                self.nodes[str(nd["id"])] = nd
        self.edges = []
        for e in raw.get("edges") or []:
            if isinstance(e, dict):
                f, t = str(e.get("from") or ""), str(e.get("to") or "")
                if f in self.nodes and t in self.nodes and f != t:
                    self.edges.append({"from": f, "to": t})
        self._out, self._in = {}, {}
        for e in self.edges:
            self._out.setdefault(e["from"], []).append(e["to"])
            self._in.setdefault(e["to"], []).append(e["from"])

    # ── دسترسی ───────────────────────────────────────────────────────
    def node(self, nid):
        return self.nodes.get(str(nid)) if nid is not None else None

    def outs(self, nid):
        return [self.nodes[i] for i in self._out.get(str(nid), [])]

    def ins(self, nid):
        return [self.nodes[i] for i in self._in.get(str(nid), [])]

    def edge(self, src_id, tgt_id):
        for e in self.edges:
            if e["from"] == str(src_id) and e["to"] == str(tgt_id):
                return e
        return None

    # ── شناسهٔ دکمه‌ها ────────────────────────────────────────────────
    @staticmethod
    def cid(act, src, tgt=None):
        sid = src["id"] if isinstance(src, dict) else (src or "_")
        tid = tgt["id"] if isinstance(tgt, dict) else (tgt or "")
        return f"{CB_PREFIX}{act}:{sid}:{tid}"[:64]

    def parse(self, data):
        """callback_data → {act, src, tgt, raw} | None  (اعتبارسنجی با گراف زنده)"""
        s = str(data or "")
        if not s.startswith(CB_PREFIX):
            return None
        parts = s[len(CB_PREFIX):].split(":", 2)
        if not parts or not parts[0]:
            return None
        act = parts[0]
        src = self.node(parts[1]) if len(parts) > 1 and parts[1] else None
        if src is None:
            return None
        raw = parts[2] if len(parts) > 2 else ""
        tgt = self.node(raw) if raw else None
        return {"act": act, "src": src, "tgt": tgt, "raw": raw}

    # ── ورودی‌ها ──────────────────────────────────────────────────────
    def cmd_nodes(self):
        return [n for n in self.nodes.values() if n.get("kind") == "cmd"]

    def entry_for(self, name):
        name = (name or "").strip().lstrip("/").lower()
        for n in self.cmd_nodes():
            if (n.get("name") or "").strip().lstrip("/").lower() == name:
                return n
        return None

    def keyword_nodes(self):
        return [n for n in self.nodes.values() if n.get("kind") == "trigger"]

    def match_keyword(self, text):
        t = (text or "").strip().lower()
        if not t:
            return None
        for n in self.keyword_nodes():
            mode = n.get("mode") or "contains"
            for kw in n.get("keywords") or []:
                k = str(kw).strip().lower()
                if not k:
                    continue
                if mode == "exact" and t == k:
                    return n
                if mode == "contains" and k in t:
                    return n
                if mode == "regex":
                    try:
                        if re.search(str(kw), text or "", re.IGNORECASE):
                            return n
                    except re.error:
                        pass
        return None

    # ── رندر: نود + سیم‌های خروجی‌اش → پیام آمادهٔ ارسال ───────────────
    def parts(self, nd, depth=0):
        """سیم‌های خروجی یک نود = محتوای همان پیام: دکمه/لینک/رسانه.
        نود reply با role=group مثل پوشش عمل می‌کند: عضوهایش به پیام والد اضافه می‌شوند."""
        btns, urls, media = [], [], None
        for t in self.outs(nd["id"]):
            k = t.get("kind")
            if k == "link" and t.get("url"):
                urls.append(t)
            elif k == "image" and t.get("url") and media is None:
                media = t
            elif k == "reply" and t.get("role") == "group" and depth < 3:
                sub_b, sub_u, sub_m = self.parts(t, depth + 1)
                btns.extend(sub_b)
                urls.extend(sub_u)
                if media is None and sub_m:
                    media = sub_m
            elif k in ("reply", "cmd", "ticket", "trigger"):
                btns.append(t)
        return btns, urls, media

    def render(self, nd, chain=None):
        """نود → پیام رندرشده. سیم خروجی = دکمه/لینک/رسانهٔ همان پیام."""
        if not nd:
            return None
        kind = nd.get("kind")
        if kind not in ("cmd", "reply", "image", "ticket", "trigger"):
            return None
        chain = chain or set()
        if nd["id"] in chain:
            return None
        chain = chain | {nd["id"]}
        btns, urls, media = self.parts(nd)

        if kind == "image":
            if not nd.get("url"):
                return None
            return {"mtype": nd.get("mediaType") or "photo", "url": nd["url"],
                    "text": _html(nd.get("caption") or ""), "cb": [], "urlb": []}

        if kind == "ticket":
            words = [str(w).strip() for w in (nd.get("words") or []) if str(w).strip()]
            cb = [{"text": "🎟 ثبت تیکت", "data": self.cid("tk", nd)}]
            for i, w in enumerate(words):
                cb.append({"text": f"🔑 {w[:18]}", "data": self.cid("tka", nd,
                                                                  {"id": f"w{i}"})})
            return {"mtype": None, "url": None, "ready": True,
                    "text": _html(nd.get("text") or "متن تیکت خود را بنویسید."),
                    "cb": cb, "urlb": urls, "words": words}

        text = _html(nd.get("text") or nd.get("response") or "")
        cb = []
        for t in btns:
            label = (t.get("label") or t.get("name") or t.get("text") or "ادامه")
            label = str(label).strip().splitlines()[0][:64] or "ادامه"
            cb.append({"text": label, "data": self.cid("go", nd, t)})
        return {"mtype": (media or {}).get("mediaType") or "photo",
                "url": (media or {}).get("url"), "text": text,
                "cb": cb, "urlb": urls, "ready": True}

    def rows(self, msg):
        """دکمه‌های رندرشده → سطرها اینلاین (لینک‌ها هرکدام یک ردیف)"""
        out = []
        for u in msg.get("urlb") or []:
            out.append([InlineKeyboardButton((u.get("text") or "لینک").strip()[:64] or "لینک",
                                             url=u["url"])])
        cbs = msg.get("cb") or []
        for i in range(0, len(cbs), 3):
            out.append([InlineKeyboardButton(b["text"], callback_data=b["data"])
                        for b in cbs[i:i + 3]])
        return out[:10]

    def markup(self, msg):
        rows = self.rows(msg)
        return InlineKeyboardMarkup(rows) if rows else None

    # ── سازگاری رو به پایین: گراف → commands/callbacks قدیمی ──────────
    def compile_commands(self):
        """گراف را به ساختار commands/callbacks نسخهٔ ۱ تبدیل می‌کند (برای export/بک‌آپ)"""
        result = {}
        for nd in self.cmd_nodes():
            name = (nd.get("name") or "start").strip().lstrip("/").lower() or "start"
            r = self.render(nd)
            if not r:
                continue
            btns = [{"text": b["text"], "id": b["data"]} for b in r["cb"]]
            urls = [{"text": (u.get("text") or "لینک"), "url": u["url"]} for u in r["urlb"]]
            rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
            for u in urls:
                rows.append([u])
            callbacks = {}
            for b in r["cb"]:
                # callback_data شامل پیشوند gs: است؛ parse خودش آن را می‌زداید
                tgt = (self.parse(b["data"]) or {}).get("tgt") or {}
                callbacks[b["data"]] = (tgt.get("text") or tgt.get("response") or "")
            result[name] = {
                "response": nd.get("text") or nd.get("response") or "",
                "description": nd.get("description") or "",
                "buttons": rows or None,
                "media": {"type": r["mtype"], "url": r["url"]} if r["url"] else None,
                "photo": None,
                "callbacks": callbacks,
            }
        return result

    def stats(self):
        c = {}
        for nd in self.nodes.values():
            c[nd.get("kind")] = c.get(nd.get("kind"), 0) + 1
        return {"nodes": len(self.nodes), "edges": len(self.edges), "by_kind": c}


def flow_from(bot: dict) -> Flow:
    raw = bot.get("flow")
    if isinstance(raw, dict) and raw.get("nodes"):
        return Flow(raw)
    return Flow(legacy_to_graph(bot))


def _short_id(prefix: str, name: str) -> str:
    slug = re.sub(r"[^a-z0-9_]", "", (name or "").lower())[:16] or "x"
    nid = f"{prefix}_{slug}"
    if len(nid) > 24:
        nid = nid[:16] + hashlib.sha1(nid.encode()).hexdigest()[:6]
    return nid


def legacy_to_graph(bot: dict) -> dict:
    """ربات‌های قدیمی (commands + callbacks) را به گراف تبدیل می‌کند — بدون از دست رفتن داده.
    قرارداد یال: مبدأ = پیام‌دهنده، مقصد = محتوای پیام (دکمه/لینک/رسانه)."""
    nodes, edges = [], []
    cmds = bot.get("commands") or {}
    for name, c in cmds.items():
        nid = _short_id("c", str(name))
        nodes.append({"id": nid, "kind": "cmd", "name": str(name).lower(),
                      "text": c.get("response") or "",
                      "description": c.get("description") or ""})
        m = c.get("media") or ({"type": "photo", "url": c["photo"]} if c.get("photo") else None)
        if m and m.get("url"):
            mid = _short_id("i", f"{name}_m")
            nodes.append({"id": mid, "kind": "image", "url": m["url"],
                          "mediaType": m.get("type") or "photo", "text": ""})
            edges.append({"from": nid, "to": mid})
        for row in c.get("buttons") or []:
            for idx, b in enumerate(row or []):
                if not isinstance(b, dict):
                    continue
                txt = (b.get("text") or "").strip()
                if not txt:
                    continue
                if b.get("url"):
                    lid = "l_" + hashlib.sha1(f"{name}{txt}{b['url']}".encode()).hexdigest()[:10]
                    if not any(n["id"] == lid for n in nodes):
                        nodes.append({"id": lid, "kind": "link", "text": txt, "url": b["url"]})
                    edges.append({"from": nid, "to": lid})
                else:
                    cbid = str(b.get("id") or txt)
                    resp = (c.get("callbacks") or {}).get(cbid, "")
                    rid = "r_" + hashlib.sha1(f"{name}{cbid}{resp}".encode()).hexdigest()[:14]
                    nodes.append({"id": rid, "kind": "reply", "label": txt, "text": resp})
                    edges.append({"from": nid, "to": rid})
    return {"version": FLOW_VERSION, "nodes": nodes, "edges": edges}


class Engine:
    """اجرای گراف: دستور / متن / دکمه → پیام یا اقدام"""

    def __init__(self, bot_rec: dict):
        self.bot_id = bot_rec["bot_id"]
        self.bot = bot_rec
        self.flow = flow_from(bot_rec)

    # ── ارسال ─────────────────────────────────────────────────────────
    async def deliver(self, ctx, chat_id, msg, message=None):
        if not msg:
            return False
        kb = self.flow.markup(msg)
        text = msg.get("text") or ""
        try:
            if msg.get("url"):
                fn = {"video": ctx.bot.send_video, "audio": ctx.bot.send_audio,
                      "animation": ctx.bot.send_animation,
                      "document": ctx.bot.send_document}.get(msg["mtype"], ctx.bot.send_photo)
                await fn(chat_id, msg["url"], caption=text or None,
                         reply_markup=kb, parse_mode=ParseMode.HTML)
            elif message is not None:
                await message.edit_text(text or "…", reply_markup=kb, parse_mode=ParseMode.HTML)
            else:
                await ctx.bot.send_message(chat_id, text or "…",
                                           reply_markup=kb, parse_mode=ParseMode.HTML)
            storage.update_bot_stats(self.bot_id, sent=1)
            storage.record_daily(self.bot_id, "messages_out", 1)
            return True
        except BadRequest as e:
            if message is not None and "text was not changed" in str(e).lower():
                return True
            if msg.get("url") and text:
                try:
                    await ctx.bot.send_message(chat_id, text, reply_markup=kb,
                                               parse_mode=ParseMode.HTML)
                    storage.update_bot_stats(self.bot_id, sent=1)
                    return True
                except Exception:
                    pass
            log.error(f"❌ deliver {self.bot_id}: {e}")
            return False
        except Exception as e:
            log.error(f"❌ deliver {self.bot_id}: {e}")
            return False

    # ── ورودی‌ها ──────────────────────────────────────────────────────
    async def on_command(self, ctx, chat_id, cmd_name):
        nd = self.flow.entry_for(cmd_name)
        if not nd:
            return False
        await self.deliver(ctx, chat_id, self.flow.render(nd))
        return True

    async def on_keyword(self, ctx, chat_id, text):
        nd = self.flow.match_keyword(text)
        if not nd:
            return False
        await self.deliver(ctx, chat_id, self.flow.render(nd))
        return True

    async def on_callback(self, ctx, chat_id, data, message, user_id=None):
        """'done' = جریان هندل کرد | 'legacy' = به منطق قدیمی (callbacks ذخیره‌شده) بسپار"""
        p = self.flow.parse(data)
        if not p:
            return "legacy"
        act, src = p["act"], p["src"]

        if act == "tk":
            await self._ticket_open(ctx, chat_id, src)
            return "done"

        if act == "tka":
            return await self._ticket_toggle(ctx, chat_id, src,
                                             str(p.get("raw") or ""), user_id or chat_id)

        if act == "go":
            tgt = p["tgt"]
            if tgt is None:
                return "legacy"
            msg = self.flow.render(tgt)
            if not msg or not msg.get("ready"):
                # نود مقصد محتوایی ندارد → متن سادهٔ خودِ نود را نشان بده
                txt = _html(tgt.get("text") or tgt.get("response") or tgt.get("caption") or "")
                if not txt:
                    return "legacy"
                msg = {"mtype": None, "url": None, "text": txt, "cb": [], "urlb": [],
                       "ready": True}
            await self.deliver(ctx, chat_id, msg)
            return "done"
        return "legacy"

    # ── تیکت پشتیبانی ─────────────────────────────────────────────────
    def ticket_admins(self):
        admins = {int(x) for x in (self.bot.get("ticket_admins") or [])
                  if str(x).lstrip("-").isdigit()}
        if self.bot.get("owner_uid"):
            admins.add(int(self.bot["owner_uid"]))
        return admins | set(ADMIN_IDS)

    async def _ticket_open(self, ctx, chat_id, nd):
        storage.ticket_set_pending(self.bot_id, chat_id, str(nd["id"]), TICKET_TTL)
        await self.deliver(ctx, chat_id, self.flow.render(nd))

    async def _ticket_toggle(self, ctx, chat_id, nd, raw, who):
        """پذیرندهٔ تیکت با زدن دکمهٔ 🔑، همان کلمهٔ عبور را برداشتن/گذاشتن می‌کند"""
        m = re.fullmatch(r"w(\d{1,3})", raw or "")
        if not m:
            return "done"
        words = [str(w) for w in (nd.get("words") or [])]
        i = int(m.group(1))
        if i >= len(words):
            return "done"
        if who not in self.ticket_admins():
            try:
                await ctx.bot.send_message(chat_id,
                                           "فقط پذیرندهٔ تیکت‌ها می‌تواند کلمات عبور را تغییر دهد.")
            except Exception:
                pass
            return "done"
        words.pop(i)
        nd["words"] = words
        self.save()
        await self.deliver(ctx, chat_id, self.flow.render(nd))
        return "done"

    async def _alert_admins(self, text: str):
        """تیکت را با ربات مادر برای پذیرنده‌ها می‌فرستد (آن‌ها ممکن است ساب‌بات را استارت نکرده باشند)"""
        targets = sorted(self.ticket_admins())
        if mother_bot_instance and mother_bot_loop and mother_bot_loop.is_running():
            for uid in targets:
                try:
                    fut = asyncio.run_coroutine_threadsafe(
                        mother_bot_instance.send_message(uid, text, parse_mode=ParseMode.HTML),
                        mother_bot_loop)
                    fut.result(timeout=10)
                except Exception:
                    pass
            return
        for uid in targets:
            try:
                await self._bot.send_message(uid, text, parse_mode=ParseMode.HTML)
            except Exception:
                pass

    async def on_ticket_text(self, ctx, chat_id, text, bot_ref=None):
        self._bot = ctx.bot
        pend = storage.ticket_pop_pending(self.bot_id, chat_id)
        if not pend:
            return False
        nd = self.flow.node(pend["node"])
        words = [str(w).strip() for w in ((nd or {}).get("words") or []) if str(w).strip()]
        body = (text or "").strip()
        if words and not any(body.lower().startswith(w.lower()) for w in words):
            await ctx.bot.send_message(
                chat_id,
                f"❌ پیام باید با یکی از این کلمه‌ها شروع شود: " + " / ".join(words),
                parse_mode=ParseMode.HTML)
            storage.ticket_set_pending(self.bot_id, chat_id, pend["node"], TICKET_TTL)
            return True
        tid = secrets.token_hex(4).upper()
        meta = {}
        try:
            u = await ctx.bot.get_chat(chat_id)
            meta = {"name": u.full_name or "", "username": u.username or ""}
        except Exception:
            pass
        storage.ticket_add(self.bot_id, tid, {
            "id": tid, "uid": chat_id, "node": pend["node"], "t": time.time(),
            "text": body[:4000], "status": "new", **meta})
        await ctx.bot.send_message(
            chat_id,
            f"🎟 <b>تیکت ثبت شد</b>\nکد پیگیری: <code>{tid}</code>\n"
            f"پاسخ را از همین ربات دریافت می‌کنید.",
            parse_mode=ParseMode.HTML)
        alert = (f"🎯 <b>تیکت جدید — {tid}</b>\n"
                 f"👤 {h_escape(meta.get('name') or '—')}\n"
                 f"🆔 <code>{chat_id}</code>\n"
                 f"📝\n{h_escape(body[:1500])}\n\n"
                 f" برای پاسخ، کد تیکت را در پنل بفرستید یا در تلگرام:\n"
                 f"<code>/reply {tid} متن پاسخ</code>")
        await self._alert_admins(alert)
        return True

    def save(self):
        storage.set_flow(self.bot_id, {
            "version": FLOW_VERSION,
            "nodes": list(self.flow.nodes.values()),
            "edges": self.flow.edges,
        })


# ══════════════════════════════════════════════════════════════════════
# هندلرهای ساب‌بات — همه از طریق موتور جریان (Engine) اجرا می‌شوند
# ══════════════════════════════════════════════════════════════════════
def _build_inline(buttons) -> Optional[InlineKeyboardMarkup]:
    """سازگار با نسخهٔ قدیمی (commands.buttons)"""
    if not buttons:
        return None
    rows = []
    for row in buttons:
        rrow = []
        for btn in row:
            url = btn.get("url")
            if url:
                rrow.append(InlineKeyboardButton(btn["text"], url=url))
            else:
                rrow.append(InlineKeyboardButton(
                    btn["text"], callback_data=btn.get("id") or btn["text"]))
        rows.append(rrow)
    return InlineKeyboardMarkup(rows) if rows else None


async def _send_media(bot, chat_id, media, caption, kb):
    if not media or not media.get("url"):
        return False
    media_type = media.get("type", "photo")
    url = media["url"]
    try:
        fn = {"photo": bot.send_photo, "video": bot.send_video,
              "audio": bot.send_audio, "animation": bot.send_animation,
              "document": bot.send_document}.get(media_type, bot.send_photo)
        await fn(chat_id, url, caption=caption, reply_markup=kb,
                 parse_mode=ParseMode.HTML)
        return True
    except Exception as e:
        log.error(f"❌ خطا در ارسال {media_type}: {e}")
        return False


async def _reply_menu(bot, chat_id, bot_rec: dict):
    """منوی اینلاین دستورها — فقط اگر کاربر خودش فعالش کرده باشد"""
    cmds = bot_rec.get("commands", {})
    rows = [[InlineKeyboardButton(f"/{n} — {(c.get('description') or '').strip()[:22] or n}",
                                 callback_data=f"cmd:{n}")]
            for n, c in list(cmds.items()) if n != "start"]
    if not rows:
        return
    try:
        await bot.send_message(chat_id, "☰ منوی دستورهای ربات:",
                               reply_markup=InlineKeyboardMarkup(rows[:12]))
    except Exception:
        pass


async def _check_force_join(ctx, bot_id, b, update) -> bool:
    """True اگر کاربر عضو کانال است یا اجباری نیست. در غیر این صورت پیام joining می‌فرستد."""
    ch = (b.get("force_join_channel") or "").strip()
    if not ch:
        return True
    uid = update.effective_user.id
    try:
        member = await ctx.bot.get_chat_member(ch, uid)
        if member.status in ("creator", "administrator", "member"):
            return True
    except BadRequest:
        return True     # کانال اشتباه/دسترس‌ناپذیر → ربات قفل نشود
    except Exception:
        return True
    disp = ch if ch.startswith("@") else ch
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 عضو شدن", url=f"https://t.me/{disp.lstrip('@')}")],
        [InlineKeyboardButton("✅ عضو شدم — بررسی مجدد", callback_data="fj:check")]])
    try:
        await update.effective_message.reply_text(
            f"🔒 برای استفاده از ربات اول در {disp} عضو شوید.", reply_markup=kb)
    except Exception:
        pass
    return False


async def _legacy_callback(b, data, ctx, chat_id):
    """پاسخ‌های دکمهٔ ساختهِ نسخهٔ قدیمی (commands.callbacks)"""
    for cmd_name, cmd in b.get("commands", {}).items():
        if data in cmd.get("callbacks", {}):
            resp = cmd["callbacks"][data] or ""
            media = cmd.get("media")
            try:
                await ctx.bot.send_message(chat_id, resp, parse_mode=ParseMode.HTML)
                storage.update_bot_stats(b["bot_id"], sent=1)
            except Exception:
                try:
                    await ctx.bot.send_message(chat_id, resp)
                    storage.update_bot_stats(b["bot_id"], sent=1)
                except Exception:
                    pass
            return True
    return False


async def _subbot_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE, bot_id: str):
    uid = update.effective_user.id
    b = storage.get_bot(bot_id)
    if not b:
        return
    is_new = storage.add_bot_user(bot_id, uid)
    storage.set_bot_user_info(bot_id, uid,
                              update.effective_user.full_name or "",
                              update.effective_user.username or "")
    storage.update_bot_stats(bot_id, received=1)
    storage.record_daily(bot_id, "messages_in", 1)

    if not await _check_force_join(ctx, bot_id, b, update):
        return

    chat_id = update.effective_chat.id
    try:
        if is_new and b.get("welcome_message"):
            await ctx.bot.send_message(chat_id, b["welcome_message"],
                                       parse_mode=ParseMode.HTML)
            storage.update_bot_stats(bot_id, sent=1)

        eng = Engine(b)
        if not await eng.on_command(ctx, chat_id, "start"):
            # نود /start وجود ندارد → پاسخ ذخیره‌شدهٔ قدیمی
            cmd = b.get("commands", {}).get("start", {})
            response = cmd.get("response", "سلام 👋")
            kb = _build_inline(cmd.get("buttons"))
            media = cmd.get("media") or ({"type": "photo", "url": cmd["photo"]}
                                         if cmd.get("photo") else None)
            sent = bool(media and await _send_media(ctx.bot, chat_id, media, response, kb))
            if not sent:
                await update.effective_message.reply_text(
                    response, reply_markup=kb, parse_mode=ParseMode.HTML)
            storage.update_bot_stats(bot_id, sent=1)

        if bool(b.get("menu_command")):
            await _reply_menu(ctx.bot, chat_id, b)
    except Exception as e:
        log.error(f"❌ خطا در /start ساب‌بات {bot_id}: {e}")
        try:
            await update.effective_message.reply_text("🤖 ربات موقتاً خطا دارد.")
        except Exception:
            pass


async def _subbot_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE, bot_id: str):
    uid  = update.effective_user.id
    text = (update.effective_message.text or "").strip()
    b    = storage.get_bot(bot_id)
    if not b:
        return
    storage.add_bot_user(bot_id, uid)
    storage.set_bot_user_info(bot_id, uid,
                              update.effective_user.full_name or "",
                              update.effective_user.username or "")
    storage.update_bot_stats(bot_id, received=1)
    storage.record_daily(bot_id, "messages_in", 1)

    if not await _check_force_join(ctx, bot_id, b, update):
        return

    cmd_name = text.lstrip("/").split(maxsplit=1)[0].lower() if text.startswith("/") else ""
    chat_id = update.effective_chat.id
    try:
        if await Engine(b).on_command(ctx, chat_id, cmd_name):
            return
        cmd = b.get("commands", {}).get(cmd_name)
        if not cmd:
            unknown = (b.get("unknown_reply") or "").strip()
            if unknown and cmd_name:
                await update.effective_message.reply_text(unknown, parse_mode=ParseMode.HTML)
                storage.update_bot_stats(bot_id, sent=1)
            return
        # پاسخ نسخهٔ قدیمی
        kb = _build_inline(cmd.get("buttons"))
        media = cmd.get("media") or ({"type": "photo", "url": cmd["photo"]}
                                     if cmd.get("photo") else None)
        response = cmd.get("response", "")
        sent = bool(media and await _send_media(ctx.bot, chat_id, media, response, kb))
        if not sent:
            await update.effective_message.reply_text(
                response, reply_markup=kb, parse_mode=ParseMode.HTML)
        storage.update_bot_stats(bot_id, sent=1)
        storage.record_daily(bot_id, "messages_out", 1)
    except Exception as e:
        log.error(f"❌ خطا در /{cmd_name} ساب‌بات {bot_id}: {e}")


async def _subbot_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE, bot_id: str):
    """ترتیب اولویت: تیکت در انتظار → کلیدواژهٔ نقشه → قوانین قدیمی → پاسخ ناشناخته"""
    uid  = update.effective_user.id
    text = (update.effective_message.text or "").strip()
    b    = storage.get_bot(bot_id)
    if not b or not text:
        return
    storage.add_bot_user(bot_id, uid)
    storage.set_bot_user_info(bot_id, uid,
                              update.effective_user.full_name or "",
                              update.effective_user.username or "")
    storage.update_bot_stats(bot_id, received=1)
    storage.record_daily(bot_id, "messages_in", 1)

    if not await _check_force_join(ctx, bot_id, b, update):
        return

    chat_id = update.effective_chat.id
    try:
        eng = Engine(b)
        if await eng.on_ticket_text(ctx, chat_id, text):
            return
        if await eng.on_keyword(ctx, chat_id, text):
            return

        rule = Storage.match_rule(b.get("rules", []), text)
        reply = (rule.get("response") if rule else None) or (b.get("unknown_reply") or "").strip()
        if not reply and not (rule and rule.get("media")):
            return
        kb = _build_inline(rule.get("buttons")) if rule else None
        media = rule.get("media") if rule else None
        sent = bool(media and await _send_media(ctx.bot, chat_id, media, reply, kb))
        if not sent:
            await update.effective_message.reply_text(
                reply, reply_markup=kb, parse_mode=ParseMode.HTML)
        storage.update_bot_stats(bot_id, sent=1)
        storage.record_daily(bot_id, "messages_out", 1)
    except Exception as e:
        log.error(f"❌ خطا در متن ساب‌بات {bot_id}: {e}")


async def _subbot_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE, bot_id: str):
    q    = update.callback_query
    data = q.data or ""
    b = storage.get_bot(bot_id)
    if not b:
        await q.answer()
        return

    chat_id = update.effective_chat.id
    uid = update.effective_user.id

    if data == "fj:check":
        ok = await _check_force_join(ctx, bot_id, b, update)
        await q.answer("✅ عضویت تأیید شد" if ok else "❌ هنوز عضو نشده‌اید",
                       show_alert=True)
        if ok:
            await _subbot_start(update, ctx, bot_id)
        return

    if data.startswith("cmd:"):
        name = data[4:16]
        try:
            handled = await Engine(b).on_command(ctx, chat_id, name)
            if not handled:
                await _legacy_command_send(b, name, ctx, chat_id)
            await q.answer()
        except Exception as e:
            log.error(f"❌ menu {bot_id}: {e}")
            await q.answer()
        return

    try:
        state = await Engine(b).on_callback(ctx, chat_id, data, q.message, user_id=uid)
        if state == "legacy":
            await _legacy_callback(b, data, ctx, chat_id)
        await q.answer()
    except Exception as e:
        log.error(f"❌ callback {bot_id}: {e}")
        await q.answer("خطا در پردازش دکمه", show_alert=True)


async def _legacy_command_send(b, cmd_name, ctx, chat_id):
    cmd = b.get("commands", {}).get(cmd_name)
    if not cmd:
        return
    kb = _build_inline(cmd.get("buttons"))
    media = cmd.get("media") or ({"type": "photo", "url": cmd["photo"]}
                                 if cmd.get("photo") else None)
    sent = bool(media and await _send_media(ctx.bot, chat_id, media, cmd.get("response", ""), kb))
    if not sent:
        try:
            await ctx.bot.send_message(chat_id, cmd.get("response", ""),
                                       reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception:
            await ctx.bot.send_message(chat_id, cmd.get("response", ""),
                                       reply_markup=kb)
    storage.update_bot_stats(b["bot_id"], sent=1)


# ══════════════════════════════════════════════════════════════════════
# ربات مادر
async def mother_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) and not storage.get_user(update.effective_user.id):
        return
    await update.effective_message.reply_text(
        "🤖 <b>دستورهای ربات مادر</b>\n\n"
        "<code>/start</code> — باز کردن پنل\n"
        "<code>/mybots</code> — ربات‌های من\n"
        "<code>/tickets &lt;bot_id&gt;</code> — آخرین تیکت‌ها\n"
        "<code>/reply &lt;کد&gt; متن</code> — پاسخ به یک تیکت\n\n"
        "💡 برای مدیریت کامل نقشه و تیکت‌ها، دکمهٔ پنل را بزنید.",
        parse_mode=ParseMode.HTML)


async def mother_my_bots(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    bots = storage.get_user_bots(uid) if storage.get_user(uid) else []
    if is_admin(uid):
        bots = bots or storage.get_all_bots()[:20]
    if not bots:
        await update.effective_message.reply_text("ربتی به حساب شما متصل نیست.")
        return
    lines = []
    for b in bots[:30]:
        state = "🟢" if factory.is_running(b["bot_id"]) else "⚪"
        lines.append(f"{state} <code>@{h_escape(b.get('bot_username') or '—')}</code> · "
                     f"<code>{b['bot_id']}</code>")
    await update.effective_message.reply_text(
        "🤖 <b>ربات‌ها</b>\n" + "\n".join(lines), parse_mode=ParseMode.HTML)


async def mother_list_tickets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bot_id = (update.effective_message.text or "").split(maxsplit=1)
    bot_id = bot_id[1].strip() if len(bot_id) > 1 else ""
    b = storage.get_bot(bot_id) if bot_id else None
    if not b:
        mine = [x for x in storage.get_user_bots(update.effective_user.id)] \
            if storage.get_user(update.effective_user.id) else []
        if len(mine) == 1:
            b = mine[0]
        else:
            await update.effective_message.reply_text(
                "شناسهٔ ربات را بدهید:\n<code>/tickets &lt;bot_id&gt;</code>",
                parse_mode=ParseMode.HTML)
            return
    if update.effective_user.id not in {int(b.get("owner_uid") or 0)} | set(ADMIN_IDS):
        await update.effective_message.reply_text("🚫 این ربات مال شما نیست.")
        return
    tks = storage.ticket_list(b["bot_id"], 10)
    if not tks:
        await update.effective_message.reply_text("تیکتی ثبت نشده است.")
        return
    out = [f"🎟 <b>تیکت‌های @{h_escape(b.get('bot_username') or '')}</b>"]
    for t in tks:
        st = "✅" if t.get("reply") else "🔵"
        out.append(f"{st} <code>{t['id']}</code> — {h_escape((t.get('text') or '')[:60])}")
    out.append(f"\nبرای پاسخ: <code>/reply {tks[0]['id']} متن شما</code>")
    await update.effective_message.reply_text("\n".join(out), parse_mode=ParseMode.HTML)


async def mother_reply_ticket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    parts = (update.effective_message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await update.effective_message.reply_text(
            "قالب: <code>/reply &lt;کد تیکت&gt; &lt;متن پاسخ&gt;</code>",
            parse_mode=ParseMode.HTML)
        return
    tid, text = parts[1].strip().upper(), parts[2].strip()
    uid = update.effective_user.id

    target = None
    for b in storage.get_all_bots():
        if uid in {int(x) for x in (b.get("ticket_admins") or [])
                   if str(x).lstrip("-").isdigit()} | {int(b.get("owner_uid") or 0)} | set(ADMIN_IDS):
            if any(t.get("id") == tid for t in storage.ticket_list(b["bot_id"], 200)):
                target = b
                break
    if not target:
        await update.effective_message.reply_text(f"تیکت <code>{tid}</code> نیافتم.",
                                                  parse_mode=ParseMode.HTML)
        return
    if not storage.ticket_reply(target["bot_id"], tid, text[:4000]):
        await update.effective_message.reply_text("ثبت پاسخ ناموفق بود.")
        return

    tk = next((t for t in storage.ticket_list(target["bot_id"], 200) if t.get("id") == tid), None)
    sub = factory.get_bot_instance(target["bot_id"])
    runner = factory._runners.get(target["bot_id"])
    if sub and runner and runner._loop and tk:
        try:
            fut = asyncio.run_coroutine_threadsafe(
                sub.send_message(tk["uid"],
                                 f"🎟 <b>پاسخ تیکت {tid}</b>\n\n{_html(text)}",
                                 parse_mode=ParseMode.HTML),
                runner._loop)
            await asyncio.wrap_future(fut)
            await update.effective_message.reply_text(f"✅ پاسخ تیکت {tid} ارسال شد.")
            return
        except Exception as e:
            log.warning(f"mother reply {tid}: {e}")
    await update.effective_message.reply_text(
        f"پاسخ ذخیره شد، اما ارسال نشد (ربات خاموش است؟) — از پنل دوباره بفرستید.")


# ══════════════════════════════════════════════════════════════════════
async def mother_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global mother_bot_instance, mother_bot_loop
    
    mother_bot_instance = ctx.bot
    mother_bot_loop = asyncio.get_event_loop()
    
    user = update.effective_user
    
    u = storage.ensure_user(
        user.id, 
        (user.username or "").lower(),
        (user.full_name or "").strip()
    )
    
    if not storage._is_memory:
        storage._users.update_one(
            {"uid": user.id},
            {"$set": {
                "telegram_username": (user.username or "").lower(),
                "telegram_user_id": user.id,
                "telegram_first_name": user.first_name or "",
            }}
        )
    else:
        if user.id in storage._users:
            storage._users[user.id]["telegram_username"] = (user.username or "").lower()
            storage._users[user.id]["telegram_user_id"] = user.id
            storage._users[user.id]["telegram_first_name"] = user.first_name or ""
    
    if u.get("is_banned"):
        await update.effective_message.reply_text("🚫 حساب شما مسدود شده است.")
        return

    text = (
        f"🤖 <b>سلام {user.first_name or 'دوست عزیز'}!</b>\n\n"
        f"✅ اکانت شما در GramSaz آماده شد.\n\n"
        f"📌 <b>Username تلگرام شما:</b> @{user.username or 'ندارید'}\n\n"
        f"👇 برای ثبت‌نام در پنل، به سایت برگردید و username خود را وارد کنید."
    )

    rows = []
    if WEBAPP_URL:
        rows.append([InlineKeyboardButton("🚀 باز کردن پنل مدیریت", web_app=WebAppInfo(url=WEBAPP_URL))])
    
    # دکمه آمار ادمین (فقط برای ادمین‌ها)
    if is_admin(user.id):
        rows.append([InlineKeyboardButton("📊 آمار پلتفرم", callback_data="admin_stats")])

    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows)
    )


async def mother_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    user = q.from_user
    data = q.data
    
    if data == "no_webapp":
        await q.message.reply_text("⚙️ WEBAPP_URL در سرور تنظیم نشده است.")
        return
    
    if data == "admin_stats":
        # فقط ادمین دسترسی دارد
        if not is_admin(user.id):
            await q.message.reply_text("🚫 شما دسترسی ادمین ندارید.")
            return
        
        # دریافت آمار
        try:
            all_users = storage.get_all_users()
            all_bots = storage.get_all_bots()
            
            total_users = len(all_users)
            total_registered = sum(1 for u in all_users if u.get("password_hash"))
            total_active_bots = sum(1 for b in all_bots if b.get("is_active"))
            total_messages = sum(b.get("stats", {}).get("messages_received", 0) for b in all_bots)
            total_bot_users = sum(len(b.get("bot_users", [])) for b in all_bots)
            total_coins = sum(u.get("coins", 0) for u in all_users)
            
            text = (
                f"📊 <b>آمار پلتفرم GramSaz</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👥 <b>کاربران:</b>\n"
                f"   • کل کاربران: <code>{total_users}</code>\n"
                f"   • ثبت‌نام شده: <code>{total_registered}</code>\n\n"
                f"🤖 <b>ربات‌ها:</b>\n"
                f"   • کل ربات‌ها: <code>{len(all_bots)}</code>\n"
                f"   • فعال: <code>{total_active_bots}</code>\n"
                f"   • کاربران ربات‌ها: <code>{total_bot_users}</code>\n\n"
                f"💬 <b>پیام‌ها:</b>\n"
                f"   • کل پیام‌های دریافتی: <code>{total_messages}</code>\n\n"
                f"🪙 <b>اقتصاد:</b>\n"
                f"   • کوین‌های توزیع شده: <code>{total_coins}</code>\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💡 برای آمار دقیق‌تر و نمودارها، از پنل وب استفاده کنید."
            )
            
            rows = []
            if WEBAPP_URL:
                rows.append([InlineKeyboardButton(
                    "🌐 مشاهده در پنل وب (با نمودار)",
                    web_app=WebAppInfo(url=WEBAPP_URL)
                )])
            
            await q.message.reply_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(rows) if rows else None
            )
            
        except Exception as e:
            log.error(f"❌ خطا در دریافت آمار تلگرام: {e}")
            await q.message.reply_text(f"❌ خطا در دریافت آمار: {e}")


def run_mother_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _run():
        global mother_bot_instance, mother_bot_loop
        app = Application.builder().token(PLATFORM_BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", mother_start))
        app.add_handler(CommandHandler("mybots", mother_my_bots))
        app.add_handler(CommandHandler("tickets", mother_list_tickets))
        app.add_handler(CommandHandler("reply", mother_reply_ticket))
        app.add_handler(CommandHandler("help", mother_help))
        app.add_handler(CallbackQueryHandler(mother_callback))
        log.info("🤖 ربات مادر شروع به polling کرد")
        await app.initialize()
        await app.start()
        mother_bot_instance = app.bot
        mother_bot_loop = asyncio.get_running_loop()
        try:
            await app.bot.set_my_commands([
                BotCommand("start", "باز کردن پنل مدیریت"),
                BotCommand("mybots", "ربات‌های من"),
                BotCommand("tickets", "تیکت‌های ربات"),
                BotCommand("reply", "پاسخ به یک تیکت"),
                BotCommand("help", "راهنما"),
            ])
        except Exception:
            pass
        await app.updater.start_polling(drop_pending_updates=True)
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


# ══════════════════════════════════════════════════════════════════════
# Flask App
# ══════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)


def _public_user(u: dict) -> dict:
    """برداشتن فیلدهای حساس از کاربر پیش از بازگشت در API"""
    if not isinstance(u, dict):
        return u
    for k in ("password_hash", "auth_token", "_id", "registration_ip", "device_id"):
        u.pop(k, None)
    return u


def get_current_user():
    init_data = request.headers.get("X-Init-Data", "")
    if init_data:
        user = verify_telegram_init_data(init_data, PLATFORM_BOT_TOKEN)
        if user:
            uid = user.get("id")
            if uid:
                db_user = storage.get_user(int(uid))
                if not db_user:
                    db_user = storage.ensure_user(
                        int(uid), user.get("username", ""), user.get("first_name", "")
                    )
                if not db_user.get("is_banned"):
                    return _public_user(db_user), None
        return None, (jsonify({"error": "initData نامعتبر است"}), 401)
    
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if token:
        user = storage.get_user_by_token(token)
        if user:
            if not user.get("is_banned"):
                return _public_user(user), None
        return None, (jsonify({"error": "توکن نامعتبر یا منقضی شده"}), 401)
    
    return None, (jsonify({"error": "احراز هویت نشده"}), 401)


def auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user, err = get_current_user()
        if err:
            return err
        g.current_user = user
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user, err = get_current_user()
        if err:
            return err
        if not user.get("is_admin") and user["uid"] not in ADMIN_IDS:
            return jsonify({"error": "دسترسی فقط برای ادمین‌ها"}), 403
        g.current_user = user
        return f(*args, **kwargs)
    return decorated


def get_owned_bot(bot_id: str):
    b = storage.get_bot(bot_id)
    if not b:
        return None, (jsonify({"error": "ربات یافت نشد"}), 404)
    user = g.current_user
    if b["owner_uid"] != user["uid"] and not user.get("is_admin"):
        return None, (jsonify({"error": "دسترسی ندارید"}), 403)
    return b, None


@flask_app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,X-Init-Data,Authorization,Accept"
    response.headers["Access-Control-Max-Age"] = "3600"
    return response

@flask_app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@flask_app.route("/<path:path>", methods=["OPTIONS"])
def options_handler(path):
    return "", 204


# ──────────────────────────────────────────────────────────────────────
# /api/me
# ──────────────────────────────────────────────────────────────────────
@flask_app.get("/api/me")
@auth_required
def api_me():
    return jsonify(g.current_user)


# ──────────────────────────────────────────────────────────────────────
# /api/bots
# ──────────────────────────────────────────────────────────────────────
@flask_app.get("/api/bots")
@auth_required
def api_list_bots():
    bots = storage.get_user_bots(g.current_user["uid"])
    for b in bots:
        b["is_running"] = factory.is_running(b["bot_id"])
        b.pop("bot_token", None)
    return jsonify({"bots": bots})


@flask_app.post("/api/bots")
@auth_required
def api_add_bot():
    data  = request.get_json(force=True) or {}
    token = (data.get("token") or "").strip()
    if ":" not in token or len(token) < 30:
        return jsonify({"error": "توکن نامعتبر است"}), 400

    existing   = storage.get_all_bots()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    for b in existing:
        if hashlib.sha256(b.get("bot_token", "").encode()).hexdigest() == token_hash:
            return jsonify({"error": "این ربات قبلاً اضافه شده"}), 409

    import requests as req_lib
    try:
        resp = req_lib.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        if not resp.ok or not resp.json().get("ok"):
            return jsonify({"error": "توکن معتبر نیست — از @BotFather بگیر"}), 400
        me_data = resp.json()["result"]
    except Exception as e:
        return jsonify({"error": f"خطا در ارتباط با تلگرام: {e}"}), 500

    bot_rec = storage.create_bot(g.current_user["uid"], token, me_data.get("username", ""))
    ok      = factory.start_bot(bot_rec["bot_id"], token)
    bot_rec.pop("bot_token", None)
    bot_rec["is_running"] = ok
    return jsonify({"bot": bot_rec, "message": f"✅ ربات @{me_data.get('username')} اضافه شد"}), 201


# ──────────────────────────────────────────────────────────────────────
# /api/bots/<bot_id>
# ──────────────────────────────────────────────────────────────────────
@flask_app.get("/api/bots/<bot_id>")
@auth_required
def api_get_bot(bot_id):
    b, err = get_owned_bot(bot_id)
    if err: return err
    b.pop("bot_token", None)
    b["is_running"] = factory.is_running(bot_id)
    return jsonify({"bot": b})


@flask_app.delete("/api/bots/<bot_id>")
@auth_required
def api_delete_bot(bot_id):
    b, err = get_owned_bot(bot_id)
    if err: return err
    factory.stop_bot(bot_id)
    ok = storage.delete_bot(bot_id, g.current_user["uid"])
    if not ok:
        return jsonify({"error": "حذف ناموفق بود"}), 500
    return jsonify({"message": "ربات حذف شد"})


@flask_app.route("/api/bots/<bot_id>/toggle", methods=["PATCH"])
@auth_required
def api_toggle_bot(bot_id):
    b, err = get_owned_bot(bot_id)
    if err: return err
    data   = request.get_json(force=True) or {}
    active = bool(data.get("active", True))
    storage.set_bot_active(bot_id, active)
    if active:
        ok = factory.start_bot(bot_id, b["bot_token"])
    else:
        factory.stop_bot(bot_id)
        ok = True
    return jsonify({"active": active, "is_running": factory.is_running(bot_id)})


@flask_app.route("/api/bots/<bot_id>/force-join", methods=["PATCH"])
@auth_required
def api_force_join(bot_id):
    _, err = get_owned_bot(bot_id)
    if err: return err
    data    = request.get_json(force=True) or {}
    channel = data.get("channel") or None
    storage.set_force_join(bot_id, channel)
    return jsonify({"force_join_channel": channel})


# ──────────────────────────────────────────────────────────────────────
# /api/bots/<bot_id>/commands
# ──────────────────────────────────────────────────────────────────────
@flask_app.get("/api/bots/<bot_id>/commands")
@auth_required
def api_list_commands(bot_id):
    b, err = get_owned_bot(bot_id)
    if err: return err
    cmds    = b.get("commands", {})
    ordered = {}
    if "start" in cmds:
        ordered["start"] = cmds["start"]
    for k, v in sorted(cmds.items()):
        if k != "start":
            ordered[k] = v
    return jsonify({"commands": ordered})


@flask_app.route("/api/bots/<bot_id>/commands/<cmd_name>", methods=["PUT"])
@auth_required
def api_set_command(bot_id, cmd_name):
    _, err = get_owned_bot(bot_id)
    if err: return err
    data     = request.get_json(force=True) or {}
    response = data.get("response", "")
    buttons  = data.get("buttons")
    photo    = data.get("photo")
    media    = data.get("media")  # ✅ سیستم جدید
    description = data.get("description", "")  # توضیح برای منوی تلگرام

    log.info(f"💾 ذخیره دستور /{cmd_name} برای {bot_id}: media={media}, photo={bool(photo)}, buttons={bool(buttons)}")

    storage.set_command(bot_id, cmd_name, response, buttons, photo, media, description)
    _mirror_cmd_in_flow(bot_id, cmd_name)
    return jsonify({"message": f"دستور /{cmd_name} ذخیره شد"})

@flask_app.route("/api/bots/<bot_id>/commands/<cmd_name>", methods=["DELETE"])
@auth_required
def api_delete_command(bot_id, cmd_name):
    if cmd_name == "start":
        return jsonify({"error": "دستور /start قابل حذف نیست"}), 400
    _, err = get_owned_bot(bot_id)
    if err: return err
    storage.delete_command(bot_id, cmd_name)
    _mirror_cmd_in_flow(bot_id, cmd_name, removed=True)
    return jsonify({"message": f"دستور /{cmd_name} حذف شد"})


# ──────────────────────────────────────────────────────────────────────
# /api/bots/<bot_id>/commands/<cmd_name>/callbacks/<cb_id>
# ──────────────────────────────────────────────────────────────────────
@flask_app.route("/api/bots/<bot_id>/commands/<cmd_name>/callbacks/<cb_id>", methods=["PUT"])
@auth_required
def api_set_callback(bot_id, cmd_name, cb_id):
    _, err = get_owned_bot(bot_id)
    if err: return err
    data     = request.get_json(force=True) or {}
    response = data.get("response", "")
    storage.set_command_callback(bot_id, cmd_name, cb_id, response)
    return jsonify({"message": f"پاسخ دکمه {cb_id} ذخیره شد"})


@flask_app.route("/api/bots/<bot_id>/commands/<cmd_name>/callbacks/<cb_id>", methods=["DELETE"])
@auth_required
def api_delete_callback(bot_id, cmd_name, cb_id):
    _, err = get_owned_bot(bot_id)
    if err: return err
    storage.delete_command_callback(bot_id, cmd_name, cb_id)
    return jsonify({"message": "پاسخ دکمه حذف شد"})


# ──────────────────────────────────────────────────────────────────────
# /api/bots/<bot_id>/broadcast
# ──────────────────────────────────────────────────────────────────────
@flask_app.post("/api/bots/<bot_id>/broadcast")
@auth_required
def api_broadcast(bot_id):
    b, err = get_owned_bot(bot_id)
    if err: return err
    data  = request.get_json(force=True) or {}
    text  = data.get("text", "")
    users = b.get("bot_users", [])
    if not users:
        return jsonify({"sent": 0, "failed": 0, "message": "هنوز کاربری ندارد"})

    sub_bot = factory.get_bot_instance(bot_id)
    if not sub_bot:
        return jsonify({"error": "ربات خاموش است — اول روشنش کن"}), 400

    runner = factory._runners.get(bot_id)

    def do_broadcast():
        loop = runner._loop if runner else None
        if not loop:
            return
        sent = failed = 0
        for uid in users:
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    sub_bot.send_message(uid, text, parse_mode=ParseMode.HTML), loop)
                fut.result(timeout=10)
                sent += 1
            except Exception:
                # در صورت خطای فرمت، بدون HTML امتحان کن
                try:
                    fut = asyncio.run_coroutine_threadsafe(
                        sub_bot.send_message(uid, text), loop)
                    fut.result(timeout=10)
                    sent += 1
                except Exception:
                    failed += 1
            time.sleep(0.05)
        storage.record_broadcast(bot_id, sent)
        log.info(f"📢 broadcast {bot_id}: ✅{sent} ❌{failed}")

    threading.Thread(target=do_broadcast, daemon=True).start()
    return jsonify({"message": f"پیام برای {len(users)} کاربر در صف ارسال قرار گرفت"})


# ──────────────────────────────────────────────────────────────────────
# /api/bots/<bot_id>/config  — خوش‌آمد، پاسخ ناشناخته، منوی خودکار
# ──────────────────────────────────────────────────────────────────────
@flask_app.route("/api/bots/<bot_id>/config", methods=["PATCH"])
@auth_required
def api_bot_config(bot_id):
    _, err = get_owned_bot(bot_id)
    if err: return err
    data = request.get_json(force=True) or {}
    patch = {}
    for k in ("welcome_message", "unknown_reply", "auto_menu"):
        if k in data:
            patch[k] = data[k]
    storage.set_bot_config(bot_id, patch)
    b, _ = get_owned_bot(bot_id)
    return jsonify({
        "welcome_message": b.get("welcome_message", ""),
        "unknown_reply": b.get("unknown_reply", ""),
        "auto_menu": b.get("auto_menu", True),
        "rules": b.get("rules", []),
        "message": "تنظیمات ذخیره شد",
    })


# ──────────────────────────────────────────────────────────────────────
# /api/bots/<bot_id>/rules — موتور پاسخ خودکار
# ──────────────────────────────────────────────────────────────────────
MAX_RULES = 50
VALID_MODES = ("exact", "contains", "regex")


def _clean_rules(raw):
    if not isinstance(raw, list):
        return None, "قالب قوانین نامعتبر است"
    if len(raw) > MAX_RULES:
        return None, f"حداکثر {MAX_RULES} قانون مجاز است"
    seen, cleaned = set(), []
    for r in raw:
        if not isinstance(r, dict):
            return None, "هر قانون باید شیء باشد"
        rid = str(r.get("id") or "").strip() or f"r_{secrets.token_hex(4)}"
        if rid in seen:
            return None, f"شناسهٔ تکراری: {rid}"
        seen.add(rid)
        kws = r.get("keywords")
        if not isinstance(kws, list) or not [k for k in kws if str(k).strip()]:
            return None, f"قانون {rid}: حداقل یک کلمهٔ کلیدی لازم است"
        mode = r.get("mode", "contains")
        if mode not in VALID_MODES:
            return None, f"قانون {rid}: نوع تطبیق نامعتبر است"
        for kw in kws:
            if mode == "regex":
                try:
                    re.compile(str(kw))
                except re.error as e:
                    return None, f"قانون {rid}: الگوی نامعتبر ({e})"
        resp = r.get("response")
        if resp is not None and not isinstance(resp, str):
            return None, f"قانون {rid}: پاسخ باید متن باشد"
        media = r.get("media")
        if media and not (isinstance(media, dict) and media.get("url")):
            return None, f"قانون {rid}: رسانه باید پیوند داشته باشد"
        buttons = r.get("buttons")
        if buttons and not isinstance(buttons, list):
            return None, f"قانون {rid}: دکمه‌ها نامعتبر است"
        cleaned.append({"id": rid, "keywords": [str(k).strip() for k in kws if str(k).strip()][:20],
                        "mode": mode, "response": (resp or "")[:4000],
                        "media": media or None, "buttons": buttons or None})
    return cleaned, None


@flask_app.get("/api/bots/<bot_id>/rules")
@auth_required
def api_get_rules(bot_id):
    b, err = get_owned_bot(bot_id)
    if err: return err
    return jsonify({"rules": b.get("rules", [])})


@flask_app.route("/api/bots/<bot_id>/rules", methods=["PUT"])
@auth_required
def api_put_rules(bot_id):
    _, err = get_owned_bot(bot_id)
    if err: return err
    data = request.get_json(force=True) or {}
    cleaned, verr = _clean_rules(data.get("rules"))
    if verr:
        return jsonify({"error": verr}), 400
    storage.set_bot_config(bot_id, {"rules": cleaned})
    return jsonify({"rules": cleaned, "message": "قوانین ذخیره شد"})


@flask_app.route("/api/bots/<bot_id>/rules/<rule_id>", methods=["DELETE"])
@auth_required
def api_delete_rule(bot_id, rule_id):
    b, err = get_owned_bot(bot_id)
    if err: return err
    kept = [r for r in b.get("rules", []) if r.get("id") != rule_id]
    storage.set_bot_config(bot_id, {"rules": kept})
    return jsonify({"rules": kept, "message": "قانون حذف شد"})


# ──────────────────────────────────────────────────────────────────────
# /api/bots/<bot_id>/users — کتابخانهٔ کاربران ربات
# ──────────────────────────────────────────────────────────────────────
@flask_app.get("/api/bots/<bot_id>/users")
@auth_required
def api_bot_users(bot_id):
    b, err = get_owned_bot(bot_id)
    if err: return err
    users = storage.get_bot_users(bot_id)
    q = (request.args.get("q") or "").strip().lower()
    if q:
        users = [u for u in users
                 if q in str(u["uid"]) or q in (u.get("name") or "").lower()
                 or q in (u.get("username") or "").lower()]
    limit = min(int(request.args.get("limit", 200)), 1000)
    offset = max(int(request.args.get("offset", 0)), 0)
    return jsonify({
        "total": len(users),
        "users": users[offset:offset + limit],
    })


# ──────────────────────────────────────────────────────────────────────
# /api/bots/<bot_id>/sync-menu — همگام‌سازی دستورها با منوی تلگرام
# ──────────────────────────────────────────────────────────────────────
@flask_app.post("/api/bots/<bot_id>/sync-menu")
@auth_required
def api_sync_menu(bot_id):
    b, err = get_owned_bot(bot_id)
    if err: return err
    sub_bot = factory.get_bot_instance(bot_id)
    if not sub_bot:
        return jsonify({"error": "ربات خاموش است — اول روشنش کن"}), 400
    cmds = b.get("commands", {})
    menu = []
    for name, cmd in cmds.items():
        desc = (cmd.get("description") or "").strip() or name
        menu.append(BotCommand(command=name[:32], description=desc[:48]))
    runner = factory._runners.get(bot_id)
    try:
        loop = runner._loop if runner else None
        if loop:
            fut = asyncio.run_coroutine_threadsafe(sub_bot.set_my_commands(menu), loop)
            fut.result(timeout=15)
        else:
            sub_bot.set_my_commands(menu)
        return jsonify({"message": f"منوی تلگرام با {len(menu)} دستور همگام شد", "count": len(menu)})
    except Exception as e:
        log.error(f"❌ خطا در sync-menu {bot_id}: {e}")
        return jsonify({"error": f"خطا در همگام‌سازی منو: {e}"}), 500


# ══════════════════════════════════════════════════════════════════════
# /api/bots/<bot_id>/flow — موتور جریان: ذخیره، اعمال، تست، تیکت‌ها
# ══════════════════════════════════════════════════════════════════════
MAX_NODES = 400
MAX_EDGES = 800
NODE_KINDS = ("cmd", "reply", "image", "link", "ticket", "trigger")


def _clean_flow(raw):
    """اعتبارسنجی سخت‌گیرانهٔ گراف پیش از ذخیره"""
    if not isinstance(raw, dict):
        return None, "قالب نقشه نامعتبر است"
    nodes_raw, edges_raw = raw.get("nodes"), raw.get("edges") or []
    if not isinstance(nodes_raw, list) or not isinstance(edges_raw, list):
        return None, "کارت‌ها و سیم‌ها باید فهرست باشند"
    if len(nodes_raw) > MAX_NODES:
        return None, f"حداکثر {MAX_NODES} کارت مجاز است"
    if len(edges_raw) > MAX_EDGES:
        return None, f"حداکثر {MAX_EDGES} اتصال مجاز است"

    def s(v, n):
        return str(v if v is not None else "")[:n]

    nodes, seen_cmd = {}, set()
    for nd in nodes_raw:
        if not isinstance(nd, dict):
            return None, "هر کارت باید شیء باشد"
        nid = s(nd.get("id"), 24).strip()
        kind = s(nd.get("kind"), 16)
        if not re.fullmatch(r"[A-Za-z0-9_\-]{2,24}", nid):
            return None, f"شناسهٔ کارت نامعتبر است (۲ تا ۲۴ نویسه): {nid!r}"
        if kind not in NODE_KINDS:
            return None, f"نوع کارت نامعتبر است: {kind}"
        try:
            x, y = int(float(nd.get("x") or 0)), int(float(nd.get("y") or 0))
        except (TypeError, ValueError):
            x = y = 0
        clean = {"id": nid, "kind": kind,
                 "x": max(-20000, min(20000, x)), "y": max(-20000, min(20000, y))}

        if kind == "cmd":
            name = re.sub(r"[^a-z0-9_]", "", s(nd.get("name"), 32).strip().lstrip("/").lower())
            if not name:
                return None, "کارت دستور نام ندارد"
            if name in seen_cmd:
                return None, f"دستور تکراری: /{name}"
            seen_cmd.add(name)
            clean.update({"name": name,
                          "text": s(nd.get("text") or nd.get("response"), 4000),
                          "description": s(nd.get("description"), 60)})
        elif kind == "reply":
            clean["text"] = s(nd.get("text"), 4000)
            clean["label"] = s(nd.get("label"), 64)
            if nd.get("role") == "group":
                clean["role"] = "group"
        elif kind == "trigger":
            kws = nd.get("keywords")
            if not isinstance(kws, list) or not [k for k in kws if str(k).strip()]:
                return None, f"کارت «{nid}»: حداقل یک کلیدواژه لازم است"
            mode = s(nd.get("mode"), 12) or "contains"
            if mode not in VALID_MODES:
                return None, f"کارت «{nid}»: نوع تطبیق نامعتبر است"
            for k in kws:
                if mode == "regex":
                    try:
                        re.compile(str(k))
                    except re.error as e:
                        return None, f"کارت «{nid}»: الگوی نامعتبر ({e})"
            clean["keywords"] = [s(k, 60).strip() for k in kws if str(k).strip()][:20]
            clean["mode"] = mode
            clean["label"] = s(nd.get("label"), 64)
            clean["text"] = s(nd.get("text") or nd.get("response"), 4000)
        elif kind in ("image", "link"):
            url = s(nd.get("url"), 500).strip()
            if url and not url.startswith(("http://", "https://")):
                return None, f"کارت «{nid}»: پیوند باید با http شروع شود"
            clean["url"] = url
            clean["text"] = s(nd.get("text") or nd.get("caption"), 1024)
            clean["caption"] = clean["text"]
            if kind == "image":
                mt = s(nd.get("mediaType"), 12) or "photo"
                clean["mediaType"] = mt if mt in MEDIA_TYPES else "photo"
        elif kind == "ticket":
            clean["text"] = s(nd.get("text"), 2000)
            words = nd.get("words")
            if words is not None and not isinstance(words, list):
                return None, f"کارت «{nid}»: کلمات عبور نامعتبر است"
            clean["words"] = [s(w, 30).strip() for w in (words or []) if str(w).strip()][:12]
        nodes[nid] = clean

    edges, seen_edge = [], set()
    for e in edges_raw:
        if not isinstance(e, dict):
            continue
        f, t = s(e.get("from"), 24), s(e.get("to"), 24)
        if f not in nodes or t not in nodes or f == t or (f, t) in seen_edge:
            continue
        seen_edge.add((f, t))
        edges.append({"from": f, "to": t})

    # عمق تودرتویی گروه‌ها: موتور فقط ۳ سطح را باز می‌کند، سطح چهارم دور ریخته می‌شود
    kids = {}
    for e in edges:
        f, t = e["from"], e["to"]
        if nodes[f].get("role") == "group" and nodes[t].get("role") == "group":
            kids.setdefault(f, []).append(t)

    def _depth(i, trail):
        best = 0
        for j in kids.get(i, ()):
            if j in trail:
                continue
            best = max(best, 1 + _depth(j, trail | {j}))
        return best

    for nid_ in nodes:
        if nodes[nid_].get("role") == "group" and _depth(nid_, {nid_}) > 2:
            return None, f"گروه «{nid_}» بیش از ۳ سطح تودرتو است — ساده‌اش کن"

    out = {"version": FLOW_VERSION, "nodes": list(nodes.values()), "edges": edges}
    v = raw.get("view")
    if isinstance(v, dict):                       # جای دوربین روی بوم — فقط برای رابط کاربری
        try:
            out["view"] = {"x": int(v.get("x") or 0), "y": int(v.get("y") or 0),
                           "z": min(2.4, max(0.35, float(v.get("z") or 1)))}
        except (TypeError, ValueError):
            pass
    return out, None


def _sync_commands_from_flow(bot_id: str, graph: dict) -> int:
    """گراف را به ساختار commands نسخهٔ قدیمی هم می‌نویسد تا هیچ نقطه‌ای از پنل نشکند"""
    comp = Flow(graph).compile_commands()
    b = storage.get_bot(bot_id) or {}
    old = b.get("commands", {})
    for name, c in comp.items():
        storage.set_command(bot_id, name, c["response"], c["buttons"],
                            c["photo"], c["media"], c["description"])
        new_cb = c["callbacks"]
        prev_cb = (old.get(name) or {}).get("callbacks") or {}
        for cid, resp in new_cb.items():
            if prev_cb.get(cid) != resp:
                storage.set_command_callback(bot_id, name, cid, resp)
        for cid in prev_cb:
            if cid not in new_cb:
                storage.delete_command_callback(bot_id, name, cid)
    for name in old:
        if name not in comp and name != "start":
            storage.delete_command(bot_id, name)
    return len(comp)


def _mirror_cmd_in_flow(bot_id: str, cmd_name: str, removed: bool = False):
    """تغییر دستور از پنل کلاسیک را در گرافِ ذخیره‌شده هم منعکس می‌کند"""
    b = storage.get_bot(bot_id) or {}
    raw = b.get("flow")
    if not (isinstance(raw, dict) and raw.get("nodes")):
        return                                   # هنوز گراف واقعی‌ای ساخته نشده
    nodes, edges = list(raw["nodes"]), list(raw["edges"])
    name = str(cmd_name).lstrip("/").lower()
    hit = next((n for n in nodes
                if isinstance(n, dict) and n.get("kind") == "cmd"
                and str(n.get("name") or "").lstrip("/").lower() == name), None)
    if removed:
        if not hit:
            return
        nid_ = str(hit["id"])
        nodes = [n for n in nodes if str(n.get("id")) != nid_]
        edges = [e for e in edges if e.get("from") != nid_ and e.get("to") != nid_]
    else:
        c = (b.get("commands") or {}).get(name) or {}
        if hit:
            hit = dict(hit)
            hit["text"] = str(c.get("response") or "")[:4000]
            hit["description"] = str(c.get("description") or "")[:60]
            nodes = [hit if str(n.get("id")) == str(hit["id"]) else n for n in nodes]
        else:
            nodes.append({"id": _short_id("c", name), "kind": "cmd", "name": name,
                          "text": str(c.get("response") or "")[:4000],
                          "description": str(c.get("description") or "")[:60],
                          "x": 40, "y": 40 + 130 * len(nodes)})
    cleaned, verr = _clean_flow({"nodes": nodes, "edges": edges, "view": raw.get("view")})
    if not verr and cleaned:
        storage.set_flow(bot_id, cleaned)


@flask_app.route("/api/bots/<bot_id>/flow", methods=["GET", "PUT"])
@auth_required
def api_flow(bot_id):
    b, err = get_owned_bot(bot_id)
    if err:
        return err

    if request.method == "GET":
        graph = b.get("flow")
        derived = not (isinstance(graph, dict) and graph.get("nodes"))
        if derived:
            graph = legacy_to_graph(b)
        return jsonify({"flow": graph, "derived": derived,
                        "stats": Flow(graph).stats()})

    data = request.get_json(force=True) or {}
    cleaned, verr = _clean_flow(data.get("flow"))
    if verr:
        return jsonify({"error": verr}), 400
    storage.set_flow(bot_id, cleaned)
    out = {"flow": cleaned, "stats": Flow(cleaned).stats(), "message": "نقشه ذخیره شد"}
    if data.get("apply"):
        out["synced"] = _sync_commands_from_flow(bot_id, cleaned)
        out["message"] = "نقشه ذخیره و روی ربات اعمال شد"
    return jsonify(out)


@flask_app.post("/api/bots/<bot_id>/flow/apply")
@auth_required
def api_flow_apply(bot_id):
    _, err = get_owned_bot(bot_id)
    if err:
        return err
    b = storage.get_bot(bot_id)
    graph = b.get("flow")
    if not (isinstance(graph, dict) and graph.get("nodes")):
        graph = legacy_to_graph(b)
    n = _sync_commands_from_flow(bot_id, graph)
    msg = f"نقشه با {n} دستور روی ربات اعمال شد"
    data = request.get_json(silent=True) or {}
    if data.get("sync_menu", True) and b.get("auto_menu", True):
        sub_bot = factory.get_bot_instance(bot_id)
        runner = factory._runners.get(bot_id)
        if sub_bot and runner and runner._loop:
            try:
                cmds = storage.get_bot(bot_id).get("commands", {})
                menu = [BotCommand(command=k[:32],
                                   description=((v or {}).get("description") or k)[:48])
                        for k, v in cmds.items()]
                asyncio.run_coroutine_threadsafe(
                    sub_bot.set_my_commands(menu), runner._loop).result(timeout=15)
                msg += " و منوی تلگرام همگام شد"
            except Exception as e:
                log.warning(f"sync-menu پس از apply {bot_id}: {e}")
    return jsonify({"message": msg, "count": n})


@flask_app.post("/api/bots/<bot_id>/flow/test")
@auth_required
def api_flow_test(bot_id):
    """یک ورودی (دستور / متن کلیدواژه / callback دکمه) را روی گراف اجرا می‌کند — بدون ارسال پیام"""
    b, err = get_owned_bot(bot_id)
    if err:
        return err
    data = request.get_json(force=True) or {}
    what = str(data.get("input") or "").strip()
    if not what:
        return jsonify({"error": "ورودی خالی است"}), 400

    graph = data.get("flow")
    if isinstance(graph, dict) and graph.get("nodes"):
        cleaned, verr = _clean_flow(graph)
        if verr:
            return jsonify({"error": verr}), 400
        flow = Flow(cleaned)
    else:
        flow = flow_from(b)

    kind = data.get("as") or ("cmd" if what.startswith("/") else
                              "callback" if what.startswith(CB_PREFIX) else "text")
    if kind == "cmd":
        nd = flow.entry_for(what.lstrip("/").split(maxsplit=1)[0])
    elif kind == "callback":
        p = flow.parse(what)
        nd = (p or {}).get("tgt") or (p or {}).get("src")
    else:
        nd = flow.match_keyword(what)

    if not nd:
        return jsonify({"matched": False, "result": None,
                        "hint": "هیچ کارتی با این ورودی مطابقت نکرد"})
    msg = flow.render(nd)
    if not msg:
        return jsonify({"matched": True, "result": None,
                        "hint": "کارت پیدا شد ولی محتوای قابل‌ارسال ندارد"})
    btn_rows = []
    for u in msg["urlb"]:
        btn_rows.append([{"text": (u.get("text") or "لینک")[:64], "url": u["url"]}])
    cbs = msg["cb"]
    for i in range(0, len(cbs), 3):
        btn_rows.append([{"text": x["text"], "callback_data": x["data"]} for x in cbs[i:i + 3]])
    return jsonify({
        "matched": True,
        "node": {"id": nd["id"], "kind": nd["kind"],
                 "label": nd.get("name") or nd.get("label") or ""},
        "result": {
            "text": msg["text"],
            "media": {"type": msg["mtype"], "url": msg["url"]} if msg.get("url") else None,
            "buttons": btn_rows,
        },
    })


# ── بک‌آپ / بازگردانی کامل (دستورها + نقشه + قوانین + تنظیمات) ───────
@flask_app.get("/api/bots/<bot_id>/backup")
@auth_required
def api_backup(bot_id):
    b, err = get_owned_bot(bot_id)
    if err:
        return err
    return jsonify({"backup": {
        "version": 3,
        "exported_at": now_ir().isoformat(),
        "bot_username": b.get("bot_username", ""),
        "commands": b.get("commands", {}),
        "flow": b.get("flow") or legacy_to_graph(b),
        "rules": b.get("rules", []),
        "settings": {k: b.get(k) for k in
                     ("welcome_message", "unknown_reply", "auto_menu",
                      "force_join_channel", "ticket_admins")},
    }})


@flask_app.post("/api/bots/<bot_id>/restore")
@auth_required
def api_restore(bot_id):
    _, err = get_owned_bot(bot_id)
    if err:
        return err
    payload = request.get_json(force=True) or {}
    bk = payload.get("backup") if isinstance(payload.get("backup"), dict) else payload
    done = []

    cmds = bk.get("commands")
    if isinstance(cmds, dict):
        for name, c in cmds.items():
            if not isinstance(c, dict):
                continue
            nm = re.sub(r"[^a-z0-9_]", "", str(name).lstrip("/").lower())
            if not nm:
                continue
            storage.set_command(bot_id, nm, str(c.get("response") or "")[:4000],
                                c.get("buttons"), c.get("photo"), c.get("media"),
                                str(c.get("description") or "")[:60])
            for cid, resp in (c.get("callbacks") or {}).items():
                storage.set_command_callback(bot_id, nm, str(cid)[:64], str(resp)[:4000])
        done.append("دستورها")

    # نقشه بعد از دستورها می‌آید؛ منبع حقیقت است و باید برنده باشد
    if isinstance(bk.get("flow"), dict) and bk["flow"].get("nodes"):
        cleaned, verr = _clean_flow(bk["flow"])
        if verr:
            return jsonify({"error": f"نقشهٔ بک‌آپ: {verr}"}), 400
        storage.set_flow(bot_id, cleaned)
        _sync_commands_from_flow(bot_id, cleaned)
        done.append("نقشه")

    if isinstance(bk.get("rules"), list):
        cleaned, verr = _clean_rules(bk["rules"])
        if not verr:
            storage.set_bot_config(bot_id, {"rules": cleaned})
            done.append("قوانین")

    st = bk.get("settings")
    if isinstance(st, dict):
        storage.set_bot_config(bot_id, {k: st[k] for k in
                                        ("welcome_message", "unknown_reply", "auto_menu")
                                        if k in st})
        if "force_join_channel" in st:
            storage.set_force_join(bot_id, st["force_join_channel"] or None)
        if isinstance(st.get("ticket_admins"), list):
            storage.set_ticket_admins(bot_id, [int(x) for x in st["ticket_admins"]
                                               if str(x).lstrip("-").isdigit()][:20])
        done.append("تنظیمات")

    if not done:
        return jsonify({"error": "چیزی برای بازگردانی پیدا نشد"}), 400
    return jsonify({"message": "بازگردانی شد: " + " · ".join(done), "restored": done})


# ── صندوق تیکت‌ها ────────────────────────────────────────────────────
@flask_app.route("/api/bots/<bot_id>/tickets", methods=["GET", "POST"])
@auth_required
def api_tickets(bot_id):
    b, err = get_owned_bot(bot_id)
    if err:
        return err

    if request.method == "GET":
        return jsonify({"tickets": storage.ticket_list(bot_id, 100),
                        "admins": b.get("ticket_admins", [])})

    data = request.get_json(force=True) or {}

    if "admins" in data:
        uids = list(dict.fromkeys(int(x) for x in (data.get("admins") or [])
                                  if str(x).lstrip("-").isdigit()))[:20]
        storage.set_ticket_admins(bot_id, uids)
        return jsonify({"admins": uids, "message": "پذیرنده‌ها ذخیره شدند"})

    tid = str(data.get("id") or "").strip()
    text = str(data.get("reply") or "").strip()
    if not tid or not text:
        return jsonify({"error": "کد تیکت و متن پاسخ لازم است"}), 400
    if not storage.ticket_reply(bot_id, tid, text[:4000]):
        return jsonify({"error": "تیکت یافت نشد"}), 404

    sent = False
    sub_bot = factory.get_bot_instance(bot_id)
    runner = factory._runners.get(bot_id)
    tk = next((t for t in storage.ticket_list(bot_id, 200) if t.get("id") == tid), None)
    if sub_bot and runner and runner._loop and tk:
        try:
            asyncio.run_coroutine_threadsafe(
                sub_bot.send_message(
                    tk["uid"],
                    f"🎟 <b>پاسخ تیکت {tid}</b>\n\n{_html(text)}",
                    parse_mode=ParseMode.HTML),
                runner._loop).result(timeout=10)
            sent = True
        except Exception as e:
            log.warning(f"ارسال پاسخ تیکت {tid}: {e}")
    return jsonify({"delivered": sent,
                    "message": "پاسخ ارسال شد" if sent else
                               "پاسخ ذخیره شد — برای ارسال، ربات باید روشن باشد"})



# ══════════════════════════════════════════════════════════════════════
# Endpoint ارسال کد تایید (تلگرام)
# ══════════════════════════════════════════════════════════════════════
@flask_app.post("/api/auth/send-code")
def api_send_code():
    """ارسال کد تایید به تلگرام کاربر"""
    try:
        data = request.get_json(force=True) or {}
        telegram_username = (data.get("telegram_username") or "").strip().lower().replace("@", "")
        device_id = (data.get("device_id") or "").strip()
        
        log.info(f"📱 درخواست کد تایید برای: @{telegram_username}")
        
        if not telegram_username or len(telegram_username) < 3:
            return jsonify({"error": "نام کاربری تلگرام نامعتبر است"}), 400
        
        # ✅ چک کردن اینکه آیا کاربر قبلاً ثبت‌نام کامل کرده
        # (یعنی password_hash دارد = ثبت‌نام کامل شده)
        existing = storage._find_user_by({"username": telegram_username})
        if existing and existing.get("password_hash"):
            return jsonify({"error": "این کاربر قبلاً ثبت‌نام کرده است. از بخش ورود استفاده کنید."}), 400
        
        # پیدا کردن user از telegram_username (که mother_start ساخته)
        user_record = storage._find_user_by({"telegram_username": telegram_username})
        
        if not user_record:
            return jsonify({
                "error": "این username در ربات ما یافت نشد. لطفاً ابتدا ربات را در تلگرام استارت کنید."
            }), 404
        
        user_id = user_record.get("telegram_user_id") or user_record.get("uid")
        
        if not user_id:
            return jsonify({"error": "شناسه کاربر یافت نشد"}), 400
        
        ip = request.headers.get("X-Forwarded-For", request.remote_addr) or ""
        if "," in ip:
            ip = ip.split(",")[0].strip()
        
        can_register, reason = storage.check_registration_limit(ip, device_id)
        if not can_register:
            return jsonify({"error": reason}), 429
        
        code = f"{random.randint(100000, 999999)}"
        
        if not hasattr(storage, '_verification_codes'):
            storage._verification_codes = {}
        vc = storage._verification_codes
        if len(vc) > 500:
            for k in [k for k, v in vc.items() if v.get("expires", 0) < time.time()]:
                vc.pop(k, None)

        vc[telegram_username] = {
            "code": code,
            "user_id": user_id,
            "expires": time.time() + 600,
            "attempts": 0,
        }
        
        code_sent = False
        
        if mother_bot_instance and mother_bot_loop:
            try:
                message_text = (
                    f"🔐 <b>کد تایید GramSaz</b>\n\n"
                    f"سلام {user_record.get('telegram_first_name', 'دوست عزیز')}!\n\n"
                    f"کد ۶ رقمی شما:\n"
                    f"<code>{code}</code>\n\n"
                    f"⏰ این کد تا ۱۰ دقیقه معتبر است.\n"
                    f"🔒 این کد را با کسی به اشتراک نگذارید."
                )
                
                future = asyncio.run_coroutine_threadsafe(
                    mother_bot_instance.send_message(
                        chat_id=user_id,
                        text=message_text,
                        parse_mode=ParseMode.HTML
                    ),
                    mother_bot_loop
                )
                future.result(timeout=10)
                
                log.info(f"✅ کد تایید به @{telegram_username} (ID: {user_id}) در تلگرام ارسال شد")
                code_sent = True
                
            except Exception as e:
                log.error(f"❌ خطا در ارسال به تلگرام: {e}")
        else:
            log.warning("⚠️ ربات مادر آماده نیست")
        
        log.info(f"📱 کد تایید برای @{telegram_username}: {code}")
        
        return jsonify({
            "message": "کد تایید به تلگرام شما ارسال شد" + ("" if code_sent else " (کد در لاگ سرور نیز موجود است)"),
            "telegram_username": telegram_username,
            "code_sent": code_sent
        })
        
    except Exception as e:
        log.error(f"❌ خطا در send-code: {e}")
        return jsonify({"error": "خطای سرور"}), 500


# ══════════════════════════════════════════════════════════════════════
# /api/auth/* - سیستم احراز هویت
# ══════════════════════════════════════════════════════════════════════
@flask_app.post("/api/auth/register")
def api_register():
    """ثبت‌نام کاربر جدید"""
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = (data.get("password") or "").strip()
    full_name = (data.get("full_name") or "").strip()
    referral_code = (data.get("referral_code") or "").strip()
    device_id = (data.get("device_id") or "").strip()
    telegram_username = (data.get("telegram_username") or "").strip().lower().replace("@", "")
    verification_code = (data.get("verification_code") or "").strip()
    
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or ""
    if "," in ip:
        ip = ip.split(",")[0].strip()
    
    # اعتبارسنجی‌ها
    if not username or len(username) < 3:
        return jsonify({"error": "نام کاربری باید حداقل ۳ کاراکتر باشد"}), 400
    if not password or len(password) < 6:
        return jsonify({"error": "رمز عبور باید حداقل ۶ کاراکتر باشد"}), 400
    if not device_id:
        return jsonify({"error": "شناسه دستگاه الزامی است"}), 400
    if not telegram_username:
        return jsonify({"error": "username تلگرام الزامی است"}), 400
    if not verification_code:
        return jsonify({"error": "کد تایید الزامی است"}), 400
    
    # بررسی کد تایید
    if not hasattr(storage, '_verification_codes'):
        return jsonify({"error": "کد تایید منقضی شده"}), 400
    
    code_data = storage._verification_codes.get(telegram_username)
    if not code_data:
        return jsonify({"error": "کد تایید یافت نشد یا منقضی شده"}), 400
    
    if time.time() > code_data["expires"]:
        del storage._verification_codes[telegram_username]
        return jsonify({"error": "کد تایید منقضی شده است"}), 400
    
    code_data["attempts"] += 1
    if code_data["attempts"] > 5:
        del storage._verification_codes[telegram_username]
        return jsonify({"error": "تعداد تلاش‌ها بیش از حد مجاز"}), 400
    
    if code_data["code"] != verification_code:
        return jsonify({"error": f"کد تایید نادرست است ({code_data['attempts']}/5 تلاش)"}), 400
    
    del storage._verification_codes[telegram_username]
    log.info(f"✅ کد تایید برای @{telegram_username} تایید شد")

    try:
        user, token = storage.register_user(
            username, password, full_name, 
            referral_code=referral_code if referral_code else None,
            ip=ip,
            device_id=device_id
        )
        
        message = "ثبت‌نام موفق"
        if referral_code:
            message += " - ۵۰ کوین هدیه دریافت کردید!"
        
        return jsonify({
            "message": message,
            "user": _public_user(user),
            "token": token
        }), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.error(f"❌ خطا در ثبت‌نام: {e}")
        return jsonify({"error": "خطای سرور در ثبت‌نام"}), 500


@flask_app.post("/api/auth/login")
def api_login():
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    
    if not username or not password:
        return jsonify({"error": "نام کاربری و رمز عبور الزامی است"}), 400
    
    try:
        user, token = storage.login_user(username, password)
        return jsonify({
            "message": "ورود موفق",
            "user": _public_user(user),
            "token": token
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        log.error(f"❌ خطا در ورود: {e}")
        return jsonify({"error": "خطای سرور در ورود"}), 500


@flask_app.post("/api/auth/logout")
def api_logout():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if token:
        storage.logout_user(token)
    return jsonify({"message": "خروج موفق"})


@flask_app.get("/api/auth/me")
def api_auth_me():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        return jsonify({"error": "توکن ارسال نشده"}), 401
    
    user = storage.get_user_by_token(token)
    if not user:
        return jsonify({"error": "توکن نامعتبر"}), 401

    return jsonify(_public_user(user))


# ══════════════════════════════════════════════════════════════════════
# /api/admin/full-stats - آمار کامل پلتفرم (فقط ادمین)
# ══════════════════════════════════════════════════════════════════════
@flask_app.get("/api/admin/full-stats")
@admin_required
def api_admin_full_stats():
    """آمار کامل پلتفرم برای ادمین"""
    try:
        # ── آمار کاربران ──
        all_users = storage.get_all_users()
        total_users = len(all_users)
        
        # کاربران ثبت‌نام شده (با password_hash = ثبت‌نام کامل)
        registered_users = [u for u in all_users if u.get("password_hash")]
        total_registered = len(registered_users)
        
        # کاربران فعال (با auth_token یا از تلگرام)
        active_users = [u for u in all_users if u.get("auth_token") or u.get("telegram_user_id")]
        total_active = len(active_users)
        
        # کاربران ban شده
        banned_users = [u for u in all_users if u.get("is_banned")]
        total_banned = len(banned_users)
        
        # مجموع کوین‌های توزیع شده
        total_coins = sum(u.get("coins", 0) for u in all_users)
        
        # کاربران با رفرال
        users_with_referral = [u for u in all_users if u.get("referred_by")]
        total_referrals = len(users_with_referral)
        
        # ── آمار ربات‌ها ──
        all_bots = storage.get_all_bots()
        total_bots = len(all_bots)
        active_bots = [b for b in all_bots if b.get("is_active")]
        total_active_bots = len(active_bots)
        
        # مجموع پیام‌ها
        total_messages_received = sum(b.get("stats", {}).get("messages_received", 0) for b in all_bots)
        total_messages_sent = sum(b.get("stats", {}).get("messages_sent", 0) for b in all_bots)
        total_broadcasts = sum(b.get("stats", {}).get("broadcasts_sent", 0) for b in all_bots)
        
        # مجموع کاربران ربات‌ها
        total_bot_users = sum(len(b.get("bot_users", [])) for b in all_bots)
        
        # ── Top 10 ربات‌های برتر (بر اساس تعداد پیام) ──
        sorted_bots = sorted(
            all_bots, 
            key=lambda b: b.get("stats", {}).get("messages_received", 0),
            reverse=True
        )[:10]
        
        top_bots = []
        for b in sorted_bots:
            owner = storage.get_user(b.get("owner_uid"))
            top_bots.append({
                "bot_id": b.get("bot_id"),
                "username": b.get("bot_username", "—"),
                "owner_name": owner.get("full_name") if owner else "—",
                "owner_username": owner.get("username") if owner else "—",
                "messages": b.get("stats", {}).get("messages_received", 0),
                "users": len(b.get("bot_users", [])),
                "is_active": b.get("is_active", False),
            })
        
        # ── Top 10 کاربران برتر (بر اساس تعداد ربات) ──
        sorted_users = sorted(
            all_users,
            key=lambda u: len(u.get("bots_created", [])),
            reverse=True
        )[:10]
        
        top_users = []
        for u in sorted_users:
            if not u.get("bots_created"):
                continue
            top_users.append({
                "uid": u.get("uid"),
                "name": u.get("full_name", "—"),
                "username": u.get("username", "—"),
                "telegram_username": u.get("telegram_username", "—"),
                "bots_count": len(u.get("bots_created", [])),
                "coins": u.get("coins", 0),
                "referrals": u.get("referral_count", 0),
                "is_admin": u.get("is_admin", False),
                "is_banned": u.get("is_banned", False),
            })
        
        # ── Top 5 معرف‌ها (بر اساس تعداد زیرمجموعه) ──
        top_referrers = sorted(
            [u for u in all_users if u.get("referral_count", 0) > 0],
            key=lambda u: u.get("referral_count", 0),
            reverse=True
        )[:5]
        
        referrers_data = []
        for u in top_referrers:
            referrers_data.append({
                "name": u.get("full_name", "—"),
                "username": u.get("username", "—"),
                "referral_count": u.get("referral_count", 0),
                "coins": u.get("coins", 0),
            })
        
        # ── آمار رشد روزانه (از daily_stats ربات‌ها) ──
        daily_growth = {}
        for b in all_bots:
            for day, stats in b.get("daily_stats", {}).items():
                if day not in daily_growth:
                    daily_growth[day] = {"new_users": 0, "bots": 0}
                daily_growth[day]["new_users"] += stats.get("new_users", 0)
        
        # مرتب‌سازی بر اساس تاریخ و گرفتن ۷ روز اخیر
        sorted_days = sorted(daily_growth.keys(), reverse=True)[:7]
        recent_growth = [{"date": d, **daily_growth[d]} for d in sorted_days]
        
        return jsonify({
            "overview": {
                "total_users": total_users,
                "total_registered": total_registered,
                "total_active": total_active,
                "total_banned": total_banned,
                "total_bots": total_bots,
                "total_active_bots": total_active_bots,
                "total_messages_received": total_messages_received,
                "total_messages_sent": total_messages_sent,
                "total_broadcasts": total_broadcasts,
                "total_bot_users": total_bot_users,
                "total_coins": total_coins,
                "total_referrals": total_referrals,
            },
            "top_bots": top_bots,
            "top_users": top_users,
            "top_referrers": referrers_data,
            "daily_growth": recent_growth,
        })
        
    except Exception as e:
        log.error(f"❌ خطا در دریافت آمار ادمین: {e}")
        import traceback
        log.error(traceback.format_exc())
        return jsonify({"error": "خطا در دریافت آمار"}), 500

# ══════════════════════════════════════════════════════════════════════
# /api/coins/* - سیستم کوین
# ══════════════════════════════════════════════════════════════════════
@flask_app.get("/api/coins/referral")
@auth_required
def api_coins_referral():
    info = storage.get_user_referral_info(g.current_user["uid"])
    return jsonify(info)

# ══════════════════════════════════════════════════════════════════════
# /api/upload-media - آپلود انواع رسانه به Catbox.moe
# ══════════════════════════════════════════════════════════════════════
@flask_app.post("/api/upload-media")
@auth_required
def api_upload_media():
    """آپلود رسانه (عکس/ویدیو/صوت/فایل) به Catbox.moe"""
    try:
        log.info(f"📤 درخواست آپلود رسانه دریافت شد")
        
        if 'file' not in request.files:
            return jsonify({"error": "فایلی ارسال نشده"}), 400
        
        file = request.files['file']
        
        if not file or not file.filename:
            return jsonify({"error": "نام فایل خالی است"}), 400
        
        original_name = file.filename
        ext = ''
        if '.' in original_name:
            ext = original_name.rsplit('.', 1)[1].lower()
        
        # تشخیص نوع media از روی extension و MIME
        media_type = None
        
        # تعریف نگاشت extension به media type
        type_map = {
            # Photo
            'jpg': 'photo', 'jpeg': 'photo', 'png': 'photo', 'webp': 'photo',
            # Video
            'mp4': 'video', 'webm': 'video', 'mov': 'video', 'avi': 'video', 'mkv': 'video',
            # Audio
            'mp3': 'audio', 'ogg': 'audio', 'm4a': 'audio', 'wav': 'audio', 'flac': 'audio',
            # Animation
            'gif': 'animation',
            # Document
            'pdf': 'document', 'zip': 'document', 'rar': 'document', '7z': 'document',
            'doc': 'document', 'docx': 'document', 'xls': 'document', 'xlsx': 'document',
            'ppt': 'document', 'pptx': 'document', 'txt': 'document', 'csv': 'document',
        }
        
        if ext in type_map:
            media_type = type_map[ext]
        else:
            # اگر extension ناشناخته بود، به عنوان document در نظر بگیر
            media_type = 'document'
        
        # محدودیت حجم بر اساس نوع
        max_size_mb = 10 if media_type == 'photo' else 50
        max_size_bytes = max_size_mb * 1024 * 1024
        
        # خواندن فایل
        file_data = file.read()
        size = len(file_data)
        
        if size > max_size_bytes:
            return jsonify({
                "error": f"حجم فایل باید کمتر از {max_size_mb} مگابایت باشد"
            }), 400
        
        if size < 100:
            return jsonify({"error": "فایل خیلی کوچک است"}), 400
        
        # MIME type
        mime_type = file.content_type or 'application/octet-stream'
        
        # نام فایل ساده (بدون کاراکتر عجیب)
        safe_filename = f"gramsaz_{int(time.time())}_{secrets.token_hex(4)}.{ext}"
        
        log.info(f"📤 آپلود {media_type} به Catbox.moe ({size} bytes)...")
        
        import requests as req_lib
        
        response = req_lib.post(
            'https://catbox.moe/user/api.php',
            data={'reqtype': 'fileupload'},
            files={'fileToUpload': (safe_filename, file_data, mime_type)},
            timeout=60  # timeout بیشتر برای فایل‌های بزرگ
        )
        
        log.info(f"   Catbox Status: {response.status_code}")
        
        if response.status_code != 200:
            return jsonify({"error": f"خطا در آپلود (کد {response.status_code})"}), 500
        
        file_url = response.text.strip()
        
        if not file_url.startswith('https://'):
            log.error(f"❌ پاسخ نامعتبر از Catbox: {file_url}")
            return jsonify({"error": "پاسخ نامعتبر از سرور"}), 500
        
        log.info(f"✅ رسانه آپلود شد: {file_url}")
        
        # اطلاعات اضافی برای نمایش در UI
        type_icons = {
            'photo': '🖼', 'video': '🎬', 'audio': '🎵',
            'animation': '🎞', 'document': '📎'
        }
        type_names = {
            'photo': 'تصویر', 'video': 'ویدیو', 'audio': 'صوت',
            'animation': 'انیمیشن', 'document': 'فایل'
        }
        
        return jsonify({
            "url": file_url,
            "filename": original_name,
            "size": size,
            "type": media_type,
            "icon": type_icons.get(media_type, '📎'),
            "type_name": type_names.get(media_type, 'فایل')
        })
        
    except Exception as e:
        log.error(f"❌ خطا در آپلود: {type(e).__name__}: {e}")
        import traceback
        log.error(traceback.format_exc())
        return jsonify({"error": f"خطای سرور: {str(e)}"}), 500

# ══════════════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════════════
def main():
    factory.start_all_active()

    t = threading.Thread(target=run_mother_bot, daemon=True)
    t.start()

    log.info(f"🌐 Flask روی http://{API_HOST}:{API_PORT}")
    flask_app.run(host=API_HOST, port=API_PORT, threaded=True)


if __name__ == "__main__":
    main()
