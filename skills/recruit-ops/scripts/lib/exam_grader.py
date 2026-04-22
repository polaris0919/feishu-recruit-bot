#!/usr/bin/env python3
"""
lib/exam_grader.py —— AI 笔试代码评分（v3.5：原 exam/exam_ai_reviewer.py 搬到 lib/）。

【为什么搬】
  v3.5 的「邮件 LLM 分析」全部归到 inbox/analyzer.py，分布式 prompt 与逻辑都在
  prompts/ + inbox/。但「笔试代码评分」是另一个非邮件 LLM 任务，逻辑完全独立：
  读 rubric + 候选人文件 → 出结构化 JSON 评分 → 给老板做参考。
  把它放进 lib/ 是因为它是被 cmd_exam_ai_review 这种 CLI 复用的「库」，
  不是 atomic CLI 本身。

【为什么 prompt 没全搬到 prompts/exam_grader.json】
  这里的 prompt 是动态拼接：rubric.json 的维度 / output_schema / 候选人代码内容
  都得在运行时塞进去。固定可抽的部分只有「角色 framing + 输出格式约束 + 强禁词」，
  这部分已经抽到 prompts/exam_grader.json，build_prompt 在运行时读取并组装。

使用方式（库）：
    from lib.exam_grader import review_submission
    result = review_submission(candidate_data, rubric_path="...")

返回值结构严格遵守 rubric["output_schema"]，并附带 _meta 段记录模型、错误等信息。
"""
from __future__ import print_function

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from lib import config as _cfg
from prompts import load_prompt


# ---------------------------------------------------------------------------
# 1. Rubric 加载与校验
# ---------------------------------------------------------------------------

DEFAULT_RUBRIC_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "exam_files", "rubric.json",
    )
)


class RubricError(Exception):
    pass


def load_rubric(path=None):
    # type: (Optional[str]) -> Dict[str, Any]
    p = path or DEFAULT_RUBRIC_PATH
    if not os.path.isfile(p):
        raise RubricError(
            "rubric 文件不存在: {}（请从 rubric.example.json 复制并改写后放到此路径）".format(p)
        )
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as e:
        raise RubricError("rubric.json 解析失败: {}".format(e))

    _validate_rubric(data)
    return data


def _validate_rubric(rubric):
    # type: (Dict[str, Any]) -> None
    if "dimensions" not in rubric or not isinstance(rubric["dimensions"], list):
        raise RubricError("rubric 缺少 dimensions 数组")
    total_w = 0
    for dim in rubric["dimensions"]:
        for k in ("key", "label", "weight", "scoring_mode"):
            if k not in dim:
                raise RubricError("dimension 缺少字段 {}: {}".format(k, dim.get("key")))
        total_w += int(dim["weight"])
        mode = dim["scoring_mode"]
        if mode == "anchor":
            if "anchors" not in dim or not isinstance(dim["anchors"], dict):
                raise RubricError("anchor 模式 dimension {} 缺少 anchors".format(dim["key"]))
        elif mode == "checklist":
            if "checklist" not in dim or not isinstance(dim["checklist"], list):
                raise RubricError("checklist 模式 dimension {} 缺少 checklist".format(dim["key"]))
            for item in dim["checklist"]:
                for k in ("key", "label", "max", "anchors"):
                    if k not in item:
                        raise RubricError(
                            "checklist 项缺少字段 {}: dim={} item={}".format(
                                k, dim["key"], item.get("key")
                            )
                        )
        else:
            raise RubricError("未知 scoring_mode: {}".format(mode))
    if total_w != 100:
        raise RubricError(
            "dimensions weight 之和必须等于 100，当前 = {}".format(total_w)
        )


# ---------------------------------------------------------------------------
# 2. Prompt 构造
# ---------------------------------------------------------------------------

# 单文件最大字符数（防止超长样本撑爆 LLM 上下文）
MAX_CHARS_PER_FILE = 12000
MAX_TOTAL_CHARS    = 80000


