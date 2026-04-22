#!/usr/bin/env python3
"""tests/test_v35_phase3_exam_grader.py —— v3.5 Phase 3 测试。

【验证目标】
v3.5 Phase 3 的核心改造：
  1. exam/exam_ai_reviewer.py 已下线，所有评分逻辑搬到 lib/exam_grader.py。
     prompts/exam_grader.json 提供固定 framing（角色 / 输出格式 / banned_patterns），
     rubric.json 与候选人提交在运行时拼装。
  2. exam/exam_prereview.py 整文件下线（review 不再 preview）。
  3. exam/cmd_exam_ai_review.py 走 lib.exam_grader.review_submission，
     --no-llm 干跑能加载 rubric / 拼 prompt 而不调外部 LLM。

不重复 test_v34_phase1 已经覆盖的 prompts.load_prompt / cache 行为。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

import tests.helpers as helpers  # noqa: F401  保证 _InMemoryTdb 注入

os.environ["RECRUIT_SUPPRESS_SELF_VERIFY_ALERT"] = "1"

import prompts  # noqa: E402
from lib import exam_grader  # noqa: E402
from lib.exam_grader import (  # noqa: E402
    DEFAULT_RUBRIC_PATH, RubricError, build_prompt, load_rubric,
    parse_response, review_submission,
)


def _stub_rubric():
    """最小可用 rubric：单维度 + 100 权重 + 简单 schema。"""
    return {
        "version": "test-rubric-v1",
        "exam_title": "test",
        "exam_summary": "",
        "exam_required_outputs": ["python.py"],
        "passing_hint": 60,
        "must_haves": ["python.py"],
        "dimensions": [
            {
                "key": "biz_understanding",
                "label": "业务理解",
                "weight": 100,
                "scoring_mode": "anchor",
                "anchors": {"0": "差", "100": "好"},
            },
        ],
        "ai_reviewer_instructions": ["保持中立"],
        "time_modifier": {"min": -5, "max": 5},
        "bonus_items": [],
        "penalties": [],
        "output_schema": {
            "dimension_scores": [{"key": "...", "score": 0, "reason": "..."}],
            "summary": "",
        },
    }


# ════════════════════════════════════════════════════════════════════════════
# 1. lib/exam_grader 库本身（无 IO，只校 framing 拼装与解析逻辑）
# ════════════════════════════════════════════════════════════════════════════

class TestExamGraderLibrary(unittest.TestCase):

    def setUp(self):
        prompts.clear_cache()

    def test_legacy_modules_offline(self):
        """v3.5：exam_ai_reviewer / exam_prereview 必须真的不能 import。"""
        import importlib
        for legacy in ("exam.exam_ai_reviewer", "exam.exam_prereview"):
            with self.assertRaises(ImportError, msg="{} 应已下线".format(legacy)):
                importlib.import_module(legacy)

    def test_default_rubric_path_resolves_to_repo(self):
        """exam_files/rubric.json 必须能被默认路径找到，否则 cmd_exam_ai_review
        在没有 --rubric 时会立即 RubricError。"""
        self.assertTrue(
            os.path.isfile(DEFAULT_RUBRIC_PATH),
            "exam_grader.DEFAULT_RUBRIC_PATH 指向不存在的文件: {}".format(
                DEFAULT_RUBRIC_PATH),
        )

    def test_load_rubric_rejects_bad_weight_sum(self):
        bad = _stub_rubric()
        bad["dimensions"][0]["weight"] = 80  # sum 不等于 100
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(bad, f)
            path = f.name
        try:
            with self.assertRaises(RubricError):
                load_rubric(path)
        finally:
            os.unlink(path)

    def test_build_prompt_uses_framing_from_prompts_dir(self):
        """build_prompt 必须把 prompts/exam_grader.json 的 role_system /
        output_format_note 拼进 prompt（v3.5 Phase 3 的核心）。"""
        rubric = _stub_rubric()
        candidate = {
            "candidate_label": "t_test",
            "exam_sent_at": "2026-04-19",
            "submitted_at": "2026-04-21",
            "code_files": [{"path": "main.py", "content": "print(1)"}],
        }
        prompt = build_prompt(rubric, candidate)
        framing = prompts.load_prompt("exam_grader")
        self.assertIn(framing["role_system"], prompt)
        self.assertIn(framing["output_format_note"], prompt)
        self.assertIn("main.py", prompt)
        # rubric 内容实际拼进 prompt（dimension key 是个稳定的可见信号）
        self.assertIn("biz_understanding", prompt)

    def test_parse_response_strips_banned_patterns(self):
        """v3.5：banned_patterns 来自 prompts/exam_grader.json。
        LLM 给出明显结论性措辞时必须被剥离。"""
        rubric = _stub_rubric()
        raw = json.dumps({
            "dimension_scores": [
                {"key": "biz_understanding", "score": 80,
                 "reason": "建议直接录取该候选人，水平很好"},
            ],
            "summary": "建议通过该候选人的笔试",
        })
        result = parse_response(raw, rubric)
        for d in result["dimension_scores"]:
            self.assertNotIn("建议直接录取", d.get("reason", ""))
            self.assertIn("[已按规则剥离结论性表述]", d.get("reason", ""))
        self.assertNotIn("建议通过", result["summary"])

    def test_parse_response_clamps_score_to_weight(self):
        """LLM 给超出 weight 的分必须被 clamp。"""
        rubric = _stub_rubric()
        raw = json.dumps({
            "dimension_scores": [
                {"key": "biz_understanding", "score": 999, "reason": "x"},
            ],
        })
        result = parse_response(raw, rubric)
        self.assertEqual(result["dimension_scores"][0]["score"], 100)
        self.assertEqual(result["main_score"], 100)

    def test_review_submission_returns_error_when_rubric_missing(self):
        """rubric 路径不存在时 review_submission 不抛异常，而是返回 _error 结构。
        cron / cmd_exam_ai_review 依赖这个契约做容错。"""
        result = review_submission(
            candidate={"candidate_label": "x"},
            rubric_path="/nonexistent/rubric.json",
        )
        self.assertEqual(result.get("_error"), "rubric_error")
        self.assertIn("不存在", result.get("_message", ""))

    def test_review_submission_propagates_llm_error(self):
        """LLM 失败时不应抛异常，而是 _error=llm_error，方便 agent / cron 重试或告警。"""
        from lib.dashscope_client import LLMError

        with mock.patch.object(
            exam_grader, "_chat_simple_prompt",
            side_effect=LLMError("network down"),
        ):
            result = review_submission(
                candidate={"candidate_label": "x",
                           "code_files": [{"path": "a.py", "content": "1"}]},
            )
        self.assertEqual(result.get("_error"), "llm_error")
        self.assertIn("network down", result.get("_message", ""))


# ════════════════════════════════════════════════════════════════════════════
# 2. exam/cmd_exam_ai_review CLI：--no-llm 路径走 lib.exam_grader.build_prompt
# ════════════════════════════════════════════════════════════════════════════

class TestCmdExamAiReviewIntegration(unittest.TestCase):
    """只覆盖 v3.5 Phase 3 关心的：CLI 改 import 后能拿到 lib.exam_grader 的能力。
    完整端到端走 LLM 的路径不在这里测，避免依赖 DashScope。"""

    def test_no_llm_dry_run_uses_lib_exam_grader(self):
        """--no-llm + --code-file 不调 LLM，但要求成功加载 rubric + 拼 prompt。
        若 lib.exam_grader 没有正确把 prompts/exam_grader.json 抽进来，
        prompt 内容会缺 role_system → 这里的字符数检查会下降很多。"""
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write("print('hello')\n")
            code_path = f.name
        try:
            out, err, rc = helpers.call_main("exam.cmd_exam_ai_review", [
                "--candidate-label", "t_test",
                "--code-file", code_path,
                "--no-llm",
                "--no-fetch",
            ])
            self.assertEqual(rc, 0, "stderr=" + err)
            self.assertIn("dry-run 通过", out)
            # prompt 长度应至少包含 framing (>200 chars 是底线)
            import re
            m = re.search(r"prompt (\d+) chars", out)
            self.assertIsNotNone(m, "无法提取 prompt 字数: " + out)
            self.assertGreater(int(m.group(1)), 500,
                               "prompt 太短，疑似 framing 没拼进来")
        finally:
            os.unlink(code_path)

    def test_save_event_without_talent_id_fails(self):
        """边界：--save-event 必须配 --talent-id，否则 CLI 必须早失败。"""
        out, err, rc = helpers.call_main("exam.cmd_exam_ai_review", [
            "--candidate-label", "t_test",
            "--no-llm", "--no-fetch",
            "--save-event",
        ])
        self.assertEqual(rc, 2)
        self.assertIn("--save-event 需要 --talent-id", err)


if __name__ == "__main__":
    unittest.main()
