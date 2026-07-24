"""同花顺问财语义搜索路由（独立数据源，不依赖 TDX 服务器）。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from easy_tdx.web.schemas import WencaiSearchRequest, WencaiSearchResponse, WencaiStockItem

router = APIRouter(tags=["wencai"])


@router.post("/wencai/search", response_model=WencaiSearchResponse)
async def wencai_search(req: WencaiSearchRequest) -> WencaiSearchResponse:
    """执行同花顺问财语义搜索并返回股票列表。"""
    from easy_tdx.wencai import WencaiClient, WencaiError

    client = WencaiClient(cookie=req.cookie)

    def _fetch() -> WencaiSearchResponse:
        stocks = client.search(
            req.query,
            perpage=req.perpage,
        )
        return WencaiSearchResponse(
            data=[
                WencaiStockItem(
                    symbol=s.symbol,
                    market=s.market,
                    name=s.name,
                    stock_reason=s.stock_reason,
                )
                for s in stocks
            ],
            count=len(stocks),
        )

    try:
        return await asyncio.to_thread(_fetch)
    except WencaiError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
