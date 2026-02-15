#!/usr/bin/env bash
set -euo pipefail

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export GRM_DATA_DIR="${GRM_DATA_DIR:-$HOME/.global-risk-monitor}"
export GRM_DB_PATH="${GRM_DB_PATH:-$GRM_DATA_DIR/risk_monitor.sqlite}"

uvicorn app.main:app --host 127.0.0.1 --port 8000
