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

# первый етст коммит
# Загружаем переменные окружения из .env файла
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
ADMIN_ID_2 = int(os.getenv('ADMIN_ID_2', 0))  # Второй администратор
OPERATOR_USERNAME = "@swiwell"
OPERATOR_USERNAME_2 = "@Deadkid"
PORT = int(os.getenv('PORT', 10000))

# Если токен не найден в .env, попробуем альтернативные способы
if not TELEGRAM_BOT_TOKEN:
    # Попробуем прочитать из файла напрямую
    try:
        with open('.env', 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('TELEGRAM_BOT_TOKEN='):
                    TELEGRAM_BOT_TOKEN = line.split('=', 1)[1].strip()
                    break
        print("✅ Токен прочитан из файла .env напрямую")
    except Exception as e:
        print(f"❌ Не удалось прочитать токен из файла: {e}")
        # Здесь можно временно указать токен напрямую (НЕ ДЛЯ ПРОДАКШЕНА!)
        # TELEGRAM_BOT_TOKEN = "ваш_токен_здесь"

# Проверка переменных окружения
print("🔍 Проверка переменных окружения:")
print(f"TELEGRAM_BOT_TOKEN: {'✅ Установлен' if TELEGRAM_BOT_TOKEN else '❌ НЕ УСТАНОВЛЕН'}")
print(f"ADMIN_ID: {ADMIN_ID if ADMIN_ID else '❌ НЕ УСТАНОВЛЕН'}")
print(f"ADMIN_ID_2: {ADMIN_ID_2 if ADMIN_ID_2 else '❌ НЕ УСТАНОВЛЕН'}")
print(f"PORT: {PORT}")

# Настройки безопасности
MAX_MESSAGE_LENGTH = 4096
RATE_LIMIT_MESSAGES = 60  # сообщений в минуту
RATE_LIMIT_WINDOW = 60  # секунд

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
    'crypto_usdt': 0.08
}

# Минимальные суммы
MIN_AMOUNTS = {
    'cards': 10,
    'transfers': 10,
    'crypto': 5
}

# Флаг для graceful shutdown
shutdown_flag = False

# База данных для кошельков и заказов
DB_PATH = 'bot_database.db'

# Статусы заказов
ORDER_STATUSES = {
    'pending': 'Ожидает оплаты',
    'paid': 'Оплачен',
    'processing': 'В обработке',
    'completed': 'Завершен',
    'cancelled': 'Отменен',
    'failed': 'Ошибка'
}

# Типы транзакций кошелька
WALLET_TRANSACTION_TYPES = {
    'deposit': 'Пополнение',
    'withdrawal': 'Вывод',
    'payment': 'Оплата заказа',
    'refund': 'Возврат',
    'commission': 'Комиссия'
}

def init_database():
    """Инициализация базы данных"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Таблица пользователей и кошельков
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS wallets (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance DECIMAL(10,2) DEFAULT 0.00,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица транзакций кошелька
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS wallet_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                transaction_type TEXT,
                amount DECIMAL(10,2),
                description TEXT,
                order_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES wallets (user_id)
            )
        ''')
        
        # Таблица заказов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                order_type TEXT,
                service_name TEXT,
                amount DECIMAL(10,2),
                commission DECIMAL(10,2),
                total_amount DECIMAL(10,2),
                status TEXT DEFAULT 'pending',
                payment_method TEXT,
                wallet_payment BOOLEAN DEFAULT FALSE,
                admin_notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
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
        logger.info("База данных инициализирована успешно")
    except Exception as e:
        logger.error(f"Ошибка инициализации базы данных: {e}")

def get_or_create_wallet(user_id, username=None, first_name=None):
    """Получить или создать кошелек пользователя"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Проверяем существование кошелька
        cursor.execute('SELECT * FROM wallets WHERE user_id = ?', (user_id,))
        wallet = cursor.fetchone()
        
        if wallet:
            return {
                'user_id': wallet[0],
                'username': wallet[1],
                'first_name': wallet[2],
                'balance': float(wallet[3]),
                'created_at': wallet[4],
                'updated_at': wallet[5]
            }
        else:
            # Создаем новый кошелек
            cursor.execute('''
                INSERT INTO wallets (user_id, username, first_name, balance)
                VALUES (?, ?, ?, 0.00)
            ''', (user_id, username, first_name))
            conn.commit()
            
            return {
                'user_id': user_id,
                'username': username,
                'first_name': first_name,
                'balance': 0.00,
                'created_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat()
            }
    except Exception as e:
        logger.error(f"Ошибка получения/создания кошелька: {e}")
        return None
    finally:
        conn.close()

def add_wallet_transaction(user_id, transaction_type, amount, description, order_id=None):
    """Добавить транзакцию в кошелек"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Добавляем транзакцию
        cursor.execute('''
            INSERT INTO wallet_transactions (user_id, transaction_type, amount, description, order_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, transaction_type, amount, description, order_id))
        
        # Обновляем баланс кошелька
        if transaction_type in ['deposit', 'refund']:
            cursor.execute('''
                UPDATE wallets 
                SET balance = balance + ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            ''', (amount, user_id))
        elif transaction_type in ['withdrawal', 'payment', 'commission']:
            cursor.execute('''
                UPDATE wallets 
                SET balance = balance - ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            ''', (amount, user_id))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка добавления транзакции: {e}")
        return False

def create_order(user_id, order_type, service_name, amount, commission, total_amount, payment_method, wallet_payment=False):
    """Создать новый заказ"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO orders (user_id, order_type, service_name, amount, commission, total_amount, payment_method, wallet_payment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, order_type, service_name, amount, commission, total_amount, payment_method, wallet_payment))
        
        order_id = cursor.lastrowid
        
        # Добавляем запись в историю статусов
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

def update_order_status(order_id, new_status, admin_id=None, notes=None):
    """Обновить статус заказа"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Обновляем статус заказа
        cursor.execute('''
            UPDATE orders 
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (new_status, order_id))
        
        # Если заказ завершен, устанавливаем время завершения
        if new_status == 'completed':
            cursor.execute('''
                UPDATE orders 
                SET completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (order_id,))
        
        # Добавляем запись в историю статусов
        cursor.execute('''
            INSERT INTO order_status_history (order_id, status, admin_id, notes)
            VALUES (?, ?, ?, ?)
        ''', (order_id, new_status, admin_id, notes))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления статуса заказа: {e}")
        return False

def get_user_orders(user_id, limit=10):
    """Получить заказы пользователя"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, order_type, service_name, amount, total_amount, status, created_at, updated_at
            FROM orders 
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        ''', (user_id, limit))
        
        orders = cursor.fetchall()
        conn.close()
        
        return [{
            'id': order[0],
            'order_type': order[1],
            'service_name': order[2],
            'amount': float(order[3]),
            'total_amount': float(order[4]),
            'status': order[5],
            'created_at': order[6],
            'updated_at': order[7]
        } for order in orders]
    except Exception as e:
        logger.error(f"Ошибка получения заказов пользователя: {e}")
        return []

def get_order_details(order_id):
    """Получить детали заказа"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT o.*, w.username, w.first_name
            FROM orders o
            LEFT JOIN wallets w ON o.user_id = w.user_id
            WHERE o.id = ?
        ''', (order_id,))
        
        order = cursor.fetchone()
        
        if not order:
            conn.close()
            return None
        
        # Получаем историю статусов
        cursor.execute('''
            SELECT status, admin_id, notes, created_at
            FROM order_status_history
            WHERE order_id = ?
            ORDER BY created_at ASC
        ''', (order_id,))
        
        status_history = cursor.fetchall()
        
        conn.close()
        
        return {
            'id': order[0],
            'user_id': order[1],
            'order_type': order[2],
            'service_name': order[3],
            'amount': float(order[4]),
            'commission': float(order[5]),
            'total_amount': float(order[6]),
            'status': order[7],
            'payment_method': order[8],
            'wallet_payment': bool(order[9]),
            'admin_notes': order[10],
            'created_at': order[11],
            'updated_at': order[12],
            'completed_at': order[13],
            'username': order[14],
            'first_name': order[15],
            'status_history': [{
                'status': status[0],
                'admin_id': status[1],
                'notes': status[2],
                'created_at': status[3]
            } for status in status_history]
        }
    except Exception as e:
        logger.error(f"Ошибка получения деталей заказа: {e}")
        return None

