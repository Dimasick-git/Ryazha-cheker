#!/usr/bin/env python3
"""
Тестовый скрипт для проверки работы Telegram API
"""

import os
import requests
import sys

def test_telegram():
    """Тестирует отправку сообщения в Telegram"""
    
    # Получаем переменные окружения
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    
    print("=== Telegram API Test ===")
    print(f"Bot Token: {bot_token[:10] if bot_token else 'None'}...")
    print(f"Chat ID: {chat_id}")
    
    if not bot_token:
        print("❌ ERROR: TELEGRAM_BOT_TOKEN not found")
        return False
    
    if not chat_id:
        print("❌ ERROR: TELEGRAM_CHAT_ID not found")
        return False
    
    # Тест 1: Проверка информации о боте
    print("\n1. Testing bot info...")
    try:
        url = f"https://api.telegram.org/bot{bot_token}/getMe"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                bot_info = data['result']
                print(f"✅ Bot info: {bot_info['username']} (@{bot_info['username']})")
                print(f"   Bot name: {bot_info['first_name']}")
            else:
                print(f"❌ Bot API error: {data.get('description')}")
                return False
        else:
            print(f"❌ HTTP error: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Exception getting bot info: {e}")
        return False
    
    # Тест 2: Проверка отправки сообщения
    print("\n2. Testing message sending...")
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        
        # Простое текстовое сообщение без Markdown
        message = "🧪 Test message from GitHub Monitor\n\nIf you see this, Telegram integration works!"
        
        data = {
            'chat_id': chat_id,
            'text': message
        }
        
        print(f"Sending to chat_id: {chat_id}")
        print(f"Message: {message}")
        
        response = requests.post(url, json=data, timeout=30)
        
        print(f"Response status: {response.status_code}")
        print(f"Response: {response.text}")
        
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                print("✅ Message sent successfully!")
                return True
            else:
                print(f"❌ Telegram API error: {result.get('description')}")
                return False
        else:
            print(f"❌ HTTP error: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Exception sending message: {e}")
        return False

if __name__ == "__main__":
    print("Starting Telegram test...")
    
    success = test_telegram()
    
    if success:
        print("\n🎉 Telegram test PASSED!")
        sys.exit(0)
    else:
        print("\n💥 Telegram test FAILED!")
        sys.exit(1)
