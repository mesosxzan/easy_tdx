"""同花顺问财数据模型。"""

from __future__ import annotations

from dataclasses import dataclass

from easy_tdx.exceptions import TdxError


@dataclass(frozen=True, slots=True)
class WencaiStock:
    """问财搜索结果中的单只股票。

    Attributes:
        symbol: 6 位股票代码（如 ``000001``）。
        market: 市场代码（如 ``SZ`` / ``SH`` / ``BJ``）。
        name: 股票简称。
        stock_reason: 概念解析/入选理由，可能为空字符串。
    """

    symbol: str
    market: str
    name: str
    stock_reason: str


class WencaiError(TdxError):
    """问财请求或解析失败。"""
