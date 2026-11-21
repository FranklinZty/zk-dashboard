"""课题组看板服务端。启动: uvicorn server.app:app

环境变量:
    LAB_SYNC_SECRET   必填，/ingest 的签名密钥
    LAB_DB_PATH       SQLite 路径，默认 ./lab.db
    LAB_VIEW_PASSWORD 访问密码；设置后所有页面和只读接口都要求 HTTP Basic 认证（用户名任意）
    LAB_CCFDDL_SYNC   设为 0 关闭后台的 ccfddl 定时同步（测试用）
    LAB_MANUAL_FILE   手工截稿文件，默认 server/deadlines.manual.yaml
"""
from __future__ import annotations

import base64
import datetime as dt
import hmac
import json
import logging
import os
import re
import sqlite3
import sys
import threading
from contextlib import asynccontextmanager, closing
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "client" / "lab_sync"))
import signing  # noqa: E402
import validate as pv  # noqa: E402

from . import ccfddl  # noqa: E402

log = logging.getLogger("lab")
CST = dt.timezone(dt.timedelta(hours=8))
MAX_BODY = 1_000_000
MAX_ABSTRACT = 6000
SYNC_INTERVAL = 24 * 3600
STALL_EXEMPT = {"submitted", "accepted", "paused"}   # 这些阶段不计入停滞
PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")


# ---------- 基础 ----------

def now_cst():
    return dt.datetime.now(CST)


def db_path():
    return os.environ.get("LAB_DB_PATH", "lab.db")


