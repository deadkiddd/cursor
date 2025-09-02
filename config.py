
# Настройки безопасности
MAX_MESSAGE_LENGTH = 4096
RATE_LIMIT_MESSAGES = 60
RATE_LIMIT_WINDOW = 60


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
    'bybit_transfer': 0.08,
    'gpt': 0.08,
    'twitter': 0.08
}

# Минимальные суммы
MIN_AMOUNTS = {
    'cards': 10,
    'transfers': 50,
    'crypto': 5
}