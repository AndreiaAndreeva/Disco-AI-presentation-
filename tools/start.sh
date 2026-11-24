#!/usr/bin/env bash
# Запуск диалога с агентом. Путь к данным подаётся уже внутри диалога.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 -m funnel_tools.chat "$@"
