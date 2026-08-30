#!/bin/bash
set -e
cd /opt/usdjpy-forecast
git fetch origin
git reset --hard origin/main
python3 -m venv venv --clear 2>/dev/null || true
source venv/bin/activate
pip install -r requirements.txt --quiet
echo "Deployed: $(git rev-parse --short HEAD)"
