"""推送签名协议（客户端与服务端共用同一套规则）。

请求头:
    X-Lab-Timestamp: Unix 秒
    X-Lab-Signature: sha256=<hex>

签名 = HMAC-SHA256(secret, "<timestamp>." + 原始请求体字节)
服务端拒绝时间戳偏差超过 MAX_SKEW 秒的请求，用于防重放。
"""
from __future__ import annotations

import hashlib
import hmac
import time

MAX_SKEW = 300
HEADER_TS = "X-Lab-Timestamp"
HEADER_SIG = "X-Lab-Signature"


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify(secret: str, timestamp: str, body: bytes, signature: str,
           now: float | None = None, max_skew: int = MAX_SKEW) -> bool:
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs((time.time() if now is None else now) - ts) > max_skew:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature or "")
