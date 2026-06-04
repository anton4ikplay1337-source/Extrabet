import telebot
from telebot import types
import sqlite3
import random
from datetime import datetime, timedelta
import string
import re
import os
import time
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

# ========== НАСТРОЙКИ ==========
TOKEN = "8965196111:AAEXbplXNwdq_AbYZFFAu-H-fRLsGbxUK_o"
ADMIN_ID = 5706071030
bot = telebot.TeleBot(TOKEN)

# ========== HTTP-СЕРВЕР ДЛЯ ПИНГОВ (24/7) ==========
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bot is alive!')
    
    def log_message(self, format, *args):
        pass

def run_health_server():
    server = HTTPServer(('0.0.0.0', 10000), HealthHandler)
    server.serve_forever()

Thread(target=run_health_server, daemon=True).start()
print("🏥 Health server started on port 10000")

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
    
    # Добавляем тестовый квест, если нет ни одного
    c.execute("SELECT COUNT(*) FROM quests")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO quests (description, quest_type, target, freebet_amount, duration_hours, is_active, created_date) VALUES (?,?,?,?,?,?,?)",
                  ("Сделай 1 ставку", "bets", 1, 500, 24, 1, msk_now().strftime("%d.%m.%Y %H:%M")))
    
    conn.commit()
    conn.close()

def generate_promo_code(length=8):
    return ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(length))

def safe_send(chat_id, text, reply_markup=None):
    try: 
        return bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception as e:
        print(f"Ошибка safe_send: {e}")
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
        wr = (u[5]/u[4]*100) if u[4]>0 else 0
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

@bot.message_handler(func=lambda m: m.text == "📊 ИСТОРИЯ")
def history(message):
    uid = message.from_user.id
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT b.team, b.amount, b.status, m.team1, m.team2, b.bet_time FROM bets b JOIN matches m ON b.match_id=m.match_id WHERE b.user_id=? ORDER BY b.bet_id DESC LIMIT 5", (uid,))
    bets = c.fetchall()
    conn.close()
    txt = "📊 ИСТОРИЯ:\n\n" if bets else "Нет ставок"
    for b in bets:
        em = "✅" if b[2]=="won" else "❌" if b[2]=="lost" else "⏳"
        txt += f"{em} {b[3]} vs {b[4]}: {b[1]} тенге на {b[0]} | {b[5]}\n"
    safe_send(message.chat.id, txt)

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
        safe_send(message.chat.id, "❌ Промокод не найден!"); 
        conn.close(); 
        return
    if p[3] <= p[4]: 
        safe_send(message.chat.id, "❌ Лимит использований исчерпан!"); 
        conn.close(); 
        return
    c.execute("SELECT id FROM used_promos WHERE user_id=? AND promo_code=?", (uid, code))
    if c.fetchone(): 
        safe_send(message.chat.id, "❌ Вы уже использовали этот промокод!"); 
        conn.close(); 
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

# ========== ИСПРАВЛЕННАЯ ФУНКЦИЯ ФРИБЕТОВ ==========
@bot.message_handler(func=lambda m: m.text == "🎁 ФРИБЕТЫ")
def show_freebets(message):
    uid = message.from_user.id
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    
    # Проверяем сколько фрибетов в счетчике пользователя
    c.execute("SELECT freebets FROM users WHERE user_id=?", (uid,))
    user_freebet_count = c.fetchone()[0]
    
    # Ищем все фрибеты в таблице bets
    c.execute("SELECT bet_id, amount, status FROM bets WHERE user_id=? AND bet_type='freebet' ORDER BY bet_id DESC", (uid,))
    all_freebets = c.fetchall()
    conn.close()
    
    # Фильтруем только доступные (status='pending')
    available_freebets = [fb for fb in all_freebets if fb[2] == 'pending']
    
    if available_freebets:
        kb = types.InlineKeyboardMarkup(row_width=1)
        for fb in available_freebets: 
            kb.add(types.InlineKeyboardButton(f"🎯 Использовать фрибет {fb[1]}💰", callback_data=f"usefree_{fb[0]}"))
        safe_send(message.chat.id, f"🎁 Ваши фрибеты (доступно: {len(available_freebets)}):", kb)
    else:
        if user_freebet_count > 0:
            safe_send(message.chat.id, f"⚠️ У вас есть {user_freebet_count} фрибет(ов) в счетчике, но они отсутствуют в системе. Исправляем...")
            
            # Восстанавливаем недостающие фрибеты
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
            
            # Показываем обновленный список
            show_freebets(message)
        else:
            safe_send(message.chat.id, "🎁 У вас нет фрибетов. Выполняйте квесты или активируйте промокоды!")

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
    
    txt = f"📋 КВЕСТЫ\n📊 Ставок сегодня: {total_bets}\n✅ Побед сегодня: {total_wins}\n\n"
    if quests:
        for q in quests:
            qid, desc, qtype, target, fb_amt = q
            if str(qid) in claimed:
                txt += f"🟢 #{qid}: {desc} [ВЫПОЛНЕНО]\n"
            else:
                current = total_bets if qtype == 'bets' else total_wins
                txt += f"🟡 #{qid}: {desc}\n🎯 {current}/{target}\n🎁 1 фрибет на {fb_amt} тенге\n\n"
    else:
        txt += "Пока нет активных квестов"
    safe_send(message.chat.id, txt)

@bot.message_handler(func=lambda m: m.text == "🏆 ТОП-10")
def top10(message):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT username, balance FROM users ORDER BY balance DESC LIMIT 10")
    users = c.fetchall()
    conn.close()
    txt = "🏆 ТОП-10 БОГАТЕЙШИХ:\n\n" if users else "Пусто"
    for i, u in enumerate(users, 1):
        medal = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else f"{i}."
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
        safe_send(message.chat.id, "📋 Главное меню", admin_keyboard())
    else:
        safe_send(message.chat.id, "📋 Главное меню", main_keyboard(uid))

