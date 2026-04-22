#!/usr/bin/env python3
"""template/cmd_preview.py —— 邮件模板列表 / 渲染预览（v3.3）。

【职责】纯 read-only，不发邮件、不写 DB
  - --list                  按目录分组列出所有模板
  - --template T --demo     用 _DEMO_VARS 渲染某个模板（快速 review 话术）
  - --template T --var k=v  用自定义变量渲染（可重复）

【替代关系】功能等价于旧 common/cmd_email_preview.py（Phase 9 删旧脚本）。

【调用示例】
  PYTHONPATH=scripts python3 -m template.cmd_preview --list
  PYTHONPATH=scripts python3 -m template.cmd_preview --template rejection_generic --demo
  PYTHONPATH=scripts python3 -m template.cmd_preview --template round1_invite \\
      --var candidate_name=张三 --var round1_time="2026-04-25 14:00" \\
      --var position="量化研究员" --var position_suffix="（量化研究员）"
"""
from __future__ import print_function

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional

from email_templates import renderer
from email_templates.constants import COMPANY, LOCATION, round_label

from lib.cli_wrapper import UserInputError


# ─── demo 变量（只是给 review 用，不影响生产渲染）────────────────────────────
_DEMO_VARS: Dict[str, Dict[str, str]] = {
    "round1_invite": {
        "candidate_name": "张三",
        "round1_time": "2026-04-25 14:00",
        "position": "量化研究员",
        "position_suffix": "（量化研究员）",
        "location": LOCATION,
        "company": COMPANY,
        "talent_id": "t_demo01",
    },
    "exam_invite": {
        "candidate_name": "张三",
        "company": COMPANY,
        "talent_id": "t_demo01",
    },
    "round2_invite": {
        "candidate_name": "张三",
        "round2_time": "2026-05-08 10:00",
        "location": LOCATION,
        "company": COMPANY,
        "talent_id": "t_demo01",
    },
    "reschedule_ack": {
        "candidate_name": "张三",
        "round_label": round_label(1),
        "company": COMPANY,
        "talent_id": "t_demo01",
    },
    "reschedule": {
        "candidate_name": "张三",
        "new_time": "2026-04-28 15:00",
        "round_label": round_label(1),
        "location": LOCATION,
        "company": COMPANY,
        "talent_id": "t_demo01",
    },
    "defer": {
        "candidate_name": "张三",
        "round_label": round_label(1),
        "company": COMPANY,
        "talent_id": "t_demo01",
    },
    "rejection_exam_no_reply": {
        "candidate_name": "张三",
        "company": COMPANY,
        "talent_id": "t_demo01",
    },
    "rejection_generic": {
        "candidate_name": "张三",
        "company": COMPANY,
        "talent_id": "t_demo01",
    },
}


def _list_templates() -> Dict[str, List[str]]:
    root = Path(renderer.__file__).resolve().parent
    grouped: Dict[str, List[str]] = {}
    for p in root.rglob("*.txt"):
        rel = p.relative_to(root)
        if any(part.startswith("_") or part == "__pycache__" for part in rel.parts):
            continue
        category = rel.parts[0] if len(rel.parts) > 1 else "root"
        grouped.setdefault(category, []).append(p.stem)
    for k in grouped:
        grouped[k].sort()
    return grouped


def _parse_kv(items: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for it in items:
        if "=" not in it:
            raise UserInputError("--var 必须是 key=value 形式，拿到: {!r}".format(it))
        k, v = it.split("=", 1)
        out[k.strip()] = v
    return out


def _build_parser():
    p = argparse.ArgumentParser(
        prog="template.cmd_preview",
        description="邮件模板列表 / 渲染预览（零副作用）",
    )
    p.add_argument("--template", help="模板名（不含 .txt 后缀）")
    p.add_argument("--var", action="append", default=[],
                   help="模板变量，可重复：--var key=value")
    p.add_argument("--demo", action="store_true",
                   help="使用内置 demo 变量快速渲染")
    p.add_argument("--list", action="store_true", help="列出所有可用模板")
    return p


def _do_preview(args):
    if args.list:
        grouped = _list_templates()
        category_labels = {
            "invite":     "面试邀请 (invite/)",
            "exam":       "笔试邀请 (exam/)",
            "reschedule": "改期 / 暂缓 (reschedule/)",
            "rejection":  "拒信 (rejection/)",
            "root":       "其他（位于根目录）",
        }
        order = ["invite", "exam", "reschedule", "rejection", "root"]
        print("可用模板（位于 email_templates/，按用途分类）:")
        for cat in order:
            if cat not in grouped:
                continue
            print()
            print("  [{}]".format(category_labels.get(cat, cat)))
            for n in grouped[cat]:
                print("    · {}".format(n))
        for cat in sorted(grouped.keys()):
            if cat in order:
                continue
            print()
            print("  [{}]".format(cat))
            for n in grouped[cat]:
                print("    · {}".format(n))
        return 0

    if not args.template:
        raise UserInputError("必须指定 --template 或 --list")

    if args.demo:
        if args.template not in _DEMO_VARS:
            raise UserInputError("--demo 不支持模板 {!r}（已支持: {}）".format(
                args.template, ", ".join(sorted(_DEMO_VARS.keys()))))
        variables = dict(_DEMO_VARS[args.template])
    else:
        variables = {}

    variables.update(_parse_kv(args.var))

    # cmd_send 会自动注入 candidate_name/company/talent_id；preview 跑独立流程，需手动补默认
    variables.setdefault("company", COMPANY)
    variables.setdefault("talent_id", "t_demo")

    try:
        subject, body = renderer.render(args.template, **variables)
    except KeyError as e:
        raise UserInputError(
            "模板缺少变量 {}（可加 --demo 用默认值，或用 --var {}=...）".format(
                e, str(e).strip("'")))
    except (renderer.TemplateNotFoundError, renderer.TemplateRenderError) as e:
        raise UserInputError("渲染失败: {}".format(e))

    print("======== Subject ========")
    print(subject)
    print()
    print("======== Body ========")
    print(body, end="")
    return 0


def main(argv=None):
    args = _build_parser().parse_args(argv)
    return _do_preview(args)


if __name__ == "__main__":
    # preview 是只读的，不需要 self-verify alert 包装；UserInputError 自处理
    try:
        sys.exit(main() or 0)
    except UserInputError as e:
        print("[template.cmd_preview] INPUT ERROR: {}".format(e), file=sys.stderr)
        sys.exit(1)
