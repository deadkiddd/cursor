import os
import logging
import asyncio
import sys
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from flask import Flask, request, jsonify
from datetime import datetime
from crypto_checker import auto_issue_card
from database.supabase_integration import (
    _update_order_status_in_supabase, 
    get_top_wallets,
    _get_user_wallet_data,
    get_or_create_wallet,
    update_wallet_balance,
    add_money_to_wallet,
    create_order,
    get_user_orders,
    get_user_transactions,
    get_all_orders,
    get_stats,
    update_order_status,
    get_order_by_id,
    get_pending_crypto_orders
)


env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
# Загружаем переменные окружения
try:
    from dotenv import load_dotenv
    load_dotenv()
    print(f"✅ .env файл загружен успешно в {os.path.basename(__file__)}")
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
ENVIRONMENT = os.getenv('ENVIRONMENT', 'local')  # 'local' или 'production'
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')  # Для Render

bot = Bot(token=TELEGRAM_BOT_TOKEN)

print(f"TELEGRAM_BOT_TOKEN: {'✅ Установлен' if TELEGRAM_BOT_TOKEN else '❌ НЕ УСТАНОВЛЕН'}")

# Кэш для rate limiting
user_message_times = {}

# Система состояний пользователей
user_states = {}

# Глобальная переменная для крипточекера
crypto_checker = None


# Создаем Flask приложение
app = Flask(__name__)


# Функции для работы с базой данных
def get_user_wallet(user_id):
    """Получить кошелек пользователя через Supabase"""
    try:
        wallet_data = _get_user_wallet_data(user_id)
        if wallet_data:
            return float(wallet_data["balance"])
        else:
            # Создаем кошелек если его нет
            wallet = get_or_create_wallet(user_id)
            if wallet:
                return float(wallet.get("balance", 0.0))
            return 0.0
    except Exception as e:
        logger.error(f"Ошибка получения кошелька: {e}")
        return 0.0


