import os
import sys
import requests
import json
import re
import time
from datetime import datetime, timedelta

# =====================================================================
# ПРИНУДИТЕЛЬНЫЙ ВЫВОД ЛОГОВ
# =====================================================================
sys.stdout.flush()
print("=" * 60, flush=True)
print("🃏 ПРОГНОЗИСТ 2.0 - ПО ТВОИМ ПРАВИЛАМ", flush=True)
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
def get_offset():
    return 0  # Всегда начинаем с 0

def save_offset(offset):
    pass  # Ничего не сохраняем
MAX_HISTORY = 200
PROCESSED_GAMES = set()
LAST_PREDICT_TIME = 0
PREDICT_INTERVAL = 120  # 2 минуты

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
# ФУНКЦИИ
# =====================================================================
def get_updates(offset):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": offset, "timeout": 30}
    try:
        print(f"📥 Запрос getUpdates с offset={offset}...", flush=True)
        response = requests.get(url, params=params, timeout=35)
        print(f"📥 Статус: {response.status_code}", flush=True)
        if response.status_code == 200:
            data = response.json()
            print(f"📥 Получено обновлений: {len(data.get('result', []))}", flush=True)
            return data
        else:
            print(f"❌ Ошибка getUpdates: {response.status_code}", flush=True)
            return {}
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

def is_final_game(text):
    """Проверяет, завершена ли игра (есть ✅ или 🔰)"""
    return "✅" in text or "🔰" in text

def parse_game(text):
    """Парсит игру и возвращает карты игрока и дилера"""
    try:
        game_match = re.search(r'#N(\d+)', text)
        if not game_match:
            return None
        game_number = int(game_match.group(1))
        
        clean_text = text.replace('✅', '').replace('🔰', '').replace('▶️', '').replace('◀️', '').replace('⚠️', '')
        
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
        if not dealer_match:
            return None
        dealer_cards_str = dealer_match.group(2).strip()
        
        player_cards = []
        for card in re.findall(r'([AKQJ]|10|\d)([♠♣♦♥])', player_cards_str):
            rank, suit = card
            player_cards.append({"rank": rank, "suit": suit})
        
        dealer_cards = []
        for card in re.findall(r'([AKQJ]|10|\d)([♠♣♦♥])', dealer_cards_str):
            rank, suit = card
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

def get_highest_card_info(cards):
    """
    Находит самую старшую карту и её позицию по ТВОИМ правилам.
    Возвращает: (ранг, позиция)
    Если есть одинаковые ранги - возвращает None
    """
    if not cards:
        return None, None
    
    # Проверяем на повторяющиеся ранги
    ranks = [c["rank"] for c in cards]
    if len(ranks) != len(set(ranks)):
        return None, None  # Пропуск, есть одинаковые ранги
    
    # Находим карту с максимальным значением ранга
    max_rank_value = -1
    max_rank = None
    max_position = None
    
    for idx, card in enumerate(cards, start=1):
        rank = card["rank"]
        rank_value = RANK_VALUES.get(rank, 0)
        if rank_value > max_rank_value:
            max_rank_value = rank_value
            max_rank = rank
            max_position = idx
    
    return max_rank, max_position

def predict(game_data):
    """
    ТВОЙ АЛГОРИТМ:
    1. Берём карты игрока
    2. Находим самую старшую по ТВОИМ правилам
    3. Смотрим её позицию
    4. Прогноз = ЭТА КАРТА (ранг без масти)
    5. Проверяем на N+1, N+2, N+3
    """
    player_cards = game_data["player_cards"]
    game_num = game_data["number"]
    
    # Проверяем, что у игрока есть карты
    if not player_cards:
        print(f"⏭️ Нет карт игрока", flush=True)
        return None
    
    # Находим самую старшую карту
    highest_rank, highest_position = get_highest_card_info(player_cards)
    
    if highest_rank is None:
        print(f"⏭️ Есть повторяющиеся ранги - пропуск", flush=True)
        return None
    
    if highest_position is None:
        print(f"⏭️ Не удалось определить старшую карту", flush=True)
        return None
    
    print(f"🔍 #N{game_num}: самая старшая {highest_rank} на позиции {highest_position}", flush=True)
    
    return {
        "from_game": game_num,
        "rank": highest_rank,
        "position": highest_position,
        "target": game_num + 1,
        "games": [game_num + 1, game_num + 2, game_num + 3]
    }