def _truncate(text, limit):
    # type: (str, int) -> str
    if not text:
        return ""
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.7)]
    tail = text[-int(limit * 0.25):]
    return head + "\n\n... [truncated {} chars] ...\n\n".format(len(text) - len(head) - len(tail)) + tail


def _format_files_block(files, label):
    # type: (List[Dict[str, str]], str) -> str
    if not files:
        return "[{}] (无)\n".format(label)
    chunks = ["[{}] 共 {} 个文件\n".format(label, len(files))]
    total = 0
    for f in files:
        path = f.get("path") or f.get("name") or "(unknown)"
        content = (f.get("content") or "").strip()
        content = _truncate(content, MAX_CHARS_PER_FILE)
        if total + len(content) > MAX_TOTAL_CHARS:
            chunks.append("--- file: {} (skipped: total budget exceeded) ---\n".format(path))
            continue
        total += len(content)
        chunks.append("--- file: {} ---\n{}\n".format(path, content))
    return "\n".join(chunks)


def _load_framing():
    # type: () -> Dict[str, Any]
    """读 prompts/exam_grader.json 里的固定 framing（role / output_format / banned_patterns）。"""
    return load_prompt("exam_grader")


def build_prompt(rubric, candidate):
    # type: (Dict[str, Any], Dict[str, Any]) -> str
    """
    构造 LLM 评审 prompt。

    framing（角色 + 输出格式约束）来自 prompts/exam_grader.json，rubric / candidate
    数据在运行时拼接进来。

    candidate dict 字段（全部可选，缺失视为空）：
      - candidate_label:   str  给 AI 看的候选人代号（建议用 talent_id 而非真名以减少偏见）
      - exam_sent_at:      str  题目发出时间（ISO 字符串）
      - submitted_at:      str  候选人首次提交时间
      - hours_used:        float 用时小时数（若已算出）
      - email_body:        str  邮件正文（包含候选人对延迟的解释等）
      - code_files:        list[{path, content}]
      - doc_files:         list[{path, content}]
      - output_files:      list[{path, content}]   通常只放预览，不必整文件
      - extra_context:     str  其它给 AI 的背景信息
    """
    framing = _load_framing()
    sections = []

    sections.append("# 你的角色")
    sections.append(framing.get("role_system", ""))

    sections.append("\n# 题目背景")
    sections.append("题目: {}".format(rubric.get("exam_title", "(unspecified)")))
    sections.append("题目摘要: {}".format(rubric.get("exam_summary", "")))
    sections.append("题目要求的提交物:")
    for x in rubric.get("exam_required_outputs", []):
        sections.append("  - {}".format(x))

    sections.append("\n# 评审硬约束（必须严格遵守）")
    for line in rubric.get("ai_reviewer_instructions", []):
        sections.append("  - {}".format(line))

    sections.append("\n# Rubric（评分细则，必须严格按这里打分）")
    sections.append("```json")
    sections.append(json.dumps({
        "dimensions":      rubric.get("dimensions", []),
        "time_modifier":   rubric.get("time_modifier", {}),
        "bonus_items":     rubric.get("bonus_items", []),
        "penalties":       rubric.get("penalties", []),
        "must_haves":      rubric.get("must_haves", []),
        "passing_hint":    rubric.get("passing_hint"),
    }, ensure_ascii=False, indent=2))
    sections.append("```")

    sections.append("\n# 候选人提交")
    sections.append("候选人代号: {}".format(candidate.get("candidate_label", "(unknown)")))
    sections.append("题目发出时间: {}".format(candidate.get("exam_sent_at", "(unknown)")))
    sections.append("首次提交时间: {}".format(candidate.get("submitted_at", "(unknown)")))
    if candidate.get("hours_used") is not None:
        sections.append("用时小时数: {:.1f}".format(float(candidate["hours_used"])))
    if candidate.get("email_body"):
        sections.append("\n[邮件正文，可能含候选人对延迟的说明]")
        sections.append(_truncate(candidate["email_body"], 3000))
    if candidate.get("extra_context"):
        sections.append("\n[额外背景]")
        sections.append(_truncate(candidate["extra_context"], 2000))

    sections.append("\n" + _format_files_block(candidate.get("code_files"),   "代码文件"))
    sections.append(_format_files_block(candidate.get("doc_files"),    "说明文档"))
    sections.append(_format_files_block(candidate.get("output_files"), "输出文件预览"))

    sections.append("\n# 输出格式")
    sections.append(framing.get("output_format_note", ""))
    sections.append("```json")
    sections.append(json.dumps(rubric.get("output_schema", {}), ensure_ascii=False, indent=2))
    sections.append("```")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# 3. LLM 调用
