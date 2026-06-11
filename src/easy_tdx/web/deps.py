"""Dependency injection for Web API routers."""

from __future__ import annotations

from fastapi import Request

from easy_tdx.client import AsyncTdxClient


def get_client(request: Request) -> AsyncTdxClient:
    """从 app.state 获取共享的 AsyncTdxClient 实例。"""
    client: AsyncTdxClient = request.app.state.tdx_client
    return client
