import os
import telebot
from telebot import types
import sqlite3
import random
from datetime import datetime, timedelta
import string
import time
import re
import threading
from flask import Flask, jsonify

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get('TOKEN', '8965196111:AAFsNCnmRTVsAUsSIKkZiIDCCzB6HSe_-OQ')
ADMIN_ID = int(os.environ.get('ADMIN_ID', '5706071030'))
PORT = int(os.environ.get('PORT', 10000))

print(f"🔑 Токен загружен: {'Да' if TOKEN else 'Нет'}")
print(f"👑 Админ ID: {ADMIN_ID}")
print(f"🌐 Порт: {PORT}")

bot = telebot.TeleBot(TOKEN)

# ========== ВЕБ-СЕРВЕР ==========
app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({
        "status": "online",
        "bot": "EXTRABET",
        "time": datetime.now().strftime("%H:%M:%S")
    })

@app.route('/ping')
def ping():
    return "pong"

@app.route('/health')
def health():
    return jsonify({"status": "healthy"})

def run_web_server():
    print(f"🌐 Запуск веб-сервера на порту {PORT}...")
    app.run(host='0.0.0.0', port=PORT, debug=False)

# ========== САМО-ПИНГ ==========
def self_ping():
    time.sleep(60)
    print("🔄 Само-пинг активирован")
    while True:
        try:
            import requests
            response = requests.get(f'http://localhost:{PORT}/ping', timeout=10)
            print(f"✅ Само-пинг: {response.status_code}")
        except Exception as e:
            print(f"⚠️ Само-пинг ошибка: {e}")
        time.sleep(600)

# ========== ВРЕМЕННЫЕ ХРАНИЛИЩА ==========
user_match_creation = {}
user_bet_amount = {}

