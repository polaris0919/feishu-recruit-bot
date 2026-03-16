#!/usr/bin/env python3
"""
笔试回复预审模块。

输入：邮件解析结果（发件人、时间、附件代码文本、正文）+ 候选人发卷时间
输出：结构化预审结果 dict + 格式化飞书报告文本
"""
from __future__ import print_function

import os
import re
import sys
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# 评分权重配置（可通过外部 JSON 覆盖）
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    "score_weights": {
        "has_file":          40,   # 提交了代码文件
        "code_volume":       20,   # 有效代码行 >= 50
        "has_functions":     10,   # 有函数封装
        "has_comments":      10,   # 有注释
        "uses_data_libs":    10,   # 使用 pandas/numpy 等
        "no_risk_issues":    10,   # 无逻辑风险问题
        "penalty_per_warn":  -5,   # 每个警告扣分
    },
    "time_levels": {
        "too_fast_hours":    2,    # 小于此小时数 → 极快
        "normal_hours":      24,   # 小于此小时数 → 正常
        "slow_hours":        72,   # 小于此小时数 → 较慢，否则 → 超时
    },
    "min_code_lines":        50,   # 有效代码行达到此数才得 code_volume 分
}


def _load_config():
    cfg = dict(_DEFAULT_CONFIG)
    cfg_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "exam_prereview_config.json"
    )
    cfg_path = os.path.normpath(cfg_path)
    if os.path.isfile(cfg_path):
        try:
            import json
            with open(cfg_path) as f:
                override = json.load(f)
            cfg.update(override)
        except Exception:
            pass
    return cfg


# ---------------------------------------------------------------------------
# 1. 答题用时分析
# ---------------------------------------------------------------------------

def _parse_datetime(s):
    # type: (str) -> datetime or None
    """解析多种日期格式，返回 naive UTC datetime。"""
    if not s:
        return None
    s = s.strip()
    # 去掉时区偏移（+0800 / +08:00 / UTC 等）
    s_clean = re.sub(r'[+-]\d{2}:?\d{2}\s*$', '', s).strip()
    s_clean = re.sub(r'\s+\([\w]+\)\s*$', '', s_clean).strip()
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%a, %d %b %Y %H:%M:%S",
        "%d %b %Y %H:%M:%S",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s_clean, fmt)
        except ValueError:
            continue
    return None


def analyze_response_time(exam_sent_at, reply_date_str, config=None):
    # type: (str, str, dict) -> dict
    """
    计算答题用时。
    返回 dict: hours, minutes, label, sent_str, reply_str
    """
    cfg = config or _load_config()
    tl = cfg["time_levels"]

    sent_dt = _parse_datetime(exam_sent_at) if exam_sent_at else None
    reply_dt = _parse_datetime(reply_date_str) if reply_date_str else None

    if not sent_dt or not reply_dt:
        return {
            "available": False,
            "sent_str": exam_sent_at or "未知",
            "reply_str": reply_date_str or "未知",
            "label": "无法计算（时间信息缺失）",
        }

    delta = reply_dt - sent_dt
    total_minutes = int(delta.total_seconds() / 60)
    if total_minutes < 0:
        # 时区问题导致负值，宽容处理
        total_minutes = abs(total_minutes)

    hours = total_minutes // 60
    minutes = total_minutes % 60

    if hours < tl["too_fast_hours"]:
        label = "极快（{}h{}m）⚠️ 注意质量".format(hours, minutes)
    elif hours < tl["normal_hours"]:
        label = "正常（{}h{}m）".format(hours, minutes)
    elif hours < tl["slow_hours"]:
        label = "较慢（{}h{}m）".format(hours, minutes)
    else:
        days = hours // 24
        label = "超时（约{}天）⚠️".format(days)

    return {
        "available": True,
        "total_minutes": total_minutes,
        "hours": hours,
        "minutes": minutes,
        "label": label,
        "sent_str": sent_dt.strftime("%Y-%m-%d %H:%M"),
        "reply_str": reply_dt.strftime("%Y-%m-%d %H:%M"),
    }


# ---------------------------------------------------------------------------
# 2. 代码质量分析（5 个维度）
# ---------------------------------------------------------------------------