def check_results(history, all_messages):
    """Проверяет результаты прогнозов (игрок + дилер)"""
    for entry in history:
        if entry.get("status") != "pending":
            continue
        
        target = entry.get("target")
        rank = entry.get("rank")
        from_game = entry.get("from_game")
        message_id = entry.get("message_id")
        
        if not rank or not message_id:
            continue
        
        found = False
        found_game = None
        found_dogon = None
        
        # Проверяем N+1, N+2, N+3
        for i in range(3):
            game_to_check = target + i
            for msg in all_messages:
                if f"#N{game_to_check}" in msg:
                    game_data = parse_game(msg)
                    if game_data:
                        # Проверяем игрока
                        for card in game_data["player_cards"]:
                            if card["rank"] == rank:
                                found = True
                                found_game = game_to_check
                                found_dogon = i + 1
                                break
                        # Проверяем дилера
                        if not found:
                            for card in game_data["dealer_cards"]:
                                if card["rank"] == rank:
                                    found = True
                                    found_game = game_to_check
                                    found_dogon = i + 1
                                    break
                    if found:
                        break
            if found:
                break
        
        # Проверяем, что все 3 игры уже есть в истории
        all_games_present = True
        for i in range(3):
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
        
        original_text = f"🔮 <b>ПРОГНОЗ</b>\n"
        original_text += f"📊 От игры: #N{from_game}\n"
        original_text += f"🃏 Ранг: {rank}\n"
        original_text += f"🎯 Целевая игра: #N{target}\n"
        original_text += f"📈 3 игры догон (N+1, N+2, N+3)\n"
        original_text += f"⏰ {entry.get('time', '')[:16]}"
        
        if found:
            result_text = f"\n\n✅ <b>ЗАШЛО</b> на догоне {found_dogon}: #N{found_game}"
        else:
            result_text = f"\n\n❌ <b>НЕ ЗАШЛО</b> (3 догона проверены до #N{target+2})"
        
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
    now = datetime.now()
    new_history = []
    for item in history:
        if "time" in item:
            try:
                item_time = datetime.fromisoformat(item["time"])
                if (now - item_time).days < 7:
                    new_history.append(item)
            except:
                new_history.append(item)
        else:
            new_history.append(item)
    return new_history

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
    """Загружает последние 50 сообщений из канала при запуске"""
    print(f"📥 Загрузка последних 50 сообщений из канала {CHANNEL_STATS}...", flush=True)
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"chat_id": CHANNEL_STATS, "limit": 50}
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            messages = []
            for update in data.get("result", []):
                post = update.get("channel_post")
                if post and post.get("text"):
                    messages.append(post.get("text"))
            print(f"📥 Загружено сообщений: {len(messages)}", flush=True)
            return messages
    except Exception as e:
        print(f"❌ Ошибка загрузки истории: {e}", flush=True)
    return []

# =====================================================================
# ОСНОВНОЙ ЦИКЛ
# =====================================================================
def main():
    global LAST_PREDICT_TIME
    
    print("🔄 ПРОГНОЗИСТ 2.0 ЗАПУЩЕН", flush=True)
    print(f"📊 Читает канал: {CHANNEL_STATS}", flush=True)
    print(f"📤 Отправляет в: {CHANNEL_PROGNOZ}", flush=True)
    print("=" * 60, flush=True)
    print("📌 АЛГОРИТМ:", flush=True)
    print("   1. Берём карты игрока из завершённой игры", flush=True)
    print("   2. Находим самую старшую (K=4, Q=3, J=2, 10=10... A=1)", flush=True)
    print("   3. Если есть повторяющиеся ранги - ПРОПУСК!", flush=True)
    print("   4. Прогноз = этот ранг (БЕЗ МАСТИ)", flush=True)
    print("   5. Проверяем N+1, N+2, N+3 (игрок + дилер)", flush=True)
    print("=" * 60, flush=True)
    
    offset = get_offset()
    history = load_history()
    
    # Принудительная загрузка последних сообщений
    all_messages = load_recent_messages()
    print(f"📥 Всего сообщений в памяти: {len(all_messages)}", flush=True)
    
    last_cleanup_time = time.time()
    
    while True:
        try:
            current_time = time.time()
            
            # Очистка кэша раз в час
            if current_time - last_cleanup_time > 3600:
                history = clean_memory(history)
                save_history(history)
                print(f"🧹 Кэш очищен. Записей в истории: {len(history)}", flush=True)
                last_cleanup_time = current_time
            
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
                
                if game_number in PROCESSED_GAMES:
                    continue
                
                if not is_final_game(text):
                    print(f"⏳ Ожидание финальной раздачи для #N{game_number}", flush=True)
                    continue
                
                print(f"📥 {text[:50]}...", flush=True)
                
                game_data = parse_game(text)
                if not game_data:
                    print(f"❌ Не удалось распарсить #N{game_number}", flush=True)
                    continue
                
                # Проверяем интервал
                if current_time - LAST_PREDICT_TIME < PREDICT_INTERVAL:
                    print(f"⏳ Интервал: {int(current_time - LAST_PREDICT_TIME)} сек < {PREDICT_INTERVAL} сек", flush=True)
                    continue
                
                # Делаем прогноз по ТВОЕМУ алгоритму
                prognoz = predict(game_data)
                if not prognoz:
                    print(f"⏭️ Нет прогноза для #N{game_number}", flush=True)
                    continue
                
                msg = f"🔮 <b>ПРОГНОЗ</b>\n"
                msg += f"📊 От игры: #N{game_data['number']}\n"
                msg += f"🃏 Ранг: {prognoz['rank']}\n"
                msg += f"🎯 Целевая игра: #N{prognoz['target']}\n"
                msg += f"📈 3 игры догон (N+1, N+2, N+3)\n"
                msg += f"⏰ {datetime.now().strftime('%H:%M:%S')}"
                
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
                        "time": datetime.now().isoformat(),
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
            import traceback
            traceback.print_exc()
            time.sleep(30)

if __name__ == "__main__":
    main()