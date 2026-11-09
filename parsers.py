import re
from datetime import datetime

from config import (
    MOSCOW_TZ,
    GAME_CYCLE,
    ENABLE_LAST_10,
    ENABLE_FULL_V1,
)


# =====================================================================
# SUITS
# =====================================================================

SUITS = {
    "\u2660": "\u2660\ufe0f",  # ♠
    "\u2663": "\u2663\ufe0f",  # ♣
    "\u2666": "\u2666\ufe0f",  # ♦
    "\u2665": "\u2665\ufe0f",  # ♥
}


def normalize_suit(suit):
    if suit is None:
        return None

    suit = str(suit).strip()
    suit = suit.replace("\ufe0f", "")

    return SUITS.get(suit)


def normalize_rank(rank):
    if rank is None:
        return None

    rank = str(rank).strip().upper()

    if rank == "А":
        rank = "A"

    if rank in {
        "6", "7", "8", "9", "10",
        "J", "Q", "K", "A",
    }:
        return rank

    return None


def card_to_text(card):
    if not card:
        return ""

    rank = normalize_rank(card.get("rank"))
    suit = normalize_suit(card.get("suit"))

    if not rank or not suit:
        return ""

    return f"{rank}{suit}"


def cards_to_text(cards):
    result = []

    for card in cards:
        value = card_to_text(card)
        if value:
            result.append(value)

    return " ".join(result)


# =====================================================================
# CYBER 21 SCORE
# =====================================================================

CARD_VALUES = {
    "6": 6, "7": 7, "8": 8, "9": 9, "10": 10,
    "J": 2, "Q": 3, "K": 4, "A": 11,
}


def cyber21_score(cards):
    total = 0

    for card in cards:
        rank = normalize_rank(card.get("rank"))
        if rank in CARD_VALUES:
            total += CARD_VALUES[rank]

    return total


# =====================================================================
# PARSE CARDS
# =====================================================================

CARD_RE = re.compile(
    r"(10|[6-9AJQK])\s*"
    r"(\u2660|\u2663|\u2666|\u2665)"
    r"\ufe0f?"
)


def parse_cards(text):
    result = []

    if not text:
        return result

    for match in CARD_RE.finditer(text):
        rank = normalize_rank(match.group(1))
        suit = normalize_suit(match.group(2))

        if not rank or not suit:
            continue

        result.append({
            "rank": rank,
            "suit": suit,
        })

    return result


# =====================================================================
# PARSE GAME MESSAGE
# =====================================================================

def parse_game_message(text):
    if not text:
        return None

    number_match = re.search(r"#N(\d+)", text)
    if not number_match:
        return None

    game_number = int(number_match.group(1))

    groups = re.findall(r"\(([^()]*)\)", text)
    if len(groups) < 2:
        return None

    player_text = groups[0]
    dealer_text = groups[1]

    player_cards = parse_cards(player_text)
    dealer_cards = parse_cards(dealer_text)

    if not player_cards:
        return None

    player_score = cyber21_score(player_cards)
    dealer_score = cyber21_score(dealer_cards)

    id_match = re.search(r"ID:\s*(\d+)", text)
    game_id = id_match.group(1) if id_match else None

    is_draw = bool(re.search(r"#X\b", text))
    is_ochko = bool(re.search(r"#O\b", text))
    is_r = bool(re.search(r"#R\b", text))
    is_finished = bool(re.search(r"✅", text))

    return {
        "game_number": game_number,
        "game_id": game_id,
        "player_cards": player_cards,
        "dealer_cards": dealer_cards,
        "player_score": player_score,
        "dealer_score": dealer_score,
        "is_draw": is_draw,
        "is_ochko": is_ochko,
        "is_r": is_r,
        "is_finished": is_finished,
        "raw_text": text,
    }


# =====================================================================
# LOG GAME
# =====================================================================

def log_game(game):
    player = game.get("player_cards", [])
    dealer = game.get("dealer_cards", [])

    print("", flush=True)
    print("────────────────────────────────────", flush=True)
    print(f"🔒 ИГРА ЗАФИКСИРОВАНА #N{game['game_number']}", flush=True)
    print(f"👤 P: {game['player_score']} ({cards_to_text(player)})", flush=True)
    print(f"🎰 D: {game['dealer_score']} ({cards_to_text(dealer)})", flush=True)

    if game.get("is_draw"):
        print("🔰 #X — НИЧЬЯ", flush=True)

    if game.get("is_ochko"):
        print("⭕ #O — ОЧКО", flush=True)
        
    if game.get("is_r"):
        print("⚠️ #R — АНОМАЛИЯ", flush=True)

    print("────────────────────────────────────", flush=True)


# =====================================================================
# GAME NUMBER (cycle)
# =====================================================================

def add_game_offset(number, offset, cycle):
    return ((int(number) - 1 + int(offset)) % cycle) + 1


# =====================================================================
# ALGORITHM: ПОСЛЕДНЯЯ 10 (С ФИЛЬТРАМИ) - СТАРЫЙ АЛГОРИТМ
# =====================================================================

