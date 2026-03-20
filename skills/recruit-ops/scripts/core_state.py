#!/usr/bin/env python3
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Set

DEFAULT_STATE_PATH_ENV = "RECRUIT_STATE_PATH"

STAGES = {
    "NEW",
    "ROUND1_SCHEDULING",
    "ROUND1_SCHEDULED",
    "ROUND1_DONE_PASS",
    "ROUND1_DONE_REJECT_KEEP",
    "ROUND1_DONE_REJECT_DELETE",
    "EXAM_PENDING",
    "EXAM_REVIEWED",
    "ROUND2_SCHEDULED",
    "ROUND2_DONE_PENDING",
    "ROUND2_DONE_PASS",
    "ROUND2_DONE_REJECT_KEEP",
    "ROUND2_DONE_REJECT_DELETE",
    "OFFER_HANDOFF",
}


def _now_iso():
    # type: () -> str
    return datetime.now().replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def get_state_path():
    # type: () -> Path
    raw = os.environ.get(DEFAULT_STATE_PATH_ENV, "~/.openclaw/recruit_state.json")
    return Path(os.path.expanduser(raw))


def load_state():
    # type: () -> Dict[str, Any]
    """优先从 PostgreSQL 加载；未配置 DB 时从 JSON 文件读取。"""
    try:
        import talent_db
        if talent_db._is_enabled():
            return talent_db.load_state_from_db()
    except Exception:
        pass
    path = get_state_path()
    if not path.exists():
        return {"candidates": {}}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"candidates": {}}


def save_state(state):
    # type: (Dict[str, Any]) -> None
    """配置了 DB 时只写 PostgreSQL；未配置时写 JSON 文件。"""
    try:
        import talent_db
        if talent_db._is_enabled():
            talent_db.sync_state_to_db(state)
            return
    except Exception:
        pass
    path = get_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def get_candidate(state, talent_id):
    # type: (Dict[str, Any], str) -> Dict[str, Any]
    cands = state.setdefault("candidates", {})
    cand = cands.get(talent_id)
    if not cand:
        cand = {"talent_id": talent_id, "stage": "NEW", "audit": []}
        cands[talent_id] = cand
    return cand


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


def normalize_for_save(state):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    return state
