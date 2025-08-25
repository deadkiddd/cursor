#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Файл для деплоя на Render.com
Поддерживает webhook и polling режимы
"""

import os
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.error import Conflict, NetworkError, TimedOut
from flask import Flask, request, jsonify
import threading
import time
from datetime import datetime, timedelta
import json
import re
import signal
import sys
import sqlite3
from decimal import Decimal, ROUND_HALF_UP
import threading
import time
from crypto_checker import CryptoPaymentChecker, auto_issue_card

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
ADMIN_ID_2 = int(os.getenv('ADMIN_ID_2', 0))
OPERATOR_USERNAME = "@swiwell"
OPERATOR_USERNAME_2 = "@realdealkid"
PORT = int(os.getenv('PORT', 10000))

# Проверка переменных окружения
print("🔍 Проверка переменных окружения:")
print(f"TELEGRAM_BOT_TOKEN: {'✅ Установлен' if TELEGRAM_BOT_TOKEN else '❌ НЕ УСТАНОВЛЕН'}")
print(f"ADMIN_ID: {ADMIN_ID if ADMIN_ID else '❌ НЕ УСТАНОВЛЕН'}")
print(f"ADMIN_ID_2: {ADMIN_ID_2 if ADMIN_ID_2 else '❌ НЕ УСТАНОВЛЕН'}")
print(f"PORT: {PORT}")

# Настройки безопасности
MAX_MESSAGE_LENGTH = 4096
RATE_LIMIT_MESSAGES = 60
RATE_LIMIT_WINDOW = 60

# Кэш для rate limiting
user_message_times = {}

# Система состояний пользователей
user_states = {}

# Комиссии для разных услуг
COMMISSION_RATES = {
    'netflix': 0.08,
    'steam': 0.08,
    'discord': 0.08,
    'spotify': 0.08,
    'youtube': 0.08,
    'twitch': 0.08,
    'apple_music': 0.08,
    'google_play': 0.08,
    'transfer_eu': 0.08,
    'transfer_us': 0.08,
    'crypto_btc': 0.08,
    'crypto_eth': 0.08,
    'crypto_usdt': 0.08,
    'crypto_sol': 0.08,
    'bybit_transfer': 0.08
}

# Минимальные суммы
MIN_AMOUNTS = {
    'cards': 10,
    'transfers': 50,
    'crypto': 5
}

# Создаем Flask приложение
app = Flask(__name__)

# Инициализация базы данных
def init_database():
    """Инициализация базы данных"""
    try:
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        
        # Таблица кошельков
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS wallets (
                user_id INTEGER PRIMARY KEY,
                balance REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица транзакций кошельков
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS wallet_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                transaction_type TEXT,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES wallets (user_id)
            )
        ''')
        
        # Таблица заказов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                service_type TEXT,
                amount REAL,
                status TEXT DEFAULT 'pending',
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES wallets (user_id)
            )
        ''')
        
        # Таблица истории статусов заказов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS order_status_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER,
                status TEXT,
                admin_id INTEGER,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (order_id) REFERENCES orders (id)
            )
        ''')
        
        conn.commit()
        conn.close()
        print("✅ База данных инициализирована")
        
    except Exception as e:
        print(f"❌ Ошибка инициализации базы данных: {e}")

# Инициализируем базу данных
init_database()

# Функции для работы с базой данных
def get_user_wallet(user_id):
    """Получить кошелек пользователя"""
    try:
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        
        # Создаем кошелек если не существует
        cursor.execute('''
            INSERT OR IGNORE INTO wallets (user_id, balance)
            VALUES (?, 0.0)
        ''', (user_id,))
        
        cursor.execute('SELECT balance FROM wallets WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.commit()
        conn.close()
        
        return result[0] if result else 0.0
        
    except Exception as e:
        logger.error(f"Ошибка получения кошелька: {e}")
        return 0.0

def update_wallet_balance(user_id, amount, transaction_type, description):
    """Обновить баланс кошелька"""
    try:
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        
        # Обновляем баланс
        cursor.execute('''
            UPDATE wallets 
            SET balance = balance + ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        ''', (amount, user_id))
        
        # Добавляем транзакцию
        cursor.execute('''
            INSERT INTO wallet_transactions (user_id, amount, transaction_type, description)
            VALUES (?, ?, ?, ?)
        ''', (user_id, amount, transaction_type, description))
        
        conn.commit()
        conn.close()
        return True
        
    except Exception as e:
        logger.error(f"Ошибка обновления кошелька: {e}")
        return False

def create_order(user_id, service_type, amount, description):
    """Создать новый заказ"""
    try:
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO orders (user_id, service_type, amount, description)
            VALUES (?, ?, ?, ?)
        ''', (user_id, service_type, amount, description))
        
        order_id = cursor.lastrowid
        
        # Добавляем начальный статус
        cursor.execute('''
            INSERT INTO order_status_history (order_id, status, notes)
            VALUES (?, 'pending', 'Заказ создан')
        ''', (order_id,))
        
        conn.commit()
        conn.close()
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
🤖 Добро пожаловать в Финансовый Бот!

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
    
    if user_id in [ADMIN_ID, ADMIN_ID_2]:
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

💳 Доступные услуги:
• Netflix, Steam, Discord
• Spotify, YouTube Premium
• Переводы на карты
• Криптовалюты (BTC, ETH, USDT)

💰 Оплата:
• Внутренний кошелек
• Криптовалюты
• Переводы

📞 Поддержка:
• Основной оператор: @swiwell
• Техподдержка: @Deadkid
"""
    
    await update.message.reply_text(help_text)

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
    elif data == "admin" and user_id in [ADMIN_ID, ADMIN_ID_2]:
        await show_admin_panel(query)
    elif data.startswith("service_"):
        await handle_service_selection(query, data)
    elif data.startswith("back_"):
        await handle_back_button(query, data)

