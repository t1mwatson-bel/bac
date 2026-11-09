import os
import json
from datetime import datetime

from config import (
    MOSCOW_TZ,
    START_BALANCE,
    DOGON_MULT,
    WIN_COEF,
    DOGON_GAMES,
    LOW_BALANCE_THRESHOLD,
)

# Файлы для каждого алгоритма
BANK_FILES = {
    "Bas_v2.0": "bank_bas.json",
    "Full_v1.0": "bank_full.json",
}


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


def get_default_bank():
    return {
        "balance": START_BALANCE,
        "start_balance": START_BALANCE,
        "current_bet": None,
        "step": 0,
        "history": [],
        "last_updated": None,
    }


def load_bank(algorithm="Bas_v2.0"):
    file_path = BANK_FILES.get(algorithm, "bank_bas.json")

    if not os.path.exists(file_path):
        state = get_default_bank()
        state["current_bet"] = get_first_bet(state["balance"])
        save_bank(state, algorithm)
        return state

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception as e:
        print(f"⚠️ Ошибка чтения {file_path}: {e}", flush=True)
        state = get_default_bank()

    if not state.get("current_bet"):
        state["current_bet"] = get_first_bet(state["balance"])

    return state


def save_bank(state, algorithm="Bas_v2.0"):
    file_path = BANK_FILES.get(algorithm, "bank_bas.json")
    state["last_updated"] = datetime.now(MOSCOW_TZ).isoformat()
    tmp = file_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, file_path)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения {file_path}: {e}", flush=True)


def get_current_bet(algorithm="Bas_v2.0"):
    state = load_bank(algorithm)
    return (
        state.get("current_bet", 0),
        state.get("step", 0),
        state.get("balance", 0),
    )


def apply_result(algorithm, status, bet_amount, dogon=None, telegram_send_func=None):
    """
    Обновляет банк после закрытия прогноза.
    Догон сквозной: при проигрыше ставка продолжает умножаться.
    """
    state = load_bank(algorithm)
    bet = bet_amount if bet_amount else state.get("current_bet", 0)
    profit = 0.0

    if status == "win":
        # Чистая прибыль от выигрышной ставки
        profit = bet * (WIN_COEF - 1)
        state["balance"] += profit
        # После выигрыша сбрасываем на первую ставку
        state["current_bet"] = get_first_bet(state["balance"])
        state["step"] = 0

    elif status == "lose":
        is_full_minus = (dogon or 0) >= DOGON_GAMES

        if is_full_minus:
            # Проиграли ВСЮ серию (дошли до Д3) — списываем деньги
            profit = -bet
            state["balance"] += profit
            # Новая серия (или продолжение, если хочешь сквозной)
            state["current_bet"] = round(bet * DOGON_MULT, 2)
            state["step"] = 0
        else:
            # Проиграли промежуточный шаг (Д0, Д1, Д2) — банк НЕ трогаем
            profit = 0.0
            state["current_bet"] = round(bet * DOGON_MULT, 2)
            state["step"] = (dogon or 0) + 1

    state["history"].append({
        "time": datetime.now(MOSCOW_TZ).isoformat(),
        "status": status,
        "bet": bet,
        "profit": profit,
        "balance_after": state["balance"],
    })
    state["history"] = state["history"][-500:]
    save_bank(state, algorithm)

    if state["balance"] < LOW_BALANCE_THRESHOLD:
        print(
            f"⚠️⚠️⚠️ [{algorithm}] ВНИМАНИЕ: банк ниже {LOW_BALANCE_THRESHOLD} ₽ "
            f"(сейчас {state['balance']:.0f} ₽)",
            flush=True,
        )
        if telegram_send_func:
            try:
                telegram_send_func(
                    f"⚠️ <b>ВНИМАНИЕ</b> [{algorithm}]\n"
                    f"Банк упал ниже {LOW_BALANCE_THRESHOLD} ₽\n"
                    f"Текущий банк: <b>{state['balance']:.0f} ₽</b>"
                )
            except Exception:
                pass

    return state, profit