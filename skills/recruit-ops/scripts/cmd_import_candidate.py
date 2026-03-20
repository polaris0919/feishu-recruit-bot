#!/usr/bin/env python3
"""
导入已有候选人到人才库，支持指定当前所在流程阶段。

适用场景：候选人已在线下/其他渠道进入流程，需要补录进系统。

用法：
  python3 cmd_import_candidate.py --template "<飞书消息内容>"

飞书模板格式（HR 每次按此格式发送，每条消息一位候选人）：

【导入候选人】
姓名：张三（必填）
邮箱：zhangsan@example.com（必填）
电话：13800000000（选填）
岗位：量化研究实习生（选填）
学历：硕士（选填）
院校：复旦大学（选填）
来源：猎头（选填）
当前阶段：笔试中（必填，见下方说明）
一面时间：2026-03-15 14:00（一面邀请中/已确认时必填）
二面时间：2026-03-25 14:00（二面邀请中/已确认时必填）

阶段填写说明：
  新候选人      → 等待安排一面
  一面邀请中    → 已向候选人发出一面邀请，等待确认
  一面已确认    → 一面时间已确认，等待面试
  笔试中        → 一面通过，笔试已发出，等待提交
  待安排二面    → 笔试已审，等待安排二面
  二面邀请中    → 已向候选人发出二面邀请，等待确认
  二面已确认    → 二面时间已确认，等待面试
"""
import argparse
import os
import random
import re
import string
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from core_state import load_state, save_state, normalize_for_save

# ─── 阶段映射 ───────────────────────────────────────────────────────────────────

# (系统阶段, round2_confirmed 三态: True/False/None)
_STAGE_MAP = [
    # 关键词        系统阶段                  round2_confirmed
    ("新候选人",    "NEW",                    None),
    ("待安排一面",  "NEW",                    None),
    ("一面邀请中",  "ROUND1_SCHEDULING",      None),
    ("一面确认中",  "ROUND1_SCHEDULING",      None),
    ("一面已确认",  "ROUND1_SCHEDULED",       None),
    ("一面完成",    "ROUND1_DONE_PASS",       None),
    ("一面通过",    "ROUND1_DONE_PASS",       None),
    ("笔试中",      "EXAM_PENDING",           None),
    ("已发笔试",    "EXAM_PENDING",           None),
    ("笔试完成",    "EXAM_REVIEWED",          None),
    ("笔试已审",    "EXAM_REVIEWED",          None),
    ("待安排二面",  "EXAM_REVIEWED",          None),
    ("二面邀请中",  "ROUND2_SCHEDULED",       False),
    ("二面确认中",  "ROUND2_SCHEDULED",       False),
    ("二面已确认",  "ROUND2_SCHEDULED",       True),
    ("二面完成",    "ROUND2_DONE_PENDING",    None),
]

# 一面时间：强制必填的阶段
_REQUIRES_ROUND1_TIME = {"ROUND1_SCHEDULING", "ROUND1_SCHEDULED"}

# 二面时间：强制必填的阶段
_REQUIRES_ROUND2_TIME = {"ROUND2_SCHEDULED", "ROUND2_DONE_PENDING"}

# 自动生成 exam_id 的阶段
_NEEDS_EXAM_ID = {"EXAM_PENDING", "EXAM_REVIEWED",
                  "ROUND2_SCHEDULED", "ROUND2_DONE_PENDING",
                  "ROUND2_DONE_PASS"}

# 下一步操作提示
_NEXT_STEP = {
    "NEW":                 "请安排一面时间，对我说：\n  安排 {name} 一面，时间是 YYYY-MM-DD HH:MM",
    "ROUND1_SCHEDULING":   "一面邀请已发出，系统自动扫描候选人回信，确认后会创建日历。",
    "ROUND1_SCHEDULED":    "一面已确认，等待面试完成。面试后告知我：\n  {name} 一面通过",
    "ROUND1_DONE_PASS":    "一面已通过，可发笔试。对我说：\n  给 {name} 发笔试",
    "EXAM_PENDING":        "笔试进行中，系统自动扫描邮件，提交后会推送预审报告。",
    "EXAM_REVIEWED":       "笔试已完成，可安排二面。对我说：\n  安排 {name} 二面，时间是 YYYY-MM-DD HH:MM",
    "ROUND2_SCHEDULED":    "二面已安排，系统自动跟踪确认状态。面试后告知我：\n  {name} 二面通过",
    "ROUND2_DONE_PENDING": "二面已完成，请告知结果：\n  {name} 二面通过 / {name} 二面不通过",
}


def _gen_talent_id(state):
    # type: (dict) -> str
    existing = set(state.get("candidates", {}).keys())
    for _ in range(20):
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        tid = "t_" + suffix
        if tid not in existing:
            return tid
    raise RuntimeError("无法生成唯一 talent_id，请重试")


def _gen_exam_id(talent_id):
    # type: (str) -> str
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    return "exam-{}-{}".format(talent_id, ts)


