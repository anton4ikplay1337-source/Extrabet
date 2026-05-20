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

bot = telebot.TeleBot(TOKEN)

# ========== ВЕБ-СЕРВЕР ==========
app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({"status": "online", "bot": "EXTRABET"})

@app.route('/ping')
def ping():
    return "pong"

@app.route('/health')
def health():
    return jsonify({"status": "healthy"})

def run_web_server():
    app.run(host='0.0.0.0', port=PORT)

# ========== ХРАНИЛИЩА ==========
user_match_creation = {}
user_bet_amount = {}
blackjack_lobbies = {}  # {lobby_id: {creator, players, bet, game, ...}}

# ========== БАЗА ДАННЫХ ==========
def init_db():
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

# ========== БЛЭКДЖЕК МУЛЬТИПЛЕЕР ==========
class MultiBlackjackGame:
    def __init__(self, lobby_id, creator_id, creator_bet):
        self.lobby_id = lobby_id
        self.creator_id = creator_id
        self.players = {}  # {user_id: {'hand': [], 'bet': amount, 'status': 'playing'}}
        self.players[creator_id] = {'hand': [], 'bet': creator_bet, 'status': 'waiting'}
        self.dealer_hand = []
        self.deck = self.create_deck()
        self.game_started = False
        self.min_bet = creator_bet
        
    def create_deck(self):
        suits = ['♠', '♥', '♦', '♣']
        ranks = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
        deck = [{'rank': r, 'suit': s} for s in suits for r in ranks] * 4  # 4 колоды
        random.shuffle(deck)
        return deck
    
    def card_value(self, card):
        if card['rank'] in ['J', 'Q', 'K']:
            return 10
        elif card['rank'] == 'A':
            return 11
        else:
            return int(card['rank'])
    
    def hand_value(self, hand):
        value = sum(self.card_value(c) for c in hand)
        aces = sum(1 for c in hand if c['rank'] == 'A')
        while value > 21 and aces > 0:
            value -= 10
            aces -= 1
        return value
    
    def card_to_str(self, card):
        return f"{card['rank']}{card['suit']}"
    
    def hand_to_str(self, hand):
        return ' '.join(self.card_to_str(c) for c in hand)
    
    def add_player(self, user_id, bet):
        if user_id in self.players:
            return False, "Вы уже в лобби!"
        if self.game_started:
            return False, "Игра уже началась!"
        self.players[user_id] = {'hand': [], 'bet': bet, 'status': 'waiting'}
        return True, f"Вы присоединились! Ставка: {bet} монет"
    
    def start_game(self):
        self.game_started = True
        # Раздаём карты
        for uid in self.players:
            self.players[uid]['hand'] = [self.deck.pop(), self.deck.pop()]
            self.players[uid]['status'] = 'playing'
        self.dealer_hand = [self.deck.pop(), self.deck.pop()]
    
    def player_hit(self, user_id):
        if user_id not in self.players:
            return None, "Вы не в игре!"
        if self.players[user_id]['status'] != 'playing':
            return None, "Вы уже закончили!"
        
        self.players[user_id]['hand'].append(self.deck.pop())
        hand = self.players[user_id]['hand']
        value = self.hand_value(hand)
        
        if value > 21:
            self.players[user_id]['status'] = 'bust'
            return 'bust', hand
        elif value == 21:
            self.players[user_id]['status'] = 'stand'
            return 'blackjack', hand
        return 'ok', hand
    
    def player_stand(self, user_id):
        if user_id not in self.players:
            return None
        self.players[user_id]['status'] = 'stand'
        return self.players[user_id]['hand']
    
    def dealer_play(self):
        while self.hand_value(self.dealer_hand) < 17:
            self.dealer_hand.append(self.deck.pop())
        return self.dealer_hand
    
    def get_results(self):
        dealer_val = self.hand_value(self.dealer_hand)
        results = {}
        
        for uid, data in self.players.items():
            player_val = self.hand_value(data['hand'])
            
            if data['status'] == 'bust':
                results[uid] = {'result': 'lose', 'winnings': 0}
            elif dealer_val > 21:
                results[uid] = {'result': 'win', 'winnings': data['bet'] * 2}
            elif player_val > dealer_val:
                results[uid] = {'result': 'win', 'winnings': data['bet'] * 2}
            elif player_val == dealer_val:
                results[uid] = {'result': 'push', 'winnings': data['bet']}
            else:
                results[uid] = {'result': 'lose', 'winnings': 0}
        
        return results
    
    def all_players_done(self):
        return all(data['status'] in ('stand', 'bust') for data in self.players.values())
    
    def get_players_list(self):
        players = []
        for uid, data in self.players.items():
            status = "🟢" if data['status']=='playing' else "🔴" if data['status']=='bust' else "⏸" if data['status']=='stand' else "⏳"
            players.append(f"{status} ID:{uid} | Ставка: {data['bet']}")
        return players

