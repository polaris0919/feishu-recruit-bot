Warning: Stack history is not empty!
Warning: block stack is not empty!
Unsupported opcode: RETURN_GENERATOR (109)
Unsupported opcode: LOAD_FAST_CHECK (237)
Warning: Stack history is not empty!
Warning: block stack is not empty!
# Source Generated with Decompyle++
# File: talent_db.cpython-312.pyc (Python 3.12)

'''
人才库 PostgreSQL 读写模块。
配置优先级：环境变量 > 工作区 config/talent-db-config.json > 显式兼容目录
'''
from __future__ import print_function
import json
import os
import re as _re
import sys
from datetime import datetime
from typing import Any, Dict, List, Set
from recruit_paths import config_candidates

try:
    import psycopg2
    from psycopg2.extras import execute_values, Json
    _CONFIG_LOADED = False
    _DISABLE_DB_ENV = 'RECRUIT_DISABLE_DB'
    
    def _config_file_paths():
        paths = []
        if os.environ.get('TALENT_DB_CONFIG_PATH'):
            paths.append(os.path.expanduser(os.environ['TALENT_DB_CONFIG_PATH']))
        (lambda .0: pass# WARNING: Decompyle incomplete
)(config_candidates('talent-db-config.json')())
        return paths

    
    def _ensure_config_from_file():
        global _CONFIG_LOADED
        if _CONFIG_LOADED:
            return None
        _CONFIG_LOADED = True
        if os.environ.get('TALENT_DB_PASSWORD'):
            return None
    # WARNING: Decompyle incomplete

    
    def _env(key, default):
        _ensure_config_from_file()
        if not os.environ.get(key):
            os.environ.get(key)
        if not ''.strip():
            ''.strip()
        return default

    
    def _is_enabled():
        _ensure_config_from_file()
        if not os.environ.get(_DISABLE_DB_ENV):
            os.environ.get(_DISABLE_DB_ENV)
        if ''.strip().lower() in ('1', 'true', 'yes', 'on'):
            return False
        if not psycopg2:
            return False
        return bool(os.environ.get('TALENT_DB_PASSWORD', '').strip())

    
    def _conn_params():
        _ensure_config_from_file()
        if not os.environ.get('TALENT_DB_PASSWORD'):
            os.environ.get('TALENT_DB_PASSWORD')
        return {
            'host': _env('TALENT_DB_HOST', '127.0.0.1'),
            'port': int(_env('TALENT_DB_PORT', '5432')),
            'dbname': _env('TALENT_DB_NAME', 'recruit'),
            'user': _env('TALENT_DB_USER', 'recruit_app'),
            'password': ''.strip(),
            'connect_timeout': 10 }

    
    def _parse_iso(ts):
        '''Parse ISO timestamp to naive local (CST) datetime. Python 3.6 compatible.'''
        if not ts:
            ts
        s = ''.strip()
        if not s:
            return datetime.now()
        s_plain = None.sub('[+-]\\d{2}:\\d{2}$', '', s.replace('Z', '')).strip()
        s_plain = s_plain.replace('T', ' ')
        for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            
            return ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'), datetime.strptime(s_plain, fmt)
        return datetime.now()
        except ValueError:
            continue

    
    def _ensure_round2_online_columns(conn):
        
        try:
            cur = conn.cursor()
            cur.execute('\n                ALTER TABLE talents\n                ADD COLUMN IF NOT EXISTS round2_mode TEXT,\n                ADD COLUMN IF NOT EXISTS round2_meeting_link TEXT,\n                ADD COLUMN IF NOT EXISTS round2_meeting_provider TEXT,\n                ADD COLUMN IF NOT EXISTS round2_mode_reason TEXT\n                ')
            
            try:
                None(None, None)
                conn.commit()
                return None
                with None:
                    if not None:
                        pass
                
                try:
                    continue
                except Exception:
                    e = None
                    msg = str(e).lower()
                    if 'must be owner of table talents' in msg or 'permission denied' in msg:
                        conn.rollback()
                    else:
                        except Exception:
                            pass
                        print('[talent_db] 跳过在线二面字段补齐: {}'.format(e), file = sys.stderr)
                        e = None
          Warning: Stack history is not empty!
