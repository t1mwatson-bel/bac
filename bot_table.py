import os
import sys
import requests
import json
import re
import time
from datetime import datetime, timedelta
import pytz

# =====================================================================
# ЧАСОВОЙ ПОЯС (МОСКВА)
# =====================================================================
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# =====================================================================
# ПРИНУДИТЕЛЬНЫЙ ВЫВОД ЛОГОВ
# =====================================================================
sys.stdout.flush()
print("=" * 60, flush=True)
print("🃏 ПРОГНОЗИСТ 2 - ПО РАНГУ (ПРАВИЛО 4 КАРТ)", flush=True)
print("=" * 60, flush=True)

# =====================================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# =====================================================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv('BOT_TOKEN_PROGNOZ')

CHANNEL_STATS = os.getenv('CHANNEL_STATS_ID')
CHANNEL_PROGNOZ = os.getenv('CHANNEL_PROGNOZ_ID')

print("🔍 ДИАГНОСТИКА ПЕРЕМЕННЫХ:", flush=True)
print(f"BOT_TOKEN: {BOT_TOKEN[:5] if BOT_TOKEN else 'НЕ ЗАДАН'}", flush=True)
print(f"CHANNEL_STATS_ID: {CHANNEL_STATS if CHANNEL_STATS else 'НЕ ЗАДАН'}", flush=True)
print(f"CHANNEL_PROGNOZ_ID: {CHANNEL_PROGNOZ if CHANNEL_PROGNOZ else 'НЕ ЗАДАН'}", flush=True)
print("=" * 60, flush=True)

if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ:
    print("❌ ОШИБКА: переменные окружения не заданы!", flush=True)
    exit(1)

print("✅ Все переменные заданы!", flush=True)

# =====================================================================
# НАСТРОЙКИ
# =====================================================================
HISTORY_FILE = "history_rank.json"
OFFSET_FILE = "offset_rank.txt"
STATS_FILE = "stats_rank.json"
MAX_HISTORY = 200
PROCESSED_GAMES = set()
LAST_PREDICT_TIME = 0
PREDICT_INTERVAL = 10
CLEANUP_INTERVAL = 3600

# =====================================================================
# ТВОИ ПРАВИЛА ДЛЯ РАНГОВ
# =====================================================================
RANK_VALUES = {
    'A': 1,   # Туз - младший
    '2': 2,
    '3': 3,
    '4': 4,
    '5': 5,
    '6': 6,
    '7': 7,
    '8': 8,
    '9': 9,
    '10': 10,
    'J': 11,  # Валет
    'Q': 12,  # Дама
    'K': 13   # Король - старший
}

# =====================================================================
# СТАТИСТИКА
# =====================================================================
def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {"total": 0, "win": 0, "lose": 0, "by_dogon": {1: 0, 2: 0, 3: 0, 4: 0}}
    return {"total": 0, "win": 0, "lose": 0, "by_dogon": {1: 0, 2: 0, 3: 0, 4: 0}}

def save_stats(stats):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

def update_stats(dogon_number, result):
    stats = load_stats()
    stats["total"] += 1
    
    if result == "win":
        stats["win"] += 1
        if dogon_number in stats["by_dogon"]:
            stats["by_dogon"][dogon_number] += 1
        else:
            stats["by_dogon"][dogon_number] = 1
    else:
        stats["lose"] += 1
    
    save_stats(stats)
    return stats

def send_stats_report():
    stats = load_stats()
    
    if stats["total"] == 0:
        msg = "📊 <b>СТАТИСТИКА ПРОГНОЗОВ (РАНГ)</b>\n\n"
        msg += "Пока нет прогнозов. Ожидаем первые результаты..."
        send_message(msg)
        return
    
    win_rate = (stats["win"] / stats["total"] * 100) if stats["total"] > 0 else 0
    
    msg = f"📊 <b>СТАТИСТИКА ПРОГНОЗОВ (РАНГ)</b>\n"
    msg += f"{'=' * 30}\n\n"
    msg += f"📈 <b>Всего прогнозов:</b> {stats['total']}\n"
    msg += f"✅ <b>Зашло:</b> {stats['win']} ({win_rate:.1f}%)\n"
    msg += f"❌ <b>Не зашло:</b> {stats['lose']} ({100 - win_rate:.1f}%)\n\n"
    msg += f"{'=' * 30}\n"
    msg += f"<b>По догонам:</b>\n"
    msg += f"🎯 Целевая игра: {stats['by_dogon'].get(1, 0)}\n"
    msg += f"🔄 Догон 1: {stats['by_dogon'].get(2, 0)}\n"
    msg += f"🔄 Догон 2: {stats['by_dogon'].get(3, 0)}\n"
    msg += f"🔄 Догон 3: {stats['by_dogon'].get(4, 0)}\n"
    msg += f"{'=' * 30}\n"
    msg += f"⏰ {datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')}"
    
    send_message(msg)

