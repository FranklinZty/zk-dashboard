#!/usr/bin/env python3
"""临时接收端：在正式服务端写好之前，用来验证推送链路。

验签规则与正式 /ingest 相同；通过后把 payload 存到 received/ 目录并打印摘要。
只依赖标准库。

用法:
    LAB_SYNC_SECRET=... python tools/dev_receiver.py [--port 8787] [--out received]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "client", "lab_sync"))
import signing  # noqa: E402

INGEST_PATH = "/api/v1/ingest"
MAX_BODY = 1_000_000


def make_handler(secret, out_dir):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code, obj):
            data = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path != INGEST_PATH:
                return self._reply(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_BODY:
                return self._reply(413, {"error": "bad length"})
            body = self.rfile.read(length)
            if not signing.verify(secret, self.headers.get(signing.HEADER_TS),
                                  body, self.headers.get(signing.HEADER_SIG)):
                return self._reply(401, {"error": "bad signature or timestamp"})
            try:
                payload = json.loads(body)
                pid, sha = payload["project_id"], payload["sha"]
            except (ValueError, KeyError, TypeError):
                return self._reply(400, {"error": "bad payload"})

            name = f"{os.path.basename(str(pid))}-{os.path.basename(str(sha))[:12]}.json"
            path = os.path.join(out_dir, name)
            duplicate = os.path.exists(path)   # 与正式端一致：同一 (project_id, sha) 幂等
            if not duplicate:
                with open(path, "wb") as f:
                    f.write(body)
            print(f"收到 {pid} @ {str(sha)[:7]}  stage={payload['project'].get('stage')}"
                  f"  commits={len(payload.get('commits', []))}"
                  f"  {'(重复，已忽略)' if duplicate else ''}", flush=True)
            self._reply(200, {"ok": True, "duplicate": duplicate})

        def log_message(self, *args):
            pass

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description="lab-sync 临时接收端")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--out", default="received")
    args = ap.parse_args(argv)

    secret = os.environ.get("LAB_SYNC_SECRET", "").strip()
    if not secret:
        print("缺少 LAB_SYNC_SECRET", file=sys.stderr)
        return 1
    os.makedirs(args.out, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(secret, args.out))
    print(f"监听 http://{args.host}:{args.port}{INGEST_PATH}，payload 存到 {args.out}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
