
import asyncio
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, 
    LabeledPrice, PreCheckoutQuery, Message, CallbackQuery, FSInputFile
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties

# ══════════════════════════════════════════════════════
#  КОНФИГУРАЦИЯ (ВАШ ID ПРОПИСАН ЖЕСТКО)
# ══════════════════════════════════════════════════════
TOKEN = "8938769101:AAGpMsifotw_yOCWktPmbQipre5fvwXtnnE"
ADMIN_IDS = [1692313698, 8339239363] 
STARS_PRICE = 199
PRICE_RUB = 199
DB_FILE = "astrovpn.sqlite3"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("astrovpn")

# --- DATABASE ---
class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        with self.conn:
            self.conn.execute("CREATE TABLE IF NOT EXISTS users (tg_id INTEGER PRIMARY KEY, name TEXT, username TEXT, registered_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, client_uuid TEXT UNIQUE, expires_at DATETIME, is_active BOOLEAN DEFAULT 1, FOREIGN KEY(user_id) REFERENCES users(tg_id))")

    def add_user(self, tg_id: int, name: str, username: str):
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO users (tg_id, name, username) VALUES (?, ?, ?)", (tg_id, name, username))

    def get_user_sub(self, tg_id: int):
        return self.conn.execute("SELECT * FROM subscriptions WHERE user_id = ? AND is_active = 1", (tg_id,)).fetchone()

    def add_sub(self, tg_id: int, days: int):
        expires_at = datetime.now() + timedelta(days=days)
        client_uuid = str(uuid.uuid4())
        with self.conn:
            self.conn.execute("INSERT INTO subscriptions (user_id, client_uuid, expires_at) VALUES (?, ?, ?)", (tg_id, client_uuid, expires_at))
        return client_uuid, expires_at

    def remove_sub(self, tg_id: int):
        with self.conn:
            self.conn.execute("UPDATE subscriptions SET is_active = 0 WHERE user_id = ?", (tg_id,))

db = Database(DB_FILE)
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# --- KEYBOARDS ---
def main_kb():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💎 Купить подписку", callback_data="buy"))
    builder.row(InlineKeyboardButton(text="👤 Мой профиль", callback_data="profile"))
    builder.row(InlineKeyboardButton(text="📚 Инструкция", callback_data="guide"))
    return builder.as_markup()

# --- HANDLERS ---
@dp.message(CommandStart())
async def cmd_start(message: Message):
    db.add_user(message.from_user.id, message.from_user.full_name, message.from_user.username)
    await message.answer("👋 <b>Добро пожаловать в AstroVPN!</b>\n\nСамый быстрый VLESS VPN. Нажмите кнопку ниже, чтобы начать.", reply_markup=main_kb())

@dp.callback_query(F.data == "profile")
async def callback_profile(call: CallbackQuery):
    sub = db.get_user_sub(call.from_user.id)
    if sub:
        text = f"👤 <b>Ваш профиль</b>\n🆔 ID: <code>{call.from_user.id}</code>\n💎 Статус: ✅ Активна до {sub['expires_at']}\n\n🔑 Ваш ключ:\n<code>vless://{sub['client_uuid']}@server:443?type=tcp&security=reality&fp=chrome&sni=google.com&sid=shortid&flow=xtls-rprx-vision#AstroVPN</code>"
    else:
        text = f"👤 <b>Ваш профиль</b>\n🆔 ID: <code>{call.from_user.id}</code>\n💎 Статус: ❌ Неактивна"
    await call.message.edit_text(text, reply_markup=main_kb())

@dp.callback_query(F.data == "buy")
async def callback_buy(call: CallbackQuery):
    await bot.send_invoice(
        chat_id=call.from_user.id,
        title="VPN Подписка (30 дней)",
        description="Доступ к высокоскоростному VPN на 30 дней.",
        payload="vpn_30_days",
        currency="XTR",
        prices=[LabeledPrice(label="30 дней", amount=STARS_PRICE)]
    )
    await call.answer()

@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)

@dp.message(F.successful_payment)
async def success_payment(message: Message):
    uid, expires = db.add_sub(message.from_user.id, 30)
    await message.answer(f"✅ <b>Оплата прошла успешно!</b>\n\nВаша подписка активна до: {expires}\nКлюч доступен в профиле.")

@dp.callback_query(F.data == "guide")
async def callback_guide(call: CallbackQuery):
    await call.message.edit_text("📚 <b>Как подключиться?</b>\n\n1. Скачайте v2rayNG (Android) или V2Box (iOS).\n2. Скопируйте ключ из профиля.\n3. Импортируйте ключ в приложение.", reply_markup=main_kb())

# --- ADMIN ---
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    count = db.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    await message.answer(f"⚙️ <b>Админ-панель</b>\n\nВсего пользователей: {count}\n\nКоманды:\n/give [id] [days]\n/kick [id]\n/backup")

@dp.message(Command("give"))
async def cmd_give(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    args = message.text.split()
    if len(args) < 3: return await message.answer("Формат: /give [id] [days]")
    db.add_sub(int(args[1]), int(args[2]))
    await message.answer(f"✅ Подписка выдана пользователю {args[1]}")

@dp.message(Command("kick"))
async def cmd_kick(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    args = message.text.split()
    if len(args) < 2: return await message.answer("Формат: /kick [id]")
    db.remove_sub(int(args[1]))
    await message.answer(f"❌ Подписка пользователя {args[1]} аннулирована.")

@dp.message(Command("backup"))
async def cmd_backup(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    await message.answer_document(FSInputFile(DB_FILE))

async def main():
    logger.info(f"AstroVPN started. Admins: {ADMIN_IDS}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