async def show_catalog(query):
    """Показать каталог услуг"""
    catalog_text = """
🛒 Каталог услуг

Выберите категорию:
"""
    
    keyboard = [
        [InlineKeyboardButton("🎬 Подписки", callback_data="service_subscriptions")],
        [InlineKeyboardButton("💳 Переводы", callback_data="service_transfers")],
        [InlineKeyboardButton("₿ Криптовалюты", callback_data="service_crypto")],
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
    """Показать заказы пользователя"""
    user_id = query.from_user.id
    
    try:
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, service_type, amount, status, created_at 
            FROM orders 
            WHERE user_id = ? 
            ORDER BY created_at DESC 
            LIMIT 10
        ''', (user_id,))
        
        orders = cursor.fetchall()
        conn.close()
        
        if orders:
            orders_text = "📋 Ваши заказы:\n\n"
            for order in orders:
                order_id, service_type, amount, status, created_at = order
                orders_text += f"🔹 Заказ #{order_id}\n"
                orders_text += f"   Услуга: {service_type}\n"
                orders_text += f"   Сумма: {amount:.2f} USD\n"
                orders_text += f"   Статус: {status}\n"
                orders_text += f"   Дата: {created_at}\n\n"
        else:
            orders_text = "📋 У вас пока нет заказов"
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_main")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(orders_text, reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"Ошибка получения заказов: {e}")
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
• Netflix, Steam, Discord
• Spotify, YouTube Premium
• Переводы на карты
• Криптовалюты (BTC, ETH, USDT)

💰 Оплата:
• Внутренний кошелек
• Криптовалюты
• Переводы

📞 Поддержка:
• Основной оператор: @swiwell
• Техподдержка: @Deadkid
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
        [InlineKeyboardButton("Spotify", callback_data="order_spotify")],
        [InlineKeyboardButton("YouTube Premium", callback_data="order_youtube")],
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
        [InlineKeyboardButton("🇪🇺 Европейские карты", callback_data="order_transfer_eu")],
        [InlineKeyboardButton("🇺🇸 Американские карты", callback_data="order_transfer_us")],
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
        [InlineKeyboardButton("Bitcoin (BTC)", callback_data="order_crypto_btc")],
        [InlineKeyboardButton("Ethereum (ETH)", callback_data="order_crypto_eth")],
        [InlineKeyboardButton("USDT", callback_data="order_crypto_usdt")],
        [InlineKeyboardButton("Solana (SOL)", callback_data="order_crypto_sol")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_catalog")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(crypto_text, reply_markup=reply_markup)

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
🤖 Добро пожаловать в Финансовый Бот!

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
    
    if user_id in [ADMIN_ID, ADMIN_ID_2]:
        keyboard.append([InlineKeyboardButton("🔧 Админ панель", callback_data="admin")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Редактируем сообщение
    await query.edit_message_text(welcome_text, reply_markup=reply_markup)

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
    """Статистика бота"""
    try:
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        
        # Количество пользователей
        cursor.execute('SELECT COUNT(*) FROM wallets')
        users_count = cursor.fetchone()[0]
        
        # Количество заказов
        cursor.execute('SELECT COUNT(*) FROM orders')
        orders_count = cursor.fetchone()[0]
        
        # Общая сумма заказов
        cursor.execute('SELECT SUM(amount) FROM orders')
        total_amount = cursor.fetchone()[0] or 0
        
        conn.close()
        
        return jsonify({
            'users_count': users_count,
            'orders_count': orders_count,
            'total_amount': total_amount,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# API для администраторов
@app.route('/admin/orders', methods=['GET'])
def get_orders():
    """Получить все заказы"""
    try:
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT o.id, o.user_id, o.service_type, o.amount, o.status, o.created_at,
                   w.balance
            FROM orders o
            LEFT JOIN wallets w ON o.user_id = w.user_id
            ORDER BY o.created_at DESC
        ''')
        
        orders = cursor.fetchall()
        conn.close()
        
        orders_list = []
        for order in orders:
            orders_list.append({
                'id': order[0],
                'user_id': order[1],
                'service_type': order[2],
                'amount': order[3],
                'status': order[4],
                'created_at': order[5],
                'user_balance': order[6] or 0
            })
        
        return jsonify(orders_list)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/order/<int:order_id>', methods=['GET'])