# ========== АВТО-ЗАКРЫТИЕ МАТЧЕЙ ==========
def auto_close_matches():
    print("🕐 Авто-закрытие матчей запущено")
    time.sleep(30)
    while True:
        try:
            conn = sqlite3.connect('hockey_bets.db')
            c = conn.cursor()
            now = datetime.now().strftime("%d.%m.%Y %H:%M")
            c.execute("UPDATE matches SET status='closed' WHERE status='upcoming' AND match_date <= ?", (now,))
            if c.rowcount > 0:
                print(f"🔒 Закрыто: {c.rowcount}")
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"⚠️ {e}")
        time.sleep(60)

# ========== ГЕНЕРАЦИЯ КОДА ==========
def generate_promo_code(length=8):
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

# ========== БЕЗОПАСНЫЕ ФУНКЦИИ ==========
def safe_send_message(chat_id, text, reply_markup=None):
    try:
        return bot.send_message(chat_id, text, reply_markup=reply_markup)
    except:
        return None

def safe_edit_message(text, chat_id, message_id, reply_markup=None):
    try:
        return bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup)
    except:
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
        caption = f"🎉 СТАВКА СЫГРАЛА!\n\n📊 {match_info}\n✅ {team}\n💰 Выигрыш: {winnings} монет"
    else:
        caption = f"💔 СТАВКА ПРОИГРАЛА\n\n📊 {match_info}\n❌ {team}\n💵 Ставка: {amount}"
    
    photo = get_photo('win') if status=="won" else get_photo('lose')
    if photo:
        try:
            bot.send_photo(user_id, photo, caption=caption)
            return
        except:
            pass
    safe_send_message(user_id, caption)

# ========== КЛАВИАТУРЫ ==========
def admin_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("🎮 Управление матчами", "🎫 Промокоды")
    kb.add("💰 Выдать фрибет", "📊 Статистика бота")
    kb.add("👥 Пользователи", "🏒 Матчи")
    kb.add("👤 Профиль", "♠ Блэкджек")
    kb.add("📋 Меню", "📸 Установить фото")
    return kb

def main_keyboard(user_id=None):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("🏒 Матчи", "👤 Профиль")
    kb.add("💰 Баланс", "📊 Статистика")
    kb.add("🎫 Активировать промокод", "🎁 Мои фрибеты")
    kb.add("🆘 Получить бонус", "♠ Блэкджек")  # ← Вот эта строка!
    if user_id == ADMIN_ID:
        kb.add("🔧 Админ-панель")
    return kb

def blackjack_menu_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("🃏 Играть с ботом", callback_data="bj_solo"),
        types.InlineKeyboardButton("👥 Создать лобби", callback_data="bj_create_lobby")
    )
    kb.add(types.InlineKeyboardButton("🔍 Найти лобби", callback_data="bj_find_lobby"))
    return kb

def blackjack_game_keyboard(lobby_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🃏 Взять карту", callback_data=f"bj_hit_{lobby_id}"),
        types.InlineKeyboardButton("✋ Хватит", callback_data=f"bj_stand_{lobby_id}")
    )
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
    text = "🏒 ДОБРО ПОЖАЛОВАТЬ В EXTRABET!\n\n💰 Баланс: 1000 монет\n🎮 Блэкджек с друзьями!\n\n📋 Меню снизу"
    kb = admin_keyboard() if user_id == ADMIN_ID else main_keyboard(user_id)
    safe_send_message(message.chat.id, text, kb)

