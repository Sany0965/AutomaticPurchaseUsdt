import telebot
import threading
import requests
import logging
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from yoomoney import Quickpay, Client
from datetime import datetime, timedelta
import os

# Конфигурация
TOKEN = "токен бота"
YOOMONEY_TOKEN = "токен юмани"
CRYPTOBOT_KEY = "токен от приложения криптобот для отправки чеков, не забудьте в настройках приложения включить отправку чеков чеккреате"  # Замените на ваш тестовый ключ Cryptobot
RECEIVER = "номер кошелька"
EXCHANGERATESAPI_KEY = ""  # Ключ от Exchange Rates API
COMMISSION = 0.08           # 8% комиссия
MINIMUM_AMOUNT_USD = 0.1    # Минимальная сумма чека в долларах
MINIMUM_AMOUNT_RUB = 10     # Минимальная сумма чека в рублях

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(TOKEN)

# Хранилище данных
user_data = {}
pending_payments = {}

# Функция получения курса USD/RUB
def get_usd_to_rub_rate():
    url = f"http://api.exchangeratesapi.io/v1/latest?access_key={EXCHANGERATESAPI_KEY}&symbols=USD,RUB"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        if data.get("success", False):
            usd_rate = data["rates"]["USD"]
            rub_rate = data["rates"]["RUB"]
            return rub_rate / usd_rate  # Курс RUB/USD
    return None

# Функции создания клавиатур
def main_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("💵 Ввести сумму", callback_data="enter_amount"))
    return keyboard

def confirm_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(
        InlineKeyboardButton("✅ Да", callback_data="confirm_yes"),
        InlineKeyboardButton("❌ Нет", callback_data="confirm_no")
    )
    return keyboard

def payment_keyboard(payment_url):
    keyboard = InlineKeyboardMarkup()
    keyboard.add(
        InlineKeyboardButton("🔗 Перейти к оплате", url=payment_url),
        InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment")
    )
    return keyboard

# Обработчики команд /start и /help
@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(
        message.chat.id,
        "Привет! Я бот, который может создать чек Cryptobot. Жми /help, если тебе не понятно.",
        reply_markup=main_keyboard()
    )
    logger.info(f"Пользователь {message.chat.id} начал работу с ботом.")

@bot.message_handler(commands=["help"])
def help(message):
    bot.send_message(
        message.chat.id,
        "Этот бот позволяет создавать чеки Cryptobot. Просто введите сумму в долларах, "
        "и бот создаст счет в Юмани. После оплаты, через Cryptobot API, будет создан чек.\n\n"
        "Минимальная сумма чека: 0.1$ или 10₽."
    )
    logger.info(f"Пользователь {message.chat.id} запросил помощь.")

# Обработчик ввода суммы
@bot.callback_query_handler(func=lambda call: call.data == "enter_amount")
def enter_amount(call):
    bot.send_message(call.message.chat.id, "Введите сумму чека в долларах (например, 5):")
    bot.register_next_step_handler(call.message, process_amount)
    logger.info(f"Пользователь {call.message.chat.id} начал ввод суммы.")

def process_amount(message):
    try:
        amount_usd = float(message.text)
        if amount_usd < MINIMUM_AMOUNT_USD:
            bot.send_message(message.chat.id, f"Минимальная сумма чека: {MINIMUM_AMOUNT_USD}$.")
            logger.warning(f"Пользователь {message.chat.id} ввел сумму меньше минимальной: {amount_usd}$.")
            return

        rate = get_usd_to_rub_rate()
        if not rate:
            bot.send_message(message.chat.id, "Ошибка: не удалось получить курс валют. Попробуйте позже.")
            logger.error("Ошибка при получении курса валют.")
            return

        amount_rub = amount_usd * rate * (1 + COMMISSION)
        if amount_rub < MINIMUM_AMOUNT_RUB:
            bot.send_message(message.chat.id, f"Минимальная сумма чека: {MINIMUM_AMOUNT_RUB}₽.")
            logger.warning(f"Пользователь {message.chat.id} ввел сумму меньше минимальной: {amount_rub}₽.")
            return

        user_data[message.chat.id] = {
            "amount_usd": amount_usd,
            "amount_rub": amount_rub,
            "rate": rate
        }

        bot.send_message(
            message.chat.id,
            f"Вы хотите чек на {amount_usd}$? Это будет {amount_rub:.2f}₽ (с учетом комиссии 8%). Подтвердите, пожалуйста.",
            reply_markup=confirm_keyboard()
        )
        logger.info(f"Пользователь {message.chat.id} подтверждает сумму: {amount_usd}$ ({amount_rub}₽).")
    except ValueError:
        bot.send_message(message.chat.id, "Пожалуйста, введите корректную сумму (например, 5).")
        logger.warning(f"Пользователь {message.chat.id} ввел некорректную сумму.")