def get_wallet_transactions(user_id, limit=10):
    """Получить транзакции кошелька пользователя"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT transaction_type, amount, description, created_at
            FROM wallet_transactions
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        ''', (user_id, limit))
        
        transactions = cursor.fetchall()
        conn.close()
        
        return [{
            'transaction_type': trans[0],
            'amount': float(trans[1]),
            'description': trans[2],
            'created_at': trans[3]
        } for trans in transactions]
    except Exception as e:
        logger.error(f"Ошибка получения транзакций кошелька: {e}")
        return []

def get_all_orders(limit=50, status=None):
    """Получить все заказы (для администратора)"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        if status:
            cursor.execute('''
                SELECT o.id, o.user_id, o.order_type, o.service_name, o.amount, o.total_amount, o.status, o.created_at, w.username, w.first_name
                FROM orders o
                LEFT JOIN wallets w ON o.user_id = w.user_id
                WHERE o.status = ?
                ORDER BY o.created_at DESC
                LIMIT ?
            ''', (status, limit))
        else:
            cursor.execute('''
                SELECT o.id, o.user_id, o.order_type, o.service_name, o.amount, o.total_amount, o.status, o.created_at, w.username, w.first_name
                FROM orders o
                LEFT JOIN wallets w ON o.user_id = w.user_id
                ORDER BY o.created_at DESC
                LIMIT ?
            ''', (limit,))
        
        orders = cursor.fetchall()
        conn.close()
        
        return [{
            'id': order[0],
            'user_id': order[1],
            'order_type': order[2],
            'service_name': order[3],
            'amount': float(order[4]),
            'total_amount': float(order[5]),
            'status': order[6],
            'created_at': order[7],
            'username': order[8],
            'first_name': order[9]
        } for order in orders]
    except Exception as e:
        logger.error(f"Ошибка получения всех заказов: {e}")
        return []

def admin_deposit_to_wallet(user_id, amount, admin_id, description="Пополнение администратором"):
    """Пополнение кошелька пользователя администратором"""
    try:
        # Проверяем существование кошелька
        wallet = get_or_create_wallet(user_id)
        if not wallet:
            return False
        
        # Добавляем транзакцию пополнения
        success = add_wallet_transaction(
            user_id=user_id,
            transaction_type='deposit',
            amount=amount,
            description=description,
            order_id=None
        )
        
        if success:
            # Добавляем запись в историю административных действий
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO wallet_transactions (user_id, transaction_type, amount, description, order_id)
                VALUES (?, 'deposit', ?, ?, NULL)
            ''', (user_id, amount, f"{description} (Админ ID: {admin_id})"))
            conn.commit()
            conn.close()
            
        return success
    except Exception as e:
        logger.error(f"Ошибка пополнения кошелька администратором: {e}")
        return False

def admin_withdraw_from_wallet(user_id, amount, admin_id, description="Вывод администратором"):
    """Вывод средств из кошелька пользователя администратором"""
    try:
        # Проверяем существование кошелька и достаточность средств
        wallet = get_or_create_wallet(user_id)
        if not wallet or wallet['balance'] < amount:
            return False
        
        # Добавляем транзакцию вывода
        success = add_wallet_transaction(
            user_id=user_id,
            transaction_type='withdrawal',
            amount=amount,
            description=description,
            order_id=None
        )
        
        if success:
            # Добавляем запись в историю административных действий
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO wallet_transactions (user_id, transaction_type, amount, description, order_id)
                VALUES (?, 'withdrawal', ?, ?, NULL)
            ''', (user_id, amount, f"{description} (Админ ID: {admin_id})"))
            conn.commit()
            conn.close()
            
        return success
    except Exception as e:
        logger.error(f"Ошибка вывода из кошелька администратором: {e}")
        return False

def get_user_wallet_info(user_id):
    """Получить информацию о кошельке пользователя"""
    try:
        wallet = get_or_create_wallet(user_id)
        if not wallet:
            return None
        
        transactions = get_wallet_transactions(user_id, limit=10)
        
        return {
            'wallet': wallet,
            'transactions': transactions
        }
    except Exception as e:
        logger.error(f"Ошибка получения информации о кошельке: {e}")
        return None

# Flask приложение для проверки состояния
app = Flask(__name__)