# =====================================================================
# ФУНКЦИИ
# =====================================================================
def get_updates(offset):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": offset, "timeout": 30}
    try:
        response = requests.get(url, params=params, timeout=35)
        return response.json()
    except Exception as e:
        print(f"❌ Ошибка getUpdates: {e}", flush=True)
        return {}

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHANNEL_PROGNOZ, "text": text, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return response.json()["result"]["message_id"]
        else:
            print(f"❌ Ошибка отправки: {response.status_code} - {response.text}", flush=True)
            return None
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}", flush=True)
        return None

def edit_message(message_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {"chat_id": CHANNEL_PROGNOZ, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return True
        else:
            print(f"❌ Ошибка редактирования: {response.status_code} - {response.text}", flush=True)
            return False
    except Exception as e:
        print(f"❌ Ошибка редактирования: {e}", flush=True)
        return False

def parse_game(text):
    """Парсит игру и возвращает карты игрока и дилера"""
    try:
        game_match = re.search(r'#N(\d+)', text)
        if not game_match:
            return None
        game_number = int(game_match.group(1))
        
        clean_text = text
        for symbol in ['✅', '🔰', '▶️', '◀️', '⚠️']:
            clean_text = clean_text.replace(symbol, '')
        
        parts = clean_text.split('-')
        if len(parts) < 2:
            return None
        
        player_part = parts[0].strip()
        dealer_part = parts[1].strip()
        
        player_match = re.search(r'(\d+)\(([^)]+)\)', player_part)
        if not player_match:
            return None
        player_cards_str = player_match.group(2).strip()
        
        dealer_match = re.search(r'(\d+)\(([^)]+)\)', dealer_part)
        dealer_cards_str = dealer_match.group(2).strip() if dealer_match else ""
        
        player_cards = []
        for card in re.findall(r'([AKQJ]|10|\d)([♠♣♦♥]|♠️|♣️|♦️|♥️)', player_cards_str):
            rank, suit = card
            suit = suit.replace('♠', '♠️').replace('♣', '♣️').replace('♦', '♦️').replace('♥', '♥️')
            player_cards.append({"rank": rank, "suit": suit})
        
        dealer_cards = []
        for card in re.findall(r'([AKQJ]|10|\d)([♠♣♦♥]|♠️|♣️|♦️|♥️)', dealer_cards_str):
            rank, suit = card
            suit = suit.replace('♠', '♠️').replace('♣', '♣️').replace('♦', '♦️').replace('♥', '♥️')
            dealer_cards.append({"rank": rank, "suit": suit})
        
        return {
            "number": game_number,
            "player_cards": player_cards,
            "dealer_cards": dealer_cards,
            "text": text
        }
    except Exception as e:
        print(f"❌ Ошибка парсинга: {e}", flush=True)
        return None

def has_ten(cards):
    for card in cards:
        if card.get("rank") == "10":
            return True
    return False

def is_valid_game(game_data):
    """Проверяет, подходит ли игра для прогноза (ПРАВИЛО 4 КАРТ)"""
    player_cards = game_data.get("player_cards", [])
    dealer_cards = game_data.get("dealer_cards", [])
    
    # 1. Если у игрока 1, 2 или 5+ карт → пропускаем
    if len(player_cards) in [1, 2] or len(player_cards) >= 5:
        print(f"⏭️ Пропускаем #N{game_data['number']}: у игрока {len(player_cards)} карт (нужно 3 или 4)", flush=True)
        return False
    
    # 2. Если у игрока 3 карты → нужна 1-я карта дилера
    if len(player_cards) == 3:
        if len(dealer_cards) == 0:
            print(f"⏭️ Пропускаем #N{game_data['number']}: у игрока 3 карты, но у дилера 0", flush=True)
            return False
        return True
    
    # 3. Если у игрока 4 карты → проверяем на 10
    if len(player_cards) == 4:
        if has_ten(player_cards):
            print(f"⏭️ Пропускаем #N{game_data['number']}: есть 10 у игрока", flush=True)
            return False
        return True
    
    return True

def predict(game_data):
    game_num = game_data["number"]
    player_cards = game_data["player_cards"]
    dealer_cards = game_data.get("dealer_cards", [])
    
    # Формируем список для анализа (4 карты)
    if len(player_cards) == 3:
        if dealer_cards:
            four_cards = player_cards + [dealer_cards[0]]
        else:
            print(f"⚠️ Игра #{game_num}: у дилера нет карт", flush=True)
            return None
    elif len(player_cards) == 4:
        four_cards = player_cards
    else:
        print(f"⚠️ Игра #{game_num}: {len(player_cards)} карт — не подходит", flush=True)
        return None
    
    # Проверяем повторяющиеся ранги
    ranks = [c["rank"] for c in four_cards]
    if len(ranks) != len(set(ranks)):
        print(f"⏭️ Пропускаем #N{game_num}: повторяющиеся ранги", flush=True)
        return None
    
    # Находим старшую карту (по рангу)
    highest_rank = None
    highest_position = None
    highest_value = -1
    
    for idx, card in enumerate(four_cards, start=1):
        rank = card["rank"]
        value = RANK_VALUES.get(rank, 0)
        if value > highest_value:
            highest_value = value
            highest_rank = rank
            highest_position = idx
    
    if highest_rank is None or highest_position is None:
        print(f"⚠️ Игра #{game_num}: не удалось определить старшую карту", flush=True)
        return None
    
    target_game = game_num + 1
    
    print(f"🔍 #N{game_num}: старший ранг {highest_rank} (поз. {highest_position})", flush=True)
    
    return {
        "from_game": game_num,
        "target": target_game,
        "rank": highest_rank,
        "position": highest_position,
        "games": [target_game, target_game + 1, target_game + 2, target_game + 3]
    }

def check_results(history, all_messages):
    """Проверяет результаты прогнозов — ТОЛЬКО ИГРОК"""
    for entry in history:
        if entry.get("status") != "pending":
            continue
        
        target = entry.get("target")
        predicted_rank = entry.get("rank")
        from_game = entry.get("from_game")
        message_id = entry.get("message_id")
        
        if not predicted_rank or not message_id:
            continue
        
        found = False
        found_game = None
        found_dogon = None
        
        for i in range(4):
            game_to_check = target + i
            for msg in all_messages:
                if f"#N{game_to_check}" in msg:
                    game_data = parse_game(msg)
                    if game_data:
                        for card in game_data["player_cards"]:
                            if card.get("rank") == predicted_rank:
                                found = True
                                found_game = game_to_check
                                found_dogon = i + 1
                                break
                    if found:
                        break
            if found:
                break
        
        all_games_present = True
        for i in range(4):
            game_to_check = target + i
            found_msg = False
            for msg in all_messages:
                if f"#N{game_to_check}" in msg:
                    found_msg = True
                    break
            if not found_msg:
                all_games_present = False
                break
        
        if not all_games_present:
            continue
        
        if found:
            update_stats(found_dogon, "win")
        else:
            update_stats(0, "lose")
        
        original_text = f"🔮 <b>ПРОГНОЗ (РАНГ)</b>\n"
        original_text += f"📊 От игры: #N{from_game}\n"
        original_text += f"🃏 Ранг: {predicted_rank}\n"
        original_text += f"🎯 Целевая игра: #N{target}\n"
        original_text += f"📈 3 игры догон\n"
        original_text += f"⏰ {entry.get('time', '')[:16]}"
        
        if found:
            if found_dogon == 1:
                result_text = f"\n\n✅ <b>ЗАШЛО</b> в целевой игре: #N{found_game}"
            else:
                result_text = f"\n\n✅ <b>ЗАШЛО</b> на догоне {found_dogon-1}: #N{found_game}"
        else:
            result_text = f"\n\n❌ <b>НЕ ЗАШЛО</b> (3 догона проверены до #N{target+3})"
        
        edit_message(message_id, original_text + result_text)
        entry["status"] = "win" if found else "loss"

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def clean_memory(history):
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
        print(f"🧹 Очистка кэша: оставлено {len(history)} записей", flush=True)
    return history

def get_offset():
    if os.path.exists(OFFSET_FILE):
        try:
            with open(OFFSET_FILE, "r") as f:
                return int(f.read().strip())
        except:
            return 0
    return 0

def save_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset))

