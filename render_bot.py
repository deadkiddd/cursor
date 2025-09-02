#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Файл для деплоя на Render.com
Поддерживает webhook и polling режимы
"""

import os
import logging
import asyncio
import tempfile
import sys
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from flask import Flask, request, jsonify
from datetime import datetime
import signal
import threading
from crypto_checker import auto_issue_card
from supabase import create_client, Client


# Проверка на множественные экземпляры
def check_single_instance():
    """Проверка, что запущен только один экземпляр бота"""
    lock_file = os.path.join(tempfile.gettempdir(), 'telegram_bot.lock')
    
    try:
        # Проверяем, существует ли файл блокировки
        if os.path.exists(lock_file):
            with open(lock_file, 'r') as f:
                pid = f.read().strip()
            
            # Проверяем, работает ли процесс
            try:
                os.kill(int(pid), 0)  # Проверка без отправки сигнала
                print(f"❌ Бот уже запущен (PID: {pid})")
                print("Остановите другой экземпляр бота перед запуском")
                sys.exit(1)
            except OSError:
                # Процесс не существует, удаляем файл блокировки
                os.remove(lock_file)
        
        # Создаем файл блокировки
        with open(lock_file, 'w') as f:
            f.write(str(os.getpid()))
            
        print(f"✅ Файл блокировки создан (PID: {os.getpid()})")
        
    except Exception as e:
        print(f"⚠️ Ошибка проверки экземпляров: {e}")


# Загружаем переменные окружения
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ .env файл загружен успешно")
except ImportError:
    print("⚠️ python-dotenv не установлен, используем системные переменные")
except Exception as e:
    print(f"⚠️ Ошибка загрузки .env: {e}")

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Переменные окружения
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))
# Список администраторов
ADMIN_IDS = [ADMIN_ID]  # Один администратор
OPERATOR_USERNAME = "@myspacehelper"
PORT = int(os.getenv('PORT', 10000))

# Проверка переменных окружения
print("🔍 Проверка переменных окружения:")
print(f"TELEGRAM_BOT_TOKEN: {'✅ Установлен' if TELEGRAM_BOT_TOKEN else '❌ НЕ УСТАНОВЛЕН'}")
print(f"ADMIN_ID: {ADMIN_ID if ADMIN_ID else '❌ НЕ УСТАНОВЛЕН'}")
print(f"PORT: {PORT}")

# Кэш для rate limiting
user_message_times = {}

# Система состояний пользователей
user_states = {}

# Глобальная переменная для крипточекера
crypto_checker = None


# Создаем Flask приложение
app = Flask(__name__)


SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_API_SECRET = os.getenv("SUPABASE_API_SECRET")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_API_SECRET)


# Функции для работы с базой данных
def get_user_wallet(user_id):
    """Получить кошелек пользователя через Supabase"""
    try:
        # Создаем кошелек, если его нет
        existing = supabase.table("wallets").select("balance").eq("user_id", user_id).execute()
        if existing.data:
            return float(existing.data[0]["balance"])
        else:
            supabase.table("wallets").insert({"user_id": user_id, "balance": 0.0}).execute()
            return 0.0
    except Exception as e:
        logger.error(f"Ошибка получения кошелька: {e}")
        return 0.0


def get_or_create_wallet(user_id, username=None, first_name=None):
    """Получить или создать кошелек пользователя через Supabase"""
    try:
        wallet = supabase.table("wallets").select("*").eq("user_id", user_id).execute()
        if wallet.data:
            w = wallet.data[0]
            return {
                "user_id": w["user_id"],
                "username": w.get("username"),
                "first_name": w.get("first_name"),
                "balance": float(w.get("balance", 0.0)),
                "created_at": w.get("created_at"),
                "updated_at": w.get("updated_at")
            }
        else:
            now_iso = datetime.now().isoformat()
            supabase.table("wallets").insert({
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "balance": 0.0,
                "created_at": now_iso,
                "updated_at": now_iso
            }).execute()
            return {
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "balance": 0.0,
                "created_at": now_iso,
                "updated_at": now_iso
            }
    except Exception as e:
        logger.error(f"Ошибка get_or_create_wallet: {e}")
        return None


def update_wallet_balance(user_id, amount, transaction_type, description):
    """Обновить баланс кошелька через Supabase"""
    try:
        # Обновляем баланс
        supabase.table("wallets").update({
            "balance": f"balance + {amount}",
            "updated_at": datetime.now().isoformat()
        }).eq("user_id", user_id).execute()

        # Добавляем транзакцию
        supabase.table("wallet_transactions").insert({
            "user_id": user_id,
            "amount": amount,
            "transaction_type": transaction_type,
            "description": description,
            "created_at": datetime.now().isoformat()
        }).execute()

        return True
    except Exception as e:
        logger.error(f"Ошибка обновления кошелька: {e}")
        return False


def add_money_to_wallet(user_id, amount, description):
    """Добавить деньги в кошелек пользователя через Supabase"""
    try:
        # Создаем кошелек, если его нет
        get_or_create_wallet(user_id)

        # Обновляем баланс
        success = update_wallet_balance(user_id, amount, 'deposit', description)

        if success:
            logger.info(f"Кошелек пользователя {user_id} пополнен на {amount} USD")
            return True
        else:
            logger.error(f"Ошибка пополнения кошелька пользователя {user_id}")
            return False
    except Exception as e:
        logger.error(f"Ошибка add_money_to_wallet: {e}")
        return False


def create_order(user_id, service_type, amount, description):
    """Создать новый заказ через Supabase"""
    try:
        order_resp = supabase.table("orders").insert({
            "user_id": user_id,
            "service_type": service_type,
            "amount": amount,
            "status": "pending",
            "description": description,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }).execute()
        order_id = order_resp.data[0]["id"]

        supabase.table("order_status_history").insert({
            "order_id": order_id,
            "status": "pending",
            "notes": "Заказ создан",
            "created_at": datetime.now().isoformat()
        }).execute()
        return order_id
    except Exception as e:
        logger.error(f"Ошибка создания заказа: {e}")
        return None


# Основные команды бота
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    # Получаем пользователя из сообщения
    if not update.message:
        logger.error("start_command вызвана без сообщения")
        return
    
    user = update.effective_user
    user_id = user.id
    
    # Получаем баланс кошелька
    balance = get_user_wallet(user_id)
    
    welcome_text = f"""
🤖 Добро пожаловать в SPACE PAY!

👤 Пользователь: {user.first_name}
💰 Баланс кошелька: {balance:.2f} USD

Выберите действие:
"""
    
    keyboard = [
        [InlineKeyboardButton("🛒 Каталог услуг", callback_data="catalog")],
        [InlineKeyboardButton("💰 Мой кошелек", callback_data="wallet")],
        [InlineKeyboardButton("📋 Мои заказы", callback_data="orders")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ]
    
    if user_id in ADMIN_IDS:
        keyboard.append([InlineKeyboardButton("🔧 Админ панель", callback_data="admin")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Отправляем новое сообщение
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    help_text = """
❓ Справка по использованию бота:

📋 Основные команды:
/start - Главное меню
/menu - Каталог услуг  
/wallet - Мой кошелек
/orders - Мои заказы
/help - Эта справка
/check_payment - Проверить платеж (админы)

💳 Доступные услуги:
• Подписки на сервисы
• Переводы на карты
• Другие услуги

💰 Оплата:
• Внутренний кошелек
• Банковские карты
• Криптовалюты

