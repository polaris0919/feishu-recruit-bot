#!/usr/bin/env python3
"""email_templates 渲染引擎 + 6 个模板的契约测试。

为什么必须有这层测试：
  - renderer 一旦行为漂移（fail-fast 退化为 silent fallback、fragment 不展开
    等），事故面是"全公司发出去的招聘邮件全乱了"，必须 CI 兜底
  - 模板话术变动需要老板 review；测试断言锁定每类模板当前承诺的关键
    关键词，任何静默改动会让 CI 红，强制走 review
"""
import unittest
from pathlib import Path
from unittest import mock

from email_templates import renderer
from email_templates.constants import COMPANY, LOCATION, round_label


# ─── renderer 引擎契约 ──────────────────────────────────────────────────────────

class TestRendererEngine(unittest.TestCase):

    def test_subject_extracted_from_first_line(self):
        # round1_invite 模板首行是 SUBJECT:，验证抽取正确
        subject, _ = renderer.render(
            "round1_invite",
            candidate_name="X", round1_time="t", position="",
            position_suffix="", location="L", company="C", talent_id="t_x",
        )
        self.assertTrue(subject.startswith("【面试邀请】"), subject)

    def test_missing_variable_raises_keyerror(self):
        # 缺变量必须 fail-fast，不能把 "$candidate_name" 字符串发出去
        with self.assertRaises(KeyError):
            renderer.render("round1_invite", round1_time="t")

    def test_unknown_template_raises(self):
        with self.assertRaises(renderer.TemplateNotFoundError):
            renderer.render("nonexistent_template", x=1)

    def test_extra_variables_ignored(self):
        # 多传变量应被静默忽略（允许调用方传 context bag）
        subject, body = renderer.render(
            "defer",
            candidate_name="X", round_label="第一轮",
            company="C", talent_id="t_x",
            extra_garbage="this should be ignored",
        )
        self.assertIn("第一轮面试暂缓", subject)
        self.assertNotIn("extra_garbage", body)

    def test_fragment_include_substituted(self):
        # round1_invite 保留共享流程/实习要求话术，确保 fragment 已展开
        _, body = renderer.render(
            "round1_invite",
            candidate_name="X", round1_time="t", position="",
            position_suffix="", location="L", company="C", talent_id="t_x",
        )
        self.assertNotIn("$$include", body, "fragment 占位符未替换")
        self.assertIn("实习要求", body)
        self.assertIn("完整面试流程", body)

    def test_subject_format_error_raises(self):
        # 不带 SUBJECT: 行的伪模板必须报错（兜底防止有人写错）
        fake_root = Path(renderer.__file__).resolve().parent
        bad_path = fake_root / "_test_bad_template.txt"
        bad_path.write_text("没有 SUBJECT 行\n直接 body", encoding="utf-8")
        try:
            with self.assertRaises(renderer.TemplateRenderError):
                renderer.render("_test_bad_template")
        finally:
            bad_path.unlink()

    def test_fragment_strips_leading_trailing_newlines(self):
        # fragment 文件末尾的 \n 不应叠加成双空行。
        root = Path(renderer.__file__).resolve().parent
        frag_path = root / "_fragments" / "_test_strip_newlines.txt"
        tmpl_path = root / "_test_strip_include.txt"
        frag_path.write_text("\nFRAGMENT\n", encoding="utf-8")
        tmpl_path.write_text(
            "SUBJECT: 测试\n\nbefore\n$$include(_test_strip_newlines)$$\nafter\n",
            encoding="utf-8",
        )
        try:
            _, body = renderer.render("_test_strip_include")
            self.assertEqual(body, "before\nFRAGMENT\nafter\n")
        finally:
            frag_path.unlink(missing_ok=True)
            tmpl_path.unlink(missing_ok=True)


# ─── 6 个模板的话术契约 ──────────────────────────────────────────────────────────

