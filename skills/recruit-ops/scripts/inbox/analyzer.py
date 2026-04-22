#!/usr/bin/env python3
"""inbox/analyzer.py —— v3.4 候选人邮件意图分析（DashScope）。

【v3.4 升级】
  Stage-aware prompt 路由：
    - POST_OFFER_FOLLOWUP 阶段 → prompts/post_offer_followup.json
      （额外生成 `draft` 字段，并跑 _scrub_draft 安全过滤）
    - 其他所有 stage → prompts/inbox_general.json（仅归类，不出草稿）

  v3.6 (2026-04-27)：原 OFFER_HANDOFF 瞬时态已下线，合并入 POST_OFFER_FOLLOWUP；
  _FOLLOWUP_STAGES 只保留 POST_OFFER_FOLLOWUP。

  此前 followup_analyzer.analyze() 是单独的 LLM 模块；v3.4 把它的能力合并进
  inbox.analyzer，followup_scanner 在 Phase 3 被删除。

【输出 schema】
  {
    "intent": str,
    "summary": str,
    "need_boss_action": bool,
    "urgency": "low" | "medium" | "high",
    "details": dict,
    "draft": str | None,         # 仅 post_offer_followup prompt 才返回
    "_meta": {
        "prompt_name": str,
        "prompt_version": str,
        "model": str
    }
  }
"""
from __future__ import print_function

import json
import re
import sys
from typing import Any, Dict, Optional

from lib import config as _cfg
import prompts as _prompts


# ─── stage → prompt 路由 ──────────────────────────────────────────────────────

_FOLLOWUP_STAGES = frozenset({"POST_OFFER_FOLLOWUP"})


def _select_prompt_name(stage):
    # type: (Optional[str]) -> str
    if stage and stage in _FOLLOWUP_STAGES:
        return "post_offer_followup"
    return "inbox_general"


# ─── 解析工具 ─────────────────────────────────────────────────────────────────

def _strip_code_fence(text):
    # type: (str) -> str
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _coerce_intent(raw, valid_intents):
    s = (raw or "").strip().lower()
    return s if s in valid_intents else "other"


def _coerce_urgency(raw, valid_urgencies):
    s = (raw or "").strip().lower()
    return s if s in valid_urgencies else "low"


def _scrub_draft(draft, banned_phrases):
    # type: (str, list) -> str
    """剥掉草稿里出现的硬承诺措辞，加上明显标注让老板自己改。
    与旧 followup_analyzer 保持完全一致的语义。"""
    if not draft:
        return draft
    hit = False
    for phrase in banned_phrases or ():
        if phrase and phrase in draft:
            hit = True
            draft = draft.replace(phrase, "（待老板/HR 确认）")
    if hit:
        draft = (draft.rstrip() +
                 "\n\n[Hermes 提示] 本草稿中检测到承诺性措辞，已用占位符替换，请老板确认后再发送。")
    return draft


# ─── 主入口 ──────────────────────────────────────────────────────────────────

def analyze(candidate_name, stage, stage_label, subject, body, max_body=None):
    # type: (str, str, str, str, str, Optional[int]) -> Optional[Dict[str, Any]]
    """对单封 inbound 邮件做意图分析。失败返回 None。

    caller 应 None-safe：任何返回 None 的情况下，cmd_analyze 仍会把
    analyzed_at 标成当下（避免死循环），但 intent 保持 NULL。

    Args:
        candidate_name: 候选人姓名
        stage:          候选人 current_stage（用于 prompt 路由）
        stage_label:    stage 中文标签（仅用于通用 prompt 上下文）
        subject:        邮件主题
        body:           邮件正文
        max_body:       覆盖 prompt 默认的 max_body_chars
    """
    ds = _cfg.get("dashscope") or {}
    if not ds.get("api_key"):
        return None

    prompt_name = _select_prompt_name(stage)
    try:
        prompt = _prompts.load_prompt(prompt_name)
    except Exception as e:
        print("[inbox.analyzer] prompt {!r} 加载失败: {}".format(prompt_name, e),
              file=sys.stderr)
        return None

    body_max = max_body or int(prompt.get("max_body_chars") or 2500)
    body_trimmed = (body or "")[:body_max] or "(空)"
    user_msg = (prompt["user_template"]
                .replace("{candidate_name}", candidate_name or "(未知)")
                .replace("{stage}", stage or "(未知)")
                .replace("{stage_label}", stage_label or "")
                .replace("{subject}", subject or "(无主题)")
                .replace("{max_body_chars}", str(body_max))
                .replace("{body}", body_trimmed))
    temperature = float(prompt.get("temperature") or 0.1)

    try:
        from lib.dashscope_client import chat_completion
        content, _meta = chat_completion(
            messages=[
                {"role": "system", "content": prompt["system"]},
                {"role": "user",   "content": user_msg},
            ],
            temperature=temperature,
            timeout=30,
            retries=2,
            dashscope_cfg=ds,
        )
    except Exception as e:
        print("[inbox.analyzer] LLM 调用失败 prompt={}: {}".format(prompt_name, e),
              file=sys.stderr)
        return None

    try:
        parsed = json.loads(_strip_code_fence(content))
    except Exception as e:
        print("[inbox.analyzer] JSON 解析失败 prompt={} content={!r}: {}".format(
            prompt_name, (content or "")[:200], e), file=sys.stderr)
        return None

    valid_intents = frozenset(prompt.get("valid_intents") or ["other"])
    valid_urgencies = frozenset(prompt.get("valid_urgencies") or ["low", "medium", "high"])

    intent = _coerce_intent(parsed.get("intent"), valid_intents)
    summary = (parsed.get("summary") or "").strip()
    urgency = _coerce_urgency(parsed.get("urgency"), valid_urgencies)
    need_boss = bool(parsed.get("need_boss_action"))
    details = parsed.get("details") if isinstance(parsed.get("details"), dict) else {}

    # 兜底规则：某些 intent 天然需要老板介入
    _NEED_BOSS_INTENTS = {
        "reschedule_request", "request_online", "defer_until_shanghai",
        "question_boss", "decline_withdraw",
        "salary_negotiation", "benefits_question", "onboarding_date", "logistics",
    }
    if intent in _NEED_BOSS_INTENTS:
        need_boss = True

    if not summary:
        return None

    out = {
        "intent": intent,
        "summary": summary[:160],
        "need_boss_action": need_boss,
        "urgency": urgency,
        "details": details,
        "_meta": {
            "prompt_name": prompt_name,
            "prompt_version": prompt.get("version"),
            "model": ds.get("model"),
        },
    }

    # post_offer_followup 额外要 draft
    if prompt.get("has_draft"):
        draft = (parsed.get("draft") or "").strip()
        if draft:
            draft = _scrub_draft(draft, prompt.get("banned_phrases") or [])
            out["draft"] = draft

    return out
