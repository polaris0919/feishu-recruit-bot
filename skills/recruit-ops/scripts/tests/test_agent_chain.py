#!/usr/bin/env python3
"""tests/test_agent_chain.py —— v3.5 端到端 agent chain 回归。

【为什么有这个文件】
v3.5 起 round1 / round2 / followup / interview / common 下的剧本 wrapper
（cmd_round1_schedule / interview.cmd_{confirm,defer,reschedule} /
cmd_finalize_interview_time / cmd_followup_reply / cmd_followup_close ...）
全部下线。原本由这些 wrapper 编码的"业务剧本"现在由 agent + lib.run_chain
在运行时拼接 atomic CLI。

为了避免回归，本文件覆盖 7 条最常被 agent 使用的链：

  1. 一面排期（NEW → ROUND1_SCHEDULING）
       outbound.cmd_send (round1_invite) + talent.cmd_update
  2. 一面改期（ROUND1_SCHEDULED → ROUND1_SCHEDULING，含日历删除）
       feishu.cmd_calendar_delete + outbound.cmd_send (reschedule)
       + talent.cmd_update
  3. 候选人在国外暂缓（ROUND1_SCHEDULED → WAIT_RETURN）
       outbound.cmd_send (defer) + talent.cmd_update
  4. 笔试通过转二面（EXAM_REVIEWED → ROUND2_SCHEDULING）
       outbound.cmd_send (round2_invite) + talent.cmd_update
  5. post-offer 一键发草稿（cached_draft → 推送 boss 通知）
       outbound.cmd_send (--use-cached-draft) + feishu.cmd_notify
  6. 笔试不过保留池（EXAM_REVIEWED → EXAM_REJECT_KEEP，v3.5.1 新增）
       outbound.cmd_send (rejection_generic) + talent.cmd_update
  7. WAIT_RETURN 候选人主动联系（v3.5.1 新增，纯通知 chain）
       feishu.cmd_notify（不写任何 talent 字段）

每条链都对照原 wrapper 的最小观察点（stage、关键字段、邮件落表、calendar
副作用）做断言。链失败任意一步必须能在断言中暴露。
"""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import tests.helpers as helpers  # noqa: E402

os.environ["RECRUIT_SUPPRESS_SELF_VERIFY_ALERT"] = "1"

from lib.run_chain import Step, run_chain  # noqa: E402


# ─── 公共 fixture ────────────────────────────────────────────────────────────

def _seed_talent(talent_id, *, stage, name="测试候选人",
                 email="cand@example.com", **extra):
    helpers.mem_tdb._state.setdefault("candidates", {})[talent_id] = {
        "talent_id": talent_id,
        "candidate_name": name,
        "candidate_email": email,
        "current_stage": stage,
        "stage": stage,
        **extra,
    }
    return talent_id


def _outbound_emails(talent_id):
    return [r for (tid, _mid), r in helpers.mem_tdb._emails.items()
            if tid == talent_id and r["direction"] == "outbound"]


# ════════════════════════════════════════════════════════════════════════════
# Chain 1：一面排期（NEW → ROUND1_SCHEDULING）
# ════════════════════════════════════════════════════════════════════════════

class TestRound1ScheduleChain(unittest.TestCase):
    """替代旧 round1/cmd_round1_schedule 剧本 wrapper。"""

    def setUp(self):
        helpers.wipe_state()

    def test_round1_invite_chain_advances_stage_and_records_invite(self):
        tid = _seed_talent("t_r1sch", stage="NEW")

        result = run_chain([
            Step("send", "outbound.cmd_send", args=[
                "--talent-id", tid,
                "--template", "round1_invite",
                "--vars",
                "round1_time=2026-04-25 14:00",
                "position_suffix=（量化研究实习生）",
                "location=上海市浦东新区",
            ]),
            Step("update", "talent.cmd_update", args=[
                "--talent-id", tid,
                "--stage", "ROUND1_SCHEDULING",
                "--set", "round1_time=2026-04-25 14:00",
                "--set", "round1_invite_sent_at={send.sent_at}",
                "--set", "round1_confirm_status=PENDING",
                "--set", "round1_calendar_event_id=__NULL__",
                "--set", "wait_return_round=__NULL__",
                "--reason", "boss schedule round1",
            ]),
        ])

        self.assertTrue(result["ok"], result)
        # stage 推进
        self.assertEqual(
            helpers.mem_tdb.get_talent_current_stage(tid),
            "ROUND1_SCHEDULING")
        # 邀约信落表
        outs = _outbound_emails(tid)
        self.assertEqual(len(outs), 1)
        self.assertEqual(outs[0]["template"], "round1_invite")
        # 关键字段
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "round1_time"),
            "2026-04-25 14:00")
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "round1_confirm_status"),
            "PENDING")
        # invite_sent_at 用 send.sent_at 占位符填，与 cmd_send 输出一致
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "round1_invite_sent_at"),
            result["steps"]["send"]["sent_at"])

    def test_round1_chain_short_circuits_when_send_fails(self):
        """cmd_send 失败时（候选人邮箱缺失），第二步必须不执行，
        stage 不应被错误地推到 ROUND1_SCHEDULING。"""
        tid = _seed_talent("t_r1bad", stage="NEW", email="")  # 邮箱缺失

        result = run_chain([
            Step("send", "outbound.cmd_send", args=[
                "--talent-id", tid,
                "--template", "round1_invite",
                "--vars", "round1_time=2026-04-25 14:00",
            ]),
            Step("update", "talent.cmd_update", args=[
                "--talent-id", tid,
                "--stage", "ROUND1_SCHEDULING",
                "--set", "round1_time=2026-04-25 14:00",
            ]),
        ])

        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_at"], "send")
        # stage 必须保持不变（chain 短路保证）
        self.assertEqual(helpers.mem_tdb.get_talent_current_stage(tid), "NEW")
        # 没有任何 outbound 邮件落表
        self.assertEqual(_outbound_emails(tid), [])


