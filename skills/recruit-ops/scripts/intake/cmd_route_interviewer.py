#!/usr/bin/env python3
"""intake/cmd_route_interviewer.py —— v3.5.7 一面派单路由（atomic, 纯查询）。

【职责（只干这一件）】
  根据 talents.{education, has_cpp} + lib.config['feishu']['interviewer_*_open_id']，
  按 §5.11 的 cpp_first 优先级算出该走哪个/哪些面试官，输出 JSON。
  zero side effect：不写 DB、不发飞书、不发邮件、不调 LLM。

【路由规则（cpp_first，写进 docs/AGENT_RULES.md §5.11 与本文件保持一致）】
  ┌────────────┬────────────────────┬──────────────┬──────────┐
  │ has_cpp    │ education          │ role         │ open_id  │
  ├────────────┼────────────────────┼──────────────┼──────────┤
  │ true       │ any                │ cpp          │ cpp     │
  │ false/null │ 硕士/博士           │ master       │ master   │
  │ false/null │ 本科                │ bachelor     │ bachelor │
  │ false/null │ 其他/null/未识别    │ ambiguous    │ -        │
  │ true       │ null               │ cpp          │ cpp     │
  └────────────┴────────────────────┴──────────────┴──────────┘
  ambiguous=true 时 caller（agent / chain）应转 ASK_HR 分支：
    feishu.cmd_notify --to hr 让 HR 手动指派 open_id，再回到本 chain。

  config_error=true 时同样不做派单（fail closed）：
    本 CLI 的 open_id 来源是 lib.config，缺失（包括占位符
    "ou_PLACEHOLDER_*"）会被识别为 "未配齐"，输出里写 config_error=true，
    caller 应推飞书让运维补齐配置后重试。

【输出 schema（--json）】
  {
    "ok": bool,
    "talent_id": str,
    "education": str|null,
    "has_cpp":  bool|null,
    "interviewer_roles":     [str],   # 例如 ["cpp"]，叠加规则可扩展
    "interviewer_open_ids":  [str],   # 与 roles 一一对应
    "ambiguous": bool,
    "ambiguous_reason": str|null,    # ambiguous 时给 HR 的「为什么」
    "config_error": bool,
    "config_error_detail": str|null,  # 哪个 role 缺 open_id
    "fallback_used": bool,            # v3.5.7 暂不允许 fallback，恒 false
  }

【调用】
  PYTHONPATH=scripts python3 -m intake.cmd_route_interviewer \
      --talent-id t_xxx --json
"""
from __future__ import print_function

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Tuple

from lib.cli_wrapper import UserInputError


_PLACEHOLDER_PREFIX = "ou_PLACEHOLDER_"

# 学历归一化：把 LLM / HR 输入的各种写法映射到 {graduate, bachelor, unknown}
_GRADUATE_KEYWORDS = ("硕士", "硕", "研究生", "研", "博士", "博", "master", "phd", "doctor")
_BACHELOR_KEYWORDS = ("本科", "本", "学士", "bachelor", "undergrad")


def _normalize_education(edu):
    # type: (Optional[str]) -> str
    """返回 'graduate' / 'bachelor' / 'unknown'。"""
    if not edu:
        return "unknown"
    s = str(edu).strip().lower()
    if not s:
        return "unknown"
    for kw in _GRADUATE_KEYWORDS:
        if kw in s:
            return "graduate"
    for kw in _BACHELOR_KEYWORDS:
        if kw in s:
            return "bachelor"
    return "unknown"


def _decide_roles(education_norm, has_cpp):
    # type: (str, Optional[bool]) -> Tuple[List[str], bool, Optional[str]]
    """实施 §5.11 路由表，返回 (roles, ambiguous, ambiguous_reason)。

    cpp_first 优先：has_cpp=True → ['cpp']，不管学历。
    """
    if has_cpp is True:
        return (["cpp"], False, None)
    # has_cpp 为 False / None：按学历分
    if education_norm == "graduate":
        return (["master"], False, None)
    if education_norm == "bachelor":
        return (["bachelor"], False, None)
    # 其他：ambiguous，让 HR 手动派
    reason = (
        "无法自动派单：has_cpp={} 且学历不在 (本科/硕士/博士) 范围内。"
        "请 HR 手动指定面试官 open_id 后重启 §5.11 chain。"
    ).format("null" if has_cpp is None else has_cpp)
    return ([], True, reason)