def _parse_import_template(text):
    # type: (str) -> dict
    """解析【导入候选人】飞书模板，返回字段字典。"""
    field_map = {
        "姓名":     "name",
        "邮箱":     "email",
        "电话":     "phone",
        "手机":     "phone",
        "微信":     "wechat",
        "应聘职位": "position",
        "岗位":     "position",
        "职位":     "position",
        "学历":     "education",
        "毕业院校": "school",
        "院校":     "school",
        "工作年限": "work_years",
        "来源渠道": "source",
        "来源":     "source",
        "简历摘要": "resume_summary",
        "简介":     "resume_summary",
        "背景":     "resume_summary",
        "当前阶段": "stage_text",
        "阶段":     "stage_text",
        "状态":     "stage_text",
        "一面时间": "round1_time",
        "二面时间": "round2_time",
        "笔试发送时间": "exam_sent_at",
    }
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("【"):
            continue
        m = re.match(r"^([^：:]+)[：:]\s*(.*)$", line)
        if not m:
            continue
        key_raw = m.group(1).strip()
        val = m.group(2).strip()
        # 去除括号内说明（如"（选填）"）
        val = re.sub(r"[（(][^）)]*[）)]$", "", val).strip()
        if not val:
            continue
        for k, field in field_map.items():
            if k in key_raw:
                result[field] = val
                break
    return result


def _map_stage(stage_text):
    # type: (str) -> tuple
    """
    将 HR 填写的中文阶段映射到 (系统阶段, round2_confirmed)。
    round2_confirmed: True/False/None（None 表示不适用）
    """
    if not stage_text:
        return None, None
    st = stage_text.strip()
    for kw, sys_stage, r2confirmed in _STAGE_MAP:
        if kw in st:
            return sys_stage, r2confirmed
    return None, None


def _validate(fields, stage):
    # type: (dict, str) -> list
    """返回错误说明列表；空列表表示校验通过。"""
    errors = []
    if not fields.get("name"):
        errors.append("姓名（必填）")
    if not fields.get("email"):
        errors.append("邮箱（必填）")
    if not fields.get("stage_text"):
        errors.append("当前阶段（必填）")
    elif stage is None:
        errors.append(
            "当前阶段\"{}\"无法识别，可填：新候选人 / 一面邀请中 / 一面已确认 / "
            "笔试中 / 待安排二面 / 二面邀请中 / 二面已确认".format(fields.get("stage_text", ""))
        )
    if stage in _REQUIRES_ROUND1_TIME and not fields.get("round1_time"):
        errors.append("一面时间（该阶段必填，格式：2026-03-15 14:00）")
    if stage in _REQUIRES_ROUND2_TIME and not fields.get("round2_time"):
        errors.append("二面时间（该阶段必填，格式：2026-03-25 14:00）")
    return errors


