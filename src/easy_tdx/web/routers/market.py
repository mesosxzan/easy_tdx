"""市场信息路由：证券列表、实时行情、市场统计、资金流向。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from easy_tdx.web.convert import market_from_str
from easy_tdx.web.deps import get_client
from easy_tdx.web.schemas import (
    CountResponse,
    DataFrameResponse,
    QuoteRequest,
)

router = APIRouter(tags=["market"])


def _df_response(df: Any) -> DataFrameResponse:
    """将 DataFrame 转为 API 响应。"""
    return DataFrameResponse.from_dataframe(df)


@router.get("/security/count", response_model=CountResponse)
async def security_count(
    market: str = Query(..., description="市场: SZ, SH, BJ"),
    client: Any = Depends(get_client),
) -> CountResponse:
    """获取市场证券总数。"""
    count = await client.get_security_count(market_from_str(market))
    return CountResponse(count=count)


@router.get("/security/list", response_model=DataFrameResponse)
async def security_list(
    market: str = Query(..., description="市场: SZ, SH, BJ"),
    start: int = Query(0, ge=0, description="分页起始位置"),
    client: Any = Depends(get_client),
) -> DataFrameResponse:
    """获取证券列表（每页约1000条）。"""
    df = await client.get_security_list(market_from_str(market), start)
    return _df_response(df)


@router.get("/security/list-all", response_model=DataFrameResponse)
async def security_list_all(
    pages: int = Query(1, ge=1, description="拉取页数（每个市场每页1000条）"),
    client: Any = Depends(get_client),
) -> DataFrameResponse:
    """获取沪深 A 股完整列表。"""
    df = await client.get_security_list_all(pages=pages)
    return _df_response(df)


# ── 股票搜索索引（声母检索） ───────────────────────────────────────────────────
# 进程级缓存：5206 条记录的 {code, name, initials} 只算一次，热重启即丢。
# 前端拉一次后模块级缓存，按 code/name.includes/initials.includes 三路过滤。
_SEARCH_INDEX: list[dict[str, str]] | None = None
# 单飞 Future：预热（lifespan）与端点请求共享同一任务，避免并发爬两次全名单。
# 首次请求会 await 这个 Future；后续请求命中 _SEARCH_INDEX 直接返回。
_SEARCH_INDEX_TASK: Any = None  # asyncio.Future[list[dict[str, str]]]


async def _build_search_index(client: Any) -> list[dict[str, str]]:
    """构建搜索索引：拉全名单 + pypinyin 预计算声母。耗时几十秒（首次）。"""
    import asyncio

    global _SEARCH_INDEX, _SEARCH_INDEX_TASK
    # 双重检查：等待期间可能已被其他协程填好
    if _SEARCH_INDEX is not None:
        return _SEARCH_INDEX
    # 单飞：已有进行中的任务则复用，避免预热 + 端点请求并发爬两次
    if _SEARCH_INDEX_TASK is None:
        _SEARCH_INDEX_TASK = asyncio.get_running_loop().create_future()

        async def _do_build() -> None:
            global _SEARCH_INDEX, _SEARCH_INDEX_TASK
            from pypinyin import Style, lazy_pinyin

            try:
                df = await client.get_security_list_all(pages="all")
                index: list[dict[str, str]] = []
                for row in df.itertuples(index=False):
                    name = str(getattr(row, "name", "") or "")
                    if not name:
                        continue
                    code = str(getattr(row, "code", ""))
                    initials = "".join(lazy_pinyin(name, style=Style.FIRST_LETTER))
                    index.append({"code": code, "name": name, "initials": initials})
                _SEARCH_INDEX = index
                if not _SEARCH_INDEX_TASK.done():
                    _SEARCH_INDEX_TASK.set_result(index)
            except Exception as e:
                # 失败清空 task，允许下次重试
                if not _SEARCH_INDEX_TASK.done():
                    _SEARCH_INDEX_TASK.set_exception(e)
                _SEARCH_INDEX_TASK = None
                raise
            finally:
                # 成功后清空 task 引用（结果已存 _SEARCH_INDEX）
                _SEARCH_INDEX_TASK = None

        asyncio.create_task(_do_build())

    from typing import cast

    return cast("list[dict[str, str]]", await _SEARCH_INDEX_TASK)


@router.get("/security/search-index")
async def security_search_index(
    client: Any = Depends(get_client),
) -> dict[str, Any]:
    """返回股票搜索索引 ``[{code, name, initials}]``（供前端声母/代码/名字搜索）。

    数据源复用 :meth:`get_security_list_all`（沪深 A 股，已有本地日级缓存）。
    声母用 pypinyin ``FIRST_LETTER`` 预计算（如 中际旭创→zjxc）。
    进程内缓存，首次请求算一次后常驻；强制刷新重启进程即可。

    与 lifespan 预热共享同一单飞任务（``_build_search_index``），
    避免预热和首次端点请求并发各爬一次全名单。
    """
    if _SEARCH_INDEX is not None:
        return {"count": len(_SEARCH_INDEX), "data": _SEARCH_INDEX}
    index = await _build_search_index(client)
    return {"count": len(index), "data": index}


@router.post("/quotes", response_model=DataFrameResponse)
async def security_quotes(
    req: QuoteRequest,
    client: Any = Depends(get_client),
) -> DataFrameResponse:
    """批量获取实时五档行情（最多80只/次）。"""
    stocks_parsed: list[tuple[Any, str]] = []
    for s in req.stocks:
        m = market_from_str(s.market)
        stocks_parsed.append((m, s.code))
    df = await client.get_security_quotes(stocks_parsed)
    return _df_response(df)


@router.get("/market/stat", response_model=DataFrameResponse)
async def market_stat(
    client: Any = Depends(get_client),
) -> DataFrameResponse:
    """获取 A 股全市场涨跌统计。"""
    df = await client.get_market_stat()
    return _df_response(df)


@router.get("/fund-flow", response_model=DataFrameResponse)
async def fund_flow(
    market: str = Query(..., description="市场: SZ, SH"),
    code: str = Query(..., min_length=6, max_length=6, description="6位股票代码"),
    client: Any = Depends(get_client),
) -> DataFrameResponse:
    """获取个股当日资金流向。"""
    df = await client.get_fund_flow(market_from_str(market), code)
    return _df_response(df)


@router.get("/fund-flow/history", response_model=DataFrameResponse)
async def history_fund_flow(
    market: str = Query(..., description="市场: SZ, SH"),
    code: str = Query(..., min_length=6, max_length=6, description="6位股票代码"),
    start: int = Query(0, ge=0),
    count: int = Query(100, ge=1, le=800),
    client: Any = Depends(get_client),
) -> DataFrameResponse:
    """获取个股历史日线资金流向。"""
    df = await client.get_history_fund_flow(market_from_str(market), code, start, count)
    return _df_response(df)


@router.get("/market/strength", response_model=DataFrameResponse)
async def market_strength(
    preset: str = Query(
        "steady",
        description="预设模式: steady(中长期稳健) / breakout(近期妖股) / balanced(均衡)",
    ),
    w5: float | None = Query(None, description="自定义 5 日权重（覆盖预设）"),
    w20: float | None = Query(None, description="自定义 20 日权重（覆盖预设）"),
    w60: float | None = Query(None, description="自定义 60 日权重（覆盖预设）"),
    vol_adjusted: bool | None = Query(None, description="波动率惩罚开关（覆盖预设）"),
    top_n: int = Query(50, ge=1, le=5000, description="返回前 N 名"),
    universe: str = Query("all", description="范围: all/sh/sz"),
    min_listed_days: int = Query(65, ge=30, description="最小上市天数"),
    min_amount: float = Query(0.0, ge=0, description="最近 5 日日均成交额下限（元）"),
    vipdoc: str | None = Query(None, description="离线数据目录（默认自动检测）"),
) -> DataFrameResponse:
    """全市场强势股排名（基于本地通达信 .day 日线文件）。

    按 5/20/60 日涨幅加权合成强势分。三种预设：

    - **steady**: 中长期稳健（60日主导 + 波动率惩罚），选出稳着涨的票
    - **breakout**: 近期妖股爆发（5日主导，纯涨幅），选出短期最猛的票
    - **balanced**: 三周期均衡 + 波动率调整

    注意：需要本地 vipdoc 数据，扫描 ~5000 只约 30-60 秒。
    """
    import asyncio

    from easy_tdx.screen.strength import StrengthRanker

    ranker = StrengthRanker(
        vipdoc_path=vipdoc,
        preset=preset,
        w5=w5,
        w20=w20,
        w60=w60,
        vol_adjusted=vol_adjusted,
        min_listed_days=min_listed_days,
        min_amount=min_amount,
    )

    # Web 端用线程池执行，避免阻塞事件循环（扫描全市场耗时较长）
    # 注：在协程内用 get_running_loop() 而非 get_event_loop()，
    # 后者在 Python 3.12+ 已弃用。
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, lambda: ranker.rank(universe=universe, top_n=top_n))

    records = [
        {
            "rank": r.rank,
            "code": r.code,
            "market": r.market,
            "name": r.name,
            "last_close": r.last_close,
            "last_date": r.last_date,
            "ret_5": r.ret_5,
            "ret_20": r.ret_20,
            "ret_60": r.ret_60,
            "vol_20": r.vol_20,
            "strength": r.strength,
        }
        for r in results
    ]
    return DataFrameResponse(data=records, count=len(records))