# ---------------------------------------------------------------------------

from lib.dashscope_client import (
    LLMError,
    chat_simple_prompt as _chat_simple_prompt,
)


def _call_dashscope(prompt, dashscope_cfg=None, timeout=None):
    # type: (str, Optional[Dict[str, Any]], Optional[int]) -> Tuple[str, Dict[str, Any]]
    """向后兼容旧签名；temperature / timeout / retries 默认从 prompts/exam_grader.json 读。"""
    framing = _load_framing()
    return _chat_simple_prompt(
        prompt,
        temperature=float(framing.get("temperature", 0.0)),
        timeout=int(timeout if timeout is not None else framing.get("timeout_sec", 90)),
        retries=int(framing.get("retries", 2)),
        dashscope_cfg=dashscope_cfg,
    )


# ---------------------------------------------------------------------------
# 4. 响应解析、归一化、护栏
# ---------------------------------------------------------------------------

# 强禁词由 prompts/exam_grader.json 的 banned_patterns 提供（v3.5 起统一）。
def _get_banned_patterns():
    # type: () -> List[str]
    return list(_load_framing().get("banned_patterns") or [])


def _strip_banned(text):
    # type: (str) -> str
    if not text:
        return text
    out = text
    for pat in _get_banned_patterns():
        out = re.sub(pat, "[已按规则剥离结论性表述]", out, flags=re.IGNORECASE)
    return out


def _strip_code_fences(content):
    # type: (str) -> str
    s = content.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _safe_int(v, lo=None, hi=None, default=0):
    try:
        n = int(round(float(v)))
    except Exception:
        return default
    if lo is not None and n < lo:
        n = lo
    if hi is not None and n > hi:
        n = hi
    return n


def _apply_logic_aggregation(rubric, parsed):
    # type: (Dict[str, Any], Dict[str, Any]) -> None
    """
    复核 logic_correctness 维度：根据 checklist 分数重新计算总分，
    并应用 hard_caps，避免 LLM 自己给出与 checklist 不匹配的总分。
    """
    logic_dim = None
    for d in rubric.get("dimensions", []):
        if d.get("key") == "logic_correctness":
            logic_dim = d
            break
    if not logic_dim or logic_dim.get("scoring_mode") != "checklist":
        return

    checklist_def = {item["key"]: item for item in logic_dim.get("checklist", [])}
    weight = int(logic_dim["weight"])
    max_per_item = sum(int(item["max"]) for item in logic_dim["checklist"])

    raw_scores = parsed.get("logic_checklist_scores") or []
    norm_scores = []
    sum_score = 0
    by_key = {}
    for s in raw_scores:
        k = s.get("key")
        if k not in checklist_def:
            continue
        score = _safe_int(s.get("score", 0), lo=0, hi=int(checklist_def[k]["max"]))
        norm_scores.append({
            "key": k,
            "label": checklist_def[k]["label"],
            "score": score,
            "max": int(checklist_def[k]["max"]),
            "reason": str(s.get("reason", ""))[:1000],
        })
        by_key[k] = score
        sum_score += score
    parsed["logic_checklist_scores"] = norm_scores

    aggregated = int(round(sum_score / max_per_item * weight)) if max_per_item else 0

    capped_reason = None
    for cap in logic_dim.get("hard_caps", []):
        cond = cap.get("when", "")
        try:
            ok = _eval_cap_condition(cond, by_key)
        except Exception:
            ok = False
        if ok and aggregated > int(cap["cap"]):
            aggregated = int(cap["cap"])
            capped_reason = cap.get("reason")

    for d in parsed.get("dimension_scores", []):
        if d.get("key") == "logic_correctness":
            d["score"] = aggregated
            d["max"] = weight
            if capped_reason:
                d["reason"] = (d.get("reason") or "") + " ｜ [hard_cap 触发] " + capped_reason
            break
    else:
        parsed.setdefault("dimension_scores", []).append({
            "key": "logic_correctness",
            "label": logic_dim["label"],
            "score": aggregated,
            "max": weight,
            "reason": ("[hard_cap 触发] " + capped_reason) if capped_reason else "",
        })


