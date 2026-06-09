import telebot
from telebot import types
import sqlite3
import random
from datetime import datetime, timedelta
import string
import re
import os
import time

# ========== НАСТРОЙКИ ==========
TOKEN = "8657731994:AAFgwuJbbd2fqvtXUqapczb9Y1I1ajW-FDM"
ADMIN_ID = 5706071030
bot = telebot.TeleBot(TOKEN)

# ========== ФУНКЦИИ БАЗЫ ДАННЫХ ==========
def msk_now():
    return datetime.now() + timedelta(hours=3)

def init_db():
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, balance INTEGER DEFAULT 1000, freebets INTEGER DEFAULT 0, total_bets INTEGER DEFAULT 0, wins INTEGER DEFAULT 0, daily_bonus_date TEXT, quest_claimed TEXT DEFAULT '')''')
    c.execute('''CREATE TABLE IF NOT EXISTS matches (match_id INTEGER PRIMARY KEY AUTOINCREMENT, league TEXT, team1 TEXT, team2 TEXT, match_date TEXT, coef1 REAL DEFAULT 2.5, coef2 REAL DEFAULT 2.5, coef_draw REAL DEFAULT 3.5, coef_ot1 REAL DEFAULT 3.5, coef_ot2 REAL DEFAULT 3.5, total_line REAL DEFAULT 5.5, coef_over REAL DEFAULT 1.9, coef_under REAL DEFAULT 1.9, status TEXT DEFAULT 'upcoming', winner TEXT, score TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS cup_teams (team_id INTEGER PRIMARY KEY AUTOINCREMENT, league TEXT, team TEXT, coefficient REAL DEFAULT 3.0, is_winner INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS bets (bet_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, match_id INTEGER, team TEXT, amount INTEGER, coefficient REAL, bet_type TEXT DEFAULT 'regular', status TEXT DEFAULT 'pending', bet_time TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS promocodes (promo_id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, freebet_amount INTEGER, max_uses INTEGER, used_count INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS used_promos (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, promo_code TEXT, used_date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS photos (photo_id INTEGER PRIMARY KEY AUTOINCREMENT, photo_type TEXT, file_id TEXT, added_date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS quests (quest_id INTEGER PRIMARY KEY AUTOINCREMENT, description TEXT, quest_type TEXT, target INTEGER, freebet_amount INTEGER DEFAULT 100, duration_hours INTEGER DEFAULT 24, is_active INTEGER DEFAULT 1, created_date TEXT)''')
    
    c.execute("SELECT COUNT(*) FROM quests")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO quests (description, quest_type, target, freebet_amount, duration_hours, is_active, created_date) VALUES (?,?,?,?,?,?,?)",
                  ("Сделай 1 ставку", "bets", 1, 500, 24, 1, msk_now().strftime("%d.%m.%Y %H:%M")))
    
    conn.commit()
    conn.close()

def safe_send(chat_id, text, reply_markup=None):
    try: 
        return bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception as e:
        print(f"Ошибка: {e}")
        return None

def get_photo(t):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT file_id FROM photos WHERE photo_type=? LIMIT 1", (t,))
    r = c.fetchone()
    conn.close()
    return r[0] if r else None

# ========== КВЕСТЫ ==========
def check_and_claim_quests(user_id):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    today = msk_now().strftime("%d.%m.%Y")
    rewards = []
    
    c.execute("SELECT COUNT(*) FROM bets WHERE user_id=? AND bet_time LIKE ?", (user_id, today+'%'))
    total_bets = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM bets WHERE user_id=? AND status='won' AND bet_time LIKE ?", (user_id, today+'%'))
    total_wins = c.fetchone()[0]
    
    c.execute("SELECT quest_claimed FROM users WHERE user_id=?", (user_id,))
    result = c.fetchone()
    claimed_str = result[0] if result and result[0] else ''
    claimed = claimed_str.split(',') if claimed_str else []
    
    c.execute("SELECT quest_id, quest_type, target, freebet_amount, description FROM quests WHERE is_active=1")
    quests = c.fetchall()
    
    new_claimed = []
    
    for q in quests:
        qid, qtype, target, fb_amt, desc = q
        if str(qid) in claimed:
            continue
        
        if qtype == 'bets':
            current = total_bets
        elif qtype == 'wins':
            current = total_wins
        else:
            continue
        
        if current >= target:
            c.execute("UPDATE users SET freebets=freebets+1 WHERE user_id=?", (user_id,))
            c.execute("""INSERT INTO bets (user_id, username, match_id, team, amount, coefficient, bet_type, status, bet_time) 
                         VALUES (?, (SELECT username FROM users WHERE user_id=?), 0, 'ФРИБЕТ КВЕСТА', ?, 1.0, 'freebet', 'pending', ?)""",
                (user_id, user_id, fb_amt, msk_now().strftime("%d.%m.%Y %H:%M")))
            new_claimed.append(str(qid))
            rewards.append(f"📋 {desc}: +1 фрибет на {fb_amt} тенге")
    
    if new_claimed:
        all_claimed = claimed + new_claimed
        c.execute("UPDATE users SET quest_claimed=? WHERE user_id=?", (','.join(all_claimed), user_id))
    
    conn.commit()
    conn.close()
    return rewards

# ========== КЛАВИАТУРЫ ==========
def main_keyboard(uid=None):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("🏒 СТАВКИ", "🏆 КУБОК")
    kb.add("👤 ПРОФИЛЬ", "💰 БАЛАНС")
    kb.add("📊 ИСТОРИЯ", "🎫 ПРОМОКОД")
    kb.add("🎁 ФРИБЕТЫ", "🎁 ЕЖЕДНЕВНЫЙ БОНУС")
    kb.add("📋 КВЕСТЫ", "🏆 ТОП-10")
    if uid == ADMIN_ID: 
        kb.add("🔧 АДМИН")
    return kb

def admin_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("➕ МАТЧ", "📋 МАТЧИ")
    kb.add("🏆 РЕЗУЛЬТАТ", "🏆 РЕЗ. КУБКА")
    kb.add("👁 СТАВКИ", "➕ КУБОК")
    kb.add("🎫 ПРОМО", "💰 ВЫДАТЬ")
    kb.add("🎁 ФРИБЕТ", "📸 ФОТО")
    kb.add("📋 КВЕСТ", "📋 МЕНЮ")
    return kb

def league_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🏒 БХМ", callback_data="league_БХМ"))
    kb.add(types.InlineKeyboardButton("🏒 МХМ", callback_data="league_МХМ"))
    kb.add(types.InlineKeyboardButton("🏒 КХМ", callback_data="league_КХМ"))
    return kb

def matches_keyboard(league):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT match_id, team1, team2, match_date FROM matches WHERE league=? AND status='upcoming' ORDER BY match_date", (league,))
    matches = c.fetchall()
    conn.close()
    kb = types.InlineKeyboardMarkup(row_width=1)
    if not matches:
        kb.add(types.InlineKeyboardButton("❌ Нет матчей", callback_data="none"))
    else:
        for m in matches:
            kb.add(types.InlineKeyboardButton(f"⚔ {m[1]} vs {m[2]} | {m[3]}", callback_data=f"match_{m[0]}"))
    kb.add(types.InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh_{league}"))
    kb.add(types.InlineKeyboardButton("🔙 К лигам", callback_data="back_to_leagues"))
    return kb

def bet_keyboard(mid):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT team1, team2, coef1, coef2, coef_draw, total_line, coef_over, coef_under FROM matches WHERE match_id=?", (mid,))
    m = c.fetchone()
    conn.close()
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(f"✅ {m[0]} x{m[2]}", callback_data=f"betsum_{mid}_{m[0]}"))
    kb.add(types.InlineKeyboardButton(f"🤝 Ничья x{m[4]}", callback_data=f"betsum_{mid}_Ничья"))
    kb.add(types.InlineKeyboardButton(f"✅ {m[1]} x{m[3]}", callback_data=f"betsum_{mid}_{m[1]}"))
    kb.add(types.InlineKeyboardButton(f"📊 ТБ {m[5]} x{m[6]}", callback_data=f"betsum_{mid}_over"))
    kb.add(types.InlineKeyboardButton(f"📊 ТМ {m[5]} x{m[7]}", callback_data=f"betsum_{mid}_under"))
    kb.add(types.InlineKeyboardButton(f"🎯 Точный счёт x25", callback_data=f"exact_{mid}"))
    kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_matches"))
    return kb

def sum_keyboard(mid, team):
    kb = types.InlineKeyboardMarkup(row_width=3)
    for amt in [100, 500, 1000, 2500, 5000]:
        kb.add(types.InlineKeyboardButton(str(amt), callback_data=f"place_{mid}_{team}_{amt}"))
    kb.add(types.InlineKeyboardButton("Своя", callback_data=f"custom_{mid}_{team}"))
    return kb

user_match_creation = {}

# ========== ОБРАБОТЧИКИ КОМАНД ==========
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
    if uid == ADMIN_ID:
        safe_send(message.chat.id, "🏒 EXTRABET\n💰 1000 тенге", admin_keyboard())
    else:
        safe_send(message.chat.id, "🏒 EXTRABET\n💰 1000 тенге", main_keyboard(uid))

@bot.message_handler(func=lambda m: m.text == "🏒 СТАВКИ")
def show_leagues(message):
    safe_send(message.chat.id, "🏒 ВЫБЕРИТЕ ЛИГУ:", league_keyboard())

@bot.message_handler(func=lambda m: m.text == "🏆 КУБОК")
def show_cup(message):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT DISTINCT league FROM cup_teams")
    leagues = c.fetchall()
    conn.close()
    if leagues:
        kb = types.InlineKeyboardMarkup(row_width=1)
        for l in leagues: 
            kb.add(types.InlineKeyboardButton(f"🏆 {l[0]}", callback_data=f"cup_{l[0]}"))
        safe_send(message.chat.id, "🏆 ОБЛАДАТЕЛЬ КУБКА\nВыберите лигу:", kb)
    else: 
        safe_send(message.chat.id, "❌ Нет команд")

@bot.message_handler(func=lambda m: m.text == "👤 ПРОФИЛЬ")
def profile(message):
    uid = message.from_user.id
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    u = c.fetchone()
    conn.close()
    if u:
        wr = (u[5]/u[4]*100) if u[4] > 0 else 0
        safe_send(message.chat.id, f"👤 {u[1]}\n💰 {u[2]} тенге\n🎁 Фрибеты: {u[3]}\n📊 Ставок: {u[4]}\n✅ Побед: {u[5]} ({wr:.1f}%)")

@bot.message_handler(func=lambda m: m.text == "💰 БАЛАНС")
def balance(message):
    uid = message.from_user.id
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT balance, freebets FROM users WHERE user_id=?", (uid,))
    d = c.fetchone()
    conn.close()
    safe_send(message.chat.id, f"💰 Баланс: {d[0]} тенге\n🎁 Фрибетов: {d[1]}")

@bot.message_handler(func=lambda m: m.text == "🎁 ЕЖЕДНЕВНЫЙ БОНУС")
def daily_bonus(message):
    uid = message.from_user.id
    today = msk_now().strftime("%d.%m.%Y")
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT daily_bonus_date FROM users WHERE user_id=?", (uid,))
    result = c.fetchone()
    last = result[0] if result else None
    if last == today:
        safe_send(message.chat.id, "❌ Уже получали сегодня!")
        conn.close()
        return
    bonus = random.randint(0, 1000)
    c.execute("UPDATE users SET balance=balance+?, daily_bonus_date=? WHERE user_id=?", (bonus, today, uid))
    conn.commit()
    conn.close()
    safe_send(message.chat.id, f"🎁 +{bonus} тенге!" if bonus > 0 else "😢 0 тенге")

@bot.message_handler(func=lambda m: m.text == "🎫 ПРОМОКОД")
def promo_activate(message):
    msg = safe_send(message.chat.id, "🎫 Отправьте промокод:")
    bot.register_next_step_handler(msg, process_promo)

def process_promo(message):
    uid = message.from_user.id
    code = message.text.strip().upper()
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT * FROM promocodes WHERE code=? AND is_active=1", (code,))
    p = c.fetchone()
    if not p: 
        safe_send(message.chat.id, "❌ Промокод не найден!")
        conn.close()
        return
    if p[3] <= p[4]: 
        safe_send(message.chat.id, "❌ Лимит использований!")
        conn.close()
        return
    c.execute("SELECT id FROM used_promos WHERE user_id=? AND promo_code=?", (uid, code))
    if c.fetchone(): 
        safe_send(message.chat.id, "❌ Уже использован!")
        conn.close()
        return
    
    c.execute("INSERT INTO used_promos (user_id, promo_code, used_date) VALUES (?,?,?)", (uid, code, msk_now().strftime("%d.%m.%Y %H:%M")))
    c.execute("UPDATE promocodes SET used_count=used_count+1 WHERE code=?", (code,))
    c.execute("UPDATE users SET freebets=freebets+1 WHERE user_id=?", (uid,))
    c.execute("""INSERT INTO bets (user_id, username, match_id, team, amount, coefficient, bet_type, status, bet_time) 
                 VALUES (?, (SELECT username FROM users WHERE user_id=?), 0, 'ФРИБЕТ ПРОМОКОДА', ?, 1.0, 'freebet', 'pending', ?)""",
        (uid, uid, p[2], msk_now().strftime("%d.%m.%Y %H:%M")))
    
    if p[4]+1 >= p[3]: 
        c.execute("UPDATE promocodes SET is_active=0 WHERE code=?", (code,))
    conn.commit()
    conn.close()
    safe_send(message.chat.id, f"🎁 Промокод активирован! +1 фрибет на {p[2]} тенге!")

@bot.message_handler(func=lambda m: m.text == "🎁 ФРИБЕТЫ")
def show_freebets(message):
    uid = message.from_user.id
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT freebets FROM users WHERE user_id=?", (uid,))
    user_freebet_count = c.fetchone()[0]
    c.execute("SELECT bet_id, amount, status FROM bets WHERE user_id=? AND bet_type='freebet' ORDER BY bet_id DESC", (uid,))
    all_freebets = c.fetchall()
    conn.close()
    available_freebets = [fb for fb in all_freebets if fb[2] == 'pending']
    if available_freebets:
        kb = types.InlineKeyboardMarkup(row_width=1)
        for fb in available_freebets: 
            kb.add(types.InlineKeyboardButton(f"🎯 Использовать фрибет {fb[1]}💰", callback_data=f"usefree_{fb[0]}"))
        safe_send(message.chat.id, f"🎁 Ваши фрибеты (доступно: {len(available_freebets)}):", kb)
    else:
        if user_freebet_count > 0:
            safe_send(message.chat.id, "⚠️ Фрибеты восстанавливаются...")
            conn = sqlite3.connect('hockey_bets.db')
            c = conn.cursor()
            needed = user_freebet_count - len(available_freebets)
            for i in range(needed):
                amount = 500
                c.execute("""INSERT INTO bets (user_id, username, match_id, team, amount, coefficient, bet_type, status, bet_time) 
                             VALUES (?, (SELECT username FROM users WHERE user_id=?), 0, 'ВОССТАНОВЛЕННЫЙ ФРИБЕТ', ?, 1.0, 'freebet', 'pending', ?)""",
                    (uid, uid, amount, msk_now().strftime("%d.%m.%Y %H:%M")))
            conn.commit()
            conn.close()
            show_freebets(message)
        else:
            safe_send(message.chat.id, "🎁 У вас нет фрибетов.")

@bot.message_handler(func=lambda m: m.text == "📋 КВЕСТЫ")
def show_quests(message):
    uid = message.from_user.id
    today = msk_now().strftime("%d.%m.%Y")
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM bets WHERE user_id=? AND bet_time LIKE ?", (uid, today+'%'))
    total_bets = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM bets WHERE user_id=? AND status='won' AND bet_time LIKE ?", (uid, today+'%'))
    total_wins = c.fetchone()[0]
    c.execute("SELECT quest_claimed FROM users WHERE user_id=?", (uid,))
    result = c.fetchone()
    claimed_str = result[0] if result and result[0] else ''
    claimed = claimed_str.split(',') if claimed_str else []
    c.execute("SELECT quest_id, description, quest_type, target, freebet_amount FROM quests WHERE is_active=1")
    quests = c.fetchall()
    conn.close()
    txt = f"📋 КВЕСТЫ\n📊 Ставок: {total_bets}\n✅ Побед: {total_wins}\n\n"
    if quests:
        for q in quests:
            qid, desc, qtype, target, fb_amt = q
            if str(qid) in claimed:
                txt += f"🟢 #{qid}: {desc} [ВЫПОЛНЕНО]\n"
            else:
                current = total_bets if qtype == 'bets' else total_wins
                txt += f"🟡 #{qid}: {desc}\n🎯 {current}/{target}\n🎁 1 фрибет на {fb_amt} тенге\n\n"
    else:
        txt += "Нет активных квестов"
    safe_send(message.chat.id, txt)

@bot.message_handler(func=lambda m: m.text == "🏆 ТОП-10")
def top10(message):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT username, balance FROM users ORDER BY balance DESC LIMIT 10")
    users = c.fetchall()
    conn.close()
    txt = "🏆 ТОП-10:\n\n" if users else "Пусто"
    for i, u in enumerate(users, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        txt += f"{medal} {u[0]}: 💰{u[1]} тенге\n"
    safe_send(message.chat.id, txt)

@bot.message_handler(func=lambda m: m.text == "🔧 АДМИН")
def admin_menu(message):
    if message.from_user.id != ADMIN_ID: return
    safe_send(message.chat.id, "👑 АДМИН-ПАНЕЛЬ", admin_keyboard())

@bot.message_handler(func=lambda m: m.text == "📋 МЕНЮ")
def menu(message):
    uid = message.from_user.id
    if uid == ADMIN_ID:
        safe_send(message.chat.id, "📋 Меню", admin_keyboard())
    else:
        safe_send(message.chat.id, "📋 Меню", main_keyboard(uid))

@bot.message_handler(func=lambda m: m.text == "➕ МАТЧ")
def add_match(message):
    if message.from_user.id != ADMIN_ID: return
    msg = safe_send(message.chat.id, "➕ ЛИГА | Команда1 vs Команда2 | ДД.ММ.ГГГГ ЧЧ:ММ | П1 П2 Н П1ОТ П2ОТ Тотал ТБ ТМ")
    bot.register_next_step_handler(msg, add_match_step2)

def add_match_step2(message):
    try:
        p = message.text.split('|')
        l, t, d = p[0].strip(), p[1].strip(), p[2].strip()
        t1, t2 = t.split(' vs ')
        datetime.strptime(d, "%d.%m.%Y %H:%M")
        coefs = list(map(float, p[3].strip().split())) if len(p) >= 4 else []
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        if len(coefs) >= 8:
            c.execute("""INSERT INTO matches (league,team1,team2,match_date,coef1,coef2,coef_draw,coef_ot1,coef_ot2,total_line,coef_over,coef_under) 
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (l, t1.strip(), t2.strip(), d, coefs[0], coefs[1], coefs[2], coefs[3], coefs[4], coefs[5], coefs[6], coefs[7]))
        else: 
            c.execute("INSERT INTO matches (league,team1,team2,match_date) VALUES (?,?,?,?)", (l, t1.strip(), t2.strip(), d))
        conn.commit()
        conn.close()
        safe_send(message.chat.id, f"✅ Матч добавлен!")
    except Exception as e:
        safe_send(message.chat.id, f"❌ Ошибка: {e}")

@bot.message_handler(func=lambda m: m.text == "📋 МАТЧИ")
def admin_matches(message):
    if message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT match_id, league, team1, team2, match_date, status FROM matches ORDER BY match_date DESC LIMIT 20")
    ms = c.fetchall()
    conn.close()
    txt = "📋 МАТЧИ:\n\n" if ms else "Нет матчей"
    for m in ms:
        s = "🟢" if m[5] == 'upcoming' else "🔴"
        txt += f"{s} #{m[0]}: {m[2]} vs {m[3]} ({m[1]}) | {m[4]}\n"
    safe_send(message.chat.id, txt)

@bot.message_handler(func=lambda m: m.text == "🏆 РЕЗУЛЬТАТ")
def set_result_start(message):
    if message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT match_id, team1, team2 FROM matches WHERE status='upcoming'")
    ms = c.fetchall()
    conn.close()
    if ms:
        kb = types.InlineKeyboardMarkup(row_width=1)
        for m in ms:
            kb.add(types.InlineKeyboardButton(f"#{m[0]} {m[1]} vs {m[2]}", callback_data=f"setres_{m[0]}"))
        safe_send(message.chat.id, "🏆 Выберите матч:", kb)
    else:
        safe_send(message.chat.id, "Нет матчей")

@bot.message_handler(func=lambda m: m.text == "👁 СТАВКИ")
def view_bets(message):
    if message.from_user.id != ADMIN_ID: return
    