@bot.message_handler(func=lambda m: m.text == "♠ Блэкджек")
def blackjack_menu(message):
    safe_send_message(message.chat.id, "🃏 БЛЭКДЖЕК\n\nВыберите режим игры:", blackjack_menu_keyboard())

# ========== CALLBACK ОБРАБОТЧИКИ ==========
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    data = call.data
    
    # БЛЭКДЖЕК МЕНЮ
    if data == "bj_solo":
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        balance = c.fetchone()[0]
        conn.close()
        
        kb = types.InlineKeyboardMarkup(row_width=3)
        kb.add(
            types.InlineKeyboardButton("50", callback_data="bjsolo_50"),
            types.InlineKeyboardButton("100", callback_data="bjsolo_100"),
            types.InlineKeyboardButton("250", callback_data="bjsolo_250")
        )
        kb.add(
            types.InlineKeyboardButton("500", callback_data="bjsolo_500"),
            types.InlineKeyboardButton("1000", callback_data="bjsolo_1000"),
            types.InlineKeyboardButton("Своя", callback_data="bjsolo_custom")
        )
        safe_edit_message(f"🃏 БОТ\n💰 Баланс: {balance}\n\nСтавка:", call.message.chat.id, call.message.message_id, kb)
    
    elif data == "bj_create_lobby":
        msg = safe_send_message(call.message.chat.id, "💰 Введите минимальную ставку для лобби:")
        bot.register_next_step_handler(msg, process_create_lobby)
        bot.answer_callback_query(call.id)
    
    elif data == "bj_find_lobby":
        show_lobbies(call)
    
    # СОЛО ИГРА
    elif data.startswith("bjsolo_"):
        bet_str = data.split("_")[1]
        if bet_str == "custom":
            msg = safe_send_message(call.message.chat.id, "💵 Введите ставку:")
            bot.register_next_step_handler(msg, process_solo_custom_bet)
            return
        start_solo_game(call, user_id, int(bet_str))
    
    elif data.startswith("bjh_"):
        parts = data.split("_")
        game_id = parts[1]
        action = parts[2] if len(parts) > 2 else None
        handle_solo_action(call, user_id, game_id, action)
    
    # ЛОББИ
    elif data.startswith("lobby_join_"):
        lobby_id = data.split("_")[2]
        join_lobby(call, user_id, lobby_id)
    
    elif data.startswith("lobby_start_"):
        lobby_id = data.split("_")[2]
        start_lobby_game(call, lobby_id)
    
    elif data.startswith("lobby_hit_"):
        lobby_id = data.split("_")[2]
        lobby_hit(call, user_id, lobby_id)
    
    elif data.startswith("lobby_stand_"):
        lobby_id = data.split("_")[2]
        lobby_stand(call, user_id, lobby_id)
    
    # ОСТАЛЬНЫЕ CALLBACK'И (сохранены из предыдущего кода)
    elif data == "set_photo_win":
        if user_id != ADMIN_ID: return
        user_match_creation[user_id] = {'photo_type': 'win'}
        bot.answer_callback_query(call.id, "Отправьте фото ПОБЕДЫ")
        safe_send_message(call.message.chat.id, "📸 Отправьте фото для 🏆 ПОБЕДЫ")
    
    elif data == "set_photo_lose":
        if user_id != ADMIN_ID: return
        user_match_creation[user_id] = {'photo_type': 'lose'}
        bot.answer_callback_query(call.id, "Отправьте фото ПОРАЖЕНИЯ")
        safe_send_message(call.message.chat.id, "📸 Отправьте фото для 💔 ПОРАЖЕНИЯ")
    
    elif data == "show_photos":
        if user_id != ADMIN_ID: return
        win = get_photo('win')
        lose = get_photo('lose')
        if win:
            try: bot.send_photo(call.message.chat.id, win, caption="🏆 ПОБЕДА")
            except: pass
        if lose:
            try: bot.send_photo(call.message.chat.id, lose, caption="💔 ПОРАЖЕНИЕ")
            except: pass
    
    elif data == "back_main":
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        kb = admin_keyboard() if user_id==ADMIN_ID else main_keyboard(user_id)
        safe_send_message(call.message.chat.id, "📋 Меню:", kb)
    
    elif data == "show_matches":
        safe_edit_message("🎯 Матчи:", call.message.chat.id, call.message.message_id, matches_keyboard())
    
    elif data == "refresh_matches":
        safe_edit_message("🔄 Обновлено!", call.message.chat.id, call.message.message_id, matches_keyboard())
    
    elif data.startswith("match_"):
        match_id = int(data.split("_")[1])
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT team1, team2, match_date, coefficient1, coefficient2, coefficient_draw FROM matches WHERE match_id=?", (match_id,))
        m = c.fetchone()
        conn.close()
        safe_edit_message(f"⚔ {m[0]} vs {m[1]}\n📅 {m[2]}\nКФ: {m[3]}/{m[4]}/{m[5]}", call.message.chat.id, call.message.message_id, bet_keyboard(match_id))
    
    elif data.startswith("betsum_"):
        parts = data.split("_", 2)
        safe_edit_message(f"💰 Сумма на {parts[2]}:", call.message.chat.id, call.message.message_id, sum_keyboard(int(parts[1]), parts[2]))
    
    elif data.startswith("bet_"):
        parts = data.split("_")
        match_id = int(parts[1])
        team = "_".join(parts[2:-1])
        amount = int(parts[-1])
        place_bet(call, user_id, match_id, team, amount)

