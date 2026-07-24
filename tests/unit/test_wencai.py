"""问财模块离线测试 -- mock requests，零网络依赖。"""

from __future__ import annotations

from typing import Any

import pytest
import requests

from easy_tdx.wencai import client as _wencai_client

# ── 辅助：构造 mock response ──────────────────────────────────────────────────


class _MockResponse:
    """模拟 requests.Response。"""

    def __init__(self, json_data: Any, status_code: int = 200) -> None:
        self._json_data = json_data
        self.status_code = status_code

    def json(self) -> Any:
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _wencai_response(stocks: list[dict[str, Any]]) -> _MockResponse:
    """构造问财 API 的标准响应结构。"""
    return _MockResponse({"data": {"data": stocks}})


# ── 模块导出 ──────────────────────────────────────────────────────────────────


def test_public_exports() -> None:
    """模块应导出 WencaiClient / WencaiError / WencaiStock。"""
    from easy_tdx import wencai

    assert hasattr(wencai, "WencaiClient")
    assert hasattr(wencai, "WencaiError")
    assert hasattr(wencai, "WencaiStock")


def test_wencai_error_subclasses_tdx_error() -> None:
    """WencaiError 必须继承 TdxError。"""
    from easy_tdx.exceptions import TdxError
    from easy_tdx.wencai import WencaiError

    assert issubclass(WencaiError, TdxError)
    assert issubclass(WencaiError, Exception)


def test_wencai_stock_dataclass_fields() -> None:
    """WencaiStock 应包含 symbol/market/name/stock_reason 字段。"""
    from easy_tdx.wencai import WencaiStock

    stock = WencaiStock(symbol="000001", market="SZ", name="平安银行", stock_reason="概念A")
    assert stock.symbol == "000001"
    assert stock.market == "SZ"
    assert stock.name == "平安银行"
    assert stock.stock_reason == "概念A"


# ── Cookie 解析 ───────────────────────────────────────────────────────────────


def test_search_missing_cookie_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EASY_TDX_WENCAI_COOKIE", raising=False)
    monkeypatch.setattr(_wencai_client, "get_wencai_cookie", lambda: "")
    from easy_tdx.wencai import WencaiClient, WencaiError

    with pytest.raises(WencaiError, match="Cookie"):
        WencaiClient().search("涨停股票")


# ── 搜索功能 ──────────────────────────────────────────────────────────────────


def test_search_uses_env_cookie_and_returns_stocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EASY_TDX_WENCAI_COOKIE", "userid=12345; u_name=testuser; ticket=abc")
    captured: dict[str, Any] = {}

    def _fake_post(url: str, data: Any = None, headers: Any = None, **kwargs: Any) -> _MockResponse:
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        captured["kwargs"] = kwargs
        return _wencai_response([
            {"股票代码": "000001.SZ", "股票简称": "平安银行", "概念解析": "银行"},
            {"股票代码": "600000.SH", "股票简称": "浦发银行"},
        ])

    monkeypatch.setattr(_wencai_client.requests, "post", _fake_post)

    from easy_tdx.wencai import WencaiClient

    stocks = WencaiClient().search("今日涨幅前十", perpage=50)

    assert len(stocks) == 2
    assert stocks[0].symbol == "000001"
    assert stocks[0].market == "SZ"
    assert stocks[0].name == "平安银行"
    assert stocks[0].stock_reason == "银行"
    assert stocks[1].symbol == "600000"
    assert stocks[1].market == "SH"
    assert stocks[1].name == "浦发银行"
    assert stocks[1].stock_reason == ""

    # 验证请求参数
    assert captured["data"]["question"] == "今日涨幅前十"
    assert captured["data"]["perpage"] == 50
    assert captured["data"]["user_id"] == "12345"
    assert captured["data"]["user_name"] == "testuser"
    assert captured["headers"]["Cookie"] == "userid=12345; u_name=testuser; ticket=abc"


def test_search_uses_config_cookie_when_request_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EASY_TDX_WENCAI_COOKIE", raising=False)
    monkeypatch.setattr(
        _wencai_client, "get_wencai_cookie", lambda: "userid=99999; u_name=configuser"
    )
    captured: dict[str, Any] = {}

    def _fake_post(url: str, data: Any = None, headers: Any = None, **kwargs: Any) -> _MockResponse:
        captured["data"] = data
        return _wencai_response([{"股票代码": "000001.SZ", "股票简称": "平安银行"}])

    monkeypatch.setattr(_wencai_client.requests, "post", _fake_post)

    from easy_tdx.wencai import WencaiClient

    stocks = WencaiClient().search("今日涨幅前十")
    assert len(stocks) == 1
    assert captured["data"]["user_id"] == "99999"


def test_search_persists_explicit_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EASY_TDX_WENCAI_COOKIE", raising=False)
    monkeypatch.setattr(_wencai_client, "get_wencai_cookie", lambda: "")
    saved: list[str] = []

    def _fake_post(url: str, data: Any = None, headers: Any = None, **kwargs: Any) -> _MockResponse:
        return _wencai_response([{"股票代码": "000001.SZ", "股票简称": "平安银行"}])

    monkeypatch.setattr(_wencai_client.requests, "post", _fake_post)
    monkeypatch.setattr(
        _wencai_client, "save_wencai_cookie", lambda cookie: saved.append(cookie)
    )

    from easy_tdx.wencai import WencaiClient

    WencaiClient().search("今日涨幅前十", cookie="userid=111; u_name=manual")
    assert saved == ["userid=111; u_name=manual"]


