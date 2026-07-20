#!/bin/zsh
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd /Users/misha/work/words-trainer
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/wordsbot"
env_file="${WORDSBOT_ENV_FILE:-$HOME/.config/wordsbot/env}"
test "$(stat -f '%Lp' "$env_file")" = "600"
set -a
source "$env_file"
set +a
uv sync --frozen
brew services start colima
docker compose up -d
UV_CACHE_DIR=/private/tmp/words-trainer-uv-cache uv run python cli.py migrate
UV_CACHE_DIR=/private/tmp/words-trainer-uv-cache uv run python cli.py user bootstrap
UV_CACHE_DIR=/private/tmp/words-trainer-uv-cache uv run python cli.py sync
install -m 600 deploy/com.wordsbot.bot.plist "$HOME/Library/LaunchAgents/"
install -m 600 deploy/com.wordsbot.backup.plist "$HOME/Library/LaunchAgents/"
launchctl bootout "gui/$UID/com.wordsbot.bot" 2>/dev/null || true
launchctl bootout "gui/$UID/com.wordsbot.backup" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$HOME/Library/LaunchAgents/com.wordsbot.bot.plist"
launchctl bootstrap "gui/$UID" "$HOME/Library/LaunchAgents/com.wordsbot.backup.plist"
