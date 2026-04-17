#!/usr/bin/env python3
"""
LLM 意图分析模块：调用 DashScope 分析候选人邮件回复意图。
从 daily_exam_review.py 拆分出的独立模块。
"""
import json
import re
import sys
import os

import config as _cfg
from typing import Any, Dict


def analyze_reply(email_body, round_label="一面"):
    # type: (str, str) -> Dict[str, Any]
    """
    调用 DashScope LLM 分析候选人邮件意图。
    返回 {"intent": "confirm|reschedule|request_online|defer_until_shanghai|unknown",
           "new_time": str|None, "summary": str}
    """
    text = (email_body or "").strip()

    # 规则优先：快速匹配二面暂缓意图
    if round_label == "二面":
        wants_online = any(x in text for x in ["线上", "视频面试", "腾讯会议", "会议链接"])
        defer_markers = [
            "不在上海", "暂时不在上海", "之后再约", "以后再约", "回上海再约",
            "等我回上海", "不在国内", "在美国", "在国外", "在日本", "在英国",
        ]
        if any(x in text for x in defer_markers) and not wants_online:
            return {"intent": "defer_until_shanghai", "new_time": None,
                    "summary": "候选人暂时不在国内/上海，之后再约"}

    ds = _cfg.get("dashscope")
    api_key = ds.get("api_key", "")
    if not api_key:
        return {"intent": "unknown", "new_time": None, "summary": "（LLM未配置）"}

    prompt = (
        "你是一个招聘助手，请分析以下候选人邮件回复，判断候选人对{}邀请的意图。\n\n"
        "邮件内容：\n{}\n\n"
        "请用JSON格式回复，包含以下字段：\n"
        "- intent: 只能是 confirm / reschedule / request_online / defer_until_shanghai / unknown\n"
        "- new_time: 若候选人提出了新时间，填写时间字符串，否则填 null\n"
        "- summary: 一句话总结（中文，20字以内）\n\n"
        "只返回 JSON，不要其他内容。"
    ).format(round_label, text[:1500])

    try:
        import urllib.request as _req
        payload = json.dumps({
            "model": ds.get("model", "qwen3-max-2026-01-23"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        }).encode("utf-8")
        request = _req.Request(
            ds.get("url", "https://coding.dashscope.aliyuncs.com/v1/chat/completions"),
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer {}".format(api_key),
            },
        )
        with _req.urlopen(request, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        content = result["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        parsed = json.loads(content)
        return {
            "intent": parsed.get("intent", "unknown"),
            "new_time": parsed.get("new_time"),
            "summary": parsed.get("summary", ""),
        }
    except Exception as e:
        return {"intent": "unknown", "new_time": None,
                "summary": "LLM分析失败: {}".format(str(e)[:50])}