# ========== АДМИН-ФУНКЦИИ ==========
@bot.message_handler(func=lambda m: m.text == "➕ МАТЧ")
def add_match(message):
    if message.from_user.id != ADMIN_ID: return
    msg = safe_send(message.chat.id, "➕ Формат: ЛИГА | Команда1 vs Команда2 | ДД.ММ.ГГГГ ЧЧ:ММ | П1 П2 Н П1ОТ П2ОТ Тотал ТБ ТМ\n\nПример: БХМ | Салават Юлаев vs Ак Барс | 25.12.2024 19:00 | 2.3 2.3 3.5 3.5 3.5 5.5 1.9 1.9")
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
        safe_send(message.chat.id, f"✅ Матч добавлен!\n{l}\n⚔ {t1} vs {t2}\n📅 {d}")
    except Exception as e:
        safe_send(message.chat.id, f"❌ Ошибка! {str(e)}")

@bot.message_handler(func=lambda m: m.text == "📋 МАТЧИ")
def admin_matches(message):
    if message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT match_id, league, team1, team2, match_date, status, winner, score FROM matches ORDER BY match_date DESC LIMIT 20")
    ms = c.fetchall()
    conn.close()
    if not ms:
        safe_send(message.chat.id, "Нет матчей")
        return
    txt = "📋 СПИСОК МАТЧЕЙ:\n\n"
    for m in ms:
        status_emoji = "🟢" if m[5]=='upcoming' else "🔴"
        txt += f"{status_emoji} #{m[0]}: {m[2]} vs {m[3]} ({m[1]}) | {m[4]}"
        if m[6]: txt += f" | Победитель: {m[6]}"
        if m[7]: txt += f" | Счёт: {m[7]}"
        txt += "\n"
    safe_send(message.chat.id, txt)

@bot.message_handler(func=lambda m: m.text == "🏆 РЕЗУЛЬТАТ")
def set_result_start(message):
    if message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT match_id, team1, team2, match_date FROM matches WHERE status='upcoming' ORDER BY match_date")
    ms = c.fetchall()
    conn.close()
    if ms:
        kb = types.InlineKeyboardMarkup(row_width=1)
        for m in ms: 
            kb.add(types.InlineKeyboardButton(f"#{m[0]} {m[1]} vs {m[2]} | {m[3]}", callback_data=f"setres_{m[0]}"))
        safe_send(message.chat.id, "🏆 Выберите матч для установки результата:", kb)
    else: 
        safe_send(message.chat.id, "Нет активных матчей для завершения")

@bot.message_handler(func=lambda m: m.text == "👁 СТАВКИ")
def view_bets(message):
    if message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT match_id, team1, team2 FROM matches WHERE status='upcoming' ORDER BY match_date LIMIT 15")
    ms = c.fetchall()
    conn.close()
    if not ms: 
        safe_send(message.chat.id, "Нет активных матчей"); 
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    for m in ms: 
        kb.add(types.InlineKeyboardButton(f"#{m[0]} {m[1]} vs {m[2]}", callback_data=f"viewbets_{m[0]}"))
    safe_send(message.chat.id, "👁 Выберите матч для просмотра ставок:", kb)

@bot.message_handler(func=lambda m: m.text == "🏆 РЕЗ. КУБКА")
def cup_result(message):
    if message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT DISTINCT league FROM cup_teams")
    leagues = c.fetchall()
    conn.close()
    if leagues:
        kb = types.InlineKeyboardMarkup(row_width=1)
        for l in leagues:
            kb.add(types.InlineKeyboardButton(f"🏆 {l[0]}", callback_data=f"cupres_{l[0]}"))
        safe_send(message.chat.id, "🏆 Выберите лигу для определения победителя кубка:", kb)
    else:
        safe_send(message.chat.id, "Нет команд в кубке")

@bot.message_handler(func=lambda m: m.text == "➕ КУБОК")
def add_cup(message):
    if message.from_user.id != ADMIN_ID: return
    msg = safe_send(message.chat.id, "🏆 Формат: ЛИГА | Команда | Кэф\n\nПример: БХМ | Салават Юлаев | 3.0")
    bot.register_next_step_handler(msg, add_cup_step2)

def add_cup_step2(message):
    try:
        p = message.text.split('|')
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("INSERT INTO cup_teams (league, team, coefficient) VALUES (?,?,?)", (p[0].strip(), p[1].strip(), float(p[2].strip())))
        conn.commit()
        conn.close()
        safe_send(message.chat.id, f"✅ Команда добавлена в кубок!\n{p[1].strip()} (коэффициент: {p[2].strip()})")
    except: 
        safe_send(message.chat.id, "❌ Ошибка! Используйте формат: ЛИГА | Команда | Кэф")

@bot.message_handler(func=lambda m: m.text == "🎫 ПРОМО")
def create_promo(message):
    if message.from_user.id != ADMIN_ID: return
    msg = safe_send(message.chat.id, "🎫 Варианты:\n1. КОД | СУММА | КОЛ-ВО\n2. СУММА КОЛ-ВО (код сгенерируется автоматически)\n\nПример: BONUS100 | 500 | 10")
    bot.register_next_step_handler(msg, create_promo_step2)