# ════════════════════════════════════════════════════════════════════════════
# Chain 2：一面已确认后改期（含日历删除）
# ════════════════════════════════════════════════════════════════════════════

class TestRound1RescheduleChain(unittest.TestCase):
    """替代旧 interview.cmd_reschedule + 旧 daily_exam_review reschedule 分支。"""

    def setUp(self):
        helpers.wipe_state()

    def test_reschedule_chain_clears_calendar_and_resets_status(self):
        tid = _seed_talent(
            "t_r1resch", stage="ROUND1_SCHEDULED",
            round1_time="2026-04-25 14:00",
            round1_confirm_status="CONFIRMED",
            round1_calendar_event_id="evt_old_123",
        )

        # mock 真实飞书 API。注意：mock 替换了整个函数，因此 lib/feishu 内部的
        # side_effects_disabled() 早返回路径不会触发；这里直接观察 mock 是否被
        # cmd_calendar_delete 正确调用。
        with mock.patch("lib.feishu.delete_calendar_event_by_id",
                        return_value=True) as mock_del:
            result = run_chain([
                Step("cal_del", "feishu.cmd_calendar_delete", args=[
                    "--event-id", "evt_old_123",
                    "--reason", "候选人改期",
                ]),
                Step("send", "outbound.cmd_send", args=[
                    "--talent-id", tid,
                    "--template", "reschedule",
                    "--vars",
                    "round_label=一面",
                    "old_time=2026-04-25 14:00",
                    "new_time=2026-04-30 15:00",
                    "location=上海市浦东新区",
                ]),
                Step("update", "talent.cmd_update", args=[
                    "--talent-id", tid,
                    "--stage", "ROUND1_SCHEDULING",
                    "--set", "round1_time=2026-04-30 15:00",
                    "--set", "round1_confirm_status=PENDING",
                    "--set", "round1_calendar_event_id=__NULL__",
                    "--set", "round1_invite_sent_at={send.sent_at}",
                    "--reason", "candidate reschedule confirmed slot",
                ]),
            ])

        self.assertTrue(result["ok"], result)
        # 因为 RECRUIT_DISABLE_SIDE_EFFECTS=1 让 lib.feishu.delete 在调用前就短路，
        # mock 不会被实际触发；改期 chain 仍要求 cmd_calendar_delete 走 dry path。
        self.assertTrue(result["steps"]["cal_del"]["ok"])
        # stage 回到 SCHEDULING
        self.assertEqual(
            helpers.mem_tdb.get_talent_current_stage(tid), "ROUND1_SCHEDULING")
        # 关键字段：时间改到新值，calendar_event_id 清空，状态回到 PENDING
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "round1_time"),
            "2026-04-30 15:00")
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "round1_confirm_status"),
            "PENDING")
        self.assertIsNone(
            helpers.mem_tdb.get_talent_field(tid, "round1_calendar_event_id"))
        # cmd_calendar_delete 必须真的调用了 lib.feishu.delete_calendar_event_by_id
        # （传入旧的 event_id），这是改期 chain 的"清旧日历"语义证据
        mock_del.assert_called_once_with("evt_old_123")


# ════════════════════════════════════════════════════════════════════════════
# Chain 3：候选人在国外暂缓（ROUND1_SCHEDULED → WAIT_RETURN）
# ════════════════════════════════════════════════════════════════════════════

class TestDeferUntilReturnChain(unittest.TestCase):
    """替代旧 interview.cmd_defer 剧本 wrapper（被 daily_exam_review 触发）。"""

    def setUp(self):
        helpers.wipe_state()

    def test_defer_round1_chain_moves_to_wait_return(self):
        tid = _seed_talent(
            "t_defer1", stage="ROUND1_SCHEDULED",
            round1_time="2026-04-25 14:00",
            round1_confirm_status="CONFIRMED",
            round1_calendar_event_id="evt_defer_123",
        )

        result = run_chain([
            Step("send", "outbound.cmd_send", args=[
                "--talent-id", tid,
                "--template", "defer",
                "--vars", "round_label=一面",
            ]),
            Step("update", "talent.cmd_update", args=[
                "--talent-id", tid,
                "--stage", "WAIT_RETURN",
                "--set", "wait_return_round=1",
                "--set", "round1_time=__NULL__",
                "--set", "round1_calendar_event_id=__NULL__",
                "--set", "round1_confirm_status=UNSET",
                "--reason", "candidate not in country, defer until return",
            ]),
        ])

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            helpers.mem_tdb.get_talent_current_stage(tid), "WAIT_RETURN")
        # 关键字段：wait_return_round 标 1，round1_time 清空
        # _InMemoryTdb 不做类型转换，所以 "1" 会被保存成字符串；生产 PG 会自动转 int。
        self.assertEqual(
            str(helpers.mem_tdb.get_talent_field(tid, "wait_return_round")), "1")
        self.assertIsNone(
            helpers.mem_tdb.get_talent_field(tid, "round1_time"))
        self.assertIsNone(
            helpers.mem_tdb.get_talent_field(tid, "round1_calendar_event_id"))
        # 暂缓通知已发出
        outs = _outbound_emails(tid)
        self.assertEqual(len(outs), 1)
        self.assertEqual(outs[0]["template"], "defer")


