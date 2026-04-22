#!/usr/bin/env python3
"""统一 DashScope HTTP 客户端。

历史上 `exam_ai_reviewer.py`（v3.5 → lib/exam_grader） /
`daily_exam_review.py._llm_analyze_reply`（v3.5 已删除） /
`followup_analyzer.py`（v3.4 已删除）各自实现了一次 urllib + json + 错误处理，
超时不一致，也都没有重试。这里收敛成一个入口。

特点：
- 默认走 `lib.http_retry.call_with_retry`，对网络瞬态错误重试 2 次。
- 超时可配置，默认 60s。
- 区分 `LLMTransientError`（可重试） / `LLMResponseError`（业务错，不重试）。
- 日志统一带 `[dashscope]` 前缀。
"""
from __future__ import print_function

import json
import sys
import urllib.error as _urlerr
import urllib.request as _req
from socket import timeout as _SocketTimeout
from typing import Any, Dict, List, Optional, Tuple

from lib import config as _cfg
from lib.http_retry import call_with_retry


DEFAULT_URL = "https://coding.dashscope.aliyuncs.com/v1/chat/completions"
DEFAULT_MODEL = "qwen3-max-2026-01-23"


class LLMError(Exception):
    """通用 LLM 错误（保持向后兼容旧代码 import LLMError）。"""


class LLMTransientError(LLMError):
    """网络瞬态错误，可重试。"""


class LLMResponseError(LLMError):
    """LLM 返回了响应但结构不可解析，不要重试。"""


_RETRIABLE = (LLMTransientError, _urlerr.URLError, _SocketTimeout, TimeoutError)


def _do_request(url, payload_bytes, api_key, timeout):
    # type: (str, bytes, str, float) -> Dict[str, Any]
    request = _req.Request(
        url,
        data=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer {}".format(api_key),
        },
    )
    try:
        with _req.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except _urlerr.HTTPError as e:
        if 500 <= e.code < 600:
            raise LLMTransientError("DashScope HTTP {}: {}".format(e.code, e.reason))
        raise LLMResponseError("DashScope HTTP {}: {}".format(e.code, e.reason))
    except (_urlerr.URLError, _SocketTimeout, TimeoutError) as e:
        raise LLMTransientError("DashScope 网络错误: {}".format(str(e)[:200]))
    except Exception as e:
        raise LLMResponseError("DashScope 请求异常: {}".format(str(e)[:200]))


def chat_completion(messages,
                    temperature=0.0,
                    timeout=60,
                    retries=2,
                    dashscope_cfg=None,
                    extra_payload=None):
    # type: (List[Dict[str, str]], float, float, int, Optional[Dict[str, Any]], Optional[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]
    """对 DashScope 的 chat/completions 接口发起一次调用。

    Returns: (content_str, meta_dict)
    Raises:
        LLMError 子类，全部失败后抛出。
    """
    ds = dashscope_cfg or _cfg.get("dashscope") or {}
    api_key = ds.get("api_key", "")
    if not api_key:
        raise LLMError("DashScope api_key 未配置")

    url = ds.get("url", DEFAULT_URL)
    model = ds.get("model", DEFAULT_MODEL)
    payload = {
        "model": model,
        "messages": messages,
        "temperature": float(temperature),
    }
    if extra_payload:
        payload.update(extra_payload)
    payload_bytes = json.dumps(payload).encode("utf-8")

    raw = call_with_retry(
        _do_request,
        args=(url, payload_bytes, api_key, timeout),
        retries=retries,
        retriable=_RETRIABLE,
        label="dashscope",
    )

    try:
        content = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LLMResponseError("DashScope 响应结构异常: {}".format(str(raw)[:300]))

    if isinstance(content, str):
        content = content.strip()
    else:
        content = json.dumps(content, ensure_ascii=False)

    meta = {
        "model": model,
        "usage": raw.get("usage", {}),
    }
    return content, meta


def chat_simple_prompt(prompt, temperature=0.0, timeout=60, retries=2, dashscope_cfg=None):
    # type: (str, float, float, int, Optional[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]
    """一段裸 prompt 当 user message 发送（兼容老的 _call_dashscope 单参数风格）。"""
    return chat_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        timeout=timeout,
        retries=retries,
        dashscope_cfg=dashscope_cfg,
    )
