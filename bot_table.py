import os
import sys
import requests
import json
import re
import time
import random
from datetime import datetime, timedelta

# =====================================================================
# ПРИНУДИТЕЛЬНЫЙ ВЫВОД ЛОГОВ
# =====================================================================
sys.stdout.flush()
print("=" * 60, flush=True)
print("🃏 ПРОГНОЗИСТ 21 ОЧКО - РАБОЧАЯ ВЕРСИЯ", flush=True)
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
HISTORY_FILE = "history.json"
OFFSET_FILE = "offset.txt"
MAX_HISTORY = 200
PROCESSED_GAMES = set()
LAST_PREDICT_TIME = 0
PREDICT_INTERVAL = 120  # 2 минуты

# =====================================================================
# ФУНКЦИИ
# =====================================================================
def count_numeric_cards(cards):
    """Считает количество числовых карт (6-10)"""
    numeric = ['6', '7', '8', '9', '10']
    count = 0
    for card in cards:
        if card["rank"] in numeric:
            count += 1
    return count

def is_skip_game(text, game_data=None):
    """Проверяет, нужно ли пропустить игру"""
    if "21" in text:
        return True
    if "🔰" in text:
        return True
    if game_data:
        total = count_numeric_cards(game_data["player_cards"]) + count_numeric_cards(game_data["dealer_cards"])
        if total >= 4:
            return True
    return False

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

def is_final_game(text):
    return "✅" in text or "🔰" in text

def parse_game(text):
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
        player_match = re.search(r'(\d+)\(([^)]+)\)', player_part)
        if not player_match:
            return None
        player_cards_str = player_match.group(2).strip()
        
        dealer_part = parts[1].strip()
        dealer_match = re.search(r'(\d+)\(([^)]+)\)', dealer_part)
        if not dealer_match:
            return None
        dealer_cards_str = dealer_match.group(2).strip() if dealer_match else ""
        
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

def get_highest_card(cards):
    ranks_order = {"6": 1, "7": 2, "8": 3, "9": 4, "10": 0, "J": 5, "Q": 6, "K": 7, "A": 8}
    filtered = [c for c in cards if c["rank"] != "10"]
    if not filtered:
        return None
    highest = max(filtered, key=lambda c: ranks_order.get(c["rank"], 0))
    return highest

def get_next_card(rank):
    map_rank = {
        "6": "J", "7": "Q", "8": "K", "9": "A",
        "J": "J", "Q": "Q", "K": "K", "A": "A"
    }
    return map_rank.get(rank, rank)

def get_suit_by_position(position):
    suits = {1: "♣", 2: "♦", 3: "♥", 4: "♠"}
    return suits.get(position, "?")

def predict(game_data):
    game_num = game_data["number"]
    if game_num % 2 != 0:
        return None
    
    result = {}
    cards_list = []
    
    player_highest = get_highest_card(game_data["player_cards"])
    if player_highest:
        pos = 1
        for i, card in enumerate(game_data["player_cards"]):
            if card["rank"] == player_highest["rank"] and card["suit"] == player_highest["suit"]:
                pos = i + 1
                break
        next_rank = get_next_card(player_highest["rank"])
        next_suit = get_suit_by_position(pos)
        result["player"] = f"{next_rank}{next_suit}"
        cards_list.append(result["player"])
    else:
        result["player"] = None
    
    dealer_highest = get_highest_card(game_data["dealer_cards"])
    if dealer_highest:
        pos = 1
        for i, card in enumerate(game_data["dealer_cards"]):
            if card["rank"] == dealer_highest["rank"] and card["suit"] == dealer_highest["suit"]:
                pos = i + 1
                break
        next_rank = get_next_card(dealer_highest["rank"])
        next_suit = get_suit_by_position(pos)
        result["dealer"] = f"{next_rank}{next_suit}"
        if result["dealer"] not in cards_list:
            cards_list.append(result["dealer"])
    else:
        result["dealer"] = None
    
    target_game = game_num + 10
    while target_game % 2 != 0:
        target_game += 1
    
    result["target"] = target_game
    result["cards"] = " ".join(cards_list) if cards_list else "Нет прогноза"

    print(f"🔍 Прогноз для #N{game_num}: {result}", flush=True)
    
    return result