# ════════════════════════════════════════════════════════════════════════════
# Chain 4：笔试通过转二面（EXAM_REVIEWED → ROUND2_SCHEDULING）
# ════════════════════════════════════════════════════════════════════════════

class TestExamPassToRound2Chain(unittest.TestCase):
    """替代旧 cmd_exam_result --result pass 一体化逻辑里的"通知 + 推 stage"两部分。
    （cmd_exam_result 仍可用，但 v3.5 推荐 agent 走两步原子 chain，
    便于 chain 的任一步出问题都可单独排查。）"""

    def setUp(self):
        helpers.wipe_state()

    def test_exam_pass_chain_advances_to_round2_scheduling(self):
        tid = _seed_talent(
            "t_exampass", stage="EXAM_REVIEWED",
            exam_sent_at="2026-04-19T05:00:00Z",
        )

        result = run_chain([
            Step("send", "outbound.cmd_send", args=[
                "--talent-id", tid,
                "--template", "round2_invite",
                "--vars",
                "round2_time=2026-05-08 10:00",
                "location=上海市浦东新区",
            ]),
            Step("update", "talent.cmd_update", args=[
                "--talent-id", tid,
                "--stage", "ROUND2_SCHEDULING",
                "--set", "round2_time=2026-05-08 10:00",
                "--set", "round2_invite_sent_at={send.sent_at}",
                "--set", "round2_confirm_status=PENDING",
                "--set", "round2_calendar_event_id=__NULL__",
                "--reason", "exam passed, advance to round2",
            ]),
        ])

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            helpers.mem_tdb.get_talent_current_stage(tid),
            "ROUND2_SCHEDULING")
        # 二面邀约信落表
        outs = _outbound_emails(tid)
        self.assertEqual(len(outs), 1)
        self.assertEqual(outs[0]["template"], "round2_invite")
        # round2 字段就位
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "round2_time"),
            "2026-05-08 10:00")
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "round2_confirm_status"),
            "PENDING")
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "round2_invite_sent_at"),
            result["steps"]["send"]["sent_at"])


# ════════════════════════════════════════════════════════════════════════════
# Chain 5：post-offer 一键发缓存草稿 + 通知 boss
# ════════════════════════════════════════════════════════════════════════════

class TestPostOfferOneClickSendChain(unittest.TestCase):
    """替代旧 followup/cmd_followup_reply.py 整套剧本 wrapper。
    v3.5：候选人邮件已被 inbox.cmd_analyze 走 post_offer_followup prompt
    生成草稿并落入 ai_payload.draft；老板审阅后说"OK 发"，agent 用
    cached_draft 一键发，再推一条飞书告诉老板"已代发"。"""

    def setUp(self):
        helpers.wipe_state()

    def _seed_post_offer_with_draft(self, tid, *, draft_text):
        _seed_talent(tid, stage="POST_OFFER_FOLLOWUP",
                     name="李四", email="lisi@example.com")
        eid = helpers.mem_tdb.insert_email_if_absent(
            talent_id=tid, message_id="<incoming@cand>",
            direction="inbound", context="followup",
            sender="lisi@example.com", sent_at="2026-04-20T10:00:00",
            subject="关于签字奖金",
            body_full="想了解能否提高 sign-on bonus",
        )
        helpers.mem_tdb.set_email_analyzed(
            eid,
            ai_summary="候选人询问签字奖金",
            ai_intent="salary_negotiation",
            ai_payload={
                "intent": "salary_negotiation",
                "summary": "候选人询问签字奖金",
                "draft": draft_text,
                "_meta": {"prompt_name": "post_offer_followup"},
            },
        )
        return eid

    def test_one_click_send_chain_sends_draft_and_notifies_boss(self):
        tid = "t_postoffer1"
        eid = self._seed_post_offer_with_draft(
            tid, draft_text="您好，关于签字奖金，已转达老板，待确认后回复。\n\nHermes 代发")

        with mock.patch("lib.feishu.send_text", return_value=True) as mock_boss:
            result = run_chain([
                Step("send", "outbound.cmd_send", args=[
                    "--talent-id", tid,
                    "--use-cached-draft", eid,
                ]),
                Step("notify", "feishu.cmd_notify", args=[
                    "--title", "已代发 post-offer 回信",
                    "--body", "talent={} email_id={}".format(tid, eid),
                    "--severity", "info",
                    "--source", "agent.post_offer_one_click",
                ]),
            ])

        self.assertTrue(result["ok"], result)
        # 1) 邮件已发并入 talent_emails（outbound）
        outs = _outbound_emails(tid)
        self.assertEqual(len(outs), 1)
        sent = outs[0]
        self.assertEqual(sent["template"], "cached_draft")
        self.assertIn("Hermes 代发", sent["body_full"])
        # 2) 主题自动 "Re: 原 subject"
        self.assertEqual(sent["subject"], "Re: 关于签字奖金")
        # 3) 线程头自动续上原 message_id
        self.assertEqual(sent["in_reply_to"], "<incoming@cand>")
        # 4) 飞书 boss 通道收到一条「已代发」通知
        mock_boss.assert_called_once()
        text = mock_boss.call_args[0][0]
        self.assertIn("已代发 post-offer 回信", text)
        self.assertIn(tid, text)
        self.assertIn(eid, text)

    def test_one_click_send_fails_when_draft_missing(self):
        """ai_payload 里没有 draft 时，send 必须 rc!=0；notify 不应执行。"""
        tid = "t_postoffer2"
        _seed_talent(tid, stage="POST_OFFER_FOLLOWUP",
                     email="x@y.com")
        eid = helpers.mem_tdb.insert_email_if_absent(
            talent_id=tid, message_id="<no-draft@cand>",
            direction="inbound", context="followup",
            sender="x@y.com", sent_at="2026-04-20",
            subject="hi", body_full="hi",
        )
        helpers.mem_tdb.set_email_analyzed(
            eid, ai_summary="x", ai_intent="other",
            ai_payload={"intent": "other", "summary": "x"},  # 没 draft
        )

        with mock.patch("lib.feishu.send_text", return_value=True) as mock_boss:
            result = run_chain([
                Step("send", "outbound.cmd_send", args=[
                    "--talent-id", tid,
                    "--use-cached-draft", eid,
                ]),
                Step("notify", "feishu.cmd_notify", args=[
                    "--title", "已代发", "--body", "should not happen",
                ]),
            ])

        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_at"], "send")
        # notify 必须没执行
        mock_boss.assert_not_called()
        # 没有 outbound 邮件落表
        self.assertEqual(_outbound_emails(tid), [])