def analyze_code_quality(code_text, config=None):
    # type: (str, dict) -> dict
    """
    对代码文本做静态分析，返回质量分析结果 dict。
    不执行代码，纯文本/正则分析。
    """
    cfg = config or _load_config()
    weights = cfg["score_weights"]
    min_lines = cfg["min_code_lines"]

    if not code_text or not code_text.strip():
        return {
            "has_code": False,
            "score": 0,
            "summary": "未提取到代码内容",
            "warnings": [],
            "metrics": {},
        }

    lines = code_text.splitlines()
    total_lines = len(lines)
    code_lines = [l for l in lines if l.strip() and not l.strip().startswith("#")]
    comment_lines = [l for l in lines if l.strip().startswith("#")]
    blank_lines = [l for l in lines if not l.strip()]
    effective_count = len(code_lines)
    comment_count = len(comment_lines)
    comment_rate = int(comment_count * 100 / max(total_lines, 1))

    # a. 函数/类统计
    func_matches = re.findall(r'^\s*def\s+\w+', code_text, re.MULTILINE)
    class_matches = re.findall(r'^\s*class\s+\w+', code_text, re.MULTILINE)
    func_count = len(func_matches)
    class_count = len(class_matches)

    # 函数平均行数（粗估：总有效行 / 函数数）
    avg_func_lines = int(effective_count / max(func_count, 1))

    # b. 命名规范：极短变量名（单字母，排除 i/j/k/n/x/y 循环变量）
    bad_names = re.findall(r'\b([a-wz])\s*=\s*[^=]', code_text)
    bad_names = [n for n in bad_names if n not in ('i', 'j', 'k', 'n', 'x', 'y', 'e', 'f')]
    has_short_names = len(bad_names) > 3

    # c. docstring
    has_docstring = '"""' in code_text or "'''" in code_text

    # d. 数据处理库
    data_libs = []
    for lib in ["pandas", "numpy", "scipy", "sklearn", "matplotlib", "seaborn", "plotly"]:
        if re.search(r'import\s+{}'.format(lib), code_text) or \
           re.search(r'from\s+{}'.format(lib), code_text):
            data_libs.append(lib)

    # e. 数据清洗关键词
    cleaning_keywords = ["dropna", "fillna", "drop_duplicates", "strip()", "replace(",
                         "astype(", "to_datetime", "isnull", "notnull"]
    has_cleaning = any(kw in code_text for kw in cleaning_keywords)

    # f. 结果输出
    output_keywords = ["to_csv", "to_excel", "print(", "savefig", "to_json", "write("]
    has_output = any(kw in code_text for kw in output_keywords)

    # g. 逻辑风险
    warnings = []
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if s.startswith("#"):
            continue
        if "eval(" in line or "exec(" in line:
            warnings.append("L{}: 使用了 eval/exec（潜在安全风险）".format(i))
        if re.search(r'password\s*=\s*["\']', line, re.IGNORECASE):
            warnings.append("L{}: 可能存在硬编码密码".format(i))
        if re.search(r'secret\s*=\s*["\']', line, re.IGNORECASE):
            warnings.append("L{}: 可能存在硬编码密钥".format(i))
        if "except:" in line or "except Exception:" in line:
            next_lines = lines[i:i+2] if i < len(lines) else []
            if any("pass" in nl for nl in next_lines):
                warnings.append("L{}: except 捕获过宽且被吞（except...pass）".format(i))
        if avg_func_lines > 80 and func_count > 0:
            # 只警告一次
            if not any("函数过长" in w for w in warnings):
                warnings.append("函数平均长度 {}行，建议拆分".format(avg_func_lines))
    if has_short_names:
        warnings.append("存在较多单字母变量名，命名规范待改进")

    # --------------- 评分 ---------------
    score = 0
    score += weights["has_file"]                                      # 有代码文件
    if effective_count >= min_lines:
        score += weights["code_volume"]
    if func_count >= 2:
        score += weights["has_functions"]
    if comment_rate >= 5 or has_docstring:
        score += weights["has_comments"]
    if data_libs:
        score += weights["uses_data_libs"]
    if not warnings:
        score += weights["no_risk_issues"]
    penalty = len(warnings) * abs(weights["penalty_per_warn"])
    score = max(0, min(100, score - penalty))

    return {
        "has_code": True,
        "score": score,
        "warnings": warnings[:8],   # 最多展示8条
        "metrics": {
            "total_lines": total_lines,
            "effective_lines": effective_count,
            "comment_lines": comment_count,
            "comment_rate": comment_rate,
            "func_count": func_count,
            "class_count": class_count,
            "avg_func_lines": avg_func_lines,
            "has_docstring": has_docstring,
            "data_libs": data_libs,
            "has_cleaning": has_cleaning,
            "has_output": has_output,
        },
    }


# ---------------------------------------------------------------------------
# 3. 提交完整性检查
# ---------------------------------------------------------------------------

