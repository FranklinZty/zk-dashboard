"""客户端脚本的单元测试。运行: python -m unittest discover -s tests -v"""
import copy
import datetime as dt
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "client", "lab_sync"))

import abstract  # noqa: E402
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


class AbstractTest(unittest.TestCase):
    def repo(self, files):
        root = tempfile.mkdtemp()
        for rel, text in files.items():
            path = os.path.join(root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        return root

    LONG = "This sentence pads the abstract so that it is long enough to count as a real one. "

    def test_latex_becomes_plain_text(self):
        tex = (r"We present \sys{}, a \emph{fast} scheme~\cite{KZG10} with $n \ge 2^{20}$ rows "
               r"and 3--5$\times$ speed-up.\footnote{link} % trailing comment" "\n\n"
               r"Second paragraph, 50\% smaller (see \cref{tab:x}).")
        out = abstract.to_plain(tex, {"sys": r"\textsf{Vicuna}\xspace"})
        self.assertEqual(out, "We present Vicuna, a fast scheme with n ≥ 2^20 rows and 3–5× speed-up."
                              "\n\nSecond paragraph, 50% smaller.")

    def test_extract_reads_environment_and_expands_project_macros(self):
        root = self.repo({
            "paper/main.tex": r"\newcommand{\sys}{\textsf{Vicuna}\xspace}" "\n\\input{sections/abstract}",
            "paper/sections/abstract.tex":
                "% header comment\n\\begin{abstract}\n" + self.LONG + r"We call it \sys{}."
                + "\n\\keywords{a \\and b}\n\\end{abstract}\n",
        })
        text, src = abstract.extract(root)
        self.assertEqual(src, "paper/sections/abstract.tex")
        self.assertTrue(text.endswith("We call it Vicuna."), text)
        self.assertNotIn("keywords", text)

    def test_template_placeholder_and_short_text_are_not_abstracts(self):
        root = self.repo({
            "paper/sections/abstract.tex": "\\begin{abstract}<<Field consensus sentence.>> "
                                           + self.LONG + "\\end{abstract}",
            "main.tex": "\\begin{abstract}TODO\\end{abstract}",
        })
        self.assertEqual(abstract.extract(root), (None, None))

    def test_falls_through_default_paths_and_honours_abstract_file(self):
        root = self.repo({
            "main.tex": "\\begin{abstract}" + self.LONG + "From main.\\end{abstract}",
            "draft/abs.tex": "\\begin{abstract}" + self.LONG + "From draft.\\end{abstract}",
        })
        self.assertTrue(abstract.extract(root)[0].endswith("From main."))
        text, src = abstract.extract(root, "draft/abs.tex")
        self.assertEqual((text.endswith("From draft."), src), (True, "draft/abs.tex"))
        self.assertEqual(abstract.extract(root, "missing.tex"), (None, None))

    def test_long_abstract_is_capped(self):
        root = self.repo({"abstract.tex": self.LONG * 100})
        self.assertLessEqual(len(abstract.extract(root)[0]), abstract.MAX_CHARS + 1)

    STATE = ("# STATE\n\n- **Phase:** P2\n"
             "- **One-sentence thesis (hypothesis):** A composition of a *malleable* protocol and a\n"
             "  recursive SNARK, see [notes](notes/x.md), supports `controlled` statement changes.\n"
             "- **Last updated:** today\n\n## Gate P0\n- [x] thesis is one falsifiable sentence\n")

    def test_state_thesis_with_continuation_lines(self):
        root = self.repo({"STATE.md": self.STATE})
        self.assertEqual(abstract.from_state(root), (
            "A composition of a malleable protocol and a recursive SNARK, see notes, "
            "supports controlled statement changes.", "STATE.md"))
        other = self.repo({"STATE.md": "- **Working thesis:** " + self.LONG + "\n- **Paper type:** x\n"})
        self.assertEqual(abstract.from_state(other)[0], self.LONG.strip())
        self.assertEqual(abstract.from_state(self.repo({"STATE.md": "- **Phase:** P1\n"})), (None, None))
        self.assertEqual(abstract.from_state(self.repo({"a.txt": "x"})), (None, None))

    def test_description_prefers_abstract_then_state(self):
        both = self.repo({"STATE.md": self.STATE,
                          "paper/sections/abstract.tex": "\\begin{abstract}" + self.LONG + "\\end{abstract}"})
        self.assertEqual(abstract.describe(both)[1], "paper/sections/abstract.tex")
        # 摘要还是模板时，退到 STATE.md
        template = self.repo({"STATE.md": self.STATE,
                              "paper/sections/abstract.tex": "\\begin{abstract}<<todo>>" + self.LONG + "\\end{abstract}"})
        self.assertEqual(abstract.describe(template)[1], "STATE.md")
        self.assertEqual(abstract.describe(self.repo({"a.txt": "x"})), (None, None))

    def test_abstract_file_field_is_validated(self):
        self.assertEqual(v.validate({**base(), "abstract_file": "paper/sections/abstract.tex"}), [])
        for bad in ("../secret.tex", "/etc/passwd.tex", "paper/abstract.md", 3):
            self.assertTrue(v.validate({**base(), "abstract_file": bad}), bad)
        out = v.normalize({**base(), "abstract_file": "a.tex"})
        self.assertEqual((out["abstract_file"], v.validate(out)), ("a.tex", []))


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
