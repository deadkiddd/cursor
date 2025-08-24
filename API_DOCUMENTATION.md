# API Документация для Администраторов

## Базовый URL
```
http://your-domain:10000
```

## Аутентификация
Все запросы должны содержать заголовок с ID администратора в JSON теле запроса.

## Заказы

### Получить все заказы
```
GET /admin/orders
```

**Параметры:**
- `status` (опционально) - фильтр по статусу: pending, paid, processing, completed, cancelled, failed
- `limit` (опционально) - количество заказов (по умолчанию 50)

**Пример:**
```bash
curl "http://your-domain:10000/admin/orders?status=pending&limit=20"
```

**Ответ:**
```json
{
  "success": true,
  "orders": [
    {
      "id": 1,
      "user_id": 123456789,
      "order_type": "cards",
      "service_name": "Netflix Premium",
      "amount": 15.00,
      "total_amount": 16.20,
      "status": "pending",
      "created_at": "2024-01-15 10:30:00",
      "username": "user123",
      "first_name": "Иван"
    }
  ],
  "count": 1
}
```

### Получить детали заказа
```
GET /admin/order/{order_id}
```

**Пример:**
```bash
curl "http://your-domain:10000/admin/order/1"
```

**Ответ:**
```json
{
  "success": true,
  "order": {
    "id": 1,
    "user_id": 123456789,
    "order_type": "cards",
    "service_name": "Netflix Premium",
    "amount": 15.00,
    "commission": 1.20,
    "total_amount": 16.20,
    "status": "pending",
    "payment_method": "external",
    "wallet_payment": false,
    "admin_notes": "",
    "created_at": "2024-01-15 10:30:00",
    "updated_at": "2024-01-15 10:30:00",
    "completed_at": null,
    "username": "user123",
    "first_name": "Иван",
    "status_history": [
      {
        "status": "pending",
        "admin_id": null,
        "notes": "Заказ создан",
        "created_at": "2024-01-15 10:30:00"
      }
    ]
  }
}
```

### Обновить статус заказа
```
POST /admin/order/{order_id}/status
```

**Тело запроса:**
```json
{
  "status": "paid",
  "admin_id": 987654321,
  "notes": "Оплата подтверждена"
}
```

**Пример:**
```bash
curl -X POST "http://your-domain:10000/admin/order/1/status" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "paid",
    "admin_id": 987654321,
    "notes": "Оплата подтверждена"
  }'
```

**Ответ:**
```json
{
  "success": true,
  "message": "Статус заказа #1 обновлен на 'paid'"
}
```

## Кошельки

### Получить информацию о кошельке пользователя
```
GET /admin/wallet/{user_id}
```

**Пример:**
```bash
curl "http://your-domain:10000/admin/wallet/123456789"
```

**Ответ:**
```json
{
  "success": true,
  "wallet": {
    "wallet": {
      "user_id": 123456789,
      "username": "user123",
      "first_name": "Иван",
      "balance": 50.00,
      "created_at": "2024-01-10 15:20:00",
      "updated_at": "2024-01-15 10:30:00"
    },
    "transactions": [
      {
        "transaction_type": "deposit",
        "amount": 50.00,
        "description": "Пополнение кошелька",
        "created_at": "2024-01-15 10:30:00"
      }
    ]
  }
}
```

### Пополнить кошелек пользователя
```
POST /admin/wallet/{user_id}/deposit
```

**Тело запроса:**
```json
{
  "amount": 25.00,
  "admin_id": 987654321,
  "description": "Пополнение за выполненный заказ"
}
```

**Пример:**
```bash
curl -X POST "http://your-domain:10000/admin/wallet/123456789/deposit" \
  -H "Content-Type: application/json" \
  -d '{
    "amount": 25.00,
    "admin_id": 987654321,
    "description": "Пополнение за выполненный заказ"
  }'
```

**Ответ:**
```json
{
  "success": true,
  "message": "Кошелек пользователя 123456789 пополнен на $25.0"
}
```

### Вывести средства из кошелька пользователя
```
POST /admin/wallet/{user_id}/withdraw
```

**Тело запроса:**
```json
{
  "amount": 10.00,
  "admin_id": 987654321,
  "description": "Вывод по запросу пользователя"
}
```

**Пример:**
```bash
curl -X POST "http://your-domain:10000/admin/wallet/123456789/withdraw" \
  -H "Content-Type: application/json" \
  -d '{
    "amount": 10.00,
    "admin_id": 987654321,
    "description": "Вывод по запросу пользователя"
  }'
```

**Ответ:**
```json
{
  "success": true,
  "message": "Из кошелька пользователя 123456789 выведено $10.0"
}
```

## Статусы заказов

- `pending` - Ожидает оплаты
- `paid` - Оплачен
- `processing` - В обработке
- `completed` - Завершен
- `cancelled` - Отменен
- `failed` - Ошибка

## Типы транзакций кошелька

- `deposit` - Пополнение
- `withdrawal` - Вывод
- `payment` - Оплата заказа
- `refund` - Возврат
- `commission` - Комиссия

## Коды ошибок

- `400` - Неверный запрос (отсутствуют обязательные параметры)
- `404` - Ресурс не найден
- `500` - Внутренняя ошибка сервера

## Примеры использования

### Получить все ожидающие оплаты заказы
```bash
curl "http://your-domain:10000/admin/orders?status=pending"
```

### Обновить статус заказа на "оплачен"
```bash
curl -X POST "http://your-domain:10000/admin/order/1/status" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "paid",
    "admin_id": 987654321,
    "notes": "Оплата получена"
  }'
```

### Пополнить кошелек пользователя за выполненный заказ
```bash
curl -X POST "http://your-domain:10000/admin/wallet/123456789/deposit" \
  -H "Content-Type: application/json" \
  -d '{
    "amount": 15.00,
    "admin_id": 987654321,
    "description": "Возврат за отмененный заказ #5"
  }'
```

### Вывести средства из кошелька
```bash
curl -X POST "http://your-domain:10000/admin/wallet/123456789/withdraw" \
  -H "Content-Type: application/json" \
  -d '{
    "amount": 25.00,
    "admin_id": 987654321,
    "description": "Вывод на карту пользователя"
  }'
```
