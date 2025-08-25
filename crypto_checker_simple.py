#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Упрощенный модуль для проверки криптоплатежей
Поддерживает только ETH/USDT и SOL
"""

import requests
import time
import json
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
import logging
import os

logger = logging.getLogger(__name__)

class SimpleCryptoChecker:
    def __init__(self, db_path="bot_database.db"):
        self.db_path = db_path
        
        # API ключи (добавьте в .env)
        self.etherscan_token = os.getenv('ETHERSCAN_TOKEN', 'YOUR_ETHERSCAN_TOKEN')
        self.solscan_token = os.getenv('SOLSCAN_TOKEN', 'YOUR_SOLSCAN_TOKEN')  # Опционально
        
        # Адреса кошельков (замените на ваши)
        self.wallets = {
            'eth': '0x12E450e53E1acD323B95e36636cB4927aC6C17eE',
            'usdt': '0x12E450e53E1acD323B95e36636cB4927aC6C17eE',  # Тот же адрес что и ETH
            'sol': '6s8bjsP5K3hvdj3bca4FxW8W6CqqSLH26aufVALTJbBq'
        }
        
        # Минимальные суммы для подтверждения
        self.min_amounts = {
            'eth': 0.001,   # ~$2-3
            'usdt': 5,      # $5
            'sol': 0.01     # ~$2-3
        }
        
        # Инициализация базы данных
        self._init_database()
    
    def _init_database(self):
        """Инициализация базы данных для отслеживания транзакций"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS processed_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tx_hash TEXT UNIQUE,
                    currency TEXT,
                    amount REAL,
                    order_id INTEGER,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info("База данных инициализирована")
            
        except Exception as e:
            logger.error(f"Ошибка инициализации базы данных: {e}")
    
    def _is_transaction_processed(self, tx_hash, currency):
        """Проверить, была ли транзакция уже обработана"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id FROM processed_transactions 
                WHERE tx_hash = ? AND currency = ?
            ''', (tx_hash, currency))
            
            result = cursor.fetchone()
            conn.close()
            
            return result is not None
            
        except Exception as e:
            logger.error(f"Ошибка проверки транзакции: {e}")
            return False
    
    def _mark_transaction_processed(self, tx_hash, currency, amount, order_id):
        """Отметить транзакцию как обработанную"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO processed_transactions (tx_hash, currency, amount, order_id)
                VALUES (?, ?, ?, ?)
            ''', (tx_hash, currency, amount, order_id))
            
            conn.commit()
            conn.close()
            logger.info(f"Транзакция {tx_hash} отмечена как обработанная")
            
        except Exception as e:
            logger.error(f"Ошибка отметки транзакции: {e}")
    
    def _is_recent_transaction(self, timestamp, minutes=30):
        """Проверить, что транзакция не старше указанного времени"""
        if not timestamp:
            return False
        
        try:
            tx_time = datetime.fromtimestamp(int(timestamp))
            now = datetime.now()
            return (now - tx_time).total_seconds() <= minutes * 60
        except:
            return False
    
    def check_eth_payment(self, expected_amount, order_id):
        """Проверка Ethereum платежа через Etherscan API"""
        try:
            if self.etherscan_token == 'YOUR_ETHERSCAN_TOKEN':
                return {'success': False, 'error': 'ETHERSCAN_TOKEN не настроен'}
            
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
            
            if data.get('status') != '1':
                return {'success': False, 'error': f"API ошибка: {data.get('message', 'Неизвестная ошибка')}"}
            
            transactions = data.get('result', [])
            
            # Проверяем последние транзакции
            for tx in transactions[:10]:  # Проверяем только последние 10
                if tx.get('to', '').lower() == self.wallets['eth'].lower():
                    amount_eth = Decimal(tx['value']) / Decimal(10**18)  # Wei в ETH
                    
                    # Проверяем сумму и время
                    if (amount_eth >= self.min_amounts['eth'] and 
                        self._is_recent_transaction(tx.get('timeStamp'), 30)):
                        
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
        """Проверка USDT платежа через Etherscan API"""
        try:
            if self.etherscan_token == 'YOUR_ETHERSCAN_TOKEN':
                return {'success': False, 'error': 'ETHERSCAN_TOKEN не настроен'}
            
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
            
            if data.get('status') != '1':
                return {'success': False, 'error': f"API ошибка: {data.get('message', 'Неизвестная ошибка')}"}
            
            transactions = data.get('result', [])
            
            # Проверяем последние транзакции
            for tx in transactions[:10]:  # Проверяем только последние 10
                if tx.get('to', '').lower() == self.wallets['usdt'].lower():
                    amount_usdt = Decimal(tx['value']) / Decimal(10**6)  # USDT имеет 6 десятичных знаков
                    
                    # Проверяем сумму и время
                    if (amount_usdt >= self.min_amounts['usdt'] and 
                        self._is_recent_transaction(tx.get('timeStamp'), 30)):
                        
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
    
    def check_sol_payment(self, expected_amount, order_id):
        """Проверка Solana платежа через Solscan API"""
        try:
            url = f"https://public-api.solscan.io/account/transactions"
            params = {
                'account': self.wallets['sol'],
                'limit': 10
            }
            
            headers = {}
            if self.solscan_token != 'YOUR_SOLSCAN_TOKEN':
                headers['Authorization'] = f'Bearer {self.solscan_token}'
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if not isinstance(data, list):
                return {'success': False, 'error': 'Неверный формат ответа от Solscan'}
            
            # Проверяем последние транзакции
            for tx in data:
                if tx.get('status') == 'Success':
                    # Проверяем входящие транзакции
                    for instruction in tx.get('parsedInstruction', []):
                        if instruction.get('type') == 'transfer':
                            amount_sol = Decimal(instruction.get('info', {}).get('lamports', 0)) / Decimal(10**9)  # Lamports в SOL
                            
                            # Проверяем сумму и время
                            if (amount_sol >= self.min_amounts['sol'] and 
                                self._is_recent_transaction(tx.get('blockTime'), 30)):
                                
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
    
    def check_payment(self, currency, expected_amount, order_id):
        """Проверить платеж по валюте"""
        if currency == 'eth':
            return self.check_eth_payment(expected_amount, order_id)
        elif currency == 'usdt':
            return self.check_usdt_payment(expected_amount, order_id)
        elif currency == 'sol':
            return self.check_sol_payment(expected_amount, order_id)
        else:
            return {'success': False, 'error': f'Неподдерживаемая валюта: {currency}'}
    
    def process_payment(self, result):
        """Обработать найденный платеж"""
        if result['success']:
            # Отмечаем транзакцию как обработанную
            self._mark_transaction_processed(
                result['tx_hash'],
                result['currency'],
                result['amount'],
                result['order_id']
            )
            
            logger.info(f"Платеж обработан: {result['amount']} {result['currency']} для заказа {result['order_id']}")
            
            return True
        else:
            logger.warning(f"Платеж не найден: {result.get('message', result.get('error', 'Неизвестная ошибка'))}")
            return False

# Функция для автоматической выдачи карт (замените на вашу логику)
def auto_issue_card(service_type, amount, user_id):
    """Автоматическая выдача карты после подтверждения платежа"""
    try:
        # Здесь должна быть ваша логика генерации карт
        # Например, вызов API вашего сервиса карт
        
        card_info = {
            'service': service_type,
            'amount': amount,
            'user_id': user_id,
            'card_number': f"****-****-****-{str(user_id)[-4:]}",
            'expiry': "12/25",
            'cvv': "***"
        }
        
        logger.info(f"Карта выдана: {card_info}")
        return card_info
        
    except Exception as e:
        logger.error(f"Ошибка выдачи карты: {e}")
        return None

# Пример использования
if __name__ == "__main__":
    # Инициализация
    checker = SimpleCryptoChecker()
    
    # Проверка платежей
    print("Проверка ETH платежа...")
    eth_result = checker.check_eth_payment(0.001, 1)
    print(f"ETH результат: {eth_result}")
    
    print("Проверка USDT платежа...")
    usdt_result = checker.check_usdt_payment(5, 1)
    print(f"USDT результат: {usdt_result}")
    
    print("Проверка SOL платежа...")
    sol_result = checker.check_sol_payment(0.01, 1)
    print(f"SOL результат: {sol_result}")
