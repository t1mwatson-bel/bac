import re


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

    return {
        "game_number": game_number,
        "game_id": game_id,
        "player_cards": player_cards,
        "dealer_cards": dealer_cards,
        "player_score": player_score,
        "dealer_score": dealer_score,
        "is_draw": is_draw,
        "is_ochko": is_ochko,
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
        print("⭕ #O — ОЧКО (21), пропускаем триггер", flush=True)

    print("────────────────────────────────────", flush=True)


# =====================================================================
# GAME NUMBER (cycle)
# =====================================================================

def add_game_offset(number, offset, cycle):
    return ((int(number) - 1 + int(offset)) % cycle) + 1


# =====================================================================
# ALGORITHM: ПОСЛЕДНЯЯ 10
# =====================================================================

def get_last_card_prediction(game, cycle):
    player = game.get("player_cards", [])
    dealer = game.get("dealer_cards", [])

    if not player:
        return None

    if dealer:
        return None

    if game.get("is_ochko"):
        print(
            f"⭕ #N{game['game_number']}: #O (очко) — пропуск триггера",
            flush=True,
        )
        return None

    last_card = player[-1]
    last_rank = normalize_rank(last_card.get("rank"))

    if last_rank != "10":
        return None

    if "✅" not in game.get("raw_text", ""):
        return None

    suit = normalize_suit(last_card.get("suit"))

    if not suit:
        return None

    target_offset = len(player)
    target_number = add_game_offset(game["game_number"], target_offset, cycle)

    from datetime import datetime
    from config import MOSCOW_TZ

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


def get_algorithm_predictions(game, cycle):
    prediction = get_last_card_prediction(game, cycle)
    return [prediction] if prediction else []


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