# ========== ФУНКЦИИ БЛЭКДЖЕКА ==========
# Соло игра
solo_games = {}

def start_solo_game(call, user_id, bet):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    if c.fetchone()[0] < bet:
        bot.answer_callback_query(call.id, "❌ Недостаточно средств!", show_alert=True)
        conn.close()
        return
    
    c.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (bet, user_id))
    c.execute("INSERT INTO bets (user_id, match_id, team, amount, bet_type) VALUES (?, 0, 'blackjack', ?, 'blackjack')", (user_id, bet))
    game_id = str(c.lastrowid)
    conn.commit()
    conn.close()
    
    # Создаём игру
    deck = [{'rank': r, 'suit': s} for s in ['♠','♥','♦','♣'] for r in ['A','2','3','4','5','6','7','8','9','10','J','Q','K']] * 4
    random.shuffle(deck)
    
    game = {
        'bet': bet,
        'deck': deck,
        'player_hand': [deck.pop(), deck.pop()],
        'dealer_hand': [deck.pop(), deck.pop()]
    }
    solo_games[game_id] = game
    
    p_hand = ' '.join(f"{c['rank']}{c['suit']}" for c in game['player_hand'])
    p_val = sum(c['rank'] in 'JQK' and 10 or c['rank']=='A' and 11 or int(c['rank']) for c in game['player_hand'])
    d_show = f"{game['dealer_hand'][0]['rank']}{game['dealer_hand'][0]['suit']}"
    
    text = f"🃏 БЛЭКДЖЕК (соло)\n\nСтавка: {bet}💰\n\nВаша рука: {p_hand} ({p_val})\nДилер: {d_show} ?"
    
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🃏 Взять", callback_data=f"bjh_{game_id}_hit"),
        types.InlineKeyboardButton("✋ Хватит", callback_data=f"bjh_{game_id}_stand")
    )
    
    bot.answer_callback_query(call.id, "Игра началась!")
    safe_edit_message(text, call.message.chat.id, call.message.message_id, kb)

