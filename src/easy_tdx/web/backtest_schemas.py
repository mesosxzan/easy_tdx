"""回测 Web API 的请求/响应模型与结果序列化。

与 ``easy_tdx.web.schemas`` 分离，避免主 schemas 文件膨胀。结果序列化复用
主 schemas 的 numpy/datetime 清洗思路，但递归处理嵌套结构（BacktestResult
的 performance 是 dict、equity_curve 是 list[dict]、config 是 dict）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "BacktestRequest",
    "BacktestResultResponse",
    "StrategySchemaResponse",
    "TaskSubmitResponse",
    "TaskStateResponse",
    "serialize_result",
]


# ── 请求模型 ───────────────────────────────────────────────────────────────────


class BacktestRequest(BaseModel):
    """单标的回测请求。

    两种数据来源（二选一）：
    - ``ohlcv``: 直接内联 OHLCV 记录（前端已有数据时）。
    - ``symbol`` + ``category`` + ``count``: 指定标的，由后端取行情。
      两者都给则以内联数据为准。
    """

    strategy: str = Field(..., description="策略名（见 /backtest/strategies）")
    params: dict[str, Any] = Field(default_factory=dict, description="策略参数")
    cash: float = Field(default=100000.0, gt=0, description="初始资金")
    commission: float = Field(default=0.0003, ge=0, le=0.01, description="佣金费率")
    min_commission: float = Field(default=5.0, ge=0, description="单笔最低佣金")
    stamp_tax: float = Field(default=0.001, ge=0, le=0.01, description="印花税（卖出）")
    slippage: float = Field(default=0.0, ge=0, le=0.05, description="滑点费率")
    execution: Literal["next_open", "next_close", "this_close", "worst", "best"] = Field(
        default="next_open", description="成交模式"
    )

    # 数据来源 A：内联 OHLCV（上限与 symbol 路径的 count 上限对齐，防 DoS）
    ohlcv: list[dict[str, Any]] | None = Field(
        default=None,
        max_length=2000,
        description="内联 K 线记录（含 datetime/open/high/low/close/vol/amount，最多 2000 条）",
    )

    # 数据来源 B：按标的取行情
    symbol: str | None = Field(
        default=None,
        pattern=r"^(SZ|SH|BJ):\d{6}$",
        description="标的代码，格式 市场:代码，如 SZ:000001",
    )
    category: Literal["DAY", "WEEK", "MONTH", "MIN_5", "MIN_15", "MIN_30", "MIN_60"] = Field(
        default="DAY", description="K 线周期"
    )
    count: int = Field(default=250, ge=20, le=2000, description="K 线根数")

    @model_validator(mode="after")
    def _check_data_source(self) -> BacktestRequest:
        if self.ohlcv is None and self.symbol is None:
            raise ValueError("必须提供 ohlcv（内联数据）或 symbol（标的代码）之一")
        return self


class PortfolioBacktestRequest(BaseModel):
    """组合（多标的）回测请求。

    与单标的 BacktestRequest 的区别：用 ``stocks`` 列表替代单个 ``symbol``，
    资金按 equal 模式均分到各标的。日期范围过滤由前端完成（取满 800 根后过滤）。
    """

    strategy: str = Field(..., description="策略名")
    params: dict[str, Any] = Field(default_factory=dict, description="策略参数")
    cash: float = Field(default=200000.0, gt=0, description="组合总资金")
    commission: float = Field(default=0.0003, ge=0, le=0.01)
    min_commission: float = Field(default=5.0, ge=0)
    stamp_tax: float = Field(default=0.001, ge=0, le=0.01)
    slippage: float = Field(default=0.0, ge=0, le=0.05)
    execution: Literal["next_open", "next_close", "this_close", "worst", "best"] = Field(
        default="next_open"
    )
    stocks: list[str] = Field(
        ...,
        min_length=1,
        max_length=20,
        description='标的列表，格式 "市场:代码"，如 ["SZ:000001","SH:600519"]',
    )
    category: Literal["DAY", "WEEK", "MONTH", "MIN_5", "MIN_15", "MIN_30", "MIN_60"] = Field(
        default="DAY"
    )
    start_date: str | None = Field(default=None, description="开始日期 YYYY-MM-DD（可选过滤）")
    end_date: str | None = Field(default=None, description="结束日期 YYYY-MM-DD（可选过滤）")

    @model_validator(mode="after")
    def _check_stocks_format(self) -> PortfolioBacktestRequest:
        for s in self.stocks:
            if not s.startswith(("SZ:", "SH:", "BJ:")) or len(s) != 9:
                raise ValueError(f"标的格式应为 '市场:6位代码'，得到 {s!r}")
        return self


class OptimizeBacktestRequest(BaseModel):
    """参数网格寻优请求。

    在单个标的上，对策略的 1-2 个参数做网格搜索。数据来源与单标的回测一致
    （ohlcv 内联或 symbol 取行情）。param_grid 指定寻优参数及其取值列表，
    网格大小（各取值数乘积）上限 200。
    """

    strategy: str = Field(..., description="策略名")
    cash: float = Field(default=100000.0, gt=0)
    commission: float = Field(default=0.0003, ge=0, le=0.01)
    slippage: float = Field(default=0.0, ge=0, le=0.05)
    execution: Literal["next_open", "next_close", "this_close", "worst", "best"] = Field(
        default="next_open"
    )
    param_grid: dict[str, list[int | float | str]] = Field(
        ...,
        min_length=1,
        max_length=2,
        description='参数取值网格，如 {"fast":[5,10,20], "slow":[15,20,30]}',
    )

    # 数据来源 A：内联 OHLCV
    ohlcv: list[dict[str, Any]] | None = Field(default=None, max_length=2000)

    # 数据来源 B：按标的取行情
    symbol: str | None = Field(default=None, pattern=r"^(SZ|SH|BJ):\d{6}$")
    category: Literal["DAY", "WEEK", "MONTH", "MIN_5", "MIN_15", "MIN_30", "MIN_60"] = Field(
        default="DAY"
    )
    count: int = Field(default=250, ge=20, le=800)
    start_date: str | None = Field(default=None)
    end_date: str | None = Field(default=None)

    @model_validator(mode="after")
    def _check_data_source(self) -> OptimizeBacktestRequest:
        if self.ohlcv is None and self.symbol is None:
            raise ValueError("必须提供 ohlcv 或 symbol 之一")
        return self


# ── 响应模型 ───────────────────────────────────────────────────────────────────


class StrategySchemaResponse(BaseModel):
    """策略 schema 列表响应（/backtest/strategies）。"""

    strategies: list[dict[str, Any]]
    count: int


class BacktestResultResponse(BaseModel):
    """回测结果响应（已清洗为 JSON 原生类型）。"""

    performance: dict[str, Any]
    equity_curve: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    positions: list[dict[str, Any]]
    config: dict[str, Any]


class TaskSubmitResponse(BaseModel):
    """后台任务提交响应。"""

    task_id: str
    status: Literal["pending", "running"]


class TaskStateResponse(BaseModel):
    """后台任务状态响应。"""

    task_id: str
    status: Literal["pending", "running", "done", "failed"]
    result: dict[str, Any] | None = None
    error: str | None = None
    description: str = ""
    elapsed: float = 0.0


class TaskSummary(BaseModel):
    """任务摘要（列表用，不含完整 result）。"""

    task_id: str
    status: Literal["pending", "running", "done", "failed"]
    description: str = ""
    created_at: float = 0.0
    elapsed: float = 0.0


class TaskListResponse(BaseModel):
    """任务摘要列表响应。"""

    tasks: list[TaskSummary]
    count: int


# ── 结果序列化 ─────────────────────────────────────────────────────────────────


def _clean_value(v: Any) -> Any:
    """把单个值转为 JSON 原生类型（递归处理容器）。"""
    # bool 必须先于 int 判断（bool 是 int 子类）
    if isinstance(v, bool):
        return v
    if isinstance(v, int | str):
        return v
    if isinstance(v, float):
        # NaN/Inf 无法 JSON 序列化，转 None
        import math

        return None if math.isnan(v) or math.isinf(v) else v
    if v is None:
        return v
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "item"):
        # numpy scalar → Python native（递归清洗，处理 numpy NaN）
        return _clean_value(v.item())
    if isinstance(v, dict):
        return {str(k): _clean_value(val) for k, val in v.items()}
    if isinstance(v, list | tuple):
        return [_clean_value(item) for item in v]
    # 兜底：不可序列化的对象转字符串
    return str(v)


def serialize_result(result: Any) -> dict[str, Any]:
    """把 BacktestResult（或其 to_dict()）清洗为纯 JSON 兼容字典。

    递归处理 performance / equity_curve / trades / positions / config，
    把 numpy scalar 转 Python 原生、NaN/Inf 转 None（JSON 不支持）、
    datetime 转 ISO 字符串。
    """
    if hasattr(result, "to_dict"):
        result = result.to_dict()
    return {str(k): _clean_value(v) for k, v in result.items()}
