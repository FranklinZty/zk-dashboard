"""推送认证协议（客户端与服务端共用同一套规则）。两种方式，服务端都接受:

1. GitHub 身份令牌（推荐，GitHub Actions 里默认使用）:
    Authorization: Bearer <GitHub Actions 签发的 OIDC 令牌>
    令牌的 audience 必须是 oidc_audience(请求体)，把令牌绑定到这次推送的内容。
    服务端验证见 server/oidc.py。不需要任何共享密钥。

2. 共享密钥 HMAC（本地开发和测试用；服务端配置了 LAB_SYNC_SECRET 才接受）:
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
OIDC_AUDIENCE_PREFIX = "zk-dashboard:"
HEADER_TS = "X-Lab-Timestamp"
HEADER_SIG = "X-Lab-Signature"


def oidc_audience(body: bytes) -> str:
    return OIDC_AUDIENCE_PREFIX + hashlib.sha256(body).hexdigest()


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
