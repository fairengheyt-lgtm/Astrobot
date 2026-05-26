
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
    LabeledPrice, PreCheckoutQuery, ContentType, Message, CallbackQuery
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

def parse_admin_ids() -> list[int]:
    raw = os.getenv("BOT_ADMINS") or os.getenv("ADMIN_IDS") or os.getenv("ADMIN_ID") or "1692313698,8339239363"
    result: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit(): result.append(int(part))
    return list(dict.fromkeys(result))

@dataclass(frozen=True)
class Config:
    bot_token: str = TOKEN
    admin_ids: list[int] = None
    db_file: str = "astrovpn.sqlite3"
    price_rub: int = 199
    stars_amount: int = 199
    support_url: str = "https://t.me/support_user" # Замените на свой
    guide_url: str = "https://telegra.ph/AstroVPN-Manual" # Замените на свой

config = Config(admin_ids=parse_admin_ids())
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
                    client_uuid TEXT UNIQUE,
                    expires_at DATETIME,
                    is_active BOOLEAN DEFAULT 1,
                    FOREIGN KEY(user_id) REFERENCES users(tg_id)
                )
            """)

    def add_user(self, tg_id: int, name: str, username: str):
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO users (tg_id, name, username) VALUES (?, ?, ?)",
                (tg_id, name, username)
            )

    def get_user_sub(self, tg_id: int):
        return self.conn.execute(
            "SELECT * FROM subscriptions WHERE user_id = ? AND is_active = 1", (tg_id,)
        ).fetchone()

    def get_stats(self):
        users = self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_subs = self.conn.execute("SELECT COUNT(*) FROM subscriptions WHERE is_active = 1").fetchone()[0]
        return users, active_subs

db = Database(config.db_file)

# --- KEYBOARDS ---
def main_kb():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💎 Купить подписку", callback_data="buy"))
    builder.row(InlineKeyboardButton(text="👤 Мой профиль", callback_data="profile"))
    builder.row(InlineKeyboardButton(text="📚 Инструкция", callback_data="guide"))
    builder.row(InlineKeyboardButton(text="🆘 Поддержка", url=config.support_url))
    return builder.as_markup()

def back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="start")]])

# --- HANDLERS ---
@dp.message(CommandStart())
@dp.callback_query(F.data == "start")
async def cmd_start(event: Message | CallbackQuery):
    user = event.from_user
    db.add_user(user.id, user.full_name, user.username)
    
    text = (
        "👋 **Добро пожаловать в AstroVPN Pro!**\n\n"
        "Ваш персональный доступ к свободному интернету.\n"
        "Используем протокол VLESS Reality — самый стабильный на сегодня."
    )
    
    if isinstance(event, Message):
        await event.answer(text, reply_markup=main_kb())
    else:
        await event.message.edit_text(text, reply_markup=main_kb())

@dp.callback_query(F.data == "profile")
async def callback_profile(call: CallbackQuery):
    sub = db.get_user_sub(call.from_user.id)
    if sub:
        status = f"✅ Активна до {sub['expires_at']}"
        key = f"<code>vless://{sub['client_uuid']}@server:443...</code>"
    else:
        status = "❌ Неактивна"
        key = "Купите подписку, чтобы получить ключ."

    text = (
        f"👤 **Ваш профиль**\n"
        f"🆔 ID: `{call.from_user.id}`\n"
        f"💎 Статус: {status}\n\n"
        f"🔑 Ваш ключ:\n{key}"
    )
    await call.message.edit_text(text, reply_markup=back_kb())

@dp.callback_query(F.data == "buy")
async def callback_buy(call: CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=f"⭐ Telegram Stars ({config.stars_amount})", callback_data="pay_stars"))
    builder.row(InlineKeyboardButton(text=f"💳 Карта/СБП ({config.price_rub}₽)", callback_data="pay_card"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="start"))
    
    await call.message.edit_text(
        "💎 **Выберите способ оплаты**\n\n"
        "Подписка на 30 дней дает полный доступ ко всем серверам.",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data == "guide")
async def callback_guide(call: CallbackQuery):
    text = (
        "📚 **Инструкция по подключению**\n\n"
        "1. Скачайте приложение **V2RayNG** (Android) или **v2box** (iOS).\n"
        "2. Скопируйте ваш ключ из профиля.\n"
        "3. Нажмите кнопку '+' или 'Import from clipboard' в приложении.\n"
        "4. Нажмите кнопку подключения.\n\n"
        f"Подробный гайд: {config.guide_url}"
    )
    await call.message.edit_text(text, reply_markup=back_kb(), disable_web_page_preview=True)

# --- ADMIN COMMANDS ---
@dp.message(Command("admin"), F.from_user.id.in_(config.admin_ids))
async def cmd_admin(message: Message):
    u, s = db.get_stats()
    text = (
        "⚙️ **Админ-панель AstroVPN**\n\n"
        f"👥 Всего пользователей: `{u}`\n"
        f"💎 Активных подписок: `{s}`\n\n"
        "**Команды:**\n"
        "• `/stats` — Общая статистика\n"
        "• `/give <id> <days>` — Выдать подписку\n"
        "• `/kick <id>` — Забрать подписку\n"
        "• `/backup` — Получить файл БД"
    )
    await message.answer(text)

@dp.message(Command("backup"), F.from_user.id.in_(config.admin_ids))
async def cmd_backup(message: Message):
    file = FSInputFile(config.db_file)
    await message.answer_document(file, caption="📦 Бэкап базы данных")

# --- MAIN ---
async def main():
    logger.info("AstroVPN Pro is starting...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
