"""Dependency injection for Web API routers."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from easy_tdx.client import AsyncTdxClient


def _is_connected(client: Any) -> bool:
    conn = getattr(client, "_conn", None)
    writer = getattr(conn, "_writer", None)
    if writer is None:
        return False
    try:
        return not writer.is_closing()
    except Exception:
        return False


async def get_client(request: Request) -> AsyncTdxClient:
    """从 app.state 获取共享的 AsyncTdxClient 实例。"""
    from easy_tdx.exceptions import TdxConnectionError

    client: AsyncTdxClient = request.app.state.tdx_client
    try:
        if not _is_connected(client):
            await client.connect()
    except Exception as e:
        raise TdxConnectionError(str(e)) from e
    return client


async def get_mac_client(request: Request) -> Any:
    """从 app.state 获取共享的 AsyncMacClient 实例。"""
    from easy_tdx.exceptions import TdxConnectionError

    client: Any | None = request.app.state.mac_client
    if client is None:
        raise TdxConnectionError("MAC 客户端未连接")
    try:
        if not _is_connected(client):
            await client.connect()
    except Exception as e:
        raise TdxConnectionError(str(e)) from e
    return client


async def get_ex_client(request: Request) -> Any:
    """从 app.state 获取共享的 AsyncExTdxClient 实例（可选）。"""
    client: Any | None = request.app.state.ex_client
    if client is None:
        from easy_tdx.exceptions import TdxConnectionError

        raise TdxConnectionError("扩展市场客户端未启用")
    try:
        if not _is_connected(client):
            await client.connect()
    except Exception as e:
        from easy_tdx.exceptions import TdxConnectionError

        raise TdxConnectionError(str(e)) from e
    return client
