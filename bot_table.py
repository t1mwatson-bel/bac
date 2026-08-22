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
print("🃏 ПРОГНОЗИСТ БАККАРА - АЛГОРИТМ ПО ТВОИМ ПРАВИЛАМ", flush=True)
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

# Масть по позиции (порядковому номеру)
SUIT_BY_POSITION = {
    1: '♣',  # 1-я карта = Трефы
    2: '♦',  # 2-я карта = Бубны
    3: '♥',  # 3-я карта = Червы
    4: '♠'   # 4-я карта = Пики
}

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

def has_duplicate_ranks(cards):
    """Проверяет, есть ли повторяющиеся ранги среди карт"""
    ranks = [c["rank"] for c in cards]
    return len(ranks) != len(set(ranks))

def get_highest_card_index(cards):
    """
    Находит индекс (позицию) самой старшей карты по ТВОИМ правилам.
    Возвращает позицию: 1, 2, 3 или 4
    Если есть одинаковые ранги - возвращает None (пропуск)
    """
    # Проверяем на повторяющиеся ранги
    ranks = [c["rank"] for c in cards]
    if len(ranks) != len(set(ranks)):
        return None  # Пропуск, есть одинаковые карты
    
    # Находим карту с максимальным значением ранга
    max_rank = max(cards, key=lambda c: RANK_VALUES.get(c["rank"], 0))
    
    # Возвращаем её позицию (индекс + 1)
    return cards.index(max_rank) + 1

def predict(game_data):
    """
    ТВОЙ АЛГОРИТМ:
    1. Проверяем, что у игрока 3 карты
    2. Берём 4 карты: 3 игрока + 1-я дилера
    3. Находим самую старшую по ТВОИМ правилам
    4. Смотрим её порядковый номер (1, 2, 3 или 4)
    5. По номеру определяем масть
    6. Прогноз на N+1, N+2, N+3
    """
    player_cards = game_data["player_cards"]
    dealer_cards = game_data["dealer_cards"]
    
    # 1. Проверяем, что у игрока 3 карты
    if len(player_cards) != 3:
        print(f"⏭️ У игрока {len(player_cards)} карт, нужно 3", flush=True)
        return None
    
    # 2. Берём 4 карты: 3 игрока + 1-я дилера
    if not dealer_cards:
        print(f"⏭️ Нет карт дилера", flush=True)
        return None
    
    four_cards = player_cards + [dealer_cards[0]]
    
    # 3. Проверяем на повторяющиеся ранги (пропуск)
    if has_duplicate_ranks(four_cards):
        print(f"⏭️ Есть повторяющиеся ранги - пропуск", flush=True)
        return None
    
    # 4. Находим позицию самой старшей карты
    position = get_highest_card_index(four_cards)
    if position is None:
        return None
    
    # 5. По позиции определяем масть
    suit = SUIT_BY_POSITION.get(position, '?')
    game_num = game_data["number"]
    
    print(f"🔍 #N{game_num}: самая старшая на позиции {position} → масть {suit}", flush=True)
    
    return {
        "from_game": game_num,
        "position": position,
        "suit": suit,
        "target": game_num + 1,  # N+1
        "dogon_1": game_num + 1,
        "dogon_2": game_num + 2,
        "dogon_3": game_num + 3,
        "cards": suit  # Для отображения
    }

def check_results(history, all_messages):
    """Проверяет результаты прогнозов"""
    for entry in history:
        if entry.get("status") != "pending":
            continue
        
        target = entry.get("target")
        suit = entry.get("suit")
        from_game = entry.get("from_game")
        message_id = entry.get("message_id")
        
        if not suit or not message_id:
            continue
        
        found = False
        found_game = None
        found_dogon = None
        
        # Проверяем N+1, N+2, N+3
        for i in range(3):
            game_to_check = target + i
            for msg in all_messages:
                if f"#N{game_to_check}" in msg:
                    # Ищем масть в сообщении (в картах)
                    if suit in msg:
                        found = True
                        found_game = game_to_check
                        found_dogon = i + 1
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
        
        # Формируем результат
        suit_name = {'♣': 'Трефы', '♦': 'Бубны', '♥': 'Червы', '♠': 'Пики'}.get(suit, suit)
        
        original_text = f"🔮 <b>ПРОГНОЗ</b>\n"
        original_text += f"📊 От игры: #N{from_game}\n"
        original_text += f"🃏 Масть: {suit} {suit_name}\n"
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

# =====================================================================
# ОСНОВНОЙ ЦИКЛ
# =====================================================================
def main():
    global LAST_PREDICT_TIME
    
    print("🔄 ПРОГНОЗИСТ ЗАПУЩЕН (НОВЫЙ АЛГОРИТМ)", flush=True)
    print(f"📊 Читает канал: {CHANNEL_STATS}", flush=True)
    print(f"📤 Отправляет в: {CHANNEL_PROGNOZ}", flush=True)
    print("=" * 60, flush=True)
    print("📌 АЛГОРИТМ:", flush=True)
    print("   1. Ждём игру где у игрока 3 карты", flush=True)
    print("   2. Берём 4 карты: 3 игрока + 1-я дилера", flush=True)
    print("   3. Находим самую старшую (K=4, Q=3, J=2, 10=10... A=1)", flush=True)
    print("   4. Смотрим порядковый номер (1,2,3,4)", flush=True)
    print("   5. 1=♣, 2=♦, 3=♥, 4=♠", flush=True)
    print("   6. Прогноз на N+1, N+2, N+3", flush=True)
    print("   7. Если есть повторяющиеся ранги - ПРОПУСК!", flush=True)
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
                
                if not is_final_game(text):
                    print(f"⏳ Ожидание финальной раздачи для #N{game_number}", flush=True)
                    continue
                
                print(f"📥 {text[:50]}...", flush=True)
                
                game_data = parse_game(text)
                if not game_data:
                    print(f"❌ Не удалось распарсить #N{game_number}", flush=True)
                    continue
                
                # Проверяем интервал
                current_time = time.time()
                if current_time - LAST_PREDICT_TIME < PREDICT_INTERVAL:
                    print(f"⏳ Интервал: {int(current_time - LAST_PREDICT_TIME)} сек < {PREDICT_INTERVAL} сек", flush=True)
                    continue
                
                # Делаем прогноз по ТВОЕМУ алгоритму
                prognoz = predict(game_data)
                if not prognoz:
                    print(f"⏭️ Нет прогноза для #N{game_number}", flush=True)
                    continue
                
                suit_name = {'♣': 'Трефы', '♦': 'Бубны', '♥': 'Червы', '♠': 'Пики'}.get(prognoz['suit'], prognoz['suit'])
                
                msg = f"🔮 <b>ПРОГНОЗ</b>\n"
                msg += f"📊 От игры: #N{game_data['number']}\n"
                msg += f"🃏 Масть: {prognoz['suit']} {suit_name}\n"
                msg += f"📌 Позиция самой старшей: {prognoz['position']}\n"
                msg += f"🎯 Целевая игра: #N{prognoz['target']}\n"
                msg += f"📈 3 игры догон (N+1, N+2, N+3)\n"
                msg += f"⏰ {datetime.now().strftime('%H:%M:%S')}"
                
                message_id = send_message(msg)
                if message_id:
                    print(f"✅ Прогноз отправлен: #N{prognoz['target']} - {prognoz['suit']}", flush=True)
                    LAST_PREDICT_TIME = current_time
                    PROCESSED_GAMES.add(game_number)
                    
                    history.append({
                        "from_game": game_data["number"],
                        "target": prognoz["target"],
                        "suit": prognoz["suit"],
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