def _eval_cap_condition(cond, by_key):
    # type: (str, Dict[str, int]) -> bool
    """
    极小化、白名单的条件求值：只支持
        L_KEY {==,!=,>=,<=,>,<} INT  用 AND/OR 连接
    例如: "L1_three_layer_state == 0 OR L4_cancel_routing == 0"
    """
    if not cond:
        return False
    expr = cond
    for k, v in by_key.items():
        expr = re.sub(r"\b{}\b".format(re.escape(k)), str(int(v)), expr)
    expr = expr.replace("AND", "and").replace("OR", "or")
    if not re.match(r"^[\d\s\(\)<>=!andort]+$", expr):
        return False
    try:
        return bool(eval(expr, {"__builtins__": {}}, {}))
    except Exception:
        return False


def _normalize_dimension_scores(rubric, parsed):
    # type: (Dict[str, Any], Dict[str, Any]) -> None
    by_key = {d["key"]: d for d in rubric.get("dimensions", [])}
    out = []
    seen = set()
    for d in parsed.get("dimension_scores") or []:
        k = d.get("key")
        if k not in by_key or k in seen:
            continue
        seen.add(k)
        weight = int(by_key[k]["weight"])
        out.append({
            "key": k,
            "label": by_key[k]["label"],
            "score": _safe_int(d.get("score", 0), lo=0, hi=weight),
            "max": weight,
            "reason": _strip_banned(str(d.get("reason", ""))[:1500]),
        })
    for k, dim in by_key.items():
        if k not in seen:
            out.append({
                "key": k,
                "label": dim["label"],
                "score": 0,
                "max": int(dim["weight"]),
                "reason": "[LLM 未对该维度给出评分，按 0 处理，需人工复核]",
            })
    parsed["dimension_scores"] = out


def _normalize_bonus_and_penalty(rubric, parsed):
    # type: (Dict[str, Any], Dict[str, Any]) -> None
    bdef = {x["key"]: x for x in rubric.get("bonus_items", [])}
    pdef = {x["key"]: x for x in rubric.get("penalties", [])}

    bout = []
    for b in parsed.get("bonus_scores") or []:
        k = b.get("key")
        if k not in bdef:
            continue
        bout.append({
            "key": k,
            "label": bdef[k]["label"],
            "score": _safe_int(b.get("score", 0), lo=0, hi=int(bdef[k]["max"])),
            "reason": _strip_banned(str(b.get("reason", ""))[:1000]),
        })
    parsed["bonus_scores"] = bout

    pout = []
    for p in parsed.get("penalty_scores") or []:
        k = p.get("key")
        if k not in pdef:
            continue
        pout.append({
            "key": k,
            "label": pdef[k]["label"],
            "score": _safe_int(p.get("score", 0), lo=0, hi=int(pdef[k]["max_deduct"])),
            "reason": _strip_banned(str(p.get("reason", ""))[:1000]),
        })
    parsed["penalty_scores"] = pout


