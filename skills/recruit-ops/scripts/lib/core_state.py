#!/usr/bin/env python3
"""
核心状态模块：PostgreSQL 为唯一数据源。

统一调用风格：
    cand = load_candidate(talent_id)      # 读单个
    # ... 修改 cand ...
    save_candidate(talent_id, cand)       # 写单个

批量读（仅 cmd_status / cmd_search 等只读查询）：
    state = load_state()                  # {"candidates": {...}}
"""
import importlib
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Set

# v3.6 (2026-04-27/28) 状态机瘦身：
#   - OFFER_HANDOFF 下线：之前它只是 round2_pass → HR 通知后 1-tick 的瞬时态，
#     从不持久化；现在 interview.cmd_result --round 2 --result pass 直接一步推到
#     POST_OFFER_FOLLOWUP（HR Feishu 通知不变）。
#   - ROUND1_DONE_REJECT_DELETE / ROUND2_DONE_REJECT_DELETE 下线：
#     reject_delete 走 _handle_reject_delete → talent_db.delete_talent()，物理删人，
#     根本留不到这俩枚举上。线上 0 行，枚举留着只会误导 agent。
STAGES = {
    "NEW",
    "ROUND1_SCHEDULING",
    "ROUND1_SCHEDULED",
    "EXAM_SENT",
    "EXAM_REVIEWED",
    "EXAM_REJECT_KEEP",
    "WAIT_RETURN",
    "ROUND2_SCHEDULING",
    "ROUND2_SCHEDULED",
    "ROUND2_DONE_REJECT_KEEP",
    "POST_OFFER_FOLLOWUP",
}

STAGE_LABELS = {
    "NEW": "新建",
    "ROUND1_SCHEDULING": "一面排期中",
    "ROUND1_SCHEDULED": "一面已安排",
    "EXAM_SENT": "笔试已发送",
    "EXAM_REVIEWED": "笔试已审阅",
    "EXAM_REJECT_KEEP": "笔试未通过（保留）",
    "WAIT_RETURN": "待回国后再约",
    "ROUND2_SCHEDULING": "二面排期中",
    "ROUND2_SCHEDULED": "二面已确认",
    "ROUND2_DONE_REJECT_KEEP": "二面未通过（保留）",
    "POST_OFFER_FOLLOWUP": "已结束面试流程，等待发放 Offer / 沟通入职",
}


def _tdb():
    # type: () -> Any
    """解析当前 talent_db 模块（每次从 sys.modules 取，便于测试注入 fake）。

    优先 lib.talent_db（生产路径），fallback 到 bare "talent_db"
    （兼容 tests/helpers.py 中老式 sys.modules 注入 + 任何 Solution B 之前的引用）。
    """
    try:
        return importlib.import_module("lib.talent_db")
    except ModuleNotFoundError:
        return importlib.import_module("talent_db")


def _require_db():
    # type: () -> None
    if not _tdb()._is_enabled():
        raise RuntimeError("DB 未配置，请检查 talent-db-config.json")


def get_tdb():
    # type: () -> Any
    """返回 talent_db 模块（DB 启用时），否则返回 None。"""
    if _tdb()._is_enabled():
        return _tdb()
    return None


def _now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f+08:00")


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


# ─── 批量读（仅供 cmd_status / cmd_search 等只读查询）──────────────────────

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
        "event_id": str(uuid.uuid4()),
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