# ════════════════════════════════════════════════════════════════════════════
# Chain 6：笔试不过保留池（EXAM_REVIEWED → EXAM_REJECT_KEEP）
# ════════════════════════════════════════════════════════════════════════════

class TestExamRejectKeepChain(unittest.TestCase):
    """v3.5.1 新增：替代旧 exam.cmd_exam_result --result reject_keep 在「需要发拒信」
    场景下的人工 agent 路径。注意 cmd_exam_result 本身不发邮件，只改 stage；
    本 chain 把发邮件 + 改 stage 拆成两个 atomic CLI，让 agent 完整走完通知 + 入库。
    """

    def setUp(self):
        helpers.wipe_state()

    def test_exam_reject_keep_chain_sends_rejection_and_advances(self):
        tid = _seed_talent("t_rej_keep", stage="EXAM_REVIEWED",
                           name="王同学", email="wang@example.com")

        result = run_chain([
            Step("send", "outbound.cmd_send", args=[
                "--talent-id", tid,
                "--template", "rejection_generic",
            ]),
            Step("update", "talent.cmd_update", args=[
                "--talent-id", tid,
                "--stage", "EXAM_REJECT_KEEP",
                "--reason", "agent: exam reject keep (per boss decision)",
            ]),
        ])

        self.assertTrue(result["ok"], result)
        # stage 推进到终态（保留池）
        self.assertEqual(
            helpers.mem_tdb.get_talent_current_stage(tid), "EXAM_REJECT_KEEP")
        # 发了一封 outbound 拒信
        outs = _outbound_emails(tid)
        self.assertEqual(len(outs), 1)
        self.assertEqual(outs[0]["template"], "rejection_generic")
        # 主题包含「感谢关注」（template 渲染后的一部分）
        self.assertIn("感谢关注", outs[0]["subject"])
        # 候选人姓名进了 body
        self.assertIn("王同学", outs[0]["body_full"])

    def test_exam_reject_keep_chain_short_circuits_on_send_failure(self):
        """如果候选人邮箱缺失（发邮件失败），stage 必须保持原样不被推到 EXAM_REJECT_KEEP。"""
        tid = _seed_talent("t_rej_no_email", stage="EXAM_REVIEWED",
                           name="无邮箱同学", email=None)

        result = run_chain([
            Step("send", "outbound.cmd_send", args=[
                "--talent-id", tid,
                "--template", "rejection_generic",
            ]),
            Step("update", "talent.cmd_update", args=[
                "--talent-id", tid,
                "--stage", "EXAM_REJECT_KEEP",
                "--reason", "should not reach here",
            ]),
        ])

        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_at"], "send")
        # stage 必须保持 EXAM_REVIEWED（没被推到终态）
        self.assertEqual(
            helpers.mem_tdb.get_talent_current_stage(tid), "EXAM_REVIEWED")
        self.assertEqual(_outbound_emails(tid), [])


# ════════════════════════════════════════════════════════════════════════════
# Chain 7：WAIT_RETURN 候选人主动联系（纯通知 chain，零写动作）
# ════════════════════════════════════════════════════════════════════════════