def load_recent_messages():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"chat_id": CHANNEL_STATS, "limit": 100}
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            messages = []
            for update in data.get("result", []):
                post = update.get("channel_post")
                if post and post.get("text"):
                    messages.append(post.get("text"))
            return messages
    except Exception as e:
        print(f"❌ Ошибка загрузки истории: {e}", flush=True)
    return []

# =====================================================================
# ОСНОВНОЙ ЦИКЛ
# =====================================================================
def main():
    global LAST_PREDICT_TIME
    
    print("🔄 ПРОГНОЗИСТ 2 (ПО РАНГУ) ЗАПУЩЕН", flush=True)
    print(f"📊 Читает канал: {CHANNEL_STATS}", flush=True)
    print(f"📤 Отправляет в: {CHANNEL_PROGNOZ}", flush=True)
    print(f"⏰ Часовой пояс: {datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')} (МСК)", flush=True)
    print("=" * 60, flush=True)
    print("📌 ПРАВИЛА:", flush=True)
    print("   - Берём 4 карты для прогноза:", flush=True)
    print("     * Если у игрока 3 карты → 3 игрока + 1-я дилера", flush=True)
    print("     * Если у игрока 4 карты → 4 игрока", flush=True)
    print("   - Находим старший ранг (10>9>8>7>6>K>Q>J>A)", flush=True)
    print("   - Если несколько старших рангов - пропускаем", flush=True)
    print("   - Если есть 10 у игрока - пропускаем", flush=True)
    print("   - Прогноз на 4 игры (целевая + 3 догона)", flush=True)
    print("=" * 60, flush=True)
    
    offset = get_offset()
    history = load_history()
    last_reset_date = datetime.now(MOSCOW_TZ).date()
    
    print("📥 Загрузка последних сообщений из канала...", flush=True)
    all_messages = load_recent_messages()
    print(f"📥 Загружено сообщений: {len(all_messages)}", flush=True)
    
    check_results(history, all_messages)
    
    last_cleanup_time = time.time()
    last_stats_time = time.time()
    
    while True:
        try:
            current_time = time.time()
            current_date = datetime.now(MOSCOW_TZ).date()
            current_hour = datetime.now(MOSCOW_TZ).hour
            
            if current_date != last_reset_date and current_hour == 3:
                print("🔄 Ежедневный сброс кэша (новый цикл игр)...", flush=True)
                history = []
                save_history(history)
                all_messages = []
                last_reset_date = current_date
                print("✅ Кэш сброшен, начинаем новый цикл", flush=True)
                continue
            
            if current_time - last_cleanup_time > CLEANUP_INTERVAL:
                print("🧹 Запуск плановой очистки кэша...", flush=True)
                history = clean_memory(history)
                save_history(history)
                print(f"🧹 Кэш очищен. Записей в истории: {len(history)}", flush=True)
                last_cleanup_time = current_time
            
            if current_time - last_stats_time > 3600:
                print("📊 Отправка статистики...", flush=True)
                send_stats_report()
                last_stats_time = current_time
            
            updates = get_updates(offset)
            
            for update in updates.get("result", []):
                offset = update["update_id"] + 1
                save_offset(offset)
                
                channel_post = update.get("channel_post")
                edited_post = update.get("edited_channel_post")
                post = channel_post if channel_post else edited_post
                if not post:
                    continue
                
                chat_id = post.get("chat", {}).get("id")
                if str(chat_id) != str(CHANNEL_STATS):
                    continue
                
                text = post.get("text", "")
                if not text or "#N" not in text:
                    continue
                
                game_id_match = re.search(r'#N(\d+)', text)
                if not game_id_match:
                    continue
                game_number = int(game_id_match.group(1))
                
                all_messages.append(text)
                if len(all_messages) > 500:
                    all_messages = all_messages[-500:]
                
                check_results(history, all_messages)
                
                if game_number in PROCESSED_GAMES:
                    continue
                
                print(f"📥 {text[:50]}...", flush=True)
                
                game_data = parse_game(text)
                if not game_data:
                    print(f"❌ Не удалось распарсить #N{game_number}", flush=True)
                    continue
                
                if not is_valid_game(game_data):
                    continue
                
                if current_time - LAST_PREDICT_TIME < PREDICT_INTERVAL:
                    print(f"⏳ Интервал: {int(current_time - LAST_PREDICT_TIME)} сек < {PREDICT_INTERVAL} сек", flush=True)
                    continue
                
                prognoz = predict(game_data)
                if prognoz:
                    msg = f"🔮 <b>ПРОГНОЗ (РАНГ)</b>\n"
                    msg += f"📊 От игры: #N{game_data['number']}\n"
                    msg += f"🃏 Ранг: {prognoz['rank']}\n"
                    msg += f"🎯 Целевая игра: #N{prognoz['target']}\n"
                    msg += f"📈 3 игры догон\n"
                    msg += f"⏰ {datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')}"
                    
                    message_id = send_message(msg)
                    if message_id:
                        print(f"✅ Прогноз отправлен: #N{prognoz['target']} - {prognoz['rank']}", flush=True)
                        LAST_PREDICT_TIME = current_time
                        PROCESSED_GAMES.add(game_number)
                        
                        history.append({
                            "from_game": game_data["number"],
                            "target": prognoz["target"],
                            "rank": prognoz["rank"],
                            "position": prognoz["position"],
                            "time": datetime.now(MOSCOW_TZ).isoformat(),
                            "status": "pending",
                            "message_id": message_id
                        })
                        save_history(history)
            
            check_results(history, all_messages)
            history = clean_memory(history)
            save_history(history)
            
            if len(PROCESSED_GAMES) > 500:
                PROCESSED_GAMES.clear()
            
            time.sleep(5)
            
        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)
            time.sleep(30)

if __name__ == "__main__":
    main()