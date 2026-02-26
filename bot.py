def check_banker_combo(self, banker_cards):
    """Проверяет комбинацию банкира"""
    if len(banker_cards) != 2:
        return False, None
    
    card1, card2 = banker_cards
    
    # НОВОЕ ПРАВИЛО: если масти одинаковые - пропускаем
    if card1['suit'] == card2['suit']:
        logger.info(f"⏭️ Пропускаем: обе карты банкира одной масти {card1['suit']}")
        return False, None
    
    # Дальше обычная проверка...
    if (card1['value'] in self.picture_values and card2['value'] in self.number_values):
        return True, card2['suit']
    elif (card2['value'] in self.picture_values and card1['value'] in self.number_values):
        return True, card1['suit']
    
    return False, None