@app.route('/')
def health_check():
    return jsonify({
        "status": "healthy", 
        "bot": "running",
        "timestamp": datetime.now().isoformat(),
        "uptime": get_uptime()
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    return jsonify({"status": "ok"})

@app.route('/stats', methods=['GET'])
def get_stats():
    return jsonify({
        "active_users": len(user_message_times),
        "timestamp": datetime.now().isoformat()
    })

@app.route('/admin/orders', methods=['GET'])
def admin_get_orders():
    """Получить все заказы для администратора"""
    try:
        status = request.args.get('status')
        limit = int(request.args.get('limit', 50))
        orders = get_all_orders(limit=limit, status=status)
        return jsonify({
            "success": True,
            "orders": orders,
            "count": len(orders)
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/admin/order/<int:order_id>', methods=['GET'])
def admin_get_order(order_id):
    """Получить детали заказа"""
    try:
        order = get_order_details(order_id)
        if not order:
            return jsonify({
                "success": False,
                "error": "Заказ не найден"
            }), 404
        
        return jsonify({
            "success": True,
            "order": order
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/admin/order/<int:order_id>/status', methods=['POST'])
def admin_update_order_status():
    """Обновить статус заказа"""
    try:
        data = request.get_json()
        new_status = data.get('status')
        admin_id = data.get('admin_id')
        notes = data.get('notes', '')
        
        if not new_status:
            return jsonify({
                "success": False,
                "error": "Статус не указан"
            }), 400
        
        success = update_order_status(order_id, new_status, admin_id, notes)
        if not success:
            return jsonify({
                "success": False,
                "error": "Ошибка обновления статуса"
            }), 500
        
        return jsonify({
            "success": True,
            "message": f"Статус заказа #{order_id} обновлен на '{new_status}'"
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/admin/wallet/<int:user_id>', methods=['GET'])
def admin_get_wallet(user_id):
    """Получить информацию о кошельке пользователя"""
    try:
        wallet_info = get_user_wallet_info(user_id)
        if not wallet_info:
            return jsonify({
                "success": False,
                "error": "Кошелек не найден"
            }), 404
        
        return jsonify({
            "success": True,
            "wallet": wallet_info
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/admin/wallet/<int:user_id>/deposit', methods=['POST'])
def admin_deposit_wallet(user_id):
    """Пополнить кошелек пользователя"""
    try:
        data = request.get_json()
        amount = data.get('amount')
        admin_id = data.get('admin_id')
        description = data.get('description', 'Пополнение администратором')
        
        if not amount or not admin_id:
            return jsonify({
                "success": False,
                "error": "Сумма и ID администратора обязательны"
            }), 400
        
        success = admin_deposit_to_wallet(user_id, float(amount), admin_id, description)
        if not success:
            return jsonify({
                "success": False,
                "error": "Ошибка пополнения кошелька"
            }), 500
        
        return jsonify({
            "success": True,
            "message": f"Кошелек пользователя {user_id} пополнен на ${amount}"
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/admin/wallet/<int:user_id>/withdraw', methods=['POST'])
def admin_withdraw_wallet(user_id):
    """Вывести средства из кошелька пользователя"""
    try:
        data = request.get_json()
        amount = data.get('amount')
        admin_id = data.get('admin_id')
        description = data.get('description', 'Вывод администратором')
        
        if not amount or not admin_id:
            return jsonify({
                "success": False,
                "error": "Сумма и ID администратора обязательны"
            }), 400
        
        success = admin_withdraw_from_wallet(user_id, float(amount), admin_id, description)
        if not success:
            return jsonify({
                "success": False,
                "error": "Ошибка вывода средств или недостаточно средств"
            }), 500
        
        return jsonify({
            "success": True,
            "message": f"Из кошелька пользователя {user_id} выведено ${amount}"
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/restart', methods=['POST'])
def restart_bot():
    """Эндпоинт для перезапуска бота"""
    global shutdown_flag
    shutdown_flag = True
    return jsonify({"status": "restarting"})

def start_flask():
    try:
        app.run(host='0.0.0.0', port=PORT, debug=False)
    except Exception as e:
        logger.error(f"Ошибка запуска Flask: {e}")

def get_uptime():
    """Получить время работы бота"""
    if hasattr(get_uptime, 'start_time'):
        return str(datetime.now() - get_uptime.start_time)
    get_uptime.start_time = datetime.now()
    return "0:00:00"

def check_rate_limit(user_id):
    """Проверка ограничения скорости сообщений"""
    current_time = time.time()
    if user_id not in user_message_times:
        user_message_times[user_id] = []
    
    # Удаляем старые сообщения
    user_message_times[user_id] = [
        msg_time for msg_time in user_message_times[user_id] 
        if current_time - msg_time < RATE_LIMIT_WINDOW
    ]
    
    # Проверяем лимит
    if len(user_message_times[user_id]) >= RATE_LIMIT_MESSAGES:
        return False
    
    # Добавляем текущее сообщение
    user_message_times[user_id].append(current_time)
    return True

def sanitize_text(text):
    """Очистка текста от потенциально опасных символов"""
    if not text:
        return ""
    # Удаляем HTML теги и экранируем специальные символы
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return text[:MAX_MESSAGE_LENGTH]

def escape_markdown(text):
    """Экранирование специальных символов для Markdown"""
    if not text:
        return ""
    # Экранируем символы Markdown
    chars_to_escape = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in chars_to_escape:
        text = text.replace(char, f'\\{char}')
    return text

def get_user_state(user_id):
    """Получить состояние пользователя"""
    return user_states.get(user_id, {})

def set_user_state(user_id, state_data):
    """Установить состояние пользователя"""
    user_states[user_id] = state_data

def clear_user_state(user_id):
    """Очистить состояние пользователя"""
    if user_id in user_states:
        del user_states[user_id]

def calculate_commission(service_type, amount):
    """Рассчитать комиссию и итоговую сумму
    - Для сумм < $30 действует фиксированная комиссия $3
    - Для сумм >= $30 используется процент по тарифу
    """
    commission_rate = COMMISSION_RATES.get(service_type, 0.08)
    if amount < 30:
        commission = 3.0
        is_fixed = True
    else:
        commission = amount * commission_rate
        is_fixed = False
    total_amount = amount + commission
    return {
        'original_amount': amount,
        'commission_rate': commission_rate,
        'commission': commission,
        'total_amount': total_amount,
        'is_fixed': is_fixed
    }

def get_payment_address(service_type):
    """Получить адрес для оплаты в зависимости от типа услуги"""
    addresses = {
        'cards': {
            'BTC': 'bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh',
            'ETH': '0x742d35Cc6634C0532925a3b8D4C9db96C4b4d8b6',
            'USDT_TRC20': 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'
        },
        'transfers': {
            'BTC': 'bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh',
            'ETH': '0x742d35Cc6634C0532925a3b8D4C9db96C4b4d8b6',
            'USDT_TRC20': 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'
        },
        'crypto': {
            'BTC': 'bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh',
            'ETH': '0x742d35Cc6634C0532925a3b8D4C9db96C4b4d8b6',
            'USDT_TRC20': 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'
        }
    }
    return addresses.get(service_type, addresses['cards'])

async def send_admin_notification(context, title, user, additional_info=""):
    """Отправка уведомления администратору"""
    if not ADMIN_ID:
        return
    
    try:
        message = f"{title}\n\n"
        message += f"👤 Пользователь: {user.first_name} (@{user.username or 'без username'})\n"
        message += f"🆔 ID: `{user.id}`\n"
        message += f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        
        if additional_info:
            message += f"\n\n{additional_info}"
        
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=message,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления администратору: {e}")

async def send_admin_notification_to_all(context, title, user, additional_info=""):
    """Отправка уведомления обоим администраторам"""
    if not ADMIN_ID and not ADMIN_ID_2:
        return
    
    try:
        message = f"{title}\n\n"
        message += f"👤 Пользователь: {user.first_name} (@{user.username or 'без username'})\n"
        message += f"🆔 ID: `{user.id}`\n"
        message += f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        
        if additional_info:
            message += f"\n\n{additional_info}"
        
        if ADMIN_ID:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=message,
                parse_mode='Markdown'
            )
        if ADMIN_ID_2:
            await context.bot.send_message(
                chat_id=ADMIN_ID_2,
                text=message,
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления обоим администраторам: {e}")

# Обработчики бота
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start"""
    if not update.message:
        return
        
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # Проверка rate limit
    if not check_rate_limit(user.id):
        await update.message.reply_text("⚠️ Слишком много сообщений. Подождите немного.")
        return
    
    # Уведомление администратора о новом пользователе
    await send_admin_notification_to_all(
        context, 
        "🆕 **Новый пользователь зарегистрирован!**", 
        user
    )
    
    welcome_text = """
🤖 **Добро пожаловать в Финансовый Бот!**

Мы предоставляем следующие услуги:
• 💳 **Оплата зарубежными картами** (Netflix, Steam, Discord, Spotify и др.)
• 💸 **Переводы на европейские и американские банковские карты**
• ₿ **Поддержка криптовалют**: BTC, ETH, USDT (TRC20/ERC20)

📋 **Условия:**
• Минимальная сумма: $10
• Комиссия: 5-15% в зависимости от услуги
• Время обработки: 10-30 минут
• Круглосуточная поддержка

Выберите нужную услугу:
"""
    
    keyboard = [
        [InlineKeyboardButton("💳 Оплата картами", callback_data="payment_cards")],
        [InlineKeyboardButton("💸 Переводы", callback_data="transfers")],
        [InlineKeyboardButton("₿ Криптовалюты", callback_data="crypto")],
        [InlineKeyboardButton("💰 Мой кошелек", callback_data="wallet_info")],
        [InlineKeyboardButton("📋 Мои заказы", callback_data="my_orders")],
        [InlineKeyboardButton("📞 Связаться с оператором", callback_data="contact_operator")],
        [InlineKeyboardButton("💰 Прайс-лист", callback_data="price_list")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /menu"""
    if not update.message:
        return

    user = update.effective_user

    # Проверка rate limit
    if not check_rate_limit(user.id):
        await update.message.reply_text("⚠️ Слишком много сообщений. Подождите немного.")
        return

    keyboard = [
        [InlineKeyboardButton("💳 Оплата картами", callback_data="payment_cards")],
        [InlineKeyboardButton("💸 Переводы", callback_data="transfers")],
        [InlineKeyboardButton("₿ Оплата криптовалютой", callback_data="crypto")],
        [InlineKeyboardButton("💰 Мой кошелек", callback_data="wallet_info")],
        [InlineKeyboardButton("📋 Мои заказы", callback_data="my_orders")],
        [InlineKeyboardButton("📞 Связаться с оператором", callback_data="contact_operator")],
        [InlineKeyboardButton("💰 Прайс-лист", callback_data="price_list")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text("Выберите категорию услуг:", reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /help"""
    if not update.message:
        return
        
    user = update.effective_user
    
    # Проверка rate limit
    if not check_rate_limit(user.id):
        await update.message.reply_text("⚠️ Слишком много сообщений. Подождите немного.")
        return
    
    help_text = """
📖 **Справка по использованию бота:**

**Основные команды:**
/start - Главное меню
/menu - Каталог услуг
/help - Эта справка
/address - Реквизиты для оплаты
/price - Прайс-лист
/wallet - Мой кошелек
/orders - Мои заказы

**Команды администратора:**
/admin_orders - Все заказы (только для админов)

**Как пользоваться:**
1. Выберите нужную услугу из меню
2. Укажите сумму и детали
3. Получите реквизиты для оплаты
4. После оплаты свяжитесь с оператором

**Кошелек:**
• Пополняйте баланс для быстрых покупок
• Отслеживайте историю транзакций
• Используйте средства для оплаты заказов

**Отслеживание заказов:**
• Просматривайте статус своих заказов
• Получайте уведомления об изменениях
• История всех транзакций

**Поддержка:**
• @swiwell - Основной оператор
• @Deadkid - Техническая поддержка

**Время работы:** Круглосуточно

**Ограничения:**
• Максимум 60 сообщений в минуту
• Максимальная длина сообщения: 4096 символов
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def address_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /address"""
    if not update.message:
        return
        
    user = update.effective_user
    
    # Проверка rate limit
    if not check_rate_limit(user.id):
        await update.message.reply_text("⚠️ Слишком много сообщений. Подождите немного.")
        return
    
    address_text = f"""
🏦 **Реквизиты для оплаты:**

**Банковская карта:**
Номер: 1234 5678 9012 3456
Срок действия: 12/25
CVV: 123

**Криптокошелек:**
BTC: `bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh`
ETH: `0x742d35Cc6634C0532925a3b8D4C9db96C4b4d8b6`
USDT (TRC20): `TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t`

⚠️ **Важно:** Указывайте комментарий к платежу с вашим Telegram ID: `{user.id}`

🔒 **Безопасность:** Все транзакции защищены и отслеживаются
"""
    
    await update.message.reply_text(address_text, parse_mode='Markdown')

async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /price"""
    if not update.message:
        return
        
    user = update.effective_user
    
    # Проверка rate limit
    if not check_rate_limit(user.id):
        await update.message.reply_text("⚠️ Слишком много сообщений. Подождите немного.")
        return
    
    price_text = """
💰 **Прайс-лист услуг:**

**💳 Оплата зарубежными картами:**
• Netflix: $15-50 (комиссия 10%)
• Steam: $10-100 (комиссия 8%)
• Discord Nitro: $10-20 (комиссия 12%)
• Spotify: $10-15 (комиссия 15%)
• YouTube Premium: $12-18 (комиссия 13%)
• Twitch Subscriptions: $5-25 (комиссия 11%)

**💸 Переводы на карты:**
• Европейские карты: 5-8% комиссия
• Американские карты: 8-12% комиссия
• Минимальная сумма: $10

**₿ Криптовалюты:**
• BTC: 3% комиссия
• ETH: 4% комиссия
• USDT: 2% комиссия

**⏱️ Время обработки:** 10-30 минут

**💳 Способы оплаты:**
• Банковские карты
• Криптовалюты
• Электронные кошельки
"""
    await update.message.reply_text(price_text, parse_mode='Markdown')

async def wallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /wallet - показать кошелек пользователя"""
    if not update.message:
        return
        
    user = update.effective_user
    
    # Проверка rate limit
    if not check_rate_limit(user.id):
        await update.message.reply_text("⚠️ Слишком много сообщений. Подождите немного.")
        return
    
    # Получаем или создаем кошелек
    wallet = get_or_create_wallet(user.id, user.username, user.first_name)
    
    if not wallet:
        await update.message.reply_text("❌ Ошибка получения кошелька. Попробуйте позже.")
        return
    
    # Получаем последние транзакции
    transactions = get_wallet_transactions(user.id, limit=5)
    
    wallet_text = f"""
💰 **Ваш кошелек:**

**💳 Баланс:** ${wallet['balance']:.2f}

**📊 Последние транзакции:**
"""
    
    if transactions:
        for trans in transactions:
            trans_type = WALLET_TRANSACTION_TYPES.get(trans['transaction_type'], trans['transaction_type'])
            amount_sign = "+" if trans['transaction_type'] in ['deposit', 'refund'] else "-"
            wallet_text += f"• {trans_type}: {amount_sign}${trans['amount']:.2f}\n"
            wallet_text += f"  📝 {trans['description']}\n"
            wallet_text += f"  📅 {trans['created_at'][:19]}\n\n"
    else:
        wallet_text += "Нет транзакций\n\n"
    
    keyboard = [
        [InlineKeyboardButton("💳 Пополнить кошелек", callback_data="wallet_deposit")],
        [InlineKeyboardButton("📊 История транзакций", callback_data="wallet_history")],
        [InlineKeyboardButton("📋 Мои заказы", callback_data="my_orders")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(wallet_text, reply_markup=reply_markup, parse_mode='Markdown')

async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /orders - показать заказы пользователя"""
    if not update.message:
        return
        
    user = update.effective_user
    
    # Проверка rate limit
    if not check_rate_limit(user.id):
        await update.message.reply_text("⚠️ Слишком много сообщений. Подождите немного.")
        return
    
    # Получаем заказы пользователя
    orders = get_user_orders(user.id, limit=10)
    
    if not orders:
        await update.message.reply_text("📋 У вас пока нет заказов.")
        return
    
    orders_text = f"""
📋 **Ваши заказы:**

"""
    
    for order in orders:
        status_emoji = {
            'pending': '⏳',
            'paid': '💰',
            'processing': '⚙️',
            'completed': '✅',
            'cancelled': '❌',
            'failed': '💥'
        }.get(order['status'], '❓')
        
        status_text = ORDER_STATUSES.get(order['status'], order['status'])
        created_date = order['created_at'][:19] if order['created_at'] else "Неизвестно"
        
        orders_text += f"""
{status_emoji} **Заказ #{order['id']}**
• Услуга: {order['service_name']}
• Сумма: ${order['amount']:.2f}
• Статус: {status_text}
• Дата: {created_date}
"""
    
    keyboard = [
        [InlineKeyboardButton("💰 Мой кошелек", callback_data="wallet_info")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(orders_text, reply_markup=reply_markup, parse_mode='Markdown')

async def admin_orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /admin_orders - показать все заказы (только для админов)"""
    if not update.message:
        return
        
    user = update.effective_user
    
    # Проверка прав администратора
    if user.id not in [ADMIN_ID, ADMIN_ID_2]:
        await update.message.reply_text("❌ У вас нет прав для выполнения этой команды.")
        return
    
    # Проверка rate limit
    if not check_rate_limit(user.id):
        await update.message.reply_text("⚠️ Слишком много сообщений. Подождите немного.")
        return
    
    # Получаем все заказы
    orders = get_all_orders(limit=20)
    
    if not orders:
        await update.message.reply_text("📋 Заказов пока нет.")
        return
    
    orders_text = f"""
📋 **Все заказы (последние 20):**

"""
    
    for order in orders:
        status_emoji = {
            'pending': '⏳',
            'paid': '💰',
            'processing': '⚙️',
            'completed': '✅',
            'cancelled': '❌',
            'failed': '💥'
        }.get(order['status'], '❓')
        
        status_text = ORDER_STATUSES.get(order['status'], order['status'])
        created_date = order['created_at'][:19] if order['created_at'] else "Неизвестно"
        username = order['username'] or order['first_name'] or f"ID: {order['user_id']}"
        
        orders_text += f"""
{status_emoji} **Заказ #{order['id']}**
• Пользователь: {username}
• Услуга: {order['service_name']}
• Сумма: ${order['amount']:.2f}
• Статус: {status_text}
• Дата: {created_date}
"""
    
    keyboard = [
        [InlineKeyboardButton("⏳ Ожидающие оплаты", callback_data="admin_orders_pending")],
        [InlineKeyboardButton("⚙️ В обработке", callback_data="admin_orders_processing")],
        [InlineKeyboardButton("✅ Завершенные", callback_data="admin_orders_completed")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(orders_text, reply_markup=reply_markup, parse_mode='Markdown')

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на кнопки"""
    query = update.callback_query
    if not query:
        return
        
    await query.answer()
    
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # Проверка rate limit
    if not check_rate_limit(user.id):
        await query.edit_message_text("⚠️ Слишком много запросов. Подождите немного.")
        return
    
    if query.data == "payment_cards":
        # Устанавливаем состояние для выбора сервиса
        set_user_state(user.id, {
            'state': 'selecting_service',
            'service_type': 'cards',
            'step': 'service_selection'
        })
        
        text = """
💳 **Оплата зарубежными покупками:**

Выберите сервис для оплаты:

• X (бывший Twitter) Premium / GPT
• Netflix Premium
• Steam Gift Cards
• Spotify Premium
• YouTube Premium
• Discord Nitro
• Apple Music / Google Play

💡 Как это работает:
1) Выберите сервис
2) Укажите сумму
3) Получите крипто‑реквизиты
4) После поступления средств выдадим карту/оплатим товар
"""
        keyboard = [
            [InlineKeyboardButton("🧠 GPT / X Premium", callback_data="service_gptx")],
            [InlineKeyboardButton("🎬 Netflix", callback_data="service_netflix")],
            [InlineKeyboardButton("🎮 Steam", callback_data="service_steam")],
            [InlineKeyboardButton("🎵 Spotify", callback_data="service_spotify")],
            [InlineKeyboardButton("📺 YouTube", callback_data="service_youtube")],
            [InlineKeyboardButton("🕹 Discord Nitro", callback_data="service_discord")],
            [InlineKeyboardButton("📱 Apple Music", callback_data="service_apple_music")],
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='Markdown')
        
    elif query.data == "transfers":
        # Устанавливаем состояние для переводов
        set_user_state(user.id, {
            'state': 'selecting_transfer_type',
            'service_type': 'transfers',
            'step': 'transfer_type_selection'
        })
        
        text = """
💸 **Переводы на зарубежные карты:**

Выберите тип перевода:

**🌍 Географические зоны:**
• Европейские карты (СЕПА)
• Американские карты
• Другие страны

**💡 Как это работает:**
1. Выберите тип перевода
2. Укажите сумму и детали
3. Получите реквизиты для оплаты
4. После оплаты получите карту получателя

🔒 **Безопасность:**
• Шифрованная передача данных
• Проверка получателя
• Отслеживание транзакций
"""
        keyboard = [
            [InlineKeyboardButton("🇪🇺 Европейские карты", callback_data="transfer_eu")],
            [InlineKeyboardButton("🇺🇸 Американские карты", callback_data="transfer_us")],
            [InlineKeyboardButton("🌍 Другие страны", callback_data="transfer_other")],
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='Markdown')
        
    elif query.data == "crypto":
        # Приём оплаты в криптовалюте (без переводов)
        set_user_state(user.id, {
            'state': 'crypto_info',
            'service_type': 'crypto',
            'step': 'show_addresses'
        })
        
        payment_addresses = get_payment_address('crypto')
        text = f"""
₿ **Оплата криптовалютой:**

Мы принимаем оплату в:
• BTC
• ETH
• USDT (TRC20)

**Реквизиты:**
BTC: `{payment_addresses['BTC']}`
ETH: `{payment_addresses['ETH']}`
USDT (TRC20): `{payment_addresses['USDT_TRC20']}`

После оплаты укажите хеш транзакции и услугу (например: GPT/X Premium).
Комиссия: 8% (для сумм < $30 — фикс $3).
"""
        keyboard = [
            [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='Markdown')
        
    elif query.data == "contact_operator":
        text = f"""
📞 **Связаться с оператором:**

Для оформления заказа или получения консультации:

**Основной оператор:** @swiwell
**Техническая поддержка:** @Deadkid

⚠️ **Важно:** При обращении указывайте:
• Ваш Telegram ID: `{user.id}`
• Выбранную услугу
• Сумму операции
• Дополнительные детали

Время ответа: 5-15 минут

🕐 **Время работы:** Круглосуточно
"""
        keyboard = [
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_menu")],
            [InlineKeyboardButton("💰 Прайс-лист", callback_data="price_list")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='Markdown')
        
        # Уведомление администратора о запросе связи
        await send_admin_notification_to_all(
            context,
            "📞 **Запрос на связь!**",
            user
        )
        
    elif query.data == "price_list":
        # Редактируем текущее сообщение с прайс-листом
        price_text = """
💰 **Прайс-лист услуг:**

**💳 Оплата зарубежными картами:**
• Netflix: $15-50 (комиссия 10%)
• Steam: $10-100 (комиссия 8%)
• Discord Nitro: $10-20 (комиссия 12%)
• Spotify: $10-15 (комиссия 15%)
• YouTube Premium: $12-18 (комиссия 13%)
• Twitch Subscriptions: $5-25 (комиссия 11%)

**💸 Переводы на карты:**
• Европейские карты: 5-8% комиссия
• Американские карты: 8-12% комиссия
• Минимальная сумма: $10

**₿ Криптовалюты:**
• BTC: 3% комиссия
• ETH: 4% комиссия
• USDT: 2% комиссия

**⏱️ Время обработки:** 10-30 минут

**💳 Способы оплаты:**
• Банковские карты
• Криптовалюты
• Электронные кошельки
"""
        keyboard = [
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=price_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    elif query.data == "back_to_menu":
        # Редактируем текущее сообщение с меню
        keyboard = [
            [InlineKeyboardButton("💳 Оплата картами", callback_data="payment_cards")],
            [InlineKeyboardButton("💸 Переводы", callback_data="transfers")],
            [InlineKeyboardButton("₿ Оплата криптовалютой", callback_data="crypto")],
            [InlineKeyboardButton("💰 Мой кошелек", callback_data="wallet_info")],
            [InlineKeyboardButton("📋 Мои заказы", callback_data="my_orders")],
            [InlineKeyboardButton("📞 Связаться с оператором", callback_data="contact_operator")],
            [InlineKeyboardButton("💰 Прайс-лист", callback_data="price_list")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text="Выберите категорию услуг:",
            reply_markup=reply_markup
        )
        
    # Обработчики выбора сервисов для карт
    elif query.data.startswith("service_"):
        service_name = query.data.replace("service_", "")
        current_state = get_user_state(user.id)
        current_state['selected_service'] = service_name
        current_state['state'] = 'entering_amount'
        current_state['step'] = 'amount_input'
        set_user_state(user.id, current_state)
        
        service_names = {
            'gptx': 'GPT / X Premium',
            'netflix': 'Netflix Premium',
            'steam': 'Steam Gift Cards',
            'discord': 'Discord Nitro',
            'spotify': 'Spotify Premium',
            'youtube': 'YouTube Premium',
            'apple_music': 'Apple Music'
        }
        
        service_display_name = service_names.get(service_name, service_name.title())
        commission_rate = 0.08
        
        text = f"""
💳 **{service_display_name}**

Выбранный сервис: **{service_display_name}**
Комиссия: **8%** (для сумм < $30 — фикс $3)

💰 **Введите сумму в долларах:**
(Минимальная сумма: ${MIN_AMOUNTS['cards']})

Пример: `15` или `25.50`

⚠️ **Важно:** Указывайте только числовое значение
"""
        keyboard = [
            [InlineKeyboardButton("🔙 Назад к выбору сервиса", callback_data="payment_cards")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='Markdown')
        
    # Обработчики выбора типов переводов
    elif query.data.startswith("transfer_"):
        transfer_type = query.data.replace("transfer_", "")
        current_state = get_user_state(user.id)
        current_state['selected_transfer_type'] = transfer_type
        current_state['state'] = 'entering_transfer_details'
        current_state['step'] = 'transfer_details_input'
        set_user_state(user.id, current_state)
        
        transfer_names = {
            'eu': 'Европейские карты (СЕПА)',
            'us': 'Американские карты',
            'other': 'Другие страны'
        }
        
        transfer_display_name = transfer_names.get(transfer_type, transfer_type.upper())
        commission_rate = COMMISSION_RATES.get(f'transfer_{transfer_type}', 0.10)
        
        text = f"""
💸 **{transfer_display_name}**

Выбранный тип: **{transfer_display_name}**
Комиссия: **{commission_rate * 100}%**

💰 **Введите сумму в долларах:**
(Минимальная сумма: ${MIN_AMOUNTS['transfers']})

Пример: `50` или `100.25`

⚠️ **Важно:** Указывайте только числовое значение
"""
        keyboard = [
            [InlineKeyboardButton("🔙 Назад к выбору типа", callback_data="transfers")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='Markdown')
        
    # Обработчики выбора криптовалют
    elif query.data.startswith("crypto_"):
        crypto_type = query.data.replace("crypto_", "")
        current_state = get_user_state(user.id)
        current_state['selected_crypto'] = crypto_type
        current_state['state'] = 'entering_crypto_amount'
        current_state['step'] = 'crypto_amount_input'
        set_user_state(user.id, current_state)
        
        crypto_names = {
            'btc': 'Bitcoin (BTC)',
            'eth': 'Ethereum (ETH)',
            'usdt': 'USDT (TRC20/ERC20)'
        }
        
        crypto_display_name = crypto_names.get(crypto_type, crypto_type.upper())
        commission_rate = COMMISSION_RATES.get(f'crypto_{crypto_type}', 0.08)
        
        text = f"""
₿ **{crypto_display_name}**

Выбранная криптовалюта: **{crypto_display_name}**
Комиссия: **{commission_rate * 100}%**

💰 **Введите сумму в долларах:**
(Минимальная сумма: ${MIN_AMOUNTS['crypto']})

Пример: `100` или `250.75`

⚠️ **Важно:** Указывайте только числовое значение
"""
        keyboard = [
            [InlineKeyboardButton("🔙 Назад к выбору криптовалюты", callback_data="crypto")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='Markdown')
        
    # Обработчики кошелька
    elif query.data == "wallet_deposit":
        set_user_state(user.id, {
            'state': 'wallet_deposit',
            'step': 'enter_deposit_amount'
        })
        
        text = """
💳 **Пополнение кошелька:**

💰 **Введите сумму для пополнения в долларах:**
(Минимальная сумма: $5)

Пример: `50` или `100.25`

⚠️ **Важно:** Указывайте только числовое значение
"""
        keyboard = [
            [InlineKeyboardButton("🔙 Назад к кошельку", callback_data="wallet_info")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='Markdown')
        
    elif query.data == "wallet_history":
        # Получаем историю транзакций
        transactions = get_wallet_transactions(user.id, limit=15)
        
        if not transactions:
            text = "📊 **История транзакций:**\n\nНет транзакций"
        else:
            text = "📊 **История транзакций:**\n\n"
            for trans in transactions:
                trans_type = WALLET_TRANSACTION_TYPES.get(trans['transaction_type'], trans['transaction_type'])
                amount_sign = "+" if trans['transaction_type'] in ['deposit', 'refund'] else "-"
                text += f"• {trans_type}: {amount_sign}${trans['amount']:.2f}\n"
                text += f"  📝 {trans['description']}\n"
                text += f"  📅 {trans['created_at'][:19]}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("💰 Мой кошелек", callback_data="wallet_info")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='Markdown')
        
    elif query.data == "wallet_info":
        # Показываем информацию о кошельке
        wallet = get_or_create_wallet(user.id, user.username, user.first_name)
        transactions = get_wallet_transactions(user.id, limit=5)
        
        if not wallet:
            await query.edit_message_text("❌ Ошибка получения кошелька.")
            return
        
        wallet_text = f"""
💰 **Ваш кошелек:**

**💳 Баланс:** ${wallet['balance']:.2f}

**📊 Последние транзакции:**
"""
        
        if transactions:
            for trans in transactions:
                trans_type = WALLET_TRANSACTION_TYPES.get(trans['transaction_type'], trans['transaction_type'])
                amount_sign = "+" if trans['transaction_type'] in ['deposit', 'refund'] else "-"
                wallet_text += f"• {trans_type}: {amount_sign}${trans['amount']:.2f}\n"
                wallet_text += f"  📝 {trans['description']}\n"
                wallet_text += f"  📅 {trans['created_at'][:19]}\n\n"
        else:
            wallet_text += "Нет транзакций\n\n"
        
        keyboard = [
            [InlineKeyboardButton("💳 Пополнить кошелек", callback_data="wallet_deposit")],
            [InlineKeyboardButton("📊 История транзакций", callback_data="wallet_history")],
            [InlineKeyboardButton("📋 Мои заказы", callback_data="my_orders")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=wallet_text, reply_markup=reply_markup, parse_mode='Markdown')
        
    elif query.data == "my_orders":
        # Показываем заказы пользователя
        orders = get_user_orders(user.id, limit=10)
        
        if not orders:
            text = "📋 **Ваши заказы:**\n\nУ вас пока нет заказов."
        else:
            text = "📋 **Ваши заказы:**\n\n"
            for order in orders:
                status_emoji = {
                    'pending': '⏳',
                    'paid': '💰',
                    'processing': '⚙️',
                    'completed': '✅',
                    'cancelled': '❌',
                    'failed': '💥'
                }.get(order['status'], '❓')
                
                status_text = ORDER_STATUSES.get(order['status'], order['status'])
                created_date = order['created_at'][:19] if order['created_at'] else "Неизвестно"
                
                text += f"""
{status_emoji} **Заказ #{order['id']}**
• Услуга: {order['service_name']}
• Сумма: ${order['amount']:.2f}
• Статус: {status_text}
• Дата: {created_date}
"""
        
        keyboard = [
            [InlineKeyboardButton("💰 Мой кошелек", callback_data="wallet_info")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='Markdown')
        
    # Обработчики администратора
    elif query.data.startswith("admin_orders_"):
        if user.id not in [ADMIN_ID, ADMIN_ID_2]:
            await query.edit_message_text("❌ У вас нет прав для выполнения этой команды.")
            return
            
        status_filter = query.data.replace("admin_orders_", "")
        orders = get_all_orders(limit=20, status=status_filter)
        
        if not orders:
            text = f"📋 Заказов со статусом '{status_filter}' нет."
        else:
            text = f"📋 **Заказы со статусом '{status_filter}' (последние 20):**\n\n"
            for order in orders:
                status_emoji = {
                    'pending': '⏳',
                    'paid': '💰',
                    'processing': '⚙️',
                    'completed': '✅',
                    'cancelled': '❌',
                    'failed': '💥'
                }.get(order['status'], '❓')
                
                status_text = ORDER_STATUSES.get(order['status'], order['status'])
                created_date = order['created_at'][:19] if order['created_at'] else "Неизвестно"
                username = order['username'] or order['first_name'] or f"ID: {order['user_id']}"
                
                text += f"""
{status_emoji} **Заказ #{order['id']}**
• Пользователь: {username}
• Услуга: {order['service_name']}
• Сумма: ${order['amount']:.2f}
• Статус: {status_text}
• Дата: {created_date}
"""
        
        keyboard = [
            [InlineKeyboardButton("⏳ Ожидающие оплаты", callback_data="admin_orders_pending")],
            [InlineKeyboardButton("⚙️ В обработке", callback_data="admin_orders_processing")],
            [InlineKeyboardButton("✅ Завершенные", callback_data="admin_orders_completed")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    if not update.message:
        return
        
    user = update.effective_user
    chat_id = update.effective_chat.id
    message_text = sanitize_text(update.message.text)
    
    # Проверка rate limit
    if not check_rate_limit(user.id):
        await update.message.reply_text("⚠️ Слишком много сообщений. Подождите немного.")
        return
    
    # Получаем текущее состояние пользователя
    current_state = get_user_state(user.id)
    
    # Обработка состояний заказа
    if current_state.get('state') == 'entering_amount':
        await handle_amount_input(update, context, message_text, current_state)
        return
    elif current_state.get('state') == 'entering_transfer_details':
        await handle_transfer_amount_input(update, context, message_text, current_state)
        return
    elif current_state.get('state') == 'entering_crypto_amount':
        await handle_crypto_amount_input(update, context, message_text, current_state)
        return
    elif current_state.get('state') == 'wallet_deposit':
        await handle_wallet_deposit(update, context, message_text, current_state)
        return
    
    # Пересылка сообщения администратору
    if ADMIN_ID and chat_id != ADMIN_ID:
        try:
            safe_text = escape_markdown(message_text)
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"💬 **Сообщение от пользователя:**\n\n"
                     f"👤 {user.first_name} (@{user.username or 'без username'})\n"
                     f"🆔 ID: `{user.id}`\n"
                     f"📝 Текст: {safe_text}\n"
                     f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Ошибка пересылки сообщения администратору: {e}")
    
    # Автоматические ответы на частые вопросы
    if any(word in message_text.lower() for word in ['привет', 'hello', 'hi', 'здравствуйте']):
        await update.message.reply_text("Привет! 👋 Используйте /menu для выбора услуг.")
    elif any(word in message_text.lower() for word in ['цена', 'стоимость', 'price', 'прайс']):
        await price_command(update, context)
    elif any(word in message_text.lower() for word in ['помощь', 'help', 'справка']):
        await help_command(update, context)
    elif any(word in message_text.lower() for word in ['реквизиты', 'адрес', 'address']):
        await address_command(update, context)
    elif any(word in message_text.lower() for word in ['статус', 'status']):
        await update.message.reply_text("✅ Бот работает нормально. Время работы: " + get_uptime())
    else:
        await update.message.reply_text(
            "Для получения помощи используйте /help или свяжитесь с оператором @swiwell"
        )

async def handle_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str, current_state: dict):
    """Обработка ввода суммы для карт"""
    user = update.effective_user
    
    try:
        amount = float(message_text)
        if amount < MIN_AMOUNTS['cards']:
            await update.message.reply_text(f"⚠️ Минимальная сумма: ${MIN_AMOUNTS['cards']}")
            return
            
        selected_service = current_state.get('selected_service')
        service_names = {
            'netflix': 'Netflix Premium',
            'steam': 'Steam Gift Cards',
            'discord': 'Discord Nitro',
            'spotify': 'Spotify Premium',
            'youtube': 'YouTube Premium',
            'apple_music': 'Apple Music'
        }
        service_display_name = service_names.get(selected_service, selected_service.title())
        
        # Рассчитываем комиссию
        calculation = calculate_commission(selected_service, amount)
        
        commission_line = (f"Комиссия (фиксированная $3.00): ${calculation['commission']:.2f}" if calculation['is_fixed']
                           else f"Комиссия ({calculation['commission_rate']*100}%): ${calculation['commission']:.2f}")
        
        # Обновляем состояние
        current_state['amount'] = amount
        current_state['calculation'] = calculation
        current_state['state'] = 'payment_ready'
        set_user_state(user.id, current_state)
        
        # Получаем адреса для оплаты
        payment_addresses = get_payment_address('cards')
        
        text = f"""
💳 **Заказ готов к оплате!**

**📋 Детали заказа:**
• Сервис: {service_display_name}
• Сумма: ${amount:.2f}
• {commission_line}
• **Итого к оплате: ${calculation['total_amount']:.2f}**

**💳 Реквизиты для оплаты:**
"""
        
        keyboard = [
            [InlineKeyboardButton("📞 Связаться с оператором", callback_data="contact_operator")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        
        # Создаем заказ в базе данных
        order_id = create_order(
            user_id=user.id,
            order_type='cards',
            service_name=service_display_name,
            amount=amount,
            commission=calculation['commission'],
            total_amount=calculation['total_amount'],
            payment_method='external'
        )
        
        # Уведомляем администратора
        await send_admin_notification_to_all(
            context,
            f"💳 **Новый заказ карты!**",
            user,
            f"Заказ #{order_id}\n"
            f"Сервис: {service_display_name}\n"
            f"Сумма: ${amount:.2f}\n"
            f"Комиссия: ${calculation['commission']:.2f}\n"
            f"Итого: ${calculation['total_amount']:.2f}"
        )
        
    except ValueError:
        await update.message.reply_text("⚠️ Пожалуйста, введите корректную сумму (например: 15 или 25.50)")

async def handle_transfer_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str, current_state: dict):
    """Обработка ввода суммы для переводов"""
    user = update.effective_user
    
    try:
        amount = float(message_text)
        if amount < MIN_AMOUNTS['transfers']:
            await update.message.reply_text(f"⚠️ Минимальная сумма: ${MIN_AMOUNTS['transfers']}")
            return
            
        selected_transfer_type = current_state.get('selected_transfer_type')
        transfer_names = {
            'eu': 'Европейские карты (СЕПА)',
            'us': 'Американские карты',
            'other': 'Другие страны'
        }
        transfer_display_name = transfer_names.get(selected_transfer_type, selected_transfer_type.upper())
        
        # Рассчитываем комиссию
        calculation = calculate_commission(f'transfer_{selected_transfer_type}', amount)
        
        commission_line = (f"Комиссия (фиксированная $3.00): ${calculation['commission']:.2f}" if calculation['is_fixed']
                           else f"Комиссия ({calculation['commission_rate']*100}%): ${calculation['commission']:.2f}")
        
        # Обновляем состояние
        current_state['amount'] = amount
        current_state['calculation'] = calculation
        current_state['state'] = 'transfer_payment_ready'
        set_user_state(user.id, current_state)
        
        # Получаем адреса для оплаты
        payment_addresses = get_payment_address('transfers')
        
        text = f"""
💸 **Заказ перевода готов к оплате!**

**📋 Детали заказа:**
• Тип: {transfer_display_name}
• Сумма: ${amount:.2f}
• {commission_line}
• **Итого к оплате: ${calculation['total_amount']:.2f}**

**💳 Реквизиты для оплаты:**
"""
        
        keyboard = [
            [InlineKeyboardButton("📞 Связаться с оператором", callback_data="contact_operator")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        
        # Создаем заказ в базе данных
        order_id = create_order(
            user_id=user.id,
            order_type='transfers',
            service_name=transfer_display_name,
            amount=amount,
            commission=calculation['commission'],
            total_amount=calculation['total_amount'],
            payment_method='external'
        )
        
        # Уведомляем администратора
        await send_admin_notification_to_all(
            context,
            f"💸 **Новый заказ перевода!**",
            user,
            f"Заказ #{order_id}\n"
            f"Тип: {transfer_display_name}\n"
            f"Сумма: ${amount:.2f}\n"
            f"Комиссия: ${calculation['commission']:.2f}\n"
            f"Итого: ${calculation['total_amount']:.2f}"
        )
        
    except ValueError:
        await update.message.reply_text("⚠️ Пожалуйста, введите корректную сумму (например: 50 или 100.25)")

async def handle_crypto_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str, current_state: dict):
    """Обработка ввода суммы для криптовалют"""
    user = update.effective_user
    
    try:
        amount = float(message_text)
        if amount < MIN_AMOUNTS['crypto']:
            await update.message.reply_text(f"⚠️ Минимальная сумма: ${MIN_AMOUNTS['crypto']}")
            return
            
        selected_crypto = current_state.get('selected_crypto')
        crypto_names = {
            'btc': 'Bitcoin (BTC)',
            'eth': 'Ethereum (ETH)',
            'usdt': 'USDT (TRC20/ERC20)'
        }
        crypto_display_name = crypto_names.get(selected_crypto, selected_crypto.upper())
        
        # Рассчитываем комиссию
        calculation = calculate_commission(f'crypto_{selected_crypto}', amount)
        
        commission_line = (f"Комиссия (фиксированная $3.00): ${calculation['commission']:.2f}" if calculation['is_fixed']
                           else f"Комиссия ({calculation['commission_rate']*100}%): ${calculation['commission']:.2f}")
        
        # Обновляем состояние
        current_state['amount'] = amount
        current_state['calculation'] = calculation
        current_state['state'] = 'crypto_payment_ready'
        set_user_state(user.id, current_state)
        
        # Получаем адреса для оплаты
        payment_addresses = get_payment_address('crypto')
        
        text = f"""
₿ **Заказ криптовалюты готов к оплате!**

**📋 Детали заказа:**
• Криптовалюта: {crypto_display_name}
• Сумма: ${amount:.2f}
• {commission_line}
• **Итого к оплате: ${calculation['total_amount']:.2f}**

**💳 Реквизиты для оплаты:**
"""
        
        keyboard = [
            [InlineKeyboardButton("📞 Связаться с оператором", callback_data="contact_operator")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        
        # Создаем заказ в базе данных
        order_id = create_order(
            user_id=user.id,
            order_type='crypto',
            service_name=crypto_display_name,
            amount=amount,
            commission=calculation['commission'],
            total_amount=calculation['total_amount'],
            payment_method='external'
        )
        
        # Уведомляем администратора
        await send_admin_notification_to_all(
            context,
            f"₿ **Новый заказ криптовалюты!**",
            user,
            f"Заказ #{order_id}\n"
            f"Криптовалюта: {crypto_display_name}\n"
            f"Сумма: ${amount:.2f}\n"
            f"Комиссия: ${calculation['commission']:.2f}\n"
            f"Итого: ${calculation['total_amount']:.2f}"
        )
        
    except ValueError:
        await update.message.reply_text("⚠️ Пожалуйста, введите корректную сумму (например: 100 или 250.75)")

async def handle_wallet_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str, current_state: dict):
    """Обработка пополнения кошелька"""
    user = update.effective_user
    
    try:
        amount = float(message_text)
        if amount < 5:
            await update.message.reply_text("⚠️ Минимальная сумма пополнения: $5")
            return
        
        # Получаем кошелек пользователя
        wallet = get_or_create_wallet(user.id, user.username, user.first_name)
        if not wallet:
            await update.message.reply_text("❌ Ошибка получения кошелька. Попробуйте позже.")
            return
        
        # Получаем адреса для оплаты
        payment_addresses = get_payment_address('cards')
        
        text = f"""
💳 **Пополнение кошелька на ${amount:.2f}**

**📋 Детали:**
• Сумма пополнения: ${amount:.2f}
• После оплаты средства поступят на ваш баланс

**💳 Реквизиты для оплаты:**
BTC: `{payment_addresses['BTC']}`
ETH: `{payment_addresses['ETH']}`
USDT (TRC20): `{payment_addresses['USDT_TRC20']}`

⚠️ **Важно:** Указывайте комментарий к платежу с вашим Telegram ID: `{user.id}`

После оплаты свяжитесь с оператором для подтверждения.
"""
        
        keyboard = [
            [InlineKeyboardButton("📞 Связаться с оператором", callback_data="contact_operator")],
            [InlineKeyboardButton("💰 Мой кошелек", callback_data="wallet_info")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Очищаем состояние
        clear_user_state(user.id)
        
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        
        # Уведомляем администратора
        await send_admin_notification_to_all(
            context,
            f"💳 **Запрос на пополнение кошелька!**",
            user,
            f"Сумма: ${amount:.2f}\n"
            f"Пользователь: {user.first_name} (@{user.username or 'без username'})\n"
            f"ID: {user.id}"
        )
        
    except ValueError:
        await update.message.reply_text("⚠️ Пожалуйста, введите корректную сумму (например: 50 или 100.25)")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ошибок"""
    error = context.error
    
    # Обработка специфических ошибок
    if isinstance(error, Conflict):
        logger.warning("Обнаружен конфликт экземпляров бота. Перезапуск...")
        # Не отправляем уведомление администратору для конфликтов
        return
    elif isinstance(error, (NetworkError, TimedOut)):
        logger.warning(f"Сетевая ошибка: {error}")
        # Не отправляем уведомление для временных сетевых ошибок
        return
    else:
        logger.error(f"Ошибка при обработке обновления: {error}")
    
    # Уведомление администратора об ошибке (только для критических)
    if (ADMIN_ID or ADMIN_ID_2) and not isinstance(error, (Conflict, NetworkError, TimedOut)):
        try:
            error_text = str(error)[:100]  # Ограничиваем длину ошибки
            safe_error = escape_markdown(error_text)
            error_message = f"❌ **Ошибка в боте:**\n\n🔍 Детали: {safe_error}\n📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
            
            if ADMIN_ID:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=error_message,
                    parse_mode='Markdown'
                )
            if ADMIN_ID_2:
                await context.bot.send_message(
                    chat_id=ADMIN_ID_2,
                    text=error_message,
                    parse_mode='Markdown'
                )
        except Exception as e:
            logger.error(f"Ошибка уведомления администратора об ошибке: {e}")

def signal_handler(signum, frame):
    """Обработчик сигналов для graceful shutdown"""
    global shutdown_flag
    logger.info("Получен сигнал завершения. Завершение работы...")
    shutdown_flag = True
    sys.exit(0)

def main():
    """Основная функция"""
    global shutdown_flag
    
    # Регистрация обработчиков сигналов
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не установлен!")
        return
    
    if not ADMIN_ID and not ADMIN_ID_2:
        logger.warning("ADMIN_ID и ADMIN_ID_2 не установлены! Уведомления администратора отключены.")
    
    # Инициализация базы данных
    init_database()

    # Запуск Flask сервера в отдельном потоке
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()
    
    max_retries = 10  # Увеличиваем количество попыток
    retry_count = 0
    
    while retry_count < max_retries and not shutdown_flag:
        try:
            # Создание приложения с обработкой ошибок
            application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
            
            # Добавление обработчиков
            application.add_handler(CommandHandler("start", start_command))
            application.add_handler(CommandHandler("menu", menu_command))
            application.add_handler(CommandHandler("help", help_command))
            application.add_handler(CommandHandler("address", address_command))
            application.add_handler(CommandHandler("price", price_command))
            application.add_handler(CommandHandler("wallet", wallet_command))
            application.add_handler(CommandHandler("orders", orders_command))
            application.add_handler(CommandHandler("admin_orders", admin_orders_command))
            application.add_handler(CallbackQueryHandler(button_callback))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
            
            # Добавление обработчика ошибок
            application.add_error_handler(error_handler)
            
            # Запуск бота с улучшенными параметрами
            logger.info(f"Запуск финального бота (попытка {retry_count + 1}/{max_retries})...")
            try:
                # Попробуем новый способ запуска
                application.run_polling(
                    allowed_updates=Update.ALL_TYPES, 
                    drop_pending_updates=True
                )
            except TypeError as e:
                # Если не поддерживаются новые параметры, используем старый способ
                logger.info("Используем совместимый режим запуска...")
                application.run_polling()
            
        except Conflict as e:
            logger.warning(f"Конфликт экземпляров бота: {e}")
            retry_count += 1
            if retry_count < max_retries:
                wait_time = min(30, retry_count * 10)  # Увеличиваем время ожидания
                logger.info(f"Ожидание {wait_time} секунд перед повторной попыткой...")
                time.sleep(wait_time)
            continue
            
        except Exception as e:
            logger.error(f"Критическая ошибка при запуске бота: {e}")
            retry_count += 1
            if retry_count < max_retries:
                wait_time = min(60, retry_count * 15)  # Увеличиваем время ожидания
                logger.info(f"Ожидание {wait_time} секунд перед повторной попыткой...")
                time.sleep(wait_time)
            else:
                logger.error("Превышено максимальное количество попыток. Завершение работы.")
                break
    
    if shutdown_flag:
        logger.info("Бот завершен по запросу.")
    else:
        logger.error("Бот завершен из-за критических ошибок.")

if __name__ == '__main__':
    main()
