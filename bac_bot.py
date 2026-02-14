# -*- coding: utf-8 -*-
import logging
import re
import random
import asyncio
from datetime import datetime
from collections import defaultdict
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    filters,
    ContextTypes,
    CommandHandler
)

# === НАСТРОЙКИ ===
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"
INPUT_CHANNEL_ID = -1003469691743
OUTPUT_CHANNEL_ID = -1003842401391
ADMIN_ID = 683219603

MAX_GAME_NUMBER = 1440

FUNNY_PHRASES = [
    "🎰 ВА-БАНК! ОБНАРУЖЕН СУПЕР ПАТТЕРН! 🎰",
    "🚀 РАКЕТА ЗАПУЩЕНА! ЛЕТИМ ЗА ПОБЕДОЙ! 🚀",
    "💎 АЛМАЗНЫЙ СИГНАЛ ПРИЛЕТЕЛ! 💎",
    "🎯 СНАЙПЕР В ЦЕЛИ! ТОЧНЫЙ РАСЧЕТ! 🎯",
    "🔥 ГОРИМ ЖЕЛАНИЕМ ПОБЕДИТЬ! 🔥"
]

WIN_PHRASES = [
    "🎉 УРА! СТРАТЕГИЯ СРАБОТАЛА! 🎉",
    "💰 КАЗИНО В ШОКЕ! МЫ ВЫИГРАЛИ! 💰",
    "🥇 ЗОЛОТАЯ ПОБЕДА! ТОЧНО В ЦЕЛЬ! 🥇",
    "🏅 ОЛИМПИЙСКАЯ ТОЧНОСТЬ! ПОБЕДА! 🏅",
    "🎯 БИНГО! ПОПАДАНИЕ В ЯБЛОЧКО! 🎯"
]

LOSS_PHRASES = [
    "😔 УВЫ, НЕ СЕГОДНЯ...",
    "🌧️ НЕБО ПЛАЧЕТ, И МЫ ТОЖЕ...",
    "🍀 НЕ ПОВЕЗЛО В ЭТОТ РАЗ...",
    "🎭 ДРАМА... НО МЫ НЕ СДАЕМСЯ!",
    "🤡 ЦИРК ВЕРНУЛСЯ... ШУТКА НЕ УДАЛАСЬ"
]

SUITS = ["♥️", "♠️", "♣️", "♦️"]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

pending_games = {}
prediction_messages = {}

# ========== НОВОЕ: Статистика замен ==========
card_stats = defaultdict(lambda: defaultdict(int))  # card_value -> suit -> count

class UniversalGameParser:
    @staticmethod
    def extract_game_data(text: str):
        logger.info(f"🔍 Парсим: {text[:150]}...")
        
        match = re.search(r'#N(\d+)', text)
        if not match:
            return None
        
        game_num = int(match.group(1))
        has_r_tag = '#R' in text
        has_x_tag = '#X' in text or '#X🟡' in text
        has_check = '✅' in text
        has_t = re.search(r'#T\d+', text) is not None
        
        is_completed = has_r_tag or has_x_tag or has_check or has_t
        
        left_part = UniversalGameParser._extract_left_part(text)
        
        left_result, cards_text, left_suits = UniversalGameParser._parse_all_cards(left_part)
        
        if left_result is None:
            left_result, cards_text, left_suits = UniversalGameParser._parse_whole_text(text)
        
        if left_result is not None and left_suits:
            # Пытаемся определить карту, которая привела к этому прогнозу
            # Ищем цифру в конце левой части (результат игры)
            card_value_match = re.search(r'(\d+)$', str(left_result))
            card_value = card_value_match.group(1) if card_value_match else None
            
            initial_cards = left_suits[:2] if len(left_suits) >= 2 else left_suits
            drawn_card = left_suits[2] if len(left_suits) == 3 else None
            
            logger.info(f"✅ Игра #{game_num} ЗАВЕРШЕНА, карта-причина: {card_value}")
            
            game_data = {
                'game_num': game_num,
                'has_r_tag': has_r_tag,
                'has_x_tag': has_x_tag,
                'has_check': has_check,
                'has_t': has_t,
                'is_deal': has_r_tag,
                'left_result': left_result,
                'left_cards_count': len(left_suits),
                'left_suits': left_suits,
                'initial_cards': initial_cards,
                'drawn_card': drawn_card,
                'has_drawn': len(left_suits) == 3,
                'original_text': text,
                'is_completed': True,
                'card_value': card_value  # запоминаем, какая карта вызвала игру
            }
            
            return game_data
        
        return None
    
    @staticmethod
    def _extract_left_part(text: str) -> str:
        separators = [
            ' 🔰 ', '🔰',
            ' - ', ' – ', ' — ',
            ' 👉👈 ', ' 👈👉 ', '👉👈', '👈👉',
            ' | ', ' |', '| ',
            ' : ', ' :', ': ',
            ';', ' ;', '; '
        ]
        
        for sep in separators:
            if sep in text:
                parts = text.split(sep, 1)
                if len(parts) > 1:
                    return parts[0].strip()
        
        return text.strip()
    
    @staticmethod
    def _parse_all_cards(left_text: str):
        left_result = None
        cards_text = ""
        suits = []
        
        bracket_pattern = r'(\d+)\(([^)]+)\)'
        bracket_match = re.search(bracket_pattern, left_text)
        
        if bracket_match:
            left_result = int(bracket_match.group(1))
            cards_text = bracket_match.group(2)
            suits = UniversalGameParser._extract_all_suits(cards_text)
        else:
            num_match = re.search(r'\b(\d+)\b', left_text)
            if num_match:
                left_result = int(num_match.group(1))
                after_num = left_text[num_match.end():]
                suits = UniversalGameParser._extract_all_suits(after_num)
        
        return left_result, cards_text, suits
    
    @staticmethod
    def _parse_whole_text(text: str):
        left_result = None
        cards_text = ""
        suits = []
        
        clean_text = text.replace('🔰', ' ').replace('✅', ' ').replace('🟡', ' ')
        
        num_match = re.search(r'\b(\d+)\b', clean_text)
        if num_match:
            left_result = int(num_match.group(1))
            
            card_search = re.search(r'\(([^)]+)\)', text)
            if card_search:
                cards_text = card_search.group(1)
                suits = UniversalGameParser._extract_all_suits(cards_text)
            else:
                suits = UniversalGameParser._extract_all_suits(text)
        
        return left_result, cards_text, suits
    
    @staticmethod
    def _extract_all_suits(text: str):
        suits = []
        
        suit_patterns = {
            '♥️': r'[♥❤♡\u2665]',
            '♠️': r'[♠♤\u2660]',
            '♣️': r'[♣♧\u2663]',
            '♦️': r'[♦♢\u2666]'
        }
        
        for suit_emoji, pattern in suit_patterns.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            for _ in matches:
                suits.append(suit_emoji)
        
        return suits

def get_next_game_number(current_game, increment=1):
    next_game = current_game + increment
    while next_game > MAX_GAME_NUMBER:
