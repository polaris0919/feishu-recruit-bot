#!/usr/bin/env python3
"""tests/test_run_chain.py —— v3.4 Phase 0.3 跨命令编排 helper 测试。

覆盖：
  - 单步成功
  - 多步链 + 占位符传递（{prev.field}）
  - 嵌套占位符（{prev.transition.to}）
  - 占位符引用 None → __NULL__
  - 失败短路（后续 step 不跑）
  - optional step 失败不阻断
  - 占位符引用未执行 step → 早失败
  - dry_run 自动透传
  - JSON 解析失败时的报错
"""
from __future__ import annotations

import json
import os
import unittest

import tests.helpers as helpers  # noqa: E402

os.environ["RECRUIT_SUPPRESS_SELF_VERIFY_ALERT"] = "1"

from lib.run_chain import Step, run_chain, ChainStepError  # noqa: E402


def _mk_talent(talent_id="t_chain123", name="链测试", email="chain@example.com",
               stage="NEW"):
    helpers.mem_tdb._state.setdefault("candidates", {})[talent_id] = {
        "talent_id": talent_id,
        "candidate_name": name,
        "candidate_email": email,
        "current_stage": stage,
        "stage": stage,
    }
    return talent_id


class TestRunChainBasic(unittest.TestCase):

    def setUp(self):
        helpers.wipe_state()

    def test_single_step_success(self):
        tid = _mk_talent()
        result = run_chain([
            Step("update", "talent.cmd_update", args=[
                "--talent-id", tid,
                "--set", "phone=18800001111",
            ]),
        ])
        self.assertTrue(result["ok"], result)
        self.assertIn("update", result["steps"])
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "phone"), "18800001111")

    def test_multi_step_with_placeholder(self):
        """v3.5 一面排期典型链（已无 wrapper，agent 直接拼）：
        先 send，再用 send.sent_at 写入 round1_invite_sent_at。"""
        tid = _mk_talent(stage="NEW")
        result = run_chain([
            Step("send", "outbound.cmd_send", args=[
                "--talent-id", tid,
                "--subject", "一面邀约",
                "--body", "请于 4 月 25 日 14:00 参加一面",
            ]),
            Step("update", "talent.cmd_update", args=[
                "--talent-id", tid,
                "--stage", "ROUND1_SCHEDULING",
                "--set", "round1_time=2026-04-25 14:00",
                "--set", "round1_invite_sent_at={send.sent_at}",
                "--set", "round1_confirm_status=PENDING",
                "--reason", "boss schedule round1",
            ]),
        ])
        self.assertTrue(result["ok"], result)
        # send 步把 message_id / sent_at 都吐出来
        self.assertIsNotNone(result["steps"]["send"]["message_id"])
        self.assertIsNotNone(result["steps"]["send"]["sent_at"])
        # update 步顺利推进 stage 并填了 round1_invite_sent_at（来自 placeholder）
        self.assertEqual(result["steps"]["update"]["transition"]["to"],
                         "ROUND1_SCHEDULING")
        self.assertEqual(
            helpers.mem_tdb.get_talent_current_stage(tid), "ROUND1_SCHEDULING")
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "round1_invite_sent_at"),
            result["steps"]["send"]["sent_at"],
        )

    def test_nested_placeholder(self):
        tid = _mk_talent(stage="NEW")
        result = run_chain([
            Step("update1", "talent.cmd_update", args=[
                "--talent-id", tid,
                "--stage", "ROUND1_SCHEDULING",
                "--set", "round1_time=2026-04-25 14:00",
                "--reason", "step1",
            ]),
            # 第二步通过占位符引用上一步的 transition.to
            Step("update2", "talent.cmd_update", args=[
                "--talent-id", tid,
                "--set", "round1_confirm_status={update1.transition.to}",
                "--reason", "step2",
            ]),
        ])
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "round1_confirm_status"),
            "ROUND1_SCHEDULING",
        )

    def test_placeholder_referencing_unknown_step_fails_fast(self):
        tid = _mk_talent()
        result = run_chain([
            Step("update", "talent.cmd_update", args=[
                "--talent-id", tid,
                "--set", "phone={ghost.message_id}",
            ]),
        ])
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_at"], "update")
        self.assertIn("ghost", result["error"])
        # phone 不应被改
        self.assertIsNone(helpers.mem_tdb.get_talent_field("t_chain123", "phone"))

    def test_short_circuit_on_step_failure(self):
        """第一步 unnatural transition 没加 --force → 失败 → 第二步不应执行。"""
        tid = _mk_talent(stage="NEW")
        result = run_chain([
            Step("bad_transition", "talent.cmd_update", args=[
                "--talent-id", tid,
                # NEW → POST_OFFER_FOLLOWUP 不在 natural 表（跨多步）
                "--stage", "POST_OFFER_FOLLOWUP",
            ]),
            Step("should_skip", "talent.cmd_update", args=[
                "--talent-id", tid,
                "--set", "phone=99999",
            ]),
        ])
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_at"], "bad_transition")
        # 第二步没跑：phone 不应被改
        self.assertIsNone(helpers.mem_tdb.get_talent_field(tid, "phone"))
        # 已完成的 steps 应当为空
        self.assertEqual(result["steps"], {})

    def test_optional_step_failure_does_not_block(self):
        tid = _mk_talent(stage="NEW")
        result = run_chain([
            Step("bad_optional", "talent.cmd_update", args=[
                "--talent-id", tid,
                "--stage", "POST_OFFER_FOLLOWUP",  # 失败（unnatural）
            ], optional=True),
            Step("succeed_after", "talent.cmd_update", args=[
                "--talent-id", tid,
                "--set", "phone=18811112222",
            ]),
        ])
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["steps"]["bad_optional"]["ok"])
        # 第二步真的跑了
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "phone"), "18811112222")

    def test_raise_on_failure(self):
        tid = _mk_talent(stage="NEW")
        with self.assertRaises(ChainStepError) as ctx:
            run_chain([
                Step("bad", "talent.cmd_update", args=[
                    "--talent-id", tid,
                    "--stage", "POST_OFFER_FOLLOWUP",
                ]),
            ], raise_on_failure=True)
        self.assertEqual(ctx.exception.step_name, "bad")
        self.assertGreater(ctx.exception.exit_code, 0)

    def test_dry_run_propagates(self):
        tid = _mk_talent(stage="NEW")
        result = run_chain([
            Step("send", "outbound.cmd_send", args=[
                "--talent-id", tid,
                "--subject", "x",
                "--body", "y",
            ]),
            Step("update", "talent.cmd_update", args=[
                "--talent-id", tid,
                "--stage", "ROUND1_SCHEDULING",
                "--set", "round1_time=2026-04-25 14:00",
            ]),
        ], dry_run=True)
        self.assertTrue(result["ok"], result)
        # 没有真的写状态
        self.assertEqual(helpers.mem_tdb.get_talent_current_stage(tid), "NEW")
        self.assertTrue(result["steps"]["send"]["dry_run"])
        self.assertTrue(result["steps"]["update"]["dry_run"])

    def test_invalid_step_name_raises(self):
        with self.assertRaises(ValueError):
            Step("123-bad", "x.y", args=[])

    def test_unknown_module_fails_gracefully(self):
        result = run_chain([
            Step("bogus", "no.such.module", args=["--talent-id", "x"]),
        ])
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_at"], "bogus")
        self.assertIn("import", result["error"])


if __name__ == "__main__":
    unittest.main()
