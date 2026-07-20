#!/bin/zsh
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

if (( $# != 1 )); then
  echo "usage: $0 BACKUP.dump" >&2
  exit 2
fi
backup="$1"
test -s "$backup"
cd /Users/misha/work/words-trainer

echo "Restore replaces the words_trainer database. Type RESTORE to continue:" >&2
read -r confirmation
[[ "$confirmation" == "RESTORE" ]]
docker compose exec -T postgres dropdb --if-exists --username words_trainer words_trainer
docker compose exec -T postgres createdb --username words_trainer words_trainer
docker compose exec -T postgres pg_restore \
  --username words_trainer --dbname words_trainer --clean --if-exists < "$backup"