class TestTemplateContents(unittest.TestCase):
    """锁定关键话术片段。任何静默修改这些断言会让 CI 红，强制走 review。"""

    def test_round1_invite_includes_intern_requirements_and_process(self):
        _, body = renderer.render(
            "round1_invite",
            candidate_name="张三", round1_time="2026-04-25 14:00",
            position="量化研究员", position_suffix="（量化研究员）",
            location=LOCATION, company=COMPANY, talent_id="t_demo",
        )
        # 实习要求关键字段（v3.8.4 改成更自然话术；任何静默改动让本测试红，强制走 review）
        self.assertIn("实习期至少 3 个月", body)
        self.assertIn("每周至少保证 4 天到岗", body)
        self.assertIn("可包含周末", body)
        # 三轮流程必须明示
        self.assertIn("第一轮：线下面试", body)
        self.assertIn("第二轮：笔试", body)
        self.assertIn("第三轮：线下复试", body)
        # 一面具体信息
        self.assertIn("2026-04-25 14:00", body)
        self.assertIn(LOCATION, body)
        # TALENT_ID 必须保留（被 followup_scanner 用于反向定位候选人）
        self.assertIn("TALENT_ID: t_demo", body)

    def test_round1_invite_intern_requirements_appear_before_schedule(self):
        # 产品决策：实习要求必须在面试时间之前（让候选人提前自筛）
        _, body = renderer.render(
            "round1_invite",
            candidate_name="张三", round1_time="2026-04-25 14:00",
            position="", position_suffix="",
            location=LOCATION, company=COMPANY, talent_id="t_demo",
        )
        intern_pos = body.find("实习要求")
        schedule_pos = body.find("一面详情")
        self.assertGreater(intern_pos, 0)
        self.assertGreater(schedule_pos, intern_pos,
                           "实习要求必须在面试时间板块之前")

    def test_exam_invite_includes_exam_instructions(self):
        # 笔试邀请当前只锁定笔试说明，不重复展示流程 + 实习要求
        _, body = renderer.render(
            "exam_invite",
            candidate_name="张三", company=COMPANY, talent_id="t_demo",
        )
        self.assertIn("笔试", body)
        self.assertIn("第二轮", body)
        self.assertIn("题目已作为附件随本邮件发送", body)
        self.assertIn("建议完成时间：3 天内", body)
        self.assertIn("自动视为放弃", body)
        self.assertIn("TALENT_ID: t_demo", body)

    def test_round2_invite_uses_third_round_language(self):
        # 在候选人语言里 ROUND2 = "第三轮"
        subject, body = renderer.render(
            "round2_invite",
            candidate_name="张三", round2_time="2026-05-08 10:00",
            location=LOCATION, company=COMPANY, talent_id="t_demo",
        )
        self.assertIn("第三轮", subject)
        self.assertIn("第三轮", body)
        self.assertIn("2026-05-08 10:00", body)
        self.assertIn(LOCATION, body)

    def test_reschedule_round2_uses_third_round(self):
        # round_num=2 → "第三轮"（候选人语言）
        subject, _ = renderer.render(
            "reschedule",
            candidate_name="X", new_time="t", round_label=round_label(2),
            location=LOCATION, company=COMPANY, talent_id="t_x",
        )
        self.assertIn("第三轮", subject)

    def test_defer_uses_round_label(self):
        subject, body = renderer.render(
            "defer",
            candidate_name="X", round_label=round_label(1),
            company=COMPANY, talent_id="t_x",
        )
        self.assertIn("第一轮面试暂缓", subject)
        self.assertIn("第一轮面试", body)

    def test_onboarding_offer_renders_all_required_vars(self):
        subject, body = renderer.render(
            "onboarding_offer",
            candidate_name="李四", position_title="量化研究员",
            interview_feedback="您在面试中展现出扎实的专业基础。",
            daily_rate="350", onboard_date="2026-05-06",
            location=LOCATION,
            evaluation_criteria="实习期前 1 个月为试用期。",
            company=COMPANY, talent_id="t_offer01",
        )
        self.assertIn("【录用通知】", subject)
        self.assertIn("恭喜加入", subject)
        self.assertIn("李四", body)
        self.assertIn("量化研究员", body)
        self.assertIn("您在面试中展现出扎实的专业基础。", body)
        self.assertIn("350 元 / 天", body)
        self.assertIn("2026-05-06", body)
        self.assertIn(LOCATION, body)
        self.assertIn("实习期前 1 个月", body)
        self.assertIn("实习生入职信息登记表", body)
        self.assertIn("致邃实习协议", body)
        self.assertIn("实习期 ≥ 3 个月", body)
        self.assertIn("每周工作 ≥ 4 天", body)
        self.assertIn("TALENT_ID: t_offer01", body)

    def test_onboarding_offer_missing_var_fails_fast(self):
        with self.assertRaises(KeyError):
            renderer.render(
                "onboarding_offer",
                candidate_name="X", position_title="P",
                daily_rate="350", onboard_date="2026-05-06",
                location=LOCATION, company=COMPANY, talent_id="t_x",
            )


# ─── 调用点确实经过 renderer ──────────────────────────────────────────────────

class TestCallSitesUseRenderer(unittest.TestCase):
    """烟测：业务调用点通过 outbound.cmd_send 模板发送候选人邮件。"""

    def test_round1_invite_template_renders_required_sections(self):
        """v3.5：cmd_round1_schedule wrapper 已彻底删除，agent 直接拼 outbound.cmd_send +
        talent.cmd_update 完成排期。这里只需验证 round1_invite 模板渲染输出符合预期。"""
        from email_templates import renderer
        from email_templates.constants import COMPANY, LOCATION
        subject, body = renderer.render(
            "round1_invite",
            candidate_name="张三",
            round1_time="2026-04-25 14:00",
            position="量化研究员",
            position_suffix="（量化研究员）",
            location=LOCATION,
            company=COMPANY,
            talent_id="t_test01",
        )
        self.assertIn("【面试邀请】", subject)
        self.assertIn("实习期至少 3 个月", body)
        self.assertIn("第一轮：线下面试", body)

    def test_exam_email_uses_template(self):
        from interview import cmd_result as mod
        with mock.patch("email_templates.auto_attachments.auto_attachments_for",
                        return_value=[]), \
             mock.patch.object(mod, "send_outbound_template",
                               return_value={"ok": True, "message_id": "<m@local>"}) as p:
            res = mod._send_exam_email("t_test01", "test@example.com", "exam-x",
                                       candidate_name="张三")
            self.assertTrue(p.called)
            self.assertEqual(res["message_id"], "<m@local>")
            _, kwargs = p.call_args
            self.assertEqual(kwargs["template"], "exam_invite")
            self.assertEqual(kwargs["talent_id"], "t_test01")

    def test_round2_invite_uses_template(self):
        from exam import cmd_exam_result as mod
        with mock.patch.object(mod, "send_outbound_template",
                               return_value={"ok": True, "message_id": "<m@local>"}) as p:
            res = mod.send_round2_notification(
                "test@example.com", "t_test01", "2026-05-08 10:00",
                company="致邃投资", candidate_name="张三",
            )
            self.assertTrue(p.called)
            self.assertEqual(res["message_id"], "<m@local>")
            _, kwargs = p.call_args
            self.assertEqual(kwargs["template"], "round2_invite")
            self.assertEqual(kwargs["vars"]["round2_time"], "2026-05-08 10:00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
