#!/usr/bin/env python3
"""把 payload.json 签名后推送到看板的 /ingest。

用法:
    LAB_SYNC_URL=http://host/api/v1/ingest LAB_SYNC_SECRET=... python push.py [payload.json]

行为:
    - 网络错误或 5xx: 重试，最终仍失败时只给警告、退出码 0
      （服务器停机不应让各仓库 CI 变红，每日定时重推会补齐）。加 --strict 则退出码 1。
    - 4xx: 签名或数据有问题，不重试，退出码 1。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signing  # noqa: E402

RETRY_DELAYS = (2, 5, 10)


def post(url, secret, body, timeout=15):
    ts = str(int(time.time()))
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "User-Agent": "lab-sync",
        signing.HEADER_TS: ts,
        signing.HEADER_SIG: signing.sign(secret, ts, body),
    })
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
    if not url or not secret:
        _say("error", "缺少 LAB_SYNC_URL 或 LAB_SYNC_SECRET")
        return 1

    with open(args.path, "rb") as f:
        body = f.read()

    last = ""
    for attempt in range(len(RETRY_DELAYS) + 1):
        if attempt:
            time.sleep(RETRY_DELAYS[attempt - 1])
        try:
            status, text = post(url, secret, body)
            print(f"推送成功 ({status}): {text[:200]}")
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
