#!/usr/bin/env python3
"""tests/test_v34_phase1.py —— v3.4 Phase 1 测试。

覆盖：
  - prompts.load_prompt 基础行为（成功 / 缺字段 / 不存在 / 缓存）
  - inbox.analyzer 的 stage-aware 路由
      * 通用 stage → inbox_general prompt（无 draft）
      * POST_OFFER_FOLLOWUP → post_offer_followup prompt（输出 draft）
  - _scrub_draft 自动剥承诺性措辞
  - outbound/cmd_send --use-cached-draft 完整路径
"""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import tests.helpers as helpers  # noqa: E402

os.environ["RECRUIT_SUPPRESS_SELF_VERIFY_ALERT"] = "1"

import prompts  # noqa: E402
from inbox import analyzer  # noqa: E402
from inbox.analyzer import _scrub_draft  # noqa: E402


def _mk_talent(talent_id="t_v34", name="李四", email="lisi@example.com",
               stage="POST_OFFER_FOLLOWUP"):
    helpers.mem_tdb._state.setdefault("candidates", {})[talent_id] = {
        "talent_id": talent_id,
        "candidate_name": name,
        "candidate_email": email,
        "current_stage": stage,
        "stage": stage,
    }
    return talent_id


# ════════════════════════════════════════════════════════════════════════════
# prompts/ 模块
# ════════════════════════════════════════════════════════════════════════════

class TestPromptsModule(unittest.TestCase):

    def setUp(self):
        prompts.clear_cache()

    def test_load_inbox_general_ok(self):
        p = prompts.load_prompt("inbox_general")
        self.assertEqual(p["version"], "inbox.general.v35.2026-04-21")
        self.assertFalse(p.get("has_draft"))
        self.assertIn("intent", p["output_fields"])

    def test_load_post_offer_followup_ok(self):
        p = prompts.load_prompt("post_offer_followup")
        self.assertTrue(p.get("has_draft"))
        self.assertIn("draft", p["output_fields"])
        self.assertGreater(len(p["banned_phrases"]), 0)

    def test_load_unknown_raises_not_found(self):
        with self.assertRaises(prompts.PromptNotFoundError):
            prompts.load_prompt("does_not_exist")

    def test_cache_returns_same_object(self):
        a = prompts.load_prompt("inbox_general")
        b = prompts.load_prompt("inbox_general")
        self.assertIs(a, b)

    def test_force_reload_resets_cache(self):
        a = prompts.load_prompt("inbox_general")
        b = prompts.load_prompt("inbox_general", force_reload=True)
        self.assertIsNot(a, b)
        self.assertEqual(a["version"], b["version"])

    def test_list_prompts_includes_both(self):
        names = prompts.list_prompts()
        self.assertIn("inbox_general", names)
        self.assertIn("post_offer_followup", names)


# ════════════════════════════════════════════════════════════════════════════
# inbox.analyzer stage-aware routing
# ════════════════════════════════════════════════════════════════════════════

