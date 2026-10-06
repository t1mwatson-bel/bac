import os
from datetime import datetime
from collections import Counter, defaultdict

from config import (
    MOSCOW_TZ,
    STATS_HTML_FILE,
    START_BALANCE,
    LOGO_URL,
    BOT_LINK,
    BRAND_NAME,
)

import bank as bank_module


def generate_stats(predictions):
    """Генерит stats.html из списка прогнозов + bank_state."""
    bank = bank_module.load_bank()

    total = len(predictions)
    win = sum(1 for p in predictions if p.get("status") == "win")
    lose = sum(1 for p in predictions if p.get("status") == "lose")
    pending = sum(1 for p in predictions if p.get("status") == "pending")
    expired = sum(1 for p in predictions if p.get("status") == "expired")

    decided = win + lose
    winrate = round(win / decided * 100, 1) if decided else 0.0

    balance = bank.get("balance", START_BALANCE)
    start_balance = bank.get("start_balance", START_BALANCE)
    profit = balance - start_balance

    total_staked = sum(
        p.get("bet_amount", 0) for p in predictions
        if p.get("status") in ("win", "lose")
    )
    roi = round(profit / total_staked * 100, 1) if total_staked else 0.0

    current_bet = bank.get("current_bet", 0)
    current_step = bank.get("step", 0)

    dogons = Counter()
    dogons_win = Counter()
    for p in predictions:
        if p.get("status") in ("win", "lose"):
            d = p.get("dogon")
            if d is not None:
                dogons[d] += 1
                if p.get("status") == "win":
                    dogons_win[d] += 1

    suits = Counter()
    suits_win = Counter()
    for p in predictions:
        s = p.get("predicted_suit")
        if s:
            suits[s] += 1
            if p.get("status") == "win":
                suits_win[s] += 1

    days = defaultdict(lambda: {"win": 0, "lose": 0})
    for p in predictions:
        if p.get("status") not in ("win", "lose"):
            continue
        created = p.get("created_at") or ""
        day = created[:10] if len(created) >= 10 else "?"
        days[day][p["status"]] += 1

    # последние 30 прогнозов
    rows = []
    for p in predictions[-30:][::-1]:
        status = p.get("status", "?")
        mark = {"win": "✅", "lose": "❌", "pending": "⏳",
                "expired": "🗑️"}.get(status, "?")
        target = p.get("target_number", "?")
        suit = p.get("predicted_suit", "").replace("\ufe0f", "")
        dogon = p.get("dogon")
        dogon_str = f"Д{dogon}" if dogon is not None else ""

        bets = p.get("bets") or [p.get("bet_amount", 0)]
        dogon_idx = p.get("dogon") or 0
        bet = bets[dogon_idx] if dogon_idx < len(bets) else bets[-1]

        created = (p.get("created_at") or "")[:16].replace("T", " ")
        rows.append(
            f"<tr><td>{mark}</td><td>#N{target}</td><td>{suit}</td>"
            f"<td>{bet:.0f} ₽</td><td>{dogon_str}</td>"
            f"<td>{status}</td><td>{created}</td></tr>"
        )
    rows_html = "\n".join(rows) or '<tr><td colspan="7">Нет данных</td></tr>'

    dogon_rows = ""
    for d in sorted(dogons.keys()):
        played = dogons[d]
        won = dogons_win.get(d, 0)
        wr = round(won / played * 100, 1) if played else 0
        dogon_rows += (
            f"<tr><td>Д{d}</td><td>{played}</td>"
            f"<td>{won}</td><td>{wr}%</td></tr>"
        )

    suit_rows = ""
    for s, cnt in suits.items():
        won = suits_win.get(s, 0)
        wr = round(won / cnt * 100, 1) if cnt else 0
        s_clean = s.replace("\ufe0f", "")
        suit_rows += (
            f"<tr><td>{s_clean}</td><td>{cnt}</td>"
            f"<td>{won}</td><td>{wr}%</td></tr>"
        )

    day_rows = ""
    for day in sorted(days.keys(), reverse=True)[:30]:
        d = days[day]
        w, l = d["win"], d["lose"]
        total_d = w + l
        wr = round(w / total_d * 100, 1) if total_d else 0
        day_rows += (
            f"<tr><td>{day}</td><td>{total_d}</td>"
            f"<td>{w}</td><td>{l}</td><td>{wr}%</td></tr>"
        )

    updated = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S")
    profit_class = "win" if profit >= 0 else "lose"

    html = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>__BRAND__ — Статистика</title>