def get_last_card_prediction(game, cycle):
    player = game.get("player_cards", [])
    dealer = game.get("dealer_cards", [])

    if not player:
        return None

    # 1. У дилера должно быть 0 карт в триггере
    if dealer:
        return None

    # 2. Нет очка в триггере
    if game.get("is_ochko"):
        return None

    # 3. Последняя карта должна быть 10
    last_card = player[-1]
    last_rank = normalize_rank(last_card.get("rank"))

    if last_rank != "10":
        return None

    # 4. Ровно ОДНА 10 у игрока
    tens_count = sum(
        1 for card in player 
        if normalize_rank(card.get("rank")) == "10"
    )

    if tens_count > 1:
        print(f"⚠️ #N{game['game_number']}: >1 десятки — пропуск", flush=True)
        return None
        
    # 5. У игрока минимум 3 карты (фильтр от триггера 192)
    if len(player) < 3:
        print(f"⚠️ #N{game['game_number']}: у игрока <3 карт — пропуск", flush=True)
        return None

    # 6. Игра завершена
    if "✅" not in game.get("raw_text", ""):
        return None

    suit = normalize_suit(last_card.get("suit"))

    if not suit:
        return None

    target_offset = len(player)
    target_number = add_game_offset(game["game_number"], target_offset, cycle)

    return {
        "algorithm": "последняя 10",
        "trigger_number": game["game_number"],
        "trigger_game_id": game.get("game_id"),
        "target_number": target_number,
        "predicted_suit": suit,
        "trigger_player": [card_to_text(c) for c in player],
        "trigger_dealer": [card_to_text(c) for c in dealer],
        "trigger_player_score": game["player_score"],
        "trigger_dealer_score": game["dealer_score"],
        "status": "pending",
        "created_at": datetime.now(MOSCOW_TZ).isoformat(),
        "result_game": None,
        "found_card": None,
        "dogon": None,
        "message_id": None,
        "sent": False,
        "target_offset": target_offset,
    }


# =====================================================================
# ALGORITHM: FULL_V1.0 (МАСТЬ ПО ПЕРВОЙ КАРТЕ, +10 ИГР) - НОВЫЙ
# =====================================================================

def get_full_v1_prediction(game, cycle):
    player = game.get("player_cards", [])
    dealer = game.get("dealer_cards", [])

    if not player:
        return None

    # 1. У дилера должны быть карты
    if not dealer:
        return None

    # 2. Нет аномалий (#R), ничьи (#X) и очка (#O)
    if game.get("is_r") or game.get("is_draw") or game.get("is_ochko"):
        return None

    # 3. У игрока нет Туза (A) и Десятки (10)
    for card in player:
        rank = normalize_rank(card.get("rank"))
        if rank in ("A", "10"):
            return None

    # 4. Берем первую карту игрока
    first_card = player[0]
    rank = normalize_rank(first_card.get("rank"))
    
    # 5. Таблица маппинга масти (Full_v1.0)
    # Q -> ♦️, J/K -> ♥️, 2/4/6/8 -> ♠️, 3/5/7/9 -> ♣️
    if rank == "Q":
        predicted_suit = normalize_suit("♦")
    elif rank in ("J", "K"):
        predicted_suit = normalize_suit("♥")
    elif rank in ("2", "4", "6", "8"):
        predicted_suit = normalize_suit("♠")
    elif rank in ("3", "5", "7", "9"):
        predicted_suit = normalize_suit("♣")
    else:
        return None

    # 6. Триггер и цель (+10 игр)
    trigger_number = game["game_number"]
    target_number = add_game_offset(trigger_number, 10, cycle)

    return {
        "algorithm": "Full_v1.0",
        "trigger_number": trigger_number,
        "trigger_game_id": game.get("game_id"),
        "target_number": target_number,
        "predicted_suit": predicted_suit,
        "trigger_player": [card_to_text(c) for c in player],
        "trigger_dealer": [card_to_text(c) for c in dealer],
        "trigger_player_score": game["player_score"],
        "trigger_dealer_score": game["dealer_score"],
        "status": "pending",
        "created_at": datetime.now(MOSCOW_TZ).isoformat(),
        "result_game": None,
        "found_card": None,
        "dogon": None,
        "message_id": None,
        "sent": False,
        "target_offset": 10,
    }


# =====================================================================
# ОБЩАЯ ФУНКЦИЯ ЗАПУСКА АЛГОРИТМОВ
# =====================================================================

def get_algorithm_predictions(game, cycle):
    predictions = []
    
    # 1. Старый алгоритм (Последняя 10) — если включен в конфиге
    if ENABLE_LAST_10:
        p1 = get_last_card_prediction(game, cycle)
        if p1:
            predictions.append(p1)
        
    # 2. Новый алгоритм (Full_v1.0) — если включен в конфиге
    if ENABLE_FULL_V1:
        p2 = get_full_v1_prediction(game, cycle)
        if p2:
            predictions.append(p2)
        
    return predictions


# =====================================================================
# CHECK PLAYER SUIT
# =====================================================================

def check_prediction_suit(game, predicted_suit):
    player_cards = game.get("player_cards", [])

    for card in player_cards:
        suit = normalize_suit(card.get("suit"))

        if suit == predicted_suit:
            return card_to_text(card)

    return None