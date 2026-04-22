#!/usr/bin/env python3
"""tests/test_v35_phase1_inbox_general.py —— v3.5 Phase 1 测试。

【验证目标】
v3.5 Phase 1 的核心改造：
  1. exam/llm_analyzer.py + followup/followup_analyzer.py 已彻底下线，
     所有候选人邮件意图判断**只通过 inbox/analyzer.py 一个入口**完成。
  2. prompts/inbox_general.json 升级到 v35：valid_intents 现在覆盖了
     原来分散在 exam/followup analyzer 里的全部 intent（confirm_interview /
     reschedule_request / request_online / defer_until_shanghai /
     question_boss / exam_submitted / thanks_fyi / decline_withdraw / other）。
  3. inbox.analyzer.analyze 在 stage=ROUND1_SCHEDULED 等通用阶段时仍然
     精准路由到 inbox_general，并能正确兜底 need_boss_action。
  4. 通用 stage 不返回 draft（draft 是 post_offer_followup 专属能力）。

不重复 test_v34_phase1.TestPromptsModule / TestAnalyzerRouting 已经覆盖的行为，
本文件只覆盖 v3.5 新引入的契约。
"""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import tests.helpers as helpers  # noqa: F401  保证 _InMemoryTdb 注入

os.environ["RECRUIT_SUPPRESS_SELF_VERIFY_ALERT"] = "1"

import prompts  # noqa: E402
from inbox import analyzer  # noqa: E402


# ════════════════════════════════════════════════════════════════════════════
# 1. inbox_general.json schema：v3.5 是统一意图入口
# ════════════════════════════════════════════════════════════════════════════

class TestInboxGeneralPromptSchema(unittest.TestCase):

    def setUp(self):
        prompts.clear_cache()

    def test_version_bumped_to_v35(self):
        p = prompts.load_prompt("inbox_general")
        self.assertTrue(
            p["version"].startswith("inbox.general.v35."),
            "v3.5 起 inbox_general.json 必须以 inbox.general.v35. 开头，"
            "实际 version={!r}".format(p["version"]),
        )

    def test_valid_intents_contain_all_unified_buckets(self):
        """v3.5 把原 exam/llm_analyzer 与 followup/followup_analyzer 的 intent
        全部并入 inbox_general，缺一不可。缺失说明意图覆盖不完整、agent
        会拿到 'other' 做错误兜底。"""
        p = prompts.load_prompt("inbox_general")
        valid = set(p["valid_intents"])
        required = {
            # 排期 / 改期相关
            "confirm_interview",
            "reschedule_request",
            "request_online",
            "defer_until_shanghai",
            # 笔试提交（原 exam/llm_analyzer）
            "exam_submitted",
            # 兜底类
            "question_boss",
            "thanks_fyi",
            "decline_withdraw",
            "other",
        }
        missing = required - valid
        self.assertFalse(
            missing,
            "inbox_general.valid_intents 缺少 v3.5 必需的桶: {}".format(sorted(missing)),
        )

    def test_inbox_general_prompt_does_not_emit_draft(self):
        """通用阶段不应生成 draft —— draft 是 post_offer_followup 的专属能力。"""
        p = prompts.load_prompt("inbox_general")
        self.assertFalse(p.get("has_draft"))
        self.assertNotIn("draft", p.get("output_fields", []))

    def test_legacy_analyzer_modules_offline(self):
        """v3.5 Phase 1：旧的 exam/llm_analyzer + followup/followup_analyzer
        必须真的不可 import。否则有可能被遗留代码引用造成"双意图源"。"""
        import importlib
        for legacy in ("exam.llm_analyzer", "followup.followup_analyzer"):
            with self.assertRaises(ImportError, msg="{} 应已下线".format(legacy)):
                importlib.import_module(legacy)


# ════════════════════════════════════════════════════════════════════════════
# 2. stage-aware routing：通用 stage 都走 inbox_general
# ════════════════════════════════════════════════════════════════════════════

