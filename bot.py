import os
import telebot
from telebot import types
import sqlite3
import random
from datetime import datetime, timezone, timedelta
import string
import time
import re
import threading
from flask import Flask, jsonify

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get('TOKEN', '8965196111:AAFsNCnmRTVsAUsSIKkZiIDCCzB6HSe_-OQ')
ADMIN_ID = int(os.environ.get('ADMIN_ID', '5706071030'))
PORT = int(os.environ.get('PORT', 10000))

bot = telebot.TeleBot(TOKEN)

# ========== МОСКОВСКОЕ ВРЕМЯ ==========
MSK = timezone(timedelta(hours=3))
def msk_now():
    return datetime.now(MSK)

# ========== ВЕБ-СЕРВЕР ==========
app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({"status": "online"})

@app.route('/ping')
def ping():
    return "pong"

def run_web_server():
    app.run(host='0.0.0.0', port=PORT)

# ========== ХРАНИЛИЩА ==========
user_match_creation = {}
user_bet_amount = {}
blackjack_lobbies = {}
solo_games = {}

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, balance INTEGER DEFAULT 1000, freebets INTEGER DEFAULT 0, total_bets INTEGER DEFAULT 0, wins INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS matches (match_id INTEGER PRIMARY KEY AUTOINCREMENT, team1 TEXT, team2 TEXT, match_date TEXT, coefficient1 REAL DEFAULT 2.5, coefficient2 REAL DEFAULT 2.5, coefficient_draw REAL DEFAULT 3.5, status TEXT DEFAULT 'upcoming', winner TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS bets (bet_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, match_id INTEGER, team TEXT, amount INTEGER, bet_type TEXT DEFAULT 'money', coefficient REAL DEFAULT 2.0, status TEXT DEFAULT 'pending')''')
    c.execute('''CREATE TABLE IF NOT EXISTS promocodes (promo_id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, freebet_amount INTEGER, max_uses INTEGER, used_count INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1, created_by INTEGER, created_date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS used_promos (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, promo_code TEXT, used_date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS photos (photo_id INTEGER PRIMARY KEY AUTOINCREMENT, photo_type TEXT, file_id TEXT, added_date TEXT)''')
    conn.commit()
    conn.close()

# ========== АВТО-ЗАКРЫТИЕ ==========
def auto_close_matches():
    time.sleep(30)
    while True:
        try:
            conn = sqlite3.connect('hockey_bets.db')
            c = conn.cursor()
            now = msk_now().strftime("%d.%m.%Y %H:%M")
            c.execute("UPDATE matches SET status='closed' WHERE status='upcoming' AND match_date <= ?", (now,))
            if c.rowcount > 0: print(f"🔒 Закрыто: {c.rowcount}")
            conn.commit()
            conn.close()
        except: pass
        time.sleep(60)

# ========== ГЕНЕРАЦИЯ ==========
def generate_promo_code(length=8):
    return ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(length))

def safe_send(chat_id, text, reply_markup=None):
    try: return bot.send_message(chat_id, text, reply_markup=reply_markup)
    except: return None

def safe_edit(text, chat_id, message_id, reply_markup=None):
    try: return bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup)
    except: return None

def get_photo(t):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT file_id FROM photos WHERE photo_type=? LIMIT 1", (t,))
    r = c.fetchone()
    conn.close()
    return r[0] if r else None

# ========== КЛАВИАТУРЫ ==========
def admin_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("🎮 Матчи", "🎫 Промокоды", "💰 Фрибет", "📊 Статистика")
    kb.add("👥 Люди", "🏒 Ставки", "👤 Профиль", "♠ Блэкджек")
    kb.add("📋 Меню", "📸 Фото")
    return kb

def main_keyboard(user_id=None):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("🏒 Ставки", "👤 Профиль", "💰 Баланс", "📊 История")
    kb.add("🎫 Промокод", "🎁 Фрибеты", "🆘 Бонус", "♠ Блэкджек")
    if user_id == ADMIN_ID: kb.add("🔧 Админ")
    return kb

def matches_keyboard():
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    now = msk_now().strftime("%d.%m.%Y %H:%M")
    c.execute("SELECT match_id, team1, team2, match_date, coefficient1, coefficient2, coefficient_draw FROM matches WHERE status='upcoming' AND match_date > ?", (now,))
    matches = c.fetchall()
    conn.close()
    kb = types.InlineKeyboardMarkup(row_width=1)
    if matches:
        for m in matches:
            kb.add(types.InlineKeyboardButton(f"⚔ {m[1]} vs {m[2]} | {m[3]}", callback_data=f"match_{m[0]}"))
    else: kb.add(types.InlineKeyboardButton("❌ Нет матчей", callback_data="none"))
    kb.add(types.InlineKeyboardButton("🔄 Обновить", callback_data="refresh_matches"), types.InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
    return kb

def bet_keyboard(match_id):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT team1, team2, coefficient1, coefficient2, coefficient_draw FROM matches WHERE match_id=?", (match_id,))
    m = c.fetchone()
    conn.close()
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton(f"✅ {m[0]} x{m[2]}", callback_data=f"betsum_{match_id}_{m[0]}"), types.InlineKeyboardButton(f"✅ {m[1]} x{m[3]}", callback_data=f"betsum_{match_id}_{m[1]}"))
    kb.add(types.InlineKeyboardButton(f"🤝 Ничья x{m[4]}", callback_data=f"betsum_{match_id}_Ничья"), types.InlineKeyboardButton("🔙 Назад", callback_data="show_matches"))
    return kb

def sum_keyboard(match_id, team):
    kb = types.InlineKeyboardMarkup(row_width=3)
    for amt in [100, 500, 1000, 2500, 5000]:
        kb.add(types.InlineKeyboardButton(str(amt), callback_data=f"bet_{match_id}_{team}_{amt}"))
    kb.add(types.InlineKeyboardButton("Своя", callback_data=f"custom_{match_id}_{team}"))
    return kb

def admin_promo_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("➕ Создать", callback_data="adm_promo_create"), types.InlineKeyboardButton("📋 Список", callback_data="adm_promo_list"))
    kb.add(types.InlineKeyboardButton("🗑 Удалить", callback_data="adm_promo_del_list"), types.InlineKeyboardButton("🔙 Назад", callback_data="back_admin_main"))
    return kb

def admin_matches_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("➕ Создать", callback_data="adm_match_create"), types.InlineKeyboardButton("📋 Все", callback_data="adm_match_all"))
    kb.add(types.InlineKeyboardButton("👁 Ставки", callback_data="adm_match_bets"), types.InlineKeyboardButton("🏆 Победитель", callback_data="adm_match_winner"))
    kb.add(types.InlineKeyboardButton("🗑 Удалить", callback_data="adm_match_del_list"), types.InlineKeyboardButton("🔙 Назад", callback_data="back_admin_main"))
    return kb

def bj_menu_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🃏 С ботом", callback_data="bj_solo"), types.InlineKeyboardButton("👥 Создать лобби", callback_data="bj_lobby_create"))
    kb.add(types.InlineKeyboardButton("🔍 Найти лобби", callback_data="bj_lobby_find"))
    return kb

# ========== КОМАНДЫ ==========
@bot.message_handler(commands=['start'])
def start(message):
    init_db()
    uid = message.from_user.id
    uname = message.from_user.username or message.from_user.first_name
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (uid, uname))
    conn.commit()
    conn.close()
    safe_send(message.chat.id, f"🏒 EXTRABET!\n💰 Баланс: 1000\n🕐 МСК: {msk_now().strftime('%H:%M')}", admin_keyboard() if uid==ADMIN_ID else main_keyboard(uid))

# ========== КНОПКИ ==========
@bot.message_handler(func=lambda m: m.text == "🏒 Ставки")
def show_bets(message):
    safe_send(message.chat.id, "🎯 Матчи:", matches_keyboard())

@bot.message_handler(func=lambda m: m.text == "👤 Профиль")
def show_profile(message):
    uid = message.from_user.id
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    u = c.fetchone()
    conn.close()
    if u:
        wr = (u[5]/u[4]*100) if u[4]>0 else 0
        safe_send(message.chat.id, f"👤 Профиль\n💰 Баланс: {u[2]}\n🎁 Фрибеты: {u[3]}\n📊 Ставок: {u[4]}\n✅ Побед: {u[5]}\n📈 Винрейт: {wr:.1f}%")

@bot.message_handler(func=lambda m: m.text == "💰 Баланс")
def show_balance(message):
    uid = message.from_user.id
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT balance, freebets FROM users WHERE user_id=?", (uid,))
    d = c.fetchone()
    conn.close()
    if d: safe_send(message.chat.id, f"💰 Баланс: {d[0]} монет\n🎁 Фрибеты: {d[1]}")
    else: safe_send(message.chat.id, "❌ Напишите /start")

@bot.message_handler(func=lambda m: m.text == "📊 История")
def show_history(message):
    uid = message.from_user.id
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT b.team, b.amount, b.status, m.team1, m.team2 FROM bets b JOIN matches m ON b.match_id=m.match_id WHERE b.user_id=? ORDER BY b.bet_id DESC LIMIT 5", (uid,))
    bets = c.fetchall()
    conn.close()
    if bets:
        txt = "📊 Последние ставки:\n\n"
        for b in bets:
            e = "✅" if b[2]=="won" else "❌" if b[2]=="lost" else "⏳"
            txt += f"{e} {b[3]} vs {b[4]}\n   {b[1]} на {b[0]}\n\n"
    else: txt = "Нет ставок"
    safe_send(message.chat.id, txt)

@bot.message_handler(func=lambda m: m.text == "🆘 Бонус")
def get_bonus(message):
    uid = message.from_user.id
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
    u = c.fetchone()
    if not u: safe_send(message.chat.id, "❌ /start"); conn.close(); return
    if u[0] != 0: safe_send(message.chat.id, f"❌ Баланс: {u[0]}\nНужно 0!"); conn.close(); return
    c.execute("UPDATE users SET balance=50 WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    safe_send(message.chat.id, "✅ +50 монет!")

@bot.message_handler(func=lambda m: m.text == "🎫 Промокод")
def promo_activate(message):
    msg = safe_send(message.chat.id, "🎫 Введите промокод:")
    bot.register_next_step_handler(msg, process_promo)

def process_promo(message):
    uid = message.from_user.id
    code = message.text.strip().upper()
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT * FROM promocodes WHERE code=? AND is_active=1", (code,))
    p = c.fetchone()
    if not p: safe_send(message.chat.id, "❌ Не найден!"); conn.close(); return
    if p[3]<=p[4]: safe_send(message.chat.id, "❌ Лимит!"); conn.close(); return
    c.execute("SELECT id FROM used_promos WHERE user_id=? AND promo_code=?", (uid, code))
    if c.fetchone(): safe_send(message.chat.id, "❌ Уже использован!"); conn.close(); return
    amt = p[2]
    c.execute("INSERT INTO used_promos (user_id, promo_code, used_date) VALUES (?,?,?)", (uid, code, msk_now().strftime("%d.%m.%Y %H:%M")))
    c.execute("UPDATE promocodes SET used_count=used_count+1 WHERE code=?", (code,))
    c.execute("UPDATE users SET freebets=freebets+1 WHERE user_id=?", (uid,))
    c.execute("INSERT INTO bets (user_id, match_id, team, amount, bet_type) VALUES (?,0,'freebet',?,'freebet')", (uid, amt))
    if p[4]+1>=p[3]: c.execute("UPDATE promocodes SET is_active=0 WHERE code=?", (code,))
    conn.commit()
    conn.close()
    safe_send(message.chat.id, f"🎁 Активирован!\n💰 {amt}")

@bot.message_handler(func=lambda m: m.text == "🎁 Фрибеты")
def show_freebets(message):
    uid = message.from_user.id
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT bet_id, amount FROM bets WHERE user_id=? AND bet_type='freebet' AND status='pending'", (uid,))
    fbs = c.fetchall()
    conn.close()
    if fbs:
        kb = types.InlineKeyboardMarkup(row_width=1)
        txt = "🎁 Фрибеты:\n\n"
        for fb in fbs:
            txt += f"#{fb[0]}: {fb[1]}💰\n"
            kb.add(types.InlineKeyboardButton(f"Исп. #{fb[0]} ({fb[1]})", callback_data=f"use_freebet_{fb[0]}"))
        safe_send(message.chat.id, txt, kb)
    else: safe_send(message.chat.id, "🎁 Нет фрибетов")

@bot.message_handler(func=lambda m: m.text == "♠ Блэкджек")
def bj_menu(message):
    safe_send(message.chat.id, "🃏 БЛЭКДЖЕК\nВыберите режим:", bj_menu_kb())

# Админ кнопки
@bot.message_handler(func=lambda m: m.text == "🔧 Админ" and m.from_user.id == ADMIN_ID)
def admin_menu(message):
    safe_send(message.chat.id, "👑 Админ-панель:", admin_keyboard())

@bot.message_handler(func=lambda m: m.text == "🎮 Матчи" and m.from_user.id == ADMIN_ID)
def admin_matches(message):
    safe_send(message.chat.id, "🎮 Управление матчами:", admin_matches_kb())

@bot.message_handler(func=lambda m: m.text == "🎫 Промокоды" and m.from_user.id == ADMIN_ID)
def admin_promo(message):
    safe_send(message.chat.id, "🎫 Промокоды:", admin_promo_kb())

@bot.message_handler(func=lambda m: m.text == "💰 Фрибет" and m.from_user.id == ADMIN_ID)
def admin_freebet(message):
    msg = safe_send(message.chat.id, "🎁 ID и сумма:\nПример: 123456789 500")
    bot.register_next_step_handler(msg, process_freebet)

def process_freebet(message):
    try:
        parts = message.text.split()
        tid, amt = int(parts[0]), int(parts[1])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("UPDATE users SET freebets=freebets+1 WHERE user_id=?", (tid,))
        c.execute("INSERT INTO bets (user_id, match_id, team, amount, bet_type) VALUES (?,0,'freebet',?,'freebet')", (tid, amt))
        conn.commit()
        conn.close()
        safe_send(message.chat.id, "✅ Выдано!")
    except: safe_send(message.chat.id, "❌ Ошибка!")

@bot.message_handler(func=lambda m: m.text == "📊 Статистика" and m.from_user.id == ADMIN_ID)
def admin_stats(message):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    u = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM matches WHERE status='upcoming'")
    m = c.fetchone()[0]
    conn.close()
    safe_send(message.chat.id, f"📊 Пользователей: {u}\n🏒 Активных матчей: {m}")

@bot.message_handler(func=lambda m: m.text == "👥 Люди" and m.from_user.id == ADMIN_ID)
def admin_users(message):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT username, balance FROM users ORDER BY balance DESC LIMIT 10")
    us = c.fetchall()
    conn.close()
    txt = "👥 Топ-10:\n\n"
    for i,u in enumerate(us,1): txt += f"{i}. {u[0]} 💰{u[1]}\n"
    safe_send(message.chat.id, txt)

@bot.message_handler(func=lambda m: m.text == "📸 Фото" and m.from_user.id == ADMIN_ID)
def admin_photo(message):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("🏆 Победа", callback_data="photo_win"), types.InlineKeyboardButton("💔 Поражение", callback_data="photo_lose"))
    kb.add(types.InlineKeyboardButton("📋 Показать", callback_data="photo_show"))
    safe_send(message.chat.id, "📸 Выберите тип:", kb)

@bot.message_handler(func=lambda m: m.text == "📋 Меню")
def menu_button(message):
    uid = message.from_user.id
    safe_send(message.chat.id, "📋 Меню:", admin_keyboard() if uid==ADMIN_ID else main_keyboard(uid))

# ========== CALLBACK ОБРАБОТЧИКИ ==========
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    uid = call.from_user.id
    d = call.data
    
    # БЛЭКДЖЕК
    if d == "bj_solo":
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        bal = c.fetchone()[0]
        conn.close()
        kb = types.InlineKeyboardMarkup(row_width=3)
        for amt in [50,100,250,500,1000]: kb.add(types.InlineKeyboardButton(str(amt), callback_data=f"bjsolo_{amt}"))
        kb.add(types.InlineKeyboardButton("Своя", callback_data="bjsolo_custom"))
        safe_edit(f"🃏 БОТ\n💰 {bal}\nСтавка:", call.message.chat.id, call.message.message_id, kb)
    
    elif d == "bj_lobby_create":
        msg = safe_send(call.message.chat.id, "💰 Мин. ставка:")
        bot.register_next_step_handler(msg, create_lobby)
        bot.answer_callback_query(call.id)
    
    elif d == "bj_lobby_find":
        active = {k:v for k,v in blackjack_lobbies.items() if not v['started']}
        if not active: bot.answer_callback_query(call.id, "Нет лобби"); return
        kb = types.InlineKeyboardMarkup(row_width=1)
        txt = "🎯 ЛОББИ:\n\n"
        for lid, l in active.items():
            txt += f"#{lid} | 💰{l['min_bet']} | 👥{len(l['players'])}\n"
            kb.add(types.InlineKeyboardButton(f"Войти #{lid} ({l['min_bet']}💰)", callback_data=f"lobby_join_{lid}"))
        safe_edit(txt, call.message.chat.id, call.message.message_id, kb)
    
    elif d.startswith("bjsolo_"):
        bet_str = d.split("_")[1]
        if bet_str == "custom":
            msg = safe_send(call.message.chat.id, "💵 Ставка:")
            bot.register_next_step_handler(msg, solo_custom_bet)
        else: start_solo(call, uid, int(bet_str))
    
    elif d.startswith("bjh_"):
        _, gid, act = d.split("_")
        solo_action(call, uid, gid, act)
    
    elif d.startswith("lobby_join_"):
        lid = d.split("_")[2]
        join_lobby(call, uid, lid)
    
    elif d.startswith("lobby_start_"):
        lid = d.split("_")[2]
        start_lobby(call, uid, lid)
    
    elif d.startswith("lobby_hit_"):
        lid = d.split("_")[2]
        lobby_action(call, uid, lid, "hit")
    
    elif d.startswith("lobby_stand_"):
        lid = d.split("_")[2]
        lobby_action(call, uid, lid, "stand")
    
    # ФОТО
    elif d == "photo_win":
        if uid!=ADMIN_ID: return
        user_match_creation[uid] = {'photo_type': 'win'}
        bot.answer_callback_query(call.id, "Отправьте фото ПОБЕДЫ")
    
    elif d == "photo_lose":
        if uid!=ADMIN_ID: return
        user_match_creation[uid] = {'photo_type': 'lose'}
        bot.answer_callback_query(call.id, "Отправьте фото ПОРАЖЕНИЯ")
    
    elif d == "photo_show":
        if uid!=ADMIN_ID: return
        for t, n in [('win','🏆'), ('lose','💔')]:
            p = get_photo(t)
            if p:
                try: bot.send_photo(call.message.chat.id, p, caption=n)
                except: pass
    
    # ПРОМОКОДЫ АДМИН
    elif d == "adm_promo_create":
        if uid!=ADMIN_ID: return
        msg = safe_send(call.message.chat.id, "🎫 Сумма Кол-во [Код]")
        bot.register_next_step_handler(msg, create_promo)
    
    elif d == "adm_promo_list":
        if uid!=ADMIN_ID: return
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT * FROM promocodes ORDER BY created_date DESC LIMIT 10")
        ps = c.fetchall()
        conn.close()
        txt = "📋 Промокоды:\n\n" if ps else "❌ Нет"
        for p in ps: txt += f"{'🟢' if p[5] else '🔴'} {p[1]} 💰{p[2]} {p[4]}/{p[3]}\n"
        safe_edit(txt, call.message.chat.id, call.message.message_id)
    
    elif d == "adm_promo_del_list":
        if uid!=ADMIN_ID: return
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT promo_id, code, freebet_amount FROM promocodes WHERE is_active=1")
        ps = c.fetchall()
        conn.close()
        if ps:
            kb = types.InlineKeyboardMarkup(row_width=1)
            for p in ps: kb.add(types.InlineKeyboardButton(f"🗑 {p[1]} ({p[2]})", callback_data=f"adm_promo_del_{p[0]}"))
            kb.add(types.InlineKeyboardButton("🔙", callback_data="back_admin_main"))
            safe_edit("Выберите:", call.message.chat.id, call.message.message_id, kb)
        else: bot.answer_callback_query(call.id, "Нет активных")
    
    elif d.startswith("adm_promo_del_"):
        if uid!=ADMIN_ID: return
        pid = int(d.split("_")[3])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("UPDATE promocodes SET is_active=0 WHERE promo_id=?", (pid,))
        conn.commit()
        conn.close()
        bot.answer_callback_query(call.id, "Удалён!")
        safe_edit("✅ Деактивирован!", call.message.chat.id, call.message.message_id)
    
    # МАТЧИ АДМИН
    elif d == "adm_match_create":
        if uid!=ADMIN_ID: return
        msg = safe_send(call.message.chat.id, "➕ Команда1 vs Команда2 ДД.ММ.ГГГГ ЧЧ:ММ коэф1 коэф2 коэф_ничьей\n🕐 МСК")
        bot.register_next_step_handler(msg, create_match)
    
    elif d == "adm_match_all":
        if uid!=ADMIN_ID: return
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT * FROM matches ORDER BY match_date DESC LIMIT 15")
        ms = c.fetchall()
        conn.close()
        if not ms: safe_edit("📋 Нет матчей", call.message.chat.id, call.message.message_id); return
        txt = "📋 МАТЧИ:\n\n"
        for m in ms:
            s = "🟢" if m[7]=='upcoming' else "🔴" if m[7]=='finished' else "🔒"
            txt += f"{s} #{m[0]}: {m[1]} vs {m[2]}\n   📅{m[3]} | {m[4]}/{m[5]}/{m[6]}"
            if m[8]: txt += f"\n   🏆{m[8]}"
            txt += "\n\n"
        kb = types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("🔙", callback_data="back_admin_main"))
        safe_edit(txt, call.message.chat.id, call.message.message_id, kb)
    
    elif d == "adm_match_bets":
        if uid!=ADMIN_ID: return
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT match_id, team1, team2, match_date FROM matches WHERE status IN ('upcoming','closed') ORDER BY match_date DESC LIMIT 15")
        ms = c.fetchall()
        conn.close()
        if not ms: bot.answer_callback_query(call.id, "Нет матчей"); return
        kb = types.InlineKeyboardMarkup(row_width=1)
        for m in ms: kb.add(types.InlineKeyboardButton(f"#{m[0]} {m[1]} vs {m[2]} | {m[3]}", callback_data=f"adm_viewbets_{m[0]}"))
        kb.add(types.InlineKeyboardButton("🔙", callback_data="back_admin_main"))
        safe_edit("👁 Выберите матч для просмотра ставок:", call.message.chat.id, call.message.message_id, kb)
    
    elif d.startswith("adm_viewbets_"):
        if uid!=ADMIN_ID: return
        mid = int(d.split("_")[2])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT team1, team2 FROM matches WHERE match_id=?", (mid,))
        m = c.fetchone()
        c.execute("""
            SELECT u.username, b.team, b.amount, b.bet_type, b.status 
            FROM bets b JOIN users u ON b.user_id=u.user_id 
            WHERE b.match_id=? AND b.bet_type!='freebet'
            ORDER BY b.bet_id DESC
        """, (mid,))
        bets = c.fetchall()
        conn.close()
        txt = f"👁 СТАВКИ НА #{mid} {m[0]} vs {m[1]}:\n\n"
        if bets:
            for b in bets:
                s = "✅" if b[4]=="won" else "❌" if b[4]=="lost" else "⏳"
                txt += f"{s} {b[0]}: {b[2]}💰 на {b[1]}\n"
        else: txt += "Нет ставок"
        kb = types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("🔙", callback_data="adm_match_bets"))
        safe_edit(txt, call.message.chat.id, call.message.message_id, kb)
    
    elif d == "adm_match_winner":
        if uid!=ADMIN_ID: return
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT match_id, team1, team2 FROM matches WHERE status='closed'")
        ms = c.fetchall()
        conn.close()
        if ms:
            kb = types.InlineKeyboardMarkup(row_width=1)
            for m in ms: kb.add(types.InlineKeyboardButton(f"#{m[0]} {m[1]} vs {m[2]}", callback_data=f"adm_winner_{m[0]}"))
            kb.add(types.InlineKeyboardButton("🔙", callback_data="back_admin_main"))
            safe_edit("🏆 Выберите матч:", call.message.chat.id, call.message.message_id, kb)
        else: bot.answer_callback_query(call.id, "Нет закрытых матчей")
    
    elif d.startswith("adm_winner_"):
        if uid!=ADMIN_ID: return
        mid = int(d.split("_")[2])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT team1, team2 FROM matches WHERE match_id=?", (mid,))
        m = c.fetchone()
        conn.close()
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton(f"✅ {m[0]}", callback_data=f"adm_setwin_{mid}_{m[0]}"))
        kb.add(types.InlineKeyboardButton(f"✅ {m[1]}", callback_data=f"adm_setwin_{mid}_{m[1]}"))
        kb.add(types.InlineKeyboardButton(f"🤝 Ничья", callback_data=f"adm_setwin_{mid}_Ничья"))
        safe_edit(f"⚔ {m[0]} vs {m[1]}\n\nКто победил?", call.message.chat.id, call.message.message_id, kb)
    
    elif d.startswith("adm_setwin_"):
        if uid!=ADMIN_ID: return
        parts = d.split("_")
        mid = int(parts[2])
        winner = parts[3]
        
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT team1, team2 FROM matches WHERE match_id=?", (mid,))
        m = c.fetchone()
        c.execute("UPDATE matches SET status='finished', winner=? WHERE match_id=?", (winner, mid))
        c.execute("SELECT bet_id, user_id, team, amount, bet_type, coefficient FROM bets WHERE match_id=? AND status='pending'", (mid,))
        bets = c.fetchall()
        
        for b in bets:
            bid, buid, team, amt, bt, coef = b
            if team == winner:
                winnings = int(amt * coef) if bt not in ('freebet','freebet_active') else amt
                c.execute("UPDATE users SET balance=balance+?, wins=wins+1 WHERE user_id=?", (winnings, buid))
                c.execute("UPDATE bets SET status='won' WHERE bet_id=?", (bid,))
                photo = get_photo('win')
                msg = f"🎉 ВЫИГРЫШ!\n📊 Матч #{mid} {m[0]} vs {m[1]}\n✅ {team}\n💰 +{winnings}"
                if photo:
                    try: bot.send_photo(buid, photo, caption=msg)
                    except: safe_send(buid, msg)
                else: safe_send(buid, msg)
            else:
                c.execute("UPDATE bets SET status='lost' WHERE bet_id=?", (bid,))
                photo = get_photo('lose')
                msg = f"💔 ПРОИГРЫШ\n📊 Матч #{mid}\n❌ {team}\n💵 -{amt}"
                if photo:
                    try: bot.send_photo(buid, photo, caption=msg)
                    except: safe_send(buid, msg)
                else: safe_send(buid, msg)
        
        conn.commit()
        conn.close()
        bot.answer_callback_query(call.id, f"✅ {winner}!")
        safe_edit(f"✅ Матч #{mid}\n🏆 {winner}\n💰 Ставки рассчитаны!", call.message.chat.id, call.message.message_id)
    
    elif d == "adm_match_del_list":
        if uid!=ADMIN_ID: return
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT match_id, team1, team2 FROM matches WHERE status IN ('upcoming','closed')")
        ms = c.fetchall()
        conn.close()
        if ms:
            kb = types.InlineKeyboardMarkup(row_width=1)
            for m in ms: kb.add(types.InlineKeyboardButton(f"🗑 #{m[0]} {m[1]} vs {m[2]}", callback_data=f"adm_match_del_{m[0]}"))
            kb.add(types.InlineKeyboardButton("🔙", callback_data="back_admin_main"))
            safe_edit("🗑 Выберите:", call.message.chat.id, call.message.message_id, kb)
        else: bot.answer_callback_query(call.id, "Нет матчей")
    
    elif d.startswith("adm_match_del_"):
        if uid!=ADMIN_ID: return
        mid = int(d.split("_")[3])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("DELETE FROM matches WHERE match_id=?", (mid,))
        c.execute("DELETE FROM bets WHERE match_id=?", (mid,))
        conn.commit()
        conn.close()
        bot.answer_callback_query(call.id, f"Удалён!")
        safe_edit(f"🗑 Матч #{mid} удалён!", call.message.chat.id, call.message.message_id)
    
    elif d == "back_admin_main":
        safe_edit("👑 Админ-панель:", call.message.chat.id, call.message.message_id)
    
    # СТАВКИ
    elif d == "show_matches":
        safe_edit("🎯 Матчи:", call.message.chat.id, call.message.message_id, matches_keyboard())
    
    elif d == "refresh_matches":
        safe_edit("🔄 Обновлено!", call.message.chat.id, call.message.message_id, matches_keyboard())
    
    elif d == "back_main":
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        safe_send(call.message.chat.id, "📋 Меню:", main_keyboard(uid))
    
    elif d == "none":
        bot.answer_callback_query(call.id, "Нет доступных матчей")
    
    elif d.startswith("match_"):
        mid = int(d.split("_")[1])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT team1, team2, match_date, coefficient1, coefficient2, coefficient_draw FROM matches WHERE match_id=?", (mid,))
        m = c.fetchone()
        conn.close()
        safe_edit(f"⚔ {m[0]} vs {m[1]}\n📅 {m[2]}\nКФ: {m[3]}/{m[4]}/{m[5]}", call.message.chat.id, call.message.message_id, bet_keyboard(mid))
    
    elif d.startswith("betsum_"):
        _, mid, team = d.split("_", 2)
        safe_edit(f"💰 Ставка на {team}:", call.message.chat.id, call.message.message_id, sum_keyboard(int(mid), team))
    
    elif d.startswith("custom_"):
        _, mid, team = d.split("_", 2)
        msg = safe_send(call.message.chat.id, f"💵 Сумма на {team}:")
        user_bet_amount[uid] = {'match_id': int(mid), 'team': team}
        bot.register_next_step_handler(msg, custom_bet)
    
    elif d.startswith("bet_"):
        parts = d.split("_")
        mid = int(parts[1])
        team = "_".join(parts[2:-1])
        amt = int(parts[-1])
        place_bet(call, uid, mid, team, amt)
    
    elif d.startswith("use_freebet_"):
        bid = int(d.split("_")[2])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT amount FROM bets WHERE bet_id=? AND bet_type='freebet' AND status='pending'", (bid,))
        fb = c.fetchone()
        conn.close()
        if fb:
            conn = sqlite3.connect('hockey_bets.db')
            c = conn.cursor()
            now = msk_now().strftime("%d.%m.%Y %H:%M")
            c.execute("SELECT match_id, team1, team2, match_date FROM matches WHERE status='upcoming' AND match_date > ?", (now,))
            ms = c.fetchall()
            conn.close()
            if ms:
                kb = types.InlineKeyboardMarkup(row_width=1)
                for m in ms: kb.add(types.InlineKeyboardButton(f"⚔ {m[1]} vs {m[2]} | {m[3]}", callback_data=f"freebet_match_{bid}_{m[0]}"))
                kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
                safe_edit(f"🎯 Выберите матч для фрибета\n💰 Номинал: {fb[0]} монет", call.message.chat.id, call.message.message_id, kb)
            else: bot.answer_callback_query(call.id, "❌ Нет доступных матчей!")
        else: bot.answer_callback_query(call.id, "❌ Фрибет недоступен!")
    
    elif d.startswith("freebet_match_"):
        parts = d.split("_")
        bid = int(parts[2])
        mid = int(parts[3])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT team1, team2, coefficient1, coefficient2, coefficient_draw FROM matches WHERE match_id=?", (mid,))
        m = c.fetchone()
        conn.close()
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton(f"✅ {m[0]}", callback_data=f"freebet_team_{bid}_{mid}_{m[0]}"))
        kb.add(types.InlineKeyboardButton(f"✅ {m[1]}", callback_data=f"freebet_team_{bid}_{mid}_{m[1]}"))
        kb.add(types.InlineKeyboardButton(f"🤝 Ничья", callback_data=f"freebet_team_{bid}_{mid}_Ничья"))
        kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data=f"use_freebet_{bid}"))
        safe_edit(f"⚔ {m[0]} vs {m[1]}\n\nВыберите исход:", call.message.chat.id, call.message.message_id, kb)
    
    elif d.startswith("freebet_team_"):
        parts = d.split("_", 4)
        bid = int(parts[2])
        mid = int(parts[3])
        team = parts[4]
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT amount FROM bets WHERE bet_id=? AND bet_type='freebet' AND status='pending'", (bid,))
        fb = c.fetchone()
        if fb:
            c.execute("UPDATE bets SET match_id=?, team=?, bet_type='freebet_active' WHERE bet_id=?", (mid, team, bid))
            c.execute("UPDATE users SET freebets=freebets-1 WHERE user_id=?", (uid,))
            conn.commit()
            safe_edit(f"✅ Фрибет использован!\nМатч #{mid}\n{team}\n💰 {fb[0]} монет\n\nЖдите результат!", call.message.chat.id, call.message.message_id)
        conn.close()

# ========== БЛЭКДЖЕК ФУНКЦИИ ==========
def create_lobby(message):
    uid = message.from_user.id
    try:
        bet = int(message.text)
        if bet < 50: safe_send(message.chat.id, "❌ Мин. 50"); return
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        if c.fetchone()[0] < bet: safe_send(message.chat.id, "❌ Нет средств!"); conn.close(); return
        conn.close()
        lid = str(int(time.time()))[-6:]
        blackjack_lobbies[lid] = {'creator': uid, 'players': {uid: bet}, 'min_bet': bet, 'started': False}
        kb = types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("🚀 Начать", callback_data=f"lobby_start_{lid}"))
        safe_send(message.chat.id, f"🎯 ЛОББИ #{lid}\n💰 {bet}\n👥 1\n/lobby_{lid}", kb)
    except: safe_send(message.chat.id, "❌ Число!")

def join_lobby(call, uid, lid):
    if lid not in blackjack_lobbies: bot.answer_callback_query(call.id, "❌ Нет!"); return
    l = blackjack_lobbies[lid]
    if l['started']: bot.answer_callback_query(call.id, "❌ Началась!"); return
    if uid in l['players']: bot.answer_callback_query(call.id, "❌ Уже там!"); return
    bet = l['min_bet']
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
    if c.fetchone()[0] < bet: bot.answer_callback_query(call.id, "❌ Средств!", show_alert=True); conn.close(); return
    c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (bet, uid))
    conn.commit()
    conn.close()
    l['players'][uid] = bet
    bot.answer_callback_query(call.id, f"✅ В #{lid}!")
    try: safe_send(l['creator'], f"👤 +1! Всего: {len(l['players'])}")
    except: pass

def start_lobby(call, uid, lid):
    if lid not in blackjack_lobbies: bot.answer_callback_query(call.id, "❌ Нет!"); return
    l = blackjack_lobbies[lid]
    if uid != l['creator']: bot.answer_callback_query(call.id, "❌ Не создатель!"); return
    deck = [{'rank':r,'suit':s} for _ in range(4) for s in ['♠','♥','♦','♣'] for r in ['A','2','3','4','5','6','7','8','9','10','J','Q','K']]
    random.shuffle(deck)
    game = {'deck': deck, 'dealer_hand': [deck.pop(), deck.pop()], 'players': {}}
    for pid in l['players']: game['players'][pid] = {'hand': [deck.pop(), deck.pop()], 'bet': l['players'][pid], 'status': 'playing'}
    l['game'] = game; l['started'] = True
    for pid, pd in game['players'].items():
        hand = ' '.join(f"{c['rank']}{c['suit']}" for c in pd['hand'])
        val = sum(10 if c['rank'] in 'JQK' else 11 if c['rank']=='A' else int(c['rank']) for c in pd['hand'])
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("🃏 Взять", callback_data=f"lobby_hit_{lid}"), types.InlineKeyboardButton("✋ Хватит", callback_data=f"lobby_stand_{lid}"))
        safe_send(pid, f"🃏 #{lid}\n\nРука: {hand} ({val})\nДилер: {game['dealer_hand'][0]['rank']}{game['dealer_hand'][0]['suit']} ?\n💰 {pd['bet']}", kb)
    bot.answer_callback_query(call.id, "🎮 Старт!")

def lobby_action(call, uid, lid, action):
    if lid not in blackjack_lobbies or not blackjack_lobbies[lid]['started']: bot.answer_callback_query(call.id, "❌ Нет!"); return
    game = blackjack_lobbies[lid]['game']
    if uid not in game['players']: bot.answer_callback_query(call.id, "❌ Не в игре!"); return
    pd = game['players'][uid]
    if action == "hit":
        pd['hand'].append(game['deck'].pop())
        val = sum(10 if c['rank'] in 'JQK' else 11 if c['rank']=='A' else int(c['rank']) for c in pd['hand'])
        aces = sum(1 for c in pd['hand'] if c['rank']=='A')
        while val > 21 and aces > 0: val -= 10; aces -= 1
        hand = ' '.join(f"{c['rank']}{c['suit']}" for c in pd['hand'])
        if val > 21:
            pd['status'] = 'bust'
            safe_edit(f"💥 ПЕРЕБОР!\n{hand} ({val})\n❌ -{pd['bet']}💰", call.message.chat.id, call.message.message_id)
        else:
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("🃏 Взять", callback_data=f"lobby_hit_{lid}"), types.InlineKeyboardButton("✋ Хватит", callback_data=f"lobby_stand_{lid}"))
            safe_edit(f"🃏 #{lid}\n\nРука: {hand} ({val})\nДилер: {game['dealer_hand'][0]['rank']}{game['dealer_hand'][0]['suit']} ?", call.message.chat.id, call.message.message_id, kb)
    elif action == "stand":
        pd['status'] = 'stand'
        hand = ' '.join(f"{c['rank']}{c['suit']}" for c in pd['hand'])
        val = sum(10 if c['rank'] in 'JQK' else 11 if c['rank']=='A' else int(c['rank']) for c in pd['hand'])
        safe_edit(f"✋ ПАС\n{hand} ({val})\nЖдите...", call.message.chat.id, call.message.message_id)
    if all(p['status'] in ('stand','bust') for p in game['players'].values()):
        d_hand = game['dealer_hand']
        while sum(10 if c['rank'] in 'JQK' else 11 if c['rank']=='A' else int(c['rank']) for c in d_hand) < 17: d_hand.append(game['deck'].pop())
        d_val = sum(10 if c['rank'] in 'JQK' else 11 if c['rank']=='A' else int(c['rank']) for c in d_hand)
        d_str = ' '.join(f"{c['rank']}{c['suit']}" for c in d_hand)
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        for pid, pd in game['players'].items():
            p_val = sum(10 if c['rank'] in 'JQK' else 11 if c['rank']=='A' else int(c['rank']) for c in pd['hand'])
            p_str = ' '.join(f"{c['rank']}{c['suit']}" for c in pd['hand'])
            if pd['status']=='bust' or (d_val<=21 and p_val<d_val): txt = f"😞 ПРОИГРЫШ\n\n{p_str} ({p_val})\nДилер: {d_str} ({d_val})\n❌ -{pd['bet']}💰"
            elif d_val>21 or p_val>d_val:
                w = pd['bet']*2; c.execute("UPDATE users SET balance=balance+?, wins=wins+1 WHERE user_id=?", (w, pid))
                txt = f"🎉 ВЫИГРЫШ!\n\n{p_str} ({p_val})\nДилер: {d_str} ({d_val})\n💰 +{w}"
            else: c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (pd['bet'], pid)); txt = f"🤝 НИЧЬЯ\n\n{p_str} ({p_val})\nДилер: {d_str} ({d_val})\n💵 Возврат"
            safe_send(pid, txt)
        conn.commit(); conn.close()
        del blackjack_lobbies[lid]

def start_solo(call, uid, bet):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
    if c.fetchone()[0] < bet: bot.answer_callback_query(call.id, "❌ Средств!", show_alert=True); conn.close(); return
    c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (bet, uid))
    c.execute("INSERT INTO bets (user_id, match_id, team, amount, bet_type) VALUES (?,0,'blackjack',?,'blackjack')", (uid, bet))
    gid = str(c.lastrowid); conn.commit(); conn.close()
    deck = [{'rank':r,'suit':s} for _ in range(4) for s in ['♠','♥','♦','♣'] for r in ['A','2','3','4','5','6','7','8','9','10','J','Q','K']]
    random.shuffle(deck)
    solo_games[gid] = {'bet': bet, 'deck': deck, 'player': [deck.pop(), deck.pop()], 'dealer': [deck.pop(), deck.pop()]}
    hand = ' '.join(f"{c['rank']}{c['suit']}" for c in solo_games[gid]['player'])
    val = sum(10 if c['rank'] in 'JQK' else 11 if c['rank']=='A' else int(c['rank']) for c in solo_games[gid]['player'])
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("🃏 Взять", callback_data=f"bjh_{gid}_hit"), types.InlineKeyboardButton("✋ Хватит", callback_data=f"bjh_{gid}_stand"))
    bot.answer_callback_query(call.id, "🎮 Игра!")
    safe_edit(f"🃏 БОТ\n💰 {bet}\n\nРука: {hand} ({val})\nДилер: {solo_games[gid]['dealer'][0]['rank']}{solo_games[gid]['dealer'][0]['suit']} ?", call.message.chat.id, call.message.message_id, kb)

def solo_action(call, uid, gid, action):
    if gid not in solo_games: bot.answer_callback_query(call.id, "❌ Нет!"); return
    g = solo_games[gid]
    if action == "hit":
        g['player'].append(g['deck'].pop())
        val = sum(10 if c['rank'] in 'JQK' else 11 if c['rank']=='A' else int(c['rank']) for c in g['player'])
        aces = sum(1 for c in g['player'] if c['rank']=='A')
        while val > 21 and aces > 0: val -= 10; aces -= 1
        hand = ' '.join(f"{c['rank']}{c['suit']}" for c in g['player'])
        if val > 21:
            conn = sqlite3.connect('hockey_bets.db'); c = conn.cursor()
            c.execute("UPDATE bets SET status='lost' WHERE bet_id=?", (int(gid),)); conn.commit(); conn.close()
            safe_edit(f"💥 ПЕРЕБОР!\n{hand} ({val})\n❌ -{g['bet']}💰", call.message.chat.id, call.message.message_id)
            del solo_games[gid]
        else:
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("🃏 Взять", callback_data=f"bjh_{gid}_hit"), types.InlineKeyboardButton("✋ Хватит", callback_data=f"bjh_{gid}_stand"))
            safe_edit(f"🃏 БОТ\n💰 {g['bet']}\n\nРука: {hand} ({val})\nДилер: {g['dealer'][0]['rank']}{g['dealer'][0]['suit']} ?", call.message.chat.id, call.message.message_id, kb)
    elif action == "stand":
        while sum(10 if c['rank'] in 'JQK' else 11 if c['rank']=='A' else int(c['rank']) for c in g['dealer']) < 17: g['dealer'].append(g['deck'].pop())
        p_val = sum(10 if c['rank'] in 'JQK' else 11 if c['rank']=='A' else int(c['rank']) for c in g['player'])
        p_str = ' '.join(f"{c['rank']}{c['suit']}" for c in g['player'])
        d_val = sum(10 if c['rank'] in 'JQK' else 11 if c['rank']=='A' else int(c['rank']) for c in g['dealer'])
        d_str = ' '.join(f"{c['rank']}{c['suit']}" for c in g['dealer'])
        conn = sqlite3.connect('hockey_bets.db'); c = conn.cursor()
        if d_val > 21 or p_val > d_val:
            w = g['bet'] * 2; c.execute("UPDATE users SET balance=balance+?, wins=wins+1 WHERE user_id=?", (w, uid))
            c.execute("UPDATE bets SET status='won' WHERE bet_id=?", (int(gid),)); txt = f"🎉 ВЫИГРЫШ!\n\n{p_str} ({p_val})\nДилер: {d_str} ({d_val})\n💰 +{w}"
        elif p_val == d_val: c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (g['bet'], uid)); txt = f"🤝 НИЧЬЯ\n\n{p_str} ({p_val})\nДилер: {d_str} ({d_val})\n💵 Возврат"
        else: c.execute("UPDATE bets SET status='lost' WHERE bet_id=?", (int(gid),)); txt = f"😞 ПРОИГРЫШ\n\n{p_str} ({p_val})\nДилер: {d_str} ({d_val})\n❌ -{g['bet']}"
        conn.commit(); conn.close()
        safe_edit(txt, call.message.chat.id, call.message.message_id); del solo_games[gid]

def solo_custom_bet(message):
    try:
        amt = int(message.text)
        if amt <= 0: raise ValueError
        start_solo_direct(message.from_user.id, amt, message.chat.id)
    except: safe_send(message.chat.id, "❌ Сумма!")

def start_solo_direct(uid, bet, cid):
    conn = sqlite3.connect('hockey_bets.db'); c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
    if c.fetchone()[0] < bet: safe_send(cid, "❌ Средств!"); conn.close(); return
    c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (bet, uid))
    c.execute("INSERT INTO bets (user_id, match_id, team, amount, bet_type) VALUES (?,0,'blackjack',?,'blackjack')", (uid, bet))
    gid = str(c.lastrowid); conn.commit(); conn.close()
    deck = [{'rank':r,'suit':s} for _ in range(4) for s in ['♠','♥','♦','♣'] for r in ['A','2','3','4','5','6','7','8','9','10','J','Q','K']]
    random.shuffle(deck)
    solo_games[gid] = {'bet': bet, 'deck': deck, 'player': [deck.pop(), deck.pop()], 'dealer': [deck.pop(), deck.pop()]}
    hand = ' '.join(f"{c['rank']}{c['suit']}" for c in solo_games[gid]['player'])
    val = sum(10 if c['rank'] in 'JQK' else 11 if c['rank']=='A' else int(c['rank']) for c in solo_games[gid]['player'])
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("🃏 Взять", callback_data=f"bjh_{gid}_hit"), types.InlineKeyboardButton("✋ Хватит", callback_data=f"bjh_{gid}_stand"))
    safe_send(cid, f"🃏 БОТ\n💰 {bet}\n\nРука: {hand} ({val})\nДилер: {solo_games[gid]['dealer'][0]['rank']}{solo_games[gid]['dealer'][0]['suit']} ?", kb)

# ========== МАТЧИ ==========
def create_promo(message):
    if message.from_user.id != ADMIN_ID: return
    try:
        p = message.text.split()
        if len(p)==2: amt, mx, code = int(p[0]), int(p[1]), generate_promo_code()
        else: amt, mx, code = int(p[0]), int(p[1]), p[2].upper()
        conn = sqlite3.connect('hockey_bets.db'); c = conn.cursor()
        c.execute("INSERT INTO promocodes (code, freebet_amount, max_uses, created_by, created_date) VALUES (?,?,?,?,?)", (code, amt, mx, ADMIN_ID, msk_now().strftime("%d.%m.%Y %H:%M")))
        conn.commit(); conn.close()
        safe_send(message.chat.id, f"✅ {code} 💰{amt} 👥{mx}")
    except: safe_send(message.chat.id, "❌ Ошибка!")

def create_match(message):
    if message.from_user.id != ADMIN_ID: return
    try:
        t = message.text; l, r = t.split(' vs ', 1); t1 = l.strip()
        dm = re.search(r'\d{2}\.\d{2}\.\d{4}\s\d{2}:\d{2}', r); ds = dm.group()
        t2 = r[:dm.start()].strip(); af = r[dm.end():].strip().split()
        c1 = float(af[0]) if af else 2.5; c2 = float(af[1]) if len(af)>1 else 2.5; cd = float(af[2]) if len(af)>2 else 3.5
        conn = sqlite3.connect('hockey_bets.db'); c = conn.cursor()
        c.execute("INSERT INTO matches (team1, team2, match_date, coefficient1, coefficient2, coefficient_draw) VALUES (?,?,?,?,?,?)", (t1, t2, ds, c1, c2, cd))
        conn.commit(); conn.close()
        safe_send(message.chat.id, f"✅ {t1} vs {t2}\n📅 {ds} МСК")
    except Exception as e: safe_send(message.chat.id, f"❌ {e}")

def place_bet(call, uid, mid, team, amt):
    conn = sqlite3.connect('hockey_bets.db'); c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)); bal = c.fetchone()
    c.execute("SELECT team1, coefficient1, coefficient2, coefficient_draw FROM matches WHERE match_id=?", (mid,)); m = c.fetchone()
    coef = m[1] if team==m[0] else m[2] if team==m[2] else m[3]
    if bal and bal[0] >= amt:
        c.execute("UPDATE users SET balance=balance-?, total_bets=total_bets+1 WHERE user_id=?", (amt, uid))
        c.execute("INSERT INTO bets (user_id, match_id, team, amount, coefficient) VALUES (?,?,?,?,?)", (uid, mid, team, amt, coef))
        conn.commit()
        bot.answer_callback_query(call.id, "✅ Принято!")
        safe_edit(f"✅ Ставка!\n🎯 {team}\n💰 {amt} (x{coef})", call.message.chat.id, call.message.message_id)
    else: bot.answer_callback_query(call.id, "❌ Мало средств!", show_alert=True)
    conn.close()

def custom_bet(message):
    uid = message.from_user.id
    try:
        amt = int(message.text); d = user_bet_amount.get(uid)
        if d:
            conn = sqlite3.connect('hockey_bets.db'); c = conn.cursor()
            c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
            if c.fetchone()[0] >= amt: place_bet_direct(uid, d['match_id'], d['team'], amt, message.chat.id)
            else: safe_send(message.chat.id, "❌ Мало средств!")
            conn.close()
        del user_bet_amount[uid]
    except: safe_send(message.chat.id, "❌ Сумма!")

def place_bet_direct(uid, mid, team, amt, cid):
    conn = sqlite3.connect('hockey_bets.db'); c = conn.cursor()
    c.execute("SELECT team1, coefficient1, coefficient2, coefficient_draw FROM matches WHERE match_id=?", (mid,)); m = c.fetchone()
    coef = m[1] if team==m[0] else m[2] if team==m[2] else m[3]
    c.execute("UPDATE users SET balance=balance-?, total_bets=total_bets+1 WHERE user_id=?", (amt, uid))
    c.execute("INSERT INTO bets (user_id, match_id, team, amount, coefficient) VALUES (?,?,?,?,?)", (uid, mid, team, amt, coef))
    conn.commit(); conn.close()
    safe_send(cid, f"✅ {amt} на {team}")

# ========== ФОТО ==========
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    uid = message.from_user.id
    if uid != ADMIN_ID: return
    if uid in user_match_creation and 'photo_type' in user_match_creation[uid]:
        try:
            pt = user_match_creation[uid]['photo_type']; fid = message.photo[-1].file_id
            conn = sqlite3.connect('hockey_bets.db'); c = conn.cursor()
            c.execute("DELETE FROM photos WHERE photo_type=?", (pt,))
            c.execute("INSERT INTO photos (photo_type, file_id, added_date) VALUES (?,?,?)", (pt, fid, msk_now().strftime("%d.%m.%Y %H:%M")))
            conn.commit(); conn.close()
            safe_send(message.chat.id, f"✅ Фото {'🏆 ПОБЕДЫ' if pt=='win' else '💔 ПОРАЖЕНИЯ'} обновлено!")
            del user_match_creation[uid]
        except: safe_send(message.chat.id, "❌ Ошибка!")

# ========== ЛОББИ КОМАНДА ==========
@bot.message_handler(commands=['lobby'])
def lobby_command(message):
    try:
        lid = message.text.split('_')[1]; uid = message.from_user.id
        if lid not in blackjack_lobbies: safe_send(message.chat.id, "❌ Нет!"); return
        l = blackjack_lobbies[lid]
        if l['started']: safe_send(message.chat.id, "❌ Началась!"); return
        bet = l['min_bet']
        conn = sqlite3.connect('hockey_bets.db'); c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        if c.fetchone()[0] < bet: safe_send(message.chat.id, "❌ Средств!"); conn.close(); return
        c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (bet, uid)); conn.commit(); conn.close()
        l['players'][uid] = bet
        safe_send(message.chat.id, f"✅ В #{lid}! 💰{bet} 👥{len(l['players'])}")
        try: safe_send(l['creator'], f"👤 +1! Всего: {len(l['players'])}")
        except: pass
    except: safe_send(message.chat.id, "❌ /lobby_КОД")

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    print(f"STARTING EXTRABET... МСК: {msk_now().strftime('%H:%M')}")
    init_db()
    threading.Thread(target=run_web_server, daemon=True).start()
    threading.Thread(target=auto_close_matches, daemon=True).start()
    print("Bot starting...")
    bot.remove_webhook()
    time.sleep(0.5)
    bot.infinity_polling()
