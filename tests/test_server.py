"""服务端测试。运行: python -m unittest discover -s tests -v"""
import datetime as dt
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import closing

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "client", "lab_sync"))

_tmp = tempfile.mkdtemp()
os.environ.update(LAB_DB_PATH=os.path.join(_tmp, "test.db"), LAB_SYNC_SECRET="test-secret",
                  LAB_CCFDDL_SYNC="0", LAB_MANUAL_FILE=os.path.join(_tmp, "none.yaml"))

from fastapi.testclient import TestClient  # noqa: E402

import signing  # noqa: E402
from server import app as srv  # noqa: E402
from server import ccfddl  # noqa: E402

CST = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 11, 21, 12, 0, tzinfo=CST)


def ccfddl_data(deadline, cid="testconf27", tz="UTC-12"):
    return [{"title": "TESTCONF", "rank": {"ccf": "A"}, "confs": [{
        "year": 2027, "id": cid, "link": "https://example.org", "timezone": tz,
        "timeline": [{"deadline": deadline, "comment": "main"}]}]}]


class ServerTest(unittest.TestCase):
    def setUp(self):
        if os.path.exists(os.environ["LAB_DB_PATH"]):
            os.remove(os.environ["LAB_DB_PATH"])
        self._real_now = srv.now_cst
        srv.now_cst = lambda: NOW
        self.ctx = TestClient(srv.app)
        self.c = self.ctx.__enter__()

    def tearDown(self):
        self.ctx.__exit__(None, None, None)
        srv.now_cst = self._real_now

    def push(self, pid, days_ago=1, sha=None, secret="test-secret", ts=None, abstract=None, **project):
        commit_at = (NOW - dt.timedelta(days=days_ago)).isoformat(timespec="seconds")
        sha = sha or (pid.encode().hex() + "0" * 40)[:40]
        body = json.dumps({
            "payload_version": 1, "project_id": pid, "repo": f"lab/{pid}", "sha": sha,
            "source": "push", "last_commit_at": commit_at,
            **({} if abstract is None else {"abstract": abstract}),
            "project": {"schema": 1, "title": pid.upper(), "owner": "alice", "stage": "implementation",
                        **project},
            "commits": [{"sha": sha, "author": "a", "date": commit_at, "message": "m"}],
        }, ensure_ascii=False).encode()
        ts = ts or str(int(time.time()))
        return self.c.post("/api/v1/ingest", content=body, headers={
            signing.HEADER_TS: ts, signing.HEADER_SIG: signing.sign(secret, ts, body)})

    def sync(self, data=None, manual=None):
        with closing(srv.connect()) as con:
            return ccfddl.apply(con, data, manual, now=NOW)

    def projects(self, query=""):
        return self.c.get("/api/v1/projects" + query).json()

    def ids(self, query=""):
        return [p["id"] for p in self.projects(query)["projects"]]

    # ---- 写入 ----
    def test_ingest_rejects_bad_signature_and_old_timestamp(self):
        self.assertEqual(self.push("p1", secret="wrong").status_code, 401)
        self.assertEqual(self.push("p1", ts=str(int(time.time()) - 3600)).status_code, 401)
        self.assertEqual(self.ids(), [])

    def test_ingest_rejects_invalid_project(self):
        self.assertEqual(self.push("p1", stage="coding").status_code, 400)
        self.assertEqual(self.push("Bad Id").status_code, 400)

    def test_ingest_is_idempotent(self):
        first, second = self.push("p1").json(), self.push("p1").json()
        self.assertEqual((first["duplicate"], second["duplicate"]), (False, True))
        self.assertEqual(self.ids(), ["p1"])
        with closing(srv.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 1)

    def test_late_older_commit_does_not_overwrite(self):
        self.push("p1", days_ago=1, sha="a" * 40, stage="writing")
        r = self.push("p1", days_ago=5, sha="b" * 40, stage="idea").json()
        self.assertTrue(r["stale"])
        self.assertEqual(self.projects()["projects"][0]["stage"], "writing")

    # ---- 时区：验收第 4 条 ----
    def test_aoe_deadline_converts_to_beijing_time(self):
        for tz in ("UTC-12", "AoE"):
            self.assertEqual(ccfddl.to_cst("2027-05-20 23:59:59", ccfddl.parse_tz(tz)),
                             "2027-05-21T19:59:59+08:00")
        self.assertEqual(ccfddl.to_cst("2027-05-20 23:59:59", ccfddl.parse_tz("UTC+8")),
                         "2027-05-20T23:59:59+08:00")
        self.assertEqual(ccfddl.to_cst("2027-05-20 23:59:59", ccfddl.parse_tz("UTC")),
                         "2027-05-21T07:59:59+08:00")
        self.assertIsNone(ccfddl.to_cst("TBD", ccfddl.parse_tz("UTC-12")))
        self.assertIsNone(ccfddl.parse_tz("Mars/Olympus"))
        # 地区缩写要考虑夏令时：2 月是 PST(UTC-8)，7 月是 PDT(UTC-7)
        pt = ccfddl.parse_tz("PT")
        self.assertEqual(ccfddl.to_cst("2026-02-12 23:59:59", pt), "2026-02-13T15:59:59+08:00")
        self.assertEqual(ccfddl.to_cst("2026-07-12 23:59:59", pt), "2026-07-13T14:59:59+08:00")

    def test_unknown_timezone_is_reported_not_silently_dropped(self):
        result = self.sync(ccfddl_data("2026-12-01 23:59:59", tz="Mars/Olympus"))
        self.assertEqual(len(result["unknown_tz"]), 1)
        self.assertIn("testconf27", result["unknown_tz"][0])

    # ---- 改一处全联动：验收第 3 条 ----
    def test_changing_a_deadline_updates_every_linked_project(self):
        target = [{"venue": "testconf27", "role": "target"}]
        self.push("p1", targets=target)
        self.push("p2", targets=target)
        self.sync(ccfddl_data("2026-12-01 23:59:59"))
        dues = {p["next_target"]["due"] for p in self.projects()["projects"]}
        self.assertEqual(dues, {"2026-12-02T19:59:59+08:00"})

        self.sync(ccfddl_data("2026-11-25 23:59:59"))
        dues = {p["next_target"]["due"] for p in self.projects()["projects"]}
        self.assertEqual(dues, {"2026-11-26T19:59:59+08:00"})
        item = self.c.get("/api/v1/deadlines").json()["deadlines"][0]
        self.assertEqual(len(item["projects"]), 2)
        self.assertTrue(item["firm"])

    def test_unlisted_conference_is_tbd_and_journal_is_soft(self):
        self.push("p1", targets=[{"venue": "future28", "role": "target"}])
        self.push("p2", targets=[{"journal": "IEEE TIFS", "planned": "2027-03-15", "role": "target"}])
        got = {p["id"]: p["next_target"] for p in self.projects()["projects"]}
        self.assertEqual(got["p1"]["status"], "tbd")
        self.assertEqual((got["p2"]["firm"], got["p2"]["due"], got["p2"]["status"]),
                         (False, "2027-03-15", "upcoming"))

    def test_unlisted_edition_borrows_name_from_its_series(self):
        self.sync(ccfddl_data("2026-12-01 23:59:59", cid="testconf27")
                  + ccfddl_data("2026-12-01 23:59:59", cid="longid2027"))
        self.push("p1", targets=[{"venue": "testconf28", "role": "target"}])
        self.push("p2", targets=[{"venue": "longid2028", "role": "target"}])
        self.push("p3", targets=[{"venue": "testconfx28", "role": "target"}])
        got = {p["id"]: p["next_target"] for p in self.projects()["projects"]}
        self.assertEqual((got["p1"]["name"], got["p1"]["status"]), ("TESTCONF 2028", "tbd"))
        self.assertEqual(got["p2"]["name"], "TESTCONF 2028")
        self.assertEqual(got["p3"]["name"], "testconfx28")     # 系列对不上就保留原样

    def test_manual_entry_overrides_ccfddl(self):
        self.push("p1", targets=[{"venue": "testconf27", "role": "target"}])
        manual = [{"id": "testconf27", "name": "手工", "rounds": [{"due": "2026-12-10 18:00"}]}]
        self.sync(ccfddl_data("2026-12-01 23:59:59"), manual)
        t = self.projects()["projects"][0]["next_target"]
        self.assertEqual((t["name"], t["due"]), ("手工", "2026-12-10T18:00:00+08:00"))
        # 手工条目删掉后，回到 ccfddl 的数据
        self.sync(ccfddl_data("2026-12-01 23:59:59"), None)
        self.assertEqual(self.projects()["projects"][0]["next_target"]["name"], "TESTCONF 2027")

    def test_failed_fetch_keeps_existing_deadlines(self):
        self.push("p1", targets=[{"venue": "testconf27", "role": "target"}])
        self.sync(ccfddl_data("2026-12-01 23:59:59"))
        self.sync(None)
        self.assertEqual(self.projects()["projects"][0]["next_target"]["status"], "upcoming")

    # ---- 停滞、筛选、排序 ----
    def test_stall_levels_and_exempt_stages(self):
        self.push("fresh", days_ago=3)
        self.push("slow", days_ago=10)
        self.push("stuck", days_ago=20)
        self.push("sent", days_ago=40, stage="submitted")
        self.push("held", days_ago=40, stage="paused")
        levels = {p["id"]: p["stall_level"] for p in self.projects()["projects"]}
        self.assertEqual(levels, {"fresh": 0, "slow": 1, "stuck": 2, "sent": 0, "held": 0})
        self.assertEqual(sorted(self.ids("?stall=1")), ["slow", "stuck"])
        self.assertEqual(self.ids("?stall=2"), ["stuck"])
        s = self.projects()["summary"]
        self.assertEqual((s["total"], s["active"], s["stalled"]), (5, 1, 1))

    def test_filters(self):
        self.sync(ccfddl_data("2026-11-25 23:59:59"))
        self.push("p1", owner="alice", members=["bob"], tags=["zk"],
                  targets=[{"venue": "testconf27", "role": "target"}])
        self.push("p2", owner="bob", stage="writing", blockers=[{"text": "x"}],
                  targets=[{"journal": "J", "planned": "2027-03-15", "role": "target"}])
        self.push("p3", owner="carol")
        self.assertEqual(self.ids("?owner=bob"), ["p2"])
        self.assertEqual(sorted(self.ids("?member=bob")), ["p1", "p2"])
        self.assertEqual(self.ids("?stage=writing"), ["p2"])
        self.assertEqual(self.ids("?tag=zk"), ["p1"])
        self.assertEqual(self.ids("?due=7"), ["p1"])
        self.assertEqual(sorted(self.ids("?due=365")), ["p1", "p2"])
        self.assertEqual(self.ids("?owner=alice&due=7"), ["p1"])     # 验收第 5 条
        data = self.projects("?owner=bob")
        self.assertEqual(data["summary"]["blocked"], 1)
        self.assertEqual(data["facets"]["owners"], ["alice", "bob", "carol"])   # 选项不随筛选变少

    def test_default_order_firm_first_then_recent_commit(self):
        self.sync(ccfddl_data("2026-12-20 23:59:59", cid="late27")
                  + ccfddl_data("2026-11-25 23:59:59", cid="soon27"))
        self.push("no-target-old", days_ago=5)
        self.push("no-target-new", days_ago=1)
        self.push("late", targets=[{"venue": "late27", "role": "target"}])
        self.push("soon", targets=[{"venue": "soon27", "role": "target"}])
        self.assertEqual(self.ids(), ["soon", "late", "no-target-new", "no-target-old"])

    def test_submitted_target_is_not_treated_as_upcoming(self):
        self.sync(ccfddl_data("2026-11-25 23:59:59"))
        self.push("p1", stage="submitted", targets=[{"venue": "testconf27", "role": "submitted"}])
        self.assertIsNone(self.projects()["projects"][0]["next_target"])
        self.assertEqual(self.ids("?due=7"), [])

    # ---- 摘要 ----
    def test_abstract_is_stored_and_card_blurb_prefers_summary(self):
        long_abs = "First paragraph of the abstract. " * 12 + "\n\nSecond paragraph."
        self.push("with-abs", abstract=long_abs)
        self.push("with-both", abstract=long_abs, summary="作者写的一句话")
        self.push("with-none")
        cards = {p["id"]: p["blurb"] for p in self.projects()["projects"]}
        self.assertTrue(cards["with-abs"].startswith("First paragraph") and cards["with-abs"].endswith("…"))
        self.assertLessEqual(len(cards["with-abs"]), 221)
        self.assertEqual((cards["with-both"], cards["with-none"]), ("作者写的一句话", ""))
        detail = self.c.get("/api/v1/projects/with-abs").json()["project"]
        self.assertEqual(detail["abstract"], long_abs)
        self.assertNotIn("abstract", self.projects()["projects"][0])      # 列表里不带全文
        self.assertEqual(self.push("bad", abstract=["not", "text"]).status_code, 400)

    def test_database_created_before_abstracts_is_upgraded(self):
        with closing(srv.connect()) as con:
            con.execute("ALTER TABLE projects DROP COLUMN abstract")
            con.commit()
        srv.init_db()
        self.assertEqual(self.push("p1", abstract="x" * 100).status_code, 200)
        self.assertEqual(self.c.get("/api/v1/projects/p1").json()["project"]["abstract"], "x" * 100)

    # ---- 访问密码 ----
    def test_view_password_protects_pages_but_not_ingest(self):
        os.environ["LAB_VIEW_PASSWORD"] = "letmein-123"
        try:
            for path in ("/", "/deadlines", "/p/x", "/api/v1/projects", "/api/v1/deadlines", "/static/app.js"):
                r = self.c.get(path)
                self.assertEqual(r.status_code, 401, path)
                self.assertIn("Basic", r.headers["www-authenticate"])
            self.assertEqual(self.c.get("/", auth=("lab", "wrong")).status_code, 401)
            self.assertEqual(self.c.get("/", headers={"Authorization": "Basic !!!"}).status_code, 401)
            self.assertEqual(self.c.get("/api/v1/projects", auth=("anyone", "letmein-123")).status_code, 200)
            self.assertEqual(self.c.get("/robots.txt").status_code, 200)
            # 推送靠签名，不需要访问密码
            self.assertEqual(self.push("p1").status_code, 200)
            self.assertEqual(self.push("p2", secret="wrong").status_code, 401)
        finally:
            del os.environ["LAB_VIEW_PASSWORD"]

    # ---- 详情与页面 ----
    def test_project_detail_and_pages(self):
        self.push("p1", summary="s", milestones=[{"title": "m", "status": "doing", "due": "2026-12-01"}],
                  blockers=[{"text": "b", "since": "2026-11-01"}])
        p = self.c.get("/api/v1/projects/p1").json()["project"]
        self.assertEqual((p["milestones"][0]["title"], p["blockers"][0]["text"], len(p["commits"])),
                         ("m", "b", 1))
        self.assertEqual(self.c.get("/api/v1/projects/nope").status_code, 404)
        for path in ("/", "/deadlines", "/p/p1", "/static/app.js"):
            self.assertEqual(self.c.get(path).status_code, 200, path)
        self.assertIn("Disallow: /", self.c.get("/robots.txt").text)
        self.assertNotIn("_when", json.dumps(self.c.get("/api/v1/deadlines").json()))


if __name__ == "__main__":
    unittest.main()