def _normalize_time_modifier(rubric, parsed):
    # type: (Dict[str, Any], Dict[str, Any]) -> None
    tm = rubric.get("time_modifier", {})
    lo = int(tm.get("min", -5))
    hi = int(tm.get("max", 5))
    parsed["time_modifier"] = _safe_int(parsed.get("time_modifier", 0), lo=lo, hi=hi)


def _compute_totals(parsed):
    # type: (Dict[str, Any]) -> None
    main = sum(int(d["score"]) for d in parsed.get("dimension_scores", []))
    bonus = sum(int(b["score"]) for b in parsed.get("bonus_scores", []))
    penalty = sum(int(p["score"]) for p in parsed.get("penalty_scores", []))
    parsed["main_score"] = max(0, min(100, main))
    parsed["bonus_total"] = bonus
    parsed["penalty_total"] = penalty
    final = parsed["main_score"] + bonus - penalty
    parsed["final_score_for_reference"] = max(0, min(115, final))


def _normalize_lists(parsed):
    # type: (Dict[str, Any]) -> None
    for k in ("highlights", "risks", "next_steps_for_boss"):
        v = parsed.get(k) or []
        if not isinstance(v, list):
            v = [str(v)]
        parsed[k] = [_strip_banned(str(x))[:500] for x in v if str(x).strip()]
    if "summary" in parsed:
        parsed["summary"] = _strip_banned(str(parsed["summary"]))[:200]
    else:
        parsed["summary"] = ""


def parse_response(raw_content, rubric):
    # type: (str, Dict[str, Any]) -> Dict[str, Any]
    """
    把 LLM 原始输出解析为结构化结果，并应用所有护栏：
      - logic_correctness 按 checklist 重算 + hard_caps
      - 维度补齐（缺失维度按 0 + 标记 risk）
      - 数值越界裁剪
      - 强禁词剥离
      - 总分重算
    任何解析失败都会抛 LLMError。
    """
    cleaned = _strip_code_fences(raw_content or "")
    if not cleaned:
        raise LLMError("LLM 返回为空")
    try:
        parsed = json.loads(cleaned)
    except ValueError as e:
        m = re.search(r"\{[\s\S]*\}", cleaned)
        if not m:
            raise LLMError("LLM 输出非 JSON：{}".format(str(e)[:120]))
        try:
            parsed = json.loads(m.group(0))
        except ValueError as e2:
            raise LLMError("LLM 输出 JSON 解析失败：{}".format(str(e2)[:120]))

    if not isinstance(parsed, dict):
        raise LLMError("LLM 输出顶层不是对象")

    _normalize_dimension_scores(rubric, parsed)
    _apply_logic_aggregation(rubric, parsed)
    _normalize_bonus_and_penalty(rubric, parsed)
    _normalize_time_modifier(rubric, parsed)
    _normalize_lists(parsed)
    _compute_totals(parsed)
    return parsed


# ---------------------------------------------------------------------------
# 5. 对外入口
# ---------------------------------------------------------------------------

def review_submission(candidate, rubric_path=None, dashscope_cfg=None):
    # type: (Dict[str, Any], Optional[str], Optional[Dict[str, Any]]) -> Dict[str, Any]
    """
    主入口：加载 rubric → 构造 prompt → 调 LLM → 解析护栏 → 返回结构化结果。

    任何错误都不会抛出到调用方，统一以 {"_error": ..., "_meta": ...} 的形式返回，
    方便上游（daily_exam_review / 命令行 / 飞书报告）做容错。
    """
    meta = {"rubric_path": rubric_path or DEFAULT_RUBRIC_PATH}
    try:
        rubric = load_rubric(rubric_path)
    except RubricError as e:
        return {"_error": "rubric_error", "_message": str(e), "_meta": meta}

    meta["rubric_version"] = rubric.get("version")

    try:
        prompt = build_prompt(rubric, candidate)
        meta["prompt_chars"] = len(prompt)
    except Exception as e:
        return {"_error": "prompt_error", "_message": str(e)[:200], "_meta": meta}

    try:
        raw, llm_meta = _call_dashscope(prompt, dashscope_cfg=dashscope_cfg)
        meta.update(llm_meta)
    except LLMError as e:
        return {"_error": "llm_error", "_message": str(e), "_meta": meta}

    try:
        result = parse_response(raw, rubric)
    except LLMError as e:
        return {
            "_error": "parse_error",
            "_message": str(e),
            "_meta": meta,
            "_raw_preview": (raw or "")[:1000],
        }

    result["_meta"] = meta
    return result


