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


def filter_tradable(stocks: list[WencaiStock]) -> list[WencaiStock]:
    """按 ths_util 默认规则过滤标的：排除 ST、科创板(68)、北交所(83/87)。

    与 :func:`easy_tdx.wencai.ths_util.wen_cai_stock_info_by_question` 的过滤
    逻辑一致，用于批量回测前剔除高风险或不可回测板块标的。
    """
    return [
        s
        for s in stocks
        if "st" not in s.name.lower() and not s.symbol.startswith(("68", "83", "87"))
    ]
