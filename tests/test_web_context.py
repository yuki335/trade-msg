"""web_context.get_web_context(instrument) の単体テスト。requests.post をモックしてネット不要。

    cd usdjpy-forecast && python -m pytest -q
"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import config
import web_context
from conftest import make_instrument


class _Resp:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


def _hit(title, url, content="ドル円 は 日銀 と FRB の 影響 で 動いた。", published="2026-08-29"):
    return {"title": title, "url": url, "content": content, "published_date": published}


@pytest.fixture
def instrument():
    return make_instrument(
        id="usdjpy",
        search_queries=["q1", "q2"],
        relevance_keywords="ドル円|USD/JPY|日銀|FRB|利上げ",
    )


@pytest.fixture(autouse=True)
def _cfg(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "WEB_CONTEXT_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "TAVILY_API_KEY", "tvly-test", raising=False)
    monkeypatch.setattr(config, "WEB_MAX_RESULTS_PER_QUERY", 4, raising=False)
    monkeypatch.setattr(config, "WEB_MAX_ARTICLES", 5, raising=False)
    monkeypatch.setattr(config, "WEB_ARTICLE_CHARS", 800, raising=False)
    monkeypatch.setattr(config, "WEB_TOTAL_CHARS", 4000, raising=False)
    monkeypatch.setattr(config, "WEB_FETCH_TIMEOUT", 5, raising=False)
    monkeypatch.setattr(web_context, "_CACHE_DIR", tmp_path / "cache", raising=False)


def test_normal_filters_dedup_sorts_and_limits(monkeypatch, instrument):
    def fake_post(_url, **kw):
        q = kw["json"]["query"]
        if q == "q1":
            return _Resp({"results": [
                _hit("ドル円 週間見通し", "https://a.com/1?utm=x", published="2026-08-29"),
                _hit("野球の試合結果", "https://a.com/sports", content="打者が本塁打", published="2026-08-29"),
            ]})
        return _Resp({"results": [
            _hit("ドル円 週間見通し dup", "https://a.com/1", published="2026-08-28"),
            _hit("日銀 利上げ観測", "https://a.com/2", published="2026-08-31"),
        ]})

    monkeypatch.setattr(web_context.requests, "post", fake_post)
    web = web_context.get_web_context(instrument)

    urls = [a["url"] for a in web["articles"]]
    assert web["used"] is True
    assert "https://a.com/sports" not in urls               # 関連度フィルタ
    assert sum(u.startswith("https://a.com/1") for u in urls) == 1  # 重複除去
    assert web["articles"][0]["url"] == "https://a.com/2"    # 新しい順


def test_relevance_keywords_come_from_instrument(monkeypatch):
    inst = make_instrument(id="toyota", search_queries=["q"], relevance_keywords="トヨタ|7203")

    def fake_post(_url, **kw):
        return _Resp({"results": [
            _hit("トヨタ 決算 発表", "https://t.com/1", content="トヨタの営業利益"),
            _hit("ドル円 見通し", "https://t.com/2", content="日銀とFRB"),
        ]})

    monkeypatch.setattr(web_context.requests, "post", fake_post)
    web = web_context.get_web_context(inst)
    assert [a["url"] for a in web["articles"]] == ["https://t.com/1"]


def test_partial_failure_keeps_other_query(monkeypatch, instrument):
    def fake_post(_url, **kw):
        if kw["json"]["query"] == "q1":
            raise RuntimeError("timeout")
        return _Resp({"results": [_hit("ドル円 見通し", "https://b.com/1")]})

    monkeypatch.setattr(web_context.requests, "post", fake_post)
    web = web_context.get_web_context(instrument)
    assert any("q1" in e for e in web["errors"])
    assert [a["url"] for a in web["articles"]] == ["https://b.com/1"]


def test_all_queries_fail_returns_unused(monkeypatch, instrument):
    monkeypatch.setattr(web_context.requests, "post",
                        lambda _u, **_k: (_ for _ in ()).throw(RuntimeError("down")))
    web = web_context.get_web_context(instrument)
    assert web["used"] is False and web["articles"] == []


def test_no_key_skips_http(monkeypatch, instrument):
    monkeypatch.setattr(config, "TAVILY_API_KEY", "", raising=False)
    monkeypatch.setattr(web_context.requests, "post",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no HTTP")))
    web = web_context.get_web_context(instrument)
    assert web["used"] is False


def test_no_queries_skips_http(monkeypatch):
    inst = make_instrument(search_queries=[])
    monkeypatch.setattr(web_context.requests, "post",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no HTTP")))
    web = web_context.get_web_context(inst)
    assert web["used"] is False


def test_cache_is_per_instrument(monkeypatch, tmp_path, instrument):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    now = datetime.now(ZoneInfo(config.TIMEZONE))
    payload = {"as_of": now.isoformat(), "used": True,
               "articles": [{"title": "x", "url": "u", "published": None, "snippet": "s"}], "errors": []}
    (cache_dir / f"web_usdjpy_{now:%Y%m%d}.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(web_context, "_CACHE_DIR", cache_dir, raising=False)
    monkeypatch.setattr(web_context.requests, "post",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("cache hit")))
    assert web_context.get_web_context(instrument) == payload


def test_result_is_cached_with_instrument_id(monkeypatch, tmp_path, instrument):
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(web_context, "_CACHE_DIR", cache_dir, raising=False)
    monkeypatch.setattr(web_context.requests, "post",
                        lambda _u, **_k: _Resp({"results": [_hit("ドル円 見通し", "https://e.com/1")]}))
    web_context.get_web_context(instrument)
    now = datetime.now(ZoneInfo(config.TIMEZONE))
    assert (cache_dir / f"web_usdjpy_{now:%Y%m%d}.json").exists()
