
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
#  КОНФИГУРАЦИЯ БОТА
# ══════════════════════════════════════════════════════
TOKEN = "8938769101:AAGpMsifotw_yOCWktPmbQipre5fvwXtnnE"
ADMIN_IDS = [1692313698, 8339239363] 
STARS_PRICE = 199
DB_FILE = "astrovpn.sqlite3"
REF_BONUS_DAYS = 7  # Сколько дней даем за друга

# ══════════════════════════════════════════════════════
#  НАСТРОЙКИ ВАШЕГО VPN СЕРВЕРА
# ══════════════════════════════════════════════════════
VPN_SERVER_IP = "1.2.3.4"
VPN_PORT = 443
VPN_SNI = "google.com"
VPN_SID = "shortid"
VPN_PBK = "public_key"

def generate_vless_link(user_uuid, name="AstroVPN"):
    return (
        f"vless://{user_uuid}@{VPN_SERVER_IP}:{VPN_PORT}?"
        f"type=tcp&security=reality&fp=chrome&pbk={VPN_PBK}&"
        f"sni={VPN_SNI}&sid={VPN_SID}&flow=xtls-rprx-vision#{name}"
    )

# ══════════════════════════════════════════════════════

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
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    tg_id INTEGER PRIMARY KEY, 
                    name TEXT, 
                    username TEXT, 
                    referred_by INTEGER,
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

    def add_user(self, tg_id: int, name: str, username: str, referred_by: int = None):
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO users (tg_id, name, username, referred_by) VALUES (?, ?, ?, ?)", 
                (tg_id, name, username, referred_by)
            )

    def get_user(self, tg_id: int):
        return self.conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()

    def get_user_sub(self, tg_id: int):
        return self.conn.execute("SELECT * FROM subscriptions WHERE user_id = ? AND is_active = 1", (tg_id,)).fetchone()

    def add_sub(self, tg_id: int, days: int):
        sub = self.get_user_sub(tg_id)
        client_uuid = str(uuid.uuid4())
        
        if sub:
            # Продлеваем существующую
            current_expiry = datetime.strptime(sub['expires_at'], "%Y-%m-%d %H:%M:%S.%f")
            new_expiry = max(current_expiry, datetime.now()) + timedelta(days=days)
            client_uuid = sub['client_uuid']
            with self.conn:
                self.conn.execute("UPDATE subscriptions SET expires_at = ? WHERE user_id = ? AND is_active = 1", (new_expiry, tg_id))
        else:
            # Создаем новую
            new_expiry = datetime.now() + timedelta(days=days)
            with self.conn:
                self.conn.execute("INSERT INTO subscriptions (user_id, client_uuid, expires_at) VALUES (?, ?, ?)", (tg_id, client_uuid, new_expiry))
        
        return client_uuid, new_expiry

    def get_referral_count(self, tg_id: int):
        return self.conn.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (tg_id,)).fetchone()[0]

db = Database(DB_FILE)
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# --- KEYBOARDS ---
def main_kb():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💎 Купить подписку", callback_data="buy"))
    builder.row(InlineKeyboardButton(text="👤 Мой профиль", callback_data="profile"))
    builder.row(InlineKeyboardButton(text="🎁 Рефералы", callback_data="refs"))
    builder.row(InlineKeyboardButton(text="📚 Инструкция", callback_data="guide"))
    return builder.as_markup()

# --- HANDLERS ---
@dp.message(CommandStart())
async def cmd_start(message: Message):
    args = message.text.split()
    referred_by = None
    if len(args) > 1 and args[1].startswith("ref"):
        try:
            ref_id = int(args[1].replace("ref", ""))
            if ref_id != message.from_user.id:
                referred_by = ref_id
        except:
            pass
    
    db.add_user(message.from_user.id, message.from_user.full_name, message.from_user.username, referred_by)
    await message.answer(
        "👋 <b>Добро пожаловать в AstroVPN!</b>\n\nСамый быстрый VLESS VPN с защитой Reality. Мы не храним логи и обеспечиваем максимальную анонимность.", 
        reply_markup=main_kb()
    )

@dp.callback_query(F.data == "profile")
async def callback_profile(call: CallbackQuery):
    sub = db.get_user_sub(call.from_user.id)
    if sub:
        vless_link = generate_vless_link(sub['client_uuid'])
        text = (
            f"👤 <b>Ваш профиль</b>\n"
            f"🆔 ID: <code>{call.from_user.id}</code>\n"
            f"💎 Статус: ✅ Активна до {sub['expires_at']}\n\n"
            f"🔑 Ваш ключ (нажмите, чтобы скопировать):\n"
            f"<code>{vless_link}</code>"
        )
    else:
        text = f"👤 <b>Ваш профиль</b>\n🆔 ID: <code>{call.from_user.id}</code>\n💎 Статус: ❌ Неактивна"
    
    await call.message.edit_text(text, reply_markup=main_kb())

@dp.callback_query(F.data == "refs")
async def callback_refs(call: CallbackQuery):
    count = db.get_referral_count(call.from_user.id)
    bot_user = await bot.get_me()
    ref_link = f"https://t.me/{bot_user.username}?start=ref{call.from_user.id}"
    
    text = (
        f"🎁 <b>Реферальная программа</b>\n\n"
        f"Приглашайте друзей и получайте <b>{REF_BONUS_DAYS} дней</b> подписки бесплатно за каждую их покупку!\n\n"
        f"👥 Приглашено друзей: <b>{count}</b>\n"
        f"🔗 Ваша ссылка:\n<code>{ref_link}</code>"
    )
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
    
    # Обработка реферала
    user = db.get_user(message.from_user.id)
    if user and user['referred_by']:
        ref_id = user['referred_by']
        db.add_sub(ref_id, REF_BONUS_DAYS)
        try:
            await bot.send_message(ref_id, f"🎉 Ваш друг оплатил подписку! Вам начислено <b>{REF_BONUS_DAYS} дней</b> бонуса.")
        except:
            pass

    await message.answer(f"✅ <b>Оплата прошла успешно!</b>\n\nВаша подписка активна до: {expires}\nКлюч доступен в профиле.")

@dp.callback_query(F.data == "guide")
async def callback_guide(call: CallbackQuery):
    await call.message.edit_text(
        "📚 <b>Инструкция по подключению</b>\n\n"
        "<b>1. Скачайте приложение:</b>\n"
        "• Android: <a href='https://play.google.com/store/apps/details?id=com.v2ray.ang'>v2rayNG</a>\n"
        "• iOS: <a href='https://apps.apple.com/us/app/v2box-v2ray-client/id6446814690'>V2Box</a>\n"
        "• Windows: <a href='https://github.com/2dust/v2rayN/releases'>v2rayN</a>\n\n"
        "<b>2. Настройка:</b>\n"
        "Скопируйте ключ из профиля, откройте приложение и выберите 'Импорт из буфера обмена'.\n\n"
        "<b>3. Наслаждайтесь!</b>", 
        disable_web_page_preview=True,
        reply_markup=main_kb()
    )

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
