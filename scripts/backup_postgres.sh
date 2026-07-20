#!/bin/zsh
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd /Users/misha/work/words-trainer
env_file="${WORDSBOT_ENV_FILE:-$HOME/.config/wordsbot/env}"
set -a
source "$env_file"
set +a
backup_dir="${WORDSBOT_BACKUP_DIR:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/Wordsbot Backups}"
mkdir -p "$backup_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$backup_dir/words_trainer-$stamp.dump"

docker compose exec -T postgres pg_dump \
  --username "${POSTGRES_USER:-words_trainer}" \
  --dbname "${POSTGRES_DB:-words_trainer}" \
  --format custom > "$target"
chmod 600 "$target"
find "$backup_dir" -type f -name 'words_trainer-*.dump' -mtime +14 -delete
test -s "$target"
echo "$target"
