#!/usr/bin/env python3
"""
招聘系统全量测试套件（全部 in-process 执行，避免 fork 带来的 OOM）。
运行方式：python3 test_all.py
"""
from __future__ import print_function

import io
import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# ─── 测试环境隔离 ─────────────────────────────────────────────────────────────
# 禁用数据库，强制使用内存/json 模式
os.environ.setdefault("RECRUIT_STATE_PATH", "/tmp/recruit_test_state.json")
os.environ.pop("TALENT_DB_PASSWORD", None)


def _call_main(module_name, argv):
    """
    In-process 调用模块的 main(argv)，捕获 stdout/stderr，返回 (stdout, stderr, rc)。
    """
    import importlib
    mod = importlib.import_module(module_name)

    buf_out = io.StringIO()
    buf_err = io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = buf_out, buf_err

    rc = 0
    try:
        result = mod.main(argv)
        rc = int(result) if result is not None else 0
    except SystemExit as e:
        rc = int(e.code) if e.code is not None else 0
    except Exception as exc:
        buf_err.write("EXCEPTION: {}\n".format(exc))
        rc = 1
    finally:
        sys.stdout, sys.stderr = old_out, old_err

    return buf_out.getvalue().strip(), buf_err.getvalue().strip(), rc


def _wipe_state():
    """每个测试用例前清空状态文件。"""
    import json
    path = os.environ.get("RECRUIT_STATE_PATH", "/tmp/recruit_test_state.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"candidates": {}}, f)


# ─── 辅助：创建候选人并返回 talent_id ────────────────────────────────────────
def _new_candidate(name="测试候选人", email="test@example.com", position="后端工程师"):
    out, err, rc = _call_main("cmd_new_candidate", [
        "--name", name, "--email", email, "--position", position,
    ])
    assert rc == 0, "cmd_new_candidate 失败 rc={} err={}".format(rc, err)
    import re
    m = re.search(r"\bt_([ a-z0-9]{6})\b", out.replace(" ", ""))
    if m:
        return "t_" + m.group(1).strip()
    m = re.search(r"talent_id\s*:\s*(t_[a-z0-9]{6})", out)
    if m:
        return m.group(1)
    raise AssertionError("无法从输出中提取 talent_id:\n{}".format(out))


# ─────────────────────────────────────────────────────────────────────────────
class TestNewCandidate(unittest.TestCase):

    def setUp(self):
        _wipe_state()

    def test_creates_candidate(self):
        out, err, rc = _call_main("cmd_new_candidate", [
            "--name", "王芳", "--email", "wf@test.com",
        ])
        self.assertEqual(rc, 0, err)
        self.assertIn("录入人才库", out)
        self.assertIn("t_", out)

    def test_talent_id_is_unique(self):
        id1 = _new_candidate("A", "a@a.com")
        id2 = _new_candidate("B", "b@b.com")
        self.assertNotEqual(id1, id2)

    def test_talent_id_format(self):
        import re
        tid = _new_candidate()
        self.assertRegex(tid, r"^t_[a-z0-9]{6}$")

    def test_optional_fields(self):
        out, err, rc = _call_main("cmd_new_candidate", [
            "--name", "李梅", "--email", "lm@x.com",
            "--position", "产品经理",
            "--education", "本科",
            "--school", "复旦大学",
            "--work-years", "3",
            "--source", "猎头",
        ])
        self.assertEqual(rc, 0, err)
        self.assertIn("产品经理", out)

    def test_missing_name_fails(self):
        _, _, rc = _call_main("cmd_new_candidate", ["--email", "x@x.com"])
        self.assertNotEqual(rc, 0)

    def test_missing_email_fails(self):
        _, _, rc = _call_main("cmd_new_candidate", ["--name", "测试"])
        self.assertNotEqual(rc, 0)


class TestStatus(unittest.TestCase):

    def setUp(self):
        _wipe_state()

    def test_status_shows_name(self):
        tid = _new_candidate("刘洋", "ly@x.com")
        out, err, rc = _call_main("cmd_status", ["--talent-id", tid])
        self.assertEqual(rc, 0, err)
        self.assertIn("刘洋", out)
        self.assertIn(tid, out)

    def test_status_shows_stage(self):
        tid = _new_candidate()
        out, _, rc = _call_main("cmd_status", ["--talent-id", tid])
        self.assertEqual(rc, 0)
        self.assertIn("NEW", out)

    def test_status_not_found(self):
        _, _, rc = _call_main("cmd_status", ["--talent-id", "t_notexist"])
        self.assertNotEqual(rc, 0)

    def test_status_all_lists_all(self):
        _new_candidate("A", "a@a.com")
        _new_candidate("B", "b@b.com")
        out, _, rc = _call_main("cmd_status", ["--all"])
        self.assertEqual(rc, 0)
        self.assertIn("2 位候选人", out)


class TestSearch(unittest.TestCase):

    def setUp(self):
        _wipe_state()

    def test_search_by_email(self):
        _new_candidate("赵磊", "zhaolei@search.com")
        out, _, rc = _call_main("cmd_search", ["--query", "zhaolei"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertGreater(data["found"], 0)

    def test_search_by_name(self):
        _new_candidate("唐书涵", "tsh@x.com")
        out, _, rc = _call_main("cmd_search", ["--query", "唐书涵"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertGreater(data["found"], 0)

    def test_search_by_talent_id(self):
        tid = _new_candidate()
        out, _, rc = _call_main("cmd_search", ["--query", tid])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["candidates"][0]["talent_id"], tid)

    def test_search_no_result(self):
        _, _, rc = _call_main("cmd_search", ["--query", "doesnotexist99999"])
        self.assertNotEqual(rc, 0)

    def test_search_all_active(self):
        _new_candidate("X", "x@x.com")
        out, _, rc = _call_main("cmd_search", ["--all-active"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertGreaterEqual(data["found"], 1)


class TestRound1Result(unittest.TestCase):

    def setUp(self):
        _wipe_state()

    def test_round1_pass_creates_exam(self):
        tid = _new_candidate()
        out, err, rc = _call_main("cmd_round1_result", [
            "--talent-id", tid, "--result", "pass", "--email", "x@x.com",
        ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("一面通过", out)
        self.assertIn("exam-", out)

    def test_round1_reject_keep(self):
        tid = _new_candidate()
        out, _, rc = _call_main("cmd_round1_result", [
            "--talent-id", tid, "--result", "reject_keep",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("保留人才库", out)
        # 验证状态
        st_out, _, _ = _call_main("cmd_status", ["--talent-id", tid])
        self.assertIn("ROUND1_DONE_REJECT_KEEP", st_out)

    def test_round1_reject_delete(self):
        tid = _new_candidate()
        out, _, rc = _call_main("cmd_round1_result", [
            "--talent-id", tid, "--result", "reject_delete",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("移除", out)

    def test_round1_pass_without_email_fails(self):
        tid = _new_candidate()
        _, _, rc = _call_main("cmd_round1_result", [
            "--talent-id", tid, "--result", "pass",
        ])
        self.assertNotEqual(rc, 0)

    def test_round1_wrong_stage_fails(self):
        tid = _new_candidate()
        _call_main("cmd_round1_result", [
            "--talent-id", tid, "--result", "reject_keep",
        ])
        # 第二次执行同一 result=pass（已在错误阶段）
        _, _, rc = _call_main("cmd_round1_result", [
            "--talent-id", tid, "--result", "pass", "--email", "x@x.com",
        ])
        self.assertNotEqual(rc, 0)


class TestExamResult(unittest.TestCase):

    def setUp(self):
        _wipe_state()

    def _setup_exam(self):
        tid = _new_candidate()
        _call_main("cmd_round1_result", [
            "--talent-id", tid, "--result", "pass", "--email", "x@x.com",
        ])
        return tid

    def test_exam_pass_transitions(self):
        tid = self._setup_exam()
        out, err, rc = _call_main("cmd_exam_result", [
            "--talent-id", tid, "--result", "pass",
            "--round2-time", "2026-04-01 14:00",
        ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("ROUND2_SCHEDULED", out)

    def test_exam_reject_keep(self):
        tid = self._setup_exam()
        out, _, rc = _call_main("cmd_exam_result", [
            "--talent-id", tid, "--result", "reject_keep",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("保留人才库", out)
        st_out, _, _ = _call_main("cmd_status", ["--talent-id", tid])
        self.assertIn("ROUND1_DONE_REJECT_KEEP", st_out)

    def test_exam_reject_delete(self):
        tid = self._setup_exam()
        out, _, rc = _call_main("cmd_exam_result", [
            "--talent-id", tid, "--result", "reject_delete",
        ])
        self.assertEqual(rc, 0)

    def test_exam_wrong_stage_fails(self):
        tid = _new_candidate()  # 还在 NEW，没过一面
        _, _, rc = _call_main("cmd_exam_result", [
            "--talent-id", tid, "--result", "pass",
            "--round2-time", "2026-04-01 14:00",
        ])
        self.assertNotEqual(rc, 0)


class TestRound2Result(unittest.TestCase):

    def setUp(self):
        _wipe_state()

    def _setup_r2(self):
        """候选人走到 ROUND2_SCHEDULED。"""
        tid = _new_candidate()
        _call_main("cmd_round1_result", [
            "--talent-id", tid, "--result", "pass", "--email", "x@x.com",
        ])
        _call_main("cmd_exam_result", [
            "--talent-id", tid, "--result", "pass",
            "--round2-time", "2026-04-01 14:00",
        ])
        return tid

    def test_round2_pending(self):
        tid = self._setup_r2()
        out, _, rc = _call_main("cmd_round2_result", [
            "--talent-id", tid, "--result", "pending",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("ROUND2_DONE_PENDING", out)

    def test_round2_pass(self):
        tid = self._setup_r2()
        out, err, rc = _call_main("cmd_round2_result", [
            "--talent-id", tid, "--result", "pass",
        ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("OFFER_HANDOFF", out)

    def test_round2_reject_keep(self):
        tid = self._setup_r2()
        out, _, rc = _call_main("cmd_round2_result", [
            "--talent-id", tid, "--result", "reject_keep",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("保留人才库", out)

    def test_round2_reject_delete(self):
        tid = self._setup_r2()
        out, _, rc = _call_main("cmd_round2_result", [
            "--talent-id", tid, "--result", "reject_delete",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("移除", out)

    def test_round2_wrong_stage_fails(self):
        tid = _new_candidate()  # 还在 NEW
        _, _, rc = _call_main("cmd_round2_result", [
            "--talent-id", tid, "--result", "pass",
        ])
        self.assertNotEqual(rc, 0)


class TestRemove(unittest.TestCase):

    def setUp(self):
        _wipe_state()

    def test_remove_without_confirm_fails(self):
        tid = _new_candidate()
        out, _, rc = _call_main("cmd_remove", ["--talent-id", tid])
        self.assertNotEqual(rc, 0)
        self.assertIn("confirm", out + _)

    def test_remove_with_confirm(self):
        tid = _new_candidate()
        out, _, rc = _call_main("cmd_remove", ["--talent-id", tid, "--confirm"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["ok"])

    def test_remove_nonexistent_fails(self):
        out, _, rc = _call_main("cmd_remove", ["--talent-id", "t_xxxxxx", "--confirm"])
        self.assertNotEqual(rc, 0)


class TestCoreState(unittest.TestCase):

    def test_stages_set_is_complete(self):
        import core_state
        expected = {
            "NEW", "ROUND1_SCHEDULED", "ROUND1_DONE_PASS",
            "ROUND1_DONE_REJECT_KEEP", "ROUND1_DONE_REJECT_DELETE",
            "EXAM_PENDING", "EXAM_REVIEWED",
            "ROUND2_SCHEDULED", "ROUND2_DONE_PENDING",
            "ROUND2_DONE_PASS", "ROUND2_DONE_REJECT_KEEP", "ROUND2_DONE_REJECT_DELETE",
            "OFFER_HANDOFF",
        }
        self.assertTrue(expected.issubset(core_state.STAGES))

    def test_ensure_stage_transition_ok(self):
        import core_state
        cand = {"talent_id": "t_test", "stage": "NEW", "audit": []}
        ok = core_state.ensure_stage_transition(cand, {"NEW"}, "EXAM_PENDING")
        self.assertTrue(ok)
        self.assertEqual(cand["stage"], "EXAM_PENDING")

    def test_ensure_stage_transition_wrong_stage(self):
        import core_state
        cand = {"talent_id": "t_test", "stage": "EXAM_PENDING", "audit": []}
        ok = core_state.ensure_stage_transition(cand, {"NEW"}, "ROUND2_SCHEDULED")
        self.assertFalse(ok)
        self.assertEqual(cand["stage"], "EXAM_PENDING")

    def test_no_round1_score_field(self):
        import core_state
        self.assertNotIn("round1_score", str(dir(core_state)))


class TestFeishuNotify(unittest.TestCase):

    def test_import_feishu_notify(self):
        import feishu_notify
        self.assertTrue(hasattr(feishu_notify, "send_text"))

    def test_send_text_no_token_returns_false(self):
        import feishu_notify
        old_func = feishu_notify._get_tenant_access_token
        feishu_notify._get_tenant_access_token = lambda: None
        try:
            result = feishu_notify.send_text("hello world test")
            self.assertFalse(result)
        finally:
            feishu_notify._get_tenant_access_token = old_func


class TestDailyExamReview(unittest.TestCase):

    def test_scan_no_imap_config(self):
        """无 IMAP 配置时应静默返回空列表。"""
        import daily_exam_review
        old_host = os.environ.pop("RECRUIT_EXAM_IMAP_HOST", None)
        try:
            results = daily_exam_review.scan_new_replies(auto_mode=True)
            self.assertIsInstance(results, list)
        finally:
            if old_host:
                os.environ["RECRUIT_EXAM_IMAP_HOST"] = old_host

    def test_format_report_uses_prereview(self):
        """format_report 应优先使用 prereview.report_text。"""
        import daily_exam_review
        fake_result = {
            "sender": "test@example.com",
            "subject": "Re: 笔试",
            "date": "2026-03-16 10:00:00",
            "exam_id": "exam-t_001-20260315",
            "prereview": {
                "report_text": "📋 笔试预审报告 | 候选人 t_001",
                "score": 75,
                "db_summary": "[自动预审] 用时正常 | 预审分:75",
            },
        }
        report = daily_exam_review.format_report(fake_result)
        self.assertIn("📋 笔试预审报告", report)

    def test_format_report_fallback(self):
        """无预审结果时 format_report 降级到简单格式。"""
        import daily_exam_review
        fake_result = {
            "sender": "x@example.com",
            "subject": "笔试回复",
            "date": "2026-03-16 10:00:00",
            "exam_id": None,
            "prereview": None,
        }
        report = daily_exam_review.format_report(fake_result)
        self.assertIn("新笔试回复", report)


class TestExamPrereview(unittest.TestCase):

    def setUp(self):
        import exam_prereview
        self.mod = exam_prereview

    def test_analyze_response_time_normal(self):
        result = self.mod.analyze_response_time(
            "2026-03-15 10:00:00", "2026-03-16 08:00:00"
        )
        self.assertTrue(result["available"])
        self.assertIn("正常", result["label"])

    def test_analyze_response_time_too_fast(self):
        result = self.mod.analyze_response_time(
            "2026-03-15 10:00:00", "2026-03-15 10:30:00"
        )
        self.assertTrue(result["available"])
        self.assertIn("极快", result["label"])

    def test_analyze_response_time_overtime(self):
        result = self.mod.analyze_response_time(
            "2026-03-10 10:00:00", "2026-03-15 10:00:00"
        )
        self.assertTrue(result["available"])
        self.assertIn("超时", result["label"])

    def test_analyze_response_time_missing(self):
        result = self.mod.analyze_response_time(None, None)
        self.assertFalse(result["available"])

    def test_code_quality_no_code(self):
        result = self.mod.analyze_code_quality("")
        self.assertFalse(result["has_code"])
        self.assertEqual(result["score"], 0)

    def test_code_quality_good_code(self):
        code = (
            "import pandas as pd\nimport numpy as np\n\n"
            "def clean(df):\n    \"\"\"清洗数据\"\"\"\n    return df.dropna()\n\n"
            "def analyze(df):\n    return df.groupby('x').sum()\n\n"
            "def main():\n    df = pd.read_csv('data.csv')\n    df = clean(df)\n"
            "    result = analyze(df)\n    result.to_csv('out.csv')\n    print(result)\n\n"
            "if __name__ == '__main__':\n    main()\n"
        )
        result = self.mod.analyze_code_quality(code)
        self.assertTrue(result["has_code"])
        self.assertGreater(result["score"], 50)
        self.assertIn("pandas", result["metrics"]["data_libs"])

    def test_code_quality_detects_eval(self):
        code = "x = eval(input())\n"
        result = self.mod.analyze_code_quality(code)
        self.assertTrue(any("eval" in w for w in result["warnings"]))

    def test_code_quality_detects_except_pass(self):
        code = "try:\n    x = 1\nexcept:\n    pass\n"
        result = self.mod.analyze_code_quality(code)
        self.assertTrue(any("except" in w.lower() for w in result["warnings"]))

    def test_completeness_code_and_result(self):
        attachments = [
            {"filename": "solution.py", "size": 1024, "is_text": True},
            {"filename": "output.csv", "size": 512, "is_text": True},
        ]
        result = self.mod.analyze_completeness(attachments, "您好，我已完成笔试题目，代码在附件中，输出结果也一并附上，请查收。")
        self.assertEqual(result["total_attachments"], 2)
        self.assertIn("solution.py", result["code_files"])
        self.assertIn("output.csv", result["result_files"])
        self.assertTrue(result["has_body_text"])

    def test_completeness_no_files(self):
        result = self.mod.analyze_completeness([], "")
        self.assertEqual(result["total_attachments"], 0)
        self.assertEqual(result["code_files"], [])
        self.assertFalse(result["has_body_text"])

    def test_run_prereview_full(self):
        email_data = {
            "sender": "candidate@example.com",
            "subject": "Re: 【笔试邀请】",
            "date": "2026-03-16 10:00:00",
            "body_text": "您好，已完成笔试，请查收附件。",
            "code_text": (
                "import pandas as pd\ndef analyze(df):\n    return df.dropna()\n"
                "def main():\n    df = pd.read_csv('data.csv')\n"
                "    r = analyze(df)\n    r.to_csv('out.csv')\n    print(r)\n"
                "if __name__ == '__main__':\n    main()\n"
            ),
            "attachment_info_list": [
                {"filename": "solution.py", "size": 500, "is_text": True},
                {"filename": "output.csv", "size": 200, "is_text": True},
            ],
        }
        cand_info = {
            "talent_id": "t_test01",
            "candidate_name": "张三",
            "exam_sent_at": "2026-03-15 10:00:00",
            "exam_id": "exam-t_test01-20260315",
        }
        result = self.mod.run_prereview(email_data, cand_info)
        self.assertIn("score", result)
        self.assertIn("report_text", result)
        self.assertIn("db_summary", result)
        self.assertIn("📋 笔试预审报告", result["report_text"])
        self.assertIn("t_test01", result["report_text"])
        self.assertIn("[自动预审]", result["db_summary"])
        self.assertGreater(result["score"], 0)


# ─── 运行测试 ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    loader = unittest.TestLoader()
    loader.sortTestMethodsUsing = None
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
