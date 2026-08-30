# usdjpy-forecast

ドル円 (USD/JPY) の 1 週間見通し（**予想上値・予想下値・現在値**）を算出し、Telegram に定期送信する。

- 価格: yfinance (`JPY=X`) → 失敗時 stooq にフォールバック
- 予測: テクニカル要約（週間高安 / ATR14 / SMA）を DeepSeek に渡して想定レンジを推定
- `DEEPSEEK_API_KEY` 未設定・API 失敗時は「現在値 ± ATR14 × 係数」で算出する**劣化モード**
- 秘密情報はすべて `config.py` で一元管理（`.env` は使わない）

> 投資助言ではありません。テクニカル指標と LLM 推論による参考値です。

## ファイル

| ファイル | 役割 |
|---|---|
| `main.py` | エントリ。予測を算出し Telegram 送信 |
| `forecast.py` | `get_usdjpy_forecast() -> [予想上値, 予想下値, 現在値]` |
| `telegram_notifier.py` | `send_message(text)` |
| `config.py` | 秘密情報・設定（**コミットしない**、`.gitignore` 済み） |
| `config.example.py` | `config.py` のテンプレート |

## ローカルでの実行

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp config.example.py config.py
# config.py を編集して TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / DEEPSEEK_API_KEY を記入

python main.py --detail    # 送信せず詳細を表示（根拠付き）
python main.py --json      # 送信せず [上値, 下値, 現在値] を JSON 出力
python main.py             # Telegram へ送信
```

関数として使う場合:

```python
from forecast import get_usdjpy_forecast
week_high, week_low, current = get_usdjpy_forecast()
```

## テスト

ネット / API 不要:

```bash
python -m pytest -q
```

## サーバーへのデプロイ (systemd timer)

```bash
# 1. クローン
sudo git clone <repo-url> /opt/usdjpy-forecast
cd /opt/usdjpy-forecast

# 2. config.py を作成
cp config.example.py config.py
sudo vi config.py            # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DEEPSEEK_API_KEY

# 3. 初回デプロイ（venv 構築 + 依存インストール）
chmod +x deploy.sh
./deploy.sh

# 4. 動作確認（送信せず値だけ）
venv/bin/python main.py --json

# 5. systemd 登録
sudo cp systemd/usdjpy-forecast.service systemd/usdjpy-forecast.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now usdjpy-forecast.timer

# 6. 確認
systemctl list-timers | grep usdjpy-forecast
sudo systemctl start usdjpy-forecast.service   # 即時テスト実行
journalctl -u usdjpy-forecast.service -n 20
```

送信間隔は `systemd/usdjpy-forecast.timer` の `OnCalendar` で調整する。

## コード更新フロー

```bash
git push                                              # ローカルで変更を push
ssh server "cd /opt/usdjpy-forecast && ./deploy.sh"   # サーバーで反映
```

## 実行時に必要な外向き通信

| ホスト | 用途 |
|---|---|
| `api.telegram.org:443` | Telegram Bot API |
| `api.deepseek.com:443` | DeepSeek API |
| `query1.finance.yahoo.com:443` / `query2.finance.yahoo.com:443` | yfinance |
| `stooq.com:443` | フォールバック価格 |
