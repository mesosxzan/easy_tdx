"""同花顺问财语义查询客户端。

直接请求问财 ``get-stock-pick`` 接口，不依赖第三方 ``pywencai`` 库。
要求有效的登录 Cookie。
"""

from __future__ import annotations

import re
import time
from typing import Any

import requests

from easy_tdx.config import get_wencai_cookie, save_wencai_cookie

from .models import WencaiError, WencaiStock

_WENCAI_URL = "http://www.iwencai.com/unifiedwap/unified-wap/result/get-stock-pick"

_DEFAULT_HEADERS: dict[str, str] = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9"
    ),
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
    "Host": "search.10jqka.com.cn",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_3) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.163 Safari/537.36"
    ),
}

_REQUEST_TIMEOUT = 15  # 秒


def _resolve_cookie(cookie: str | None) -> str:
    """优先使用显式传参，其次回退统一配置。"""
    resolved = (cookie or get_wencai_cookie()).strip()
    if not resolved:
        raise WencaiError(
            "缺少问财 Cookie。请先在 Web UI 的“服务器设置”页保存，"
            "或在请求中传 `cookie`，或设置环境变量 `EASY_TDX_WENCAI_COOKIE`。"
        )
    return resolved


def _extract_cookie_field(cookie: str, field: str) -> str:
    """从 Cookie 字符串中提取指定字段的值。"""
    match = re.search(rf"\b{re.escape(field)}=([^;]+)", cookie)
    return match.group(1) if match else ""


def _parse_stock(item: dict[str, Any]) -> WencaiStock | None:
    """从问财 API 返回的单条数据解析出 WencaiStock。

    问财返回的 ``股票代码`` 格式为 ``000001.SZ``，用 ``.`` 拆分得到
    代码与市场。解析失败时返回 ``None``。
    """
    code_raw = str(item.get("股票代码", "")).strip()
    if not code_raw or "." not in code_raw:
        return None

    symbol, _, market = code_raw.partition(".")
    if not symbol or not market:
        return None

    name = str(item.get("股票简称", "")).strip()
    stock_reason = str(item.get("概念解析", "")).strip()

    return WencaiStock(symbol=symbol, market=market, name=name, stock_reason=stock_reason)


class WencaiClient:
    """问财语义搜索客户端。

    直接请求同花顺问财 ``get-stock-pick`` 接口，返回固定字段的
    :class:`WencaiStock` 列表，不依赖 ``pywencai`` 库。
    """

    def __init__(self, *, cookie: str | None = None) -> None:
        self.cookie = cookie

    def search(
        self,
        query: str,
        *,
        perpage: int = 100,
        cookie: str | None = None,
    ) -> list[WencaiStock]:
        """执行问财语义查询并返回股票列表。

        Args:
            query: 问财自然语言查询语句。
            perpage: 每页返回条数（最大 500）。
            cookie: 可选 Cookie；不传时自动读取配置。
        """
        query = query.strip()
        if not query:
            raise ValueError("query 不能为空")

        effective_cookie = _resolve_cookie(cookie or self.cookie)
        if cookie is not None or self.cookie is not None:
            save_wencai_cookie(effective_cookie)

        user_id = _extract_cookie_field(effective_cookie, "userid")
        user_name = _extract_cookie_field(effective_cookie, "u_name")

        query_data = {
            "perpage": min(perpage, 500),
            "version": "2.0",
            "source": "ths_mobile_iwencai",
            "user_id": user_id,
            "user_name": user_name,
            "question": query,
            "direct_mode": "",
            "secondary_intent": "",
            "add_info": '{"urp":{"scene":3,"company":1,"business":1},"contentType":"json"}',
            "_": str(int(time.time() * 1000)),
        }

        headers = {**_DEFAULT_HEADERS, "Cookie": effective_cookie}

        try:
            response = requests.post(
                _WENCAI_URL,
                data=query_data,
                headers=headers,
                allow_redirects=False,
                timeout=_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            raise WencaiError(f"问财请求失败: {e}") from e

        try:
            content = response.json()
        except ValueError as e:
            raise WencaiError(f"问财返回非 JSON 响应: {e}") from e

        stocks_data = content.get("data", {}).get("data", [])
        if not isinstance(stocks_data, list):
            raise WencaiError("问财返回数据格式异常")

        results: list[WencaiStock] = []
        for item in stocks_data:
            if not isinstance(item, dict):
                continue
            stock = _parse_stock(item)
            if stock is not None:
                results.append(stock)

        return results
