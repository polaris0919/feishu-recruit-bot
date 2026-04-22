#!/usr/bin/env python3
"""self_verify.py —— v3.3 写入脚本的"动作-验证"基础库。

核心思路（D5）：每个写入脚本（cmd_send / cmd_update / cmd_delete 等）
在完成主动作后，必须立刻查数据库确认动作真的生效，否则抛 SelfVerifyError。

抛出的异常应当被 lib.cli_wrapper.run_with_self_verify() 捕获 → 推飞书告警 → 非零退出。
不在 lib 里直接推告警是为了让单元测试容易隔离。

为什么这么严格：v3.3 把"业务动作"和"状态变更"拆成两个脚本（cmd_send 不动 stage、
cmd_update 不发邮件），如果两边数据不一致没及时发现，故障会越攒越深。"动作-验证"
就是用最便宜的方式（一次额外 SELECT）把窗口缩到最小。
"""
from __future__ import print_function

from typing import Any, Optional

from lib import talent_db


# ─── 异常 ─────────────────────────────────────────────────────────────────────

class SelfVerifyError(Exception):
    """自验证失败：动作完成后从 DB 读到的状态和预期不一致。

    属性：
        check     —— 检查名（assert_xxx）
        context   —— 失败上下文 dict（talent_id / field / expected / actual ...）
                     会被 cli_wrapper 序列化进飞书卡片
    """

    def __init__(self, check, context, message=None):
        # type: (str, dict, Optional[str]) -> None
        self.check = check
        self.context = context or {}
        msg = message or "self-verify FAIL: {} | ctx={}".format(check, context)
        super(SelfVerifyError, self).__init__(msg)


# ─── inbound / outbound 邮件断言 ──────────────────────────────────────────────

def assert_email_sent(talent_id, message_id):
    # type: (str, str) -> dict
    """cmd_send 发完邮件后调：确认 (talent_id, message_id) 真的写进 talent_emails。

    返回查到的行 dict；查不到抛 SelfVerifyError。
    """
    row = talent_db.find_outbound_email_by_message_id(talent_id, message_id)
    if not row:
        raise SelfVerifyError(
            "assert_email_sent",
            {"talent_id": talent_id, "message_id": message_id,
             "hint": "SMTP 可能成功但 talent_emails INSERT 失败"},
        )
    return row


def assert_emails_inserted(talent_id, expected_message_ids):
    # type: (str, list) -> None
    """cmd_scan 拉完邮件后调：确认这批 message_id 全部入了库（或本来就在）。"""
    missing = []
    for mid in expected_message_ids:
        row = talent_db.find_email_by_message_id(talent_id, mid)
        if not row:
            missing.append(mid)
    if missing:
        raise SelfVerifyError(
            "assert_emails_inserted",
            {"talent_id": talent_id,
             "expected_count": len(expected_message_ids),
             "missing": missing[:10],
             "missing_count": len(missing)},
        )


def assert_email_analyzed(email_id):
    # type: (str) -> None
    """cmd_analyze 写完 LLM 结果后调：analyzed_at 必须非空。

    需要 email_id 而非 message_id 是因为 cmd_analyze 处理时已经知道 email_id。
    """
    row = talent_db._query_one(
        "SELECT analyzed_at, ai_intent FROM talent_emails WHERE email_id = %s",
        (email_id,),
    )
    if not row:
        raise SelfVerifyError(
            "assert_email_analyzed",
            {"email_id": email_id, "hint": "talent_emails 行不存在"},
        )
    if row.get("analyzed_at") is None:
        raise SelfVerifyError(
            "assert_email_analyzed",
            {"email_id": email_id,
             "hint": "analyzed_at 仍为空，LLM 写入未生效"},
        )


# ─── talents 表断言 ──────────────────────────────────────────────────────────

def assert_talent_state(talent_id, expected_stage=None, expected_fields=None):
    # type: (str, Optional[str], Optional[dict]) -> None
    """cmd_update 写完后调。

    Args:
        talent_id:      候选人主键
        expected_stage: 期望的 current_stage（None 表示不检查 stage）
        expected_fields: {"round1_time": "...", ...} 期望值字典；None / "__SET__" 占位特殊处理：
                         - 显式 None     表示该字段必须为 NULL
                         - "__SET__"     表示该字段必须非 NULL（不关心具体值，写入路径动态生成时用）
                         - 其他任意值     == 比较

    任何不匹配都抛 SelfVerifyError 并把所有不匹配项放进 context，方便老板看告警卡片。
    """
    if not talent_db.talent_exists(talent_id):
        raise SelfVerifyError(
            "assert_talent_state",
            {"talent_id": talent_id, "hint": "candidate row missing"},
        )

    mismatches = []
    if expected_stage is not None:
        actual = talent_db.get_talent_current_stage(talent_id)
        if actual != expected_stage:
            mismatches.append({"field": "current_stage",
                               "expected": expected_stage,
                               "actual": actual})

    for field, expected in (expected_fields or {}).items():
        actual = talent_db.get_talent_field(talent_id, field)
        if expected == "__SET__":
            if actual is None:
                mismatches.append({"field": field,
                                   "expected": "<non-null>",
                                   "actual": None})
        elif expected is None:
            if actual is not None:
                mismatches.append({"field": field,
                                   "expected": None,
                                   "actual": _shorten(actual)})
        else:
            if actual != expected:
                mismatches.append({"field": field,
                                   "expected": _shorten(expected),
                                   "actual": _shorten(actual)})

    if mismatches:
        raise SelfVerifyError(
            "assert_talent_state",
            {"talent_id": talent_id, "mismatches": mismatches},
        )


def assert_talent_deleted(talent_id):
    # type: (str) -> None
    """cmd_delete 删完后调：talents 行必须不存在。"""
    if talent_db.talent_exists(talent_id):
        raise SelfVerifyError(
            "assert_talent_deleted",
            {"talent_id": talent_id, "hint": "DELETE 似乎没生效"},
        )


# ─── 内部 ─────────────────────────────────────────────────────────────────────

def _shorten(v):
    # type: (Any) -> Any
    """飞书告警卡片不要塞太长，简单截 200 字符。"""
    if v is None:
        return None
    s = str(v)
    return s if len(s) <= 200 else s[:200] + "...(+{} chars)".format(len(s) - 200)