def create_promo_step2(message):
    try:
        p = message.text.split('|')
        if len(p) == 3: 
            code, amt, mx = p[0].strip().upper(), int(p[1].strip()), int(p[2].strip())
        else:
            parts = message.text.split()
            amt, mx = int(parts[0]), int(parts[1])
            code = generate_promo_code()
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("INSERT INTO promocodes (code, freebet_amount, max_uses) VALUES (?,?,?)", (code, amt, mx))
        conn.commit()
        conn.close()
        safe_send(message.chat.id, f"✅ Промокод создан!\nКод: {code}\nСумма фрибета: {amt} тенге\nКол-во использований: {mx}")
    except: 
        safe_send(message.chat.id, "❌ Ошибка! Проверьте формат.")

@bot.message_handler(func=lambda m: m.text == "💰 ВЫДАТЬ")
def give_money(message):
    if message.from_user.id != ADMIN_ID: return
    msg = safe_send(message.chat.id, "💰 Формат: ID_ПОЛЬЗОВАТЕЛЯ СУММА\n\nПример: 5706071030 5000")
    bot.register_next_step_handler(msg, give_money_step2)

def give_money_step2(message):
    try:
        p = message.text.split()
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (int(p[1]), int(p[0])))
        conn.commit()
        conn.close()
        safe_send(message.chat.id, f"✅ Выдано {p[1]} тенге пользователю {p[0]}!")
        try:
            bot.send_message(int(p[0]), f"💰 Администратор выдал вам {p[1]} тенге!")
        except:
            pass
    except: 
        safe_send(message.chat.id, "❌ Ошибка! Используйте формат: ID СУММА")

# ========== ИСПРАВЛЕННАЯ ФУНКЦИЯ ВЫДАЧИ ФРИБЕТА АДМИНОМ ==========
@bot.message_handler(func=lambda m: m.text == "🎁 ФРИБЕТ")
def give_freebet(message):
    if message.from_user.id != ADMIN_ID: return
    msg = safe_send(message.chat.id, "🎁 Формат: ID_ПОЛЬЗОВАТЕЛЯ СУММА\n\nПример: 5706071030 1000")
    bot.register_next_step_handler(msg, give_freebet_step2)

def give_freebet_step2(message):
    try:
        p = message.text.split()
        user_id = int(p[0])
        amount = int(p[1])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        
        c.execute("UPDATE users SET freebets=freebets+1 WHERE user_id=?", (user_id,))
        c.execute("""INSERT INTO bets (user_id, username, match_id, team, amount, coefficient, bet_type, status, bet_time) 
                     VALUES (?, (SELECT username FROM users WHERE user_id=?), 0, 'ФРИБЕТ ОТ АДМИНА', ?, 1.0, 'freebet', 'pending', ?)""",
            (user_id, user_id, amount, msk_now().strftime("%d.%m.%Y %H:%M")))
        
        conn.commit()
        conn.close()
        safe_send(message.chat.id, f"✅ Фрибет на {amount} тенге выдан пользователю {user_id}!")
        try:
            bot.send_message(user_id, f"🎁 Администратор выдал вам фрибет на {amount} тенге! Нажмите кнопку '🎁 ФРИБЕТЫ' чтобы использовать!")
        except:
            pass
    except: 
        safe_send(message.chat.id, "❌ Ошибка! Используйте формат: ID СУММА")

@bot.message_handler(func=lambda m: m.text == "📸 ФОТО")
def photo_menu(message):
    if message.from_user.id != ADMIN_ID: return
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("🏆 Фото победы", callback_data="photo_win"), 
           types.InlineKeyboardButton("💔 Фото поражения", callback_data="photo_lose"))
    kb.add(types.InlineKeyboardButton("📋 Показать текущие фото", callback_data="photo_show"))
    safe_send(message.chat.id, "📸 Управление фото для уведомлений:", kb)

@bot.message_handler(func=lambda m: m.text == "📋 КВЕСТ")
def add_quest(message):
    if message.from_user.id != ADMIN_ID: return
    msg = safe_send(message.chat.id, "📋 Формат: Описание | Тип (bets/wins) | Цель | Сумма фрибета | Длительность(часы)\n\nПример: Поставь 3 ставки | bets | 3 | 500 | 24")
    bot.register_next_step_handler(msg, add_quest_step2)

def add_quest_step2(message):
    try:
        p = message.text.split('|')
        desc = p[0].strip()
        qtype = p[1].strip().lower()
        target = int(p[2].strip())
        freebet = int(p[3].strip())
        duration = int(p[4].strip())
        if qtype not in ('bets', 'wins'):
            safe_send(message.chat.id, "❌ Тип должен быть 'bets' или 'wins'")
            return
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("""INSERT INTO quests (description, quest_type, target, freebet_amount, duration_hours, created_date, is_active) 
                     VALUES (?,?,?,?,?,?,1)""",
            (desc, qtype, target, freebet, duration, msk_now().strftime("%d.%m.%Y %H:%M")))
        conn.commit()
        conn.close()
        safe_send(message.chat.id, f"✅ Квест создан!\n{desc}\n🎯 {qtype} {target}\n🎁 Фрибет на {freebet} тенге\n⏰ Длительность: {duration}ч")
    except Exception as e:
        safe_send(message.chat.id, f"❌ Ошибка! {str(e)}\nИспользуйте формат: Описание | bets/wins | цель | сумма | часы")

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    uid = message.from_user.id
    if uid != ADMIN_ID:
        bot.reply_to(message, "❌ Только администратор может загружать фото")
        return
        
    if uid in user_match_creation and 'photo_type' in user_match_creation[uid]:
        pt = user_match_creation[uid]['photo_type']
        fid = message.photo[-1].file_id
        
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("DELETE FROM photos WHERE photo_type=?", (pt,))
        c.execute("INSERT INTO photos (photo_type, file_id, added_date) VALUES (?,?,?)", 
                 (pt, fid, msk_now().strftime("%d.%m.%Y %H:%M")))
        conn.commit()
        conn.close()
        
        safe_send(message.chat.id, f"✅ Фото {'🏆 ПОБЕДЫ' if pt=='win' else '💔 ПОРАЖЕНИЯ'} обновлено!")
        del user_match_creation[uid]
    else:
        safe_send(message.chat.id, "❌ Сначала выберите тип фото в меню Админ -> Фото")