class TestWaitReturnPokeChain(unittest.TestCase):
    """v3.5.1 新增：候选人 WAIT_RETURN 期间主动来信，agent **不**自动恢复 stage，
    只推一张飞书 warn 卡片让老板拍。这条 chain 只有一步，但要保证：
      1. 飞书必发（否则老板看不到主动联系）
      2. 不动 stage / 不动任何 talent 字段
    """

    def setUp(self):
        helpers.wipe_state()

    def test_wait_return_poke_only_notifies_does_not_change_stage(self):
        tid = _seed_talent("t_waitret", stage="WAIT_RETURN",
                           name="出差同学", email="travel@example.com",
                           wait_return_round=1)
        # 模拟 WAIT_RETURN 期间字段：round1_time 已被清空
        helpers.mem_tdb._state["candidates"][tid]["round1_time"] = None
        helpers.mem_tdb._state["candidates"][tid]["round1_calendar_event_id"] = None

        with mock.patch("lib.feishu.send_text", return_value=True) as mock_boss:
            result = run_chain([
                Step("notify", "feishu.cmd_notify", args=[
                    "--severity", "warn",
                    "--title", "WAIT_RETURN 候选人主动联系",
                    "--body",
                    "talent={tid} round=1\n"
                    "intent=return_to_shanghai summary=已回上海，可继续\n\n"
                    "建议下一步：\n"
                    "  1) talent.cmd_update --talent-id {tid} "
                    "--stage ROUND1_SCHEDULING --reason \"candidate returned\"\n"
                    "  2) outbound.cmd_send --talent-id {tid} "
                    "--template round1_invite --vars round1_time=… location=…".format(
                        tid=tid),
                    "--source", "agent.wait_return_poke",
                ]),
            ])

        self.assertTrue(result["ok"], result)
        # 飞书 boss 通道收到了一张 warn 卡片
        mock_boss.assert_called_once()
        text = mock_boss.call_args[0][0]
        self.assertIn("WAIT_RETURN 候选人主动联系", text)
        self.assertIn(tid, text)
        self.assertIn("ROUND1_SCHEDULING", text)
        # stage 必须保持 WAIT_RETURN（agent 不自动恢复）
        self.assertEqual(
            helpers.mem_tdb.get_talent_current_stage(tid), "WAIT_RETURN")
        # round1_time / event_id 仍然是 None（agent 没碰任何字段）
        self.assertIsNone(
            helpers.mem_tdb.get_talent_field(tid, "round1_time"))
        self.assertIsNone(
            helpers.mem_tdb.get_talent_field(tid, "round1_calendar_event_id"))
        # 没有任何 outbound 邮件
        self.assertEqual(_outbound_emails(tid), [])


# ════════════════════════════════════════════════════════════════════════════
# Chain 8：发放 onboarding offer（POST_OFFER_FOLLOWUP，附件 + HR 飞书通知）
# v3.5.5 新增。AGENT_RULES.md §5.10。
# ════════════════════════════════════════════════════════════════════════════

class TestOnboardingOfferChain(unittest.TestCase):
    """覆盖 §5.10：
      1. outbound.cmd_send 模板 onboarding_offer + --attach <docx>
      2. feishu.cmd_notify --to hr 推 HR 飞书

    断言：
      - 候选人收到带模板渲染 + 附件元数据的 outbound 邮件
      - HR 飞书通道收到通知（不是 boss）
      - stage 不变（保持 POST_OFFER_FOLLOWUP）
      - 附件不存在 / chain 第一步必失败短路（HR 不被通知）
    """

    def setUp(self):
        helpers.wipe_state()
        # 测试用临时附件文件（生产用 /home/admin/openclaw-workspace-import/...）
        import tempfile
        self._tmpdir = tempfile.mkdtemp(prefix="onboarding_test_")
        self.attach_path = os.path.join(self._tmpdir, "实习生入职信息登记表-test.docx")
        with open(self.attach_path, "wb") as f:
            f.write(b"PK\x03\x04 fake docx bytes for test")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_onboarding_offer_chain_sends_email_and_notifies_hr(self):
        tid = _seed_talent("t_offer1", stage="POST_OFFER_FOLLOWUP",
                           name="冯同学", email="feng@example.com",
                           position="量化研究员")

        with mock.patch("lib.feishu.send_text", return_value=True) as mock_boss, \
             mock.patch("lib.feishu.send_text_to_hr", return_value=True) as mock_hr:
            result = run_chain([
                Step("send", "outbound.cmd_send", args=[
                    "--talent-id", tid,
                    "--template", "onboarding_offer",
                    "--vars",
                    "position_title=量化研究员",
                    "interview_feedback=面试表现突出，逻辑清晰。",
                    "daily_rate=350",
                    "onboard_date=2026-05-06",
                    "location=上海市浦东新区",
                    "evaluation_criteria=实习期前 1 个月为试用期。",
                    "--attach", self.attach_path,
                ]),
                Step("notify", "feishu.cmd_notify", args=[
                    "--to", "hr",
                    "--severity", "info",
                    "--title", "新候选人 offer 已发，请准备入职",
                    "--body",
                    "candidate={tid} name=冯同学\n入职日期=2026-05-06\n薪资=350 元/天\n岗位=量化研究员".format(tid=tid),
                    "--source", "agent.onboarding_offer",
                ]),
            ])

        self.assertTrue(result["ok"], result)

        # stage 不变（onboarding offer 不动 stage）
        self.assertEqual(
            helpers.mem_tdb.get_talent_current_stage(tid), "POST_OFFER_FOLLOWUP")

        # 候选人收到 outbound 邮件 + 模板正确
        outs = _outbound_emails(tid)
        self.assertEqual(len(outs), 1)
        self.assertEqual(outs[0]["template"], "onboarding_offer")
        self.assertIn("【录用通知】", outs[0]["subject"])
        self.assertIn("量化研究员", outs[0]["body_full"])
        self.assertIn("2026-05-06", outs[0]["body_full"])
        self.assertIn("350 元 / 天", outs[0]["body_full"])
        self.assertIn("面试表现突出，逻辑清晰。", outs[0]["body_full"])

        # HR 飞书被通知；boss 没被通知
        mock_hr.assert_called_once()
        hr_text = mock_hr.call_args[0][0]
        self.assertIn("新候选人 offer 已发", hr_text)
        self.assertIn(tid, hr_text)
        self.assertIn("2026-05-06", hr_text)
        mock_boss.assert_not_called()

    def test_onboarding_offer_chain_short_circuits_on_missing_attachment(self):
        """附件路径不存在 → 第一步失败 → HR 不应被通知（防止"邮件没发但 HR 以为发了"）。"""
        tid = _seed_talent("t_offer_noattach", stage="POST_OFFER_FOLLOWUP",
                           name="缺附件同学", email="x@example.com",
                           position="量化研究员")

        bad_path = os.path.join(self._tmpdir, "does-not-exist.docx")

        with mock.patch("lib.feishu.send_text", return_value=True) as mock_boss, \
             mock.patch("lib.feishu.send_text_to_hr", return_value=True) as mock_hr:
            result = run_chain([
                Step("send", "outbound.cmd_send", args=[
                    "--talent-id", tid,
                    "--template", "onboarding_offer",
                    "--vars",
                    "position_title=量化研究员",
                    "interview_feedback=测试",
                    "daily_rate=350",
                    "onboard_date=2026-05-06",
                    "location=上海",
                    "evaluation_criteria=测试",
                    "--attach", bad_path,
                ]),
                Step("notify", "feishu.cmd_notify", args=[
                    "--to", "hr",
                    "--severity", "info",
                    "--title", "should not reach here",
                    "--body", "should not reach here",
                ]),
            ])

        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_at"], "send")
        # stage 不变 + 没有 outbound 邮件入库
        self.assertEqual(
            helpers.mem_tdb.get_talent_current_stage(tid), "POST_OFFER_FOLLOWUP")
        self.assertEqual(_outbound_emails(tid), [])
        # HR 飞书 / boss 飞书都不应该被通知（chain 短路）
        mock_hr.assert_not_called()
        mock_boss.assert_not_called()


