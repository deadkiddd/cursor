import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from flask import Flask, request, jsonify
import threading
import time
from datetime import datetime

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

# Flask приложение для проверки состояния
app = Flask(__name__)

@app.route('/')
def health_check():
    return jsonify({
        "status": "healthy", 
        "bot": "running",
        "timestamp": datetime.now().isoformat()
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    return jsonify({"status": "ok"})

def start_flask():
    app.run(host='0.0.0.0', port=PORT)

# Обработчики бота
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start"""
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # Уведомление администратора о новом пользователе
    if ADMIN_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🆕 **Новый пользователь зарегистрирован!**\n\n"
                     f"👤 Имя: {user.first_name} {user.last_name or ''}\n"
                     f"🔗 Username: @{user.username or 'без username'}\n"
                     f"🆔 ID: `{user.id}`\n"
                     f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления администратора: {e}")
    
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
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def address_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /address"""
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
""".format(user_id=update.effective_user.id)
    
    await update.message.reply_text(address_text, parse_mode='Markdown')

async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /price"""
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
"""
    await update.message.reply_text(price_text, parse_mode='Markdown')

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    chat_id = update.effective_chat.id
    
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
"""
        keyboard = [
            [InlineKeyboardButton("📞 Связаться с оператором", callback_data="contact_operator")],
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='Markdown')
        
        # Уведомление администратора о заявке
        if ADMIN_ID:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"💳 **Заявка на оплату картами!**\n\n"
                         f"👤 Пользователь: {user.first_name} (@{user.username or 'без username'})\n"
                         f"🆔 ID: `{user.id}`\n"
                         f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления администратора: {e}")
        
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
"""
        keyboard = [
            [InlineKeyboardButton("📞 Связаться с оператором", callback_data="contact_operator")],
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='Markdown')
        
        # Уведомление администратора о заявке на перевод
        if ADMIN_ID:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"💸 **Заявка на перевод!**\n\n"
                         f"👤 Пользователь: {user.first_name} (@{user.username or 'без username'})\n"
                         f"🆔 ID: `{user.id}`\n"
                         f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления администратора: {e}")
        
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
""".format(user_id=user.id)
        keyboard = [
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_menu")],
            [InlineKeyboardButton("💰 Прайс-лист", callback_data="price_list")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='Markdown')
        
        # Уведомление администратора о запросе связи
        if ADMIN_ID:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"📞 **Запрос на связь!**\n\n"
                         f"👤 Пользователь: {user.first_name} (@{user.username or 'без username'})\n"
                         f"🆔 ID: `{user.id}`\n"
                         f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления администратора: {e}")
        
    elif query.data == "price_list":
        await price_command(update, context)
        
    elif query.data == "back_to_menu":
        await menu_command(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    user = update.effective_user
    chat_id = update.effective_chat.id
    message_text = update.message.text
    
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
    
    # Создание приложения
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
    
    # Запуск бота
    logger.info("Запуск бота...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