def _resolve_open_ids(roles):
    # type: (List[str]) -> Tuple[List[str], Optional[str]]
    """role → open_id 查表（lib.config['feishu']['interviewer_*_open_id']）。

    返回 (open_ids, config_error_detail)。任何 role 取不到真实 open_id（包括
    占位符 ou_PLACEHOLDER_*）→ config_error_detail 非 None，open_ids 为空。
    """
    from lib import config as _cfg
    feishu = _cfg.get("feishu") or {}
    open_ids = []
    for role in roles:
        key = "interviewer_{}_open_id".format(role)
        oid = (feishu.get(key, "") or "").strip()
        if not oid:
            return ([], "未配置 {}（请设置 FEISHU_INTERVIEWER_{}_OPEN_ID 环境变量"
                        " 或 openclaw config 里的 interviewer{}OpenId）".format(
                            key, role.upper(), role.capitalize()))
        if oid.startswith(_PLACEHOLDER_PREFIX):
            return ([], "{} 仍是占位符（{}），请用真实 open_id 替换".format(
                key, oid))
        open_ids.append(oid)
    return (open_ids, None)


def _load_talent_fields(talent_id):
    # type: (str) -> Dict[str, Any]
    """读 talents 的 education + has_cpp + 基本身份字段。

    走 talent_db.get_one（生产 DB）或 测试用内存 mock。
    """
    from lib import talent_db as _tdb
    # 测试用 _InMemoryTdb 也实现了 get_one；生产路径走真实 PG
    cand = None
    try:
        cand = _tdb.get_one(talent_id)
    except Exception as e:
        # 读 DB 失败：直接抛，由 cli_wrapper 包装成飞书告警
        raise RuntimeError("查询 talents.{} 失败: {}".format(talent_id, e))
    if not cand:
        raise UserInputError("候选人 {} 不存在".format(talent_id))
    return {
        "talent_id":      talent_id,
        "candidate_name": cand.get("candidate_name"),
        "education":      cand.get("education"),
        "has_cpp":        cand.get("has_cpp"),  # 三态 True/False/None
    }


def _build_payload(talent_id):
    # type: (str) -> Dict[str, Any]
    fields = _load_talent_fields(talent_id)
    edu_norm = _normalize_education(fields.get("education"))
    roles, ambiguous, ambiguous_reason = _decide_roles(edu_norm, fields.get("has_cpp"))

    open_ids = []
    config_error = False
    config_error_detail = None
    if not ambiguous:
        open_ids, err = _resolve_open_ids(roles)
        if err:
            config_error = True
            config_error_detail = err
            open_ids = []  # fail closed

    return {
        "ok":             not ambiguous and not config_error,
        "talent_id":      talent_id,
        "candidate_name": fields.get("candidate_name"),
        "education":      fields.get("education"),
        "education_normalized": edu_norm,
        "has_cpp":        fields.get("has_cpp"),
        "interviewer_roles":    roles if not ambiguous else [],
        "interviewer_open_ids": open_ids,
        "ambiguous":            ambiguous,
        "ambiguous_reason":     ambiguous_reason,
        "config_error":         config_error,
        "config_error_detail":  config_error_detail,
        "fallback_used":        False,
    }


def _build_parser():
    p = argparse.ArgumentParser(
        prog="intake.cmd_route_interviewer",
        description="一面派单路由：根据 talents.{education,has_cpp} 输出面试官 open_id（atomic, 纯查询）",
    )
    p.add_argument("--talent-id", required=True)
    p.add_argument("--json", action="store_true",
                   help="JSON 输出（推荐 chain 调用时使用）")
    return p


def _emit(args, payload):
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        lines = [
            "[cmd_route_interviewer] talent_id={} name={}".format(
                payload["talent_id"], payload.get("candidate_name") or ""),
            "  education={} (normalized={}) has_cpp={}".format(
                payload.get("education"),
                payload.get("education_normalized"),
                payload.get("has_cpp")),
        ]
        if payload["ambiguous"]:
            lines.append("  ambiguous=True reason={}".format(payload["ambiguous_reason"]))
        elif payload["config_error"]:
            lines.append("  config_error=True detail={}".format(payload["config_error_detail"]))
        else:
            lines.append("  → roles={} open_ids={}".format(
                payload["interviewer_roles"], payload["interviewer_open_ids"]))
        print("\n".join(lines))


def main(argv=None):
    args = _build_parser().parse_args(argv)
    talent_id = (args.talent_id or "").strip()
    if not talent_id:
        raise UserInputError("--talent-id 不能为空")
    payload = _build_payload(talent_id)
    _emit(args, payload)
    # 退出码：ok=0；ambiguous / config_error 都用 0（chain 仍要消费 JSON）
    # 真异常（DB 挂、talent 不存在）已由 UserInputError / RuntimeError 抛出
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except UserInputError as e:
        print("[cmd_route_interviewer] INPUT ERROR: {}".format(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print("[cmd_route_interviewer] CRASH: {}".format(e), file=sys.stderr)
        sys.exit(1)
