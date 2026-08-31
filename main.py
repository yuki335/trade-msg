"""instruments.yaml の各銘柄について 1 週間見通しを算出し、Telegram に送信する。

使い方:
    python main.py                  全銘柄を算出して Telegram へ送信（非取引日の市場はスキップ）
    python main.py --force          取引日判定を無視して実行
    python main.py --market jpx     その市場の銘柄だけ実行（fx / jpx / nyse）
    python main.py --only 7203      id または symbol が一致する銘柄だけ実行（カンマ区切り可）
    python main.py --json           送信せず {id: [上値, 下値, 現在値]} を JSON 出力
    python main.py --detail         送信せず {id: 詳細dict} を JSON 出力
    python main.py --check          送信せず、解決後プロファイルと市場の取引判定を表示（ネット最小）
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import instruments as instruments_mod
from forecast import get_forecast_detail
from markets import is_trading_day
from telegram_notifier import send_message


def _arg_value(args: list[str], name: str) -> str | None:
    """--name value / --name=value のどちらも受ける。"""
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def _select(instruments: list[dict], args: list[str]) -> list[dict]:
    market = _arg_value(args, "--market")
    only = _arg_value(args, "--only")
    out = instruments
    if market:
        out = [i for i in out if i.get("market") == market]
    if only:
        wanted = {w.strip().lower() for w in only.split(",") if w.strip()}
        out = [
            i for i in out
            if i["id"].lower() in wanted or str(i.get("symbol", "")).lower() in wanted
        ]
    return out


def _clip(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _short_date(value) -> str:
    """"2026-08-29" / ISO 文字列 -> "08-29"。"""
    m = re.search(r"\d{4}-(\d{2})-(\d{2})", str(value))
    return f"{m.group(1)}-{m.group(2)}" if m else str(value)[:10]


def build_message(detail: dict) -> str:
    d = int(detail.get("price_decimals", 2))
    unit = detail.get("unit", "")
    suffix = f" {unit}" if unit else ""
    name = detail.get("display_name", detail.get("instrument_id", ""))
    now = datetime.now(ZoneInfo(config.TIMEZONE))

    def fmt(x: float) -> str:
        return f"{x:.{d}f}{suffix}"

    lines = [f"=== {name} 1週間見通し ({now:%Y-%m-%d}) ==="]

    edge = detail.get("edge") or {}
    if edge.get("alert"):
        pct = round(edge.get("ratio", 0.0) * 100)
        thr = round(edge.get("threshold", 0.0) * 100)
        lines.append(
            f"位置    : 現在値はレンジ端寄り（{edge.get('side', '')} {pct}% / 閾値 {thr}%）"
        )

    lines += [
        f"現在値  : {fmt(detail['current'])}",
        f"予想上値: {fmt(detail['week_high'])}",
        f"予想下値: {fmt(detail['week_low'])}",
    ]
    rationale = detail.get("rationale", "")
    if rationale:
        lines.append(f"根拠    : {rationale}")

    degraded = detail.get("source") == "degraded"

    if not degraded:
        bias = (detail.get("bias") or "").strip()
        key_events = detail.get("key_events") or []
        if bias or key_events:
            parts = []
            if bias:
                parts.append(f"bias : {bias}")
            if key_events:
                parts.append("注目 : " + ", ".join(str(e) for e in key_events))
            lines.append("---")
            lines.append("  / ".join(parts))

        articles = detail.get("web_articles") or []
        if articles:
            limit = getattr(config, "WEB_SUMMARY_MAX_LINES", 5)
            lines.append(f"--- 参照ニュース ({len(articles)}件) ---")
            for a in articles[:limit]:
                pub = a.get("published")
                date = f"[{_short_date(pub)}] " if pub else ""
                lines.append(f"・{date}{_clip(a.get('title', ''), 30)} — {a.get('url', '')}")

    return "\n".join(lines)


def _print_check(selected: list[dict]) -> None:
    status: dict[str, tuple[bool, str]] = {}
    for inst in selected:
        mkt = inst.get("market", "")
        if mkt not in status:
            status[mkt] = is_trading_day(mkt)
        trading, reason = status[mkt]
        print(f"--- {inst['id']}  ({inst.get('display_name', '')}) ---")
        print(f"  市場        : {mkt}  取引{'あり' if trading else 'なし'}"
              f"{'（' + reason + '）' if reason else ''}")
        print(f"  symbol      : {inst.get('symbol')}  / stooq: {inst.get('stooq_symbol')}")
        print(f"  小数桁      : {inst.get('price_decimals')}  ATR係数: {inst.get('atr_multiplier')}"
              f"  lookback: {inst.get('lookback_days')}日")
        print(f"  検索クエリ  : {inst.get('search_queries')}")
        print(f"  明示指定    : {sorted(inst.get('_explicit', []))}")
    print()
    print("市場サマリ:", {m: ("取引あり" if t else r) for m, (t, r) in status.items()})


def main() -> None:
    args = sys.argv[1:]

    enrich = "--check" not in args and "--no-enrich" not in args
    instruments = instruments_mod.load_instruments(enrich_meta=enrich)
    if not instruments:
        print("instruments.yaml に有効な銘柄がありません", file=sys.stderr)
        sys.exit(1)

    selected = _select(instruments, args)
    if not selected:
        print("該当する銘柄がありません（--market / --only を確認）", file=sys.stderr)
        sys.exit(1)

    if "--check" in args:
        _print_check(selected)
        return

    if "--detail" in args:
        print(json.dumps(
            {i["id"]: get_forecast_detail(i) for i in selected}, ensure_ascii=False, indent=2
        ))
        return

    if "--json" in args:
        out = {}
        for i in selected:
            d = get_forecast_detail(i)
            out[i["id"]] = [d["week_high"], d["week_low"], d["current"]]
        print(json.dumps(out, ensure_ascii=False))
        return

    check_trading = getattr(config, "TRADING_DAY_CHECK", True) and "--force" not in args
    edge_alert_only = getattr(config, "EDGE_ALERT_ONLY", False)
    status: dict[str, tuple[bool, str]] = {}
    sent, skipped, failed = [], [], []

    for inst in selected:
        mkt = inst.get("market", "")
        if check_trading:
            if mkt not in status:
                status[mkt] = is_trading_day(mkt)
            trading, reason = status[mkt]
            if not trading:
                skipped.append((inst["id"], mkt, reason))
                continue
        try:
            detail = get_forecast_detail(inst)
            if edge_alert_only and not (detail.get("edge") or {}).get("alert"):
                skipped.append((inst["id"], mkt, "端寄りでない"))
                continue
            send_message(build_message(detail))
            sent.append(inst["id"])
        except Exception as exc:  # noqa: BLE001 - 1 銘柄の失敗で全体を止めない
            failed.append((inst["id"], str(exc)))
            print(f"[error] {inst['id']}: {exc}", file=sys.stderr)

    print(f"sent={sent}")
    if skipped:
        print(f"skipped={[f'{i}({m}:{r})' for i, m, r in skipped]}")
    if failed:
        print(f"failed={[f'{i}: {e}' for i, e in failed]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
