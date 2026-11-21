#!/usr/bin/env python3
"""采集 project.yaml 和最近提交，生成推送用的 payload JSON。

用法:
    python collect.py [project.yaml] [-o payload.json] [--repo-dir .]

在 GitHub Actions 中从 GITHUB_* 环境变量取仓库信息；本地运行时退回到 git 命令。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import abstract  # noqa: E402
import validate as v  # noqa: E402

PAYLOAD_VERSION = 1
MAX_COMMITS = 10
CST = dt.timezone(dt.timedelta(hours=8))
SOURCES = {"push": "push", "schedule": "schedule", "workflow_dispatch": "manual"}


def _git(repo_dir, *args):
    return subprocess.run(
        ["git", "-C", repo_dir, *args],
        check=True, capture_output=True, text=True,
    ).stdout


def recent_commits(repo_dir, limit=MAX_COMMITS):
    fmt = "%H%x1f%an%x1f%cI%x1f%s%x1e"
    out = _git(repo_dir, "log", f"-n{limit}", f"--format={fmt}")
    commits = []
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, author, date, subject = rec.split("\x1f")
        commits.append({"sha": sha, "author": author, "date": date,
                        "message": subject[:200]})
    return commits


def repo_full_name(repo_dir):
    name = os.environ.get("GITHUB_REPOSITORY")
    if name:
        return name
    try:
        url = _git(repo_dir, "remote", "get-url", "origin").strip()
    except subprocess.CalledProcessError:
        url = ""
    if url:
        tail = url.removesuffix(".git").replace(":", "/").split("/")[-2:]
        return "/".join(tail)
    return "local/" + os.path.basename(os.path.abspath(repo_dir))


def build_payload(doc, repo_dir="."):
    repo = repo_full_name(repo_dir)
    commits = recent_commits(repo_dir)
    if not commits:
        raise RuntimeError("仓库里没有提交")
    text, source = abstract.describe(repo_dir, doc.get("abstract_file"))
    return {
        "abstract": text or "",              # 论文摘要；没有摘要时是 STATE.md 的 thesis。来源见 abstract_source
        "abstract_source": source or "",
        "payload_version": PAYLOAD_VERSION,
        "project_id": repo.split("/")[-1].lower(),
        "repo": repo,
        "sha": os.environ.get("GITHUB_SHA") or commits[0]["sha"],
        "ref": os.environ.get("GITHUB_REF", ""),
        "source": SOURCES.get(os.environ.get("GITHUB_EVENT_NAME", ""), "manual"),
        "collected_at": dt.datetime.now(CST).isoformat(timespec="seconds"),
        "last_commit_at": commits[0]["date"],
        "project": v.normalize(doc),
        "commits": commits,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成看板推送 payload")
    ap.add_argument("path", nargs="?", default="project.yaml")
    ap.add_argument("-o", "--output", default="payload.json")
    ap.add_argument("--repo-dir", default=".")
    args = ap.parse_args(argv)

    doc = v.load(args.path)
    errors = v.validate(doc)
    if errors:
        for e in errors:
            print(f"错误: {e}", file=sys.stderr)
        return 1

    payload = build_payload(doc, args.repo_dir)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if payload["abstract"]:
        kind = "摘要" if payload["abstract_source"].endswith(".tex") else "项目描述"
        print(f"{kind}取自 {payload['abstract_source']}（{len(payload['abstract'])} 字符）")
    elif doc.get("summary"):
        print("论文没有摘要，STATE.md 里也没有 thesis，看板将显示 project.yaml 里的 summary")
    else:
        msg = "没有论文摘要，STATE.md 里也没有 thesis；请在 project.yaml 里填写 summary"
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::warning file={args.path}::{msg}")
        else:
            print(f"警告: {msg}", file=sys.stderr)
    print(f"已生成 {args.output}: {payload['project_id']} @ {payload['sha'][:7]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
