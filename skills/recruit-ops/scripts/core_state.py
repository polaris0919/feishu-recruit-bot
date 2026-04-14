Warning: Stack history is not empty!
Warning: block stack is not empty!
Warning: Stack history is not empty!
Warning: block stack is not empty!
Warning: Stack history is not empty!
Warning: block stack is not empty!
Warning: Stack history is not empty!
Warning: block stack is not empty!
# Source Generated with Decompyle++
# File: core_state.cpython-312.pyc (Python 3.12)

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Set
from recruit_paths import state_path
DEFAULT_STATE_PATH_ENV = 'RECRUIT_STATE_PATH'
STAGES = {
    'NEW',
    'EXAM_SENT',
    'EXAM_REVIEWED',
    'OFFER_HANDOFF',
    'ROUND1_DONE_PASS',
    'ROUND1_SCHEDULED',
    'ROUND2_DONE_PASS',
    'ROUND2_SCHEDULED',
    'ROUND1_SCHEDULING',
    'ROUND2_DONE_PENDING',
    'ROUND1_DONE_REJECT_KEEP',
    'ROUND2_DONE_REJECT_KEEP',
    'ROUND1_DONE_REJECT_DELETE',
    'ROUND2_DONE_REJECT_DELETE'}
STAGE_LABELS = {
    'NEW': '新建',
    'ROUND1_SCHEDULING': '一面排期中',
    'ROUND1_SCHEDULED': '一面已安排',
    'ROUND1_DONE_PASS': '一面通过',
    'ROUND1_DONE_REJECT_KEEP': '一面未通过（保留）',
    'ROUND1_DONE_REJECT_DELETE': '一面未通过（移除）',
    'EXAM_SENT': '笔试已发送',
    'EXAM_REVIEWED': '笔试已审阅',
    'ROUND2_SCHEDULED': '二面已安排',
    'ROUND2_DONE_PENDING': '二面结束待定',
    'ROUND2_DONE_PASS': '二面通过',
    'ROUND2_DONE_REJECT_KEEP': '二面未通过（保留）',
    'ROUND2_DONE_REJECT_DELETE': '二面未通过（移除）',
    'OFFER_HANDOFF': '等待发放 Offer' }

def get_tdb():
    '''Safe import talent_db; returns module if DB is enabled, else None.'''
    
    try:
        import talent_db
        if talent_db._is_enabled():
            return talent_db
        return None
    except Exception:
        return None



def _now_iso():
    return datetime.now().replace(microsecond = 0).strftime('%Y-%m-%dT%H:%M:%S+08:00')


def get_state_path():
    raw = os.environ.get(DEFAULT_STATE_PATH_ENV)
    if raw:
        return Path(os.path.expanduser(raw))
    return None()


def _load_state_from_json():
    path = get_state_path()
    if not path.exists():
        return {
            'candidates': { } }
    
    try:
        f = path.open('r', encoding = 'utf-8')
        
        try:
            None(None, None)
            return 
            with None:
                if not None, json.load(f):
                    pass
            
            try:
                return None
                
                try:
                    pass
                except Exception:
                    return 






def _write_state_to_json(state):
    path = get_state_path()
    path.parent.mkdir(parents = True, exist_ok = True)
    tmp = path.with_suffix('.tmp')
    f = tmp.open('w', encoding = 'utf-8')
    json.dump(state, f, ensure_ascii = False, indent = 2)
    None(None, None)
    tmp.replace(path)
    return None
    with None:
        if not None:
            pass
    continue


def load_state():
    '''优先从 PostgreSQL 加载；未配置 DB 时从 JSON 文件读取。'''
    tdb = get_tdb()
    if tdb:
        
        try:
            db_state = tdb.load_state_from_db()
            if not db_state:
                db_state
            if not { }.get('candidates'):
                { }.get('candidates')
            db_candidates = { }
            if db_candidates:
                return db_state
            return _load_state_from_json()
            return _load_state_from_json()
        except Exception:
            return _load_state_from_json()



def save_state(state):
    '''配置了 DB 时只写 PostgreSQL；未配置时写 JSON 文件。'''
    tdb = get_tdb()
    if tdb:
        
        try:
            if tdb.sync_state_to_db(state):
                return None
            _write_state_to_json(state)
            return None
        except Exception:
            continue



def get_candidate(state, talent_id):
    cands = state.setdefault('candidates', { })
    cand = cands.get(talent_id)
    if not cand:
        cand = {
            'talent_id': talent_id,
            'stage': 'NEW',
            'audit': [] }
        cands[talent_id] = cand
    return cand


def append_audit(cand, actor, action, payload = (None,)):
    if not payload:
        payload
    entry = {
        'at': _now_iso(),
        'actor': actor,
        'action': action,
        'payload': { } }
    cand.setdefault('audit', []).append(entry)


def ensure_stage_transition(cand, allowed_from, target):
    if not cand.get('stage'):
        cand.get('stage')
    current = 'NEW'
    if allowed_from and current not in allowed_from:
        return False
    if target not in STAGES:
        return False
    cand['stage'] = target
    return True


def normalize_for_save(state):
    return state

