#!/usr/bin/env python3

import os, sys
_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.normpath(os.path.join(_HERE, "..", "lib"))
for _p in (_LIB, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

"""
新建候选人脚本。
系统自动生成 talent_id（t_ + 6位随机字母数字），无需 Agent 手动编造。

用法1：逐字段参数
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
    [--source Boss直聘] \
    [--resume-summary "金融工程背景..."] \
    [--feishu-notify]

用法2：解析飞书【新候选人】模板（通过 --template 参数传入原始文本）
  python3 cmd_new_candidate.py --template "【新候选人】
姓名：张三
邮箱：zhangsan@example.com
..."

飞书模板格式（HR 发给 OC）：
【新候选人】
姓名：（必填）
邮箱：（必填）
电话：（选填）
微信：（选填）
应聘职位：（选填）
学历：（选填）
毕业院校：（选填）
工作年限：（选填）
来源渠道：（选填）
简历摘要：（选填）
"""
import argparse
import os
import random
import re
import string
import sys

from core_state import load_state, save_candidate


def _gen_talent_id(state):
    # type: (dict) -> str
    existing = set(state.get("candidates", {}).keys())
    for _ in range(20):
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        tid = "t_" + suffix
        if tid not in existing:
            return tid
    raise RuntimeError("无法生成唯一 talent_id，请重试")


def _parse_template(text):
    # type: (str) -> dict
    """
    解析【新候选人】飞书模板，返回字段字典。
    字段映射：姓名/邮箱/电话/微信/应聘职位/学历/毕业院校/工作年限/来源渠道/简历摘要
    """
    field_map = {
        "姓名": "name",
        "邮箱": "email",
        "电话": "phone",
        "手机": "phone",
        "微信": "wechat",
        "应聘职位": "position",
        "岗位": "position",
        "职位": "position",
        "学历": "education",
        "毕业院校": "school",
        "院校": "school",
        "工作年限": "work_years",
        "来源渠道": "source",
        "来源": "source",
        "简历摘要": "resume_summary",
        "简介": "resume_summary",
        "背景": "resume_summary",
    }
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("【"):
            continue
        # 匹配 "字段名：值" 格式（支持中英文冒号）
        m = re.match(r"^([^：:]+)[：:]\s*(.*)$", line)
        if not m:
            continue
        key_raw = m.group(1).strip()
        val = m.group(2).strip()
        # 去除括号内的说明（如"（选填）"、"（必填）"）
        val = re.sub(r"[（(][^）)]*[）)]$", "", val).strip()
        if not val:
            continue
        for k, field in field_map.items():
            if k in key_raw:
                result[field] = val
                break
    return result


def _validate_fields(fields):
    # type: (dict) -> list
    """返回缺失的必填字段列表。"""
    missing = []
    if not fields.get("name"):
        missing.append("姓名")
    if not fields.get("email"):
        missing.append("邮箱")
    return missing


def main(argv=None):
    p = argparse.ArgumentParser(description="新建候选人")
    p.add_argument("--template",      default="",  help="直接传入【新候选人】模板原文（自动解析）")
    p.add_argument("--name",          default="",  help="候选人姓名")
    p.add_argument("--email",         default="",  help="候选人邮箱（必填，用于发笔试）")
    p.add_argument("--phone",         default="",  help="手机号")
    p.add_argument("--wechat",        default="",  help="微信号")
    p.add_argument("--position",      default="",  help="应聘岗位")
    p.add_argument("--education",     default="",  help="学历（本科/硕士/博士）")
    p.add_argument("--school",        default="",  help="毕业院校")
    p.add_argument("--work-years",    type=int, default=None, help="工作年限")
    p.add_argument("--experience",    default="",  help="工作经历简述")
    p.add_argument("--resume-summary", default="", help="简历摘要（HR 填写）")
    p.add_argument("--source",        default="",  help="简历来源（Boss直聘/猎头/内推/官网）")
    p.add_argument("--cv-path",       default="",  help="简历 PDF 本地路径（由 cmd_parse_cv.py 自动传入）")
    p.add_argument("--feishu-notify", action="store_true", help="录入成功后飞书通知老板")
    args = p.parse_args(argv or sys.argv[1:])

    # 从模板解析字段（若提供了 --template）
    if args.template:
        # 防止 Agent 误用：【导入候选人】必须用 cmd_import_candidate.py
        if "【导入候选人】" in args.template:
            print(
                "ERROR: 检测到【导入候选人】标识符。\n"
                "此命令（cmd_new_candidate.py）仅处理【新候选人】模板。\n"
                "请改用：python3 intake/cmd_import_candidate.py --template \"<消息原文>\"\n"
                "cmd_import_candidate.py 支持指定当前阶段，不会重复发送邮件。"
            )
            return 1
        tpl_fields = _parse_template(args.template)
        missing = _validate_fields(tpl_fields)
        if missing:
            print(
                "ERROR: 候选人信息不完整，以下必填字段缺失：{}\n"
                "请补充后重新发送，格式示例：\n"
                "  姓名：张三\n"
                "  邮箱：zhangsan@example.com".format("、".join(missing))
            )
            return 1
        # 模板字段覆盖命令行参数
        if tpl_fields.get("name"):
            args.name = tpl_fields["name"]
        if tpl_fields.get("email"):
            args.email = tpl_fields["email"]
        if tpl_fields.get("phone"):
            args.phone = tpl_fields["phone"]
        if tpl_fields.get("wechat"):
            args.wechat = tpl_fields["wechat"]
        if tpl_fields.get("position"):
            args.position = tpl_fields["position"]
        if tpl_fields.get("education"):
            args.education = tpl_fields["education"]
        if tpl_fields.get("school"):
            args.school = tpl_fields["school"]
        if tpl_fields.get("work_years"):
            try:
                args.work_years = int(re.sub(r"[^\d]", "", tpl_fields["work_years"]) or "0")
            except ValueError:
                pass
        if tpl_fields.get("source"):
            args.source = tpl_fields["source"]
        if tpl_fields.get("resume_summary"):
            args.experience = tpl_fields["resume_summary"]

    # 校验必填项
    if not args.name.strip():
        print("ERROR: --name 必填（候选人姓名）")
        return 1
    if not args.email.strip():
        print("ERROR: --email 必填（候选人邮箱）")
        return 1

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
        "experience":      (args.resume_summary or args.experience).strip() or None,
        "source":          args.source.strip() or None,
        "cv_path":         args.cv_path.strip() or None,
    }

    save_candidate(talent_id, cand)

    lines = [
        "[新候选人已录入]",
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
    if args.experience:
        lines.append("- 简历摘要 : {}".format(args.experience[:80]))
    lines.append("- 当前阶段 : NEW（等待老板安排一面时间）")
    lines.append("")
    lines.append("老板可通过以下命令安排一面：")
    lines.append("  python3 round1/cmd_round1_schedule.py --talent-id {} --time \"YYYY-MM-DD HH:MM\"".format(talent_id))

    output = "\n".join(lines)
    print(output)

    # 飞书通知老板
    if args.feishu_notify or args.template:
        try:
            import feishu as _fn
            edu_str = " ".join(filter(None, [args.education, args.school]))
            summary_lines = []
            if args.position:
                summary_lines.append("  · 应聘岗位：{}".format(args.position))
            if edu_str:
                summary_lines.append("  · 学历：{}".format(edu_str))
            if args.work_years is not None:
                summary_lines.append("  · 工作年限：{}年".format(args.work_years))
            if args.source:
                summary_lines.append("  · 来源：{}".format(args.source))
            if args.experience:
                summary_lines.append("  · 简历摘要：{}".format(args.experience[:80]))

            notify_lines = [
                "📋 新候选人待安排一面",
                "━━━━━━━━━━━━━━━━━━━━",
                "姓名：{}　｜　ID: {}".format(args.name, talent_id),
                "邮箱：{}".format(args.email),
            ]
            if args.phone:
                notify_lines.append("电话：{}".format(args.phone))
            if summary_lines:
                notify_lines.append("")
                notify_lines.extend(summary_lines)
            notify_lines += [
                "━━━━━━━━━━━━━━━━━━━━",
                "如需安排一面，请回复：",
                "  安排 {} 一面，时间是 YYYY-MM-DD HH:MM".format(args.name),
            ]
            _fn.send_text("\n".join(notify_lines))
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