class TestStageAwareRouting(unittest.TestCase):
    """v3.6：除 POST_OFFER_FOLLOWUP 外，所有 stage（含 ROUND1_SCHEDULED /
    EXAM_SENT / ROUND2_SCHEDULING 等）都走 inbox_general。
    （v3.5 原文写的 "POST_OFFER_FOLLOWUP / OFFER_HANDOFF"；v3.6 下线了 OFFER_HANDOFF。）"""

    def setUp(self):
        prompts.clear_cache()

    def _patch_llm(self, response_dict):
        return mock.patch(
            "lib.dashscope_client.chat_completion",
            return_value=(json.dumps(response_dict), {"raw": "ok"}),
        )

    def _patch_cfg(self):
        return mock.patch(
            "lib.config.get",
            side_effect=lambda key: {
                "dashscope": {"api_key": "fake-key", "model": "qwen3-max"}
            }.get(key),
        )

    def _analyze(self, stage, intent, summary="候选人有反馈", urgency="medium",
                 details=None):
        with self._patch_cfg(), self._patch_llm({
            "intent": intent,
            "summary": summary,
            "urgency": urgency,
            "details": details or {},
        }):
            return analyzer.analyze(
                candidate_name="测试",
                stage=stage,
                stage_label=stage,
                subject="主题",
                body="正文",
            )

    def test_exam_sent_stage_routes_to_general(self):
        result = self._analyze("EXAM_SENT", "exam_submitted",
                               summary="候选人提交笔试附件",
                               details={"has_attachment": True})
        self.assertIsNotNone(result)
        self.assertEqual(result["_meta"]["prompt_name"], "inbox_general")
        self.assertEqual(result["intent"], "exam_submitted")

    def test_round1_scheduled_routes_to_general_with_reschedule(self):
        result = self._analyze(
            "ROUND1_SCHEDULED", "reschedule_request",
            summary="候选人希望改到 4/30 下午",
            urgency="high",
            details={"reason": "出差冲突", "new_time": "2026-04-30 15:00"},
        )
        self.assertEqual(result["_meta"]["prompt_name"], "inbox_general")
        self.assertEqual(result["intent"], "reschedule_request")
        self.assertTrue(result["need_boss_action"], "reschedule 必须兜底为 True")

    def test_round2_scheduled_request_online_marks_need_boss(self):
        result = self._analyze("ROUND2_SCHEDULED", "request_online",
                               summary="想改成腾讯会议",
                               details={"preferred_tool": "腾讯会议"})
        self.assertEqual(result["_meta"]["prompt_name"], "inbox_general")
        self.assertTrue(result["need_boss_action"])

    def test_thanks_fyi_does_not_force_boss_action(self):
        """thanks_fyi 不在 _NEED_BOSS_INTENTS，need_boss_action 应保持 LLM 给的 false。"""
        result = self._analyze("ROUND1_SCHEDULED", "thanks_fyi",
                               summary="候选人致谢", urgency="low")
        self.assertFalse(result["need_boss_action"])

    def test_general_stage_never_returns_draft(self):
        """v3.6：除 POST_OFFER_FOLLOWUP 外，draft 必须不出现在结果里，
        即使 LLM 误返回 draft，inbox_general prompt has_draft=False，
        analyzer 不应回填该字段。"""
        with self._patch_cfg(), mock.patch(
            "lib.dashscope_client.chat_completion",
            return_value=(json.dumps({
                "intent": "thanks_fyi",
                "summary": "感谢",
                "urgency": "low",
                "draft": "您好，我已把这个事情记录下来。",
            }), {"raw": "ok"}),
        ):
            result = analyzer.analyze("张三", "ROUND1_SCHEDULED", "一面已排",
                                      "Re: 面试安排", "感谢老板！")
        self.assertNotIn("draft", result,
                         "通用 stage 走 inbox_general 不应保留 draft 字段")

    def test_unknown_intent_falls_back_to_other(self):
        """LLM 返回桶外 intent → analyzer 必须强制 coerce 到 'other'。"""
        result = self._analyze("ROUND1_SCHEDULED", "weird_unknown_bucket",
                               summary="奇怪的 LLM 输出", urgency="low")
        self.assertEqual(result["intent"], "other")


if __name__ == "__main__":
    unittest.main()
