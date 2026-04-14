Unsupported opcode: LOAD_FAST_CHECK (237)
Something TERRIBLE happened!
Something TERRIBLE happened!
Something TERRIBLE happened!
Warning: Stack history is not empty!
Warning: block stack is not empty!
Warning: Stack history is not empty!
Warning: block stack is not empty!
# Source Generated with Decompyle++
# File: feishu_client.cpython-312.pyc (Python 3.12)

'''
统一飞书客户端：合并 IM 消息 + 日历操作，单一 token 管理。
替代原来的 feishu_notify.py + feishu_calendar.py。
'''
import json
import os
import sys
import urllib.request as urllib
import urllib.error as urllib
from datetime import datetime, timedelta
from typing import Optional
import config as _cfg
from side_effect_guard import side_effects_disabled
FEISHU_API = 'https://open.feishu.cn/open-apis'
_token_cache = {
    'token': None,
    'expires_at': 0 }

def _get_tenant_access_token():
    import time
    now = time.time()
    if _token_cache['token'] and now < _token_cache['expires_at']:
        return _token_cache['token']
    feishu = None.get('feishu')
    app_id = feishu.get('app_id', '')
    app_secret = feishu.get('app_secret', '')
    if not app_id or app_secret:
        return None
    payload = json.dumps({
        'app_id': app_id,
        'app_secret': app_secret }).encode('utf-8')
    req = urllib.request.Request(FEISHU_API + '/auth/v3/tenant_access_token/internal', data = payload, headers = {
        'Content-Type': 'application/json' })
# WARNING: Decompyle incomplete


def _feishu_request(token, method, path, body = (None,)):
    url = FEISHU_API + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data = data, headers = {
        'Authorization': 'Bearer ' + token,
        'Content-Type': 'application/json' }, method = method)
    
    try:
        r = urllib.request.urlopen(req, timeout = 10)
        
        try:
            None(None, None)
            return 
            with None:
                if not None, json.loads(r.read()):
                    pass
            
            try:
                return None
                
                try:
                    pass
                except urllib.error.HTTPError:
                    del e
                    return None
                    None = 
                    del e