# ========== CALLBACK ОБРАБОТЧИК ==========
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    uid = call.from_user.id
    d = call.data
    
    if d == "photo_win":
        if uid != ADMIN_ID: 
            bot.answer_callback_query(call.id, "Доступно только администратору")
            return
        user_match_creation[uid] = {'photo_type': 'win'}
        bot.answer_callback_query(call.id, "Отправьте фото для ПОБЕДЫ")
        return
    elif d == "photo_lose":
        if uid != ADMIN_ID: 
            bot.answer_callback_query(call.id, "Доступно только администратору")
            return
        user_match_creation[uid] = {'photo_type': 'lose'}
        bot.answer_callback_query(call.id, "Отправьте фото для ПОРАЖЕНИЯ")
        return
    elif d == "photo_show":
        if uid != ADMIN_ID: 
            bot.answer_callback_query(call.id, "Доступно только администратору")
            return
        for t, n in [('win','🏆 Фото победы'), ('lose','💔 Фото поражения')]:
            p = get_photo(t)
            if p:
                try: 
                    bot.send_photo(call.message.chat.id, p, caption=n)
                except:
                    pass
            else:
                safe_send(call.message.chat.id, f"❌ {n} не установлено")
        bot.answer_callback_query(call.id)
        return
    
    elif d.startswith("league_"):
        league = d.split("_")[1]
        try:
            bot.edit_message_text(f"🏒 {league}", call.message.chat.id, call.message.message_id, 
                                 reply_markup=matches_keyboard(league))
        except:
            safe_send(call.message.chat.id, f"🏒 {league}", matches_keyboard(league))
    
    elif d == "back_to_leagues":
        try:
            bot.edit_message_text("🏒 ВЫБЕРИТЕ ЛИГУ:", call.message.chat.id, call.message.message_id, 
                                 reply_markup=league_keyboard())
        except:
            safe_send(call.message.chat.id, "🏒 ВЫБЕРИТЕ ЛИГУ:", league_keyboard())
    
    elif d.startswith("refresh_"):
        league = d.split("_")[1]
        try:
            bot.edit_message_text("🔄 Обновлено", call.message.chat.id, call.message.message_id, 
                                 reply_markup=matches_keyboard(league))
        except:
            safe_send(call.message.chat.id, "🔄 Обновлено", matches_keyboard(league))
    
    elif d == "back_to_matches":
        try:
            bot.edit_message_text("🎯 Выберите матч:", call.message.chat.id, call.message.message_id, 
                                 reply_markup=league_keyboard())
        except:
            safe_send(call.message.chat.id, "🎯 Выберите матч:", league_keyboard())
    
    elif d == "none":
        bot.answer_callback_query(call.id, "Нет матчей")
        return
    
    elif d.startswith("viewbets_"):
        if uid != ADMIN_ID: 
            bot.answer_callback_query(call.id, "Доступно только администратору")
            return
        mid = int(d.split("_")[1])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT team1, team2 FROM matches WHERE match_id=?", (mid,))
        m = c.fetchone()
        c.execute("SELECT username, team, amount, bet_type, coefficient, status, bet_time FROM bets WHERE match_id=? ORDER BY bet_id DESC", (mid,))
        bets = c.fetchall()
        conn.close()
        txt = f"👁 СТАВКИ НА МАТЧ #{mid}\n{m[0]} vs {m[1]}:\n\n"
        if bets:
            for b in bets:
                status_emoji = "✅" if b[5]=="won" else "❌" if b[5]=="lost" else "⏳"
                bet_type_text = ""
                if b[3] == "over": bet_type_text = "[ТБ]"
                elif b[3] == "under": bet_type_text = "[ТМ]"
                elif b[3] == "exact": bet_type_text = "[Точный счёт]"
                elif b[3] == "freebet": bet_type_text = "[Фрибет]"
                txt += f"{status_emoji} {b[0]}: {b[1]}💰 {bet_type_text} на {b[2]} (x{b[4]}) | {b[6]}\n"
        else: 
            txt += "Нет ставок"
        safe_send(call.message.chat.id, txt)
        bot.answer_callback_query(call.id)
        return
    
    elif d.startswith("setres_"):
        if uid != ADMIN_ID: 
            bot.answer_callback_query(call.id, "Доступно только администратору")
            return
        mid = int(d.split("_")[1])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT team1, team2 FROM matches WHERE match_id=?", (mid,))
        m = c.fetchone()
        conn.close()
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton(f"✅ {m[0]}", callback_data=f"winner_{mid}_{m[0]}"))
        kb.add(types.InlineKeyboardButton(f"✅ {m[1]}", callback_data=f"winner_{mid}_{m[1]}"))
        kb.add(types.InlineKeyboardButton(f"🤝 Ничья", callback_data=f"winner_{mid}_Ничья"))
        try:
            bot.edit_message_text(f"⚔ {m[0]} vs {m[1]}\n\nКто победил?", 
                                 call.message.chat.id, call.message.message_id, reply_markup=kb)
        except:
            safe_send(call.message.chat.id, f"⚔ {m[0]} vs {m[1]}\n\nКто победил?", kb)
        bot.answer_callback_query(call.id)
        return
    
    elif d.startswith("winner_"):
        if uid != ADMIN_ID: 
            bot.answer_callback_query(call.id, "Доступно только администратору")
            return
        parts = d.split("_", 2)
        mid = int(parts[1])
        winner = parts[2]
        msg = safe_send(call.message.chat.id, f"Введите счёт (например 5:3):")
        bot.register_next_step_handler(msg, process_result, mid, winner)
        bot.answer_callback_query(call.id)
        return
    
    elif d.startswith("match_"):
        mid = int(d.split("_")[1])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT * FROM matches WHERE match_id=?", (mid,))
        m = c.fetchone()
        conn.close()
        if m:
            txt = f"⚔ {m[2]} vs {m[3]} ({m[1]})\n📅 {m[4]} МСК\n\nКоэффициенты:\nП1: {m[5]} | Ничья: {m[7]} | П2: {m[6]}\nТотал {m[10]}: ТБ={m[11]} | ТМ={m[12]}\n🎯 Точный счёт: x25"
            try:
                bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, 
                                     reply_markup=bet_keyboard(mid))
            except:
                safe_send(call.message.chat.id, txt, bet_keyboard(mid))
    
    elif d.startswith("betsum_"):
        parts = d.split("_")
        mid = int(parts[1])
        team = parts[2]
        label = "Тотал БОЛЬШЕ" if team == 'over' else "Тотал МЕНЬШЕ" if team == 'under' else team
        try:
            bot.edit_message_text(f"💰 Ставка: {label}\n\nВыберите сумму:", 
                                 call.message.chat.id, call.message.message_id, 
                                 reply_markup=sum_keyboard(mid, team))
        except:
            safe_send(call.message.chat.id, f"💰 Ставка: {label}\n\nВыберите сумму:", sum_keyboard(mid, team))
    
    elif d.startswith("place_"):
        parts = d.split("_")
        mid, team, amt = int(parts[1]), parts[2], int(parts[3])
        
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT team1, team2, coef1, coef2, coef_draw, total_line, coef_over, coef_under FROM matches WHERE match_id=?", (mid,))
        m = c.fetchone()
        if not m:
            bot.answer_callback_query(call.id, "❌ Матч не найден!", show_alert=True)
            conn.close()
            return
            
        if team == m[0]: coef = m[2]
        elif team == m[1]: coef = m[3]
        elif team == 'Ничья': coef = m[4]
        elif team == 'over': coef = m[6]
        elif team == 'under': coef = m[7]
        else: coef = 25.0
            
        bet_type = 'over' if team=='over' else 'under' if team=='under' else 'regular'
        if ':' in team: bet_type = 'exact'
            
        c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        bal = c.fetchone()
        
        if bal and bal[0] >= amt:
            c.execute("UPDATE users SET balance=balance-?, total_bets=total_bets+1 WHERE user_id=?", (amt, uid))
            c.execute("""INSERT INTO bets (user_id, username, match_id, team, amount, coefficient, bet_type, status, bet_time) 
                         VALUES (?,?,?,?,?,?,?,?,?)""", 
                (uid, call.from_user.username or "User", mid, team, amt, coef, bet_type, 'pending', 
                 msk_now().strftime("%d.%m.%Y %H:%M")))
            conn.commit()
            
            rewards = check_and_claim_quests(uid)
            
            msg = f"✅ СТАВКА ПРИНЯТА!\n🎯 {team}\n💰 {amt} тенге (x{coef})"
            if rewards: msg += "\n\n🎁 ВЫПОЛНЕНЫ КВЕСТЫ:\n" + "\n".join(rewards)
            
            bot.answer_callback_query(call.id, "✅ Ставка принята!")
            try:
                bot.edit_message_text(msg, call.message.chat.id, call.message.message_id)
            except:
                safe_send(call.message.chat.id, msg)
        else: 
            bot.answer_callback_query(call.id, "❌ Недостаточно тенге!", show_alert=True)
        conn.close()
    
    elif d.startswith("custom_"):
        parts = d.split("_")
        mid, team = int(parts[1]), parts[2]
        msg = safe_send(call.message.chat.id, f"💵 Введите сумму для ставки на {team}:")
        bot.register_next_step_handler(msg, process_custom, mid, team)
        bot.answer_callback_query(call.id)
    
    elif d.startswith("exact_"):
        mid = int(d.split("_")[1])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT team1, team2 FROM matches WHERE match_id=?", (mid,))
        m = c.fetchone()
        conn.close()
        msg = safe_send(call.message.chat.id, f"🎯 ТОЧНЫЙ СЧЁТ (x25)\n{m[0]} vs {m[1]}\n\nВведите счёт (пример: 3:2):")
        bot.register_next_step_handler(msg, process_exact, mid)
        bot.answer_callback_query(call.id)
    
    elif d.startswith("usefree_"):
        bid = int(d.split("_")[1])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT amount FROM bets WHERE bet_id=? AND bet_type='freebet' AND status='pending'", (bid,))
        fb = c.fetchone()
        conn.close()
        
        if fb:
            conn = sqlite3.connect('hockey_bets.db')
            c = conn.cursor()
            c.execute("SELECT match_id, team1, team2, league FROM matches WHERE status='upcoming' ORDER BY match_date")
            ms = c.fetchall()
            conn.close()
            
            if ms:
                kb = types.InlineKeyboardMarkup(row_width=1)
                for m in ms: 
                    kb.add(types.InlineKeyboardButton(f"⚔ {m[1]} vs {m[2]} ({m[3]})", callback_data=f"freebet_{bid}_{m[0]}"))
                try:
                    bot.edit_message_text(f"🎯 Выберите матч для фрибета\n💰 Сумма: {fb[0]} тенге", 
                                         call.message.chat.id, call.message.message_id, reply_markup=kb)
                except:
                    safe_send(call.message.chat.id, f"🎯 Выберите матч для фрибета\n💰 Сумма: {fb[0]} тенге", kb)
            else:
                bot.answer_callback_query(call.id, "Нет доступных матчей для ставки")
        else:
            bot.answer_callback_query(call.id, "❌ Фрибет не найден или уже использован!", show_alert=True)
    
    elif d.startswith("freebet_"):
        parts = d.split("_")
        bid, mid = int(parts[1]), int(parts[2])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT team1, team2, coef1, coef2, coef_draw FROM matches WHERE match_id=?", (mid,))
        m = c.fetchone()
        conn.close()
        
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton(f"✅ {m[0]} x{m[2]}", callback_data=f"freebetp_{bid}_{mid}_{m[0]}_{m[2]}"))
        kb.add(types.InlineKeyboardButton(f"🤝 Ничья x{m[4]}", callback_data=f"freebetp_{bid}_{mid}_Ничья_{m[4]}"))
        kb.add(types.InlineKeyboardButton(f"✅ {m[1]} x{m[3]}", callback_data=f"freebetp_{bid}_{mid}_{m[1]}_{m[3]}"))
        
        try:
            bot.edit_message_text(f"⚔ {m[0]} vs {m[1]}\n\nВыберите исход для фрибета:", 
                                 call.message.chat.id, call.message.message_id, reply_markup=kb)
        except:
            safe_send(call.message.chat.id, f"⚔ {m[0]} vs {m[1]}\n\nВыберите исход для фрибета:", kb)
    
    elif d.startswith("freebetp_"):
        parts = d.split("_")
        bid, mid = int(parts[1]), int(parts[2])
        team, coef = parts[3], float(parts[4])
        
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT amount FROM bets WHERE bet_id=?", (bid,))
        result = c.fetchone()
        if not result:
            conn.close()
            bot.answer_callback_query(call.id, "❌ Фрибет не найден!", show_alert=True)
            return
            
        amt = result[0]
        c.execute("""UPDATE bets SET match_id=?, team=?, coefficient=?, bet_type='freebet_active', status='pending', bet_time=? 
                     WHERE bet_id=?""", (mid, team, coef, msk_now().strftime("%d.%m.%Y %H:%M"), bid))
        c.execute("UPDATE users SET freebets=freebets-1 WHERE user_id=?", (uid,))
        conn.commit()
        
        rewards = check_and_claim_quests(uid)
        
        msg = f"✅ Фрибет активирован!\n🎯 {team}\n💰 {amt} тенге (x{coef})"
        if rewards: msg += "\n\n🎁 ВЫПОЛНЕНЫ КВЕСТЫ:\n" + "\n".join(rewards)
        
        conn.close()
        bot.answer_callback_query(call.id, "✅ Фрибет использован!")
        try:
            bot.edit_message_text(msg, call.message.chat.id, call.message.message_id)
        except:
            safe_send(call.message.chat.id, msg)
    
    elif d.startswith("cup_"):
        league = d.split("_")[1]
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT team, coefficient, team_id FROM cup_teams WHERE league=?", (league,))
        teams = c.fetchall()
        conn.close()
        
        kb = types.InlineKeyboardMarkup(row_width=1)
        for t in teams: 
            kb.add(types.InlineKeyboardButton(f"🏆 {t[0]} (x{t[1]})", callback_data=f"cupbet_{t[2]}_{t[0]}_{t[1]}"))
        kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_cup"))
        
        try:
            bot.edit_message_text(f"🏆 КУБОК {league}\n\nВыберите команду для ставки:", 
                                 call.message.chat.id, call.message.message_id, reply_markup=kb)
        except:
            safe_send(call.message.chat.id, f"🏆 КУБОК {league}\n\nВыберите команду для ставки:", kb)
    
    elif d == "back_to_cup":
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT DISTINCT league FROM cup_teams")
        leagues = c.fetchall()
        conn.close()
        if leagues:
            kb = types.InlineKeyboardMarkup(row_width=1)
            for l in leagues: 
                kb.add(types.InlineKeyboardButton(f"🏆 {l[0]}", callback_data=f"cup_{l[0]}"))
            try:
                bot.edit_message_text("🏆 ОБЛАДАТЕЛЬ КУБКА\nВыберите лигу:", 
                                     call.message.chat.id, call.message.message_id, reply_markup=kb)
            except:
                safe_send(call.message.chat.id, "🏆 ОБЛАДАТЕЛЬ КУБКА\nВыберите лигу:", kb)
    
    elif d.startswith("cupbet_"):
        parts = d.split("_")
        team_id = parts[1]
        team_name = parts[2]
        coef = float(parts[3])
        
        kb = types.InlineKeyboardMarkup(row_width=3)
        for amt in [100, 500, 1000, 2500, 5000]:
            kb.add(types.InlineKeyboardButton(str(amt), callback_data=f"cupplace_{team_id}_{team_name}_{coef}_{amt}"))
        kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_cup"))
        
        try:
            bot.edit_message_text(f"🏆 {team_name}\nКоэффициент: x{coef}\n\nВыберите сумму ставки:", 
                                 call.message.chat.id, call.message.message_id, reply_markup=kb)
        except:
            safe_send(call.message.chat.id, f"🏆 {team_name}\nКоэффициент: x{coef}\n\nВыберите сумму ставки:", kb)
    
    elif d.startswith("cupplace_"):
        parts = d.split("_")
        team_id = parts[1]
        team_name = parts[2]
        coef = float(parts[3])
        amount = int(parts[4])
        
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        bal = c.fetchone()
        
        if bal and bal[0] >= amount:
            c.execute("UPDATE users SET balance=balance-?, total_bets=total_bets+1 WHERE user_id=?", (amount, uid))
            c.execute("""INSERT INTO bets (user_id, username, match_id, team, amount, coefficient, bet_type, status, bet_time) 
                         VALUES (?,?,0,?,?,?,'cup','pending',?)""", 
                (uid, call.from_user.username or "User", team_name, amount, coef, msk_now().strftime("%d.%m.%Y %H:%M")))
            conn.commit()
            
            rewards = check_and_claim_quests(uid)
            
            msg = f"✅ СТАВКА НА КУБОК ПРИНЯТА!\n🏆 {team_name}\n💰 {amount} тенге (x{coef})"
            if rewards: msg += "\n\n🎁 ВЫПОЛНЕНЫ КВЕСТЫ:\n" + "\n".join(rewards)
            
            bot.answer_callback_query(call.id, "✅ Ставка принята!")
            try:
                bot.edit_message_text(msg, call.message.chat.id, call.message.message_id)
            except:
                safe_send(call.message.chat.id, msg)
        else: 
            bot.answer_callback_query(call.id, "❌ Недостаточно тенге!", show_alert=True)
        conn.close()
    
    elif d.startswith("cupres_"):
        if uid != ADMIN_ID: 
            bot.answer_callback_query(call.id, "Доступно только администратору")
            return
        league = d.split("_")[1]
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT team, team_id FROM cup_teams WHERE league=?", (league,))
        teams = c.fetchall()
        conn.close()
        
        if teams:
            kb = types.InlineKeyboardMarkup(row_width=1)
            for t in teams: 
                kb.add(types.InlineKeyboardButton(f"🏆 {t[0]}", callback_data=f"cupwinner_{league}_{t[0]}"))
            try:
                bot.edit_message_text(f"🏆 Кто выиграл кубок {league}?", 
                                     call.message.chat.id, call.message.message_id, reply_markup=kb)
            except:
                safe_send(call.message.chat.id, f"🏆 Кто выиграл кубок {league}?", kb)
    
    elif d.startswith("cupwinner_"):
        if uid != ADMIN_ID: 
            bot.answer_callback_query(call.id, "Доступно только администратору")
            return
        parts = d.split("_", 2)
        league = parts[1]
        winner = parts[2]
        
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("UPDATE cup_teams SET is_winner=1 WHERE league=? AND team=?", (league, winner))
        c.execute("SELECT bet_id, user_id, team, amount, coefficient FROM bets WHERE bet_type='cup' AND status='pending'")
        for b in c.fetchall():
            if b[2] == winner:
                winnings = int(b[3] * b[4])
                c.execute("UPDATE users SET balance=balance+?, wins=wins+1 WHERE user_id=?", (winnings, b[1]))
                c.execute("UPDATE bets SET status='won' WHERE bet_id=?", (b[0],))
                try:
                    bot.send_message(b[1], f"🎉 ПОБЕДА В КУБКЕ {league}!\n🏆 {winner}\n💰 Выигрыш: {winnings} тенге")
                except:
                    pass
            else: 
                c.execute("UPDATE bets SET status='lost' WHERE bet_id=?", (b[0],))
                try:
                    bot.send_message(b[1], f"💔 ПОРАЖЕНИЕ В КУБКЕ {league}\n🏆 Победитель: {winner}\n💵 Ставка проиграла")
                except:
                    pass
        
        conn.commit()
        conn.close()
        
        try:
            bot.edit_message_text(f"✅ Кубок {league} выиграл {winner}!", 
                                 call.message.chat.id, call.message.message_id)
        except:
            safe_send(call.message.chat.id, f"✅ Кубок {league} выиграл {winner}!")

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def process_result(message, mid, winner):
    score = message.text.strip()
    if not re.match(r'\d+:\d+', score): 
        safe_send(message.chat.id, "❌ Неверный формат! Пример: 5:3")
        return
    
    parts = score.split(':')
    goals1 = int(parts[0])
    goals2 = int(parts[1])
    total_goals = goals1 + goals2
    
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    
    c.execute("UPDATE matches SET status='finished', winner=?, score=? WHERE match_id=?", (winner, score, mid))
    c.execute("SELECT team1, team2, total_line FROM matches WHERE match_id=?", (mid,))
    m = c.fetchone()
    total_line = m[2]
    
    c.execute("SELECT bet_id, user_id, team, amount, bet_type, coefficient FROM bets WHERE match_id=? AND status='pending'", (mid,))
    bets = c.fetchall()
    
    for b in bets:
        bid, buid, team, amt, bt, coef = b
        
        if bt == 'over':
            if total_goals > total_line:
                winnings = int(amt * coef)
                c.execute("UPDATE users SET balance=balance+?, wins=wins+1 WHERE user_id=?", (winnings, buid))
                c.execute("UPDATE bets SET status='won' WHERE bet_id=?", (bid,))
                safe_send(buid, f"🎉 ТБ {total_line} ВЫИГРАЛ!\n⚽ Голов забито: {total_goals}\n💰 Выигрыш: {winnings} тенге")
            else:
                c.execute("UPDATE bets SET status='lost' WHERE bet_id=?", (bid,))
                safe_send(buid, f"💔 ТБ {total_line} ПРОИГРАЛ\n⚽ Голов забито: {total_goals}\n💵 Потеря: {amt} тенге")
        
        elif bt == 'under':
            if total_goals < total_line:
                winnings = int(amt * coef)
                c.execute("UPDATE users SET balance=balance+?, wins=wins+1 WHERE user_id=?", (winnings, buid))
                c.execute("UPDATE bets SET status='won' WHERE bet_id=?", (bid,))
                safe_send(buid, f"🎉 ТМ {total_line} ВЫИГРАЛ!\n⚽ Голов забито: {total_goals}\n💰 Выигрыш: {winnings} тенге")
            else:
                c.execute("UPDATE bets SET status='lost' WHERE bet_id=?", (bid,))
                safe_send(buid, f"💔 ТМ {total_line} ПРОИГРАЛ\n⚽ Голов забито: {total_goals}\n💵 Потеря: {amt} тенге")
        
        elif bt == 'exact':
            if team == score:
                winnings = int(amt * coef)
                c.execute("UPDATE users SET balance=balance+?, wins=wins+1 WHERE user_id=?", (winnings, buid))
                c.execute("UPDATE bets SET status='won' WHERE bet_id=?", (bid,))
                safe_send(buid, f"🎉 ТОЧНЫЙ СЧЁТ {score} УГАДАН!\n💰 Выигрыш: {winnings} тенге")
            else:
                c.execute("UPDATE bets SET status='lost' WHERE bet_id=?", (bid,))
                safe_send(buid, f"💔 Точный счёт не угадан\nВаш счёт: {team}\nРеальный счёт: {score}\n💵 Потеря: {amt} тенге")
        
        elif team == winner:
            winnings = int(amt * coef)
            c.execute("UPDATE users SET balance=balance+?, wins=wins+1 WHERE user_id=?", (winnings, buid))
            c.execute("UPDATE bets SET status='won' WHERE bet_id=?", (bid,))
            
            photo = get_photo('win')
            msg = f"🎉 СТАВКА ВЫИГРАЛА!\n{m[0]} vs {m[1]}\n✅ {winner} ({score})\n💰 Выигрыш: {winnings} тенге (ставка {amt} × кэф {coef})"
            if photo:
                try: bot.send_photo(buid, photo, caption=msg)
                except: safe_send(buid, msg)
            else: safe_send(buid, msg)
        
        else:
            c.execute("UPDATE bets SET status='lost' WHERE bet_id=?", (bid,))
            photo = get_photo('lose')
            msg = f"💔 СТАВКА ПРОИГРАЛА\n{m[0]} vs {m[1]}\n🏆 {winner} ({score})\n💵 Потеря: {amt} тенге"
            if photo:
                try: bot.send_photo(buid, photo, caption=msg)
                except: safe_send(buid, msg)
            else: safe_send(buid, msg)
    
    conn.commit()
    
    c.execute("SELECT DISTINCT user_id FROM bets WHERE match_id=?", (mid,))
    for uid_row in c.fetchall():
        check_and_claim_quests(uid_row[0])
    conn.close()
    
    safe_send(message.chat.id, f"✅ Результат установлен!\n{m[0]} vs {m[1]}\n🏆 Победитель: {winner}\n📊 Счёт: {score}\n⚽ Всего голов: {total_goals}")

