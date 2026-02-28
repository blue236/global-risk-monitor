#!/usr/bin/env bash
set -euo pipefail

# Reuse existing virtualenv if present. Creating a new venv on every run can
# fail on systems where the active `python3` runtime is missing stdlib modules
# (e.g. `_posixsubprocess`) and is unnecessary for normal startup.
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export GRM_DATA_DIR="${GRM_DATA_DIR:-$HOME/.global-risk-monitor}"
export GRM_DB_PATH="${GRM_DB_PATH:-$GRM_DATA_DIR/risk_monitor.sqlite}"

export GRM_HOST="${GRM_HOST:-127.0.0.1}"
export GRM_PORT="${GRM_PORT:-8000}"

uvicorn app.main:app --host "$GRM_HOST" --port "$GRM_PORT"
