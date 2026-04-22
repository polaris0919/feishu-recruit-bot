#!/usr/bin/env python3
"""talent/cmd_add.py —— v3.3 新建候选人【唯一入口】。

【职责】
  1. 自动生成 talent_id（t_ + 6 随机字母数字）
  2. 插入 talents 一行（stage=NEW，audit 空）
  3. 写审计事件 "talent.created"
  4. 可选推飞书通知老板
  5. 自验证：talent_exists

【绝不做】
  - 不发任何候选人邮件（用 outbound.cmd_send）
  - 不安排一面（用 talent.cmd_update --stage ROUND1_SCHEDULED 或专用脚本）

【两种输入】
  a) --template  飞书【新候选人】模板原文，字段自动解析；
  b) 离散参数    --name / --email / --phone / ...（与模板互斥优先级：template 覆盖）

【调用示例】
  # 离散参数
  PYTHONPATH=scripts python3 -m talent.cmd_add \\
    --name 张伟 --email zhangwei@test.com --position 量化研究员

  # 飞书模板
  PYTHONPATH=scripts python3 -m talent.cmd_add --template "$(cat msg.txt)" --feishu-notify
"""
from __future__ import print_function

import argparse
import json
import random
import re
import string
import sys
from typing import Any, Dict, Optional

from lib import talent_db
from lib.cli_wrapper import run_with_self_verify, UserInputError


_TEMPLATE_FIELD_MAP = {
    "姓名": "name", "邮箱": "email",
    "电话": "phone", "手机": "phone",
    "微信": "wechat",
    "应聘职位": "position", "岗位": "position", "职位": "position",
    "学历": "education",
    "毕业院校": "school", "院校": "school",
    "工作年限": "work_years",
    "来源渠道": "source", "来源": "source",
    "简历摘要": "resume_summary", "简介": "resume_summary", "背景": "resume_summary",
}


def _gen_talent_id():
    # type: () -> str
    """带重试的 ID 生成（DB UNIQUE 会兜底，但先尽量减少 collision）。"""
    for _ in range(20):
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        tid = "t_" + suffix
        if not talent_db.talent_exists(tid):
            return tid
    raise RuntimeError("无法生成唯一 talent_id，请重试")


def _parse_template(text):
    # type: (str) -> Dict[str, str]
    result = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("【"):
            continue
        m = re.match(r"^([^：:]+)[：:]\s*(.*)$", line)
        if not m:
            continue
        key_raw = m.group(1).strip()
        val = m.group(2).strip()
        val = re.sub(r"[（(][^）)]*[）)]$", "", val).strip()
        if not val:
            continue
        for k, field in _TEMPLATE_FIELD_MAP.items():
            if k in key_raw:
                result[field] = val
                break
    return result


def _build_parser():
    p = argparse.ArgumentParser(description="v3.3 新建候选人")
    p.add_argument("--template", default=None, help="飞书【新候选人】原文")
    p.add_argument("--name", default=None)
    p.add_argument("--email", default=None)
    p.add_argument("--phone", default=None)
    p.add_argument("--wechat", default=None)
    p.add_argument("--position", default=None)
    p.add_argument("--education", default=None)
    p.add_argument("--school", default=None)
    p.add_argument("--work-years", type=int, default=None)
    p.add_argument("--experience", default=None, help="工作经历或简历摘要")
    p.add_argument("--source", default=None)
    p.add_argument("--actor", default="system")
    p.add_argument("--feishu-notify", action="store_true",
                   help="录入成功后推飞书卡片给老板")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    return p


def _push_feishu_card(cand):
    try:
        from lib import feishu
        lines = [
            "📋 新候选人待安排一面",
            "━━━━━━━━━━━━━━━━━━━━",
            "姓名：{}   ID：{}".format(cand["candidate_name"], cand["talent_id"]),
            "邮箱：{}".format(cand["candidate_email"]),
        ]
        if cand.get("phone"):
            lines.append("电话：{}".format(cand["phone"]))
        if cand.get("position"):
            lines.append("岗位：{}".format(cand["position"]))
        if cand.get("education") or cand.get("school"):
            lines.append("学历：{} {}".format(
                cand.get("education") or "", cand.get("school") or "").strip())
        if cand.get("work_years") is not None:
            lines.append("工作年限：{} 年".format(cand["work_years"]))
        if cand.get("source"):
            lines.append("来源：{}".format(cand["source"]))
        if cand.get("experience"):
            lines.append("摘要：{}".format(cand["experience"][:120]))
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("请在 Cursor 里告诉 agent：")
        lines.append("  安排 {} 一面 YYYY-MM-DD HH:MM".format(cand["candidate_name"]))
        feishu.send_text("\n".join(lines))
        return True
    except Exception as e:
        print("[talent.cmd_add] 飞书推送失败: {}".format(e), file=sys.stderr)
        return False