def process_exact(message, mid):
    try:
        score = message.text.strip()
        if not re.match(r'\d+:\d+', score): 
            safe_send(message.chat.id, "❌ Неверный формат! Пример: 3:2")
            return
        
        kb = types.InlineKeyboardMarkup(row_width=3)
        for amt in [50, 100, 250, 500, 1000]:
            kb.add(types.InlineKeyboardButton(str(amt), callback_data=f"place_{mid}_{score}_{amt}"))
        kb.add(types.InlineKeyboardButton("Своя сумма", callback_data=f"custom_{mid}_{score}"))
        safe_send(message.chat.id, f"🎯 Точный счёт: {score}\nКоэффициент: x25\n\nВыберите сумму ставки:", kb)
    except: 
        safe_send(message.chat.id, "❌ Ошибка!")

def process_custom(message, mid, team):
    try:
        amt = int(message.text)
        if amt < 50:
            safe_send(message.chat.id, "❌ Минимальная сумма ставки: 50 тенге")
            return
            
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT team1, team2, coef1, coef2, coef_draw, total_line, coef_over, coef_under FROM matches WHERE match_id=?", (mid,))
        m = c.fetchone()
        
        if team == m[0]: coef = m[2]
        elif team == m[1]: coef = m[3]
        elif team == 'Ничья': coef = m[4]
        elif team == 'over': coef = m[6]
        elif team == 'under': coef = m[7]
        else: coef = 25.0
            
        bet_type = 'over' if team=='over' else 'under' if team=='under' else 'regular'
        if ':' in team: bet_type = 'exact'
            
        c.execute("SELECT balance FROM users WHERE user_id=?", (message.from_user.id,))
        bal = c.fetchone()
        
        if bal and bal[0] >= amt:
            c.execute("UPDATE users SET balance=balance-?, total_bets=total_bets+1 WHERE user_id=?", (amt, message.from_user.id))
            c.execute("""INSERT INTO bets (user_id, username, match_id, team, amount, coefficient, bet_type, status, bet_time) 
                         VALUES (?,?,?,?,?,?,?,?,?)""", 
                (message.from_user.id, message.from_user.username or "User", mid, team, amt, coef, bet_type, 'pending', 
                 msk_now().strftime("%d.%m.%Y %H:%M")))
            conn.commit()
            
            rewards = check_and_claim_quests(message.from_user.id)
            
            msg = f"✅ Ставка {amt} тенге на {team} (x{coef}) принята!"
            if rewards: msg += "\n\n🎁 ВЫПОЛНЕНЫ КВЕСТЫ:\n" + "\n".join(rewards)
                
            safe_send(message.chat.id, msg)
        else: 
            safe_send(message.chat.id, "❌ Недостаточно тенге!")
        conn.close()
    except ValueError:
        safe_send(message.chat.id, "❌ Введите число!")
    except:
        safe_send(message.chat.id, "❌ Ошибка!")

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    print("🏒 EXTRABOT ЗАПУЩЕН!")
    print("🏥 Health server: http://0.0.0.0:10000")
    print("📋 Система квестов активна")
    print("🎁 Система фрибетов активна (с автоматическим восстановлением)")
    init_db()
    
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            print("🔄 Переподключение через 15 секунд...")
            time.sleep(15)