📞 Поддержка:
• Оператор: @myspacehelper
"""
    
    await update.message.reply_text(help_text)


async def check_payment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для проверки платежа (только для админов) через Supabase"""
    user_id = update.effective_user.id

    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ У вас нет прав для использования этой команды")
        return

    if not context.args:
        await update.message.reply_text("❌ Укажите ID заказа\nПример: /check_payment 123")
        return

    try:
        order_id = int(context.args[0])

        # Получаем информацию о заказе через Supabase
        order_resp = supabase.table("orders")\
            .select("user_id, service_type, amount, status, description")\
            .eq("id", order_id)\
            .execute()

        if not order_resp.data:
            await update.message.reply_text(f"❌ Заказ {order_id} не найден")
            return

        order = order_resp.data[0]
        user_id_order = order["user_id"]
        service_type = order["service_type"]
        amount = order["amount"]
        status = order["status"]
        description = order["description"]

        if not service_type.startswith('deposit_crypto_'):
            await update.message.reply_text("❌ Этот заказ не является криптопополнением")
            return

        currency = service_type.replace('deposit_crypto_', '')

        # Запускаем проверку платежа
        await update.message.reply_text(f"🔍 Проверяю платеж для заказа {order_id}...")

        # Создаем задачу для фоновой проверки
        asyncio.create_task(check_payment_background(order_id, currency, amount, user_id_order))

        await update.message.reply_text(f"✅ Проверка платежа запущена для заказа {order_id}")

    except ValueError:
        await update.message.reply_text("❌ Неверный формат ID заказа")
    except Exception as e:
        logger.error(f"Ошибка проверки платежа: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def add_money_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для ручного пополнения кошелька (только для админов)"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ У вас нет прав для использования этой команды")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("❌ Укажите ID пользователя и сумму\nПример: /add_money 123456789 100")
        return
    
    try:
        target_user_id = int(context.args[0])
        amount = float(context.args[1])
        
        if amount <= 0:
            await update.message.reply_text("❌ Сумма должна быть больше 0")
            return
        
        # Пополняем кошелек
        success = add_money_to_wallet(target_user_id, amount, f"Ручное пополнение администратором {user_id}")
        
        if success:
            # Уведомляем пользователя
            try:
                from telegram import Bot
                bot = Bot(token=TELEGRAM_BOT_TOKEN)
                await bot.send_message(
                    chat_id=target_user_id,
                    text=f"💰 **Кошелек пополнен!**\n\n💵 Сумма: {amount:.2f} USD\n\n🎉 Ваш баланс обновлен!"
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления пользователя: {e}")
            
            await update.message.reply_text(f"✅ Кошелек пользователя {target_user_id} пополнен на {amount:.2f} USD")
        else:
            await update.message.reply_text("❌ Ошибка пополнения кошелька")
        
    except ValueError:
        await update.message.reply_text("❌ Неверный формат данных")
    except Exception as e:
        logger.error(f"Ошибка пополнения кошелька: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")


# Обработчики callback
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий кнопок"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data
    
    if data == "catalog":
        await show_catalog(query)
    elif data == "wallet":
        await show_wallet(query)
    elif data == "orders":
        await show_orders(query)
    elif data == "help":
        await show_help(query)
    elif data == "admin" and user_id in ADMIN_IDS:
        await show_admin_panel(query)
    elif data.startswith("service_"):
        await handle_service_selection(query, data)
    elif data.startswith("back_"):
        await handle_back_button(query, data)
    elif data.startswith("order_"):
        await handle_order_selection(query, data)
    elif data.startswith("wallet_"):
        await handle_wallet_action(query, data)
    elif data.startswith("admin_"):
        await handle_admin_action(query, data)
    elif data.startswith("deposit_"):
        await handle_deposit_action(query, data)
    elif data.startswith("crypto_deposit_"):
        await handle_crypto_deposit_selection(query, data)


async def show_catalog(query):
    """Показать каталог услуг"""
    catalog_text = """
🛒 Каталог услуг

Выберите категорию:
"""
    
    keyboard = [
        [InlineKeyboardButton("🤖 GPT", callback_data="service_gpt")],
        [InlineKeyboardButton("🐦 Twitter/X", callback_data="service_twitter")],
        [InlineKeyboardButton("🔧 Другие сервисы", callback_data="service_other_services")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_main")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(catalog_text, reply_markup=reply_markup)


async def show_wallet(query):
    """Показать кошелек пользователя"""
    user_id = query.from_user.id
    balance = get_user_wallet(user_id)
    
    wallet_text = f"""
💰 Мой кошелек

💵 Баланс: {balance:.2f} USD

Выберите действие:
"""
    
    keyboard = [
        [InlineKeyboardButton("💳 Пополнить", callback_data="wallet_deposit")],
        [InlineKeyboardButton("📊 История", callback_data="wallet_history")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_main")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(wallet_text, reply_markup=reply_markup)


async def show_orders(query):
    """Показать заказы пользователя через Supabase"""
    user_id = query.from_user.id
    try:
        orders = supabase.table("orders")\
            .select("id, service_type, amount, status, created_at")\
            .eq("user_id", user_id)\
            .order("created_at", desc=True)\
            .limit(10).execute().data

        if orders:
            orders_text = "📋 Ваши заказы:\n\n"
            for o in orders:
                orders_text += f"🔹 Заказ #{o['id']}\n"
                orders_text += f"   Услуга: {o['service_type']}\n"
                orders_text += f"   Сумма: {o['amount']:.2f} USD\n"
                orders_text += f"   Статус: {o['status']}\n"
                orders_text += f"   Дата: {o['created_at']}\n\n"
        else:
            orders_text = "📋 У вас пока нет заказов"

        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_main")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(orders_text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Ошибка show_orders: {e}")
        await query.edit_message_text("❌ Ошибка получения заказов")


async def show_help(query):
    """Показать справку"""
    help_text = """
❓ Справка по использованию бота:

📋 Основные команды:
/start - Главное меню
/menu - Каталог услуг  
/wallet - Мой кошелек
/orders - Мои заказы
/help - Эта справка

💳 Доступные услуги:
• Подписки на сервисы
• Переводы на карты
• Другие услуги

💰 Оплата:
• Внутренний кошелек
• Банковские карты
• Криптовалюты

📞 Поддержка:
• Оператор: @myspacehelper
"""
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(help_text, reply_markup=reply_markup)


async def show_admin_panel(query):
    """Показать админ панель"""
    admin_text = """
🔧 Админ панель

Выберите действие:
"""
    
    keyboard = [
        [InlineKeyboardButton("📋 Все заказы", callback_data="admin_orders")],
        [InlineKeyboardButton("💰 Управление кошельками", callback_data="admin_wallets")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_main")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(admin_text, reply_markup=reply_markup)


async def handle_service_selection(query, data):
    """Обработка выбора услуги"""
    service_type = data.replace("service_", "")
    
    if service_type == "subscriptions":
        await show_subscriptions(query)
    elif service_type == "transfers":
        await show_transfers(query)
    elif service_type == "crypto":
        await show_crypto(query)
    elif service_type == "gpt":
        await show_gpt_services(query)
    elif service_type == "twitter":
        await show_twitter_services(query)
    elif service_type == "other_services":
        await show_other_services(query)


async def show_subscriptions(query):
    """Показать подписки"""
    subscriptions_text = """
🎬 Подписки

Выберите сервис:
"""
    
    keyboard = [
        [InlineKeyboardButton("Netflix", callback_data="order_netflix")],
        [InlineKeyboardButton("Steam", callback_data="order_steam")],
        [InlineKeyboardButton("Discord", callback_data="order_discord")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_catalog")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(subscriptions_text, reply_markup=reply_markup)


async def show_transfers(query):
    """Показать переводы"""
    transfers_text = """
💳 Переводы

Выберите тип перевода:
"""
    
    keyboard = [
        [InlineKeyboardButton("💳 Переводы", callback_data="order_transfer")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_catalog")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(transfers_text, reply_markup=reply_markup)


async def show_crypto(query):
    """Показать криптовалюты"""
    crypto_text = """
₿ Криптовалюты

Выберите валюту:
"""
    
    keyboard = [
        [InlineKeyboardButton("Ethereum (ETH)", callback_data="order_crypto_eth")],
        [InlineKeyboardButton("USDT", callback_data="order_crypto_usdt")],
        [InlineKeyboardButton("Solana (SOL)", callback_data="order_crypto_sol")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_catalog")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(crypto_text, reply_markup=reply_markup)


async def show_other_services(query):
    """Показать другие сервисы"""
    other_services_text = """
🔧 Оплата других сервисов

Выберите услугу:
"""
    
    keyboard = [
        [InlineKeyboardButton("🔧 Другие сервисы", callback_data="order_other_services")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_catalog")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(other_services_text, reply_markup=reply_markup)


async def show_gpt_services(query):
    """Показать услуги GPT"""
    gpt_services_text = """
🤖 GPT сервисы

Выберите услугу:
"""
    
    keyboard = [
        [InlineKeyboardButton("🤖 GPT", callback_data="order_gpt")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_catalog")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(gpt_services_text, reply_markup=reply_markup)


async def show_twitter_services(query):
    """Показать услуги Twitter/X"""
    twitter_services_text = """
🐦 Twitter/X сервисы

Выберите услугу:
"""
    
    keyboard = [
        [InlineKeyboardButton("🐦 Twitter/X", callback_data="order_twitter")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_catalog")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(twitter_services_text, reply_markup=reply_markup)

async def handle_back_button(query, data):
    """Обработка кнопки назад"""
    if data == "back_main":
        # Просто показываем главное меню через редактирование сообщения
        await show_main_menu(query)
    elif data == "back_catalog":
        await show_catalog(query)

async def show_main_menu(query):
    """Показать главное меню"""
    user_id = query.from_user.id
    user = query.from_user
    
    # Получаем баланс кошелька
    balance = get_user_wallet(user_id)
    
    welcome_text = f"""
🤖 Добро пожаловать в SPACE PAY!

👤 Пользователь: {user.first_name}
💰 Баланс кошелька: {balance:.2f} USD

Выберите действие:
"""
    
    keyboard = [
        [InlineKeyboardButton("🛒 Каталог услуг", callback_data="catalog")],
        [InlineKeyboardButton("💰 Мой кошелек", callback_data="wallet")],
        [InlineKeyboardButton("📋 Мои заказы", callback_data="orders")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ]
    
    if user_id in ADMIN_IDS:
        keyboard.append([InlineKeyboardButton("🔧 Админ панель", callback_data="admin")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Редактируем сообщение
    await query.edit_message_text(welcome_text, reply_markup=reply_markup)

async def handle_order_selection(query, data):
    """Обработка выбора заказа"""
    service_type = data.replace("order_", "")
    user_id = query.from_user.id
    
    # Получаем информацию об услуге
    service_info = get_service_info(service_type)
    
    if not service_info:
        await query.edit_message_text("❌ Услуга не найдена", reply_markup=get_back_keyboard("back_catalog"))
        return
    
    # Показываем информацию об услуге и запрашиваем сумму
    service_text = f"""
🛒 {service_info['name']}

📝 Описание: {service_info['description']}
💰 Минимальная сумма: {service_info['min_amount']} USD
💸 Комиссия: {service_info['commission']*100}%

Введите сумму заказа (в USD):
"""
    
    # Сохраняем состояние пользователя
    user_states[user_id] = {
        'state': 'waiting_amount',
        'service_type': service_type,
        'service_info': service_info
    }
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_catalog")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(service_text, reply_markup=reply_markup)

async def handle_wallet_action(query, data):
    """Обработка действий с кошельком"""
    action = data.replace("wallet_", "")
    
    if action == "deposit":
        await show_deposit_options(query)
    elif action == "history":
        await show_wallet_history(query)

async def handle_admin_action(query, data):
    """Обработка админ действий"""
    action = data.replace("admin_", "")
    
    if action == "orders":
        await show_all_orders(query)
    elif action == "wallets":
        await show_wallets_management(query)
    elif action == "stats":
        await show_admin_stats(query)


async def handle_deposit_action(query, data):
    """Обработка действий пополнения"""
    action = data.replace("deposit_", "")
    
    if action == "card":
        await show_card_deposit(query)
    elif action == "crypto":
        await show_crypto_deposit(query)


async def handle_crypto_deposit_selection(query, data):
    """Обработка выбора криптовалюты для пополнения"""
    global crypto_checker
    user_id = query.from_user.id
    
    # Парсим данные: crypto_deposit_btc_100 -> currency=btc, amount=100
    # crypto_deposit_usdc_sol_100 -> currency=usdc_sol, amount=100
    parts = data.split('_')
    if len(parts) >= 4:
        if len(parts) == 5 and parts[3] == 'sol':
            # Для USDC и USDT в сети Solana
            currency = f"{parts[2]}_{parts[3]}"  # usdc_sol, usdt_sol
            amount = float(parts[4])  # сумма
        else:
            # Для обычных криптовалют
            currency = parts[2]  # btc, eth, usdt, sol
            amount = float(parts[3])  # сумма
        
        # Получаем адрес кошелька и рассчитываем количество криптовалюты
        wallet_address = "Адрес не настроен"
        crypto_amount = 0
        
        # Отладочная информация
        logger.info(f"crypto_checker: {crypto_checker}")
        logger.info(f"currency: {currency}")
        logger.info(f"amount: {amount}")
        
        if crypto_checker and hasattr(crypto_checker, 'wallets') and currency in crypto_checker.wallets:
            wallet_address = crypto_checker.wallets[currency]
            logger.info(f"wallet_address: {wallet_address}")
            # Рассчитываем количество криптовалюты по текущему курсу
            try:
                crypto_amount = crypto_checker.calculate_crypto_amount(amount, currency)
                logger.info(f"crypto_amount: {crypto_amount}")
            except Exception as e:
                logger.error(f"Ошибка расчета количества {currency}: {e}")
                # Fallback расчет
                fallback_rates = {
                    'eth': 3000.0,
                    'usdt': 1.0,
                    'sol': 100.0,
                    'usdc_sol': 1.0,
                    'usdt_sol': 1.0,
                }
                rate = fallback_rates.get(currency, 1.0)
                crypto_amount = amount / rate if rate > 0 else 0
                logger.info(f"fallback crypto_amount: {crypto_amount}")
        else:
            logger.error(f"crypto_checker не инициализирован или валюта {currency} не найдена")
            logger.error(f"crypto_checker: {crypto_checker}")
            logger.error(f"hasattr wallets: {hasattr(crypto_checker, 'wallets') if crypto_checker else False}")
            if crypto_checker and hasattr(crypto_checker, 'wallets'):
                logger.error(f"available currencies: {list(crypto_checker.wallets.keys())}")
            
            # Fallback адреса кошельков
            fallback_wallets = {
                'eth': '0x12E450e53E1acD323B95e36636cB4927aC6C17eE',
                'usdt': '0x12E450e53E1acD323B95e36636cB4927aC6C17eE',
                'sol': '6s8bjsP5K3hvdj3bca4FxW8W6CqqSLH26aufVALTJbBq',
                'usdc_sol': '6s8bjsP5K3hvdj3bca4FxW8W6CqqSLH26aufVALTJbBq',
                'usdt_sol': '6s8bjsP5K3hvdj3bca4FxW8W6CqqSLH26aufVALTJbBq'
            }
            
            if currency in fallback_wallets:
                wallet_address = fallback_wallets[currency]
                logger.info(f"Используем fallback адрес: {wallet_address}")
                
                # Fallback расчет криптовалюты
                fallback_rates = {
                    'eth': 3000.0,
                    'usdt': 1.0,
                    'sol': 100.0,
                    'usdc_sol': 1.0,
                    'usdt_sol': 1.0,
                }
                rate = fallback_rates.get(currency, 1.0)
                crypto_amount = amount / rate if rate > 0 else 0
                logger.info(f"fallback crypto_amount: {crypto_amount}")
        
        # Получаем текущий курс
        currency_mapping = {
            'eth': 'ethereum',
            'usdt': 'tether',
            'sol': 'solana',
            'usdc_sol': 'usd-coin',
            'usdt_sol': 'tether'
        }
        
        coin_id = currency_mapping.get(currency, currency)
        
        # Безопасное получение курса
        current_price = 0
        if crypto_checker and hasattr(crypto_checker, 'get_crypto_price'):
            try:
                current_price = crypto_checker.get_crypto_price(coin_id)
            except Exception as e:
                logger.error(f"Ошибка получения курса {coin_id}: {e}")
                # Fallback курсы
                fallback_rates = {
                    'solana': 100.0,
                    'ethereum': 3000.0,
                    'tether': 1.0,
                    'usd-coin': 1.0,
                }
                current_price = fallback_rates.get(coin_id, 1.0)
        else:
            logger.error(f"crypto_checker не инициализирован для получения курса")
            # Fallback курсы
            fallback_rates = {
                'solana': 100.0,
                'ethereum': 3000.0,
                'tether': 1.0,
                'usd-coin': 1.0,
            }
            current_price = fallback_rates.get(coin_id, 1.0)
            logger.info(f"Используем fallback курс для {coin_id}: {current_price}")
        
        crypto_text = f"""
₿ **Пополнение {currency.upper()}**

💰 Сумма к оплате: {amount:.2f} USD

📊 **Калькулятор:**
• Курс {currency.upper()}: ${current_price:.4f}
• Количество для оплаты: {crypto_amount:.6f} {currency.upper()}

📝 **Адрес для оплаты:**
`{wallet_address}`

⚠️ **Важно:**
• Отправьте точную сумму: {crypto_amount:.6f} {currency.upper()}
• Укажите в комментарии: {user_id}
• После оплаты баланс пополнится автоматически
• При проблемах обращайтесь к @myspacehelper

⏰ Ожидайте подтверждения платежа...
        """
        
        # Создаем заказ на пополнение
        order_id = create_order(user_id, f'deposit_crypto_{currency}', amount, f"Пополнение {currency.upper()} {amount} USD")
        
        # Запускаем проверку платежа в фоне
        asyncio.create_task(check_payment_background(order_id, currency, crypto_amount, user_id))
        
        keyboard = [
            [InlineKeyboardButton("💰 Мой кошелек", callback_data="wallet")],
            [InlineKeyboardButton("📋 Мои заказы", callback_data="orders")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(crypto_text, reply_markup=reply_markup, parse_mode='Markdown')
        del user_states[user_id]
    else:
        await query.edit_message_text("❌ Ошибка обработки выбора криптовалюты")
        del user_states[user_id]


async def check_payment_background(order_id, currency, expected_amount, user_id):
    """Фоновая проверка платежа"""
    global crypto_checker
    
    try:
        logger.info(f"Начинаем проверку платежа для заказа {order_id}, валюта: {currency}")
        
        # Проверяем платеж несколько раз с интервалом
        for attempt in range(10):  # 10 попыток
            await asyncio.sleep(6)  # Ждем 6 секунд между проверками
            
            if crypto_checker and hasattr(crypto_checker, 'check_payment'):
                try:
                    result = crypto_checker.check_payment(currency, expected_amount, order_id)
                    logger.info(f"Попытка {attempt + 1}: результат проверки: {result}")
                    
                    if result.get('success'):
                        # Платеж найден!
                        amount = result.get('amount', expected_amount)
                        
                        # Пополняем кошелек пользователя
                        success = add_money_to_wallet(user_id, amount, f"Пополнение {currency.upper()}")
                        
                        if success:
                            # Обновляем статус заказа
                            _update_order_status_in_supabase(order_id, 'completed', ADMIN_ID, f"Платеж подтвержден: {result.get('tx_hash', 'N/A')}")
                            
                            # Уведомляем пользователя
                            try:
                                from telegram import Bot
                                bot = Bot(token=TELEGRAM_BOT_TOKEN)
                                await bot.send_message(
                                    chat_id=user_id,
                                    text=f"✅ **Платеж подтвержден!**\n\n💰 Сумма: {amount:.6f} {currency.upper()}\n💳 Зачислено на кошелек: {amount:.2f} USD\n\n🎉 Ваш кошелек пополнен!"
                                )
                            except Exception as e:
                                logger.error(f"Ошибка уведомления пользователя: {e}")
                            
                            logger.info(f"Платеж успешно обработан для заказа {order_id}")
                            return
                        else:
                            logger.error(f"Ошибка пополнения кошелька для заказа {order_id}")
                    else:
                        logger.info(f"Платеж не найден, попытка {attempt + 1}")
                        
                except Exception as e:
                    logger.error(f"Ошибка проверки платежа: {e}")
            else:
                logger.error("crypto_checker недоступен для проверки платежа")
                break
        
        # Если платеж не найден после всех попыток
        logger.warning(f"Платеж не найден для заказа {order_id} после 10 попыток")
        
        # Уведомляем администратора
        try:
            from telegram import Bot
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=f"⚠️ **Платеж не найден**\n\nЗаказ: {order_id}\nВалюта: {currency}\nОжидаемая сумма: {expected_amount}\nПользователь: {user_id}\n\nПроверьте вручную!"
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления администратора: {e}")
            
    except Exception as e:
        logger.error(f"Ошибка фоновой проверки платежа: {e}")

def get_service_info(service_type):
    """Получить информацию об услуге"""
    services = {
        'netflix': {
            'name': 'Netflix Premium',
            'description': 'Подписка на Netflix Premium с доступом к 4K контенту',
            'min_amount': 10,
            'commission': 0.08
        },
        'steam': {
            'name': 'Steam Gift Cards',
            'description': 'Подарочные карты Steam для покупки игр',
            'min_amount': 10,
            'commission': 0.08
        },
        'discord': {
            'name': 'Discord Nitro',
            'description': 'Подписка Discord Nitro с эксклюзивными возможностями',
            'min_amount': 10,
            'commission': 0.08
        },
        'spotify': {
            'name': 'Spotify Premium',
            'description': 'Подписка Spotify Premium без рекламы',
            'min_amount': 10,
            'commission': 0.08
        },
        'youtube': {
            'name': 'YouTube Premium',
            'description': 'YouTube Premium с фоновым воспроизведением',
            'min_amount': 10,
            'commission': 0.08
        },
        'transfer_eu': {
            'name': 'Перевод на европейские карты',
            'description': 'Перевод средств на карты европейских банков',
            'min_amount': 50,
            'commission': 0.08
        },
        'transfer_us': {
            'name': 'Перевод на американские карты',
            'description': 'Перевод средств на карты американских банков',
            'min_amount': 50,
            'commission': 0.08
        },
        'crypto_eth': {
            'name': 'Ethereum (ETH)',
            'description': 'Покупка Ethereum через криптоплатеж',
            'min_amount': 5,
            'commission': 0.08
        },
        'crypto_usdt': {
            'name': 'USDT (Ethereum)',
            'description': 'Покупка USDT через криптоплатеж',
            'min_amount': 5,
            'commission': 0.08
        },

        'crypto_sol': {
            'name': 'Solana (SOL)',
            'description': 'Покупка/продажа Solana',
            'min_amount': 5,
            'commission': 0.08
        },
        'gpt': {
            'name': 'GPT',
            'description': 'Подписки на GPT сервисы (ChatGPT Plus, Pro, API)',
            'min_amount': 20,
            'commission': 0.08
        },
        'twitter': {
            'name': 'Twitter/X',
            'description': 'Подписки на Twitter/X (Blue, Premium, Verified)',
            'min_amount': 8,
            'commission': 0.08
        },
        'other_services': {
            'name': 'Оплата других сервисов',
            'description': 'Оплата любых других сервисов и услуг',
            'min_amount': 10,
            'commission': 0.08
        }
    }
    
    return services.get(service_type)

def get_back_keyboard(back_action):
    """Получить клавиатуру с кнопкой назад"""
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=back_action)]]
    return InlineKeyboardMarkup(keyboard)

async def show_deposit_options(query):
    """Показать варианты пополнения"""
    deposit_text = """
💳 Пополнение кошелька

Выберите способ пополнения:
"""
    
    keyboard = [
        [InlineKeyboardButton("💳 Банковская карта", callback_data="deposit_card")],
        [InlineKeyboardButton("₿ Криптовалюта", callback_data="deposit_crypto")],
        [InlineKeyboardButton("🔙 Назад", callback_data="wallet")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(deposit_text, reply_markup=reply_markup)


async def show_card_deposit(query):
    """Показать пополнение картой"""
    user_id = query.from_user.id
    
    card_text = f"""
💳 Пополнение банковской картой

💰 Минимальная сумма: 10 USD

📝 Для пополнения:
1. Введите сумму пополнения
2. Получите реквизиты для оплаты
3. После оплаты баланс пополнится автоматически

Введите сумму пополнения (в USD):
"""
    
    # Сохраняем состояние пользователя
    user_states[user_id] = {
        'state': 'waiting_deposit_amount',
        'deposit_type': 'card'
    }
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="wallet_deposit")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(card_text, reply_markup=reply_markup)

async def show_crypto_deposit(query):
    """Показать пополнение криптовалютой"""
    user_id = query.from_user.id
    
    crypto_text = f"""
₿ Пополнение криптовалютой

💰 Минимальная сумма: 10 USD

📝 Доступные валюты:
• Ethereum (ETH)
• USDT (ERC-20)
• Solana (SOL)
• USDC (Solana Network)
• USDT (Solana Network)

📝 Для пополнения:
1. Введите сумму пополнения
2. Выберите криптовалюту
3. Получите точное количество для оплаты
4. После оплаты баланс пополнится автоматически

Введите сумму пополнения (в USD):
"""
    
    # Сохраняем состояние пользователя
    user_states[user_id] = {
        'state': 'waiting_deposit_amount',
        'deposit_type': 'crypto'
    }
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="wallet_deposit")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(crypto_text, reply_markup=reply_markup)


async def show_wallet_history(query):
    """Показать историю кошелька через Supabase"""
    user_id = query.from_user.id
    try:
        transactions = supabase.table("wallet_transactions")\
            .select("*")\
            .eq("user_id", user_id)\
            .order("created_at", desc=True)\
            .limit(10).execute().data

        if transactions:
            history_text = "📊 История транзакций:\n\n"
            for t in transactions:
                emoji = "➕" if t["amount"] > 0 else "➖"
                history_text += f"{emoji} {t['amount']:.2f} USD\n"
                history_text += f"   Тип: {t['transaction_type']}\n"
                history_text += f"   Описание: {t['description']}\n"
                history_text += f"   Дата: {t['created_at']}\n\n"
        else:
            history_text = "📊 История транзакций пуста"

        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="wallet")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(history_text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Ошибка show_wallet_history: {e}")
        await query.edit_message_text("❌ Ошибка получения истории")


async def show_all_orders(query):
    """Показать все заказы (админ) через Supabase"""
    try:
        # Получаем заказы с балансом пользователя через Supabase
        orders_resp = supabase.table("orders")\
            .select("id, user_id, service_type, amount, status, created_at, wallets(balance)")\
            .order("created_at", desc=True)\
            .limit(20)\
            .execute()

        orders = orders_resp.data

        if orders:
            orders_text = "📋 Все заказы:\n\n"
            for order in orders:
                order_id = order["id"]
                user_id = order["user_id"]
                service_type = order["service_type"]
                amount = order["amount"]
                status = order["status"]
                created_at = order["created_at"]
                # Баланс из связанной таблицы wallets
                balance = order.get("wallets", [{}])[0].get("balance", 0)

                orders_text += f"🔹 Заказ #{order_id}\n"
                orders_text += f"   Пользователь: {user_id}\n"
                orders_text += f"   Услуга: {service_type}\n"
                orders_text += f"   Сумма: {amount:.2f} USD\n"
                orders_text += f"   Статус: {status}\n"
                orders_text += f"   Баланс: {balance:.2f} USD\n"
                orders_text += f"   Дата: {created_at}\n\n"
        else:
            orders_text = "📋 Заказов пока нет"

        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(orders_text, reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Ошибка получения заказов: {e}")
        await query.edit_message_text("❌ Ошибка получения заказов")


async def show_wallets_management(query):
    """Показать управление кошельками (админ) через Supabase"""
    try:
        # Получаем кошельки, сортируя по балансу по убыванию
        wallets_resp = supabase.table("wallets")\
            .select("user_id, balance, created_at")\
            .order("balance", desc=True)\
            .limit(10)\
            .execute()

        wallets = wallets_resp.data

        if wallets:
            wallets_text = "💰 Управление кошельками:\n\n"
            for wallet in wallets:
                user_id = wallet["user_id"]
                balance = wallet["balance"]
                created_at = wallet["created_at"]

                wallets_text += f"👤 Пользователь: {user_id}\n"
                wallets_text += f"   Баланс: {balance:.2f} USD\n"
                wallets_text += f"   Создан: {created_at}\n\n"
        else:
            wallets_text = "💰 Кошельков пока нет"

        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(wallets_text, reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Ошибка получения кошельков: {e}")
        await query.edit_message_text("❌ Ошибка получения кошельков")


async def show_admin_stats(query):
    """Показать статистику (админ) через Supabase"""
    try:
        # Количество пользователей
        users_resp = supabase.table("wallets").select("user_id", count="exact").execute()
        users_count = users_resp.count or 0

        # Количество заказов
        orders_resp = supabase.table("orders").select("id", count="exact").execute()
        orders_count = orders_resp.count or 0

        # Общая сумма заказов
        total_amount_resp = supabase.table("orders").select("amount").execute()
        total_amount = sum(order["amount"] for order in total_amount_resp.data) if total_amount_resp.data else 0

        # Общий баланс всех кошельков
        total_balance_resp = supabase.table("wallets").select("balance").execute()
        total_balance = sum(wallet["balance"] for wallet in total_balance_resp.data) if total_balance_resp.data else 0

        stats_text = f"""
📊 Статистика бота:

👥 Пользователей: {users_count}
📋 Заказов: {orders_count}
💰 Общая сумма заказов: {total_amount:.2f} USD
💳 Общий баланс кошельков: {total_balance:.2f} USD
"""

        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(stats_text, reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        await query.edit_message_text("❌ Ошибка получения статистики")


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    user_id = update.effective_user.id
    text = update.message.text
    
    # Проверяем состояние пользователя
    if user_id in user_states:
        state = user_states[user_id]
        
        if state['state'] == 'waiting_amount':
            await handle_amount_input(update, context, text, state)
        elif state['state'] == 'waiting_deposit_amount':
            await handle_deposit_amount_input(update, context, text, state)
        else:
            # Неизвестное состояние, сбрасываем
            del user_states[user_id]
            await update.message.reply_text("❌ Неизвестное состояние. Используйте /start для перезапуска.")
    else:
        # Пользователь не в состоянии ожидания
        await update.message.reply_text("❌ Используйте кнопки меню для навигации или /start для перезапуска.")


async def handle_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, state: dict):
    """Обработка ввода суммы заказа"""
    user_id = update.effective_user.id
    
    try:
        # Парсим сумму
        amount = float(text.replace(',', '.'))
        
        # Проверяем минимальную сумму
        service_info = state['service_info']
        if amount < service_info['min_amount']:
            await update.message.reply_text(
                f"❌ Минимальная сумма для {service_info['name']}: {service_info['min_amount']} USD\n"
                f"Попробуйте еще раз:"
            )
            return
        
        # Проверяем, является ли это криптоплатежом
        is_crypto_payment = state['service_type'].startswith('crypto_')
        
        if is_crypto_payment:
            # Для криптоплатежей показываем адрес для оплаты
            currency = state['service_type'].replace('crypto_', '')
            global crypto_checker
            
            if crypto_checker:
                wallet_address = crypto_checker.wallets.get(currency, 'Адрес не настроен')
                
                crypto_text = f"""
💳 **Криптоплатеж {currency.upper()}**

💰 Сумма: {amount:.2f} USD
🛒 Услуга: {service_info['name']}
💸 Комиссия: {amount * service_info['commission']:.2f} USD
💳 Итого: {amount + (amount * service_info['commission']):.2f} USD

📝 **Адрес для оплаты:**
`{wallet_address}`

⚠️ **Важно:**
• Отправьте точную сумму в {currency.upper()}
• Платеж будет проверен автоматически
• После подтверждения карта будет выдана

⏰ Ожидайте подтверждения платежа...
"""
                
                # Создаем заказ без списания средств
                order_id = create_order(user_id, state['service_type'], amount, f"Криптоплатеж {currency.upper()}")
                
                if order_id:
                    # Проверяем платеж сразу после создания заказа
                    try:
                        result = crypto_checker.check_payment(currency, amount, order_id)
                        if result['success']:
                            # Платеж найден, обрабатываем
                            if crypto_checker.process_payment(result):
                                # Обновляем статус заказа
                                _update_order_status_in_supabase(order_id, 'completed', ADMIN_ID, f'Криптоплатеж подтвержден: {result["amount"]} {result["currency"]}')
                                
                                # Выдаем карту
                                card_info = auto_issue_card(state['service_type'], amount, user_id)
                                
                                success_text = f"""
✅ **Платеж подтвержден!**

💰 Сумма: {result['amount']} {result['currency'].upper()}
🆔 Заказ: #{order_id}
📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}

🎫 **Ваша карта:**
Номер: {card_info['card_number']}
Срок: {card_info['expiry']}
CVV: {card_info['cvv']}

Спасибо за покупку! 🎉
                                """
                                
                                keyboard = [
                                    [InlineKeyboardButton("🛒 Новый заказ", callback_data="catalog")],
                                    [InlineKeyboardButton("📋 Мои заказы", callback_data="orders")]
                                ]
                                reply_markup = InlineKeyboardMarkup(keyboard)
                                
                                await update.message.reply_text(success_text, reply_markup=reply_markup, parse_mode='Markdown')
                                del user_states[user_id]
                                return
                    except Exception as e:
                        logger.error(f"Ошибка проверки криптоплатежа: {e}")
                    
                    # Если платеж не найден, показываем адрес для оплаты
                    keyboard = [
                        [InlineKeyboardButton("🛒 Новый заказ", callback_data="catalog")],
                        [InlineKeyboardButton("📋 Мои заказы", callback_data="orders")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await update.message.reply_text(crypto_text, reply_markup=reply_markup, parse_mode='Markdown')
                    del user_states[user_id]
                else:
                    await update.message.reply_text("❌ Ошибка создания заказа. Попробуйте еще раз.")
            else:
                await update.message.reply_text("❌ Криптоплатежи временно недоступны. Попробуйте позже.")
                del user_states[user_id]
            return
        
        # Для обычных платежей проверяем баланс
        user_balance = get_user_wallet(user_id)
        total_cost = amount + (amount * service_info['commission'])
        
        if user_balance < total_cost:
            await update.message.reply_text(
                f"❌ Недостаточно средств на кошельке!\n"
                f"💰 Необходимо: {total_cost:.2f} USD\n"
                f"💳 Доступно: {user_balance:.2f} USD\n\n"
                f"Пополните кошелек через /wallet"
            )
            del user_states[user_id]
            return
        
        # Создаем заказ и списываем средства
        order_id = create_order(user_id, state['service_type'], amount, f"Заказ {service_info['name']}")
        
        if order_id:
            # Списываем средства
            success = update_wallet_balance(user_id, -total_cost, 'purchase', f'Покупка {service_info["name"]}')
            
            if success:
                # Выдаем карту
                card_info = auto_issue_card(state['service_type'], amount, user_id)
                
                success_text = f"""
✅ **Заказ выполнен!**

💰 Сумма: {amount:.2f} USD
💸 Комиссия: {amount * service_info['commission']:.2f} USD
💳 Итого: {total_cost:.2f} USD
🆔 Заказ: #{order_id}

🎫 **Ваша карта:**
Номер: {card_info['card_number']}
Срок: {card_info['expiry']}
CVV: {card_info['cvv']}

Спасибо за покупку! 🎉
                """
                
                keyboard = [
                    [InlineKeyboardButton("🛒 Новый заказ", callback_data="catalog")],
                    [InlineKeyboardButton("📋 Мои заказы", callback_data="orders")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(success_text, reply_markup=reply_markup, parse_mode='Markdown')
                del user_states[user_id]
            else:
                await update.message.reply_text("❌ Ошибка списания средств. Попробуйте еще раз.")
                del user_states[user_id]
        else:
            await update.message.reply_text("❌ Ошибка создания заказа. Попробуйте еще раз.")
            del user_states[user_id]
            
    except ValueError:
        await update.message.reply_text("❌ Неверный формат суммы. Введите число (например: 50.5)")
    except Exception as e:
        logger.error(f"Ошибка обработки суммы заказа: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте еще раз.")
        del user_states[user_id]


async def handle_deposit_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, state: dict):
    """Обработка ввода суммы пополнения"""
    user_id = update.effective_user.id
    deposit_type = state['deposit_type']
    
    try:
        # Парсим сумму
        amount = float(text.replace(',', '.'))
        
        # Проверяем минимальную сумму
        min_amount = 10
        if amount < min_amount:
            await update.message.reply_text(
                f"❌ Минимальная сумма пополнения: {min_amount} USD\n"
                f"Попробуйте еще раз:"
            )
            return
        
        if deposit_type == 'card':
            # Пополнение банковской картой
            deposit_text = f"""
💳 **Пополнение банковской картой**

💰 Сумма к оплате: {amount:.2f} USD

📝 **Реквизиты для оплаты:**
Банк: Tinkoff Bank
Номер карты: 2200 7004 XXXX XXXX
Получатель: Иван Иванов

⚠️ **Важно:**
• Укажите в комментарии: {user_id}
• После оплаты баланс пополнится в течение 10 минут
• При проблемах обращайтесь к @myspacehelper

⏰ Ожидайте подтверждения платежа...
            """
            
            # Создаем заказ на пополнение
            order_id = create_order(user_id, 'deposit_card', amount, f"Пополнение картой {amount} USD")
            
            keyboard = [
                [InlineKeyboardButton("💰 Мой кошелек", callback_data="wallet")],
                [InlineKeyboardButton("📋 Мои заказы", callback_data="orders")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(deposit_text, reply_markup=reply_markup, parse_mode='Markdown')
            del user_states[user_id]
            
        elif deposit_type == 'crypto':
            # Пополнение криптовалютой
            # Показываем выбор криптовалюты
            crypto_text = f"""
₿ **Пополнение криптовалютой**

💰 Сумма к оплате: {amount:.2f} USD

Выберите криптовалюту:
            """
            
            keyboard = [
                [InlineKeyboardButton("Ethereum (ETH)", callback_data=f"crypto_deposit_eth_{amount}")],
                [InlineKeyboardButton("USDT (ERC-20)", callback_data=f"crypto_deposit_usdt_{amount}")],
                [InlineKeyboardButton("Solana (SOL)", callback_data=f"crypto_deposit_sol_{amount}")],
                [InlineKeyboardButton("USDC (Solana)", callback_data=f"crypto_deposit_usdc_sol_{amount}")],
                [InlineKeyboardButton("USDT (Solana)", callback_data=f"crypto_deposit_usdt_sol_{amount}")],
                [InlineKeyboardButton("🔙 Назад", callback_data="wallet_deposit")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Сохраняем сумму в состоянии
            user_states[user_id] = {
                'state': 'waiting_crypto_selection',
                'deposit_amount': amount
            }
            
            await update.message.reply_text(crypto_text, reply_markup=reply_markup, parse_mode='Markdown')
            
    except ValueError:
        await update.message.reply_text("❌ Неверный формат суммы. Введите число (например: 50.5)")
    except Exception as e:
        logger.error(f"Ошибка обработки суммы пополнения: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте еще раз.")
        del user_states[user_id]


# Internal helper functions for Supabase operations (replacing external API endpoints)
def _get_orders_from_supabase():
    """Получить все заказы из Supabase (внутренняя функция)"""
    try:
        # Получаем заказы с балансами пользователей
        orders_resp = supabase.table("orders").select("id,user_id,service_type,amount,status,created_at").order("created_at", desc=True).execute()
        orders_data = orders_resp.data or []

        # Получаем балансы всех пользователей, чтобы не делать отдельный запрос на каждого
        user_ids = [order["user_id"] for order in orders_data]
        wallets_resp = supabase.table("wallets").select("user_id,balance").in_("user_id", user_ids).execute()
        wallets_data = {w["user_id"]: w["balance"] for w in wallets_resp.data} if wallets_resp.data else {}

        orders_list = []
        for order in orders_data:
            orders_list.append({
                'id': order["id"],
                'user_id': order["user_id"],
                'service_type': order["service_type"],
                'amount': order["amount"],
                'status': order["status"],
                'created_at': order["created_at"],
                'user_balance': wallets_data.get(order["user_id"], 0)
            })

        return orders_list

    except Exception as e:
        logger.error(f"Ошибка получения заказов: {e}")
        return []

def _get_order_from_supabase(order_id):
    """Получить конкретный заказ из Supabase (внутренняя функция)"""
    try:
        # Получаем заказ
        order_resp = supabase.table("orders").select("*").eq("id", order_id).single().execute()
        order = order_resp.data

        if not order:
            return None

        # Получаем баланс пользователя
        wallet_resp = supabase.table("wallets").select("balance").eq("user_id", order["user_id"]).single().execute()
        balance = wallet_resp.data["balance"] if wallet_resp.data else 0

        return {
            'id': order["id"],
            'user_id': order["user_id"],
            'service_type': order["service_type"],
            'amount': order["amount"],
            'status': order["status"],
            'description': order.get("description"),
            'created_at': order["created_at"],
            'updated_at': order["updated_at"],
            'user_balance': balance
        }

    except Exception as e:
        logger.error(f"Ошибка получения заказа {order_id}: {e}")
        return None

def _update_order_status_in_supabase(order_id, new_status, admin_id, notes=''):
    """Обновить статус заказа в Supabase (внутренняя функция)"""
    try:
        # Обновляем статус заказа
        supabase.table("orders").update({
            "status": new_status,
            "updated_at": datetime.now().isoformat()
        }).eq("id", order_id).execute()

        # Добавляем в историю
        supabase.table("order_status_history").insert({
            "order_id": order_id,
            "status": new_status,
            "admin_id": admin_id,
            "notes": notes,
            "created_at": datetime.now().isoformat()
        }).execute()

        logger.info(f"Статус заказа {order_id} обновлен на '{new_status}' администратором {admin_id}")
        return True

    except Exception as e:
        logger.error(f"Ошибка обновления статуса заказа {order_id}: {e}")
        return False

def _get_wallet_info_from_supabase(user_id):
    """Получить информацию о кошельке из Supabase (внутренняя функция)"""
    try:
        # Получаем кошелек пользователя
        wallet_resp = supabase.table("wallets").select("balance, created_at").eq("user_id", user_id).single().execute()
        wallet = wallet_resp.data

        if not wallet:
            return None

        # Получаем количество транзакций
        transactions_resp = supabase.table("wallet_transactions").select("id", count="exact").eq("user_id", user_id).execute()
        transactions_count = transactions_resp.count or 0

        return {
            'user_id': user_id,
            'balance': wallet['balance'],
            'created_at': wallet['created_at'],
            'transactions_count': transactions_count
        }

    except Exception as e:
        logger.error(f"Ошибка получения информации о кошельке пользователя {user_id}: {e}")
        return None

def _deposit_wallet_in_supabase(user_id, amount, admin_id):
    """Пополнить кошелек в Supabase (внутренняя функция)"""
    try:
        if not amount or amount <= 0:
            logger.error(f"Некорректная сумма для пополнения: {amount}")
            return False

        # Получаем текущий баланс
        wallet_resp = supabase.table("wallets").select("balance").eq("user_id", user_id).single().execute()
        wallet = wallet_resp.data

        if not wallet:
            logger.error(f"Кошелек пользователя {user_id} не найден")
            return False

        new_balance = wallet['balance'] + amount

        # Обновляем баланс кошелька
        supabase.table("wallets").update({"balance": new_balance, "updated_at": datetime.now().isoformat()}).eq("user_id", user_id).execute()

        # Добавляем транзакцию
        supabase.table("wallet_transactions").insert({
            "user_id": user_id,
            "amount": amount,
            "transaction_type": "deposit",
            "description": f"Пополнение администратором {admin_id}",
            "created_at": datetime.now().isoformat()
        }).execute()

        logger.info(f"Кошелек пользователя {user_id} пополнен на {amount} USD администратором {admin_id}")
        return True

    except Exception as e:
        logger.error(f"Ошибка пополнения кошелька пользователя {user_id}: {e}")
        return False

def _withdraw_wallet_in_supabase(user_id, amount, admin_id):
    """Вывести средства из кошелька в Supabase (внутренняя функция)"""
    try:
        if not amount or amount <= 0:
            logger.error(f"Некорректная сумма для вывода: {amount}")
            return False

        # Получаем текущий баланс
        wallet_resp = supabase.table("wallets").select("balance").eq("user_id", user_id).single().execute()
        wallet = wallet_resp.data

        if not wallet:
            logger.error(f"Кошелек пользователя {user_id} не найден")
            return False

        current_balance = wallet['balance']
        if current_balance < amount:
            logger.error(f"Недостаточно средств для вывода: {current_balance} < {amount}")
            return False

        new_balance = current_balance - amount

        # Обновляем баланс кошелька
        supabase.table("wallets").update({"balance": new_balance, "updated_at": datetime.now().isoformat()}).eq("user_id", user_id).execute()

        # Добавляем транзакцию
        supabase.table("wallet_transactions").insert({
            "user_id": user_id,
            "amount": -amount,
            "transaction_type": "withdraw",
            "description": f"Вывод администратором {admin_id}",
            "created_at": datetime.now().isoformat()
        }).execute()

        logger.info(f"Из кошелька пользователя {user_id} выведено {amount} USD администратором {admin_id}")
        return True

    except Exception as e:
        logger.error(f"Ошибка вывода средств из кошелька пользователя {user_id}: {e}")
        return False


# Flask маршруты
@app.route('/')
def home():
    """Главная страница"""
    return jsonify({
        'status': 'online',
        'bot': 'Telegram Financial Bot',
        'version': '1.0.0',
        'timestamp': datetime.now().isoformat()
    })


@app.route('/health')
def health():
    """Health check для Render"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat()
    })


@app.route('/stats')
def stats():
    try:
        users_count = supabase.table("wallets").select("user_id").execute().count
        orders_count = supabase.table("orders").select("id").execute().count
        total_amount_resp = supabase.table("orders").select("amount").execute()
        total_amount = sum([o["amount"] for o in total_amount_resp.data])

        return jsonify({
            "users_count": users_count,
            "orders_count": orders_count,
            "total_amount": total_amount,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# API для администраторов через Supabase - УДАЛЕНО
# Функциональность перенесена в внутренние функции _get_orders_from_supabase()


# GET /admin/order/{id} - УДАЛЕНО
# Функциональность перенесена в внутренние функции _get_order_from_supabase()


# POST /admin/order/{id}/status - УДАЛЕНО
# Функциональность перенесена в внутренние функции _update_order_status_in_supabase()


# GET /admin/wallet/{user_id} - УДАЛЕНО
# Функциональность перенесена в внутренние функции _get_wallet_info_from_supabase()


# POST /admin/wallet/{user_id}/deposit - УДАЛЕНО
# Функциональность перенесена в внутренние функции _deposit_wallet_in_supabase()


# POST /admin/wallet/{user_id}/withdraw - УДАЛЕНО
# Функциональность перенесена в внутренние функции _withdraw_wallet_in_supabase()


# Функция для проверки криптоплатежей
async def check_crypto_payments():
    """Проверка криптоплатежей через Supabase в фоновом режиме"""
    global crypto_checker

    if not crypto_checker:
        logger.warning("Крипточекер не инициализирован")
        return

    try:
        # Получаем все pending заказы с криптоплатежами через Supabase
        pending_resp = supabase.table("orders")\
            .select("id, user_id, service_type, amount")\
            .eq("status", "pending")\
            .like("service_type", "crypto_%")\
            .execute()

        pending_orders = pending_resp.data or []

        for order in pending_orders:
            order_id = order["id"]
            user_id = order["user_id"]
            service_type = order["service_type"]
            amount = order["amount"]

            currency = service_type.replace("crypto_", "")

            # Проверяем платеж
            result = crypto_checker.check_payment(currency, amount, order_id)

            if result["success"] and crypto_checker.process_payment(result):
                # Обновляем статус заказа через Supabase
                supabase.table("orders").update({
                    "status": "completed",
                    "updated_at": datetime.now().isoformat()
                }).eq("id", order_id).execute()

                # Добавляем запись в историю статусов
                supabase.table("order_status_history").insert({
                    "order_id": order_id,
                    "status": "completed",
                    "admin_id": ADMIN_ID,
                    "notes": f'Криптоплатеж подтвержден: {result["amount"]} {result["currency"]}',
                    "created_at": datetime.now().isoformat()
                }).execute()

                # Выдаем карту
                card_info = auto_issue_card(service_type, amount, user_id)

                # Уведомляем пользователя
                try:
                    from telegram.ext import Application
                    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

                    success_text = f"""
✅ **Платеж подтвержден!**

💰 Сумма: {result['amount']} {result['currency'].upper()}
🆔 Заказ: #{order_id}
📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}

🎫 **Ваша карта:**
Номер: {card_info['card_number']}
Срок: {card_info['expiry']}
CVV: {card_info['cvv']}

Спасибо за покупку! 🎉
                    """

                    await app.bot.send_message(
                        chat_id=user_id,
                        text=success_text,
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logger.error(f"Ошибка уведомления пользователя {user_id}: {e}")

                logger.info(f"Заказ {order_id} обработан успешно")

    except Exception as e:
        logger.error(f"Ошибка проверки криптоплатежей: {e}")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Ошибка при обработке обновления: {context.error}")
    
    # Получаем информацию о пользователе
    user_info = "Неизвестный пользователь"
    if update:
        if update.effective_user:
            user_info = f"{update.effective_user.first_name} (ID: {update.effective_user.id})"
        elif update.callback_query and update.callback_query.from_user:
            user_info = f"{update.callback_query.from_user.first_name} (ID: {update.callback_query.from_user.id})"
    
    # Уведомление администратора об ошибке
    if ADMIN_ID:
        try:
            error_text = f"❌ **Ошибка в боте:**\n\n"
            error_text += f"🔍 Детали: {context.error}\n"
            error_text += f"👤 Пользователь: {user_info}\n"
            error_text += f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
            
            # Добавляем тип обновления
            if update:
                if update.message:
                    error_text += f"\n📝 Тип: Сообщение"
                elif update.callback_query:
                    error_text += f"\n📝 Тип: Callback Query"
                    error_text += f"\n🔘 Данные: {update.callback_query.data}"
            
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=error_text,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления администратора об ошибке: {e}")
    
    # Отправляем сообщение пользователю об ошибке
    try:
        if update and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Произошла ошибка при обработке запроса. Попробуйте еще раз или используйте /start для перезапуска."
            )
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения пользователю: {e}")


# Основная функция запуска
def main():
    """Основная функция запуска"""
    check_single_instance() # Вызываем проверку экземпляров перед запуском бота

    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN не установлен!")
        print("Установите переменную окружения TELEGRAM_BOT_TOKEN")
        sys.exit(1)
    
    if not ADMIN_ID:
        print("❌ ADMIN_ID не установлен!")
        print("Установите переменную окружения ADMIN_ID")
        sys.exit(1)
    
    print("🚀 Запуск Telegram Financial Bot...")
    print(f"📊 Порт: {PORT}")
    print(f"👤 Администратор: {ADMIN_ID}")
    
    # Инициализируем крипточекер
    global crypto_checker
    try:
        from crypto_checker import SimpleCryptoChecker
        crypto_checker = SimpleCryptoChecker()
        
        # Проверяем, что методы существуют
        if hasattr(crypto_checker, 'get_crypto_price') and hasattr(crypto_checker, 'calculate_crypto_amount'):
            print("✅ Крипточекер инициализирован успешно")
            print(f"📊 Доступные валюты: {list(crypto_checker.wallets.keys())}")
        else:
            print("⚠️ Крипточекер инициализирован, но методы недоступны")
            crypto_checker = None
    except ImportError as e:
        print(f"⚠️ Ошибка импорта crypto_checker_simple: {e}")
        crypto_checker = None
    except Exception as e:
        print(f"⚠️ Ошибка инициализации крипточекера: {e}")
        crypto_checker = None
    
    # Создаем приложение
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("check_payment", check_payment_command))
    application.add_handler(CommandHandler("add_money", add_money_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    # Добавляем обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Обработчик сигналов для корректного завершения
    def signal_handler(signum, frame):
        print(f"\n🛑 Получен сигнал {signum}, завершение работы...")
        try:
            # Удаляем файл блокировки
            lock_file = os.path.join(tempfile.gettempdir(), 'telegram_bot.lock')
            if os.path.exists(lock_file):
                os.remove(lock_file)
                print("✅ Файл блокировки удален")
        except Exception as e:
            print(f"⚠️ Ошибка удаления файла блокировки: {e}")
        sys.exit(0)
    
    # Регистрируем обработчики сигналов
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Запускаем Flask в отдельном потоке
    def run_flask():
        app.run(host='0.0.0.0', port=PORT, debug=False)
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    print(f"🌐 Flask сервер запущен на порту {PORT}")
    
    print("🤖 Бот запущен и готов к работе!")
    print("🔍 Криптоплатежи будут проверяться при создании заказов")
    
    # Запускаем бота
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен пользователем")
        signal_handler(signal.SIGINT, None)
    except Exception as e:
        print(f"❌ Ошибка запуска бота: {e}")
        signal_handler(signal.SIGTERM, None)


if __name__ == '__main__':
    main()