def handle_solo_action(call, user_id, game_id, action):
    if game_id not in solo_games:
        bot.answer_callback_query(call.id, "❌ Игра не найдена!")
        return
    
    game = solo_games[game_id]
    
    if action == "hit":
        game['player_hand'].append(game['deck'].pop())
        p_val = sum(c['rank'] in 'JQK' and 10 or c['rank']=='A' and 11 or int(c['rank']) for c in game['player_hand'])
        aces = sum(1 for c in game['player_hand'] if c['rank']=='A')
        while p_val > 21 and aces > 0:
            p_val -= 10
            aces -= 1
        
        p_hand = ' '.join(f"{c['rank']}{c['suit']}" for c in game['player_hand'])
        
        if p_val > 21:
            conn = sqlite3.connect('hockey_bets.db')
            c = conn.cursor()
            c.execute("UPDATE bets SET status='lost' WHERE bet_id=?", (int(game_id),))
            conn.commit()
            conn.close()
            
            safe_edit_message(f"💥 ПЕРЕБОР!\n\nРука: {p_hand} ({p_val})\n\n❌ Проигрыш: {game['bet']}💰", call.message.chat.id, call.message.message_id)
            del solo_games[game_id]
        else:
            d_show = f"{game['dealer_hand'][0]['rank']}{game['dealer_hand'][0]['suit']}"
            text = f"🃏 БЛЭКДЖЕК\n\nСтавка: {game['bet']}💰\n\nВаша рука: {p_hand} ({p_val})\nДилер: {d_show} ?"
            
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("🃏 Взять", callback_data=f"bjh_{game_id}_hit"),
                types.InlineKeyboardButton("✋ Хватит", callback_data=f"bjh_{game_id}_stand")
            )
            safe_edit_message(text, call.message.chat.id, call.message.message_id, kb)
    
    elif action == "stand":
        # Дилер добирает
        while sum(c['rank'] in 'JQK' and 10 or c['rank']=='A' and 11 or int(c['rank']) for c in game['dealer_hand']) < 17:
            game['dealer_hand'].append(game['deck'].pop())
        
        p_val = sum(c['rank'] in 'JQK' and 10 or c['rank']=='A' and 11 or int(c['rank']) for c in game['player_hand'])
        d_val = sum(c['rank'] in 'JQK' and 10 or c['rank']=='A' and 11 or int(c['rank']) for c in game['dealer_hand'])
        
        # Корректировка тузов
        p_aces = sum(1 for c in game['player_hand'] if c['rank']=='A')
        while p_val > 21 and p_aces > 0:
            p_val -= 10
            p_aces -= 1
        
        d_aces = sum(1 for c in game['dealer_hand'] if c['rank']=='A')
        while d_val > 21 and d_aces > 0:
            d_val -= 10
            d_aces -= 1
        
        p_hand = ' '.join(f"{c['rank']}{c['suit']}" for c in game['player_hand'])
        d_hand = ' '.join(f"{c['rank']}{c['suit']}" for c in game['dealer_hand'])
        
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        
        if d_val > 21 or p_val > d_val:
            winnings = game['bet'] * 2
            c.execute("UPDATE users SET balance = balance + ?, wins = wins + 1 WHERE user_id=?", (winnings, user_id))
            c.execute("UPDATE bets SET status='won' WHERE bet_id=?", (int(game_id),))
            text = f"🎉 ВЫИГРЫШ!\n\nВаша рука: {p_hand} ({p_val})\nДилер: {d_hand} ({d_val})\n\n💰 +{winnings}"
        elif p_val == d_val:
            c.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (game['bet'], user_id))
            text = f"🤝 НИЧЬЯ!\n\nВаша рука: {p_hand} ({p_val})\nДилер: {d_hand} ({d_val})\n\n💵 Ставка возвращена"
        else:
            c.execute("UPDATE bets SET status='lost' WHERE bet_id=?", (int(game_id),))
            text = f"😞 ПРОИГРЫШ!\n\nВаша рука: {p_hand} ({p_val})\nДилер: {d_hand} ({d_val})\n\n❌ -{game['bet']}"
        
        conn.commit()
        conn.close()
        
        safe_edit_message(text, call.message.chat.id, call.message.message_id)
        del solo_games[game_id]

