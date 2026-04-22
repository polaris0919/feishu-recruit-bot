#!/usr/bin/env python3
"""outbound.cmd_send._normalize_body 单元测试（v3.5.13 / 2026-04-22）

事故背景：飞书侧 agent 起草的"入职时间确认"邮件，body 含字面 `\\n\\n` 与
`**...**`，原样进 SMTP（plain text），收件人看到反斜杠 + 字面星号。
（详见 outbound/cmd_send.py 顶部 _normalize_body 注释；候选人A / t_demo01
2026-04-22 16:08 邮件 msg_demo_* 是冒烟枪，body_excerpt 在 DB 里还能查到。）

本套测试钉死归一化规则的边界，避免将来手贱误改：
  - 字面 \\n / \\r\\n / \\r / \\t 解码（顺序敏感：\\r\\n 先吃）
  - **粗体** / __粗体__ 剥外壳保内容
  - 行首 # / ## / ### 标题前缀剥掉
  - 不碰：单 *斜体* / _斜体_ / `inline code` / "- 列表项"（中文邮件易误伤）
  - --no-body-normalize 完全跳过（薄包装路径）
  - 真已干净的 body 跑一遍 stats 全 0、内容完全不变（幂等）
"""
import os
import sys
import unittest


# 让 scripts/ 在 sys.path 上
_SCRIPTS_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPTS_ROOT)

# helpers 自带 RECRUIT_DISABLE_SIDE_EFFECTS=1 的安全壳；我们这里其实不动 SMTP / DB，
# 但 cmd_send import 链里需要 lib.talent_db 等，跟着 helpers 一起走最稳。
from tests import helpers  # noqa: F401, E402

from outbound.cmd_send import _normalize_body, _maybe_normalize_body_inplace  # noqa: E402


class NormalizeBodyTests(unittest.TestCase):

    # ─── 冒烟枪：完整还原 2026-04-22 16:08 那封"入职时间确认" ────────────

    def test_smoking_gun_onboarding_confirm(self):
        raw = ("候选人A，您好！\\n\\n您可于 **5月6日（周二）上午9点** "
               "准时入职。\\n\\n如有问题请随时联系。")
        out, stats = _normalize_body(raw)
        self.assertEqual(
            out,
            "候选人A，您好！\n\n您可于 5月6日（周二）上午9点 准时入职。\n\n如有问题请随时联系.".replace(".", "。"))
        self.assertEqual(stats["esc_n"], 4)  # 四处 \n
        self.assertEqual(stats["bold"], 1)
        self.assertEqual(stats["esc_t"], 0)
        self.assertEqual(stats["header"], 0)

    # ─── 反斜杠转义解码 ──────────────────────────────────────────────────

    def test_decode_backslash_n(self):
        out, stats = _normalize_body("a\\nb\\nc")
        self.assertEqual(out, "a\nb\nc")
        self.assertEqual(stats["esc_n"], 2)

    def test_decode_backslash_rn_takes_precedence(self):
        # \r\n 必须先于 \r / \n 单独解码，否则会拆出空 \n
        out, _ = _normalize_body("a\\r\\nb")
        self.assertEqual(out, "a\nb")

    def test_decode_backslash_r_alone(self):
        out, _ = _normalize_body("a\\rb")
        self.assertEqual(out, "a\nb")

    def test_decode_backslash_t(self):
        out, stats = _normalize_body("col1\\tcol2")
        self.assertEqual(out, "col1\tcol2")
        self.assertEqual(stats["esc_t"], 1)

    # ─── markdown 粗体剥离 ───────────────────────────────────────────────

    def test_strip_double_star_bold(self):
        out, stats = _normalize_body("入职 **5月6日** 上午")
        self.assertEqual(out, "入职 5月6日 上午")
        self.assertEqual(stats["bold"], 1)

    def test_strip_double_underscore_bold(self):
        out, stats = _normalize_body("__重要__：请阅读")
        self.assertEqual(out, "重要：请阅读")
        self.assertEqual(stats["bold"], 1)

    def test_bold_does_not_cross_newline(self):
        # 段间不该被吞成一对：第一行收尾的 ** 不能匹配下一段开头的 **
        raw = "**第一段没收尾\n第二段才收**尾"
        out, stats = _normalize_body(raw)
        # 没合法配对 → 一个都不剥
        self.assertEqual(stats["bold"], 0)
        self.assertEqual(out, raw)

    # ─── 行首标题前缀剥离 ────────────────────────────────────────────────

    def test_strip_atx_header(self):
        raw = "# 入职须知\n## 第一步\n正文..."
        out, stats = _normalize_body(raw)
        self.assertEqual(out, "入职须知\n第一步\n正文...")
        self.assertEqual(stats["header"], 2)

    def test_hash_inside_line_not_stripped(self):
        # 行中间的 # 是普通字符（话题标签 / 编号），不能剥
        raw = "本期编号 #42 已发布"
        out, stats = _normalize_body(raw)
        self.assertEqual(out, raw)
        self.assertEqual(stats["header"], 0)

    # ─── 边界：不该碰的东西 ─────────────────────────────────────────────

    def test_single_asterisk_italic_kept(self):
        # *斜体* 不剥（避免误伤数学/技术表达）
        raw = "f(x) = a*x + b"
        out, _ = _normalize_body(raw)
        self.assertEqual(out, raw)

    def test_inline_code_kept(self):
        raw = "请运行 `git status` 查看状态"
        out, _ = _normalize_body(raw)
        self.assertEqual(out, raw)

    def test_bullet_dash_kept(self):
        raw = "- 第一项\n- 第二项"
        out, _ = _normalize_body(raw)
        self.assertEqual(out, raw)

    # ─── 幂等 / no-op ────────────────────────────────────────────────────

    def test_clean_body_is_noop(self):
        raw = "候选人A，您好！\n\n感谢您的回复。\n\nHermes 代发"
        out, stats = _normalize_body(raw)
        self.assertEqual(out, raw)
        self.assertEqual(sum(stats.values()), 0)

    def test_idempotent(self):
        # 已经 normalize 过的再 normalize 一次必须完全一致
        raw = "**重要** 请于 \\n5月6日入职\\n\\n谢谢"
        once, _ = _normalize_body(raw)
        twice, twice_stats = _normalize_body(once)
        self.assertEqual(once, twice)
        self.assertEqual(sum(twice_stats.values()), 0)

    def test_empty_body(self):
        out, stats = _normalize_body("")
        self.assertEqual(out, "")
        self.assertEqual(sum(stats.values()), 0)

    # ─── 薄包装：开关行为 ───────────────────────────────────────────────

    def test_disabled_skips_normalize(self):
        raw = "候选人A\\n\\n**5月6日**入职"
        out = _maybe_normalize_body_inplace(raw, "freeform", enabled=False)
        # enabled=False → 原样返回，连字面转义都不解码
        self.assertEqual(out, raw)

    def test_enabled_runs_normalize(self):
        raw = "候选人A\\n\\n**5月6日**入职"
        out = _maybe_normalize_body_inplace(raw, "freeform", enabled=True)
        self.assertEqual(out, "候选人A\n\n5月6日入职")

    def test_enabled_on_clean_body_is_noop(self):
        # 真模板渲染出来的内容跑一遍应当无副作用
        raw = "亲爱的张三：\n\n邀请您参加笔试。\n\n示例科技公司 HR"
        out = _maybe_normalize_body_inplace(raw, "round1_invite", enabled=True)
        self.assertEqual(out, raw)


if __name__ == "__main__":
    unittest.main()
