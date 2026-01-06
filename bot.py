import os
import json
import asyncio
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
import aiohttp

# Загрузка переменных окружения
load_dotenv()

# Исправленный блок (строки 18-20)
BOT_TOKEN = 'BOT_TOKEN'
GROQ_API_KEY = 'API_KEY'
ADMIN_ID = 12345678  # Здесь убираем кавычки и int(), пишем просто число

# Инициализация бота БЕЗ DefaultBotProperties (это важно для business!)
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Хранилище бизнес-подключений
business_connections = {}
BUSINESS_CONNECTIONS_FILE = 'business_connections.json'


# Состояния FSM
class ConfigStates(StatesGroup):
    waiting_for_config = State()
    waiting_for_edit = State()


# ==================== РАБОТА С БИЗНЕС-ПОДКЛЮЧЕНИЯМИ ====================
def load_business_connections():
    """Загрузить бизнес-подключения из файла"""
    if os.path.exists(BUSINESS_CONNECTIONS_FILE):
        try:
            with open(BUSINESS_CONNECTIONS_FILE, 'r', encoding='utf-8') as f:
                connections = json.load(f)
                print(f"✅ Загружено {len(connections)} бизнес-подключений")
                return connections
        except Exception as e:
            print(f"❌ Ошибка загрузки подключений: {e}")
            return {}
    return {}


def save_business_connections(connections):
    """Сохранить бизнес-подключения в файл"""
    try:
        with open(BUSINESS_CONNECTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(connections, f, ensure_ascii=False, indent=2)
        print(f"💾 Сохранено {len(connections)} бизнес-подключений")
    except Exception as e:
        print(f"❌ Ошибка сохранения подключений: {e}")


# ==================== ИНИЦИАЛИЗАЦИЯ БД ====================
def init_db():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ai_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            system_prompt TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            user_id INTEGER,
            message TEXT,
            response TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Добавляем дефолтный конфиг если его нет
    cursor.execute('SELECT COUNT(*) FROM ai_config WHERE is_active = 1')
    if cursor.fetchone()[0] == 0:
        default_prompt = "Ты - профессиональный помощник бизнес-аккаунта. Отвечай вежливо, по делу и профессионально."
        cursor.execute('INSERT INTO ai_config (system_prompt, is_active) VALUES (?, 1)', (default_prompt,))

    conn.commit()
    conn.close()


# Получение активного конфига
def get_active_config():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT system_prompt FROM ai_config WHERE is_active = 1 ORDER BY id DESC LIMIT 1')
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else "Ты - помощник."


# Сохранение конфига
def save_config(system_prompt):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE ai_config SET is_active = 0')
    cursor.execute('INSERT INTO ai_config (system_prompt, is_active) VALUES (?, 1)', (system_prompt,))
    conn.commit()
    conn.close()


# Удаление конфига (возврат к дефолтному)
def delete_config():
    default_prompt = "Ты - профессиональный помощник бизнес-аккаунта. Отвечай вежливо, по делу и профессионально."
    save_config(default_prompt)


# Сохранение истории
def save_history(chat_id, user_id, message, response):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO chat_history (chat_id, user_id, message, response) 
        VALUES (?, ?, ?, ?)
    ''', (chat_id, user_id, message, response))
    conn.commit()
    conn.close()


# Клавиатуры
def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Текущий конфиг")],
            [KeyboardButton(text="⚙️ Изменить конфиг"), KeyboardButton(text="🗑 Удалить конфиг")],
            [KeyboardButton(text="📊 Статистика")]
        ],
        resize_keyboard=True
    )
    return keyboard


# Запрос к Groq API
async def get_ai_response(message_text, system_prompt, chat_id):
    # Получаем последние 5 сообщений из истории для контекста
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT message, response FROM chat_history 
        WHERE chat_id = ? 
        ORDER BY timestamp DESC LIMIT 5
    ''', (chat_id,))
    rows = cursor.fetchall()[::-1] # переворачиваем, чтобы был хронологический порядок
    conn.close()

    # Формируем историю для ИИ
    history_messages = [{"role": "system", "content": system_prompt}]
    for msg, resp in rows:
        history_messages.append({"role": "user", "content": msg})
        history_messages.append({"role": "assistant", "content": resp})
    
    # Добавляем текущее сообщение
    history_messages.append({"role": "user", "content": message_text})

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": "llama-3.3-70b-versatile",
        "messages": history_messages, # Теперь здесь вся история
        "temperature": 0.5, # Чуть меньше случайности для логики
        "max_tokens": 100
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as response:
                if response.status == 200:
                    result = await response.json()
                    return result['choices'][0]['message']['content']
                return "ошибка связи"
    except:
        return "глючу чето"


