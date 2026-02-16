import asyncio
import logging
import sqlite3
from datetime import datetime
import json
import re
from collections import defaultdict
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ======================= КОНФИГУРАЦИЯ =======================
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"
INPUT_CHANNEL_ID = -1003469691743
OUTPUT_CHANNEL_ID = -1003842401391
ADMIN_ID = 683219603
# ============================================================

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS games_analysis
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, game_number INTEGER,
                  game_date TIMESTAMP, player_hand TEXT, banker_hand TEXT, total_points INTEGER,
                  winner TEXT, first_suit TEXT, predicted_suit TEXT, is_confirmation BOOLEAN DEFAULT 0, raw_data TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS signals
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, from_game INTEGER,
                  to_game INTEGER, suit TEXT, is_active BOOLEAN DEFAULT 1, is_confirmed BOOLEAN DEFAULT 0,
                  created_at TIMESTAMP, confirmed_at TIMESTAMP)''')
    conn.commit()
    conn.close()

class BaccaratParser:
    def __init__(self):
        self.suit_map = {'♠️': 'пики', '♣️': 'трефы', '♥️': 'черви', '♦️': 'бубны'}
    
    def parse_game(self, text):
        """Парсит ТОЛЬКО ЛЕВУЮ РУКУ ИГРОКА из завершенной игры"""
        try:
            # Номер игры
            game_match = re.search(r'#N(\d+)', text)
            if not game_match: return None
            
            game_num = int(game_match.group(1))
            
            # Общие очки
            total_match = re.search(r'#T(\d+)', text)
            total_points = int(total_match.group(1)) if total_match else 0
            
            # ИЩЕМ ПЕРВУЮ РУКУ ИГРОКА (левая часть до разделителя)
            # Паттерн: число(карты)
            hand_match = re.search(r'(\d+)\s*\(([^\)]+)\)', text)
            if not hand_match:
                return None
            
            score, player_cards_str = hand_match.groups()
            
            # Парсим карты ИГРОКА (ТОЛЬКО ЛЕВАЯ РУКА)
            player_cards = self.parse_cards(player_cards_str)
            first_suit = player_cards[0]['suit'] if player_cards else None
            
            # Определяем победителя
            winner = 'player' if '✅' in hand_match.group(0) or '🔰' in hand_match.group(0) else 'banker'
            
            return {
                'game_number': game_num,
                'player_hand': player_cards,
                'player_score': int(score),
                'first_suit': first_suit,
                'total_points': total_points,
                'winner': winner,
                'raw_data': text
            }
        except Exception as e:
            logger.error(f"Parse error: {e}")
            return None
    
    def parse_cards(self, cards_str):
        """Парсит карты из строки"""
        cards = []
        for item in cards_str.split():
            if len(item) >= 2:
                value = '10' if item.startswith('10') else item[0]
                suit_symbol = item[2:] if item.startswith('10') else item[1:]
                suit = self.suit_map.get(suit_symbol, 'unknown')
                cards.append({'value': value, 'suit': suit, 'symbol': suit_symbol})
        return cards

analyzer = BaccaratParser()

class SignalProcessor:
    @staticmethod
    def is_even_decade(game_num):
        return (game_num // 10) % 2 == 0
    
    @staticmethod
    def get_signal_suit(game_num, first_suit):
        rules_even = {'пики':'бубны','бубны':'пики','черви':'трефы','трефы':'черви'}
        rules_odd = {'пики':'трефы','трефы':'пики','черви':'бубны','бубны':'черви'}
        rules = rules_even if SignalProcessor.is_even_decade(game_num) else rules_odd
        return rules.get(first_suit, first_suit)
    
    @staticmethod
    def process_signal(user_id, game_num, first_suit):
        conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
        c = conn.cursor()
        
        signals_text = []
        predicted_suit = None
        is_confirmed = False
        
        # Проверяем активный сигнал
        c.execute('SELECT id, from_game, suit FROM signals WHERE user_id=? AND to_game=? AND is_active=1', 
                 (user_id, game_num))
        active_signal = c.fetchone()
        
        if active_signal:
            signal_id, from_game, expected_suit = active_signal
            
            if expected_suit == first_suit:
                is_confirmed = True
                c.execute('UPDATE signals SET is_confirmed=1, is_active=0, confirmed_at=? WHERE id=?', 
                         (datetime.now(), signal_id))
                
                # Повторный сигнал
                next_game = game_num + 1
                c.execute('INSERT INTO signals (user_id, from_game, to_game, suit, is_active, created_at) VALUES (?, ?, ?, ?, 1, ?)',
                         (user_id, game_num, next_game, expected_suit, datetime.now()))
                
                signals_text.extend([
                    f"✅ <b>ПОДТВЕРЖДЕНИЕ!</b> #{from_game}→#{game_num}",
                    f"🔄 Новый сигнал #{next_game}: <b>{expected_suit}</b>"
                ])
                predicted_suit = expected_suit
            else:
                c.execute('UPDATE signals SET is_active=0 WHERE id=?', (signal_id,))
                signals_text.append(f"❌ Сигнал #{from_game}→#{game_num}: {expected_suit} ≠ {first_suit}")
        
        # Новый сигнал N→N+3
        if first_suit:
            signal_suit = SignalProcessor.get_signal_suit(game_num, first_suit)
            target_game = game_num + 3
            
            c.execute('SELECT id FROM signals WHERE user_id=? AND to_game=? AND is_active=1', (user_id, target_game))
            if not c.fetchone():
                c.execute('INSERT INTO signals (user_id, from_game, to_game, suit, is_active, created_at) VALUES (?, ?, ?, ?, 1, ?)',
                         (user_id, game_num, target_game, signal_suit, datetime.now()))
                signals_text.append(f"🆕 <b>СИГНАЛ!</b> #{game_num}→#{target_game}: <b>{signal_suit}</b>")
                if not predicted_suit:
                    predicted_suit = signal_suit
        
        conn.commit()
        conn.close()
        return signals_text, predicted_suit, is_confirmed

# ==================== HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [['📊 Ввести игру', '🔔 Сигналы'], ['🔮 Прогноз', '📈 Статистика'], ['📋 История']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        '🎰 <b>МАКС МОСКВА - БАККАРА</b>\n\n'
        '🔥 Система сигналов:\n'
        '• N → N+3 (основной)\n'
        '• N+1 (повтор при ✓)\n\n'
        '<b>Проверяем ТОЛЬКО ЛЕВУЮ РУКУ ИГРОКА!</b>',
        reply_markup=reply_markup, parse_mode='HTML')

async def handle_game_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    parsed = analyzer.parse_game(text)
    if not parsed:
        await update.message.reply_text(
            '❌ Неверный формат!\n\n'
            '📝 Пример:\n'
            '#N1092. ✅7(5♦️ 9♦️ 3♥️) - 6(J♦️ 6♥️) #T13 🟩',
            parse_mode='HTML')
        return
    
    signals, predicted, confirmed = SignalProcessor.process_signal(user_id, parsed['game_number'], parsed['first_suit'])
    
    # Сохраняем в БД
    conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''INSERT INTO games_analysis 
                 (user_id, game_number, game_date, player_hand, total_points, winner, first_suit, predicted_suit, is_confirmation, raw_data)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
             (user_id, parsed['game_number'], datetime.now(), json.dumps(parsed['player_hand']),
              parsed['total_points'], parsed['winner'], parsed['first_suit'], predicted, confirmed, parsed['raw_data']))
    conn.commit()
    conn.close()
    
    # Формируем отчет
    player_str = ' '.join(f"{c['value']}{c['symbol']}" for c in parsed['player_hand'])
    winner_text = 'ИГРОК ✓' if parsed['winner'] == 'player' else 'БАНКЕР ✓'
    
    report = [
        f'🎮 <b>ИГРА #{parsed["game_number"]}</b>',
        f'👨‍💼 <b>ИГРОК:</b> {player_str}',
        f'⭐ <b>Первая масть:</b> {parsed["first_suit"]}',
        f'🏆 <b>{winner_text}</b>'
    ]
    
    if signals:
        report.extend(['', '📡 <b>СИГНАЛЫ:</b>'] + signals)
    else:
        report.append('📭 Нет сигналов')
    
    await update.message.reply_text('\n'.join(report), parse_mode='HTML')

async def show_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT from_game, to_game, suit FROM signals WHERE user_id=? AND is_active=1 ORDER BY to_game', (user_id,))
    signals = c.fetchall()
    conn.close()
    
    if not signals:
        await update.message.reply_text('🔔 Нет активных сигналов', parse_mode='HTML')
        return
    
    report = ['🔔 <b>АКТИВНЫЕ СИГНАЛЫ</b>']
    for from_game, to_game, suit in signals:
        report.append(f'💎 #{from_game} → <b>#{to_game}</b>: {suit}')
    
    await update.message.reply_text('\n'.join(report), parse_mode='HTML')

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
    c = conn.cursor()
    
    c.execute('SELECT COUNT(*), SUM(CASE WHEN winner="player" THEN 1 ELSE 0 END) FROM games_analysis WHERE user_id=?', (user_id,))
    total_games, player_wins = c.fetchone() or (0, 0)
    
    c.execute('SELECT COUNT(*), SUM(is_confirmed) FROM signals WHERE user_id=?', (user_id,))
    total_signals, confirmed = c.fetchone() or (0, 0)
    
    conn.close()
    
    player_win_rate = (player_wins / total_games * 100) if total_games else 0
    signal_accuracy = (confirmed / total_signals * 100) if total_signals else 0
    
    stats = [
        '📈 <b>СТАТИСТИКА МАКС МОСКВА</b>',
        f'🎮 Игр: <b>{total_games}</b>',
        f'👨‍💼 Побед игрока: <b>{player_wins}</b> ({player_win_rate:.1f}%)',
        '',
        f'🔔 Сигналов: <b>{total_signals}</b>',
        f'✅ Подтверждено: <b>{confirmed}</b> ({signal_accuracy:.1f}%)'
    ]
    
    await update.message.reply_text('\n'.join(stats), parse_mode='HTML')

async def handle_channel_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Автообработка из входного канала"""
    if update.channel_post and update.channel_post.chat.id == INPUT_CHANNEL_ID:
        text = update.channel_post.text or ""
        parsed = analyzer.parse_game(text)
        
        if parsed and parsed['first_suit']:
            # Обрабатываем для админа
            signals, predicted, confirmed = SignalProcessor.process_signal(ADMIN_ID, parsed['game_number'], parsed['first_suit'])
            
            # Сохраняем
            conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
            c = conn.cursor()
            c.execute('''INSERT OR IGNORE INTO games_analysis 
                        (user_id, game_number, game_date, player_hand, total_points, winner, first_suit, predicted_suit, is_confirmation, raw_data)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (ADMIN_ID, parsed['game_number'], datetime.now(), json.dumps(parsed['player_hand']),
                      parsed['total_points'], parsed['winner'], parsed['first_suit'], predicted, confirmed, text))
            conn.commit()
            conn.close()
            
            # Отправляем анализ в выходной канал
            player_str = ' '.join(f"{c['value']}{c['symbol']}" for c in parsed['player_hand'])
            winner_emoji = '👨‍💼' if parsed['winner'] == 'player' else '🏦'
            
            output_text = f"""🎮 <b>ИГРА #{parsed['game_number']}</b>

{winner_emoji} ИГРОК: {player_str}
⭐ Первая масть: <b>{parsed['first_suit']}</b>

📊 {'' if not confirmed else '✅ ПОДТВЕРЖДЕНИЕ!'}"""
            
            if signals:
                output_text += '\n\n📡 <b>СИГНАЛЫ:</b>\n' + '\n'.join(signals)
            
            await context.bot.send_message(chat_id=OUTPUT_CHANNEL_ID, text=output_text, parse_mode='HTML')

# ==================== MAIN ====================
def main():
    init_db()
    print("🤖 МАКС МОСКВА - ЛЕВАЯ РУКА ИГРОКА!")
    print(f"📱 Вход: {INPUT_CHANNEL_ID}")
    print(f"📤 Выход: {OUTPUT_CHANNEL_ID}")
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex(r'^(📊 Ввести игру|Игра)$'), handle_game_input))
    app.add_handler(MessageHandler(filters.Regex(r'^(🔔 Сигналы)$'), show_signals))
    app.add_handler(MessageHandler(filters.Regex(r'^(📈 Статистика)$'), show_stats))
    app.add_handler(MessageHandler(filters.Chat(chat_id=INPUT_CHANNEL_ID) & filters.TEXT, handle_channel_message))
    
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