# ---------------------------------------------------------------------------
# 6. 飞书 / CLI 友好的格式化函数
# ---------------------------------------------------------------------------

def format_report_for_feishu(result, candidate_label=""):
    # type: (Dict[str, Any], str) -> str
    """
    把 review_submission 的结果格式化成给老板看的飞书纯文本报告。
    保持“仅供参考、最终由老板决定”的硬性提示。
    """
    if result.get("_error"):
        return (
            "【AI 笔试评审 · 失败】{}\n"
            "原因: {} ({})\n"
            "请人工评审或查看日志。"
        ).format(candidate_label or "", result.get("_error"), result.get("_message", ""))

    meta = result.get("_meta", {})
    lines = []
    lines.append("AI 笔试评审建议（{} · 仅供参考，最终由老板决定）".format(
        meta.get("rubric_version", "rubric")
    ))
    if candidate_label:
        lines.append("候选人: {}".format(candidate_label))
    lines.append(
        "主分: {}/100   加分: +{}   扣分: -{}   参考总分: {}/115".format(
            result.get("main_score", 0),
            result.get("bonus_total", 0),
            result.get("penalty_total", 0),
            result.get("final_score_for_reference", 0),
        )
    )
    tm = result.get("time_modifier", 0)
    lines.append("完成时间调节项（独立，不并入主分）: {:+d}".format(int(tm)))

    lines.append("")
    lines.append("== 维度得分 ==")
    for d in result.get("dimension_scores", []):
        lines.append("· {} ({}/{})".format(d["label"], d["score"], d["max"]))
        if d.get("reason"):
            lines.append("  理由: {}".format(d["reason"]))

    chk = result.get("logic_checklist_scores") or []
    if chk:
        lines.append("")
        lines.append("== 逻辑 checklist 明细 ==")
        for c in chk:
            lines.append("  - {} {}/{}: {}".format(
                c.get("key", ""), c.get("score", 0), c.get("max", 0), c.get("reason", "")
            ))

    bs = result.get("bonus_scores") or []
    if bs:
        lines.append("")
        lines.append("== 加分项 ==")
        for b in bs:
            lines.append("  + {} +{}: {}".format(b["label"], b["score"], b.get("reason", "")))

    ps = result.get("penalty_scores") or []
    if ps:
        lines.append("")
        lines.append("== 扣分项 ==")
        for p in ps:
            lines.append("  - {} -{}: {}".format(p["label"], p["score"], p.get("reason", "")))

    if result.get("highlights"):
        lines.append("")
        lines.append("亮点:")
        for h in result["highlights"]:
            lines.append("  · {}".format(h))

    if result.get("risks"):
        lines.append("")
        lines.append("风险 / 不确定项:")
        for r in result["risks"]:
            lines.append("  · {}".format(r))

    if result.get("summary"):
        lines.append("")
        lines.append("AI 总结: {}".format(result["summary"]))

    if result.get("next_steps_for_boss"):
        lines.append("")
        lines.append("可执行下一步建议（仅供参考）:")
        for s in result["next_steps_for_boss"]:
            lines.append("  → {}".format(s))

    lines.append("")
    lines.append("⚠️ 本报告由 AI 生成，仅供参考。最终通过/不通过请老板使用 cmd_exam_result.py 决定。")
    return "\n".join(lines)