# ========== БАЗА ДАННЫХ ==========
def init_db():
    print("📦 Инициализация базы данных...")
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY,
                  username TEXT,
                  balance INTEGER DEFAULT 1000,
                  freebets INTEGER DEFAULT 0,
                  total_bets INTEGER DEFAULT 0,
                  wins INTEGER DEFAULT 0)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS matches
                 (match_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  team1 TEXT,
                  team2 TEXT,
                  match_date TEXT,
                  coefficient1 REAL DEFAULT 2.5,
                  coefficient2 REAL DEFAULT 2.5,
                  coefficient_draw REAL DEFAULT 3.5,
                  status TEXT DEFAULT 'upcoming',
                  winner TEXT,
                  score TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS bets
                 (bet_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  match_id INTEGER,
                  team TEXT,
                  amount INTEGER,
                  bet_type TEXT DEFAULT 'money',
                  coefficient REAL DEFAULT 2.0,
                  status TEXT DEFAULT 'pending')''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS promocodes
                 (promo_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  code TEXT UNIQUE,
                  freebet_amount INTEGER,
                  max_uses INTEGER,
                  used_count INTEGER DEFAULT 0,
                  is_active INTEGER DEFAULT 1,
                  created_by INTEGER,
                  created_date TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS used_promos
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  promo_code TEXT,
                  used_date TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS photos
                 (photo_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  photo_type TEXT,
                  file_id TEXT,
                  added_date TEXT)''')
    
    conn.commit()
    conn.close()
    print("✅ База данных готова")

# ========== ГЕНЕРАЦИЯ КОДА ==========
def generate_promo_code(length=8):
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

# ========== БЕЗОПАСНЫЕ ФУНКЦИИ ==========
def safe_send_message(chat_id, text, reply_markup=None):
    try:
        return bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception as e:
        print(f"Ошибка отправки в {chat_id}: {e}")
        time.sleep(1)
        try:
            return bot.send_message(chat_id, text, reply_markup=reply_markup)
        except:
            return None

def safe_edit_message(text, chat_id, message_id, reply_markup=None):
    try:
        return bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup)
    except Exception as e:
        print(f"Ошибка редактирования: {e}")
        return None

def get_photo(photo_type):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT file_id FROM photos WHERE photo_type=? ORDER BY photo_id DESC LIMIT 1", (photo_type,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

def notify_user(user_id, match_info, team, amount, coefficient, bet_type, status, winnings=0):
    if status == "won":
        if bet_type in ('freebet', 'freebet_active'):
            caption = f"🏆 ФРИБЕТ ВЫИГРАЛ!\n\n📊 {match_info}\n✅ Ваш прогноз: {team}\n💰 Сумма выигрыша: {amount} монет\n\nПоздравляем с победой!"
        else:
            caption = f"🎉 СТАВКА СЫГРАЛА!\n\n📊 {match_info}\n✅ Ваш прогноз: {team}\n💵 Сумма ставки: {amount} монет\n📈 Коэффициент: x{coefficient}\n💰 ВЫИГРЫШ: {winnings} монет\n\nОтличный результат!"
    else:
        if bet_type in ('freebet', 'freebet_active'):
            caption = f"😞 ФРИБЕТ ПРОИГРАЛ\n\n📊 {match_info}\n❌ Ваш прогноз: {team}\n💰 Сумма: {amount} монет\n\nНе повезло, но ты справишься!"
        else:
            caption = f"💔 СТАВКА ПРОИГРАЛА\n\n📊 {match_info}\n❌ Ваш прогноз: {team}\n💵 Сумма ставки: {amount} монет\n📈 Коэффициент: x{coefficient}\n\nВ следующий раз повезёт больше!"
    
    photo_sent = False
    if status == "won":
        win_photo = get_photo('win')
        if win_photo:
            try:
                bot.send_photo(user_id, win_photo, caption=caption)
                photo_sent = True
            except:
                pass
    else:
        lose_photo = get_photo('lose')
        if lose_photo:
            try:
                bot.send_photo(user_id, lose_photo, caption=caption)
                photo_sent = True
            except:
                pass
    
    if not photo_sent:
        safe_send_message(user_id, caption)

# ========== КЛАВИАТУРЫ ==========
def admin_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("🎮 Управление матчами", "🎫 Промокоды")
    kb.add("💰 Выдать фрибет", "📊 Статистика бота")
    kb.add("👥 Пользователи", "🏒 Матчи")
    kb.add("👤 Профиль", "📋 Меню")
    kb.add("📸 Установить фото")
    return kb

def main_keyboard(user_id=None):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("🏒 Матчи", "👤 Профиль")
    kb.add("💰 Баланс", "📊 Статистика")
    kb.add("🎫 Активировать промокод", "🎁 Мои фрибеты")
    kb.add("🆘 Получить бонус")
    if user_id == ADMIN_ID:
        kb.add("🔧 Админ-панель")
    return kb

def admin_promo_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("➕ Создать промокод", callback_data="admin_create_promo"),
        types.InlineKeyboardButton("📋 Список промокодов", callback_data="admin_list_promos")
    )
    kb.add(
        types.InlineKeyboardButton("🗑 Удалить промокод", callback_data="admin_delete_promo_list"),
        types.InlineKeyboardButton("📊 Статистика промо", callback_data="admin_promo_stats")
    )
    kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_admin_main"))
    return kb

def admin_matches_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("➕ Создать матч", callback_data="admin_create_match"),
        types.InlineKeyboardButton("📋 Все матчи", callback_data="admin_all_matches")
    )
    kb.add(
        types.InlineKeyboardButton("✅ Установить результат", callback_data="admin_set_result"),
        types.InlineKeyboardButton("🗑 Удалить матч", callback_data="admin_delete_match_list")
    )
    kb.add(
        types.InlineKeyboardButton("🎲 Рассчитать все", callback_data="admin_calculate_all"),
        types.InlineKeyboardButton("🔙 Назад", callback_data="back_admin_main")
    )
    return kb

def matches_keyboard():
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT match_id, team1, team2, match_date, coefficient1, coefficient2, coefficient_draw FROM matches WHERE status='upcoming'")
    matches = c.fetchall()
    conn.close()
    kb = types.InlineKeyboardMarkup(row_width=1)
    for match in matches:
        kb.add(types.InlineKeyboardButton(
            f"⚔ {match[1]} (x{match[4]}) vs {match[2]} (x{match[5]}) | Ничья (x{match[6]}) | {match[3]}",
            callback_data=f"match_{match[0]}"
        ))
    kb.add(types.InlineKeyboardButton("🔄 Обновить", callback_data="refresh_matches"))
    kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
    return kb

def bet_keyboard(match_id):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT team1, team2, coefficient1, coefficient2, coefficient_draw FROM matches WHERE match_id=?", (match_id,))
    match = c.fetchone()
    conn.close()
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton(f"✅ {match[0]} x{match[2]}", callback_data=f"betsum_{match_id}_{match[0]}"),
        types.InlineKeyboardButton(f"✅ {match[1]} x{match[3]}", callback_data=f"betsum_{match_id}_{match[1]}")
    )
    kb.add(
        types.InlineKeyboardButton(f"🤝 Ничья x{match[4]}", callback_data=f"betsum_{match_id}_Ничья"),
        types.InlineKeyboardButton("🔙 К матчам", callback_data="show_matches")
    )
    return kb

def sum_keyboard(match_id, team):
    kb = types.InlineKeyboardMarkup(row_width=3)
    kb.add(
        types.InlineKeyboardButton("100", callback_data=f"bet_{match_id}_{team}_100"),
        types.InlineKeyboardButton("500", callback_data=f"bet_{match_id}_{team}_500"),
        types.InlineKeyboardButton("1000", callback_data=f"bet_{match_id}_{team}_1000")
    )
    kb.add(
        types.InlineKeyboardButton("2500", callback_data=f"bet_{match_id}_{team}_2500"),
        types.InlineKeyboardButton("5000", callback_data=f"bet_{match_id}_{team}_5000"),
        types.InlineKeyboardButton("Своя сумма", callback_data=f"custom_{match_id}_{team}")
    )
    kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data=f"match_{match_id}"))
    return kb

# ========== КОМАНДЫ ==========
@bot.message_handler(commands=['start'])
def start(message):
    init_db()
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    conn.close()
    print(f"👤 Новый пользователь: {username} (ID: {user_id})")
    welcome_text = "🏒 ДОБРО ПОЖАЛОВАТЬ В EXTRABET!\n\n💰 Ваш стартовый баланс: 1000 монет\n\n📋 Меню находится снизу"
    if user_id == ADMIN_ID:
        safe_send_message(message.chat.id, welcome_text, admin_keyboard())
    else:
        safe_send_message(message.chat.id, welcome_text, main_keyboard(user_id))

@bot.message_handler(func=lambda m: m.text == "📸 Установить фото")
def set_photo_button(message):
    if message.from_user.id != ADMIN_ID:
        safe_send_message(message.chat.id, "⛔ Только админ может менять фото!")
        return
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🏆 Фото победы", callback_data="set_photo_win"),
        types.InlineKeyboardButton("💔 Фото поражения", callback_data="set_photo_lose")
    )
    kb.add(types.InlineKeyboardButton("📋 Показать фото", callback_data="show_photos"))
    safe_send_message(message.chat.id, "📸 Управление фотографиями\n\nВыберите тип фото для замены:", kb)

@bot.message_handler(commands=['setphoto'])
def set_photo_command(message):
    set_photo_button(message)

@bot.message_handler(func=lambda m: m.text == "📋 Меню")
def show_menu(message):
    user_id = message.from_user.id
    if user_id == ADMIN_ID:
        safe_send_message(message.chat.id, "📋 Меню находится снизу\nВыберите раздел:", admin_keyboard())
    else:
        safe_send_message(message.chat.id, "📋 Меню находится снизу\nВыберите раздел:", main_keyboard(user_id))

@bot.message_handler(func=lambda m: m.text == "🔧 Админ-панель")
def admin_panel(message):
    if message.from_user.id == ADMIN_ID:
        safe_send_message(message.chat.id, "👑 Админ-панель управления\n📋 Меню снизу", admin_keyboard())
    else:
        safe_send_message(message.chat.id, "⛔ Доступ запрещен!")

@bot.message_handler(func=lambda m: m.text == "🆘 Получить бонус")
def get_bonus(message):
    user_id = message.from_user.id
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()
    if not user:
        safe_send_message(message.chat.id, "❌ Используйте /start")
        conn.close()
        return
    balance = user[0]
    if balance != 0:
        safe_send_message(message.chat.id, f"❌ Бонус недоступен!\nВаш баланс: {balance} монет")
    else:
        c.execute("UPDATE users SET balance = balance + 50 WHERE user_id=?", (user_id,))
        conn.commit()
        safe_send_message(message.chat.id, "✅ Бонус получен!\n💰 Начислено: 50 монет")
    conn.close()

@bot.message_handler(func=lambda m: m.text == "🎫 Промокоды")
def promo_menu_handler(message):
    if message.from_user.id != ADMIN_ID:
        safe_send_message(message.chat.id, "⛔ Доступ запрещен!")
        return
    safe_send_message(message.chat.id, "🎫 Управление промокодами", reply_markup=admin_promo_keyboard())

@bot.message_handler(func=lambda m: m.text == "🎫 Активировать промокод")
def activate_promo_start(message):
    msg = safe_send_message(message.chat.id, "🎫 Отправьте промокод:")
    bot.register_next_step_handler(msg, process_activate_promo)

def process_activate_promo(message):
    user_id = message.from_user.id
    code = message.text.strip().upper()
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT * FROM promocodes WHERE code=? AND is_active=1", (code,))
    promo = c.fetchone()
    if not promo:
        safe_send_message(message.chat.id, "❌ Промокод не найден!")
        conn.close()
        return
    if promo[3] <= promo[4]:
        safe_send_message(message.chat.id, "❌ Лимит исчерпан!")
        conn.close()
        return
    c.execute("SELECT id FROM used_promos WHERE user_id=? AND promo_code=?", (user_id, code))
    if c.fetchone():
        safe_send_message(message.chat.id, "❌ Вы уже использовали!")
        conn.close()
        return
    freebet_amount = promo[2]
    c.execute("INSERT INTO used_promos (user_id, promo_code, used_date) VALUES (?, ?, ?)", (user_id, code, datetime.now().strftime("%d.%m.%Y %H:%M")))
    c.execute("UPDATE promocodes SET used_count = used_count + 1 WHERE code=?", (code,))
    c.execute("UPDATE users SET freebets = freebets + 1 WHERE user_id=?", (user_id,))
    c.execute("INSERT INTO bets (user_id, match_id, team, amount, bet_type, coefficient) VALUES (?, 0, 'freebet', ?, 'freebet', 1.0)", (user_id, freebet_amount))
    if promo[4] + 1 >= promo[3]:
        c.execute("UPDATE promocodes SET is_active=0 WHERE code=?", (code,))
    conn.commit()
    conn.close()
    safe_send_message(message.chat.id, f"🎁 Фрибет активирован!\n💰 {freebet_amount} монет")

@bot.message_handler(func=lambda m: m.text == "🎁 Мои фрибеты")
def show_freebets(message):
    user_id = message.from_user.id
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT bet_id, amount FROM bets WHERE user_id=? AND bet_type='freebet' AND status='pending'", (user_id,))
    freebets = c.fetchall()
    conn.close()
    if freebets:
        kb = types.InlineKeyboardMarkup(row_width=1)
        text = "🎁 Ваши фрибеты:\n\n"
        for fb in freebets:
            text += f"🆔 #{fb[0]}: {fb[1]} монет\n"
            kb.add(types.InlineKeyboardButton(f"Использовать #{fb[0]} ({fb[1]}💰)", callback_data=f"use_freebet_{fb[0]}"))
        safe_send_message(message.chat.id, text, kb)
    else:
        safe_send_message(message.chat.id, "🎁 Нет фрибетов")

@bot.message_handler(func=lambda m: m.text == "🎮 Управление матчами")
def manage_matches(message):
    if message.from_user.id == ADMIN_ID:
        safe_send_message(message.chat.id, "🎮 Управление матчами", admin_matches_keyboard())

@bot.message_handler(func=lambda m: m.text == "💰 Выдать фрибет")
def give_freebet_start(message):
    if message.from_user.id != ADMIN_ID: return
    msg = safe_send_message(message.chat.id, "🎁 ID и сумма:\nПример: 123456789 500")
    bot.register_next_step_handler(msg, process_freebet)

def process_freebet(message):
    try:
        parts = message.text.split()
        target_id = int(parts[0])
        amount = int(parts[1])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("UPDATE users SET freebets = freebets + 1 WHERE user_id=?", (target_id,))
        c.execute("INSERT INTO bets (user_id, match_id, team, amount, bet_type) VALUES (?, 0, 'freebet', ?, 'freebet')", (target_id, amount))
        conn.commit()
        conn.close()
        safe_send_message(message.chat.id, f"✅ Выдано!")
    except:
        safe_send_message(message.chat.id, "❌ Ошибка!")

@bot.message_handler(func=lambda m: m.text == "👤 Профиль")
def profile_handler(message):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (message.from_user.id,))
    user = c.fetchone()
    conn.close()
    if user:
        win_rate = (user[5] / user[4] * 100) if user[4] > 0 else 0
        text = f"👤 Профиль\n\n💰 Баланс: {user[2]}\n🎁 Фрибеты: {user[3]}\n📊 Ставок: {user[4]}\n✅ Побед: {user[5]}\n📈 Винрейт: {win_rate:.1f}%"
        safe_send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.text == "💰 Баланс")
def balance_handler(message):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT balance, freebets FROM users WHERE user_id=?", (message.from_user.id,))
    data = c.fetchone()
    conn.close()
    safe_send_message(message.chat.id, f"💰 Баланс: {data[0]}\n🎁 Фрибеты: {data[1]}")

@bot.message_handler(func=lambda m: m.text == "📊 Статистика")
def stats_handler(message):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT b.team, b.amount, b.status, m.team1, m.team2 FROM bets b JOIN matches m ON b.match_id=m.match_id WHERE b.user_id=? ORDER BY b.bet_id DESC LIMIT 5", (message.from_user.id,))
    bets = c.fetchall()
    conn.close()
    text = "📊 Последние ставки:\n\n" if bets else "Нет ставок"
    for bet in bets:
        emoji = "✅" if bet[2]=="won" else "❌" if bet[2]=="lost" else "⏳"
        text += f"{emoji} {bet[3]} vs {bet[4]}\n   {bet[1]} на {bet[0]}\n\n"
    safe_send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.text == "🏒 Матчи")
def show_matches_handler(message):
    safe_send_message(message.chat.id, "🎯 Доступные матчи:", matches_keyboard())

@bot.message_handler(func=lambda m: m.text == "📊 Статистика бота")
def bot_stats(message):
    if message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM matches WHERE status='upcoming'")
    active_matches = c.fetchone()[0]
    conn.close()
    safe_send_message(message.chat.id, f"📊 Статистика\n👥 Пользователей: {total_users}\n🏒 Матчей: {active_matches}")

@bot.message_handler(func=lambda m: m.text == "👥 Пользователи")
def users_list(message):
    if message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT username, balance FROM users ORDER BY balance DESC LIMIT 10")
    users = c.fetchall()
    conn.close()
    text = "👥 Топ-10:\n\n"
    for i, u in enumerate(users, 1):
        text += f"{i}. {u[0]} | 💰{u[1]}\n"
    safe_send_message(message.chat.id, text)

# ========== ОБРАБОТЧИК ФОТО ==========
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID: return
    if user_id in user_match_creation and 'photo_type' in user_match_creation[user_id]:
        photo_type = user_match_creation[user_id]['photo_type']
        file_id = message.photo[-1].file_id
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("DELETE FROM photos WHERE photo_type=?", (photo_type,))
        c.execute("INSERT INTO photos (photo_type, file_id, added_date) VALUES (?, ?, ?)", (photo_type, file_id, datetime.now().strftime("%d.%m.%Y %H:%M")))
        conn.commit()
        conn.close()
        type_name = "🏆 ПОБЕДЫ" if photo_type == 'win' else "💔 ПОРАЖЕНИЯ"
        safe_send_message(message.chat.id, f"✅ Фото для {type_name} обновлено!")
        del user_match_creation[user_id]

# ========== CALLBACK ОБРАБОТЧИКИ ==========
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    
    if call.data == "set_photo_win":
        if user_id != ADMIN_ID: return
        user_match_creation[user_id] = {'photo_type': 'win'}
        bot.answer_callback_query(call.id, "Отправьте фото для ПОБЕДЫ")
        safe_send_message(call.message.chat.id, "📸 Отправьте фото для 🏆 ПОБЕДЫ")
    
    elif call.data == "set_photo_lose":
        if user_id != ADMIN_ID: return
        user_match_creation[user_id] = {'photo_type': 'lose'}
        bot.answer_callback_query(call.id, "Отправьте фото для ПОРАЖЕНИЯ")
        safe_send_message(call.message.chat.id, "📸 Отправьте фото для 💔 ПОРАЖЕНИЯ")
    
    elif call.data == "show_photos":
        if user_id != ADMIN_ID: return
        win_photo = get_photo('win')
        lose_photo = get_photo('lose')
        if win_photo:
            safe_send_message(call.message.chat.id, "🏆 Фото ПОБЕДЫ:")
            try: bot.send_photo(call.message.chat.id, win_photo)
            except: pass
        if lose_photo:
            safe_send_message(call.message.chat.id, "💔 Фото ПОРАЖЕНИЯ:")
            try: bot.send_photo(call.message.chat.id, lose_photo)
            except: pass
        if not win_photo and not lose_photo:
            bot.answer_callback_query(call.id, "Фото не установлены!")
    
    elif call.data == "admin_create_promo":
        if user_id != ADMIN_ID: return
        msg = safe_send_message(call.message.chat.id, "🎫 Создание промокода\nОтправьте: СУММА КОЛ-ВО [КОД]\nПример: 500 10 или 1000 5 HOCKEY")
        bot.register_next_step_handler(msg, admin_create_promo_process)
    
    elif call.data == "admin_list_promos":
        if user_id != ADMIN_ID: return
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT * FROM promocodes ORDER BY created_date DESC LIMIT 10")
        promos = c.fetchall()
        conn.close()
        text = "📋 Промокоды:\n\n" if promos else "❌ Нет промокодов"
        for p in promos:
            status = "🟢" if p[5] else "🔴"
            text += f"{status} {p[1]} | 💰{p[2]} | {p[4]}/{p[3]}\n"
        safe_edit_message(text, call.message.chat.id, call.message.message_id)
    
    elif call.data == "admin_delete_promo_list":
        if user_id != ADMIN_ID: return
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT promo_id, code, freebet_amount FROM promocodes WHERE is_active=1")
        promos = c.fetchall()
        conn.close()
        if promos:
            kb = types.InlineKeyboardMarkup(row_width=1)
            for p in promos:
                kb.add(types.InlineKeyboardButton(f"🗑 {p[1]} ({p[2]}💰)", callback_data=f"admin_delete_promo_{p[0]}"))
            kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_admin_promo"))
            safe_edit_message("Выберите для удаления:", call.message.chat.id, call.message.message_id, kb)
        else:
            bot.answer_callback_query(call.id, "Нет активных промокодов")
    
    elif call.data.startswith("admin_delete_promo_"):
        if user_id != ADMIN_ID: return
        promo_id = int(call.data.split("_")[3])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("UPDATE promocodes SET is_active=0 WHERE promo_id=?", (promo_id,))
        conn.commit()
        conn.close()
        bot.answer_callback_query(call.id, "Удалён!")
        safe_edit_message("✅ Промокод деактивирован!", call.message.chat.id, call.message.message_id)
    
    elif call.data == "admin_promo_stats":
        if user_id != ADMIN_ID: return
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT COUNT(*), SUM(used_count) FROM promocodes")
        total, used = c.fetchone()
        conn.close()
        safe_edit_message(f"📊 Промокоды\n📝 Всего: {total}\n👥 Использований: {used or 0}", call.message.chat.id, call.message.message_id)
    
    elif call.data == "back_admin_promo":
        safe_edit_message("🎫 Управление промокодами:", call.message.chat.id, call.message.message_id, admin_promo_keyboard())
    
    elif call.data == "back_admin_main":
        safe_edit_message("👑 Админ-панель:", call.message.chat.id, call.message.message_id)
    
    elif call.data == "admin_create_match":
        if user_id != ADMIN_ID: return
        msg = safe_send_message(call.message.chat.id, "➕ Создание матча\nОтправьте:\nКоманда1 vs Команда2 ДД.ММ.ГГГГ ЧЧ:ММ коэф1 коэф2 коэф_ничьей")
        bot.register_next_step_handler(msg, admin_create_match)
    
    elif call.data == "admin_set_result":
        if user_id != ADMIN_ID: return
        msg = safe_send_message(call.message.chat.id, "✅ Результат\nОтправьте: ID Победитель Счёт")
        bot.register_next_step_handler(msg, admin_set_result)
    
    elif call.data == "admin_calculate_all":
        if user_id != ADMIN_ID: return
        calculate_all_matches()
        bot.answer_callback_query(call.id, "✅ Рассчитано!")
        safe_send_message(call.message.chat.id, "✅ Все матчи рассчитаны!")
    
    elif call.data == "show_matches":
        safe_edit_message("🎯 Матчи:", call.message.chat.id, call.message.message_id, matches_keyboard())
    
    elif call.data == "refresh_matches":
        safe_edit_message("🔄 Обновлено!", call.message.chat.id, call.message.message_id, matches_keyboard())
    
    elif call.data == "back_main":
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        safe_send_message(call.message.chat.id, "📋 Меню снизу:", main_keyboard(call.from_user.id))
    
    elif call.data.startswith("match_"):
        match_id = int(call.data.split("_")[1])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT team1, team2, match_date, coefficient1, coefficient2, coefficient_draw FROM matches WHERE match_id=?", (match_id,))
        match = c.fetchone()
        conn.close()
        safe_edit_message(f"⚔ {match[0]} vs {match[1]}\n📅 {match[2]}\nКФ: {match[3]}/{match[4]}/{match[5]}", call.message.chat.id, call.message.message_id, bet_keyboard(match_id))
    
    elif call.data.startswith("betsum_"):
        data = call.data.split("_", 2)
        match_id = int(data[1])
        team = data[2]
        safe_edit_message(f"💰 Сумма на {team}:", call.message.chat.id, call.message.message_id, sum_keyboard(match_id, team))
    
    elif call.data.startswith("custom_"):
        data = call.data.split("_", 2)
        match_id = int(data[1])
        team = data[2]
        msg = safe_send_message(call.message.chat.id, f"💵 Введите сумму на {team}:")
        user_bet_amount[user_id] = {'match_id': match_id, 'team': team}
        bot.register_next_step_handler(msg, process_custom_bet)
    
    elif call.data.startswith("bet_"):
        parts = call.data.split("_")
        match_id = int(parts[1])
        team = "_".join(parts[2:-1])
        amount = int(parts[-1])
        place_bet(call, user_id, match_id, team, amount)
    
    elif call.data.startswith("use_freebet_"):
        bet_id = int(call.data.split("_")[2])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT amount FROM bets WHERE bet_id=? AND bet_type='freebet' AND status='pending'", (bet_id,))
        freebet = c.fetchone()
        conn.close()
        if freebet:
            conn = sqlite3.connect('hockey_bets.db')
            c = conn.cursor()
            c.execute("SELECT match_id, team1, team2, match_date FROM matches WHERE status='upcoming'")
            matches = c.fetchall()
            conn.close()
            if matches:
                kb = types.InlineKeyboardMarkup(row_width=1)
                text = f"🎯 Выберите матч\n💰 Номинал: {freebet[0]}\n\n"
                for match in matches:
                    kb.add(types.InlineKeyboardButton(f"⚔ {match[1]} vs {match[2]}", callback_data=f"freebet_match_{bet_id}_{match[0]}"))
                safe_edit_message(text, call.message.chat.id, call.message.message_id, kb)
            else:
                bot.answer_callback_query(call.id, "❌ Нет матчей!")
    
    elif call.data.startswith("freebet_match_"):
        parts = call.data.split("_")
        bet_id = int(parts[2])
        match_id = int(parts[3])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT team1, team2 FROM matches WHERE match_id=?", (match_id,))
        match = c.fetchone()
        conn.close()
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton(f"✅ {match[0]}", callback_data=f"freebet_team_{bet_id}_{match_id}_{match[0]}"),
            types.InlineKeyboardButton(f"✅ {match[1]}", callback_data=f"freebet_team_{bet_id}_{match_id}_{match[1]}")
        )
        kb.add(
            types.InlineKeyboardButton(f"🤝 Ничья", callback_data=f"freebet_team_{bet_id}_{match_id}_Ничья"),
            types.InlineKeyboardButton("🔙 Назад", callback_data=f"use_freebet_{bet_id}")
        )
        safe_edit_message(f"⚔ {match[0]} vs {match[1]}\n\nИсход:", call.message.chat.id, call.message.message_id, kb)
    
    elif call.data.startswith("freebet_team_"):
        parts = call.data.split("_", 4)
        bet_id = int(parts[2])
        match_id = int(parts[3])
        team = parts[4]
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT amount FROM bets WHERE bet_id=? AND bet_type='freebet' AND status='pending'", (bet_id,))
        freebet = c.fetchone()
        if freebet:
            c.execute("UPDATE bets SET match_id=?, team=?, bet_type='freebet_active' WHERE bet_id=?", (match_id, team, bet_id))
            c.execute("UPDATE users SET freebets = freebets - 1 WHERE user_id=?", (call.from_user.id,))
            conn.commit()
            safe_edit_message(f"✅ Фрибет использован!\nМатч #{match_id}\n{team}\n💰 {freebet[0]} монет", call.message.chat.id, call.message.message_id)
        conn.close()

# ========== ФУНКЦИИ ==========
def admin_create_promo_process(message):
    if message.from_user.id != ADMIN_ID: return
    try:
        parts = message.text.split()
        if len(parts) == 2:
            amount = int(parts[0])
            max_uses = int(parts[1])
            code = generate_promo_code()
        else:
            amount = int(parts[0])
            max_uses = int(parts[1])
            code = parts[2].upper()
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("INSERT INTO promocodes (code, freebet_amount, max_uses, created_by, created_date) VALUES (?, ?, ?, ?, ?)", (code, amount, max_uses, ADMIN_ID, datetime.now().strftime("%d.%m.%Y %H:%M")))
        conn.commit()
        conn.close()
        safe_send_message(message.chat.id, f"✅ Создан!\n🎫 {code}\n💰 {amount} монет\n👥 {max_uses} исп.")
    except:
        safe_send_message(message.chat.id, "❌ Ошибка!")

def admin_create_match(message):
    if message.from_user.id != ADMIN_ID: return
    try:
        text = message.text
        left_part, right_part = text.split(' vs ', 1)
        team1 = left_part.strip()
        date_match = re.search(r'\d{2}\.\d{2}\.\d{4}\s\d{2}:\d{2}', right_part)
        date_str = date_match.group()
        team2 = right_part[:date_match.start()].strip()
        after = right_part[date_match.end():].strip().split()
        coef1 = float(after[0]) if after else 2.5
        coef2 = float(after[1]) if len(after)>1 else 2.5
        coef_draw = float(after[2]) if len(after)>2 else 3.5
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("INSERT INTO matches (team1, team2, match_date, coefficient1, coefficient2, coefficient_draw) VALUES (?, ?, ?, ?, ?, ?)", (team1, team2, date_str, coef1, coef2, coef_draw))
        conn.commit()
        conn.close()
        safe_send_message(message.chat.id, f"✅ Матч создан!\n⚔ {team1} vs {team2}\n📅 {date_str}")
    except Exception as e:
        safe_send_message(message.chat.id, f"❌ Ошибка: {e}")

def admin_set_result(message):
    if message.from_user.id != ADMIN_ID: return
    try:
        parts = message.text.split()
        match_id = int(parts[0])
        winner = parts[1]
        score = parts[2]
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("UPDATE matches SET status='finished', winner=?, score=? WHERE match_id=?", (winner, score, match_id))
        c.execute("SELECT bet_id, user_id, team, amount, bet_type, coefficient FROM bets WHERE match_id=? AND status='pending'", (match_id,))
        bets = c.fetchall()
        for bet in bets:
            bet_id, uid, team, amount, bet_type, coefficient = bet
            if team == winner:
                winnings = int(amount * coefficient) if bet_type not in ('freebet','freebet_active') else amount
                c.execute("UPDATE users SET balance = balance + ?, wins = wins + 1 WHERE user_id=?", (winnings, uid))
                c.execute("UPDATE bets SET status='won' WHERE bet_id=?", (bet_id,))
                notify_user(uid, f"Матч #{match_id}", team, amount, coefficient, bet_type, "won", winnings)
            else:
                c.execute("UPDATE bets SET status='lost' WHERE bet_id=?", (bet_id,))
                notify_user(uid, f"Матч #{match_id}", team, amount, coefficient, bet_type, "lost")
        conn.commit()
        conn.close()
        safe_send_message(message.chat.id, f"✅ Результат установлен!\n#{match_id} | {winner} | {score}")
    except Exception as e:
        safe_send_message(message.chat.id, f"❌ Ошибка: {e}")

def calculate_all_matches():
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT match_id, team1, team2 FROM matches WHERE status='upcoming'")
    matches = c.fetchall()
    for match in matches:
        winner = random.choice([match[1], match[2], "Ничья"])
        score = f"{random.randint(1,5)}:{random.randint(1,5)}" if winner=="Ничья" else f"{random.randint(1,7)}:{random.randint(0,6)}"
        c.execute("UPDATE matches SET status='finished', winner=?, score=? WHERE match_id=?", (winner, score, match[0]))
        c.execute("SELECT bet_id, user_id, team, amount, bet_type, coefficient FROM bets WHERE match_id=? AND status='pending'", (match[0],))
        for bet in c.fetchall():
            bet_id, uid, team, amount, bet_type, coefficient = bet
            if team == winner:
                winnings = int(amount * coefficient) if bet_type not in ('freebet','freebet_active') else amount
                c.execute("UPDATE users SET balance = balance + ?, wins = wins + 1 WHERE user_id=?", (winnings, uid))
                c.execute("UPDATE bets SET status='won' WHERE bet_id=?", (bet_id,))
                notify_user(uid, f"Матч #{match[0]}", team, amount, coefficient, bet_type, "won", winnings)
            else:
                c.execute("UPDATE bets SET status='lost' WHERE bet_id=?", (bet_id,))
                notify_user(uid, f"Матч #{match[0]}", team, amount, coefficient, bet_type, "lost")
    conn.commit()
    conn.close()

def place_bet(call, user_id, match_id, team, amount):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    balance = c.fetchone()
    c.execute("SELECT team1, coefficient1, coefficient2, coefficient_draw FROM matches WHERE match_id=?", (match_id,))
    match = c.fetchone()
    if team == match[0]: coefficient = match[1]
    elif team == match[2]: coefficient = match[2]
    else: coefficient = match[3]
    if balance and balance[0] >= amount:
        c.execute("UPDATE users SET balance = balance - ?, total_bets = total_bets + 1 WHERE user_id=?", (amount, user_id))
        c.execute("INSERT INTO bets (user_id, match_id, team, amount, coefficient) VALUES (?, ?, ?, ?, ?)", (user_id, match_id, team, amount, coefficient))
        conn.commit()
        bot.answer_callback_query(call.id, "✅ Принято!")
        safe_edit_message(f"✅ Ставка!\n🎯 {team}\n💰 {amount} (x{coefficient})\n💵 Баланс: {balance[0]-amount}", call.message.chat.id, call.message.message_id)
    else:
        bot.answer_callback_query(call.id, "❌ Недостаточно средств!", show_alert=True)
    conn.close()

def process_custom_bet(message):
    user_id = message.from_user.id
    try:
        amount = int(message.text)
        data = user_bet_amount.get(user_id)
        if data:
            conn = sqlite3.connect('hockey_bets.db')
            c = conn.cursor()
            c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
            balance = c.fetchone()[0]
            conn.close()
            if balance >= amount:
                place_bet_direct(user_id, data['match_id'], data['team'], amount, message.chat.id)
            else:
                safe_send_message(message.chat.id, "❌ Недостаточно средств!")
        del user_bet_amount[user_id]
    except:
        safe_send_message(message.chat.id, "❌ Введите сумму!")

def place_bet_direct(user_id, match_id, team, amount, chat_id):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT team1, coefficient1, coefficient2, coefficient_draw FROM matches WHERE match_id=?", (match_id,))
    match = c.fetchone()
    if team == match[0]: coefficient = match[1]
    elif team == match[2]: coefficient = match[2]
    else: coefficient = match[3]
    c.execute("UPDATE users SET balance = balance - ?, total_bets = total_bets + 1 WHERE user_id=?", (amount, user_id))
    c.execute("INSERT INTO bets (user_id, match_id, team, amount, coefficient) VALUES (?, ?, ?, ?, ?)", (user_id, match_id, team, amount, coefficient))
    conn.commit()
    conn.close()
    safe_send_message(chat_id, f"✅ Ставка принята!\n💰 {amount} на {team}")

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    print("=" * 50)
    print("🏒 EXTRABET ЗАПУСКАЕТСЯ...")
    print("=" * 50)
    
    # База данных
    init_db()
    
    # Веб-сервер
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    print(f"🌐 Веб-сервер на порту {PORT}")
    
    # Само-пинг
    ping_thread = threading.Thread(target=self_ping, daemon=True)
    ping_thread.start()
    
    # Запуск бота
    print("🤖 Бот запущен! Жду сообщения...")
    print("=" * 50)
    
    while True:
        try:
            bot.polling(none_stop=True, timeout=30)
        except Exception as e:
            print(f"⚠️ Ошибка: {e}")
            print("🔄 Перезапуск через 5 сек...")
            time.sleep(5)