def main(argv=None):
    p = argparse.ArgumentParser(description="导入已有候选人到人才库（指定当前阶段）")
    p.add_argument("--template", required=True, help="【导入候选人】飞书模板原文")
    args = p.parse_args(argv or sys.argv[1:])

    fields = _parse_import_template(args.template)
    stage, round2_confirmed = _map_stage(fields.get("stage_text", ""))
    errors = _validate(fields, stage)

    if errors:
        print(
            "ERROR: 候选人信息不完整，以下字段缺失或有误：\n  · {}\n\n"
            "请补充后重新发送。模板示例：\n"
            "【导入候选人】\n姓名：张三\n邮箱：zhangsan@example.com\n"
            "当前阶段：笔试中\n一面时间：2026-03-15 14:00".format("\n  · ".join(errors))
        )
        return 1

    name       = fields["name"].strip()
    email      = fields["email"].strip()
    phone      = fields.get("phone", "").strip() or None
    wechat     = fields.get("wechat", "").strip() or None
    position   = fields.get("position", "").strip() or None
    education  = fields.get("education", "").strip() or None
    school     = fields.get("school", "").strip() or None
    source     = fields.get("source", "").strip() or None
    experience = fields.get("resume_summary", "").strip() or None
    round1_time = fields.get("round1_time", "").strip() or None
    round2_time = fields.get("round2_time", "").strip() or None
    exam_sent_at_str = fields.get("exam_sent_at", "").strip() or None
    stage_cn   = fields.get("stage_text", stage)

    work_years_raw = fields.get("work_years", "")
    try:
        work_years = int(re.sub(r"[^\d]", "", work_years_raw) or "0") or None
    except ValueError:
        work_years = None

    state = load_state()
    talent_id = _gen_talent_id(state)

    # EXAM_PENDING/EXAM_REVIEWED 及之后：自动生成 exam_id
    exam_id = None
    if stage in _NEEDS_EXAM_ID:
        exam_id = _gen_exam_id(talent_id)

    # exam_sent_at：HR 填写时用填写值，否则用当前时间
    exam_sent_at = None
    if stage in _NEEDS_EXAM_ID:
        exam_sent_at = exam_sent_at_str or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    cand = {
        "talent_id":       talent_id,
        "stage":           stage,
        "audit":           [],
        "candidate_name":  name,
        "candidate_email": email,
        "phone":           phone,
        "wechat":          wechat,
        "position":        position,
        "education":       education,
        "school":          school,
        "work_years":      work_years,
        "experience":      experience,
        "source":          source,
        "round1_time":     round1_time,
        "round2_time":     round2_time,
        "exam_id":         exam_id,
        "exam_sent_at":    exam_sent_at,
    }

    if "candidates" not in state:
        state["candidates"] = {}
    state["candidates"][talent_id] = cand
    state = normalize_for_save(state)
    save_state(state)

    # ── 同步到 DB ──────────────────────────────────────────────────────────────
    db_ok = False
    try:
        import talent_db as _tdb
        if _tdb._is_enabled():
            _tdb.sync_state_to_db({"candidates": {talent_id: cand}})

            # 阶段特定补充字段（round1/round2 invite/confirm flags）
            if stage == "ROUND1_SCHEDULING":
                _tdb.save_round1_invite_info(talent_id)

            elif stage == "ROUND1_SCHEDULED":
                _tdb.save_round1_invite_info(talent_id)
                _tdb.mark_round1_confirmed(talent_id)

            elif stage in ("ROUND1_DONE_PASS", "EXAM_PENDING", "EXAM_REVIEWED"):
                # round1 已完成，设置 confirmed 标记但不改变 current_stage（直接 SQL）
                try:
                    import psycopg2 as _pg2
                    _conn = _pg2.connect(**_tdb._conn_params())
                    with _conn.cursor() as _cur:
                        _cur.execute(
                            "UPDATE talents SET round1_confirmed = TRUE WHERE talent_id = %s",
                            (talent_id,),
                        )
                    _conn.commit()
                    _conn.close()
                except Exception as _e:
                    print("WARN: round1_confirmed 设置失败: {}".format(_e), file=sys.stderr)

            elif stage in ("ROUND2_SCHEDULED", "ROUND2_DONE_PENDING"):
                # round1 也已完成，确保 confirmed 标记正确（不改 current_stage）
                try:
                    import psycopg2 as _pg2
                    _conn = _pg2.connect(**_tdb._conn_params())
                    with _conn.cursor() as _cur:
                        _cur.execute(
                            "UPDATE talents SET round1_confirmed = TRUE WHERE talent_id = %s",
                            (talent_id,),
                        )
                    _conn.commit()
                    _conn.close()
                except Exception as _e:
                    print("WARN: round1_confirmed 设置失败: {}".format(_e), file=sys.stderr)
                _tdb.save_round2_invite_info(talent_id)
                if round2_confirmed is True:
                    _tdb.mark_round2_confirmed(talent_id)

            db_ok = True
    except Exception as e:
        print("WARN: DB 写入部分失败: {}".format(e), file=sys.stderr)

    # ── 输出结果 ───────────────────────────────────────────────────────────────
    hint = _NEXT_STEP.get(stage, "候选人已导入，请根据实际情况推进流程。").format(name=name)

    lines = [
        "[候选人已导入]",
        "- talent_id : {}".format(talent_id),
        "- 姓名     : {}".format(name),
        "- 邮箱     : {}".format(email),
    ]
    if position:
        lines.append("- 岗位     : {}".format(position))
    edu_str = " ".join(filter(None, [education, school]))
    if edu_str:
        lines.append("- 学历     : {}".format(edu_str))
    if source:
        lines.append("- 来源     : {}".format(source))
    lines.append("- 导入阶段 : {}（{}）".format(stage_cn, stage))
    if round1_time:
        lines.append("- 一面时间 : {}".format(round1_time))
    if round2_time:
        lines.append("- 二面时间 : {}".format(round2_time))
    if exam_id:
        lines.append("- 笔试ID   : {}".format(exam_id))
    lines.append("- DB 状态  : {}".format("已同步" if db_ok else "仅写入本地状态文件"))
    lines.append("")
    lines.append("下一步：{}".format(hint))

    output = "\n".join(lines)
    print(output)

    # ── 飞书通知老板 ────────────────────────────────────────────────────────────
    try:
        import feishu_notify as _fn
        edu_str = " ".join(filter(None, [education, school]))
        summary_lines = []
        if position:
            summary_lines.append("  · 应聘岗位：{}".format(position))
        if edu_str:
            summary_lines.append("  · 学历：{}".format(edu_str))
        if source:
            summary_lines.append("  · 来源：{}".format(source))
        if round1_time:
            summary_lines.append("  · 一面时间：{}".format(round1_time))
        if round2_time:
            summary_lines.append("  · 二面时间：{}".format(round2_time))

        notify_lines = [
            "📋 候选人补录（{}）".format(stage_cn),
            "━━━━━━━━━━━━━━━━━━━━",
            "姓名：{}　｜　ID: {}".format(name, talent_id),
            "邮箱：{}".format(email),
        ]
        if phone:
            notify_lines.append("电话：{}".format(phone))
        if summary_lines:
            notify_lines.append("")
            notify_lines.extend(summary_lines)
        notify_lines += [
            "━━━━━━━━━━━━━━━━━━━━",
            "下一步：{}".format(hint),
        ]
        _fn.send_text("\n".join(notify_lines))
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