def connect():
    con = sqlite3.connect(db_path(), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db():
    with closing(connect()) as con:
        con.executescript((HERE / "schema.sql").read_text(encoding="utf-8"))
        # 旧库升级：CREATE TABLE IF NOT EXISTS 不会给已有的表加列
        columns = {r["name"] for r in con.execute("PRAGMA table_info(projects)")}
        if "abstract" not in columns:
            con.execute("ALTER TABLE projects ADD COLUMN abstract TEXT NOT NULL DEFAULT ''")
        con.commit()


def parse_ts(text):
    t = dt.datetime.fromisoformat(text)
    return t if t.tzinfo else t.replace(tzinfo=CST)


def sync_deadlines(fetch=True):
    """拉取 ccfddl 并合并手工文件。拉取失败时保留旧数据，只刷新手工条目。"""
    data = None
    if fetch:
        try:
            data = ccfddl.fetch()
        except Exception as e:
            log.warning("ccfddl 同步失败，保留旧数据: %s", e)
    manual_path = Path(os.environ.get("LAB_MANUAL_FILE", HERE / "deadlines.manual.yaml"))
    manual = yaml.safe_load(manual_path.read_text(encoding="utf-8")) if manual_path.exists() else None
    with closing(connect()) as con:
        result = ccfddl.apply(con, data, manual)
    if result["unknown_tz"]:
        log.warning("这些会议的时区认不出，日期显示为待定: %s", result["unknown_tz"])
    log.info("截稿同步完成: ccfddl %d 条，手工 %d 条", result["ccfddl"], result["manual"])
    return result


def _sync_loop(stop):
    while not stop.is_set():
        try:
            sync_deadlines()
        except Exception:
            log.exception("截稿同步出错")
        stop.wait(SYNC_INTERVAL)


@asynccontextmanager
async def lifespan(app):
    init_db()
    stop = threading.Event()
    if os.environ.get("LAB_CCFDDL_SYNC", "1") != "0":
        threading.Thread(target=_sync_loop, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="lab dashboard", lifespan=lifespan, docs_url=None, redoc_url=None)


# ---------- 访问密码 ----------

OPEN_PATHS = {"/api/v1/ingest", "/robots.txt"}   # ingest 有自己的签名校验


@app.middleware("http")
async def view_auth(request: Request, call_next):
    password = os.environ.get("LAB_VIEW_PASSWORD", "")
    if not password or request.url.path in OPEN_PATHS:
        return await call_next(request)
    given = None
    header = request.headers.get("authorization", "")
    if header[:6].lower() == "basic ":
        try:
            given = base64.b64decode(header[6:]).decode("utf-8").partition(":")[2]
        except ValueError:
            given = None
    if given is None or not hmac.compare_digest(given.encode(), password.encode()):
        return Response("需要访问密码", status_code=401, media_type="text/plain; charset=utf-8",
                        headers={"WWW-Authenticate": 'Basic realm="lab dashboard", charset="UTF-8"'})
    return await call_next(request)


# ---------- 写入：只有这一个入口 ----------

@app.post("/api/v1/ingest")
async def ingest(request: Request):
    secret = os.environ.get("LAB_SYNC_SECRET", "")
    if not secret:
        raise HTTPException(503, "服务端未配置 LAB_SYNC_SECRET")
    body = await request.body()
    if not 0 < len(body) <= MAX_BODY:
        raise HTTPException(413, "请求体大小不合法")
    if not signing.verify(secret, request.headers.get(signing.HEADER_TS), body,
                          request.headers.get(signing.HEADER_SIG)):
        raise HTTPException(401, "签名或时间戳无效")

    try:
        p = json.loads(body)
        pid, sha, repo = p["project_id"], p["sha"], p["repo"]
        project, commits = p["project"], p["commits"]
        last_commit_at = parse_ts(p["last_commit_at"]).isoformat(timespec="seconds")
        abstract = p.get("abstract") or ""
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(400, "payload 结构不正确")
    if not (isinstance(pid, str) and PROJECT_ID_RE.match(pid)
            and isinstance(sha, str) and SHA_RE.match(sha)
            and isinstance(repo, str) and isinstance(commits, list)
            and isinstance(abstract, str)):
        raise HTTPException(400, "payload 字段不合法")
    errors = pv.validate(project)
    if errors:
        raise HTTPException(400, "project 校验失败: " + "; ".join(errors[:5]))

    now_iso = now_cst().isoformat(timespec="seconds")
    with closing(connect()) as con:
        cur = con.execute(
            "INSERT OR IGNORE INTO snapshots (project_id, sha, payload_json, received_at, source) "
            "VALUES (?,?,?,?,?)",
            (pid, sha, body.decode("utf-8"), now_iso, str(p.get("source") or "manual")[:20]))
        duplicate = cur.rowcount == 0

        row = con.execute("SELECT last_commit_at FROM projects WHERE id=?", (pid,)).fetchone()
        stale = row is not None and parse_ts(row["last_commit_at"]) > parse_ts(last_commit_at)
        if not stale:   # 迟到的旧提交只留快照，不覆盖当前状态
            dump = lambda v: json.dumps(v, ensure_ascii=False)
            con.execute(
                "INSERT INTO projects (id, title, owner, stage, summary, abstract, members_json, tags_json, "
                "milestones_json, blockers_json, commits_json, repo, last_sha, last_commit_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "title=excluded.title, owner=excluded.owner, stage=excluded.stage, summary=excluded.summary, "
                "abstract=excluded.abstract, "
                "members_json=excluded.members_json, tags_json=excluded.tags_json, "
                "milestones_json=excluded.milestones_json, blockers_json=excluded.blockers_json, "
                "commits_json=excluded.commits_json, repo=excluded.repo, last_sha=excluded.last_sha, "
                "last_commit_at=excluded.last_commit_at, updated_at=excluded.updated_at",
                (pid, project["title"], project["owner"], project["stage"], project.get("summary", ""),
                 abstract[:MAX_ABSTRACT],
                 dump(project.get("members") or []), dump(project.get("tags") or []),
                 dump(project.get("milestones") or []), dump(project.get("blockers") or []),
                 dump(commits[:20]), repo, sha, last_commit_at, now_iso))
            con.execute("DELETE FROM project_targets WHERE project_id=?", (pid,))
            for pos, t in enumerate(project.get("targets") or []):
                kind = "venue" if "venue" in t else "journal"
                planned = t.get("planned")
                con.execute(
                    "INSERT INTO project_targets (project_id, kind, ref, planned, role, pos) VALUES (?,?,?,?,?,?)",
                    (pid, kind, t[kind].strip(), str(planned) if planned else None, t["role"], pos))
        con.commit()
    return {"ok": True, "duplicate": duplicate, "stale": stale}


# ---------- 读取 ----------

def load_deadlines(con):
    out = {r["id"]: {**dict(r), "rounds": []} for r in con.execute("SELECT * FROM deadlines")}
    for r in con.execute("SELECT * FROM deadline_rounds ORDER BY deadline_id, idx"):
        if r["deadline_id"] in out:
            out[r["deadline_id"]]["rounds"].append(dict(r))
    return out


def days_between(later, earlier):
    return (later - earlier).days   # 向下取整：不足 24 小时算 0 天


_VENUE_ID_RE = re.compile(r"^(.*?)(\d{4}|\d{2})$")


def guess_venue_name(venue_id, deadlines):
    """数据源还没收录的届次，从同系列的往届推出名称: crypto27 + 'CRYPTO 2026' -> 'CRYPTO 2027'。"""
    m = _VENUE_ID_RE.match(venue_id)
    if not m:
        return venue_id
    series, year = m.groups()
    sibling = re.compile(re.escape(series) + r"(\d{4}|\d{2})")
    for did, d in deadlines.items():
        if sibling.fullmatch(did):
            base = re.sub(r"\s*\d{4}$", "", d["name"])
            return f"{base} {year if len(year) == 4 else '20' + year}"
    return venue_id


def resolve_target(t, deadlines, now):
    """把一条 project_targets 记录变成页面要显示的形式。

    status: upcoming（未到）| past（已过）| tbd（会议日期待定）| submitted（已投出）
    firm:   会议为 True（硬截稿），期刊为 False（自定的预计日期）
    """
    out = {"kind": t["kind"], "role": t["role"], "firm": t["kind"] == "venue",
           "due": None, "days_left": None, "url": "", "comment": "", "abstract_due": None}
    if t["kind"] == "journal":
        out.update(name=t["ref"], due=t["planned"])
        when = parse_ts(t["planned"] + "T23:59:59")
    else:
        d = deadlines.get(t["ref"])
        out.update(id=t["ref"], name=d["name"] if d else guess_venue_name(t["ref"], deadlines),
                   url=d["url"] if d else "")
        dated = [r for r in (d["rounds"] if d else []) if r["due"]]
        if not dated:
            out["status"] = "submitted" if t["role"] == "submitted" else "tbd"
            return out
        # 多轮截稿时取下一个还没过的；都过了就取最后一轮
        rnd = next((r for r in dated if parse_ts(r["due"]) >= now), dated[-1])
        out.update(due=rnd["due"], abstract_due=rnd["abstract_due"], comment=rnd["comment"])
        when = parse_ts(rnd["due"])
    out["days_left"] = days_between(when, now)
    out["status"] = ("submitted" if t["role"] == "submitted"
                     else "upcoming" if when >= now else "past")
    out["_when"] = when
    return out


def blurb(row, limit=220):
    """卡片上的简短说明：优先用作者写的一句话，没有就截取摘要开头。"""
    text = row["summary"] or row["abstract"].split("\n\n")[0]
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def project_view(row, targets, deadlines, now, detail=False):
    last = parse_ts(row["last_commit_at"])
    days = max(0, days_between(now, last))
    stall = 0 if row["stage"] in STALL_EXEMPT else 2 if days > 14 else 1 if days > 7 else 0
    resolved = [resolve_target(t, deadlines, now) for t in targets]
    upcoming = sorted((t for t in resolved if t["status"] == "upcoming"), key=lambda t: t["_when"])
    firm = [t for t in upcoming if t["firm"]]
    next_target = (upcoming[0] if upcoming
                   else next((t for t in resolved if t["status"] == "tbd"), None))
    blockers = json.loads(row["blockers_json"])
    view = {
        "id": row["id"], "title": row["title"], "owner": row["owner"], "stage": row["stage"],
        "summary": row["summary"], "blurb": blurb(row), "members": json.loads(row["members_json"]),
        "tags": json.loads(row["tags_json"]), "repo": row["repo"], "last_sha": row["last_sha"],
        "last_commit_at": last.astimezone(CST).isoformat(timespec="seconds"),
        "days_since_commit": days, "stall_level": stall, "blocker_count": len(blockers),
        "next_target": next_target,
        "_firm_when": firm[0]["_when"] if firm else None,
        "_min_days": upcoming[0]["days_left"] if upcoming else None,
        "_last": last,
    }
    if detail:
        view.update(abstract=row["abstract"], blockers=blockers, milestones=json.loads(row["milestones_json"]),
                    commits=json.loads(row["commits_json"]), targets=resolved)
    return view


def strip_private(obj):
    if isinstance(obj, dict):
        return {k: strip_private(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [strip_private(v) for v in obj]
    return obj


def targets_by_project(con):
    out = {}
    for t in con.execute("SELECT * FROM project_targets ORDER BY project_id, pos"):
        out.setdefault(t["project_id"], []).append(dict(t))
    return out


@app.get("/api/v1/projects")
def list_projects(owner: str = "", member: str = "", stage: str = "", tag: str = "",
                  due: int | None = None, stall: int | None = None):
    now = now_cst()
    with closing(connect()) as con:
        deadlines = load_deadlines(con)
        tmap = targets_by_project(con)
        views = [project_view(r, tmap.get(r["id"], []), deadlines, now)
                 for r in con.execute("SELECT * FROM projects")]

    facets = {
        "owners": sorted({v["owner"] for v in views}),
        "members": sorted({m for v in views for m in [v["owner"], *v["members"]]}),
        "stages": [s for s in pv.STAGES if any(v["stage"] == s for v in views)],
        "tags": sorted({t for v in views for t in v["tags"]}),
    }

    def keep(v):
        return ((not owner or v["owner"] == owner)
                and (not member or member == v["owner"] or member in v["members"])
                and (not stage or v["stage"] == stage)
                and (not tag or tag in v["tags"])
                and (due is None or (v["_min_days"] is not None and v["_min_days"] <= due))
                and (stall is None or v["stall_level"] >= stall))

    views = [v for v in views if keep(v)]
    # 有 firm 截稿的排前面，按截稿时间升序；其余按最近提交降序
    views.sort(key=lambda v: (0, v["_firm_when"].timestamp()) if v["_firm_when"]
               else (1, -v["_last"].timestamp()))
    summary = {
        "total": len(views),
        "active": sum(v["days_since_commit"] <= 7 for v in views),
        "blocked": sum(v["blocker_count"] > 0 for v in views),
        "stalled": sum(v["stall_level"] == 2 for v in views),
    }
    return {"now": now.isoformat(timespec="seconds"), "summary": summary,
            "facets": facets, "projects": strip_private(views)}


@app.get("/api/v1/projects/{pid}")
def get_project(pid: str):
    now = now_cst()
    with closing(connect()) as con:
        row = con.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        if row is None:
            raise HTTPException(404, "没有这个项目")
        targets = [dict(t) for t in con.execute(
            "SELECT * FROM project_targets WHERE project_id=? ORDER BY pos", (pid,))]
        view = project_view(row, targets, load_deadlines(con), now, detail=True)
    return {"now": now.isoformat(timespec="seconds"), "project": strip_private(view)}


@app.get("/api/v1/deadlines")
def list_deadlines():
    """所有被项目挂靠的投稿日期。同一会议合并成一条，期刊每个项目各一条。"""
    now = now_cst()
    with closing(connect()) as con:
        deadlines = load_deadlines(con)
        titles = {r["id"]: r["title"] for r in con.execute("SELECT id, title FROM projects")}
        rows = [dict(t) for t in con.execute("SELECT * FROM project_targets ORDER BY project_id, pos")]

    items = {}
    for t in rows:
        if t["project_id"] not in titles:
            continue
        key = ("venue", t["ref"]) if t["kind"] == "venue" else ("journal", t["ref"], t["project_id"])
        if key not in items:
            item = resolve_target({**t, "role": "target"}, deadlines, now)   # 条目状态与单个项目的角色无关
            item.pop("role")
            item["projects"] = []
            items[key] = item
        items[key]["projects"].append(
            {"id": t["project_id"], "title": titles[t["project_id"]], "role": t["role"]})

    far = dt.datetime.max.replace(tzinfo=CST)
    ordered = sorted(items.values(), key=lambda i: i.get("_when") or far)
    return {"now": now.isoformat(timespec="seconds"), "deadlines": strip_private(ordered)}


# ---------- 页面 ----------

@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nDisallow: /\n"


def _index():
    return FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/")
def page_home():
    return _index()


@app.get("/deadlines")
def page_deadlines():
    return _index()


@app.get("/p/{pid}")
def page_project(pid: str):
    return _index()


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
