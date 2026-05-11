# GitHub Repository Monitor

Автоматический мониторинг всех ваших GitHub репозиториев с отправкой уведомлений в Telegram каждые 30 минут.

## 🚀 Возможности

- **Автоматический мониторинг**: Отслеживает все репозитории пользователя GitHub
- **Уведомления в Telegram**: Отправляет подробные отчеты каждые 30 минут
- **Информация о коммитах**: Показывает последние коммиты в каждом репозитории
- **Статус Pull Requests**: Отслеживает открытые PRs
- **Статистика**: Общая информация о звездах, форках и активности
- **GitHub Actions**: Полностью автоматизированный процесс

## 📋 Установка и настройка

### 1. Создание Telegram бота

1. Найдите в Telegram бота [@BotFather](https://t.me/botfather)
2. Отправьте команду `/newbot`
3. Следуйте инструкциям для создания нового бота
4. Сохраните **токен бота** (выглядит как `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`)

### 2. Получение вашего Chat ID

1. Найдите в Telegram бота [@userinfobot](https://t.me/userinfobot)
2. Отправьте ему любое сообщение
3. Бот пришлет ваш **Chat ID** (число)

### 3. Создание GitHub Personal Access Token

1. Перейдите в [GitHub Settings > Developer settings > Personal access tokens](https://github.com/settings/tokens)
2. Нажмите "Generate new token"
3. Дайте имя токену (например, "Repository Monitor")
4. Выберите срок действия
5. Установите галочки:
   - ✅ `repo` (полный доступ к репозиториям)
   - ✅ `read:org` (если у вас есть организации)
6. Нажмите "Generate token"
7. **Скопируйте и сохраните токен** (он больше не будет показан)

### 4. Настройка репозитория

1. Создайте новый репозиторий на GitHub с названием `Ryazha-ckeker`
2. Склонируйте репозиторий или загрузите в него файлы из этого проекта
3. Перейдите в настройки репозитория: `Settings` → `Secrets and variables` → `Actions`

### 5. Добавление секретов в GitHub

В разделе "Repository secrets" добавьте следующие секреты:

| Имя секрета | Значение |
|-------------|----------|
| `GITHUB_TOKEN` | Ваш Personal Access Token |
| `TELEGRAM_BOT_TOKEN` | Токен вашего Telegram бота |
| `TELEGRAM_CHAT_ID` | Ваш Chat ID в Telegram |
| `GITHUB_USERNAME` | `Dimasick-git` (ваш GitHub username) |

## 🏃‍♂️ Запуск

### Автоматический запуск

После настройки секретов GitHub Actions будет автоматически запускать мониторинг каждые 30 минут.

### Ручной запуск для тестирования

1. Перейдите в раздел `Actions` вашего репозитория
2. Выберите workflow "GitHub Repository Monitor"
3. Нажмите "Run workflow"

## 📊 Пример сообщения в Telegram

```
🔍 GitHub Repository Monitor Report
👤 User: Dimasick-git
📅 2024-01-15 14:30:00 UTC

📊 Summary:
• Total repositories: 15
• ⭐ Total stars: 142
• 🍴 Total forks: 28

🚀 Recently Active Repositories:

📁 my-awesome-project
📝 A cool project for learning
🌟 25 ⭐ | 🍴 8 🍴 | 💻 Python
📝 Recent commits:
  • `a1b2c3d` Fix authentication bug (John Doe, 01-15 14:15)
  • `e4f5g6h` Add new features (Jane Smith, 01-15 13:45)

🔄 Repositories with Open PRs:

📁 another-repo - 2 open PRs:
  • #42 Add new functionality (by contributor)
  • #41 Fix critical bug (by maintainer)
```

## ⚙️ Настройка

### Изменение интервала проверки

Откройте файл `.github/workflows/monitor.yml` и измените расписание:

```yaml
schedule:
  # Каждые 30 минут
  - cron: '*/30 * * * *'
  
  # Каждый час
  # - cron: '0 * * * *'
  
  # Каждые 2 часа
  # - cron: '0 */2 * * *'
```

### Кастомизация сообщения

Отредактируйте метод `format_telegram_message()` в файле `main.py` для изменения формата сообщений.

## 🔧 Локальный запуск

Для тестирования можно запустить скрипт локально:

```bash
# Установка зависимостей
pip install -r requirements.txt

# Установка переменных окружения
export GITHUB_TOKEN="your_token_here"
export TELEGRAM_BOT_TOKEN="your_bot_token_here"
export TELEGRAM_CHAT_ID="your_chat_id_here"
export GITHUB_USERNAME="Dimasick-git"

# Запуск
python main.py
```

## 🚨 Важные замечания

- GitHub Actions имеет лимиты на бесплатных аккаунтах (2000 минут в месяц)
- Мониторинг каждые 30 минут потребляет ~1440 минут в месяц
- Убедитесь, что ваш токен имеет необходимые права доступа
- Храните все токены и секреты в безопасности

## 📝 Лицензия

MIT License - свободно использовать и модифицировать.

## 🤝 Поддержка

Если возникли проблемы:
1. Проверьте правильность всех секретов в GitHub
2. Убедитесь, что Telegram бот работает (отправьте ему сообщение)
3. Проверьте логи в GitHub Actions для диагностики проблем

---

**Создано с ❤️ для автоматического мониторинга GitHub репозиториев**