# ════════════════════════════════════════════════════════════════════════════
# Chain 9：HR 触发的学历感知一面排期（§5.11，v3.5.7）
# ════════════════════════════════════════════════════════════════════════════

class TestRound1DispatchChain(unittest.TestCase):
    """覆盖 §5.11 完整链：
      0. intake.cmd_route_interviewer    → 决定派给 master/bachelor/cpp
      1. outbound.cmd_send (round1_invite)
      2. feishu.cmd_calendar_create --round 1 --duration-minutes 30 --extra-attendee
      3. talent.cmd_update --stage ROUND1_SCHEDULED
      4. feishu.cmd_notify --to interviewer-{role}
      5. feishu.cmd_notify --to boss

    对三种关键路径分别断言：cpp_first 优先 / bachelor / ambiguous（短路）。
    """

    def setUp(self):
        helpers.wipe_state()
        from lib import config as _cfg
        _cfg._ensure_loaded()
        # 真实 open_id（区别于占位符），保证 cmd_route_interviewer 不返回 config_error
        self._saved_feishu = dict(_cfg._cache.get("feishu") or {})
        _cfg._cache["feishu"] = dict(self._saved_feishu)
        _cfg._cache["feishu"].update({
            "interviewer_master_open_id":   "ou_iv_master_real",
            "interviewer_bachelor_open_id": "ou_iv_bach_real",
            "interviewer_cpp_open_id":      "ou_iv_cpp_real",
            # boss / hr / app 字段从原始 cfg 继承，不动
        })

    def tearDown(self):
        from lib import config as _cfg
        _cfg._cache["feishu"] = self._saved_feishu

    # ── 工具：拿到 route step 的输出 ────────────────────────────────────────

    @staticmethod
    def _route(tid):
        out, err, rc = helpers.call_main(
            "intake.cmd_route_interviewer", ["--talent-id", tid, "--json"])
        if rc != 0:
            raise AssertionError("route failed rc={} err={}".format(rc, err))
        return json.loads(out)

    # ── 主路径：cpp_first 优先 ─────────────────────────────────────────────

    def test_round1_dispatch_cpp_priority_full_chain(self):
        """C++ 硕士：should route to cpp（cpp_first 比 master 优先）。
        全链跑通后：
          - stage = ROUND1_SCHEDULED（直接 _SCHEDULED，不是 _SCHEDULING）
          - round1_calendar_event_id 落到由 cmd_calendar_create 抠出来的 event_id
          - 邀约信进 talent_emails
          - 飞书 cpp 面试官 + boss 都被通知（master/bachelor 不应被通知）
          - lib.feishu.create_interview_event 被传入 extra_attendee_open_ids
            包含 cpp 面试官 open_id 且 duration_minutes=30
        """
        tid = _seed_talent(
            "t_iv_cpp_master", stage="NEW", name="周同学",
            email="zhou@example.com",
            education="硕士", has_cpp=True,
        )
        round1_time = "2026-04-25 14:00"

        route = self._route(tid)
        self.assertTrue(route["ok"], route)
        self.assertEqual(route["interviewer_roles"], ["cpp"])
        self.assertEqual(route["interviewer_open_ids"], ["ou_iv_cpp_real"])

        with mock.patch("lib.feishu.create_interview_event",
                        return_value="ok event_id=evt_iv_777") as mock_cal, \
             mock.patch("lib.feishu.send_text_to_interviewer_cpp",
                        return_value=True) as m_cpp, \
             mock.patch("lib.feishu.send_text_to_interviewer_master") as m_master, \
             mock.patch("lib.feishu.send_text_to_interviewer_bachelor") as m_bach, \
             mock.patch("lib.feishu.send_text", return_value=True) as m_boss, \
             mock.patch("lib.feishu.send_text_to_hr") as m_hr:
            iv_open_ids = route["interviewer_open_ids"]
            iv_roles = route["interviewer_roles"]

            cal_args = [
                "--talent-id", tid,
                "--time", round1_time,
                "--round", "1",
                "--duration-minutes", "30",
                "--candidate-email", "zhou@example.com",
                "--candidate-name", "周同学",
            ]
            for oid in iv_open_ids:
                cal_args += ["--extra-attendee", oid]
            cal_args.append("--json")

            steps = [
                Step("send", "outbound.cmd_send", args=[
                    "--talent-id", tid,
                    "--template", "round1_invite",
                    "--vars",
                    "round1_time={}".format(round1_time),
                    "location=上海市浦东新区",
                    "position_suffix=（量化研究员）",
                ]),
                Step("cal", "feishu.cmd_calendar_create", args=cal_args),
                Step("update", "talent.cmd_update", args=[
                    "--talent-id", tid,
                    "--stage", "ROUND1_SCHEDULED",
                    "--set", "round1_time={}".format(round1_time),
                    "--set", "round1_invite_sent_at={send.sent_at}",
                    "--set", "round1_confirm_status=CONFIRMED",
                    "--set", "round1_calendar_event_id={cal.event_id}",
                    "--reason", "agent: §5.11 HR 触发派单（cpp_first）",
                ]),
            ]
            for role in iv_roles:
                steps.append(Step(
                    "notify_iv_{}".format(role), "feishu.cmd_notify", args=[
                        "--to", "interviewer-{}".format(role),
                        "--severity", "info",
                        "--title", "一面安排：周同学",
                        "--body", "talent={} time={} 学历=硕士 会C++=true".format(
                            tid, round1_time),
                    ]))
            steps.append(Step("notify_boss", "feishu.cmd_notify", args=[
                "--to", "boss",
                "--severity", "info",
                "--title", "一面已排：周同学 {}".format(round1_time),
                "--body", "talent={} 派给 cpp 面试官".format(tid),
            ]))

            result = run_chain(steps)

        self.assertTrue(result["ok"], result)

        # ── stage / 字段断言 ─────────────────────────────────────────────
        self.assertEqual(
            helpers.mem_tdb.get_talent_current_stage(tid), "ROUND1_SCHEDULED")
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "round1_time"), round1_time)
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "round1_confirm_status"),
            "CONFIRMED")
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "round1_calendar_event_id"),
            "evt_iv_777")

        # ── 邀约信落表 ──────────────────────────────────────────────────
        outs = _outbound_emails(tid)
        self.assertEqual(len(outs), 1)
        self.assertEqual(outs[0]["template"], "round1_invite")
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "round1_invite_sent_at"),
            result["steps"]["send"]["sent_at"])

        # ── 日历调用：cpp 面试官在 extra_attendees + duration=30 ────────
        mock_cal.assert_called_once()
        kwargs = mock_cal.call_args.kwargs
        self.assertEqual(kwargs["interview_time"], round1_time)
        self.assertEqual(kwargs["round_num"], 1)
        self.assertEqual(kwargs["duration_minutes"], 30)
        self.assertIn("ou_iv_cpp_real", kwargs["extra_attendee_open_ids"])
        # 不应该混进其他面试官 open_id（cpp_first 隔离）
        self.assertNotIn("ou_iv_master_real", kwargs["extra_attendee_open_ids"])
        self.assertNotIn("ou_iv_bach_real", kwargs["extra_attendee_open_ids"])

        # ── 飞书通知：cpp + boss 各一次；master / bachelor / hr 都不应被触发 ──
        m_cpp.assert_called_once()
        m_boss.assert_called_once()
        m_master.assert_not_called()
        m_bach.assert_not_called()
        m_hr.assert_not_called()
        # 通知文案带候选人 tid，便于 cron / agent 关联
        self.assertIn(tid, m_cpp.call_args[0][0])
        self.assertIn(tid, m_boss.call_args[0][0])

    # ── 主路径：本科 → bachelor ──────────────────────────────────────────

    def test_round1_dispatch_bachelor_routes_to_bachelor_only(self):
        """无 C++ 的本科：派给 bachelor，cpp / master 不应被通知，
        日历 extra_attendees 只含 bachelor open_id。"""
        tid = _seed_talent(
            "t_iv_bach", stage="NEW", name="李同学",
            email="li@example.com",
            education="本科", has_cpp=False,
        )
        route = self._route(tid)
        self.assertEqual(route["interviewer_roles"], ["bachelor"])
        self.assertEqual(route["interviewer_open_ids"], ["ou_iv_bach_real"])

        with mock.patch("lib.feishu.create_interview_event",
                        return_value="ok event_id=evt_iv_bach") as mock_cal, \
             mock.patch("lib.feishu.send_text_to_interviewer_bachelor",
                        return_value=True) as m_bach, \
             mock.patch("lib.feishu.send_text_to_interviewer_master") as m_master, \
             mock.patch("lib.feishu.send_text_to_interviewer_cpp") as m_cpp, \
             mock.patch("lib.feishu.send_text", return_value=True) as m_boss:

            cal_args = [
                "--talent-id", tid, "--time", "2026-04-26 10:00",
                "--round", "1", "--duration-minutes", "30",
                "--candidate-name", "李同学",
                "--candidate-email", "li@example.com",
                "--extra-attendee", route["interviewer_open_ids"][0],
                "--json",
            ]
            result = run_chain([
                Step("send", "outbound.cmd_send", args=[
                    "--talent-id", tid,
                    "--template", "round1_invite",
                    "--vars",
                    "round1_time=2026-04-26 10:00",
                    "position_suffix=（量化研究员）",
                    "location=上海市浦东新区",
                ]),
                Step("cal", "feishu.cmd_calendar_create", args=cal_args),
                Step("update", "talent.cmd_update", args=[
                    "--talent-id", tid,
                    "--stage", "ROUND1_SCHEDULED",
                    "--set", "round1_time=2026-04-26 10:00",
                    "--set", "round1_calendar_event_id={cal.event_id}",
                    "--set", "round1_invite_sent_at={send.sent_at}",
                    "--set", "round1_confirm_status=CONFIRMED",
                ]),
                Step("notify_iv", "feishu.cmd_notify", args=[
                    "--to", "interviewer-bachelor",
                    "--title", "一面安排：李同学",
                    "--body", "talent={}".format(tid),
                ]),
                Step("notify_boss", "feishu.cmd_notify", args=[
                    "--to", "boss",
                    "--title", "一面已排",
                    "--body", "talent={} bach".format(tid),
                ]),
            ])

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            helpers.mem_tdb.get_talent_current_stage(tid), "ROUND1_SCHEDULED")
        # 路由 isolation：只 bachelor 通道被打到
        m_bach.assert_called_once()
        m_master.assert_not_called()
        m_cpp.assert_not_called()
        # boss 通知一次
        m_boss.assert_called_once()
        # 日历 attendees 只含 bachelor open_id
        kwargs = mock_cal.call_args.kwargs
        self.assertEqual(kwargs["extra_attendee_open_ids"], ["ou_iv_bach_real"])
        self.assertEqual(kwargs["duration_minutes"], 30)

    # ── 路径：ambiguous（route 无法判断 → ASK_HR，不发邀约信） ─────────

    def test_round1_dispatch_ambiguous_routes_to_hr_and_skips_invite(self):
        """has_cpp=null + 学历未识别 → cmd_route_interviewer 返回 ambiguous=True。
        agent 必须只推一条 hr 飞书 + STOP，不要继续发邮件 / 建日历 / 推 stage。"""
        tid = _seed_talent(
            "t_iv_amb", stage="NEW", name="未知同学",
            email="amb@example.com",
            education=None, has_cpp=None,
        )

        route = self._route(tid)
        self.assertTrue(route["ambiguous"])
        self.assertFalse(route["ok"])
        self.assertEqual(route["interviewer_roles"], [])
        self.assertEqual(route["interviewer_open_ids"], [])

        # § 5.11：ambiguous 时 chain 只走「ASK_HR 一条」，绝不能继续后续步骤
        with mock.patch("lib.feishu.create_interview_event") as mock_cal, \
             mock.patch("lib.feishu.send_text_to_hr",
                        return_value=True) as m_hr, \
             mock.patch("lib.feishu.send_text") as m_boss, \
             mock.patch("lib.feishu.send_text_to_interviewer_master") as m_master, \
             mock.patch("lib.feishu.send_text_to_interviewer_bachelor") as m_bach, \
             mock.patch("lib.feishu.send_text_to_interviewer_cpp") as m_cpp:
            result = run_chain([
                Step("ask_hr", "feishu.cmd_notify", args=[
                    "--to", "hr",
                    "--severity", "warn",
                    "--title", "一面派单需 HR 手动指派 t_iv_amb",
                    "--body", "原因：{}".format(route["ambiguous_reason"]),
                    "--source", "agent.round1_dispatch_ambiguous",
                ]),
            ])

        self.assertTrue(result["ok"], result)
        # HR 收到通知，其余通道全静默
        m_hr.assert_called_once()
        for unused in (m_boss, m_master, m_bach, m_cpp, mock_cal):
            unused.assert_not_called()
        hr_text = m_hr.call_args[0][0]
        self.assertIn("一面派单需 HR 手动指派", hr_text)
        self.assertIn("无法自动派单", hr_text)

        # 关键：stage 不动，没有 outbound 邮件落表，没有 calendar event 字段
        self.assertEqual(
            helpers.mem_tdb.get_talent_current_stage(tid), "NEW")
        self.assertEqual(_outbound_emails(tid), [])
        self.assertIsNone(
            helpers.mem_tdb.get_talent_field(tid, "round1_time"))
        self.assertIsNone(
            helpers.mem_tdb.get_talent_field(tid, "round1_calendar_event_id"))


if __name__ == "__main__":
    unittest.main()
