# -*- coding: utf-8 -*-
import logging
import re
import os
import sys
import csv
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    filters,
    ContextTypes
)
import pytz

# ======== НАСТРОЙКИ ========
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"  # Вставь токен свободного бота
INPUT_CHANNEL_ID = -1003855079501  # Канал со статистикой 21 Classic
OUTPUT_FILE = "value_statistics.csv"  # Файл для сохранения данных
# ===========================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Маппинг значений (для читаемости)
VALUE_NAMES = {
    '2': '2', '3': '3', '4': '4', '5': '5', '6': '6', '7': '7', '8': '8', '9': '9', '10': '10',
    'J': 'J', 'Q': 'Q', 'K': 'K', 'A': 'A'
}

def normalize_suit(s):
    if not s:
        return None
    s = str(s).strip()
    if s in ('♥', '❤', '♡', '♥️'):
        return '♥️'
    if s in ('♠', '♤', '♠️'):
        return '♠️'
    if s in ('♣', '♧', '♣️'):
        return '♣️'
    if s in ('♦', '♢', '♦️'):
        return '♦️'
    return None

def parse_game_values(text):
    """
    Парсит игру и возвращает номер игры и список значений
    """
    match = re.search(r'#N(\d+)', text)
    if not match:
        return None, None
    
    game_num = int(match.group(1))
    
    # Определяем, завершена ли игра (нужны только завершённые)
    is_complete = '☑️' in text or '#П1' in text or '#П2' in text or '#НИЧЬЯ' in text
    if not is_complete:
        return None, None
    
    # Убираем служебные символы
    clean_text = re.sub(r'#N\d+\s*', '', text)
    clean_text = re.sub(r'#П[12]|#НИЧЬЯ|#T\d+', '', clean_text)
    clean_text = clean_text.replace('☑️', '').replace('✅', '').replace('🟩', '').replace('🔰', '')
    
    # Находим все карты в формате "значение+масть"
    card_pattern = r'(\d+|J|Q|K|A)\s*([♥️♦️♠️♣️])'
    all_cards = re.findall(card_pattern, clean_text)
    
    # Извлекаем только значения
    values = []
    for card in all_cards:
        value = card[0]
        # Приводим к стандартному виду
        if value in VALUE_NAMES:
            values.append(VALUE_NAMES[value])
    
    # Убираем дубликаты (если карта повторяется, но порядок сохраняем)
    # Можно оставить как есть, но для статистики лучше уникальные
    # values = list(dict.fromkeys(values))  # раскомментируй, если нужны уникальные
    
    return game_num, values

def save_to_csv(game_num, values):
    """Сохраняет данные в CSV-файл"""
    file_exists = os.path.isfile(OUTPUT_FILE)
    
    with open(OUTPUT_FILE, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        
        # Если файл новый, пишем заголовок
        if not file_exists:
            writer.writerow(['game_num', 'values', 'timestamp'])
        
        # Пишем данные
        writer.writerow([
            game_num,
            ','.join(values),  # значения через запятую
            datetime.now(pytz.timezone('Europe/Moscow')).isoformat()
        ])
    
    logger.info(f"💾 Сохранена игра #{game_num}: {values}")

async def handle_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает новую игру и сохраняет значения"""
    try:
        if update.channel_post:
            message = update.channel_post
        elif update.edited_channel_post:
            message = update.edited_channel_post
        else:
            return
        
        text = message.text
        if not text:
            return
        
        # Парсим игру
        game_num, values = parse_game_values(text)
        
        if game_num and values:
            # Сохраняем в CSV
            save_to_csv(game_num, values)
            
            # Логируем для наглядности
            logger.info(f"📊 Игра #{game_num}: значения {values}")
    
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}", exc_info=True)

def main():
    print("\n" + "="*60)
    print("📊 БОТ-СБОРЩИК ЗНАЧЕНИЙ (21 CLASSIC)")
    print("="*60)
    print(f"📥 Канал: {INPUT_CHANNEL_ID}")
    print(f"📁 Выходной файл: {OUTPUT_FILE}")
    print("="*60 + "\n")
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(MessageHandler(
        filters.Chat(INPUT_CHANNEL_ID) & filters.TEXT,
        handle_game
    ))
    
    try:
        app.run_polling(
            allowed_updates=['channel_post', 'edited_channel_post'],
            drop_pending_updates=True
        )
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")

if __name__ == "__main__":
    main()