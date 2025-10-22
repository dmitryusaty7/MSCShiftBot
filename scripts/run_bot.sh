#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if [ -f .env ]; then
  echo "Загружаю переменные из .env"
  set -a
  source .env
  set +a
fi

python bot_shift.py "$@"
