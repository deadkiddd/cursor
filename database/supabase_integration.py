from supabase import create_client, Client
import os
from datetime import datetime
import logging

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


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


SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_API_SECRET = os.getenv("SUPABASE_API_SECRET")

# Создаем клиент Supabase только если переменные окружения установлены
if SUPABASE_URL and SUPABASE_API_SECRET:
    supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_API_SECRET)
    logger.info("✅ Supabase клиент инициализирован")
else:
    supabase_client = None
    logger.warning("⚠️ Supabase не настроен - переменные SUPABASE_URL и SUPABASE_API_SECRET не установлены")


# Internal helper functions for Supabase operations (replacing external API endpoints)
def _get_orders_from_supabase():
    """Получить все заказы из Supabase (внутренняя функция)"""
    
    try:
        # Получаем заказы с балансами пользователей
        orders_resp = supabase_client.table("orders").select("id,user_id,service_type,amount,status,created_at").order("created_at", desc=True).execute()
        orders_data = orders_resp.data or []

        # Получаем балансы всех пользователей, чтобы не делать отдельный запрос на каждого
        user_ids = [order["user_id"] for order in orders_data]
        wallets_resp = supabase_client.table("wallets").select("user_id,balance").in_("user_id", user_ids).execute()
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


def get_top_wallets(limit: int = 10):
    """Получить топ-кошельки по балансу"""
    
    try:
        resp = supabase_client.table("wallets") \
            .select("user_id, balance, created_at") \
            .order("balance", desc=True) \
            .limit(limit) \
            .execute()
        return resp.data
    except Exception as e:
        # Логировать можно тут или пробросить выше
        raise RuntimeError(f"Ошибка получения кошельков: {e}")


