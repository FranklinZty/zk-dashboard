"""用 GitHub Actions 的 OIDC 身份令牌认证推送。

工作流向 GitHub 申请一张身份令牌（JWT），GitHub 用自己的私钥签名，里面写明这次运行来自
哪个仓库、哪个组织、哪个提交。服务端用 GitHub 公开的公钥验证签名，再检查:

    - 签发方是 GitHub Actions
    - 令牌的 audience 等于 "zk-dashboard:" + sha256(请求体)，把令牌绑定到这一次的推送内容，
      HTTP 传输中被改动的请求体会对不上
    - 仓库所属的组织或用户在允许名单里（LAB_ALLOWED_OWNERS）
    - 调用方（app.py）再核对令牌里的仓库和提交与 payload 一致，仓库只能更新自己的项目

不需要任何共享密钥。
"""
from __future__ import annotations

import ssl

import jwt

import signing

ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = ISSUER + "/.well-known/jwks"
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "repository", "repository_owner", "sha"]


class OIDCError(Exception):
    """令牌无效或不被允许，对应 401/403。"""


class Unavailable(Exception):
    """暂时拿不到 GitHub 的公钥，对应 503，客户端会重试。"""


_client = None


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _signing_key(token):
    """按令牌头里的 kid 取 GitHub 公钥。公钥集缓存 1 小时，遇到没见过的 kid 会重新拉取。"""
    global _client
    if _client is None:
        _client = jwt.PyJWKClient(JWKS_URL, cache_jwk_set=True, lifespan=3600,
                                  timeout=15, ssl_context=_ssl_context())
    return _client.get_signing_key_from_jwt(token).key


def verify(token, body, allowed_owners):
    """验证令牌并返回其中的声明（claims）。失败抛 OIDCError 或 Unavailable。"""
    try:
        key = _signing_key(token)
    except jwt.PyJWKClientConnectionError as e:
        raise Unavailable(f"暂时无法获取 GitHub 公钥: {e}")
    except (jwt.PyJWKClientError, jwt.DecodeError) as e:
        raise OIDCError(f"身份令牌无效: {e}")
    try:
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=ISSUER,
                            audience=signing.oidc_audience(body), leeway=60,
                            options={"require": REQUIRED_CLAIMS})
    except jwt.InvalidAudienceError:
        raise OIDCError("身份令牌与请求内容不匹配（请求体可能被改动）")
    except jwt.InvalidTokenError as e:
        raise OIDCError(f"身份令牌无效: {e}")
    owners = {o.strip().lower() for o in allowed_owners if o.strip()}
    if str(claims["repository_owner"]).lower() not in owners:
        raise OIDCError(f"{claims['repository_owner']} 不在看板允许的组织名单里")
    return claims