def send_text(text, open_id = (None,)):
    if not text or text.strip():
        return True
    if side_effects_disabled():
        return True
    feishu = _cfg.get('feishu')
    if not open_id:
        open_id
    open_id = feishu.get('boss_open_id', '')
    token = _get_tenant_access_token()
    if not token:
        print('[feishu] 无法获取 token，消息未发送', file = sys.stderr)
        return False
    payload = json.dumps({
        'receive_id': open_id,
        'msg_type': 'text',
        'content': json.dumps({
            'text': text }) }).encode('utf-8')
    
    try:
        req = urllib.request.Request(FEISHU_API + '/im/v1/messages?receive_id_type=open_id', data = payload, headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer {}'.format(token) })
        resp = urllib.request.urlopen(req, timeout = 15)
        result = json.loads(resp.read().decode('utf-8'))
        if result.get('code') == 0:
            
            try:
                None(None, None)
                return True
                print('[feishu] API 错误: {}'.format(result), file = sys.stderr)
                
                try:
                    None(None, None)
                    return False
                    with None:
                        if not None:
                            pass
                    
                    try:
                        return None
                        
                        try:
                            pass
                        except Exception:
                            e = None
                            print('[feishu] 发送失败: {}'.format(e), file = sys.stderr)
                            e = None
                            del e
                            return False
                            e = None
                            del e







def send_text_to_hr(text):
    feishu = _cfg.get('feishu')
    return send_text(text, open_id = feishu.get('hr_open_id', ''))


def _parse_time_to_timeWarning: Stack history is not empty!
Warning: block stack is not empty!
Warning: Stack history is not empty!
Warning: block stack is not empty!
stamp(time_str, duration_minutes = (60,)):
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%Y/%m/%d %H:%M'):
        naive = datetime.strptime(time_str.strip(), fmt)
        utc = naive - timedelta(hours = 8)
        start_ts = int((utc - datetime(1970, 1, 1)).total_seconds())
        
        return ('%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%Y/%m/%d %H:%M'), (str(start_ts), str(start_ts + duration_minutes * 60))
    raise ValueError('无法解析时间格式: ' + time_str)
    except ValueError:
        continue


def _create_calendar_event(token, calendar_id, summary, description, start_ts, end_ts):
    return _feishu_request(token, 'POST', '/calendar/v4/calendars/{}/events'.format(calendar_id), {
        'summary': summary,
        'description': description,
        'start_time': {
            'timestamp': start_ts,
            'timezone': 'Asia/Shanghai' },
        'end_time': {
            'timestamp': end_ts,
            'timezone': 'Asia/Shanghai' },
        'visibility': 'default',
        'free_busy_status': 'busy' })


def _add_attendee(token, calendar_id, event_id, boss_open_id):
    return _feishu_request(token, 'POST', '/calendar/v4/calendars/{}/events/{}/attendees?user_id_type=open_id'.format(calendar_id, event_id), {
        'attendees': [
            {
                'type': 'user',
                'user_id': boss_open_id }],
        'need_notification': True })


def delete_calendar_event_by_id(event_id):
    if side_effects_disabled():
        return True
    token = _get_tenant_access_token()
    if not token:
        return False
    feishu = _cfg.get('feishu')
    calendar_id = feishu.get('calendar_id', '')
    
    try:
        result = _feishu_request(token, 'DELETE', '/calendar/v4/calendars/{}/events/{}'.format(calendar_id, event_id))
        return result.get('code') == 0
    except Exception:
        return False



def create_interview_event(talent_id, interview_time, round_num, candidate_email, candidate_name, old_event_id = (2, '', '', '')):
    '''
    创建面试日历事件。round_num=1 为一面，round_num=2 为二面。
    返回操作结果消息。若 old_event_id 非空则先删旧事件。
    '''
    if side_effects_disabled():
        return '测试模式：已跳过创建日历事件 talent_id={} round={} time={}'.format(talent_id, round_num, interview_time)
    feishu = None.get('feishu')
    boss_open_id = feishu.get('boss_open_id', '')
    calendar_id = feishu.get('calendar_id', '')
    token = _get_tenant_access_token()
    if not token:
        raise RuntimeError('无法获取飞书 token')
    if old_event_id:
        
        try:
            _feishu_request(token, 'DELETE', '/calendar/v4/calendars/{}/events/{}'.format(calendar_id, old_event_id))
            (start_ts, end_ts) = _parse_time_to_timestamp(interview_time)
            if not candidate_name:
                candidate_name
            if not ''.strip():
                ''.strip()
            display_name = talent_id
            round_label = '一面' if round_num == 1 else '二面'
            summary = '[{}] {}'.format(round_label, display_name)
            desc_parts = [
                'talent_id: {}'.format(talent_id),
                '面试时间: {}'.format(interview_time)]
            if candidate_email:
                desc_parts.append('候选人邮箱: {}'.format(candidate_email))
            if round_num == 2:
                desc_parts.append('面试地点: 上海市浦东新区杨高中路丁香国际商业中心西塔21楼致邃投资')
            desc_parts.append('\n由 OpenClaw 招聘助手自动创建')
            result = _create_calendar_event(token, calendar_id, summary, '\n'.join(desc_parts), start_ts, end_ts)
            if result.get('code') != 0:
                raise RuntimeError('创建日历事件失败: ' + json.dumps(result, ensure_ascii = False))
            event = result['data']['event']
            event_id = event.get('event_id', '')
            app_link = event.get('app_link', '')
            msg = '已在飞书日历创建{}事件：{}\n  - 时间: {}\n  - 事件ID: {}'.format(round_label, summary, interview_time, event_id)
            if app_link:
                msg += '\n  - 直达链接: {}'.format(app_link)
            if boss_open_id:
                att_result = _add_attendee(token, calendar_id, event_id, boss_open_id)
                if att_result.get('code') == 0:
                    msg += '\n  - 已成功邀请老板为参与者'
                else:
                    msg += '\n  - 邀请老板失败: {}'.format(att_result.get('msg', ''))
            
            try:
                import talent_db as _tdb
                if _tdb._is_enabled():
                    _tdb.update_calendar_event_id(talent_id, round_num, event_id)
                return msg
                except Exception:
                    continue
            except Exception:
                return msg