class TestAnalyzerRouting(unittest.TestCase):

    def setUp(self):
        prompts.clear_cache()

    def _patch_llm(self, response_dict):
        """patch chat_completion 返回固定 JSON 字符串。"""
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

    def test_general_stage_uses_inbox_general_prompt(self):
        with self._patch_cfg(), self._patch_llm({
            "intent": "reschedule_request",
            "summary": "候选人请求改到下周三",
            "urgency": "high",
            "details": {"reason": "出差"},
        }):
            result = analyzer.analyze(
                candidate_name="张三",
                stage="ROUND1_SCHEDULED",
                stage_label="一面已排",
                subject="改期",
                body="老板，能否改到下周三？",
            )
        self.assertIsNotNone(result)
        self.assertEqual(result["intent"], "reschedule_request")
        self.assertTrue(result["need_boss_action"])  # 兜底规则
        self.assertNotIn("draft", result)
        self.assertEqual(result["_meta"]["prompt_name"], "inbox_general")

    def test_post_offer_stage_uses_followup_prompt_and_returns_draft(self):
        with self._patch_cfg(), self._patch_llm({
            "intent": "salary_negotiation",
            "summary": "候选人询问能否提高签字奖金",
            "urgency": "high",
            "details": {"ask": "+2w"},
            "draft": "您好，关于签字奖金的具体数字，我已转达老板，由他/HR 在确认后正式回复您。\n\nHermes 代发",
        }):
            result = analyzer.analyze(
                candidate_name="李四",
                stage="POST_OFFER_FOLLOWUP",
                stage_label="Offer 后跟进",
                subject="关于签字奖金",
                body="想了解能否提高 sign-on bonus",
            )
        self.assertIsNotNone(result)
        self.assertEqual(result["intent"], "salary_negotiation")
        self.assertTrue(result["need_boss_action"])
        self.assertIn("draft", result)
        self.assertIn("Hermes 代发", result["draft"])
        self.assertEqual(result["_meta"]["prompt_name"], "post_offer_followup")

    # v3.6: OFFER_HANDOFF 已合并进 POST_OFFER_FOLLOWUP；原
    # test_post_offer_offer_handoff_also_routes_to_followup 失去意义，删除。

    def test_no_dashscope_key_returns_none(self):
        with mock.patch("lib.config.get",
                        side_effect=lambda k: {} if k == "dashscope" else None):
            result = analyzer.analyze("a", "ROUND1_SCHEDULED", "一面",
                                      "x", "y")
        self.assertIsNone(result)

    def test_llm_failure_returns_none(self):
        with self._patch_cfg(), mock.patch(
            "lib.dashscope_client.chat_completion",
            side_effect=RuntimeError("network down"),
        ):
            result = analyzer.analyze("a", "ROUND1_SCHEDULED", "一面",
                                      "x", "y")
        self.assertIsNone(result)

    def test_llm_returns_invalid_json_returns_none(self):
        with self._patch_cfg(), mock.patch(
            "lib.dashscope_client.chat_completion",
            return_value=("this is not json", {}),
        ):
            result = analyzer.analyze("a", "ROUND1_SCHEDULED", "一面",
                                      "x", "y")
        self.assertIsNone(result)


# ════════════════════════════════════════════════════════════════════════════
# _scrub_draft 安全过滤
# ════════════════════════════════════════════════════════════════════════════

class TestScrubDraft(unittest.TestCase):

    def test_replaces_banned_and_appends_warning(self):
        out = _scrub_draft(
            "您好，我们一定会让您入职。",
            banned_phrases=["我们一定", "保证"],
        )
        self.assertNotIn("我们一定", out)
        self.assertIn("（待老板/HR 确认）", out)
        self.assertIn("[Hermes 提示]", out)

    def test_no_banned_phrase_unchanged(self):
        clean = "您好，关于您的问题我已转达老板。"
        self.assertEqual(_scrub_draft(clean, banned_phrases=["保证"]), clean)

    def test_empty_banned_list_safe(self):
        s = "任意内容"
        self.assertEqual(_scrub_draft(s, banned_phrases=[]), s)

    def test_empty_draft_safe(self):
        self.assertEqual(_scrub_draft("", banned_phrases=["保证"]), "")


# ════════════════════════════════════════════════════════════════════════════
# outbound/cmd_send --use-cached-draft
# ════════════════════════════════════════════════════════════════════════════