def analyze_completeness(attachment_info_list, body_text):
    # type: (list, str) -> dict
    """
    attachment_info_list: [{"filename": "xxx.py", "size": 1234, "is_text": True}, ...]
    """
    code_exts = {".py", ".ipynb", ".r", ".R", ".sql", ".java", ".cpp", ".go"}
    result_exts = {".csv", ".txt", ".xlsx", ".png", ".jpg", ".pdf", ".json"}

    code_files = [a for a in attachment_info_list
                  if os.path.splitext(a.get("filename", ""))[1].lower() in code_exts]
    result_files = [a for a in attachment_info_list
                    if os.path.splitext(a.get("filename", ""))[1].lower() in result_exts]
    other_files = [a for a in attachment_info_list
                   if a not in code_files and a not in result_files]

    body_len = len((body_text or "").strip())
    has_body = body_len > 30

    return {
        "total_attachments": len(attachment_info_list),
        "code_files": [a["filename"] for a in code_files],
        "result_files": [a["filename"] for a in result_files],
        "other_files": [a["filename"] for a in other_files],
        "has_body_text": has_body,
        "body_length": body_len,
    }


# ---------------------------------------------------------------------------
# 4. 主入口：run_prereview
# ---------------------------------------------------------------------------

def run_prereview(email_data, candidate_info=None):
    # type: (dict, dict) -> dict
    """
    email_data keys:
      sender, subject, date (回复时间字符串),
      body_text, code_text, attachment_info_list
    candidate_info keys:
      talent_id, candidate_name, exam_sent_at, exam_id
    返回 prereview_result dict，包含 score/report_text/summary 等。
    """
    cfg = _load_config()
    cand = candidate_info or {}

    # 1. 答题用时
    time_result = analyze_response_time(
        cand.get("exam_sent_at"),
        email_data.get("date"),
        config=cfg,
    )

    # 2. 代码质量
    code_result = analyze_code_quality(
        email_data.get("code_text", ""),
        config=cfg,
    )

    # 3. 完整性
    complete_result = analyze_completeness(
        email_data.get("attachment_info_list", []),
        email_data.get("body_text", ""),
    )

    # 4. 综合评分
    final_score = code_result["score"] if code_result["has_code"] else 0

    # 5. 生成报告
    report_text = _format_prereview_report(
        email_data, cand, time_result, code_result, complete_result, final_score
    )

    # 6. 生成摘要（存数据库用）
    summary = _make_db_summary(time_result, code_result, complete_result, final_score)

    return {
        "score": final_score,
        "time_result": time_result,
        "code_result": code_result,
        "complete_result": complete_result,
        "report_text": report_text,
        "db_summary": summary,
    }


# ---------------------------------------------------------------------------
# 5. 报告格式化
# ---------------------------------------------------------------------------

def _format_prereview_report(email_data, cand, time_r, code_r, comp_r, score):
    tid = cand.get("talent_id", "未知")
    name = cand.get("candidate_name", "")
    name_part = "（{}）".format(name) if name else ""

    lines = [
        "📋 笔试预审报告 | 候选人 {}{}".format(tid, name_part),
        "",
        "📧 回复邮件",
        "  - 发件人: {}".format(email_data.get("sender", "未知")),
        "  - 主题: {}".format(email_data.get("subject", "未知")),
        "  - 回复时间: {}".format(time_r.get("reply_str", "未知")),
        "",
    ]

    # 答题用时
    lines.append("⏱ 答题用时")
    if time_r.get("available"):
        lines.append("  - 发出时间: {}".format(time_r["sent_str"]))
        lines.append("  - 回复时间: {}".format(time_r["reply_str"]))
        lines.append("  - 用时: {}".format(time_r["label"]))
    else:
        lines.append("  - {}".format(time_r.get("label", "无法计算")))
    lines.append("")

    # 提交完整性
    lines.append("📁 提交完整性")
    lines.append("  - 附件数量: {}个".format(comp_r["total_attachments"]))
    if comp_r["code_files"]:
        lines.append("  - 代码文件: {}".format(", ".join(comp_r["code_files"])))
    else:
        lines.append("  - 代码文件: 未找到 ⚠️")
    if comp_r["result_files"]:
        lines.append("  - 结果文件: {}".format(", ".join(comp_r["result_files"])))
    if comp_r["other_files"]:
        lines.append("  - 其他附件: {}".format(", ".join(comp_r["other_files"])))
    lines.append("  - 正文说明: {}".format(
        "有（{}字）".format(comp_r["body_length"]) if comp_r["has_body_text"] else "无"
    ))
    lines.append("")

    # 代码质量
    if code_r["has_code"]:
        m = code_r["metrics"]
        lines.append("💻 代码质量预审 [初评: {}/100]".format(score))
        lines.append("  - 有效代码行: {}行 / 注释率: {}%".format(
            m["effective_lines"], m["comment_rate"]))
        func_info = "{}个函数".format(m["func_count"])
        if m["class_count"]:
            func_info += " / {}个类".format(m["class_count"])
        if m["func_count"] > 0:
            func_info += " / 平均{}行".format(m["avg_func_lines"])
        lines.append("  - 结构: {}".format(func_info))
        if m["data_libs"]:
            lines.append("  - 使用了: {}".format(", ".join(m["data_libs"])))
        cleaning_str = "有数据清洗逻辑 ✓" if m["has_cleaning"] else "未检测到数据清洗"
        output_str = "有结果输出 ✓" if m["has_output"] else "未检测到输出"
        lines.append("  - 数据处理: {} | {}".format(cleaning_str, output_str))
        if code_r["warnings"]:
            lines.append("  - 警告（{}条）:".format(len(code_r["warnings"])))
            for w in code_r["warnings"]:
                lines.append("    · {}".format(w))
        else:
            lines.append("  - 未发现明显风险 ✓")
    else:
        lines.append("💻 代码质量预审")
        lines.append("  - 未提取到代码内容（可能为 zip/binary 附件或纯文字回复）")
    lines.append("")

    # 建议
    lines.append("📌 建议")
    advice = _generate_advice(time_r, code_r, comp_r, score)
    lines.append("  {}".format(advice))
    lines.append("")
    exam_id = cand.get("exam_id", "")
    lines.append('  -> 告知 Agent "笔试通过 {}" 或 "笔试不过 {}" 推进流程'.format(tid, tid))

    return "\n".join(lines)


