#!/bin/bash
# 初回セットアップ / 手動デプロイ用。定期実行時は systemd の ExecStartPre が
# 同等の git pull + pip install を行うため、通常はこのスクリプトを叩く必要はない。
set -e
cd "$(dirname "$0")"
git fetch origin
git reset --hard origin/main          # config.py は追跡外なので影響なし
python3 -m venv venv 2>/dev/null || true
venv/bin/pip install -r requirements.txt --quiet
echo "Deployed: $(git rev-parse --short HEAD)"
