"""
🌳 Telegram-бот "Посади дерево — получи скидку"
Район: Левенцовский, Ростов-на-Дону

Функции:
- Верификация по номеру телефона
- Антиплагиат фото (pHash)
- Выдача промокода
"""

import asyncio
import logging
import os
import secrets
import io
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, Contact, PhotoSize,
    ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

import aiohttp
import imagehash
from PIL import Image

from database import Database

# ── Настройки ────────────────────────────────────────────────────────────────
BOT_TOKEN = "8805661883:AAG3227BCaOmhC9b1f17WQGPPCpk754VvYA"

# Порог схожести фото (0 = одинаковые, чем больше — тем менее строго)
PHASH_THRESHOLD = 10

# ── Логирование ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger(__name__)

# ── FSM-состояния ─────────────────────────────────────────────────────────────
class PlantStates(StatesGroup):
    waiting_phone   = State()   # ждём контакт
    waiting_photo   = State()   # ждём фото дерева


# ── Инициализация ─────────────────────────────────────────────────────────────
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())
db  = Database("tree_bot.db")


# ── Клавиатуры ────────────────────────────────────────────────────────────────
def phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


# ── Хелперы ───────────────────────────────────────────────────────────────────
def generate_promo() -> str:
    """Генерируем уникальный промокод вида TREE-XXXXXXXX"""
    return "TREE-" + secrets.token_hex(4).upper()


async def download_photo(photo: PhotoSize) -> bytes:
    """Скачиваем фото из Telegram и возвращаем байты"""
    file = await bot.get_file(photo.file_id)
    buf  = io.BytesIO()
    await bot.download_file(file.file_path, destination=buf)
    buf.seek(0)
    return buf.read()


def compute_phash(image_bytes: bytes) -> str:
    """Считаем perceptual hash изображения"""
    img  = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    return str(imagehash.phash(img))


def is_duplicate(new_hash_str: str, existing_hashes: list[str]) -> bool:
    """Проверяем, не является ли фото дубликатом"""
    new_hash = imagehash.hex_to_hash(new_hash_str)
    for h in existing_hashes:
        if new_hash - imagehash.hex_to_hash(h) < PHASH_THRESHOLD:
            return True
    return False


# ── Хендлеры ─────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    """Точка входа — проверяем, верифицирован ли пользователь"""
    user_id = msg.from_user.id

    if db.is_verified(user_id):
        await msg.answer(
            "👋 Ты уже верифицирован!\n\n"
            "🌳 Отправь фото посаженного дерева в Левенцовском районе — "
            "и получи промокод на скидку в кафе.",
            reply_markup=remove_keyboard(),
        )
        await state.set_state(PlantStates.waiting_photo)
    else:
        await msg.answer(
            "🌱 <b>Привет! Это бот «Посади дерево»</b>\n\n"
            "Сажай деревья в <b>Левенцовском районе</b> Ростова-на-Дону "
            "и получай скидки в кафе-партнёрах!\n\n"
            "Для начала нужно подтвердить свой номер телефона 👇",
            parse_mode="HTML",
            reply_markup=phone_keyboard(),
        )
        await state.set_state(PlantStates.waiting_phone)


@dp.message(PlantStates.waiting_phone, F.contact)
async def handle_phone(msg: Message, state: FSMContext):
    """Получаем контакт — регистрируем пользователя"""
    contact: Contact = msg.contact

    # Безопасность: контакт должен принадлежать самому пользователю
    if contact.user_id != msg.from_user.id:
        await msg.answer("⚠️ Пожалуйста, поделись своим собственным номером телефона.")
        return

    phone = contact.phone_number.lstrip("+")

    # Проверяем, не занят ли номер другим аккаунтом
    if db.phone_exists(phone):
        await msg.answer(
            "❌ Этот номер телефона уже зарегистрирован в системе.\n"
            "Каждый номер может быть использован только один раз.",
            reply_markup=remove_keyboard(),
        )
        return

    db.register_user(msg.from_user.id, phone, msg.from_user.username or "")
    log.info(f"Новый пользователь: tg_id={msg.from_user.id}, phone={phone}")

    await msg.answer(
        "✅ <b>Отлично! Номер подтверждён.</b>\n\n"
        "🌳 Теперь посади дерево в <b>Левенцовском районе</b> "
        "и пришли фото — мы выдадим промокод на скидку!\n\n"
        "<i>Правила:</i>\n"
        "• Дерево должно быть реально посажено\n"
        "• Фото сделано на месте посадки\n"
        "• Каждое уникальное фото = один промокод",
        parse_mode="HTML",
        reply_markup=remove_keyboard(),
    )
    await state.set_state(PlantStates.waiting_photo)