# Основные команды бота
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    # Получаем пользователя из сообщения
    if not update.message:
        logger.error("start_command вызвана без сообщения")
        return

    user = update.effective_user
    user_id = user.id
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
        order = get_order_by_id(order_id)

        if not order:
            await update.message.reply_text(f"❌ Заказ {order_id} не найден")
            return

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
        print("add_wallet amount to be added: ", amount)

        if amount <= 0:
            await update.message.reply_text("❌ Сумма должна быть больше 0")
            return

        # Пополняем кошелек
        logger.info(f"Попытка пополнения кошелька пользователя {target_user_id} на сумму {amount}")
        success = add_money_to_wallet(target_user_id, amount, f"Ручное пополнение администратором {user_id}")

        if success:
            # Уведомляем пользователя
            try:
                await bot.send_message(
                    chat_id=target_user_id,
                    text=f"💰 **Кошелек пополнен!**\n\n💵 Сумма: {amount:.2f} USD\n\n🎉 Ваш баланс обновлен!"
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления пользователя: {e}")

            await update.message.reply_text(f"✅ Кошелек пользователя {target_user_id} пополнен на {amount:.2f} USD")
        else:
            await update.message.reply_text(f"❌ Ошибка пополнения кошелька пользователя {target_user_id}. Проверьте логи для деталей.")

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
        [InlineKeyboardButton("💳 Оплата зарубежной картой", callback_data="service_payment")],
        # заменить на переводы --
        [InlineKeyboardButton("💶 Перевод на счёт", callback_data="service_transfers")],
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
        orders = get_user_orders(user_id)

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

    if service_type == "transfers":
        await show_transfers(query)
    elif service_type == "payment":
        await show_payment_services(query)
    elif service_type == "other_services":
        await show_other_services(query)


async def show_transfers(query):
    """Показать переводы"""
    transfers_text = """
💳 Переводы

Выберите тип перевода:
"""

    keyboard = [
        [InlineKeyboardButton("🇪🇺 Переводы в EC", callback_data="order_transfer_eu")],
        [InlineKeyboardButton("🇺🇸 Переводы в США", callback_data="order_transfer_us")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_catalog")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(transfers_text, reply_markup=reply_markup)


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


async def show_payment_services(query):
    """Показать услуги оплаты зарубежной картой"""
    payment_services_text = """
💳 Оплата любых платежей иностранной картой

Выберите услугу:
"""

    keyboard = [
        [InlineKeyboardButton("🤖 ChatGPT Plus", callback_data="order_gpt")],
        [InlineKeyboardButton("🐦 X / Twitter", callback_data="order_twitter")],
        [InlineKeyboardButton("🔍 Другое", callback_data="order_other_services")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_catalog")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(payment_services_text, reply_markup=reply_markup)


async def handle_back_button(query, data):
    """Обработка кнопки назад"""
    if data == "back_main":
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
    """Фоновая проверка платежа (отключена для предотвращения постоянных запросов)"""

    try:
        logger.info(f"Заказ {order_id} создан для ручной проверки платежа, валюта: {currency}")

        # Уведомляем администратора о необходимости ручной проверки
        try:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🔍 **Новый заказ на пополнение**\n\n"
                     f"🆔 Заказ: {order_id}\n"
                     f"💰 Валюта: {currency.upper()}\n"
                     f"💵 Ожидаемая сумма: {expected_amount}\n"
                     f"👤 Пользователь: {user_id}\n\n"
                     f"⚠️ Требуется ручная проверка платежа!\n"
                     f"Используйте команду: /check_payment {order_id}"
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления администратора: {e}")

        logger.info(f"Заказ {order_id} ожидает ручной проверки администратором")

    except Exception as e:
        logger.error(f"Ошибка обработки заказа: {e}")


def get_service_info(service_type):
    """Получить информацию об услуге"""
    services = {
        'transfer_eu': {
            'name': 'Перевод на европейские карты',
            'description': 'Перевод средств на карты европейских банков',
            'min_amount': 10,
            'commission': 0.08
        },
        'transfer_us': {
            'name': 'Перевод на американские карты',
            'description': 'Перевод средств на карты американских банков',
            'min_amount': 10,
            'commission': 0.08
        },
        'gpt': {
            'name': 'GPT',
            'description': 'Подписки на ChatGPT Plus',
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
        [InlineKeyboardButton("💳 Перевод рублей на карту", callback_data="deposit_card")],
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
        transactions = get_user_transactions(user_id)

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
        # Получаем заказы через функцию
        orders = get_all_orders(limit=20)

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
        wallets = get_top_wallets()

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
        # Получаем статистику через функцию
        stats = get_stats()
        users_count = stats["users_count"]
        orders_count = stats["orders_count"]
        total_amount = stats["total_amount"]
        total_balance = stats["total_balance"]

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


# Функция для проверки криптоплатежей
async def check_crypto_payments():
    """Проверка криптоплатежей через Supabase в фоновом режиме"""

    if not crypto_checker:
        logger.warning("Крипточекер не инициализирован")
        return

    try:
        # Получаем все pending заказы с криптоплатежами через Supabase
        pending_orders = get_pending_crypto_orders()

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
                update_order_status(
                    order_id,
                    "completed",
                    ADMIN_ID,
                    f'Криптоплатеж подтвержден: {result["amount"]} {result["currency"]}'
                )

                # Выдаем карту
                card_info = auto_issue_card(service_type, amount, user_id)

                # Уведомляем пользователя
                try:
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

                    await application.bot.send_message(
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


# Flask маршруты
@app.route('/')
def home():
    """Главная страница"""
    return jsonify({
        'status': 'online',
        'bot': 'Telegram Financial Bot',
        'version': '1.0.0',
        'environment': ENVIRONMENT,
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
        stats_data = get_stats()
        return jsonify({
            "users_count": stats_data["users_count"],
            "orders_count": stats_data["orders_count"],
            "total_amount": stats_data["total_amount"],
            "total_balance": stats_data["total_balance"],
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Ошибка получения /stats: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/webhook', methods=['POST'])
def webhook():
    if request.method == "POST":
        try:
            update = Update.de_json(request.get_json(force=True), application.bot)
            asyncio.run(application.process_update(update))
            return jsonify({"status": "ok"}), 200
        except Exception as e:
            logger.error(f"Ошибка обработки вебхука: {e}")
            return jsonify({"error": str(e)}), 500


def init_bot():
    """Инициализация бота"""
    global application

    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN не установлен!")
        sys.exit(1)

    if not ADMIN_ID:
        logger.error("❌ ADMIN_ID не установлен!")
        sys.exit(1)

    logger.info("🚀 Инициализация Telegram Financial Bot...")
    logger.info(f"📊 Порт: {PORT}")
    logger.info(f"🌍 Окружение: {ENVIRONMENT}")
    logger.info(f"👤 Администратор: {ADMIN_ID}")

    # Создаем приложение
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("check_payment", check_payment_command))
    application.add_handler(CommandHandler("add_money", add_money_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    application.add_error_handler(error_handler)

    return application


async def setup_webhook():
    """Настройка вебхука для production"""
    await application.initialize()

    # delete old webhook if exists
    await application.bot.delete_webhook(drop_pending_updates=True)

    await application.start()

    await application.bot.set_webhook(
        url=f"{WEBHOOK_URL}/webhook",
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )

    # PTB’s internal web server starts automatically
    await application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="webhook",
    )


async def run_polling():
    """Запуск polling режима"""
    try:
        # Удаляем вебхук если был установлен
        await application.bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Вебхук удален")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось удалить вебхук: {e}")

    # Инициализируем и запускаем
    await application.initialize()
    await application.start()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    logger.info("🤖 Бот запущен в polling режиме!")
    logger.info("Нажмите Ctrl+C для остановки...")

    # Ждем остановки
    try:
        # Бесконечный цикл
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("\n🛑 Получен сигнал остановки...")
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        logger.info("✅ Бот остановлен")


def main():
    """Основная функция запуска"""
    init_bot()

    if ENVIRONMENT == 'хуй': # production
        # Production режим (Render) - вебхуки
        if not WEBHOOK_URL:
            logger.error("❌ WEBHOOK_URL не установлен для production!")
            sys.exit(1)

        logger.info("🌐 Запуск в production режиме (вебхуки)")

        # Настраиваем вебхук при старте
        asyncio.run(setup_webhook())

        # Запускаем Flask
        app.run(host='0.0.0.0', port=PORT, debug=False)

    else:
        # Local режим - polling
        logger.info("🖥️  Запуск в локальном режиме (polling)")

        try:
            asyncio.run(run_polling())
        except KeyboardInterrupt:
            logger.info("\n🛑 Бот остановлен пользователем")
        except Exception as e:
            logger.error(f"❌ Ошибка запуска бота: {e}")


if __name__ == '__main__':
    main()