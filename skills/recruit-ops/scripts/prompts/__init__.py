#!/usr/bin/env python3
"""prompts/ —— v3.4 集中管理 LLM prompt 文件。

【为什么】
  在 v3.3 之前 prompts 散落在三处：
    - inbox/analyzer.py 把 system/user template 写死在 .py 里
    - followup/followup_analyzer.py 走 exam_files/followup_prompt.json
    - auto_reject 模块也曾内置 prompt
  v3.4 统一放到 scripts/prompts/<name>.json，由本模块的 load_prompt()
  懒加载 + 缓存。这样：
    1. 改 prompt 不需要改 .py 文件
    2. 版本号 / 模型 / temperature 集中可见
    3. 便于做 prompt diff review

【prompt JSON schema（两类）】
  A. 邮件分类类（system + user_template，对应 inbox prompts）：
    {
      "version": "inbox.general.vXX.YYYY-MM-DD",
      "purpose": "...",
      "model_hint": "qwen3-max",
      "temperature": 0.1,
      "max_body_chars": 2500,
      "system": "...完整 system prompt...",
      "user_template": "...含 {placeholder} 的 user 模板...",
      "output_fields": ["intent", "summary", "urgency", "details"],
      "has_draft": false
    }

    has_draft=true 表示 LLM 应额外输出 draft 字段；caller 应当跑 _scrub_draft
    再持久化。

  B. Framing-only 类（v3.5 起，对应 lib/exam_grader 之类的动态 prompt 调用方）：
    {
      "version": "exam_grader.vXX.YYYY-MM-DD",
      "purpose": "...",
      "model_hint": "qwen3-max",
      "temperature": 0.0,
      "role_system": "...",
      "output_format_note": "...",
      "banned_patterns": [...]
    }

    本模块只强制 version 一个字段；A/B 各类自有字段由调用方按需取。
"""
from __future__ import print_function

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


_HERE = Path(__file__).resolve().parent
_CACHE = {}  # type: Dict[str, Dict[str, Any]]


class PromptNotFoundError(Exception):
    pass


class PromptInvalidError(Exception):
    pass


_REQUIRED_KEYS = ("version",)


def load_prompt(name, force_reload=False):
    # type: (str, bool) -> Dict[str, Any]
    """按名称加载 prompts/<name>.json，缓存结果。

    Args:
        name:        prompt 文件名（不含 .json）
        force_reload: True 时跳过缓存（测试 / 调试用）

    Raises:
        PromptNotFoundError: 文件不存在
        PromptInvalidError:  缺必填字段或 JSON 解析失败
    """
    if not force_reload and name in _CACHE:
        return _CACHE[name]

    path = _HERE / "{}.json".format(name)
    if not path.is_file():
        raise PromptNotFoundError(
            "prompt {!r} 不存在；预期路径 {}".format(name, path))

    try:
        with open(str(path), "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise PromptInvalidError(
            "prompt {!r} JSON 解析失败 path={}: {}".format(name, path, e))

    missing = [k for k in _REQUIRED_KEYS if not data.get(k)]
    if missing:
        raise PromptInvalidError(
            "prompt {!r} 缺必填字段: {}".format(name, missing))

    _CACHE[name] = data
    return data


def list_prompts():
    # type: () -> list
    """返回 prompts/ 目录下所有 .json 文件名（不含扩展名），按字母排序。"""
    out = []
    for entry in sorted(os.listdir(str(_HERE))):
        if entry.endswith(".json"):
            out.append(entry[:-len(".json")])
    return out


def clear_cache():
    """测试用：清空内部缓存。"""
    _CACHE.clear()
