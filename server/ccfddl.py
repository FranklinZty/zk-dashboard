"""会议截稿数据：从 ccfddl.com 同步，并支持手工补充和覆盖。

ccfddl 的原始时间带各自的时区（多数是 AoE，即 UTC-12）。这里是全系统唯一做时区
换算的地方：入库前统一换成北京时间（UTC+8），之后各处只见北京时间。

手工文件（默认 server/deadlines.manual.yaml）里的时间直接按北京时间填写，格式:

    - id: myworkshop27
      name: My Workshop 2027
      url: https://example.org
      rounds:
        - due: "2027-02-01 19:59"
          comment: 可选

手工条目与 ccfddl 的 id 相同时，以手工为准。
"""
from __future__ import annotations

import datetime as dt
import re
import ssl
import urllib.request

import yaml
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CCFDDL_URL = "https://ccfddl.com/conference/allconf.yml"
CST = dt.timezone(dt.timedelta(hours=8))
_TZ_RE = re.compile(r"^(?:UTC|GMT)\s*(?:([+-])\s*(\d{1,2})(?::?(\d{2}))?)?$", re.I)


# ccfddl 里偶尔出现的地区缩写。用地区时区而不是固定偏移，夏令时才不会差一小时。
NAMED_ZONES = {
    "PT": "America/Los_Angeles", "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles",
    "ET": "America/New_York", "EST": "America/New_York", "EDT": "America/New_York",
    "CT": "America/Chicago", "CST": "America/Chicago", "CDT": "America/Chicago",
    "MT": "America/Denver", "MST": "America/Denver", "MDT": "America/Denver",
    "CET": "Europe/Paris", "CEST": "Europe/Paris", "BST": "Europe/London",
}


def parse_tz(text):
    """'UTC-12' / 'AoE' / 'UTC+8' / 'UTC+5:30' / 'UTC' / 'PT' -> tzinfo；认不出返回 None。"""
    s = str(text or "").strip()
    if s.lower() == "aoe":
        return dt.timezone(dt.timedelta(hours=-12))
    if s.upper() in NAMED_ZONES:
        try:
            return ZoneInfo(NAMED_ZONES[s.upper()])
        except ZoneInfoNotFoundError:
            return None
    m = _TZ_RE.match(s)
    if not m:
        return None
    sign, hours, minutes = m.groups()
    delta = dt.timedelta(hours=int(hours or 0), minutes=int(minutes or 0))
    if delta > dt.timedelta(hours=14):
        return None
    return dt.timezone(-delta if sign == "-" else delta)


def to_cst(text, tz):
    """把某时区的 'YYYY-MM-DD HH:MM[:SS]' 换算成北京时间 ISO 字符串；TBD 或无法解析返回 None。"""
    if tz is None or not isinstance(text, (str, dt.datetime)):
        return None
    try:
        naive = text if isinstance(text, dt.datetime) else dt.datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    if naive.tzinfo is not None:
        naive = naive.replace(tzinfo=None)
    return naive.replace(tzinfo=tz).astimezone(CST).isoformat(timespec="seconds")


def parse_allconf(data, min_year, unknown_tz=None):
    """ccfddl allconf.yml -> [{id, name, url, rank, rounds: [{due, abstract_due, comment}]}]

    时区认不出的会议，日期会变成待定；把它们记进 unknown_tz，不能悄悄丢掉。
    """
    out = []
    for entry in data or []:
        title = str(entry.get("title") or "").strip()
        rank = str((entry.get("rank") or {}).get("ccf") or "")
        for conf in entry.get("confs") or []:
            cid, year = conf.get("id"), conf.get("year")
            if not cid or not isinstance(year, int) or year < min_year:
                continue
            tz = parse_tz(conf.get("timezone"))
            if tz is None and unknown_tz is not None:
                unknown_tz.append(f"{cid}: {conf.get('timezone')!r}")
            rounds = [
                {
                    "due": to_cst(r.get("deadline"), tz),
                    "abstract_due": to_cst(r.get("abstract_deadline"), tz),
                    "comment": str(r.get("comment") or "")[:200],
                }
                for r in conf.get("timeline") or [] if isinstance(r, dict)
            ]
            out.append({"id": str(cid), "name": f"{title} {year}".strip(),
                        "url": str(conf.get("link") or ""), "rank": rank, "rounds": rounds})
    return out


def parse_manual(data):
    out = []
    for e in data or []:
        if not isinstance(e, dict) or not e.get("id") or not e.get("name"):
            continue
        rounds = [
            {
                "due": to_cst(str(r.get("due") or ""), CST),
                "abstract_due": to_cst(str(r.get("abstract_due") or ""), CST),
                "comment": str(r.get("comment") or "")[:200],
            }
            for r in e.get("rounds") or [] if isinstance(r, dict)
        ]
        out.append({"id": str(e["id"]), "name": str(e["name"]), "url": str(e.get("url") or ""),
                    "rank": str(e.get("rank") or ""), "rounds": rounds})
    return out


def fetch(url=CCFDDL_URL, timeout=30):
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "lab-dashboard"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return yaml.safe_load(resp.read())


def _upsert(con, conf, source, now_iso):
    con.execute(
        "INSERT INTO deadlines (id, name, url, rank, source, updated_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, url=excluded.url, rank=excluded.rank, "
        "source=excluded.source, updated_at=excluded.updated_at",
        (conf["id"], conf["name"], conf["url"], conf["rank"], source, now_iso))
    con.execute("DELETE FROM deadline_rounds WHERE deadline_id=?", (conf["id"],))
    con.executemany(
        "INSERT INTO deadline_rounds (deadline_id, idx, due, abstract_due, comment) VALUES (?,?,?,?,?)",
        [(conf["id"], i, r["due"], r["abstract_due"], r["comment"])
         for i, r in enumerate(conf["rounds"])])


def apply(con, ccfddl_data=None, manual_data=None, now=None):
    """把两份数据写入库。ccfddl_data 为 None 表示本次没拉到，只刷新手工条目。"""
    now = now or dt.datetime.now(CST)
    now_iso = now.isoformat(timespec="seconds")
    manual = parse_manual(manual_data)
    manual_ids = {m["id"] for m in manual}

    # 手工文件里删掉的条目要跟着消失
    stale = [r[0] for r in con.execute("SELECT id FROM deadlines WHERE source='manual'")
             if r[0] not in manual_ids]
    for sid in stale:
        con.execute("DELETE FROM deadline_rounds WHERE deadline_id=?", (sid,))
        con.execute("DELETE FROM deadlines WHERE id=?", (sid,))

    synced, unknown_tz = 0, []
    if ccfddl_data is not None:
        for conf in parse_allconf(ccfddl_data, now.year - 1, unknown_tz):
            if conf["id"] not in manual_ids:
                _upsert(con, conf, "ccfddl", now_iso)
                synced += 1
    for conf in manual:
        _upsert(con, conf, "manual", now_iso)
    con.commit()
    return {"ccfddl": synced, "manual": len(manual), "unknown_tz": unknown_tz}