# Обработчик подтверждения операции
@bot.callback_query_handler(func=lambda call: call.data in ["confirm_yes", "confirm_no"])
def confirm(call):
    if call.data == "confirm_no":
        bot.send_message(call.message.chat.id, "Операция отменена.")
        logger.info(f"Пользователь {call.message.chat.id} отменил операцию.")
        return

    user_id = call.message.chat.id
    if user_id not in user_data:
        bot.send_message(user_id, "Ошибка: данные не найдены. Начните заново.")
        logger.error(f"Данные пользователя {user_id} не найдены.")
        return

    amount_rub = user_data[user_id]["amount_rub"]
    label = f"user_{user_id}_{datetime.now().timestamp()}"

    quickpay = Quickpay(
        receiver=RECEIVER,
        quickpay_form="shop",
        targets="Оплата чека Cryptobot",
        paymentType="SB",
        sum=amount_rub,
        label=label
    )

    pending_payments[user_id] = {
        "label": label,
        "amount_usd": user_data[user_id]["amount_usd"],
        "expiry_time": datetime.now() + timedelta(minutes=10)
    }

    bot.send_message(
        user_id,
        f"Оплатите счет {amount_rub:.2f}₽ для получения чека Cryptobot на {user_data[user_id]['amount_usd']}$.",
        reply_markup=payment_keyboard(quickpay.base_url)
    )
    logger.info(f"Пользователь {user_id} создал счет на {amount_rub}₽.")

    # Запуск таймера для удаления неоплаченного платежа через 10 минут
    threading.Timer(600, delete_pending_payment, args=[user_id]).start()

# Функция удаления неоплаченного платежа
def delete_pending_payment(user_id):
    if user_id in pending_payments:
        del pending_payments[user_id]
        bot.send_message(user_id, "Вы не оплатили счет в течение 10 минут. Он был удален. Нажмите /start, чтобы начать заново.")
        logger.info(f"Счет пользователя {user_id} удален из-за истечения времени.")

def create_crypto_check(asset, amount, user_id=None):
    url = "https://pay.crypt.bot/api/createCheck"  # Тестовый URL
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_KEY,
        "Content-Type": "application/json"
    }
    
    # Преобразуем сумму в строку с 6 знаками для USDT (1e6)
    amount_str = f"{amount:.6f}".rstrip('0').rstrip('.') if '.' in f"{amount:.6f}" else f"{amount:.6f}"
    
    payload = {
        "asset": asset,
        "amount": amount_str,
        "user_id": user_id
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()
        logger.info(f"Ответ Cryptobot API: {result}")  # Логируем полный ответ
        
        if not result.get("ok"):
            logger.error(f"Ошибка Cryptobot: {result.get('error')}")
            return None
            
        check_data = result.get("result", {})
        if "check_id" not in check_data or "bot_check_url" not in check_data:  # Проверяем bot_check_url
            logger.error("Некорректный ответ от Cryptobot: отсутствуют необходимые поля")
            return None
            
        logger.info(f"Чек успешно создан для пользователя {user_id} на сумму {amount} {asset}.")
        return result
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка при создании чека: {str(e)}")
        return None

# Обновленный обработчик проверки оплаты
@bot.callback_query_handler(func=lambda call: call.data == "check_payment")
def check_payment(call):
    user_id = call.message.chat.id
    if user_id not in pending_payments:
        bot.answer_callback_query(call.id, "Оплата не найдена.", show_alert=True)
        logger.warning(f"Пользователь {user_id} попытался проверить несуществующую оплату.")
        return

    label = pending_payments[user_id]["label"]
    client = Client(YOOMONEY_TOKEN)
    history = client.operation_history(label=label)

    for operation in history.operations:
        if operation.status == "success":
            payment_time = datetime.now()
            # Создаем чек через Cryptobot API
            asset = "USDT"  # Можно изменить на нужный криптоактив
            amount = pending_payments[user_id]["amount_usd"]
            crypto_check = create_crypto_check(asset, amount, user_id=user_id)
            
            if crypto_check and crypto_check.get("result"):
                check_data = crypto_check["result"]
                bot.send_message(
                    user_id,
                    f"✅ Чек успешно создан!\n\nСумма: {amount} {asset}\n"
                    f"ID чека: {check_data['check_id']}\n"
                    f"Ссылка: {check_data['bot_check_url']}"  # Используем bot_check_url вместо url
                )
            else:
                bot.send_message(
                    user_id,
                    "❌ Ошибка при создании чека. Администратор уже уведомлен. Пожалуйста, попробуйте позже."
                )
            
            del pending_payments[user_id]
            return

    bot.answer_callback_query(call.id, "Оплата не найдена. Попробуйте позже.", show_alert=True)
    logger.warning(f"Оплата для пользователя {user_id} не найдена.")

if __name__ == "__main__":
    logger.info("Бот запущен.")
    bot.polling(none_stop=True)