def _do_add(args):
    fields = {}  # type: Dict[str, Any]

    if args.template:
        parsed = _parse_template(args.template)
        fields.update(parsed)
        # work_years 规整成 int
        if "work_years" in fields:
            digits = re.sub(r"[^\d]", "", str(fields["work_years"]))
            fields["work_years"] = int(digits) if digits else None

    # 命令行参数覆盖模板（便于矫正模板解析不对的情况）
    for k in ("name", "email", "phone", "wechat", "position", "education",
              "school", "source"):
        v = getattr(args, k)
        if v:
            fields[k] = v.strip()
    if args.work_years is not None:
        fields["work_years"] = args.work_years
    if args.experience:
        fields["experience"] = args.experience.strip()
    if fields.get("resume_summary") and not fields.get("experience"):
        fields["experience"] = fields.pop("resume_summary")

    # 必填校验
    if not fields.get("name"):
        raise UserInputError("缺少候选人姓名（--name 或模板『姓名』字段）")
    email = (fields.get("email") or "").strip()
    if not email or "@" not in email:
        raise UserInputError("缺少合法的候选人邮箱（--email 或模板『邮箱』字段）")
    fields["email"] = email

    # dry-run：只演算，不写 DB
    if args.dry_run:
        preview = {"would_insert": {
            "talent_id": "(未分配 / dry-run)",
            "candidate_name": fields["name"],
            "candidate_email": fields["email"],
            "stage": "NEW",
            "other_fields": {k: v for k, v in fields.items()
                             if k not in ("name", "email")},
        }, "dry_run": True}
        print(json.dumps(preview, ensure_ascii=False, indent=2) if args.json
              else "[DRY-RUN] 会录入 {} <{}>".format(fields["name"], fields["email"]))
        return 0

    talent_id = _gen_talent_id()
    cand = {
        "talent_id": talent_id,
        "stage": "NEW",
        "audit": [],
        "candidate_name": fields["name"],
        "candidate_email": fields["email"],
        "phone": fields.get("phone"),
        "wechat": fields.get("wechat"),
        "position": fields.get("position"),
        "education": fields.get("education"),
        "school": fields.get("school"),
        "work_years": fields.get("work_years"),
        "experience": fields.get("experience"),
        "source": fields.get("source"),
    }

    # 落库（复用 core_state.save_candidate 路径：它会写 talents + audit）
    from lib.core_state import save_candidate
    save_candidate(talent_id, cand)

    # 审计事件（除 save_candidate 内部可能已写，还多写一条明确 created）
    try:
        talent_db.save_audit_event(
            talent_id, "talent.created",
            payload={"source": "talent.cmd_add", "fields": {
                k: v for k, v in fields.items() if k != "email"}},
            actor=args.actor,
        )
    except Exception as e:
        print("[talent.cmd_add] save_audit_event 失败（继续）: {}".format(e),
              file=sys.stderr)

    # ── 自验证（D5）──
    from lib.self_verify import assert_talent_state
    assert_talent_state(talent_id, expected_stage="NEW")

    feishu_ok = None
    if args.feishu_notify:
        feishu_ok = _push_feishu_card(cand)

    result = {
        "ok": True,
        "talent_id": talent_id,
        "candidate_name": cand["candidate_name"],
        "candidate_email": cand["candidate_email"],
        "stage": "NEW",
        "feishu_pushed": feishu_ok,
        "dry_run": False,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("[talent.cmd_add] OK talent={} name={} email={} stage=NEW{}".format(
            talent_id, cand["candidate_name"], cand["candidate_email"],
            " + feishu" if feishu_ok else ""))
    return 0


def main(argv=None):
    args = _build_parser().parse_args(argv)
    return _do_add(args)


if __name__ == "__main__":
    run_with_self_verify("talent.cmd_add", main)