@dp.message(PlantStates.waiting_phone)
async def handle_phone_wrong(msg: Message):
    """Пользователь написал текст вместо кнопки"""
    await msg.answer(
        "👇 Пожалуйста, нажми кнопку <b>«Поделиться номером»</b> ниже.",
        parse_mode="HTML",
        reply_markup=phone_keyboard(),
    )


@dp.message(PlantStates.waiting_photo, F.photo)
async def handle_photo(msg: Message, state: FSMContext):
    """Получаем фото дерева — проверяем и выдаём промокод"""
    user_id = msg.from_user.id

    # Берём фото наилучшего качества
    best_photo: PhotoSize = msg.photo[-1]

    await msg.answer("🔍 Проверяем фото...")

    # Скачиваем и считаем хэш
    try:
        image_bytes = await download_photo(best_photo)
        new_hash    = compute_phash(image_bytes)
    except Exception as e:
        log.error(f"Ошибка обработки фото: {e}")
        await msg.answer("⚠️ Не удалось обработать фото. Попробуй ещё раз.")
        return

    # Антиплагиат
    all_hashes = db.get_all_hashes()
    if is_duplicate(new_hash, all_hashes):
        await msg.answer(
            "❌ <b>Это фото уже было загружено ранее.</b>\n\n"
            "Пожалуйста, отправь новое уникальное фото посаженного дерева.",
            parse_mode="HTML",
        )
        log.info(f"Дубликат фото от пользователя {user_id}")
        return

    # Проверяем лимит промокодов на пользователя (опционально)
    promo_count = db.get_user_promo_count(user_id)
    if promo_count >= 5:
        await msg.answer(
            "⚠️ Ты уже получил максимальное количество промокодов (5).\n"
            "Спасибо за участие в озеленении района! 🌿"
        )
        return

    # Генерируем промокод
    promo = generate_promo()
    db.save_submission(user_id, new_hash, promo)

    log.info(f"Промокод выдан: user={user_id}, promo={promo}")

    await msg.answer(
        f"🎉 <b>Спасибо! Дерево засчитано.</b>\n\n"
        f"Твой промокод на скидку:\n\n"
        f"<code>{promo}</code>\n\n"
        f"📍 Покажи этот код на кассе в кафе-партнёрах Левенцовского района.\n"
        f"<i>Промокод действует 30 дней.</i>\n\n"
        f"🌳 Можешь посадить ещё одно дерево и получить новый промокод!",
        parse_mode="HTML",
    )


@dp.message(PlantStates.waiting_photo)
async def handle_photo_wrong(msg: Message):
    """Пользователь прислал не фото"""
    await msg.answer(
        "📸 Пожалуйста, отправь <b>фотографию</b> посаженного дерева.",
        parse_mode="HTML",
    )


@dp.message(Command("my_promos"))
async def cmd_my_promos(msg: Message):
    """Показываем промокоды пользователя"""
    promos = db.get_user_promos(msg.from_user.id)
    if not promos:
        await msg.answer("У тебя пока нет промокодов. Посади дерево! 🌳")
        return

    lines = ["🎟 <b>Твои промокоды:</b>\n"]
    for promo, created_at in promos:
        lines.append(f"• <code>{promo}</code> — {created_at[:10]}")

    await msg.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("stats"))
async def cmd_stats(msg: Message):
    """Статистика (только для теста)"""
    stats = db.get_stats()
    await msg.answer(
        f"📊 <b>Статистика бота:</b>\n\n"
        f"👥 Пользователей: {stats['users']}\n"
        f"📸 Фото принято: {stats['submissions']}\n"
        f"🎟 Промокодов выдано: {stats['promos']}",
        parse_mode="HTML",
    )


# ── Запуск ────────────────────────────────────────────────────────────────────
async def main():
    db.init()
    log.info("Бот запущен 🌳")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
