"""API 速率限制（SPEC §11）：Redis 固定視窗計數，多個 API 行程共用同一個計數。

登入（防暴力猜密碼）與 Agent 對話（每次都可能呼叫 LLM）各自限流。Redis 暫時不可用時放行並記錄，
不讓限流本身變成服務中斷的原因。
"""

from __future__ import annotations

import logging
import time

from fastapi import HTTPException, status
from redis.exceptions import RedisError

from app.events.bus import get_bus
from app.i18n import t

log = logging.getLogger(__name__)


def check(key: str, *, limit: int, window_s: int = 60) -> None:
    bucket = f"rl:{key}:{int(time.time()) // window_s}"
    client = get_bus().client
    try:
        count = int(client.incr(bucket))
        if count == 1:
            client.expire(bucket, window_s)
    except RedisError:
        log.warning("速率限制暫時無法計數（Redis 不可用），本次放行：%s", key)
        return
    if count > limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            t("auth.rate_limited"),
            headers={"Retry-After": str(window_s)},
        )
