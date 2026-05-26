
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, List
from urllib.parse import quote

from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, 
    LabeledPrice, PreCheckoutQuery, ContentType, Message
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

try:
    from py3xui import AsyncApi, Client
except ImportError:
    AsyncApi = None
    Client = None

# ══════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════
TOKEN = "8938769101:AAGpMsifotw_yOCWktPmbQipre5fvwXtnnE"

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("astrovpn")
UTC = timezone.utc

def parse_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None: return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}

def parse_admin_ids() -> list[int]:
    raw = os.getenv("BOT_ADMINS") or os.getenv("ADMIN_IDS") or os.getenv("ADMIN_ID") or "8339239363"
    result: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit(): result.append(int(part))
    return list(dict.fromkeys(result))

@dataclass(frozen=True)
class ServerConfig:
    id: int
    name: str
    host: str
    server_ip: str
    max_clients: int
    inbound_id: Optional[int] = None
    vless_port: int = 443
    vless_sni: str = "www.apple.com"
    vless_fingerprint: str = "chrome"
    vless_flow: str = "xtls-rprx-vision"
    vless_public_key: str = "PUBLIC_KEY"
    vless_short_id: str = "SHORT_ID"

@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: list[int]
    db_file: str
    price_rub: int
    stars_amount: int
    subscription_days: int
    payment_stars_enabled: bool
    payment_tribute_enabled: bool
    tribute_secret: str
    xui_username: str
    xui_password: str
    servers: list[ServerConfig]

    @classmethod
    def from_env(cls) -> "Config":
        raw_servers = os.getenv("XUI_SERVERS_JSON", "[]")
        try:
            server_data = json.loads(raw_servers)
        except:
            server_data = []
            
        servers = [ServerConfig(
            id=int(s.get("id", i)),
            name=s.get("name", f"Server-{i}"),
            host=s["host"].rstrip("/"),
            server_ip=s.get("server_ip", s["host"].split("//")[-1].split(":")[0]),
            max_clients=int(s.get("max_clients", 100)),
            inbound_id=s.get("inbound_id"),
            vless_public_key=os.getenv("VLESS_PUBLIC_KEY", "KEY"),
            vless_short_id=os.getenv("VLESS_SHORT_ID", "SID")
        ) for i, s in enumerate(server_data, 1)]

        return cls(
            bot_token=TOKEN,
            admin_ids=parse_admin_ids(),
            db_file=os.getenv("DB_FILE", "astrovpn.sqlite3").strip(),
            price_rub=int(os.getenv("PRICE_RUB", "199")),
            stars_amount=int(os.getenv("STARS_AMOUNT", "199")),
            subscription_days=int(os.getenv("SUBSCRIPTION_DAYS", "30")),
            payment_stars_enabled=parse_bool("PAYMENT_STARS_ENABLED", True),
            payment_tribute_enabled=parse_bool("PAYMENT_TRIBUTE_ENABLED", True),
            tribute_secret=os.getenv("TRIBUTE_SECRET", "").strip(),
            xui_username=os.getenv("XUI_USERNAME", "admin").strip(),
            xui_password=os.getenv("XUI_PASSWORD", "admin").strip(),
            servers=servers,
        )

config = Config.from_env()
bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# --- DATABASE ---
class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    tg_id INTEGER PRIMARY KEY,
                    name TEXT,
                    username TEXT,
                    registered_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    server_id INTEGER,
                    client_uuid TEXT UNIQUE,
                    email TEXT,
                    expires_at DATETIME,
                    is_active BOOLEAN DEFAULT 1,
                    FOREIGN KEY(user_id) REFERENCES users(tg_id)
                )
            """)

    def get_user(self, tg_id: int):
        return self.conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()

    def add_user(self, tg_id: int, name: str, username: str):
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO users (tg_id, name, username) VALUES (?, ?, ?)",
                (tg_id, name, username)
            )

db = Database(config.db_file)

# --- HANDLERS ---
@dp.message(CommandStart())
async def cmd_start(message: Message):
    db.add_user(message.from_user.id, message.from_user.full_name, message.from_user.username)
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💎 Купить подписку", callback_data="buy"))
    builder.row(InlineKeyboardButton(text="👤 Мой профиль", callback_data="profile"))
    builder.row(InlineKeyboardButton(text="📚 Инструкция", callback_data="guide"))
    
    await message.answer(
        "👋 **Добро пожаловать в AstroVPN Pro!**\n\n"
        "Ваш персональный доступ к свободному интернету.\n"
        "Используем протокол VLESS Reality — самый стабильный на сегодня.",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data == "profile")
async def callback_profile(call: types.CallbackQuery):
    user = db.get_user(call.from_user.id)
    # Logic for profile display
    await call.message.edit_text(f"👤 **Профиль {user['name']}**\nID: `{user['tg_id']}`", reply_markup=None)

# --- ADMIN PANEL ---
@dp.message(Command("stats"), F.from_user.id.in_(config.admin_ids))
async def admin_stats(message: Message):
    await message.answer("📊 **Статистика системы**\n\n...")

# --- MAIN ---
async def main():
    logger.info("AstroVPN Pro is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
