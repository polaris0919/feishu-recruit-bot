#!/usr/bin/env python3
"""
新建候选人脚本。
系统自动生成 talent_id（t_ + 6位随机字母数字），无需 Agent 手动编造。

用法：
  python3 cmd_new_candidate.py \
    --name 张伟 \
    --email zhangwei@test.com \
    [--phone 13900000001] \
    [--wechat zhangwei_wx] \
    [--position 后端工程师] \
    [--education 本科] \
    [--school 清华大学] \
    [--work-years 4] \
    [--experience "前美团，做过订单系统"] \
    [--source Boss直聘]
"""
import argparse
import random
import string
import sys

from core_state import load_state, save_state, normalize_for_save


def _gen_talent_id(state):
    # type: (dict) -> str
    existing = set(state.get("candidates", {}).keys())
    for _ in range(20):
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        tid = "t_" + suffix
        if tid not in existing:
            return tid
    raise RuntimeError("无法生成唯一 talent_id，请重试")


def main(argv=None):
    p = argparse.ArgumentParser(description="新建候选人")
    p.add_argument("--name",       required=True,  help="候选人姓名")
    p.add_argument("--email",      required=True,  help="候选人邮箱（必填，用于发笔试）")
    p.add_argument("--phone",      default="",     help="手机号")
    p.add_argument("--wechat",     default="",     help="微信号")
    p.add_argument("--position",   default="",     help="应聘岗位")
    p.add_argument("--education",  default="",     help="学历（本科/硕士/博士）")
    p.add_argument("--school",     default="",     help="毕业院校")
    p.add_argument("--work-years", type=int, default=None, help="工作年限")
    p.add_argument("--experience", default="",     help="工作经历简述")
    p.add_argument("--source",     default="",     help="简历来源（Boss直聘/猎头/内推/官网）")
    args = p.parse_args(argv or sys.argv[1:])

    state = load_state()
    talent_id = _gen_talent_id(state)

    cand = {
        "talent_id":       talent_id,
        "stage":           "NEW",
        "audit":           [],
        "candidate_name":  args.name.strip(),
        "candidate_email": args.email.strip(),
        "phone":           args.phone.strip() or None,
        "wechat":          args.wechat.strip() or None,
        "position":        args.position.strip() or None,
        "education":       args.education.strip() or None,
        "school":          args.school.strip() or None,
        "work_years":      args.work_years,
        "experience":      args.experience.strip() or None,
        "source":          args.source.strip() or None,
    }

    if "candidates" not in state:
        state["candidates"] = {}
    state["candidates"][talent_id] = cand
    state = normalize_for_save(state)
    save_state(state)

    lines = [
        "✅ 候选人已录入人才库",
        "- talent_id : {}".format(talent_id),
        "- 姓名     : {}".format(args.name),
        "- 邮箱     : {}".format(args.email),
    ]
    if args.position:
        lines.append("- 岗位     : {}".format(args.position))
    if args.education or args.school:
        lines.append("- 学历     : {} {}".format(args.education, args.school).strip())
    if args.work_years is not None:
        lines.append("- 工作年限 : {}年".format(args.work_years))
    if args.source:
        lines.append("- 来源     : {}".format(args.source))
    lines.append("- 当前阶段 : NEW（等待一面结果）")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