# Лобби
def process_create_lobby(message):
    user_id = message.from_user.id
    try:
        bet = int(message.text)
        if bet < 50:
            safe_send_message(message.chat.id, "❌ Минимальная ставка: 50")
            return
        
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        if c.fetchone()[0] < bet:
            safe_send_message(message.chat.id, "❌ Недостаточно средств!")
            conn.close()
            return
        
        lobby_id = str(int(time.time()))[-6:]
        
        blackjack_lobbies[lobby_id] = {
            'game': None,
            'creator': user_id,
            'players': {user_id: bet},
            'min_bet': bet,
            'started': False
        }
        
        conn.close()
        
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("🚀 Начать игру", callback_data=f"lobby_start_{lobby_id}"))
        
        safe_send_message(
            message.chat.id,
            f"🎯 ЛОББИ СОЗДАНО!\n\n🆔 Код: {lobby_id}\n💰 Ставка: {bet}\n👥 Игроков: 1\n\nОтправьте этот код друзьям:\n/lobby_{lobby_id}",
            kb
        )
    except:
        safe_send_message(message.chat.id, "❌ Введите сумму!")

def show_lobbies(call):
    if not blackjack_lobbies:
        bot.answer_callback_query(call.id, "Нет активных лобби")
        return
    
    text = "🎯 ДОСТУПНЫЕ ЛОББИ:\n\n"
    for lid, lobby in blackjack_lobbies.items():
        if not lobby['started']:
            text += f"🆔 {lid} | 💰 {lobby['min_bet']} | 👥 {len(lobby['players'])}\n"
    
    safe_edit_message(text, call.message.chat.id, call.message.message_id)

def join_lobby(call, user_id, lobby_id):
    if lobby_id not in blackjack_lobbies:
        bot.answer_callback_query(call.id, "❌ Лобби не найдено!")
        return
    
    lobby = blackjack_lobbies[lobby_id]
    
    if lobby['started']:
        bot.answer_callback_query(call.id, "❌ Игра уже началась!")
        return
    
    if user_id in lobby['players']:
        bot.answer_callback_query(call.id, "❌ Вы уже в лобби!")
        return
    
    bet = lobby['min_bet']
    
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    if c.fetchone()[0] < bet:
        bot.answer_callback_query(call.id, "❌ Недостаточно средств!", show_alert=True)
        conn.close()
        return
    
    c.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (bet, user_id))
    conn.commit()
    conn.close()
    
    lobby['players'][user_id] = bet
    
    bot.answer_callback_query(call.id, "✅ Вы в игре!")
    
    # Уведомляем создателя
    try:
        safe_send_message(lobby['creator'], f"👤 Игрок {user_id} присоединился!\n👥 Игроков: {len(lobby['players'])}")
    except:
        pass

def start_lobby_game(call, lobby_id):
    if lobby_id not in blackjack_lobbies:
        bot.answer_callback_query(call.id, "❌ Лобби не найдено!")
        return
    
    lobby = blackjack_lobbies[lobby_id]
    
    if call.from_user.id != lobby['creator']:
        bot.answer_callback_query(call.id, "❌ Только создатель может начать!")
        return
    
    if len(lobby['players']) < 1:
        bot.answer_callback_query(call.id, "❌ Нет игроков!")
        return
    
    # Создаём игру
    game = MultiBlackjackGame(lobby_id, lobby['creator'], lobby['min_bet'])
    
    # Добавляем остальных игроков
    for uid, bet in lobby['players'].items():
        if uid != lobby['creator']:
            game.players[uid] = {'hand': [], 'bet': bet, 'status': 'waiting'}
    
    game.start_game()
    lobby['game'] = game
    lobby['started'] = True
    
    # Уведомляем всех игроков
    for uid in game.players:
        hand = game.hand_to_str(game.players[uid]['hand'])
        val = game.hand_value(game.players[uid]['hand'])
        d_show = game.card_to_str(game.dealer_hand[0])
        
        text = f"🃏 БЛЭКДЖЕК (лобби {lobby_id})\n\nВаша рука: {hand} ({val})\nДилер: {d_show} ?\n\nСтавка: {game.players[uid]['bet']}💰"
        
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🃏 Взять", callback_data=f"lobby_hit_{lobby_id}"),
            types.InlineKeyboardButton("✋ Хватит", callback_data=f"lobby_stand_{lobby_id}")
        )
        
        safe_send_message(uid, text, kb)
    
    bot.answer_callback_query(call.id, "🎮 Игра началась!")