def get_order(order_id):
    """Получить конкретный заказ"""
    try:
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT o.*, w.balance
            FROM orders o
            LEFT JOIN wallets w ON o.user_id = w.user_id
            WHERE o.id = ?
        ''', (order_id,))
        
        order = cursor.fetchone()
        conn.close()
        
        if order:
            return jsonify({
                'id': order[0],
                'user_id': order[1],
                'service_type': order[2],
                'amount': order[3],
                'status': order[4],
                'description': order[5],
                'created_at': order[6],
                'updated_at': order[7],
                'user_balance': order[8] or 0
            })
        else:
            return jsonify({'error': 'Заказ не найден'}), 404
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/order/<int:order_id>/status', methods=['POST'])
def update_order_status(order_id):
    """Обновить статус заказа"""
    try:
        data = request.get_json()
        new_status = data.get('status')
        notes = data.get('notes', '')
        admin_id = data.get('admin_id')
        
        if not new_status:
            return jsonify({'error': 'Статус обязателен'}), 400
        
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        
        # Обновляем статус заказа
        cursor.execute('''
            UPDATE orders 
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (new_status, order_id))
        
        # Добавляем в историю
        cursor.execute('''
            INSERT INTO order_status_history (order_id, status, admin_id, notes)
            VALUES (?, ?, ?, ?)
        ''', (order_id, new_status, admin_id, notes))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Статус обновлен'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/wallet/<int:user_id>', methods=['GET'])
def get_wallet_info(user_id):
    """Получить информацию о кошельке"""
    try:
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT w.balance, w.created_at,
                   COUNT(t.id) as transactions_count
            FROM wallets w
            LEFT JOIN wallet_transactions t ON w.user_id = t.user_id
            WHERE w.user_id = ?
            GROUP BY w.user_id
        ''', (user_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return jsonify({
                'user_id': user_id,
                'balance': result[0],
                'created_at': result[1],
                'transactions_count': result[2]
            })
        else:
            return jsonify({'error': 'Кошелек не найден'}), 404
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/wallet/<int:user_id>/deposit', methods=['POST'])
def deposit_wallet(user_id):
    """Пополнить кошелек"""
    try:
        data = request.get_json()
        amount = data.get('amount')
        admin_id = data.get('admin_id')
        
        if not amount or amount <= 0:
            return jsonify({'error': 'Сумма должна быть больше 0'}), 400
        
        success = update_wallet_balance(user_id, amount, 'deposit', f'Пополнение администратором {admin_id}')
        
        if success:
            return jsonify({'success': True, 'message': 'Кошелек пополнен'})
        else:
            return jsonify({'error': 'Ошибка пополнения'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/wallet/<int:user_id>/withdraw', methods=['POST'])
def withdraw_wallet(user_id):
    """Вывести средства из кошелька"""
    try:
        data = request.get_json()
        amount = data.get('amount')
        admin_id = data.get('admin_id')
        
        if not amount or amount <= 0:
            return jsonify({'error': 'Сумма должна быть больше 0'}), 400
        
        # Проверяем баланс
        current_balance = get_user_wallet(user_id)
        if current_balance < amount:
            return jsonify({'error': 'Недостаточно средств'}), 400
        
        success = update_wallet_balance(user_id, -amount, 'withdraw', f'Вывод администратором {admin_id}')
        
        if success:
            return jsonify({'success': True, 'message': 'Средства выведены'})
        else:
            return jsonify({'error': 'Ошибка вывода'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
    
    # Создаем приложение
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Добавляем обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Запускаем Flask в отдельном потоке
    def run_flask():
        app.run(host='0.0.0.0', port=PORT, debug=False)
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    print(f"🌐 Flask сервер запущен на порту {PORT}")
    print("🤖 Бот запущен и готов к работе!")
    
    # Запускаем бота
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен")
    except Exception as e:
        print(f"❌ Ошибка запуска бота: {e}")

if __name__ == '__main__':
    main()