def _update_order_status_in_supabase(order_id, new_status, admin_id, notes=''):
    """Обновить статус заказа в Supabase (внутренняя функция)"""
    
    try:
        # Обновляем статус заказа
        supabase_client.table("orders").update({
            "status": new_status,
            "updated_at": datetime.now().isoformat()
        }).eq("id", order_id).execute()

        # Добавляем в историю
        supabase_client.table("order_status_history").insert({
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
        wallet_resp = supabase_client.table("wallets").select("balance, created_at").eq("user_id", user_id).single().execute()
        wallet = wallet_resp.data

        if not wallet:
            return None

        # Получаем количество транзакций
        transactions_resp = supabase_client.table("wallet_transactions").select("id", count="exact").eq("user_id", user_id).execute()
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
        wallet_resp = supabase_client.table("wallets").select("balance").eq("user_id", user_id).single().execute()
        wallet = wallet_resp.data

        if not wallet:
            logger.error(f"Кошелек пользователя {user_id} не найден")
            return False

        new_balance = wallet['balance'] + amount

        # Обновляем баланс кошелька
        supabase_client.table("wallets").update({"balance": new_balance, "updated_at": datetime.now().isoformat()}).eq("user_id", user_id).execute()

        # Добавляем транзакцию
        supabase_client.table("wallet_transactions").insert({
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
        wallet_resp = supabase_client.table("wallets").select("balance").eq("user_id", user_id).single().execute()
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
        supabase_client.table("wallets").update({"balance": new_balance, "updated_at": datetime.now().isoformat()}).eq("user_id", user_id).execute()

        # Добавляем транзакцию
        supabase_client.table("wallet_transactions").insert({
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


def _get_user_wallet_data(user_id):
    """Получить данные кошелька пользователя"""
    
    try:
        existing = supabase_client.table("wallets").select("balance").eq("user_id", user_id).execute()
        if existing.data:
            return existing.data[0]
        return None
    except Exception as e:
        logger.error(f"Ошибка получения кошелька пользователя {user_id}: {e}")
        return None


def get_or_create_wallet(user_id, username=None, first_name=None):
    """Получить или создать кошелек пользователя"""
    
    try:
        # Проверяем существование кошелька
        existing = supabase_client.table("wallets").select("balance").eq("user_id", user_id).execute()
        if existing.data:
            return existing.data[0]
        
        # Создаем новый кошелек
        supabase_client.table("wallets").insert({"user_id": user_id, "balance": 0.0}).execute()
        
        # Получаем созданный кошелек
        wallet = supabase_client.table("wallets").select("*").eq("user_id", user_id).execute()
        if wallet.data:
            return wallet.data[0]
        
        # Если не удалось получить, создаем базовый объект
        return {"user_id": user_id, "balance": 0.0}
        
    except Exception as e:
        logger.error(f"Ошибка создания/получения кошелька пользователя {user_id}: {e}")
        # Создаем базовый объект кошелька
        try:
            supabase_client.table("wallets").insert({
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "balance": 0.0,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }).execute()
        except Exception as insert_error:
            logger.error(f"Ошибка создания кошелька в блоке except: {insert_error}")
        return {"user_id": user_id, "balance": 0.0}


def update_wallet_balance(user_id, amount, transaction_type, description):
    """Обновить баланс кошелька"""
    
    try:
        # Получаем текущий баланс
        wallet_resp = supabase_client.table("wallets").select("balance").eq("user_id", user_id).execute()
        if not wallet_resp.data:
            logger.error(f"Кошелек пользователя {user_id} не найден")
            return False
        
        current_balance = wallet_resp.data[0]['balance']
        new_balance = current_balance + amount
        
        # Обновляем баланс
        supabase_client.table("wallets").update({
            "balance": new_balance,
            "updated_at": datetime.now().isoformat()
        }).eq("user_id", user_id).execute()
        
        # Добавляем транзакцию
        supabase_client.table("wallet_transactions").insert({
            "user_id": user_id,
            "amount": amount,
            "transaction_type": transaction_type,
            "description": description,
            "created_at": datetime.now().isoformat()
        }).execute()
        
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления баланса кошелька пользователя {user_id}: {e}")
        return False


def add_money_to_wallet(user_id, amount, description):
    """Добавить деньги в кошелек"""
    return update_wallet_balance(user_id, amount, "deposit", description)


def create_order(user_id, service_type, amount, description):
    """Создать заказ"""
    
    try:
        order_resp = supabase_client.table("orders").insert({
            "user_id": user_id,
            "service_type": service_type,
            "amount": amount,
            "description": description,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }).execute()
        
        if order_resp.data:
            order_id = order_resp.data[0]['id']
            
            # Добавляем в историю статусов
            supabase_client.table("order_status_history").insert({
                "order_id": order_id,
                "status": "pending",
                "admin_id": None,
                "notes": "Заказ создан",
                "created_at": datetime.now().isoformat()
            }).execute()
            
            return order_resp.data[0]
        
        return None
    except Exception as e:
        logger.error(f"Ошибка создания заказа для пользователя {user_id}: {e}")
        return None


def get_user_orders(user_id):
    """Получить заказы пользователя"""
    
    try:
        order_resp = supabase_client.table("orders")\
            .select("id,service_type,amount,status,created_at")\
            .eq("user_id", user_id)\
            .order("created_at", desc=True)\
            .execute()
        return order_resp.data or []
    except Exception as e:
        logger.error(f"Ошибка получения заказов пользователя {user_id}: {e}")
        return []


def get_user_transactions(user_id):
    """Получить транзакции пользователя"""
    
    try:
        transactions = supabase_client.table("wallet_transactions")\
            .select("id,amount,transaction_type,description,created_at")\
            .eq("user_id", user_id)\
            .order("created_at", desc=True)\
            .limit(10)\
            .execute()
        return transactions.data or []
    except Exception as e:
        logger.error(f"Ошибка получения транзакций пользователя {user_id}: {e}")
        return []


def get_all_orders():
    """Получить все заказы"""
    
    try:
        orders_resp = supabase_client.table("orders")\
            .select("id,user_id,service_type,amount,status,created_at")\
            .order("created_at", desc=True)\
            .execute()
        return orders_resp.data or []
    except Exception as e:
        logger.error(f"Ошибка получения всех заказов: {e}")
        return []


def get_stats():
    """Получить статистику"""
    
    try:
        users_resp = supabase_client.table("wallets").select("user_id", count="exact").execute()
        users_count = users_resp.count or 0
        
        orders_resp = supabase_client.table("orders").select("id", count="exact").execute()
        orders_count = orders_resp.count or 0
        
        total_amount_resp = supabase_client.table("orders").select("amount").execute()
        total_amount = sum([o["amount"] for o in total_amount_resp.data]) if total_amount_resp.data else 0
        
        total_balance_resp = supabase_client.table("wallets").select("balance").execute()
        total_balance = sum([w["balance"] for w in total_balance_resp.data]) if total_balance_resp.data else 0
        
        return {
            "users_count": users_count,
            "orders_count": orders_count,
            "total_amount": total_amount,
            "total_balance": total_balance
        }
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        return {
            "users_count": 0,
            "orders_count": 0,
            "total_amount": 0,
            "total_balance": 0
        }


def get_pending_orders():
    """Получить ожидающие заказы"""
    
    try:
        pending_resp = supabase_client.table("orders")\
            .select("id,user_id,service_type,amount,status,created_at")\
            .eq("status", "pending")\
            .order("created_at", desc=True)\
            .execute()
        return pending_resp.data or []
    except Exception as e:
        logger.error(f"Ошибка получения ожидающих заказов: {e}")
        return []


def update_order_status(order_id, new_status, admin_id, notes=''):
    """Обновить статус заказа"""
    try:
        # Обновляем статус заказа
        supabase_client.table("orders").update({
            "status": new_status,
            "updated_at": datetime.now().isoformat()
        }).eq("id", order_id).execute()
        
        # Добавляем в историю
        supabase_client.table("order_status_history").insert({
            "order_id": order_id,
            "status": new_status,
            "admin_id": admin_id,
            "notes": notes,
            "created_at": datetime.now().isoformat()
        }).execute()
        
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления статуса заказа {order_id}: {e}")
        return False


def get_order_by_id(order_id):
    """Получить заказ по ID"""
    try:
        order_resp = supabase_client.table("orders")\
            .select("id, user_id, service_type, amount, status, description, created_at")\
            .eq("id", order_id)\
            .execute()
        
        if order_resp.data:
            return order_resp.data[0]
        return None
    except Exception as e:
        logger.error(f"Ошибка получения заказа {order_id}: {e}")
        return None


def get_pending_crypto_orders():
    """Получить ожидающие криптозаказы"""
    try:
        pending_resp = supabase_client.table("orders")\
            .select("id, user_id, service_type, amount")\
            .eq("status", "pending")\
            .like("service_type", "crypto_%")\
            .execute()
        return pending_resp.data or []
    except Exception as e:
        logger.error(f"Ошибка получения ожидающих криптозаказов: {e}")
        return []
