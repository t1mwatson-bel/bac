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
print("🃏 ПРОГНОЗИСТ 21 ОЧКО - ЗАПУСК", flush=True)
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
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}", flush=True)
        return False

def is_final_game(text):
    """Проверяет, что раздача финальная (есть ✅ или 🔰)"""
    return "✅" in text or "🔰" in text

def parse_game(text):
    try:
        game_match = re.search(r'#N(\d+)', text)
        if not game_match:
            return None
        game_number = int(game_match.group(1))
        
        parts = text.split('-')
        if len(parts) < 2:
            return None
        
        player_part = parts[0].strip()
        player_match = re.search(r'(\d+)\(([^)]+)\)', player_part)
        if not player_match:
            return None
        player_score = int(player_match.group(1))
        player_cards_str = player_match.group(2).strip()
        
        dealer_part = parts[1].strip()
        dealer_match = re.search(r'(\d+)\(([^)]+)\)', dealer_part)
        if not dealer_match:
            return None
        dealer_score = int(dealer_match.group(1))
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
            "player_score": player_score,
            "dealer_score": dealer_score,
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
    else:
        result["dealer"] = None
    
    target_game = game_num + 10
    while target_game % 2 != 0:
        target_game += 1
    
    result["target"] = target_game
    return result

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
    print("🔄 ПРОГНОЗИСТ ЗАПУЩЕН", flush=True)
    print(f"📊 Читает канал: {CHANNEL_STATS}", flush=True)
    print(f"📤 Отправляет в: {CHANNEL_PROGNOZ}", flush=True)
    print("=" * 60, flush=True)
    
    offset = get_offset()
    history = load_history()
    
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
                
                # 🔥 СНАЧАЛА ПРОВЕРЯЕМ ФИНАЛЬНОСТЬ
                if not is_final_game(text):
                    print(f"⏳ Ожидание финальной раздачи для #N{game_number}", flush=True)
                    continue
                
                # ПОТОМ ПРОВЕРЯЕМ, НЕ ОБРАБОТАНА ЛИ УЖЕ
                if game_number in PROCESSED_GAMES:
                    continue
                
                print(f"📥 {text[:50]}...", flush=True)
                
                game_data = parse_game(text)
                if not game_data:
                    continue
                
                # Прогноз только для чётных игр
                if game_data["number"] % 2 != 0:
                    continue
                
                prognoz = predict(game_data)
                if not prognoz:
                    continue
                
                msg = f"🔮 <b>ПРОГНОЗ</b>\n"
                msg += f"📊 От игры: #N{game_data['number']}\n"
                if prognoz.get("player"):
                    msg += f"🃏 Игрок: {prognoz['player']}\n"
                if prognoz.get("dealer"):
                    msg += f"🃏 Дилер: {prognoz['dealer']}\n"
                msg += f"🎯 Целевая игра: #N{prognoz['target']}\n"
                msg += f"⏰ {datetime.now().strftime('%H:%M:%S')}"
                
                if send_message(msg):
                    print(f"✅ Прогноз отправлен: #N{prognoz['target']}", flush=True)
                    PROCESSED_GAMES.add(game_data["number"])
                    history.append({
                        "from_game": game_data["number"],
                        "target": prognoz["target"],
                        "player_prognoz": prognoz.get("player"),
                        "dealer_prognoz": prognoz.get("dealer"),
                        "time": datetime.now().isoformat(),
                        "status": "pending"
                    })
                    save_history(history)
            
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