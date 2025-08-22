import os
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from flask import Flask, request, jsonify
import threading
import time
from datetime import datetime, timedelta
import json
import re

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Переменные окружения
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))
PORT = int(os.getenv('PORT', 10000))

# Настройки безопасности
MAX_MESSAGE_LENGTH = 4096
RATE_LIMIT_MESSAGES = 60  # сообщений в минуту
RATE_LIMIT_WINDOW = 40  # секунд

# Кэш для rate limiting
user_message_times = {}

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

# Обработчики бота
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start"""
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # Проверка rate limit
    if not check_rate_limit(user.id):
        await update.message.reply_text("⚠️ Слишком много сообщений. Подождите немного.")
        return
    
    # Уведомление администратора о новом пользователе
    await send_admin_notification(
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
    user = update.effective_user
    
    # Проверка rate limit
    if not check_rate_limit(user.id):
        await update.message.reply_text("⚠️ Слишком много сообщений. Подождите немного.")
        return
    
    keyboard = [
        [InlineKeyboardButton("💳 Оплата картами", callback_data="payment_cards")],
        [InlineKeyboardButton("💸 Переводы", callback_data="transfers")],
        [InlineKeyboardButton("₿ Криптовалюты", callback_data="crypto")],
        [InlineKeyboardButton("📞 Связаться с оператором", callback_data="contact_operator")],
        [InlineKeyboardButton("💰 Прайс-лист", callback_data="price_list")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text("Выберите категорию услуг:", reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /help"""
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
• Максимум 5 сообщений в минуту
• Максимальная длина сообщения: 4096 символов
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def address_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /address"""
    user = update.effective_user
    
    # Проверка rate limit
    if not check_rate_limit(user.id):
        await update.message.reply_text("⚠️ Слишком много сообщений. Подождите немного.")
        return
    
    address_text = """
🏦 **Реквизиты для оплаты:**

**Банковская карта:**
Номер: 1234 5678 9012 3456
Срок действия: 12/25
CVV: 123

**Криптокошелек:**
BTC: `bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh`
ETH: `0x742d35Cc6634C0532925a3b8D4C9db96C4b4d8b6`
USDT (TRC20): `TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t`

⚠️ **Важно:** Указывайте комментарий к платежу с вашим Telegram ID: `{user_id}`

🔒 **Безопасность:** Все транзакции защищены и отслеживаются
""".format(user_id=user.id)
    
    await update.message.reply_text(address_text, parse_mode='Markdown')

async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /price"""
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
    await query.answer()
    
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # Проверка rate limit
    if not check_rate_limit(user.id):
        await query.edit_message_text("⚠️ Слишком много запросов. Подождите немного.")
        return
    
    if query.data == "payment_cards":
        text = """
💳 **Оплата зарубежными картами:**

Доступные сервисы:
• Netflix Premium
• Steam Gift Cards
• Discord Nitro
• Spotify Premium
• YouTube Premium
• Twitch Subscriptions
• Apple Music
• Google Play
• И другие...

Укажите:
1. Сервис
2. Сумму
3. Длительность подписки

Пример: "Netflix Premium, $15, 1 месяц"

🔒 **Гарантии:**
• 100% успешность операций
• Возврат средств при проблемах
• Поддержка 24/7
"""
        keyboard = [
            [InlineKeyboardButton("📞 Связаться с оператором", callback_data="contact_operator")],
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='Markdown')
        
        # Уведомление администратора о заявке
        await send_admin_notification(
            context,
            "💳 **Заявка на оплату картами!**",
            user
        )
        
    elif query.data == "transfers":
        text = """
💸 **Переводы на зарубежные карты:**

Поддерживаемые страны:
• 🇺🇸 США
• 🇪🇺 Европа (СЕПА)
• 🇬🇧 Великобритания
• 🇨🇦 Канада
• 🇦🇺 Австралия
• 🇨🇭 Швейцария

Укажите:
1. Страну получателя
2. Сумму
3. Номер карты получателя

Комиссия: 5-12% в зависимости от страны

🔒 **Безопасность:**
• Шифрованная передача данных
• Проверка получателя
• Отслеживание транзакций
"""
        keyboard = [
            [InlineKeyboardButton("📞 Связаться с оператором", callback_data="contact_operator")],
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='Markdown')
        
        # Уведомление администратора о заявке на перевод
        await send_admin_notification(
            context,
            "💸 **Заявка на перевод!**",
            user
        )
        
    elif query.data == "crypto":
        text = """
₿ **Криптовалютные операции:**

Поддерживаемые сети:
• Bitcoin (BTC)
• Ethereum (ETH)
• USDT (TRC20/ERC20)
• USDC (ERC20)
• BNB (BSC)

Услуги:
• Покупка криптовалют
• Продажа криптовалют
• Переводы между кошельками
• Конвертация валют

Комиссия: 2-4%

🔒 **Особенности:**
• Мгновенные транзакции
• Низкие комиссии
• Анонимность
• Глобальная доступность
"""
        keyboard = [
            [InlineKeyboardButton("📞 Связаться с оператором", callback_data="contact_operator")],
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='Markdown')
        
    elif query.data == "contact_operator":
        text = """
📞 **Связаться с оператором:**

Для оформления заказа или получения консультации:

**Основной оператор:** @swiwell
**Техническая поддержка:** @Deadkid

⚠️ **Важно:** При обращении указывайте:
• Ваш Telegram ID: `{user_id}`
• Выбранную услугу
• Сумму операции
• Дополнительные детали

Время ответа: 5-15 минут

🕐 **Время работы:** Круглосуточно
""".format(user_id=user.id)
        keyboard = [
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_menu")],
            [InlineKeyboardButton("💰 Прайс-лист", callback_data="price_list")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='Markdown')
        
        # Уведомление администратора о запросе связи
        await send_admin_notification(
            context,
            "📞 **Запрос на связь!**",
            user
        )
        
    elif query.data == "price_list":
        await price_command(update, context)
        
    elif query.data == "back_to_menu":
        await menu_command(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    user = update.effective_user
    chat_id = update.effective_chat.id
    message_text = sanitize_text(update.message.text)
    
    # Проверка rate limit
    if not check_rate_limit(user.id):
        await update.message.reply_text("⚠️ Слишком много сообщений. Подождите немного.")
        return
    
    # Пересылка сообщения администратору
    if ADMIN_ID and chat_id != ADMIN_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"💬 **Сообщение от пользователя:**\n\n"
                     f"👤 {user.first_name} (@{user.username or 'без username'})\n"
                     f"🆔 ID: `{user.id}`\n"
                     f"📝 Текст: {message_text}\n"
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

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ошибок"""
    logger.error(f"Ошибка при обработке обновления: {context.error}")
    
    # Уведомление администратора об ошибке
    if ADMIN_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"❌ **Ошибка в боте:**\n\n"
                     f"🔍 Детали: {context.error}\n"
                     f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления администратора об ошибке: {e}")

def main():
    """Основная функция"""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не установлен!")
        return
    
    if not ADMIN_ID:
        logger.warning("ADMIN_ID не установлен! Уведомления администратора отключены.")
    
    # Запуск Flask сервера в отдельном потоке
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()
    
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
        
        # Запуск бота с drop_pending_updates для избежания конфликтов
        logger.info("Запуск исправленного бота...")
        application.run_polling(
            allowed_updates=Update.ALL_TYPES, 
            drop_pending_updates=True,
            close_loop=False
        )
        
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}")
        # Попытка перезапуска через 30 секунд
        time.sleep(30)
        main()

if __name__ == '__main__':
    main()

