"""客户端脚本的单元测试。运行: python -m unittest discover -s tests -v"""
import copy
import datetime as dt
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "client", "lab_sync"))

import signing  # noqa: E402
import validate as v  # noqa: E402

EXAMPLE = os.path.join(ROOT, "client", "project.example.yaml")


def base():
    return {"schema": 1, "title": "T", "owner": "alice", "stage": "idea"}


class ValidateTest(unittest.TestCase):
    def assertInvalid(self, doc, fragment):
        errors = v.validate(doc)
        self.assertTrue(any(fragment in e for e in errors), errors)

    def test_example_file_passes(self):
        self.assertEqual(v.validate(v.load(EXAMPLE)), [])

    def test_minimal_passes(self):
        self.assertEqual(v.validate(base()), [])

    def test_top_level_must_be_mapping(self):
        self.assertTrue(v.validate(["x"]))
        self.assertTrue(v.validate(None))

    def test_required_and_enum(self):
        for key in ("schema", "title", "owner", "stage"):
            doc = base()
            del doc[key]
            self.assertInvalid(doc, key)
        self.assertInvalid({**base(), "stage": "coding"}, "stage")
        self.assertInvalid({**base(), "schema": 2}, "schema")
        self.assertInvalid({**base(), "schema": True}, "schema")
        self.assertInvalid({**base(), "owner": "not a login"}, "owner")

    def test_unknown_key_rejected(self):
        self.assertInvalid({**base(), "deadline": "2027-01-01"}, "未知字段")
        self.assertInvalid(
            {**base(), "milestones": [{"title": "a", "status": "todo", "date": "x"}]},
            "未知字段")

    def test_lists(self):
        self.assertInvalid({**base(), "tags": "zk"}, "tags")
        self.assertInvalid({**base(), "tags": ["ZK"]}, "tags[0]")
        self.assertInvalid({**base(), "tags": ["a", "a"]}, "重复")
        self.assertInvalid({**base(), "members": ["bad login"]}, "members[0]")

    def test_dates(self):
        ok = {**base(), "milestones": [
            {"title": "a", "status": "done", "due": dt.date(2027, 1, 2)},
            {"title": "b", "status": "todo", "due": "2027-01-03"},
            {"title": "c", "status": "doing"},
        ]}
        self.assertEqual(v.validate(ok), [])
        for bad in ("2027-13-01", "2027/01/02", 20270102, dt.datetime(2027, 1, 2, 3)):
            self.assertInvalid(
                {**base(), "milestones": [{"title": "a", "status": "todo", "due": bad}]},
                "due")

    def test_targets(self):
        conf = {"venue": "crypto27", "role": "target"}
        jour = {"journal": "IEEE TIFS", "planned": "2027-03-15", "role": "backup"}
        self.assertEqual(v.validate({**base(), "targets": [conf, jour]}), [])
        # 会议不能写日期
        self.assertInvalid({**base(), "targets": [{**conf, "planned": "2027-01-01"}]},
                           "不能写 planned")
        # 期刊必须写日期
        self.assertInvalid({**base(), "targets": [{"journal": "J", "role": "target"}]},
                           "planned")
        # 二选一
        self.assertInvalid({**base(), "targets": [{"role": "target"}]}, "二选一")
        self.assertInvalid(
            {**base(), "targets": [{**conf, "journal": "J", "planned": "2027-01-01"}]},
            "二选一")
        self.assertInvalid({**base(), "targets": [{**conf, "role": "main"}]}, "role")
        self.assertInvalid({**base(), "targets": [conf, copy.deepcopy(conf)]}, "重复")
        self.assertInvalid(
            {**base(), "targets": [jour, {**jour, "journal": "ieee tifs"}]}, "重复")

    def test_normalize_is_json_serializable(self):
        out = v.normalize(v.load(EXAMPLE))
        json.dumps(out)
        self.assertEqual(out["milestones"][0]["due"], "2026-12-15")
        self.assertEqual(out["targets"][1]["planned"], "2027-03-15")
        self.assertEqual(v.normalize(base())["targets"], [])
        self.assertEqual(v.validate(out), [])
        self.assertEqual(v.validate(v.normalize(base())), [])
        self.assertEqual(v.validate({**base(), "stage": "paused"}), [])

    def test_check_venues(self):
        doc = {**base(), "targets": [
            {"venue": "crypto27", "role": "target"},
            {"venue": "nosuch99", "role": "backup"},
            {"journal": "J", "planned": "2027-01-01", "role": "backup"},
        ]}
        errors, warnings = v.check_venues(doc, {"crypto27"})
        self.assertEqual(len(errors), 1)
        self.assertIn("nosuch99", errors[0])
        self.assertEqual(warnings, [])

    def test_check_venues_future_edition_is_warning(self):
        doc = {**base(), "targets": [{"venue": "crypto27", "role": "target"}]}
        errors, warnings = v.check_venues(doc, {"crypto26", "sp27"})
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)
        # 系列对不上仍然是错误
        errors, warnings = v.check_venues(doc, {"sp27"})
        self.assertEqual(len(errors), 1)


class SigningTest(unittest.TestCase):
    def test_roundtrip(self):
        sig = signing.sign("s3cret", "1000", b"{}")
        self.assertTrue(signing.verify("s3cret", "1000", b"{}", sig, now=1000))

    def test_rejects_tampering(self):
        sig = signing.sign("s3cret", "1000", b"{}")
        self.assertFalse(signing.verify("other", "1000", b"{}", sig, now=1000))
        self.assertFalse(signing.verify("s3cret", "1000", b'{"a":1}', sig, now=1000))
        self.assertFalse(signing.verify("s3cret", "1001", b"{}", sig, now=1000))
        self.assertFalse(signing.verify("s3cret", "1000", b"{}", None, now=1000))

    def test_rejects_replay_outside_window(self):
        sig = signing.sign("s3cret", "1000", b"{}")
        self.assertTrue(signing.verify("s3cret", "1000", b"{}", sig, now=1300))
        self.assertFalse(signing.verify("s3cret", "1000", b"{}", sig, now=1301))
        self.assertFalse(signing.verify("s3cret", "abc", b"{}", sig, now=1000))


if __name__ == "__main__":
    unittest.main()
