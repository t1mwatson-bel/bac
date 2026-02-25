# ai_train.py
import sqlite3
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
import xgboost as xgb
import joblib
import os
from datetime import datetime

DB_FILE = 'bot3_stats.db'
MODEL_FILE = 'ai_model.pkl'
ENCODER_FILE = 'suit_encoder.pkl'

def prepare_features(conn):
    """Подготовка признаков из базы данных"""
    
    # Загружаем игры
    games_df = pd.read_sql_query('''
        SELECT game_num, left_suits, right_suits, has_r, has_x, is_tie 
        FROM games 
        ORDER BY game_num
    ''', conn)
    
    # Загружаем прогнозы с результатами
    preds_df = pd.read_sql_query('''
        SELECT pred_id, source_game, target_game, suit, result, attempt 
        FROM predictions 
        WHERE result IS NOT NULL
    ''', conn)
    
    if len(preds_df) < 50:
        print(f"⚠️ Мало данных для обучения: {len(preds_df)} прогнозов. Нужно минимум 50.")
        return None, None
    
    # Создаём признаки для каждого прогноза
    X = []
    y = []
    feature_names = []
    
    # Кодировщик для мастей
    le = LabelEncoder()
    all_suits = ['♥️', '♠️', '♣️', '♦️']
    le.fit(all_suits)
    joblib.dump(le, ENCODER_FILE)
    
    for _, pred in preds_df.iterrows():
        features = []
        
        # Находим исходную игру
        source_game = games_df[games_df['game_num'] == pred['source_game']]
        if len(source_game) == 0:
            continue
            
        # Признак 1: масть в исходной игре
        if source_game.iloc[0]['right_suits']:
            right_suits = source_game.iloc[0]['right_suits'].split(',')
            if len(right_suits) > 0:
                features.append(le.transform([right_suits[0]])[0])
            else:
                features.append(-1)
        else:
            features.append(-1)
        
        # Признак 2: был ли #R в исходной игре
        features.append(source_game.iloc[0]['has_r'])
        
        # Признак 3: был ли #X
        features.append(source_game.iloc[0]['has_x'])
        
        # Признак 4: ничья?
        features.append(source_game.iloc[0]['is_tie'])
        
        # Признак 5: номер попытки
        features.append(pred['attempt'])
        
        # Признак 6: целевая масть (которую предсказываем)
        target_suit = pred['suit']
        features.append(le.transform([target_suit])[0])
        
        X.append(features)
        
        # Целевая переменная: зашёл (1) или нет (0)
        y.append(1 if pred['result'] == 'win' else 0)
    
    feature_names = [
        'source_suit', 'has_r_source', 'has_x_source', 
        'is_tie_source', 'attempt_num', 'target_suit'
    ]
    
    return np.array(X), np.array(y), feature_names, le

def train_model():
    """Основная функция обучения"""
    print(f"\n{'='*50}")
    print("🤖 AI ОБУЧЕНИЕ БОТА 3")
    print(f"{'='*50}")
    
    if not os.path.exists(DB_FILE):
        print("❌ База данных не найдена")
        return
    
    conn = sqlite3.connect(DB_FILE)
    
    # Подготовка данных
    X, y, feature_names, le = prepare_features(conn)
    conn.close()
    
    if X is None:
        return
    
    print(f"\n📊 Данных для обучения: {len(X)} прогнозов")
    print(f"   Признаков: {len(feature_names)}")
    
    # Разделение на обучающую и тестовую выборки
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    
    # Обучение модели XGBoost
    model = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=5,
        learning_rate=0.1,
        random_state=42,
        use_label_encoder=False,
        eval_metric='logloss'
    )
    
    model.fit(X_train, y_train)
    
    # Оценка качества
    train_score = model.score(X_train, y_train)
    test_score = model.score(X_test, y_test)
    
    print(f"\n📈 Качество модели:")
    print(f"   Train accuracy: {train_score:.3f}")
    print(f"   Test accuracy:  {test_score:.3f}")
    
    # Важность признаков
    importance = model.feature_importances_
    print(f"\n🔍 Важность признаков:")
    for name, imp in sorted(zip(feature_names, importance), key=lambda x: -x[1]):
        print(f"   {name}: {imp:.3f}")
    
    # Сохранение модели
    joblib.dump(model, MODEL_FILE)
    print(f"\n✅ Модель сохранена в {MODEL_FILE}")
    
    # Сохраняем метаданные
    metadata = {
        'train_date': datetime.now().isoformat(),
        'train_samples': len(X),
        'train_accuracy': train_score,
        'test_accuracy': test_score,
        'feature_names': feature_names
    }
    joblib.dump(metadata, 'ai_metadata.pkl')
    print(f"✅ Метаданные сохранены")

if __name__ == "__main__":
    train_model()