import os
import json
from datetime import datetime

from config import (
    MOSCOW_TZ,
    BANK_FILE,
    START_BALANCE,
    DOGON_MULT,
    WIN_COEF,
    DOGON_GAMES,
    LOW_BALANCE_THRESHOLD,
)


def get_base(balance):
    if balance < 50000:
        return 20000
    if balance < 100000:
        return 50000
    if balance < 200000:
        return 100000
    if balance < 400000:
        return 200000
    return 400000


def get_first_bet(balance):
    base = get_base(balance)
    return round(base * 0.0025, 2)


DEFAULT_BANK = {
    "balance": START_BALANCE,
    "start_balance": START_BALANCE,
    "current_bet": None,
    "step": 0,
    "history": [],
    "last_updated": None,
}


def load_bank():
    if not os.path.exists(BANK_FILE):
        state = dict(DEFAULT_BANK)
        state["current_bet"] = get_first_bet(state["balance"])
        save_bank(state)
        return state

    try:
        with open(BANK_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception as e:
        print(f"⚠️ Ошибка чтения bank_state: {e}", flush=True)
        state = dict(DEFAULT_BANK)

    if not state.get("current_bet"):
        state["current_bet"] = get_first_bet(state["balance"])

    return state


def save_bank(state):
    state["last_updated"] = datetime.now(MOSCOW_TZ).isoformat()
    tmp = BANK_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, BANK_FILE)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения bank_state: {e}", flush=True)


def get_current_bet():
    state = load_bank()
    return (
        state.get("current_bet", 0),
        state.get("step", 0),
        state.get("balance", 0),
    )


def apply_result(status, bet_amount, dogon=None, telegram_send_func=None):
    """Обновляет банк после закрытия прогноза."""
    state = load_bank()
    bet = bet_amount if bet_amount else state.get("current_bet", 0)
    profit = 0.0

    if status == "win":
        profit = bet * (WIN_COEF - 1)
        state["balance"] += profit
        state["current_bet"] = get_first_bet(state["balance"])
        state["step"] = 0

    elif status == "lose":
        profit = -bet
        state["balance"] += profit
        state["current_bet"] = round(bet * DOGON_MULT, 2)
        # если проиграли все догоны — новая серия с Д0
        if (dogon or 0) >= DOGON_GAMES:
            state["step"] = 0
        else:
            state["step"] = (dogon or 0) + 1

    state["history"].append({
        "time": datetime.now(MOSCOW_TZ).isoformat(),
        "status": status,
        "bet": bet,
        "profit": profit,
        "balance_after": state["balance"],
    })
    state["history"] = state["history"][-500:]
    save_bank(state)

    if state["balance"] < LOW_BALANCE_THRESHOLD:
        print(
            f"⚠️⚠️⚠️ ВНИМАНИЕ: банк ниже {LOW_BALANCE_THRESHOLD} ₽ "
            f"(сейчас {state['balance']:.0f} ₽)",
            flush=True,
        )
        if telegram_send_func:
            try:
                telegram_send_func(
                    f"⚠️ <b>ВНИМАНИЕ</b>\n"
                    f"Банк упал ниже {LOW_BALANCE_THRESHOLD} ₽\n"
                    f"Текущий банк: <b>{state['balance']:.0f} ₽</b>"
                )
            except Exception:
                pass

    return state, profit