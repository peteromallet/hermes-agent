"""Shared async utilities for bridging sync tool handlers to async coroutines.

This prevents cross-module private imports and duplication of the thread-pool
fallback logic.
"""

import asyncio
import concurrent.futures
import logging
from typing import Any, Awaitable, TypeVar, cast

logger = logging.getLogger(__name__)

T = TypeVar("T")


def run_async(coro: Awaitable[T]) -> T:
    """Run an async coroutine from a sync context.

    If the current thread already has a running event loop (e.g., inside
    the gateway's async stack or Atropos's event loop), we spin up a
    disposable thread so asyncio.run() can create its own loop without
    conflicting.

    This is the single source of truth for sync->async bridging in tool
    handlers.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Already inside an event loop -- create a new thread to run asyncio.run()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return cast(T, future.result(timeout=300))
    return asyncio.run(coro)