<meta http-equiv="refresh" content="300">
<link rel="icon" type="image/png" href="__LOGO__">
<style>
* { box-sizing: border-box; }
body {
    font-family: 'Segoe UI', Arial, sans-serif;
    background: radial-gradient(circle at top, #0a1428 0%, #050810 60%, #000 100%);
    color: #e8eef7;
    margin: 0;
    padding: 20px;
    min-height: 100vh;
}
.logo-wrap { text-align: center; margin-bottom: 20px; padding: 8px 0; }
.logo-wrap img {
    max-width: 220px;
    width: 100%;
    height: auto;
    filter: drop-shadow(0 0 30px rgba(0, 179, 255, 0.6));
    animation: pulse 4s ease-in-out infinite;
}
@keyframes pulse {
    0%, 100% { filter: drop-shadow(0 0 25px rgba(0, 179, 255, 0.5)); }
    50% { filter: drop-shadow(0 0 45px rgba(0, 229, 255, 0.9)); }
}
h1 {
    color: #fff;
    text-align: center;
    font-size: 22px;
    letter-spacing: 3px;
    text-transform: uppercase;
    margin: 0 0 28px 0;
    text-shadow: 0 0 20px rgba(0, 179, 255, 0.5);
}
h2 {
    color: #00e5ff;
    font-size: 15px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    margin: 30px 0 12px 0;
    text-shadow: 0 0 15px rgba(0, 229, 255, 0.4);
}
.cards { display:flex; flex-wrap:wrap; gap:12px; margin-bottom:24px; }
.card {
    background: linear-gradient(145deg, #0d1b2e 0%, #0a1428 100%);
    border: 1px solid rgba(0, 179, 255, 0.25);
    padding: 16px 20px;
    border-radius: 12px;
    min-width: 140px;
    flex: 1 1 140px;
    box-shadow: 0 0 20px rgba(0, 179, 255, 0.08);
    transition: all 0.3s ease;
}
.card:hover {
    border-color: rgba(0, 229, 255, 0.6);
    box-shadow: 0 0 30px rgba(0, 229, 255, 0.25);
    transform: translateY(-2px);
}
.card .label {
    font-size: 11px;
    color: #6a8db8;
    text-transform: uppercase;
    letter-spacing: 1px;
}
.card .value {
    font-size: 26px;
    font-weight: bold;
    margin-top: 6px;
    color: #fff;
    text-shadow: 0 0 15px rgba(0, 229, 255, 0.3);
}
.win { color: #00ff9d !important; text-shadow: 0 0 15px rgba(0, 255, 157, 0.5) !important; }
.lose { color: #ff3860 !important; text-shadow: 0 0 15px rgba(255, 56, 96, 0.5) !important; }
.pending { color: #ffd700 !important; text-shadow: 0 0 15px rgba(255, 215, 0, 0.5) !important; }
.expired { color: #6a8db8 !important; }
table {
    width: 100%;
    border-collapse: collapse;
    background: linear-gradient(145deg, #0d1b2e 0%, #0a1428 100%);
    border: 1px solid rgba(0, 179, 255, 0.25);
    border-radius: 12px;
    overflow: hidden;
    margin-bottom: 24px;
    box-shadow: 0 0 20px rgba(0, 179, 255, 0.08);
}
th, td {
    padding: 10px 14px;
    text-align: left;
    border-bottom: 1px solid rgba(0, 179, 255, 0.15);
    font-size: 14px;
}
th {
    background: rgba(0, 179, 255, 0.08);
    color: #00e5ff;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1px;
    font-size: 12px;
}
tr:last-child td { border-bottom: none; }
tr:hover td { background: rgba(0, 179, 255, 0.05); }
.updated {
    color: #6a8db8;
    font-size: 12px;
    margin-top: 20px;
    text-align: center;
}
.bot-btn {
    display: inline-block;
    background: linear-gradient(135deg, #00b3ff 0%, #00e5ff 100%);
    color: #050810 !important;
    padding: 14px 32px;
    border-radius: 30px;
    text-decoration: none;
    font-weight: bold;
    letter-spacing: 1px;
    box-shadow: 0 0 30px rgba(0, 229, 255, 0.5);
    transition: all 0.3s ease;
    font-size: 15px;
}
.bot-btn:hover {
    box-shadow: 0 0 45px rgba(0, 229, 255, 0.9);
    transform: scale(1.05);
}
.bot-btn-wrap { text-align: center; margin: 32px 0 16px 0; }
.disclaimer {
    text-align: center;
    color: #4a5b73;
    font-size: 11px;
    margin-top: 24px;
    line-height: 1.6;
    max-width: 700px;
    margin-left: auto;
    margin-right: auto;
}
.footer-brand {
    text-align: center;
    color: #00b3ff;
    font-size: 12px;
    letter-spacing: 3px;
    margin-top: 8px;
    text-transform: uppercase;
    text-shadow: 0 0 15px rgba(0, 179, 255, 0.5);
}
@media (max-width: 600px) {
    .card { min-width: 110px; padding: 12px 14px; }
    .card .value { font-size: 20px; }
    th, td { padding: 8px 8px; font-size: 12px; }
    h1 { font-size: 17px; }
    .logo-wrap img { max-width: 170px; }
}
</style>
</head>
<body>

<div class="logo-wrap">
    <img src="__LOGO__" alt="__BRAND__">
</div>

<h1>__BRAND__ — Статистика</h1>

<div class="cards">
<div class="card"><div class="label">Баланс</div><div class="value">__BALANCE__ ₽</div></div>
<div class="card"><div class="label">Профит</div><div class="value __PROFIT_CLASS__">__PROFIT__ ₽</div></div>
<div class="card"><div class="label">Winrate</div><div class="value">__WINRATE__%</div></div>
<div class="card"><div class="label">ROI</div><div class="value">__ROI__%</div></div>
<div class="card"><div class="label">Всего</div><div class="value">__TOTAL__</div></div>
<div class="card"><div class="label">Плюсы</div><div class="value win">__WIN__</div></div>
<div class="card"><div class="label">Минусы</div><div class="value lose">__LOSE__</div></div>
<div class="card"><div class="label">В ожидании</div><div class="value pending">__PENDING__</div></div>
<div class="card"><div class="label">Текущая ставка</div><div class="value">__CURRENT_BET__ ₽</div></div>
</div>

<h2>🎯 По догонам</h2>
<table>
<tr><th>Догон</th><th>Сыграно</th><th>Плюсов</th><th>Winrate</th></tr>
__DOGON_ROWS__
</table>

<h2>🃏 По мастям</h2>
<table>
<tr><th>Масть</th><th>Всего</th><th>Плюсов</th><th>Winrate</th></tr>
__SUIT_ROWS__
</table>

<h2>📅 По дням</h2>
<table>
<tr><th>Дата</th><th>Всего</th><th>Плюсов</th><th>Минусов</th><th>Winrate</th></tr>
__DAY_ROWS__
</table>

<h2>🕐 Последние 30 прогнозов</h2>
<table>
<tr><th></th><th>Игра</th><th>Масть</th><th>Ставка</th><th>Догон</th><th>Статус</th><th>Создан</th></tr>
__ROWS__
</table>

<div class="bot-btn-wrap">
    <a href="__BOT_LINK__" class="bot-btn" target="_blank">
        🤖 ОТКРЫТЬ БОТА
    </a>
</div>

<div class="updated">Обновлено: __UPDATED__ (МСК)</div>

<div class="disclaimer">
    ⚠️ Ставки — это риск. Прошлые результаты не гарантируют будущую прибыль. Играйте ответственно.
</div>

<div class="footer-brand">__BRAND__</div>

</body>
</html>"""

    html = html.replace("__BRAND__", BRAND_NAME)
    html = html.replace("__LOGO__", LOGO_URL)
    html = html.replace("__BOT_LINK__", BOT_LINK)
    html = html.replace("__BALANCE__", f"{balance:.0f}")
    html = html.replace("__PROFIT__", f"{profit:+.0f}")
    html = html.replace("__PROFIT_CLASS__", profit_class)
    html = html.replace("__WINRATE__", str(winrate))
    html = html.replace("__ROI__", str(roi))
    html = html.replace("__TOTAL__", str(total))
    html = html.replace("__WIN__", str(win))
    html = html.replace("__LOSE__", str(lose))
    html = html.replace("__PENDING__", str(pending))
    html = html.replace("__CURRENT_BET__", f"{current_bet:.0f}")
    html = html.replace("__DOGON_ROWS__", dogon_rows or '<tr><td colspan="4">Нет данных</td></tr>')
    html = html.replace("__SUIT_ROWS__", suit_rows or '<tr><td colspan="4">Нет данных</td></tr>')
    html = html.replace("__DAY_ROWS__", day_rows or '<tr><td colspan="5">Нет данных</td></tr>')
    html = html.replace("__ROWS__", rows_html)
    html = html.replace("__UPDATED__", updated)

    try:
        with open(STATS_HTML_FILE, "w", encoding="utf-8") as f:
            f.write(html)

        print(
            f"📊 Статистика: {total} прогнозов, "
            f"банк {balance:.0f} ₽, профит {profit:+.0f} ₽, "
            f"winrate {winrate}%, ROI {roi}%, "
            f"ставка {current_bet:.0f} ₽ (Д{current_step})",
            flush=True,
        )
    except Exception as e:
        print(f"⚠️ Ошибка генерации статистики: {e}", flush=True)