Warning: block stack is not empty!
Warning: Stack history is not empty!
Warning: block stack is not empty!
Warning: block stack is not empty!
              del e
                        return None
                        raise 
                    e = None
                    del e




    
    def _ensure_reschedule_columns(conn):
        
        try:
            cur = conn.cursor()
            cur.execute('\n                ALTER TABLE talents\n                ADD COLUMN IF NOT EXISTS round1_reschedule_pending BOOLEAN DEFAULT FALSE,\n                ADD COLUMN IF NOT EXISTS round2_reschedule_pending BOOLEAN DEFAULT FALSE\n                ')
            
            try:
                None(None, None)
                conn.commit()
                return None
                with None:
                    if not None:
                        pass
                
                try:
                    continue
                except Exception:
                    e = None
                    msg = str(e).lower()
                    if 'must be owner of table talents' in msg or 'permission denied' in msg:
                        conn.rollback()
                    else:
                        except Exception:
                            pass
                        e = None
                        del e
                        return None
                        conn.rollback()
                except Exception:
                    pass

                e = None
                del e
                return None
                e = None
                del e
                return None
                e = None
                del e



    
    def _supports_round2_online_columns(conn):
        
        try:
            cur = conn.cursor()
            cur.execute('SELECT round2_mode, round2_meeting_link, round2_meeting_provider, round2_mode_reason FROM talents LIMIT 0')
            
            try:
                None(None, None)
                return True
                with None:
                    if not None:
                        pass
                
                try:
                    return True
                    
                    try:
                        pass
                    except Exception:
                        e = None
                        msg = str(e).lower()
                        if 'round2_mode' in msg and 'does not exist' in msg:
                            conn.rollback()
                        else:
                            except Exception:
                                pass
                            e = None
                            del e
                            return False
                            raise 
                        e = None
                        del e





    
    def _upsert_talent(conn, tid, cand, has_round2_online_columns = (True,)):
        if not cand.get('stage'):
            cand.get('stage')
        stage = 'NEW'.strip()
        if not cand.get('candidate_email'):
            cand.get('candidate_email')
        if not ''.strip():
            ''.strip()
        email = None
        if not cand.get('candidate_name'):
            cand.get('candidate_name')
        if not ''.strip():
            ''.strip()
        name = None
        if not cand.get('exam_id'):
            cand.get('exam_id')
        if not ''.strip():
            ''.strip()
        exam_id = None
        if not cand.get('round1_time'):
            cand.get('round1_time')
        if not ''.strip():
            ''.strip()
        round1_time = None
        if not cand.get('round2_time'):
            cand.get('round2_time')
        if not ''.strip():
            ''.strip()
        round2_time = None
        if not cand.get('round2_interviewer'):
            cand.get('round2_interviewer')
        if not ''.strip():
            ''.strip()
        round2_interviewer = None
        if not cand.get('round2_mode'):
            cand.get('round2_mode')
        if not ''.strip():
            ''.strip()
        round2_mode = None
        if not cand.get('round2_meeting_link'):
            cand.get('round2_meeting_link')
        if not ''.strip():
            ''.strip()
        round2_meeting_link = None
        if not cand.get('round2_meeting_provider'):
            cand.get('round2_meeting_provider')
        if not ''.strip():
            ''.strip()
        round2_meeting_provider = None
        if not cand.get('round2_mode_reason'):
            cand.get('round2_mode_reason')
        if not ''.strip():
            ''.strip()
        round2_mode_reason = None
        if not cand.get('position'):
            cand.get('position')
        if not ''.strip():
            ''.strip()
        position = None
        if not cand.get('education'):
            cand.get('education')
        if not ''.strip():
            ''.strip()
        education = None
        if not cand.get('work_years'):
            cand.get('work_years')
        work_years = None
        if not cand.get('experience'):
            cand.get('experience')
        if not ''.strip():
            ''.strip()
        experience = None
        if not cand.get('source'):
            cand.get('source')
        if not ''.strip():
            ''.strip()
        source = None
        if not cand.get('school'):
            cand.get('school')
        if not ''.strip():
            ''.strip()
        school = None
        if not cand.get('phone'):
            cand.get('phone')
        if not ''.strip():
            ''.strip()
        phone = None
        if not cand.get('wechat'):
            cand.get('wechat')
        if not ''.strip():
            ''.strip()
        wechat = None
        if not cand.get('round1_notes'):
            cand.get('round1_notes')
        if not ''.strip():
            ''.strip()
        round1_notes = None
        if not cand.get('exam_score'):
            cand.get('exam_score')
        exam_score = None
        if not cand.get('exam_notes'):
            cand.get('exam_notes')
        if not ''.strip():
            ''.strip()
        exam_notes = None
        if not cand.get('round2_score'):
            cand.get('round2_score')
        round2_score = None
        if not cand.get('round2_notes'):
            cand.get('round2_notes')
        if not ''.strip():
            ''.strip()
        round2_notes = None
        if not cand.get('cv_path'):
            cand.get('cv_path')
        if not ''.strip():
            ''.strip()
        cv_path = None
        exam_sent_at_raw = cand.get('exam_sent_at')
        
        try:
            exam_sent_at = _parse_iso(exam_sent_at_raw) if exam_sent_at_raw else None
            now = datetime.now()
            cur = conn.cursor()
            if has_round2_online_columns:
                cur.execute('\n                INSERT INTO talents (\n                    talent_id, candidate_email, candidate_name, current_stage,\n                    exam_id, round1_time, round2_time, round2_interviewer,\n                    round2_mode, round2_meeting_link, round2_meeting_provider, round2_mode_reason, source,\n                    position, education, work_years, experience,\n                    school, phone, wechat,\n                    round1_notes, exam_score, exam_notes,\n                    round2_score, round2_notes,\n                    exam_sent_at, cv_path,\n                    created_at, updated_at\n                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)\n                ON CONFLICT (talent_id) DO UPDATE SET\n                    candidate_email    = EXCLUDED.candidate_email,\n                    candidate_name     = EXCLUDED.candidate_name,\n                    current_stage      = EXCLUDED.current_stage,\n                    exam_id            = EXCLUDED.exam_id,\n                    round1_time        = COALESCE(EXCLUDED.round1_time, talents.round1_time),\n                    round2_time        = EXCLUDED.round2_time,\n                    round2_interviewer = EXCLUDED.round2_interviewer,\n                    round2_mode        = EXCLUDED.round2_mode,\n                    round2_meeting_link = EXCLUDED.round2_meeting_link,\n                    round2_meeting_provider = EXCLUDED.round2_meeting_provider,\n                    round2_mode_reason = EXCLUDED.round2_mode_reason,\n                    source             = EXCLUDED.source,\n                    position           = EXCLUDED.position,\n                    education          = EXCLUDED.education,\n                    work_years         = EXCLUDED.work_years,\n                    experience         = EXCLUDED.experience,\n                    school             = EXCLUDED.school,\n                    phone              = EXCLUDED.phone,\n                    wechat             = EXCLUDED.wechat,\n                    round1_notes       = EXCLUDED.round1_notes,\n                    exam_score         = EXCLUDED.exam_score,\n                    exam_notes         = EXCLUDED.exam_notes,\n                    round2_score       = EXCLUDED.round2_score,\n                    round2_notes       = EXCLUDED.round2_notes,\n                    exam_sent_at       = COALESCE(talents.exam_sent_at, EXCLUDED.exam_sent_at),\n                    cv_path            = COALESCE(EXCLUDED.cv_path, talents.cv_path),\n                    updated_at         = EXCLUDED.updated_at\n                ', (tid, email, name, stage, exam_id, round1_time, round2_time, round2_interviewer, round2_mode, round2_meeting_link, round2_meeting_provider, round2_mode_reason, source, position, education, work_years, experience, school, phone, wechat, round1_notes, exam_score, exam_notes, round2_score, round2_notes, exam_sent_at, cv_path, now, now))
            else:
                cur.execute('\n                INSERT INTO talents (\n                    talent_id, candidate_email, candidate_name, current_stage,\n                    exam_id, round1_time, round2_time, round2_interviewer,\n                    source, position, education, work_years, experience,\n                    school, phone, wechat,\n                    round1_notes, exam_score, exam_notes,\n                    round2_score, round2_notes,\n                    exam_sent_at, cv_path,\n                    created_at, updated_at\n                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)\n                ON CONFLICT (talent_id) DO UPDATE SET\n                    candidate_email    = EXCLUDED.candidate_email,\n                    candidate_name     = EXCLUDED.candidate_name,\n                    current_stage      = EXCLUDED.current_stage,\n                    exam_id            = EXCLUDED.exam_id,\n                    round1_time        = COALESCE(EXCLUDED.round1_time, talents.round1_time),\n                    round2_time        = EXCLUDED.round2_time,\n                    round2_interviewer = EXCLUDED.round2_interviewer,\n                    source             = EXCLUDED.source,\n                    position           = EXCLUDED.position,\n                    education          = EXCLUDED.education,\n                    work_years         = EXCLUDED.work_years,\n                    experience         = EXCLUDED.experience,\n                    school             = EXCLUDED.school,\n                    phone              = EXCLUDED.phone,\n                    wechat             = EXCLUDED.wechat,\n                    round1_notes       = EXCLUDED.round1_notes,\n                    exam_score         = EXCLUDED.exam_score,\n                    exam_notes         = EXCLUDED.exam_notes,\n                    round2_score       = EXCLUDED.round2_score,\n                    round2_notes       = EXCLUDED.round2_notes,\n                    exam_sent_at       = COALESCE(talents.exam_sent_at, EXCLUDED.exam_sent_at),\n                    cv_path            = COALESCE(EXCLUDED.cv_path, talents.cv_path),\n                    updated_at         = EXCLUDED.updated_at\n                ', (tid, email, name, stage, exam_id, round1_time, round2_time, round2_interviewer, source, position, education, work_years, experience, school, phone, wechat, round1_notes, exam_score, exam_notes, round2_score, round2_notes, exam_sent_at, cv_path, now, now))
            None(None, None)
            return None
        except Exception:
            exam_sent_at = None
            continue
       Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
     with None:
                if not None:
                    pass
            return None


    
    def _existing_event_keys(conn, talent_id):
        cur = conn.cursor()
        cur.execute('SELECT at, action FROM talent_events WHERE talent_id = %s', (talent_id,))
        result = set()
        for r in cur.fetchall():
            at_val = r[0]
            result.add((k, r[1]))
        None(None, None)
        return 
        with None:
            if not None, result:
                pass

    
    def _insert_events(conn, talent_id, audit):
        if not audit:
            return None
        existing = _existing_event_keys(conn, talent_id)
        rows = []
        for entry in audit:
            if not entry.get('at'):
                entry.get('at')
            at_str = ''.strip()
            if not entry.get('actor'):
                entry.get('actor')
            if not 'system'.strip():
                'system'.strip()
            actor = 'system'
            if not entry.get('action'):
                entry.get('action')
            action = ''.strip()
            payload = entry.get('payload')
            payload_obj = payload if isinstance(payload, dict) else { }
            at_dt = _parse_iso(at_str)
            key = (at_dt.strftime('%Y-%m-%d %H:%M:%S'), action)
            if key in existing:
                continue
            rows.append((talent_id, at_dt, actor, action, Json(payload_obj) if Json else json.dumps(payload_obj)))
        if not rows:
            return None
        cur = conn.cursor()
        execute_values(cur, 'INSERT INTO talent_events (talent_id, at, actor, action, payload) VALUES %s', rows)
        None(None, None)
        return None
        except Exception:
            at_dt = datetime.now()
            continue
        with None:
            if not None:
                pass

    
    def load_state_from_db():
        if not _is_enabled():
            return {
                'candidates': { } }
    # WARNING: Decompyle incomplete

    
    def sync_state_to_db(state):
        if not _is_enabled():
            return False
        if not state.get('candidates'):
            state.get('candidates')
        candidates = { }
        if not candidates:
            return False
    # WARNING: Decompyle incomplete

    
    def delete_talent_from_db(talent_id):
        if not _is_enabled():
            return False
        if not talent_id:
            talent_id
        talent_id = ''.strip()
        if not talent_id:
            return False
    # WARNING: Decompyle incomplete

    
    def get_processed_email_ids():
        if not _is_enabled():
            return set()
    # WARNING: Decompyle incomplete

    
    def delete_talent(talent_id):
        '''从数据库彻底删除候选人及其所有关联记录（talent_events、processed_emails）。'''
        if not _is_enabled():
            return None
    # WARNING: Decompyle incomplete

    
    def mark_emails_processed(entries):
        if not _is_enabled() or entries:
            return None
    # WARNING: Decompyle incomplete

    
    def clear_processed_emails(talent_ids, message_id_patterns = (None, None)):
        '''清除已处理邮件标记，允许重新扫描。可按 talent_id 列表或 message_id LIKE 模式匹配。'''
        if not _is_enabled():
            return 0
        if not talent_ids and message_id_patterns:
            return 0
    # WARNING: Decompyle incomplete

    
    def mark_interview_reminded(talent_id):
        if not _is_enabled():
            return None
    # WARNING: Decompyle incomplete

    
    def get_pending_round1_reminders():
        '''查找一面已安排、面试时间已过、尚未发过提醒的候选人。'''
        if not _is_enabled():
            return []
    # WARNING: Decompyle incomplete

    
    def mark_round1_reminded(talent_id):
        if not _is_enabled():
            return None
    # WARNING: Decompyle incomplete

    
    def get_pending_interview_reminders():
        if not _is_enabled():
            return []
    # WARNING: Decompyle incomplete
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)

    
    def save_exam_prereview(talent_id, exam_score, exam_notes):
        '''
    将笔试预审初评分和摘要写入 talents 表。
    仅在候选人存在且处于笔试相关阶段时写入，不覆盖人工填写的内容（若已有值则追加）。
    '''
        if not _is_enabled():
            return None
    # WARNING: Decompyle incomplete

    
    def save_round2_invite_info(talent_id, calendar_event_id = (None,)):
        '''记录二面邀请发出时间（NOW()），可选同时写入日历事件 ID。'''
        if not _is_enabled():
            return None
    # WARNING: Decompyle incomplete

    
    def mark_round2_confirmed(talent_id, auto = (False,)):
        '''标记候选人已确认二面时间（auto=True 表示超时默认确认）。'''
        if not _is_enabled():
            return None
    # WARNING: Decompyle incomplete

    
    def update_round2_calendar_event_id(talent_id, event_id):
        '''仅更新二面日历事件 ID，不改动 round2_confirmed / round2_invite_sent_at。'''
        if not _is_enabled():
            return None
    # WARNING: Decompyle incomplete

    
    def clear_round2_calendar_event_id(talent_id):
        '''清空二面日历事件 ID（用于确认前取消/改期）。'''
        if not _is_enabled():
            return None
    # WARNING: Decompyle incomplete

    
    def reset_round2_scheduling_tracking(talent_id):
        '''清空二面邀请跟踪字段，用于暂缓/撤回二面安排。'''
        if not _is_enabled():
            return None
    # WARNING: Decompyle incomplete

    
    def get_round2_pending_confirmations():
        '''
    返回所有 ROUND2_SCHEDULED 且尚未确认的候选人，
    附带 round2_invite_sent_at 用于判断是否超 48 小时。
    '''
        if not _is_enabled():
            return []
    # WARNING: Decompyle incomplete

    
    def save_round1_invite_info(talent_id, calendar_event_id = (None,)):
        '''记录一面邀请发出时间（NOW()），可选同时写入日历事件 ID，并重置 round1_confirmed=FALSE。'''
        if not _is_enabled():
            return None
    # WARNING: Decompyle incomplete

    
    def update_round1_calendar_event_id(talent_id, event_id):
        '''仅更新一面日历事件 ID，不改动 round1_confirmed / round1_invite_sent_at。'''
        if not _is_enabled():
            return None
    # WARNING: Decompyle incomplete

    
    def mark_round1_confirmed(talent_id, auto = (False,)):
        '''标记候选人已确认一面时间，阶段推进到 ROUND1_SCHEDULED。'''
        if not _is_enabled():
            return None
    # WARNING: Decompyle incomplete

    
    def get_round1_pending_confirmations():
        '''
    返回所有 ROUND1_SCHEDULING 且尚未确认的候选人，
    附带 round1_invite_sent_at 用于判断是否超 48 小时。
    '''
        if not _is_enabled():
            return []
    # WARNING: Decompyle incomplete

    
    def get_round1_confirmed_candidates():
        '''返回所有一面已确认（confirmed=TRUE）的候选人，用于扫描改期请求。'''
        if not _is_enabled():
            return []
    # WARNING: Decompyle incomplete

    
    def get_round2_confirmed_candidates():
        '''返回所有二面已确认（confirmed=TRUE）的候选人，用于扫描改期请求。'''
        if not _is_enabled():
            return []
    # WARNING: Decompyle incomplete

    
    def mark_reschedule_pending(talent_id, round_num):
        '''标记候选人改期待处理：confirmed=FALSE + reschedule_pending=TRUE。'''
        if not _is_enabled():
            return None
    # WARNING: Decompyle incomplete

    
    def clear_reschedule_pending(talent_id, round_num):
        '''清除改期待处理标记（老板执行 reschedule 后调用）。'''
        if not _is_enabled():
            return None
    # WARNING: Decompyle incomplete

    
    def set_boss_confirm_pending(talent_id, round_num, proposed_time, proposed_by):
        '''
    记录一面/二面"待老板最终确认"的握手Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
Unsupported opcode: DICT_MERGE (213)
信息。
    proposed_by: "boss" 或 "candidate"
    '''
        if not _is_enabled():
            return None
        col_time = 'round{}_proposed_time'.format(round_num)
        col_by = 'round{}_proposed_by'.format(round_num)
        col_pending = 'round{}_boss_confirm_pending'.format(round_num)
        col_prompt = 'round{}_boss_confirm_prompt_at'.format(round_num)
    # WARNING: Decompyle incomplete

    
    def get_boss_confirm_pending(talent_id, round_num):
        '''
    查询一面/二面"待老板最终确认"的握手信息。
    返回 {"pending": bool, "proposed_time": str|None, "proposed_by": str|None, "prompt_at": str|None}
    '''
        empty = {
            'pending': False,
            'proposed_time': None,
            'proposed_by': None,
            'prompt_at': None }
        if not _is_enabled():
            return empty
        col_time = None.format(round_num)
        col_by = 'round{}_proposed_by'.format(round_num)
        col_pending = 'round{}_boss_confirm_pending'.format(round_num)
        col_prompt = 'round{}_boss_confirm_prompt_at'.format(round_num)
    # WARNING: Decompyle incomplete

    
    def clear_boss_confirm_pending(talent_id, round_num):
        '''清除握手字段（最终确认完成后调用）。'''
        if not _is_enabled():
            return None
        col_time = 'round{}_proposed_time'.format(round_num)
        col_by = 'round{}_proposed_by'.format(round_num)
        col_pending = 'round{}_boss_confirm_pending'.format(round_num)
        col_prompt = 'round{}_boss_confirm_prompt_at'.format(round_num)
    # WARNING: Decompyle incomplete

    
    def get_all_boss_confirm_pending():
        '''返回所有"待老板最终确认面试时间"的候选人（一面或二面），用于 cron 催问。'''
        if not _is_enabled():
            return []
    # WARNING: Decompyle incomplete

    if __name__ == '__main__':
        state = load_state_from_db()
        if not state.get('candidates'):
            state.get('candidates')
        n = len({ })
        print('load_state_from_db: {} 位候选人'.format(n))
        return None
    return None
except ImportError:
    psycopg2 = None
    Json = None
    continue

