#!/usr/bin/env python3
"""
GitHub Repository Monitor
Отслеживает состояние всех репозиториев пользователя и отправляет уведомления в Telegram
"""

import os
import requests
import json
from datetime import datetime, timezone
from typing import List, Dict, Any

class GitHubMonitor:
    def __init__(self):
        self.github_token = os.getenv('GITHUB_TOKEN')
        self.telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        self.github_username = os.getenv('GITHUB_USERNAME', 'Dimasick-git')
        
        if not all([self.github_token, self.telegram_bot_token, self.telegram_chat_id]):
            raise ValueError("Missing required environment variables")
    
    def get_user_repositories(self) -> List[Dict[str, Any]]:
        """Получает список всех репозиториев пользователя"""
        url = f"https://api.github.com/users/{self.github_username}/repos"
        headers = {
            'Authorization': f'token {self.github_token}',
            'Accept': 'application/vnd.github.v3+json'
        }
        
        all_repos = []
        page = 1
        
        while True:
            params = {'page': page, 'per_page': 100, 'type': 'all'}
            response = requests.get(url, headers=headers, params=params)
            
            if response.status_code != 200:
                print(f"Error fetching repositories: {response.status_code}")
                break
            
            repos = response.json()
            if not repos:
                break
            
            all_repos.extend(repos)
            page += 1
        
        return all_repos
    
    def get_repository_activity(self, repo_name: str) -> Dict[str, Any]:
        """Получает информацию о последней активности в репозитории"""
        url = f"https://api.github.com/repos/{self.github_username}/{repo_name}"
        headers = {
            'Authorization': f'token {self.github_token}',
            'Accept': 'application/vnd.github.v3+json'
        }
        
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            return {'error': f"Failed to fetch repo info: {response.status_code}"}
        
        repo_data = response.json()
        
        # Получаем последние коммиты
        commits_url = f"https://api.github.com/repos/{self.github_username}/{repo_name}/commits"
        commits_response = requests.get(commits_url, headers=headers, params={'per_page': 5})
        
        recent_commits = []
        if commits_response.status_code == 200:
            commits = commits_response.json()
            for commit in commits[:3]:  # Берем последние 3 коммита
                recent_commits.append({
                    'sha': commit['sha'][:7],
                    'message': commit['commit']['message'].split('\n')[0][:50],
                    'author': commit['commit']['author']['name'],
                    'date': commit['commit']['author']['date']
                })
        
        # Получаем информацию о PRs и issues
        pulls_url = f"https://api.github.com/repos/{self.github_username}/{repo_name}/pulls"
        pulls_response = requests.get(pulls_url, headers=headers, params={'state': 'open', 'per_page': 5})
        
        open_prs = []
        if pulls_response.status_code == 200:
            pulls = pulls_response.json()
            for pr in pulls[:3]:  # Берем последние 3 PR
                open_prs.append({
                    'number': pr['number'],
                    'title': pr['title'][:50],
                    'author': pr['user']['login'],
                    'created_at': pr['created_at']
                })
        
        return {
            'name': repo_name,
            'description': repo_data.get('description', 'No description'),
            'updated_at': repo_data['updated_at'],
            'pushed_at': repo_data['pushed_at'],
            'stars': repo_data['stargazers_count'],
            'forks': repo_data['forks_count'],
            'language': repo_data.get('language', 'Unknown'),
            'private': repo_data['private'],
            'recent_commits': recent_commits,
            'open_prs': open_prs
        }
    
    def format_telegram_message(self, repos_data: List[Dict[str, Any]]) -> str:
        """Форматирует сообщение для Telegram"""
        message = f"🔍 **GitHub Repository Monitor Report**\n"
        message += f"👤 User: {self.github_username}\n"
        message += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
        
        total_repos = len(repos_data)
        total_stars = sum(repo.get('stars', 0) for repo in repos_data)
        total_forks = sum(repo.get('forks', 0) for repo in repos_data)
        
        message += f"📊 **Summary:**\n"
        message += f"• Total repositories: {total_repos}\n"
        message += f"• ⭐ Total stars: {total_stars}\n"
        message += f"• 🍴 Total forks: {total_forks}\n\n"
        
        # Показываем репозитории с недавней активностью
        active_repos = [repo for repo in repos_data if repo.get('recent_commits')]
        
        if active_repos:
            message += "🚀 **Recently Active Repositories:**\n\n"
            
            for repo in active_repos[:5]:  # Показываем до 5 активных репозиториев
                message += f"📁 **{repo['name']}**\n"
                if repo['description'] != 'No description':
                    message += f"📝 {repo['description']}\n"
                
                message += f"🌟 {repo['stars']} ⭐ | 🍴 {repo['forks']} 🍴 | 💻 {repo['language']}\n"
                
                # Последние коммиты
                if repo['recent_commits']:
                    message += "📝 Recent commits:\n"
                    for commit in repo['recent_commits'][:2]:
                        commit_time = datetime.fromisoformat(commit['date'].replace('Z', '+00:00')).strftime('%m-%d %H:%M')
                        message += f"  • `{commit['sha']}` {commit['message']} ({commit['author']}, {commit_time})\n"
                
                # Open PRs
                if repo['open_prs']:
                    message += f"🔄 Open PRs: {len(repo['open_prs'])}\n"
                
                message += "\n"
        
        # Показываем репозитории с открытыми PRs
        repos_with_prs = [repo for repo in repos_data if repo.get('open_prs')]
        if repos_with_prs:
            message += "🔄 **Repositories with Open PRs:**\n\n"
            for repo in repos_with_prs[:3]:  # Показываем до 3 репозиториев с PRs
                message += f"📁 **{repo['name']}** - {len(repo['open_prs'])} open PRs:\n"
                for pr in repo['open_prs'][:2]:
                    message += f"  • #{pr['number']} {pr['title']} (by {pr['author']})\n"
                message += "\n"
        
        return message
    
    def send_telegram_message(self, message: str) -> bool:
        """Отправляет сообщение в Telegram"""
        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
        
        data = {
            'chat_id': self.telegram_chat_id,
            'text': message,
            'parse_mode': 'Markdown'
        }
        
        try:
            response = requests.post(url, json=data)
            if response.status_code == 200:
                print("Message sent successfully to Telegram")
                return True
            else:
                print(f"Failed to send message: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"Error sending Telegram message: {e}")
            return False
    
    def run_monitor(self):
        """Основной метод мониторинга"""
        try:
            print(f"Starting GitHub monitor for user: {self.github_username}")
            
            # Получаем все репозитории
            repositories = self.get_user_repositories()
            print(f"Found {len(repositories)} repositories")
            
            # Собираем информацию о каждом репозитории
            repos_data = []
            for repo in repositories:
                repo_name = repo['name']
                print(f"Processing repository: {repo_name}")
                
                repo_info = self.get_repository_activity(repo_name)
                repos_data.append(repo_info)
            
            # Сортируем по времени последнего обновления
            repos_data.sort(key=lambda x: x.get('pushed_at', ''), reverse=True)
            
            # Формируем и отправляем сообщение
            message = self.format_telegram_message(repos_data)
            
            success = self.send_telegram_message(message)
            
            if success:
                print("Monitor completed successfully")
            else:
                print("Monitor completed with errors")
                
        except Exception as e:
            error_message = f"❌ Error in GitHub monitor: {str(e)}"
            print(error_message)
            self.send_telegram_message(error_message)

if __name__ == "__main__":
    monitor = GitHubMonitor()
    monitor.run_monitor()