# ==================== КОМАНДЫ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    await message.answer(
        "<b>🤖 ИИ Бот запущен!</b>\n\n"
        "Я буду отвечать на сообщения в чатах вместо вас.\n\n"
        "📋 Используйте кнопки ниже для управления:",
        reply_markup=get_main_keyboard()
    )


@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    config = get_active_config()
    await message.answer(
        f"<b>👨‍💼 Админ-панель</b>\n\n"
        f"<b>Текущий конфиг:</b>\n<code>{config[:200]}{'...' if len(config) > 200 else ''}</code>\n\n"
        f"<b>ID админа:</b> <code>{ADMIN_ID}</code>",
        reply_markup=get_main_keyboard()
    )


@dp.message(F.text == "📝 Текущий конфиг")
async def show_config(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    config = get_active_config()
    await message.answer(
        f"<b>📝 Текущая конфигурация ИИ:</b>\n\n"
        f"<code>{config}</code>",
        reply_markup=get_main_keyboard()
    )


@dp.message(F.text == "⚙️ Изменить конфиг")
async def change_config(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    await state.set_state(ConfigStates.waiting_for_config)
    await message.answer(
        "<b>⚙️ Изменение конфигурации</b>\n\n"
        "Отправьте новый system prompt текстом или JSON файлом.\n\n"
        "<i>Для JSON используйте формат:</i>\n"
        "<code>{\"system_prompt\": \"Ваш текст\"}</code>",
        reply_markup=ReplyKeyboardRemove()
    )


@dp.message(ConfigStates.waiting_for_config)
async def process_new_config(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    new_config = None

    if message.document:
        if message.document.mime_type == 'application/json':
            file = await bot.get_file(message.document.file_id)
            file_content = await bot.download_file(file.file_path)
            try:
                json_data = json.loads(file_content.read().decode('utf-8'))
                new_config = json_data.get('system_prompt', '')
            except:
                await message.answer("❌ Ошибка чтения JSON файла")
                return
        else:
            await message.answer("❌ Поддерживаются только JSON файлы")
            return
    elif message.text:
        try:
            json_data = json.loads(message.text)
            new_config = json_data.get('system_prompt', message.text)
        except:
            new_config = message.text

    if new_config:
        save_config(new_config)
        await state.clear()
        await message.answer(
            f"✅ <b>Конфигурация обновлена!</b>\n\n"
            f"<b>Новый system prompt:</b>\n<code>{new_config[:200]}{'...' if len(new_config) > 200 else ''}</code>",
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer("❌ Не удалось получить конфигурацию")


@dp.message(F.text == "🗑 Удалить конфиг")
async def remove_config(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    delete_config()
    await message.answer(
        "✅ <b>Конфигурация сброшена!</b>\n\n"
        "Установлен стандартный system prompt.",
        reply_markup=get_main_keyboard()
    )


@dp.message(F.text == "📊 Статистика")
async def show_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()

    cursor.execute('SELECT COUNT(*) FROM chat_history')
    total_messages = cursor.fetchone()[0]

    cursor.execute('SELECT COUNT(DISTINCT chat_id) FROM chat_history')
    total_chats = cursor.fetchone()[0]

    cursor.execute('''
        SELECT COUNT(*) FROM chat_history 
        WHERE DATE(timestamp) = DATE('now')
    ''')
    today_messages = cursor.fetchone()[0]

    conn.close()

    await message.answer(
        f"<b>📊 Статистика бота</b>\n\n"
        f"<b>Всего сообщений:</b> {total_messages}\n"
        f"<b>Чатов обработано:</b> {total_chats}\n"
        f"<b>Сообщений сегодня:</b> {today_messages}",
        reply_markup=get_main_keyboard()
    )


# ==================== BUSINESS HANDLERS ====================
@dp.business_connection()
async def handle_business_connection(business_connection: types.BusinessConnection):
    """Обработка подключения/отключения бизнес-аккаунта"""
    try:
        user_id = business_connection.user.id
        connection_id = business_connection.id
        is_enabled = business_connection.is_enabled

        if is_enabled:
            business_connections[connection_id] = user_id
            save_business_connections(business_connections)
            print(f"✅ Бизнес-подключение установлено: {connection_id} -> User {user_id}")
        else:
            if connection_id in business_connections:
                del business_connections[connection_id]
                save_business_connections(business_connections)
            print(f"❌ Бизнес-подключение отключено: {connection_id}")

        print(f"📊 Всего подключений: {len(business_connections)}")

    except Exception as e:
        print(f"❌ Ошибка сохранения подключения: {e}")


@dp.business_message(F.text)
async def handle_business_text_message(message: types.Message):
    """Обработка текстовых сообщений из бизнес-чатов"""
    try:
        business_connection_id = message.business_connection_id

        if not business_connection_id:
            return

        if business_connection_id not in business_connections:
            business_connections[business_connection_id] = ADMIN_ID
            save_business_connections(business_connections)
            print(f"✅ Автосохранение: {business_connection_id} -> {ADMIN_ID}")

        bot_owner_id = business_connections[business_connection_id]

        if message.from_user and message.from_user.id == bot_owner_id:
            print(f"⏭️ Сообщение от владельца - пропускаем")
            return

        user_message = message.text
        print(f"📨 Сообщение от клиента {message.from_user.id}: {user_message}")

        # ПОКАЗЫВАЕМ ЧТО ПЕЧАТАЕМ
        await bot.send_chat_action(
            chat_id=message.chat.id,
            action="typing",
            business_connection_id=business_connection_id
        )

        # Получаем ответ от ИИ
        system_prompt = get_active_config()
        ai_response = await get_ai_response(user_message, system_prompt, message.chat.id)

        # Отправляем ответ клиенту
        await bot.send_message(
            chat_id=message.chat.id,
            text=ai_response,
            business_connection_id=business_connection_id
        )

        print(f"✅ Ответ отправлен клиенту")

        # Сохраняем в историю
        save_history(message.chat.id, message.from_user.id, user_message, ai_response)

    except Exception as e:
        print(f"❌ Ошибка бизнес-сообщения: {e}")
        import traceback
        traceback.print_exc()


# Обработка обычных сообщений (только для админа)
@dp.message()
async def handle_message(message: types.Message):
    # Игнорируем business сообщения
    if hasattr(message, 'business_connection_id') and message.business_connection_id:
        return

    # Для личных сообщений боту - только админ
    if message.from_user.id != ADMIN_ID:
        return

    # Игнорируем команды и кнопки
    if message.text and (message.text.startswith('/') or message.text in [
        "📝 Текущий конфиг", "⚙️ Изменить конфиг", "🗑 Удалить конфиг", "📊 Статистика"
    ]):
        return

    user_message = message.text or message.caption or ""

    if not user_message:
        return

    # Получаем ответ от ИИ
    system_prompt = get_active_config()
    ai_response = await get_ai_response(user_message, system_prompt, message.chat.id)

    # Отправляем ответ
    await message.answer(ai_response)

    # Сохраняем в историю
    save_history(message.chat.id, message.from_user.id, user_message, ai_response)


# Запуск бота
async def main():
    global business_connections

    # Загружаем подключения
    business_connections = load_business_connections()

    init_db()
    print("🤖 Бот запущен!")
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())