def lobby_hit(call, user_id, lobby_id):
    if lobby_id not in blackjack_lobbies or not blackjack_lobbies[lobby_id]['started']:
        bot.answer_callback_query(call.id, "❌ Игра не найдена!")
        return
    
    game = blackjack_lobbies[lobby_id]['game']
    result, hand = game.player_hit(user_id)
    
    if result is None:
        bot.answer_callback_query(call.id, "❌ Вы не в игре!")
        return
    
    if result == 'bust':
        safe_edit_message(f"💥 ПЕРЕБОР!\n\n{game.hand_to_str(hand)} ({game.hand_value(hand)})\n\n❌ Вы проиграли!", call.message.chat.id, call.message.message_id)
    elif result == 'blackjack':
        safe_edit_message(f"🎯 BLACKJACK!\n\n{game.hand_to_str(hand)} (21)\n\nЖдите других игроков...", call.message.chat.id, call.message.message_id)
    else:
        d_show = game.card_to_str(game.dealer_hand[0])
        text = f"🃏 БЛЭКДЖЕК\n\nВаша рука: {game.hand_to_str(hand)} ({game.hand_value(hand)})\nДилер: {d_show} ?"
        safe_edit_message(text, call.message.chat.id, call.message.message_id, blackjack_game_keyboard(lobby_id))

def lobby_stand(call, user_id, lobby_id):
    if lobby_id not in blackjack_lobbies or not blackjack_lobbies[lobby_id]['started']:
        bot.answer_callback_query(call.id, "❌ Игра не найдена!")
        return
    
    game = blackjack_lobbies[lobby_id]['game']
    hand = game.player_stand(user_id)
    
    if hand is None:
        bot.answer_callback_query(call.id, "❌ Вы не в игре!")
        return
    
    safe_edit_message(f"✋ ВЫ ПАСУЕТЕ\n\n{game.hand_to_str(hand)} ({game.hand_value(hand)})\n\nЖдите других игроков...", call.message.chat.id, call.message.message_id)
    
    # Проверяем, все ли закончили
    if game.all_players_done():
        # Дилер играет
        dealer_hand = game.dealer_play()
        dealer_val = game.hand_value(dealer_hand)
        dealer_str = game.hand_to_str(dealer_hand)
        
        # Результаты
        results = game.get_results()
        
        conn = sqlite3.connect('hockey_bets.db')
        c = conn.cursor()
        
        # Отправляем результаты каждому игроку
        for uid, result in results.items():
            hand = game.players[uid]['hand']
            player_str = game.hand_to_str(hand)
            player_val = game.hand_value(hand)
            
            if result['result'] == 'win':
                c.execute("UPDATE users SET balance = balance + ?, wins = wins + 1 WHERE user_id=?", (result['winnings'], uid))
                text = f"🎉 ВЫИГРЫШ!\n\nРука: {player_str} ({player_val})\nДилер: {dealer_str} ({dealer_val})\n\n💰 +{result['winnings']}"
            elif result['result'] == 'push':
                c.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (result['winnings'], uid))
                text = f"🤝 НИЧЬЯ!\n\nРука: {player_str} ({player_val})\nДилер: {dealer_str} ({dealer_val})\n\n💵 Ставка возвращена"
            else:
                text = f"😞 ПРОИГРЫШ!\n\nРука: {player_str} ({player_val})\nДилер: {dealer_str} ({dealer_val})\n\n❌ Вы проиграли"
            
            safe_send_message(uid, text)
        
        conn.commit()
        conn.close()
        
        del blackjack_lobbies[lobby_id]

# ========== КОМАНДА ДЛЯ ВХОДА В ЛОББИ ==========
@bot.message_handler(commands=['lobby'])
def join_lobby_command(message):
    try:
        lobby_id = message.text.split('_')[1]
        join_lobby_direct(message.from_user.id, lobby_id, message.chat.id)
    except:
        safe_send_message(message.chat.id, "❌ Используйте: /lobby_КОД")

