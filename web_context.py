"""Tavily 検索 API 経由で、指定銘柄に関連するニュースの抜粋を集める。

get_web_context(instrument) -> dict
    instrument["search_queries"]      検索クエリ（instruments.py が生成 or YAML 指定）
    instrument["relevance_keywords"]  関連度フィルタの正規表現
    instrument["id"]                  日次キャッシュのキー

LLM は使わない。取得 → 関連度フィルタ → URL 正規化して重複除去 → 新しい順 →
件数 / 文字数トリム → 日次キャッシュ、だけを行う。
すべての外部呼び出しを保護し、**この関数は例外を投げない**（全滅時 used=False, articles=[]）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import config

_TAVILY_URL = "https://api.tavily.com/search"
_CACHE_DIR = Path(__file__).resolve().parent / "cache"


def _now() -> datetime:
    return datetime.now(ZoneInfo(config.TIMEZONE))


def _cache_path(instrument_id: str, now: datetime) -> Path:
    return _CACHE_DIR / f"web_{instrument_id}_{now:%Y%m%d}.json"


def _relevance_re(instrument: dict) -> re.Pattern:
    pattern = instrument.get("relevance_keywords") or r".+"
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(r".+")


def _search_tavily(query: str) -> list[dict]:
    r = requests.post(
        _TAVILY_URL,
        json={
            "api_key": config.TAVILY_API_KEY,
            "query": query,
            "topic": "news",
            "days": 7,
            "max_results": config.WEB_MAX_RESULTS_PER_QUERY,
            "search_depth": "basic",
            "include_raw_content": False,
        },
        timeout=config.WEB_FETCH_TIMEOUT,
    )
    r.raise_for_status()
    return [
        {
            "title": (x.get("title") or "").strip(),
            "url": (x.get("url") or "").strip(),
            "snippet": (x.get("content") or "").strip(),
            "published": x.get("published_date"),
        }
        for x in r.json().get("results", [])
    ]


def _normalize_url(url: str) -> str:
    return url.split("?", 1)[0].split("#", 1)[0].rstrip("/")


def _published_key(article: dict) -> str:
    # 新しい順ソート用。published が None の記事は空文字にして最後へ。
    return article.get("published") or ""


def _empty(now: datetime, errors: list[str]) -> dict:
    return {"as_of": now.isoformat(), "used": False, "articles": [], "errors": errors}


def get_web_context(instrument: dict) -> dict:
    now = _now()
    inst_id = instrument.get("id", "default")
    queries = instrument.get("search_queries") or []
    relevance = _relevance_re(instrument)

    # 1. 同日キャッシュ（あれば即返す）
    try:
        cp = _cache_path(inst_id, now)
        if cp.exists():
            return json.loads(cp.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass

    if not (getattr(config, "WEB_CONTEXT_ENABLED", False) and getattr(config, "TAVILY_API_KEY", "")):
        return _empty(now, ["web context disabled or TAVILY_API_KEY unset"])
    if not queries:
        return _empty(now, ["no search queries for instrument"])

    # 2. Tavily 呼び出し（クエリごとに個別 try/except）
    collected: list[dict] = []
    errors: list[str] = []
    for query in queries:
        try:
            collected.extend(_search_tavily(query))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"tavily[{query}]: {exc}")

    # 3. 関連度フィルタ
    collected = [
        a for a in collected
        if a.get("url") and relevance.search(f"{a.get('title', '')} {a.get('snippet', '')}")
    ]

    # 4. 重複除去（URL 正規化）
    seen: set[str] = set()
    deduped: list[dict] = []
    for a in collected:
        key = _normalize_url(a["url"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(a)

    # 5. 新しい順ソート → 上位 N
    deduped.sort(key=_published_key, reverse=True)
    deduped = deduped[: config.WEB_MAX_ARTICLES]

    # 6. トリム（記事ごと WEB_ARTICLE_CHARS / 合計 WEB_TOTAL_CHARS）
    articles: list[dict] = []
    total = 0
    for a in deduped:
        snippet = (a.get("snippet") or "")[: config.WEB_ARTICLE_CHARS]
        if total + len(snippet) > config.WEB_TOTAL_CHARS:
            snippet = snippet[: max(0, config.WEB_TOTAL_CHARS - total)]
        total += len(snippet)
        articles.append(
            {
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "published": a.get("published"),
                "snippet": snippet,
            }
        )
        if total >= config.WEB_TOTAL_CHARS:
            break

    result = {
        "as_of": now.isoformat(),
        "used": bool(articles),
        "articles": articles,
        "errors": errors,
    }

    # 7. キャッシュ保存（失敗は無視）
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(inst_id, now).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass

    return result


if __name__ == "__main__":
    import instruments

    for prof in instruments.load_instruments(enrich_meta=False):
        print(prof["id"], json.dumps(get_web_context(prof), ensure_ascii=False, indent=2))
