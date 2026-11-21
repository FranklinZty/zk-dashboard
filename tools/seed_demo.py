#!/usr/bin/env python3
"""往本地开发服务器灌入几条示例项目，用来看页面效果。不要对正式服务器运行。

用法:
    LAB_SYNC_SECRET=dev-secret python tools/seed_demo.py [http://127.0.0.1:8765]
"""
import datetime as dt
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client", "lab_sync"))
import push  # noqa: E402

CST = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime.now(CST)
day = lambda n: (NOW.date() + dt.timedelta(days=n)).isoformat()

DEMO = [
    ("folding-updatable", 2, dict(
        title="可更新证明的折叠方案", owner="alice", stage="implementation",
        members=["bob", "carol"], tags=["zk", "folding"], summary="用折叠方案降低可更新证明的更新开销。",
        milestones=[{"title": "完成基线复现", "status": "done", "due": day(-20)},
                    {"title": "主实验", "status": "doing", "due": day(5)},
                    {"title": "初稿", "status": "todo", "due": day(-2)}],
        targets=[{"venue": "pets27", "role": "target"}, {"venue": "uss27", "role": "backup"}])),
    ("anon-blocklist", 9, dict(
        title="匿名黑名单协议", owner="bob", stage="writing", members=["alice"], tags=["zk", "privacy"],
        blockers=[{"text": "等待合作方提供评测数据", "since": day(-12), "owner": "bob"}],
        targets=[{"venue": "eurosp27", "role": "target"},
                 {"journal": "IEEE TIFS", "planned": day(110), "role": "backup"}])),
    ("range-proof-opt", 18, dict(
        title="范围证明优化", owner="carol", stage="design", tags=["range-proof"],
        targets=[{"venue": "crypto27", "role": "target"}])),
    ("lookup-zkvm", 4, dict(
        title="zkVM 中的查找表论证", owner="dave", stage="idea", members=["carol"], tags=["zkvm", "lookup"],
        targets=[{"journal": "Journal of Cryptology", "planned": day(10), "role": "target"}])),
    ("malleable-pcd", 30, dict(
        title="可延展 PCD", owner="alice", stage="submitted", members=["dave"], tags=["pcd"],
        targets=[{"venue": "eurocrypt27", "role": "submitted"}])),
    ("gpu-msm", 12, dict(
        title="GPU 上的 MSM 加速", owner="erin", stage="implementation", tags=["gpu", "msm"],
        blockers=[{"text": "GPU 配额不足"}], targets=[{"venue": "uss27", "role": "target"}])),
    ("old-survey", 60, dict(title="折叠方案综述", owner="erin", stage="paused", tags=["survey"])),
]


def main():
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765").rstrip("/")
    if not any(h in base for h in ("127.0.0.1", "localhost")):
        sys.exit("只允许对本机地址运行")
    secret = os.environ.get("LAB_SYNC_SECRET", "")
    for pid, days_ago, project in DEMO:
        at = (NOW - dt.timedelta(days=days_ago)).isoformat(timespec="seconds")
        sha = hashlib.sha1(f"{pid}{days_ago}".encode()).hexdigest()
        body = json.dumps({
            "payload_version": 1, "project_id": pid, "repo": f"demo-lab/{pid}", "sha": sha,
            "source": "manual", "last_commit_at": at, "project": {"schema": 1, **project},
            "commits": [{"sha": sha, "author": project["owner"], "date": at, "message": "示例提交"}],
        }, ensure_ascii=False).encode()
        status, text = push.post(base + "/api/v1/ingest", secret, body)
        print(pid, status, text)


if __name__ == "__main__":
    main()