def join_lobby_direct(user_id, lobby_id, chat_id):
    if lobby_id not in blackjack_lobbies:
        safe_send_message(chat_id, "❌ Лобби не найдено!")
        return
    
    lobby = blackjack_lobbies[lobby_id]
    
    if lobby['started']:
        safe_send_message(chat_id, "❌ Игра уже началась!")
        return
    
    bet = lobby['min_bet']
    
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    if c.fetchone()[0] < bet:
        safe_send_message(chat_id, "❌ Недостаточно средств!")
        conn.close()
        return
    
    c.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (bet, user_id))
    conn.commit()
    conn.close()
    
    lobby['players'][user_id] = bet
    
    safe_send_message(chat_id, f"✅ Вы в лобби {lobby_id}!\n💰 Ставка: {bet}\n👥 Игроков: {len(lobby['players'])}")
    try:
        safe_send_message(lobby['creator'], f"👤 Игрок присоединился!\n👥 Всего: {len(lobby['players'])}")
    except:
        pass

# ========== ОСТАЛЬНЫЕ ФУНКЦИИ (сохранены) ==========
def matches_keyboard():
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    c.execute("SELECT match_id, team1, team2, match_date, coefficient1, coefficient2, coefficient_draw FROM matches WHERE status='upcoming' AND match_date > ?", (now,))
    matches = c.fetchall()
    conn.close()
    kb = types.InlineKeyboardMarkup(row_width=1)
    for m in matches:
        kb.add(types.InlineKeyboardButton(f"⚔ {m[1]} vs {m[2]} | {m[3]}", callback_data=f"match_{m[0]}"))
    kb.add(types.InlineKeyboardButton("🔄 Обновить", callback_data="refresh_matches"))
    kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
    return kb

def bet_keyboard(match_id):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT team1, team2, coefficient1, coefficient2, coefficient_draw FROM matches WHERE match_id=?", (match_id,))
    m = c.fetchone()
    conn.close()
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton(f"✅ {m[0]} x{m[2]}", callback_data=f"betsum_{match_id}_{m[0]}"),
        types.InlineKeyboardButton(f"✅ {m[1]} x{m[3]}", callback_data=f"betsum_{match_id}_{m[1]}")
    )
    kb.add(types.InlineKeyboardButton(f"🤝 Ничья x{m[4]}", callback_data=f"betsum_{match_id}_Ничья"))
    return kb

def sum_keyboard(match_id, team):
    kb = types.InlineKeyboardMarkup(row_width=3)
    for amt in [100, 500, 1000, 2500, 5000]:
        kb.add(types.InlineKeyboardButton(str(amt), callback_data=f"bet_{match_id}_{team}_{amt}"))
    kb.add(types.InlineKeyboardButton("Своя", callback_data=f"custom_{match_id}_{team}"))
    return kb

def place_bet(call, user_id, match_id, team, amount):
    conn = sqlite3.connect('hockey_bets.db')
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    bal = c.fetchone()
    c.execute("SELECT team1, coefficient1, coefficient2, coefficient_draw FROM matches WHERE match_id=?", (match_id,))
    m = c.fetchone()
    coef = m[1] if team==m[0] else m[2] if team==m[2] else m[3]
    if bal and bal[0] >= amount:
        c.execute("UPDATE users SET balance=balance-?, total_bets=total_bets+1 WHERE user_id=?", (amount, user_id))
        c.execute("INSERT INTO bets (user_id, match_id, team, amount, coefficient) VALUES (?,?,?,?,?)", (user_id, match_id, team, amount, coef))
        conn.commit()
        bot.answer_callback_query(call.id, "✅ Принято!")
        safe_edit_message(f"✅ Ставка!\n🎯 {team}\n💰 {amount} (x{coef})", call.message.chat.id, call.message.message_id)
    else:
        bot.answer_callback_query(call.id, "❌ Мало средств!", show_alert=True)
    conn.close()

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    print("STARTING EXTRABET...")
    init_db()
    threading.Thread(target=run_web_server, daemon=True).start()
    threading.Thread(target=auto_close_matches, daemon=True).start()
    print("Bot starting...")
    bot.remove_webhook()
    time.sleep(0.5)
    bot.infinity_polling()