def _generate_advice(time_r, code_r, comp_r, score):
    parts = []
    if time_r.get("available"):
        hours = time_r.get("hours", 0)
        cfg = _load_config()
        tl = cfg["time_levels"]
        if hours < tl["too_fast_hours"]:
            parts.append("回复极快，需关注答题质量是否仓促。")
        elif hours >= tl["slow_hours"]:
            parts.append("回复已超过3天，时间观念需关注。")
    if not comp_r["code_files"]:
        parts.append("未找到代码文件，请确认附件是否完整。")
    if code_r["has_code"]:
        m = code_r["metrics"]
        if not m["data_libs"]:
            parts.append("未使用数据分析库，建议确认解题方式。")
        if not m["has_cleaning"]:
            parts.append("未检测到数据清洗逻辑，可重点审查。")
        if code_r["warnings"]:
            first_warn = code_r["warnings"][0]
            parts.append("建议重点审查: {}。".format(first_warn))
    if not parts:
        parts.append("整体提交正常，请结合代码逻辑进行人工复核。")
    return " ".join(parts)


def _make_db_summary(time_r, code_r, comp_r, score):
    """生成存入 exam_notes 的简短摘要。"""
    parts = []
    if time_r.get("available"):
        parts.append("用时{}".format(time_r["label"].split("（")[0]))
    if comp_r["code_files"]:
        parts.append("代码文件:{}".format(",".join(comp_r["code_files"][:2])))
    if code_r["has_code"]:
        m = code_r["metrics"]
        parts.append("{}行有效代码".format(m["effective_lines"]))
        if m["data_libs"]:
            parts.append("使用{}".format("/".join(m["data_libs"][:2])))
        if code_r["warnings"]:
            parts.append("{}条警告".format(len(code_r["warnings"])))
    parts.append("预审分:{}".format(score))
    return "[自动预审] " + " | ".join(parts)


# ---------------------------------------------------------------------------
# CLI 测试入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 简单自测
    demo_email = {
        "sender": "test@example.com",
        "subject": "Re: 【笔试邀请】技术岗位笔试",
        "date": "2026-03-16 10:30:00",
        "body_text": "您好，我已完成笔试，请查收附件，代码实现了基本的数据分析逻辑。",
        "code_text": """
import pandas as pd
import numpy as np

def load_data(path):
    \"\"\"加载数据\"\"\"
    df = pd.read_csv(path)
    return df

def clean_data(df):
    df = df.dropna()
    df = df.drop_duplicates()
    return df

def analyze(df):
    result = df.groupby('symbol').agg({'price': 'mean', 'volume': 'sum'})
    return result

def main():
    df = load_data('data.csv')
    df = clean_data(df)
    result = analyze(df)
    result.to_csv('output.csv')
    print(result.head())

if __name__ == '__main__':
    main()
""",
        "attachment_info_list": [
            {"filename": "solution.py", "size": 1500, "is_text": True},
            {"filename": "output.csv", "size": 2048, "is_text": True},
        ],
    }
    demo_cand = {
        "talent_id": "t_test01",
        "candidate_name": "张三",
        "exam_sent_at": "2026-03-15 15:40:00",
        "exam_id": "exam-t_test01-20260315154000",
    }
    result = run_prereview(demo_email, demo_cand)
    print(result["report_text"])
    print("\n--- DB 摘要 ---")
    print("exam_notes:", result["db_summary"])
    print("exam_score:", result["score"])