def check_results(history, all_messages):
    for entry in history:
        if entry.get("status") != "pending":
            continue
        
        target = entry.get("target")
        cards_to_find = entry.get("cards", "").split()
        from_game = entry.get("from_game")
        cards_str = entry.get("cards", "")
        message_id = entry.get("message_id")
        
        if not cards_to_find or not message_id:
            continue
        
        found = False
        found_game = None
        found_dogon = None
        
        for i in range(4):
            game_to_check = target + i
            
            for msg in all_messages:
                if f"#N{game_to_check}" in msg:
                    for card in cards_to_find:
                        if card in msg:
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
        
        original_text = f"🔮 <b>ПРОГНОЗ</b>\n"
        original_text += f"📊 От игры: #N{from_game}\n"
        original_text += f"🃏 Игрок и Дилер: {cards_str}\n"
        original_text += f"🎯 Целевая игра: #N{target}\n"
        original_text += f"📈 3 игры догон\n"
        original_text += f"⏰ {entry.get('time', '')[:16]}"
        
        if found:
            result_text = f"\n\n✅ <b>ЗАШЛО</b> на догоне {found_dogon}: #N{found_game}"
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

# =====================================================================
# ОСНОВНОЙ ЦИКЛ
# =====================================================================
def main():
    global LAST_PREDICT_TIME
    
    print("🔄 ПРОГНОЗИСТ ЗАПУЩЕН", flush=True)
    print(f"📊 Читает канал: {CHANNEL_STATS}", flush=True)
    print(f"📤 Отправляет в: {CHANNEL_PROGNOZ}", flush=True)
    print("=" * 60, flush=True)
    print("📌 Фильтры: 21, 🔰 и общее число 6-10 ≥ 4 — пропускаются", flush=True)
    print(f"⏱️ Интервал: {PREDICT_INTERVAL} сек (2 мин)", flush=True)
    print("=" * 60, flush=True)
    
    offset = get_offset()
    history = load_history()
    all_messages = []
    
    while True:
        try:
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
                
                if game_number % 2 != 0:
                    print(f"⏭️ Пропускаем нечётную игру #N{game_number}", flush=True)
                    continue
                
                if not is_final_game(text):
                    print(f"⏳ Ожидание финальной раздачи для #N{game_number}", flush=True)
                    continue
                
                print(f"📥 {text[:50]}...", flush=True)
                
                game_data = parse_game(text)
                if not game_data:
                    print(f"❌ Не удалось распарсить #N{game_number}", flush=True)
                    continue
                
                # 🔥 ФИЛЬТРЫ
                if is_skip_game(text, game_data):
                    numeric_count = count_numeric_cards(game_data["player_cards"]) + count_numeric_cards(game_data["dealer_cards"])
                    reason = "21" if "21" in text else "🔰" if "🔰" in text else f"числовых: {numeric_count} (≥4)"
                    print(f"⏭️ Пропускаем #N{game_number} (фильтр: {reason})", flush=True)
                    continue
                
                # Рандом: 70% шанс дать прогноз
                if random.random() > 0.7:
                    print(f"⏭️ Случайно пропускаем #N{game_number}", flush=True)
                    continue
                
                current_time = time.time()
                if current_time - LAST_PREDICT_TIME < PREDICT_INTERVAL:
                    print(f"⏳ Интервал: {int(current_time - LAST_PREDICT_TIME)} сек < {PREDICT_INTERVAL} сек", flush=True)
                    continue
                
                prognoz = predict(game_data)
                if prognoz:
                    msg = f"🔮 <b>ПРОГНОЗ</b>\n"
                    msg += f"📊 От игры: #N{game_data['number']}\n"
                    msg += f"🃏 Игрок и Дилер: {prognoz['cards']}\n"
                    msg += f"🎯 Целевая игра: #N{prognoz['target']}\n"
                    msg += f"📈 3 игры догон\n"
                    msg += f"⏰ {datetime.now().strftime('%H:%M:%S')}"
                    
                    message_id = send_message(msg)
                    if message_id:
                        print(f"✅ Прогноз отправлен: #N{prognoz['target']}", flush=True)
                        LAST_PREDICT_TIME = current_time
                        PROCESSED_GAMES.add(game_number)
                        
                        history.append({
                            "from_game": game_data["number"],
                            "target": prognoz["target"],
                            "cards": prognoz['cards'],
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
            time.sleep(30)

if __name__ == "__main__":
    main()