def test_search_empty_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EASY_TDX_WENCAI_COOKIE", "userid=12345")

    def _fake_post(url: str, data: Any = None, headers: Any = None, **kwargs: Any) -> _MockResponse:
        return _wencai_response([])

    monkeypatch.setattr(_wencai_client.requests, "post", _fake_post)

    from easy_tdx.wencai import WencaiClient

    stocks = WencaiClient().search("不存在的条件")
    assert stocks == []


def test_search_skips_invalid_items(monkeypatch: pytest.MonkeyPatch) -> None:
    """应跳过无法解析股票代码的条目。"""
    monkeypatch.setenv("EASY_TDX_WENCAI_COOKIE", "userid=12345")

    def _fake_post(url: str, data: Any = None, headers: Any = None, **kwargs: Any) -> _MockResponse:
        return _wencai_response([
            {"股票代码": "000001.SZ", "股票简称": "平安银行"},
            {"股票代码": "", "股票简称": "无效"},
            "not_a_dict",  # 非 dict
            {"股票简称": "无代码"},
        ])

    monkeypatch.setattr(_wencai_client.requests, "post", _fake_post)

    from easy_tdx.wencai import WencaiClient

    stocks = WencaiClient().search("测试")
    assert len(stocks) == 1
    assert stocks[0].symbol == "000001"


def test_search_wraps_request_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EASY_TDX_WENCAI_COOKIE", "userid=12345")

    def _fake_post(url: str, data: Any = None, headers: Any = None, **kwargs: Any) -> _MockResponse:
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(_wencai_client.requests, "post", _fake_post)

    from easy_tdx.wencai import WencaiClient, WencaiError

    with pytest.raises(WencaiError, match="问财请求失败"):
        WencaiClient().search("涨停股票")


def test_search_wraps_json_decode_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EASY_TDX_WENCAI_COOKIE", "userid=12345")

    class _BadJsonResponse(_MockResponse):
        def json(self) -> Any:
            raise ValueError("not json")

    def _fake_post(url: str, data: Any = None, headers: Any = None, **kwargs: Any) -> _MockResponse:
        return _BadJsonResponse({})

    monkeypatch.setattr(_wencai_client.requests, "post", _fake_post)

    from easy_tdx.wencai import WencaiClient, WencaiError

    with pytest.raises(WencaiError, match="非 JSON"):
        WencaiClient().search("涨停股票")


def test_search_wraps_invalid_data_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """问财返回的 data.data 不是列表时应报错。"""
    monkeypatch.setenv("EASY_TDX_WENCAI_COOKIE", "userid=12345")

    def _fake_post(url: str, data: Any = None, headers: Any = None, **kwargs: Any) -> _MockResponse:
        return _MockResponse({"data": {"data": "not_a_list"}})

    monkeypatch.setattr(_wencai_client.requests, "post", _fake_post)

    from easy_tdx.wencai import WencaiClient, WencaiError

    with pytest.raises(WencaiError, match="格式异常"):
        WencaiClient().search("涨停股票")


def test_search_empty_query_raises() -> None:
    from easy_tdx.wencai import WencaiClient

    with pytest.raises(ValueError, match="query"):
        WencaiClient(cookie="userid=1").search("   ")


# ── Web 路由测试 ──────────────────────────────────────────────────────────────


def test_wencai_router_success(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("EASY_TDX_WENCAI_COOKIE", "userid=12345")

    def _fake_post(url: str, data: Any = None, headers: Any = None, **kwargs: Any) -> _MockResponse:
        return _wencai_response([
            {"股票代码": "000001.SZ", "股票简称": "平安银行", "概念解析": "银行股"},
        ])

    monkeypatch.setattr(_wencai_client.requests, "post", _fake_post)

    from easy_tdx.web.errors import register_exception_handlers
    from easy_tdx.web.routers.wencai import router as wencai_router

    app = FastAPI()
    app.include_router(wencai_router, prefix="/api/v1")
    register_exception_handlers(app)
    client = TestClient(app)

    resp = client.post("/api/v1/wencai/search", json={"query": "今日涨幅前十"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["data"][0] == {
        "symbol": "000001",
        "market": "SZ",
        "name": "平安银行",
        "stock_reason": "银行股",
    }


def test_wencai_router_missing_cookie_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.delenv("EASY_TDX_WENCAI_COOKIE", raising=False)
    monkeypatch.setattr(_wencai_client, "get_wencai_cookie", lambda: "")

    def _fake_post(url: str, data: Any = None, headers: Any = None, **kwargs: Any) -> _MockResponse:
        return _wencai_response([{"股票代码": "000001.SZ", "股票简称": "平安银行"}])

    monkeypatch.setattr(_wencai_client.requests, "post", _fake_post)

    from easy_tdx.web.errors import register_exception_handlers
    from easy_tdx.web.routers.wencai import router as wencai_router

    app = FastAPI()
    app.include_router(wencai_router, prefix="/api/v1")
    register_exception_handlers(app)
    client = TestClient(app)

    resp = client.post("/api/v1/wencai/search", json={"query": "今日涨幅前十"})
    assert resp.status_code == 503
    assert "Cookie" in resp.json()["detail"]
