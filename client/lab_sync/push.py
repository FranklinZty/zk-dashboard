#!/usr/bin/env python3
"""把 payload.json 推送到看板的 /ingest。

认证方式（见 signing.py）:
    - 在 GitHub Actions 里且工作流有 id-token: write 权限: 用 GitHub 身份令牌，不需要密钥
    - 否则用环境变量 LAB_SYNC_SECRET 做 HMAC 签名（本地开发）

用法:
    LAB_SYNC_URL=http://host/api/v1/ingest python push.py [payload.json]

行为:
    - 网络错误或 5xx: 重试，最终仍失败时只给警告、退出码 0
      （服务器停机不应让各仓库 CI 变红，每日定时重推会补齐）。加 --strict 则退出码 1。
    - 4xx: 签名或数据有问题，不重试，退出码 1。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signing  # noqa: E402

RETRY_DELAYS = (2, 5, 10)


def oidc_available():
    return bool(os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
                and os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN"))


def oidc_token(body, timeout=15):
    """向 GitHub 申请绑定到这次推送内容的身份令牌。"""
    url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    url += ("&" if "?" in url else "?") + urllib.parse.urlencode(
        {"audience": signing.oidc_audience(body)})
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"],
        "Accept": "application/json", "User-Agent": "lab-sync"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)["value"]


def auth_headers(body, secret=None):
    """返回 (认证方式说明, 请求头)。"""
    if oidc_available():
        return "GitHub 身份认证", {"Authorization": "Bearer " + oidc_token(body)}
    if secret:
        ts = str(int(time.time()))
        return "共享密钥签名", {signing.HEADER_TS: ts, signing.HEADER_SIG: signing.sign(secret, ts, body)}
    raise RuntimeError("无法认证：工作流缺少 `permissions: id-token: write`，也没有设置 LAB_SYNC_SECRET")


def post(url, body, headers, timeout=15):
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": "lab-sync", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def _say(level, msg):
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::{level}::{msg}")
    else:
        print(f"{level}: {msg}", file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description="推送 payload 到看板")
    ap.add_argument("path", nargs="?", default="payload.json")
    ap.add_argument("--strict", action="store_true", help="服务器不可达时也让 CI 失败")
    args = ap.parse_args(argv)

    url = os.environ.get("LAB_SYNC_URL", "").strip()
    secret = os.environ.get("LAB_SYNC_SECRET", "").strip()
    if not url:
        _say("error", "缺少 LAB_SYNC_URL")
        return 1
    if not oidc_available() and not secret:
        _say("error", "无法认证：工作流缺少 `permissions: id-token: write`，也没有设置 LAB_SYNC_SECRET")
        return 1

    with open(args.path, "rb") as f:
        body = f.read()

    last = ""
    for attempt in range(len(RETRY_DELAYS) + 1):
        if attempt:
            time.sleep(RETRY_DELAYS[attempt - 1])
        try:
            method, headers = auth_headers(body, secret)
            status, text = post(url, body, headers)
            print(f"推送成功 ({status}，{method}): {text[:200]}")
            return 0
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            if 400 <= e.code < 500:
                _say("error", f"看板拒绝了推送 ({e.code}): {detail}")
                return 1
            last = f"HTTP {e.code}: {detail}"
        except (urllib.error.URLError, OSError) as e:
            last = str(e)
        print(f"第 {attempt + 1} 次推送失败: {last}", file=sys.stderr)

    _say("warning", f"看板暂时不可达，本次未送达，等待定时重推补齐: {last}")
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
