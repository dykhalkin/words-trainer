#!/bin/zsh
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd /Users/misha/work/words-trainer
env_file="${WORDSBOT_ENV_FILE:-$HOME/.config/wordsbot/env}"
test -f "$env_file"
permissions="$(stat -f '%Lp' "$env_file")"
[[ "$permissions" == "600" ]]
docker compose ps --status running postgres
UV_CACHE_DIR=/private/tmp/words-trainer-uv-cache uv run python cli.py stats >/dev/null
pmset -g custom | awk '$1 == "sleep" && $2 == 0 { found = 1 } END { exit !found }'
echo "wordsbot operational checks passed"
