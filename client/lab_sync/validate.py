#!/usr/bin/env python3
"""校验 project.yaml（schema 1）。规范见 docs/project-yaml.md。

用法:
    python validate.py [project.yaml] [--check-venues]

退出码: 0 通过, 1 校验失败, 2 文件无法读取或解析。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
import urllib.request

import yaml

SCHEMA_VERSION = 1
STAGES = (
    "idea", "design", "implementation", "writing",
    "submitted", "revision", "accepted", "paused",
)
MILESTONE_STATUSES = ("todo", "doing", "done")
TARGET_ROLES = ("target", "backup", "submitted")
CCFDDL_URL = "https://ccfddl.com/conference/allconf.yml"

TOP_KEYS = {
    "schema", "title", "owner", "stage", "members", "tags",
    "summary", "milestones", "blockers", "targets",
}
LOGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
VENUE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,39}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_str(v, max_len):
    return isinstance(v, str) and 0 < len(v.strip()) <= max_len


def _date(v):
    """接受 YAML 日期或 'YYYY-MM-DD' 字符串，返回 ISO 字符串；不合法返回 None。"""
    if isinstance(v, dt.datetime):
        return None
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, str) and DATE_RE.match(v):
        try:
            return dt.date.fromisoformat(v).isoformat()
        except ValueError:
            return None
    return None


def _check_keys(item, allowed, where, errors):
    extra = sorted(set(item) - allowed)
    if extra:
        errors.append(f"{where}: 未知字段 {', '.join(map(str, extra))}")


def _check_list(doc, key, errors):
    """字段缺省视为空列表；存在则必须是列表。返回列表或 None（类型错误）。"""
    v = doc.get(key)
    if v is None:
        return []
    if not isinstance(v, list):
        errors.append(f"{key}: 必须是列表")
        return None
    return v


def validate(doc):
    """返回错误信息列表，空列表表示通过。"""
    errors = []
    if not isinstance(doc, dict):
        return ["文件顶层必须是键值映射"]

    _check_keys(doc, TOP_KEYS, "顶层", errors)

    if doc.get("schema") != SCHEMA_VERSION or isinstance(doc.get("schema"), bool):
        errors.append(f"schema: 必须是 {SCHEMA_VERSION}")
    if not _is_str(doc.get("title"), 120):
        errors.append("title: 必填，1–120 字符")
    if not (isinstance(doc.get("owner"), str) and LOGIN_RE.match(doc["owner"])):
        errors.append("owner: 必填，必须是 GitHub 用户名")
    if doc.get("stage") not in STAGES:
        errors.append(f"stage: 必须是 {' | '.join(STAGES)} 之一")
    if "summary" in doc and doc["summary"] is not None and not _is_str(doc["summary"], 300):
        errors.append("summary: 最多 300 字符，且不能为空字符串")

    members = _check_list(doc, "members", errors)
    for i, m in enumerate(members or []):
        if not (isinstance(m, str) and LOGIN_RE.match(m)):
            errors.append(f"members[{i}]: 必须是 GitHub 用户名")
    if members and len(set(members)) != len(members):
        errors.append("members: 有重复项")

    tags = _check_list(doc, "tags", errors)
    for i, t in enumerate(tags or []):
        if not (isinstance(t, str) and TAG_RE.match(t)):
            errors.append(f"tags[{i}]: 只能用小写字母、数字、连字符，最多 32 字符")
    if tags and len(set(tags)) != len(tags):
        errors.append("tags: 有重复项")

    for i, m in enumerate(_check_list(doc, "milestones", errors) or []):
        where = f"milestones[{i}]"
        if not isinstance(m, dict):
            errors.append(f"{where}: 必须是键值映射")
            continue
        _check_keys(m, {"title", "status", "due"}, where, errors)
        if not _is_str(m.get("title"), 120):
            errors.append(f"{where}.title: 必填，1–120 字符")
        if m.get("status") not in MILESTONE_STATUSES:
            errors.append(f"{where}.status: 必须是 {' | '.join(MILESTONE_STATUSES)} 之一")
        if m.get("due") is not None and _date(m["due"]) is None:
            errors.append(f"{where}.due: 必须是 YYYY-MM-DD")

    for i, b in enumerate(_check_list(doc, "blockers", errors) or []):
        where = f"blockers[{i}]"
        if not isinstance(b, dict):
            errors.append(f"{where}: 必须是键值映射")
            continue
        _check_keys(b, {"text", "since", "owner"}, where, errors)
        if not _is_str(b.get("text"), 300):
            errors.append(f"{where}.text: 必填，1–300 字符")
        if b.get("since") is not None and _date(b["since"]) is None:
            errors.append(f"{where}.since: 必须是 YYYY-MM-DD")
        if b.get("owner") is not None and not (
            isinstance(b["owner"], str) and LOGIN_RE.match(b["owner"])
        ):
            errors.append(f"{where}.owner: 必须是 GitHub 用户名")

    seen = set()
    for i, t in enumerate(_check_list(doc, "targets", errors) or []):
        where = f"targets[{i}]"
        if not isinstance(t, dict):
            errors.append(f"{where}: 必须是键值映射")
            continue
        _check_keys(t, {"venue", "journal", "planned", "role"}, where, errors)
        if t.get("role") not in TARGET_ROLES:
            errors.append(f"{where}.role: 必须是 {' | '.join(TARGET_ROLES)} 之一")
        has_venue, has_journal = "venue" in t, "journal" in t
        if has_venue == has_journal:
            errors.append(f"{where}: venue（会议）和 journal（期刊）必须二选一")
            continue
        if has_venue:
            if not (isinstance(t["venue"], str) and VENUE_RE.match(t["venue"])):
                errors.append(f"{where}.venue: 必须是 ccfddl.com 的会议 id，如 crypto27")
                continue
            if "planned" in t:
                errors.append(f"{where}: 会议日期由看板自动同步，不能写 planned")
            key = ("venue", t["venue"])
        else:
            if not _is_str(t["journal"], 120):
                errors.append(f"{where}.journal: 必填，1–120 字符")
                continue
            if _date(t.get("planned")) is None:
                errors.append(f"{where}.planned: 期刊必须写预计投稿日期 YYYY-MM-DD")
            key = ("journal", t["journal"].strip().lower())
        if key in seen:
            errors.append(f"{where}: 与前面的条目重复")
        seen.add(key)

    return errors


def normalize(doc):
    """把已通过校验的文档转成可 JSON 序列化的规范形式（补默认值、日期转字符串）。"""
    def clean(item, date_keys):
        out = {k: v for k, v in item.items() if v is not None}
        for k in date_keys:
            if k in out:
                out[k] = _date(out[k])
        return out

    out = {
        "schema": SCHEMA_VERSION,
        "title": doc["title"].strip(),
        "owner": doc["owner"],
        "stage": doc["stage"],
        "members": list(doc.get("members") or []),
        "tags": list(doc.get("tags") or []),
        "milestones": [clean(m, ("due",)) for m in doc.get("milestones") or []],
        "blockers": [clean(b, ("since",)) for b in doc.get("blockers") or []],
        "targets": [clean(t, ("planned",)) for t in doc.get("targets") or []],
    }
    if (doc.get("summary") or "").strip():
        out["summary"] = doc["summary"].strip()
    return out   # 规范形式本身也能通过 validate()，服务端据此复验


def load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_venue_ids(url=CCFDDL_URL, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "lab-sync"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = yaml.safe_load(resp.read())
    return {
        str(conf["id"])
        for entry in data or []
        for conf in entry.get("confs") or []
        if conf.get("id")
    }


def _series(venue_id):
    return venue_id.rstrip("0123456789")


def check_venues(doc, known_ids):
    """返回 (errors, warnings)。

    id 不存在但同系列的往届存在（如 crypto27 未公布而 crypto26 存在）只给警告：
    下一届还没被 ccfddl 收录，标注「待定」。系列也对不上才算错误，标注「未找到会议」。
    """
    known_series = {_series(i) for i in known_ids}
    errors, warnings = [], []
    for i, t in enumerate(doc.get("targets") or []):
        if not (isinstance(t, dict) and "venue" in t) or t["venue"] in known_ids:
            continue
        vid = t["venue"]
        if _series(vid) != vid and _series(vid) in known_series:
            warnings.append(f"targets[{i}].venue: 待定 — ccfddl.com 尚未收录 {vid}，日期公布前看板显示「待定」")
        else:
            errors.append(f"targets[{i}].venue: 未找到会议 — ccfddl.com 上没有 {vid}，也没有同系列的往届")
    return errors, warnings


def _annotate(level, path, msg):
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::{level} file={path}::{msg}")
    else:
        print(f"{'错误' if level == 'error' else '警告'}: {msg}", file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description="校验 project.yaml")
    ap.add_argument("path", nargs="?", default="project.yaml")
    ap.add_argument("--check-venues", action="store_true",
                    help="联网到 ccfddl.com 核对会议 id 是否存在")
    args = ap.parse_args(argv)

    try:
        doc = load(args.path)
    except (OSError, yaml.YAMLError) as e:
        _annotate("error", args.path, f"无法读取或解析: {e}")
        return 2

    errors = validate(doc)
    if not errors and args.check_venues:
        try:
            errors, warnings = check_venues(doc, fetch_venue_ids())
            for w in warnings:
                _annotate("warning", args.path, w)
        except Exception as e:  # 网络问题不应让 CI 变红
            _annotate("warning", args.path, f"无法访问 ccfddl.com，跳过会议 id 核对: {e}")

    for e in errors:
        _annotate("error", args.path, e)
    if errors:
        return 1
    print(f"{args.path}: 校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