class TestCmdSendUseCachedDraft(unittest.TestCase):

    def setUp(self):
        helpers.wipe_state()

    def _seed_inbound(self, talent_id, draft_text="您好，已转达老板。\n\nHermes 代发",
                      message_id="<orig@candidate>", subject="关于薪资",
                      references_chain=None, intent="salary_negotiation"):
        eid = helpers.mem_tdb.insert_email_if_absent(
            talent_id=talent_id, message_id=message_id,
            direction="inbound", context="followup",
            sender="lisi@example.com", sent_at="2026-04-20T10:00:00",
            subject=subject,
            body_full="想了解能否提高薪资",
            references_chain=references_chain,
        )
        helpers.mem_tdb.set_email_analyzed(
            eid,
            ai_summary="询问薪资",
            ai_intent=intent,
            ai_payload={
                "intent": intent,
                "summary": "询问薪资",
                "draft": draft_text,
                "_meta": {"prompt_name": "post_offer_followup"},
            },
        )
        return eid

    def test_use_cached_draft_happy_path(self):
        tid = _mk_talent()
        eid = self._seed_inbound(tid)

        out, err, rc = helpers.call_main("outbound.cmd_send", [
            "--talent-id", tid,
            "--use-cached-draft", eid,
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        result = json.loads(out)
        self.assertEqual(result["template"], "cached_draft")
        # subject 自动 "Re: 关于薪资"
        self.assertEqual(result["subject"], "Re: 关于薪资")
        # outbound 邮件落表
        outbound_rows = [r for r in helpers.mem_tdb._emails.values()
                         if r["direction"] == "outbound"]
        self.assertEqual(len(outbound_rows), 1)
        sent = outbound_rows[0]
        self.assertIn("已转达老板", sent["body_full"])
        # 线程头自动续上原 message_id
        self.assertEqual(sent["in_reply_to"], "<orig@candidate>")
        self.assertEqual(sent["references_chain"], "<orig@candidate>")

    def test_use_cached_draft_subject_no_double_re(self):
        tid = _mk_talent()
        # 原邮件 subject 已是 "Re: ..."，不应叠加
        eid = self._seed_inbound(tid, subject="Re: 关于薪资")
        out, err, rc = helpers.call_main("outbound.cmd_send", [
            "--talent-id", tid,
            "--use-cached-draft", eid,
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        result = json.loads(out)
        self.assertEqual(result["subject"], "Re: 关于薪资")

    def test_use_cached_draft_override_subject(self):
        tid = _mk_talent()
        eid = self._seed_inbound(tid)
        out, err, rc = helpers.call_main("outbound.cmd_send", [
            "--talent-id", tid,
            "--use-cached-draft", eid,
            "--override-subject", "您好，关于您 4/20 来信",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        result = json.loads(out)
        self.assertEqual(result["subject"], "您好，关于您 4/20 来信")

    def test_use_cached_draft_appends_to_references_chain(self):
        tid = _mk_talent()
        eid = self._seed_inbound(
            tid, references_chain="<thread1@x> <thread2@x>")
        out, err, rc = helpers.call_main("outbound.cmd_send", [
            "--talent-id", tid,
            "--use-cached-draft", eid,
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        outbound_rows = [r for r in helpers.mem_tdb._emails.values()
                         if r["direction"] == "outbound"]
        sent = outbound_rows[0]
        self.assertEqual(sent["references_chain"],
                         "<thread1@x> <thread2@x> <orig@candidate>")

    def test_unknown_email_id_fails(self):
        tid = _mk_talent()
        out, err, rc = helpers.call_main("outbound.cmd_send", [
            "--talent-id", tid,
            "--use-cached-draft", "eml_doesnotexist",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("找不到", err)

    def test_talent_id_mismatch_fails(self):
        tid = _mk_talent()
        other = _mk_talent(talent_id="t_other", email="other@x.com")
        eid = self._seed_inbound(other)
        out, err, rc = helpers.call_main("outbound.cmd_send", [
            "--talent-id", tid,
            "--use-cached-draft", eid,
        ])
        self.assertEqual(rc, 1)
        self.assertIn("不一致", err)

    def test_outbound_email_id_rejected(self):
        tid = _mk_talent()
        # 制造一封 outbound 邮件
        eid = helpers.mem_tdb.insert_email_if_absent(
            talent_id=tid, message_id="<some-out@x>",
            direction="outbound", context="followup",
            sender="us@x.com", sent_at="2026-04-19",
        )
        out, err, rc = helpers.call_main("outbound.cmd_send", [
            "--talent-id", tid,
            "--use-cached-draft", eid,
        ])
        self.assertEqual(rc, 1)
        self.assertIn("inbound", err)

    def test_missing_draft_fails(self):
        tid = _mk_talent()
        # 没有 ai_payload.draft
        eid = helpers.mem_tdb.insert_email_if_absent(
            talent_id=tid, message_id="<no-draft@x>",
            direction="inbound", context="followup",
            sender="x@y.com", sent_at="2026-04-19",
            subject="hi", body_full="hi",
        )
        helpers.mem_tdb.set_email_analyzed(
            eid, ai_summary="x", ai_intent="other",
            ai_payload={"intent": "other", "summary": "x"},  # 没 draft
        )
        out, err, rc = helpers.call_main("outbound.cmd_send", [
            "--talent-id", tid,
            "--use-cached-draft", eid,
        ])
        self.assertEqual(rc, 1)
        self.assertIn("draft", err)


if __name__ == "__main__":
    unittest.main()
