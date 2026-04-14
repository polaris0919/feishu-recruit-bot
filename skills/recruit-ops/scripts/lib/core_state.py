#!/usr/bin/env python3
"""
核心状态模块：PostgreSQL 为唯一数据源。

统一调用风格：
    cand = load_candidate(talent_id)      # 读单个
    # ... 修改 cand ...
    save_candidate(talent_id, cand)       # 写单个

批量读（仅 cmd_status / cmd_search / daily_exam_review）：
    state = load_state()                  # {"candidates": {...}}
"""
import importlib
from datetime import datetime
from typing import Any, Dict, Optional, Set

STAGES = {
    "NEW",
    "ROUND1_SCHEDULING",
    "ROUND1_SCHEDULED",
    "ROUND1_DONE_PASS",
    "ROUND1_DONE_REJECT_KEEP",
    "ROUND1_DONE_REJECT_DELETE",
    "EXAM_SENT",
    "EXAM_REVIEWED",
    "WAIT_RETURN",
    "ROUND2_SCHEDULING",
    "ROUND2_SCHEDULED",
    "ROUND2_DONE_PENDING",
    "ROUND2_DONE_PASS",
    "ROUND2_DONE_REJECT_KEEP",
    "ROUND2_DONE_REJECT_DELETE",
    "OFFER_HANDOFF",
}

STAGE_LABELS = {
    "NEW": "新建",
    "ROUND1_SCHEDULING": "一面排期中",
    "ROUND1_SCHEDULED": "一面已安排",
    "ROUND1_DONE_PASS": "一面通过",
    "ROUND1_DONE_REJECT_KEEP": "一面未通过（保留）",
    "ROUND1_DONE_REJECT_DELETE": "一面未通过（移除）",
    "EXAM_SENT": "笔试已发送",
    "EXAM_REVIEWED": "笔试已审阅",
    "WAIT_RETURN": "待回国后再约",
    "ROUND2_SCHEDULING": "二面排期中",
    "ROUND2_SCHEDULED": "二面已确认",
    "ROUND2_DONE_PENDING": "二面结束待定",
    "ROUND2_DONE_PASS": "二面通过",
    "ROUND2_DONE_REJECT_KEEP": "二面未通过（保留）",
    "ROUND2_DONE_REJECT_DELETE": "二面未通过（移除）",
    "OFFER_HANDOFF": "等待发放 Offer",
}


def _tdb():
    # type: () -> Any
    """解析当前 talent_db 模块（每次从 sys.modules 取，便于测试注入 fake）。"""
    return importlib.import_module("talent_db")


def _db_enabled():
    # type: () -> bool
    return _tdb()._is_enabled()


def _require_db():
    # type: () -> None
    if not _db_enabled():
        raise RuntimeError("DB 未配置，请检查 talent-db-config.json")


def get_tdb():
    # type: () -> Any
    """返回 talent_db 模块（DB 启用时），否则返回 None。"""
    if _db_enabled():
        return _tdb()
    return None


def _now_iso():
    return datetime.now().replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S+08:00")


# ─── 单记录读写（推荐的统一调用风格）─────────────────────────────────────────

def load_candidate(talent_id):
    # type: (str) -> Optional[Dict[str, Any]]
    """从 DB 加载单个候选人，未找到返回 None。"""
    _require_db()
    return _tdb().get_one(talent_id)


def save_candidate(talent_id, cand):
    # type: (str, Dict[str, Any]) -> None
    """将单个候选人写入 DB。"""
    _require_db()
    _tdb().upsert_one(talent_id, cand)


# ─── 批量读（仅供 cmd_status / cmd_search / daily_exam_review）──────────────

def load_state():
    # type: () -> Dict[str, Any]
    """加载全部候选人。仅在需要遍历所有人时使用。"""
    _require_db()
    return _tdb().load_state_from_db()


# ─── 兼容函数（供测试 / cmd_status / cmd_search 直接操作 state 使用）──────────

def get_candidate(state, talent_id):
    # type: (Dict[str, Any], str) -> Dict[str, Any]
    """从已加载的 state 中获取候选人，不存在时返回空记录骨架。"""
    return (state.get("candidates") or {}).get(talent_id) or {
        "talent_id": talent_id, "stage": "NEW", "audit": []
    }


def save_state(state):
    # type: (Dict[str, Any]) -> None
    """将 state 中所有候选人写入 DB。"""
    _require_db()
    _tdb().sync_state_to_db(state)


# ─── 工具函数 ─────────────────────────────────────────────────────────────────

def append_audit(cand, actor, action, payload=None):
    # type: (Dict[str, Any], str, str, Optional[Dict[str, Any]]) -> None
    entry = {
        "at": _now_iso(),
        "actor": actor,
        "action": action,
        "payload": payload or {},
    }
    cand.setdefault("audit", []).append(entry)


def ensure_stage_transition(cand, allowed_from, target):
    # type: (Dict[str, Any], Set[str], str) -> bool
    current = cand.get("stage") or "NEW"
    if allowed_from and current not in allowed_from:
        return False
    if target not in STAGES:
        return False
    cand["stage"] = target
    return True
