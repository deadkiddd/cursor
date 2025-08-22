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

**Как пользоваться:**
1. Выберите нужную услугу из меню
2. Укажите сумму и детали
3. Получите реквизиты для оплаты
4. После оплаты свяжитесь с оператором

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
        
        # Уведомляем администратора
        await send_admin_notification_to_all(
            context,
            f"💳 **Новый заказ карты!**",
            user,
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
        
        # Уведомляем администратора
        await send_admin_notification_to_all(
            context,
            f"💸 **Новый заказ перевода!**",
            user,
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
        
        # Уведомляем администратора
        await send_admin_notification_to_all(
            context,
            f"₿ **Новый заказ криптовалюты!**",
            user,
            f"Криптовалюта: {crypto_display_name}\n"
            f"Сумма: ${amount:.2f}\n"
            f"Комиссия: ${calculation['commission']:.2f}\n"
            f"Итого: ${calculation['total_amount']:.2f}"
        )
        
    except ValueError:
        await update.message.reply_text("⚠️ Пожалуйста, введите корректную сумму (например: 100 или 250.75)")

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
            application.add_handler(CallbackQueryHandler(button_callback))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
            
            # Добавление обработчика ошибок
            application.add_error_handler(error_handler)
            
            # Запуск бота с улучшенными параметрами
            logger.info(f"Запуск финального бота (попытка {retry_count + 1}/{max_retries})...")
            application.run_polling(
                allowed_updates=Update.ALL_TYPES, 
                drop_pending_updates=True,
                close_loop=False,
                read_timeout=30,
                write_timeout=30,
                connect_timeout=30,
                pool_timeout=30,
                timeout=30
            )
            
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
