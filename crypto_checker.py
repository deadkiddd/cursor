#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Модуль для проверки криптоплатежей
"""

import requests
import time
import json
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

class CryptoPaymentChecker:
    def __init__(self, db_path="bot_database.db"):
        self.db_path = db_path
        
        # API ключи (добавьте в .env)
        self.blockcypher_token = "YOUR_BLOCKCYPHER_TOKEN"  # Бесплатный
        self.etherscan_token = "YOUR_ETHERSCAN_TOKEN"      # Бесплатный
        self.bybit_api_key = "YOUR_BYBIT_API_KEY"         # Bybit API
        self.bybit_secret_key = "YOUR_BYBIT_SECRET_KEY"   # Bybit Secret
        
        # Адреса кошельков
        self.wallets = {
            'btc': 'bc1qknfqlkx6njdsp8y24julefe2uwhw75jm09yd27',
            'eth': '0x12E450e53E1acD323B95e36636cB4927aC6C17eE',
            'usdt': '0x12E450e53E1acD323B95e36636cB4927aC6C17eE',  # Тот же адрес что и ETH
            'sol': '6s8bjsP5K3hvdj3bca4FxW8W6CqqSLH26aufVALTJbBq',
            'bybit_uid': '20452669'  # UID в Bybit
        }
        
        # Минимальные суммы для подтверждения
        self.min_amounts = {
            'btc': 0.0001,  # ~$3-4
            'eth': 0.001,   # ~$2-3
            'usdt': 5,      # $5
            'sol': 0.01,    # ~$2-3
            'bybit_usdt': 5 # $5 (комиссия 8%)
        }
    
    def check_btc_payment(self, expected_amount, order_id):
        """Проверка Bitcoin платежа через BlockCypher API"""
        try:
            url = f"https://api.blockcypher.com/v1/btc/main/addrs/{self.wallets['btc']}/full"
            params = {'token': self.blockcypher_token}
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            # Проверяем последние транзакции
            for tx in data.get('txs', []):
                # Проверяем только входящие транзакции
                if tx.get('outputs'):
                    for output in tx['outputs']:
                        if output.get('addresses') and self.wallets['btc'] in output['addresses']:
                            amount_btc = Decimal(output['value']) / Decimal(100000000)  # Сатоши в BTC
                            
                            # Проверяем сумму и время
                            if (amount_btc >= self.min_amounts['btc'] and 
                                self._is_recent_transaction(tx.get('confirmed'), 30)):  # 30 минут
                                
                                # Проверяем, не обрабатывали ли мы уже эту транзакцию
                                if not self._is_transaction_processed(tx['hash'], 'btc'):
                                    return {
                                        'success': True,
                                        'amount': float(amount_btc),
                                        'tx_hash': tx['hash'],
                                        'currency': 'btc',
                                        'order_id': order_id
                                    }
            
            return {'success': False, 'message': 'Платеж не найден'}
            
        except Exception as e:
            logger.error(f"Ошибка проверки BTC платежа: {e}")
            return {'success': False, 'error': str(e)}
    
    def check_eth_payment(self, expected_amount, order_id):
        """Проверка Ethereum платежа через Etherscan API"""
        try:
            url = "https://api.etherscan.io/api"
            params = {
                'module': 'account',
                'action': 'txlist',
                'address': self.wallets['eth'],
                'startblock': 0,
                'endblock': 99999999,
                'sort': 'desc',
                'apikey': self.etherscan_token
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data['status'] == '1':
                for tx in data['result']:
                    # Проверяем только входящие транзакции
                    if tx['to'].lower() == self.wallets['eth'].lower():
                        amount_eth = Decimal(tx['value']) / Decimal(10**18)  # Wei в ETH
                        
                        # Проверяем сумму и время
                        if (amount_eth >= self.min_amounts['eth'] and 
                            self._is_recent_transaction(int(tx['timeStamp']), 30)):
                            
                            # Проверяем, не обрабатывали ли мы уже эту транзакцию
                            if not self._is_transaction_processed(tx['hash'], 'eth'):
                                return {
                                    'success': True,
                                    'amount': float(amount_eth),
                                    'tx_hash': tx['hash'],
                                    'currency': 'eth',
                                    'order_id': order_id
                                }
            
            return {'success': False, 'message': 'Платеж не найден'}
            
        except Exception as e:
            logger.error(f"Ошибка проверки ETH платежа: {e}")
            return {'success': False, 'error': str(e)}
    
    def check_usdt_payment(self, expected_amount, order_id):
        """Проверка USDT платежа (ERC-20 токен)"""
        try:
            # USDT контракт на Ethereum
            usdt_contract = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
            
            url = "https://api.etherscan.io/api"
            params = {
                'module': 'account',
                'action': 'tokentx',
                'contractaddress': usdt_contract,
                'address': self.wallets['usdt'],
                'startblock': 0,
                'endblock': 99999999,
                'sort': 'desc',
                'apikey': self.etherscan_token
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data['status'] == '1':
                for tx in data['result']:
                    # Проверяем только входящие транзакции
                    if tx['to'].lower() == self.wallets['usdt'].lower():
                        amount_usdt = Decimal(tx['value']) / Decimal(10**6)  # USDT имеет 6 десятичных знаков
                        
                        # Проверяем сумму и время
                        if (amount_usdt >= self.min_amounts['usdt'] and 
                            self._is_recent_transaction(int(tx['timeStamp']), 30)):
                            
                            # Проверяем, не обрабатывали ли мы уже эту транзакцию
                            if not self._is_transaction_processed(tx['hash'], 'usdt'):
                                return {
                                    'success': True,
                                    'amount': float(amount_usdt),
                                    'tx_hash': tx['hash'],
                                    'currency': 'usdt',
                                    'order_id': order_id
                                }
            
            return {'success': False, 'message': 'Платеж не найден'}
            
        except Exception as e:
            logger.error(f"Ошибка проверки USDT платежа: {e}")
            return {'success': False, 'error': str(e)}
    
    def _is_recent_transaction(self, timestamp, minutes=30):
        """Проверяет, была ли транзакция недавно"""
        if isinstance(timestamp, str):
            timestamp = int(timestamp)
        
        tx_time = datetime.fromtimestamp(timestamp)
        current_time = datetime.now()
        
        return (current_time - tx_time).total_seconds() <= minutes * 60
    
    def _is_transaction_processed(self, tx_hash, currency):
        """Проверяет, была ли транзакция уже обработана"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id FROM processed_transactions 
                WHERE tx_hash = ? AND currency = ?
            """, (tx_hash, currency))
            
            result = cursor.fetchone()
            conn.close()
            
            return result is not None
            
        except Exception as e:
            logger.error(f"Ошибка проверки обработанной транзакции: {e}")
            return False
    
    def mark_transaction_processed(self, tx_hash, currency, order_id):
        """Отмечает транзакцию как обработанную"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO processed_transactions (tx_hash, currency, order_id, processed_at)
                VALUES (?, ?, ?, ?)
            """, (tx_hash, currency, order_id, datetime.now()))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Ошибка отметки транзакции: {e}")
    
    def check_sol_payment(self, expected_amount, order_id):
        """Проверка Solana платежа через Solscan API"""
        try:
            url = f"https://public-api.solscan.io/account/transactions"
            params = {
                'account': self.wallets['sol'],
                'limit': 20
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            for tx in data.get('data', []):
                # Проверяем только входящие транзакции
                if tx.get('status') == 'Success' and tx.get('lamport'):
                    amount_sol = Decimal(tx['lamport']) / Decimal(10**9)  # Lamports в SOL
                    
                    # Проверяем сумму и время
                    tx_time = datetime.fromtimestamp(tx.get('blockTime', 0))
                    current_time = datetime.now()
                    
                    if (amount_sol >= self.min_amounts['sol'] and 
                        (current_time - tx_time).total_seconds() <= 30 * 60):  # 30 минут
                        
                        # Проверяем, не обрабатывали ли мы уже эту транзакцию
                        if not self._is_transaction_processed(tx['txHash'], 'sol'):
                            return {
                                'success': True,
                                'amount': float(amount_sol),
                                'tx_hash': tx['txHash'],
                                'currency': 'sol',
                                'order_id': order_id
                            }
            
            return {'success': False, 'message': 'Платеж не найден'}
            
        except Exception as e:
            logger.error(f"Ошибка проверки SOL платежа: {e}")
            return {'success': False, 'error': str(e)}
    
    def check_bybit_transfer(self, expected_amount, order_id):
        """Проверка внутреннего перевода Bybit"""
        try:
            import hmac
            import hashlib
            import time
            
            # Подготовка параметров для API Bybit
            timestamp = str(int(time.time() * 1000))
            
            # Получаем историю внутренних переводов
            params = {
                'api_key': self.bybit_api_key,
                'timestamp': timestamp,
                'coin': 'USDT',
                'limit': 50
            }
            
            # Создаем подпись
            query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
            signature = hmac.new(
                self.bybit_secret_key.encode('utf-8'),
                query_string.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            params['sign'] = signature
            
            url = "https://api.bybit.com/v2/private/wallet/coin/query"
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data['ret_code'] == 0:
                # Получаем историю переводов
                transfer_params = {
                    'api_key': self.bybit_api_key,
                    'timestamp': timestamp,
                    'coin': 'USDT',
                    'limit': 50
                }
                
                transfer_query = '&'.join([f"{k}={v}" for k, v in sorted(transfer_params.items())])
                transfer_signature = hmac.new(
                    self.bybit_secret_key.encode('utf-8'),
                    transfer_query.encode('utf-8'),
                    hashlib.sha256
                ).hexdigest()
                
                transfer_params['sign'] = transfer_signature
                
                transfer_url = "https://api.bybit.com/v2/private/wallet/transfer/query"
                transfer_response = requests.get(transfer_url, params=transfer_params, timeout=10)
                transfer_response.raise_for_status()
                
                transfer_data = transfer_response.json()
                
                if transfer_data['ret_code'] == 0:
                    for transfer in transfer_data['result']['data']:
                        # Проверяем входящие переводы на наш UID
                        if (transfer['type'] == 'IN' and 
                            transfer['to_account_type'] == 'UNIFIED' and
                            transfer['amount'] >= self.min_amounts['bybit_usdt']):
                            
                            # Проверяем время (последние 30 минут)
                            transfer_time = datetime.fromtimestamp(transfer['timestamp'] / 1000)
                            current_time = datetime.now()
                            
                            if (current_time - transfer_time).total_seconds() <= 30 * 60:
                                # Проверяем, не обрабатывали ли мы уже эту транзакцию
                                if not self._is_transaction_processed(transfer['id'], 'bybit_usdt'):
                                    return {
                                        'success': True,
                                        'amount': float(transfer['amount']),
                                        'tx_hash': transfer['id'],
                                        'currency': 'bybit_usdt',
                                        'order_id': order_id
                                    }
            
            return {'success': False, 'message': 'Перевод не найден'}
            
        except Exception as e:
            logger.error(f"Ошибка проверки Bybit перевода: {e}")
            return {'success': False, 'error': str(e)}
    
    def check_all_payments(self, order_id, expected_amount, currency):
        """Проверяет все типы платежей для заказа"""
        checkers = {
            'btc': self.check_btc_payment,
            'eth': self.check_eth_payment,
            'usdt': self.check_usdt_payment,
            'sol': self.check_sol_payment,
            'bybit_usdt': self.check_bybit_transfer
        }
        
        if currency in checkers:
            return checkers[currency](expected_amount, order_id)
        else:
            return {'success': False, 'error': f'Неподдерживаемая валюта: {currency}'}

# Функция для автоматической выдачи карт
def auto_issue_card(order_id, payment_info):
    """Автоматическая выдача карты после подтверждения платежа"""
    try:
        conn = sqlite3.connect("bot_database.db")
        cursor = conn.cursor()
        
        # Получаем информацию о заказе
        cursor.execute("""
            SELECT user_id, service_type, amount, status 
            FROM orders WHERE id = ?
        """, (order_id,))
        
        order = cursor.fetchone()
        if not order:
            return {'success': False, 'error': 'Заказ не найден'}
        
        user_id, service_type, amount, status = order
        
        # Проверяем, что заказ еще не обработан
        if status != 'pending':
            return {'success': False, 'error': 'Заказ уже обработан'}
        
        # Генерируем карту (здесь ваша логика генерации)
        card_data = generate_card(service_type, amount)
        
        if card_data:
            # Обновляем статус заказа
            cursor.execute("""
                UPDATE orders 
                SET status = 'completed', 
                    completed_at = ?,
                    payment_tx_hash = ?,
                    payment_amount = ?
                WHERE id = ?
            """, (datetime.now(), payment_info['tx_hash'], payment_info['amount'], order_id))
            
            # Добавляем транзакцию в кошелек
            cursor.execute("""
                INSERT INTO wallet_transactions 
                (user_id, transaction_type, amount, description, created_at)
                VALUES (?, 'payment', ?, ?)
            """, (user_id, amount, f"Оплата заказа #{order_id}"))
            
            conn.commit()
            conn.close()
            
            return {
                'success': True,
                'card_data': card_data,
                'order_id': order_id,
                'user_id': user_id
            }
        else:
            return {'success': False, 'error': 'Ошибка генерации карты'}
            
    except Exception as e:
        logger.error(f"Ошибка автоматической выдачи карты: {e}")
        return {'success': False, 'error': str(e)}

def generate_card(service_type, amount):
    """Генерация карты (заглушка - замените на вашу логику)"""
    # Здесь должна быть ваша логика генерации карт
    # Например, подключение к API карт, генерация номеров и т.д.
    
    card_templates = {
        'netflix': {
            'type': 'Netflix Premium',
            'validity': '1 месяц',
            'features': ['4K качество', '4 устройства', 'Без рекламы']
        },
        'steam': {
            'type': 'Steam Gift Card',
            'validity': 'Бессрочно',
            'features': ['Покупка игр', 'Пополнение кошелька']
        },
        'spotify': {
            'type': 'Spotify Premium',
            'validity': '1 месяц',
            'features': ['Без рекламы', 'Высокое качество', 'Офлайн режим']
        }
    }
    
    if service_type in card_templates:
        template = card_templates[service_type]
        return {
            'card_number': f"XXXX-XXXX-XXXX-{str(amount).zfill(4)}",
            'pin': f"{str(amount).zfill(4)}",
            'type': template['type'],
            'validity': template['validity'],
            'features': template['features'],
            'amount': amount
        }
    
    return None

# Пример использования
if __name__ == "__main__":
    checker = CryptoPaymentChecker()
    
    # Проверяем платеж
    result = checker.check_btc_payment(0.001, "test_order_123")
    print(f"Результат проверки: {result}")
    
    if result['success']:
        # Автоматически выдаем карту
        card_result = auto_issue_card("test_order_123", result)
        print(f"Результат выдачи карты: {card_result}")
