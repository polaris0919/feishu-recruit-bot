Unsupported opcode: LOAD_FAST_CHECK (237)
Unsupported opcode: LOAD_FAST_CHECK (237)
Something TERRIBLE happened!
Something TERRIBLE happened!
Something TERRIBLE happened!
Warning: Stack history is not empty!
Warning: block stack is not empty!
# Source Generated with Decompyle++
# File: feishu_calendar.cpython-312.pyc (Python 3.12)

'''
飞书日历集成：在 OpenClaw 日历上创建二面事件并邀请老板（attendee）。

依赖：
  - FEISHU_APP_ID / FEISHU_APP_SECRET  （或从工作区 config/openclaw.json 读取）
  - FEISHU_BOSS_OPEN_ID                （老板的 open_id，格式 ou_xxx）
  - FEISHU_CALENDAR_ID                 （机器人的日历 ID，默认内置）

用法示例（直接调用）：
  python3 feishu_calendar.py     --talent-id test001     --round2-time "2026-03-20 14:00"     --interviewer "老板A"     --candidate-email "xxx@example.com"
'''
import argparse
import json
import os
import sys
import urllib.request as urllib
import urllib.error as urllib
from datetime import datetime, timedelta
from typing import Optional
from recruit_paths import config_candidates, first_existing
from side_effect_guard import side_effects_disabled
OPENCLAW_CONFIG = first_existing(config_candidates('openclaw.json'))
DEFAULT_CALENDAR_ID = 'feishu.cn_vPEnd4yYlOvbjzLuY9Ye2e@group.calendar.feishu.cn'
FEISHU_API = 'https://open.feishu.cn/open-apis'

def get_app_credentials():
    '''从环境变量或 openclaw.json 获取 app_id / app_secret。'''
    app_id = os.environ.get('FEISHU_APP_ID', '')
    app_secret = os.environ.get('FEISHU_APP_SECRET', '')
    if app_id and app_secret:
        return (app_id, app_secret)
# WARNING: Decompyle incomplete


def get_tenant_token(app_id, app_secret):
    '''获取 tenant_access_token。'''
    body = json.dumps({
        'app_id': app_id,
        'app_secret': app_secret }).encode()
    req = urllib.request.Request(FEISHU_API + '/auth/v3/tenant_access_token/internal', data = body, headers = {
        'Content-Type': 'application/json' }, method = 'POST')
    r = urllib.request.urlopen(req, timeout = 10)
    d = json.loads(r.read())
    None(None, None)
# WARNING: Decompyle incomplete


def parse_time_to_timestamp(time_str, duration_minutes = (60,)):
    """
    将 '2026-03-20 14:00' 解析为 (start_ts, end_ts) Unix 时间戳（CST = UTC+8）。
    """
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%Y/%m/%d %H:%M'):
        naive = datetime.strptime(time_str.strip(), fmt)
        utc = naive - timedelta(hours = 8)
        start_ts = int((utc - datetime(1970, 1, 1)).total_seconds())
        end_ts = start_ts + duration_minutes * 60
        
        return ('%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%Y/%m/%d %H:%M'), (str(start_ts), str(end_ts))
    raise ValueError('无法解析时间格式: ' + time_str + "，请使用 'YYYY-MM-DD HH:MM'")
    except ValueError:
        continue


def feishu_request(token, method, path, body = (None,)):
    '''通用飞书 API 请求。'''
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






def create_calendar_event(token, calendar_id, summary, description, start_ts, end_ts):
    '''在指定日历创建事件。'''
    payload = {
        'summary': summary,
        'description': description,
        'start_time': {
            'timestamp': start_ts,
            'timezone': 'Asia/Shanghai' },
        'end_time': {
            'timestamp': end_ts,
            'timezone': 'Asia/Shanghai' },
        'visibility': 'default',
        'free_busy_status': 'busy' }
    return feishu_request(token, 'POST', '/calendar/v4/calendars/{}/events'.format(calendar_id), payload)Unsupported opcode: LOAD_FAST_CHECK (237)
Warning: Stack history is not empty!
Warning: block stack is not empty!
Unsupported opcode: LOAD_FAST_CHECK (237)
Warning: Stack history is not empty!
Warning: block stack is not empty!



def add_attendee(token, calendar_id, event_id, boss_open_id):
    '''
    单独调用 attendees 接口邀请老板，必须带 user_id_type=open_id。
    文档：POST /calendar/v4/calendars/{calendar_id}/events/{event_id}/attendees
    '''
    payload = {
        'attendees': [
            {
                'type': 'user',
                'user_id': boss_open_id }],
        'need_notification': True }
    return feishu_request(token, 'POST', '/calendar/v4/calendars/{}/events/{}/attendees?user_id_type=open_id'.format(calendar_id, event_id), payload)


def create_round2_event(talent_id, round2_time, interviewer, candidate_email, candidate_name, mode, meeting_link, meeting_provider = ('', '', '', 'offline', '', '')):
    '''
    创建二面日历事件。返回事件的 app_link（飞书日历直达链接）。
    '''
    if side_effects_disabled():
        return '测试模式：已跳过创建二面日历事件 talent_id={} time={}'.format(talent_id, round2_time)
    boss_open_id = None.environ.get('FEISHU_BOSS_OPEN_ID', '').strip()
# WARNING: Decompyle incomplete


def delete_calendar_event(token, calendar_id, event_id):
    '''删除日历事件，返回是否成功。'''
    if side_effects_disabled():
        return True
    
    try:
        result = feishu_request(token, 'DELETE', '/calendar/v4/calendars/{}/events/{}'.format(calendar_id, event_id))
        return result.get('code') == 0
    except Exception:
        return False



def create_round1_event(talent_id, round1_time, interviewer, candidate_email, old_event_id, candidate_name = ('', '', '', '')):
    '''
    创建一面日历事件（若 old_event_id 非空则先删旧事件）。
    返回操作结果消息。
    '''
    if side_effects_disabled():
        return '测试模式：已跳过创建一面日历事件 talent_id={} time={}'.format(talent_id, round1_time)
    boss_open_id = None.environ.get('FEISHU_BOSS_OPEN_ID', '').strip()
# WARNING: Decompyle incomplete


def parse_args(argv = (None,)):
    p = argparse.ArgumentParser(description = '创建飞书日历面试事件')
    p.add_argument('--talent-id', required = True)
    p.add_argument('--round2-time', default = '', help = "面试时间，例如: '2026-03-20 14:00'")
    p.add_argument('--interviewer', default = '')
    p.add_argument('--candidate-email', default = '')
    p.add_argument('--candidate-name', default = '', help = '候选人真实姓名（用于日历标题）')
    p.add_argument('--mode', choices = [
        'offline',
        'online'], default = 'offline', help = '面试形式')
    p.add_argument('--meeting-link', default = '', help = '线上面试链接')
    p.add_argument('--meeting-provider', default = '', help = '线上会议平台')
    p.add_argument('--event-round', default = '2', help = '面试轮次：1=一面, 2=二面（默认2）')
    p.add_argument('--old-event-id', default = '', help = '旧日历事件 ID（重约时用于删旧建新）')
    p.add_argument('--delete-event-id', default = '', help = '仅删除指定日历事件 ID，不创建新事件')
    if not argv:
        argv
    return p.parse_args(sys.argv[1:])


def main(argv = (None,)):
    args = parse_args(argv)
    if side_effects_disabled():
        if args.delete_event_id:
            print('测试模式：已跳过删除旧日历事件 {}'.format(args.delete_event_id))
            return 0
        if args.round2_time:
            print('测试模式：已跳过日历操作 talent_id={} time={}'.format(args.talent_id, args.round2_time))
            return 0
    if args.delete_event_id:
        
        try:
            (app_id, app_secret) = get_app_credentials()
            token = get_tenant_token(app_id, app_secret)
            calendar_id = os.environ.get('FEISHU_CALENDAR_ID', DEFAULT_CALENDAR_ID).strip()
            ok = delete_calendar_event(token, calendar_id, args.delete_event_id)
            print('删除旧日历事件 {}: {}'.format(args.delete_event_id, '成功' if ok else '失败/不存在'))
            if ok:
                return 0
            return None
            interview_time = args.round2_time.strip()
            if not interview_time:
                print('ERROR: --round2-time 不能为空', file = sys.stderr)
                return 1
            
            try:
                if args.event_round == '1':
                    msg = create_round1_event(talent_id = args.talent_id, round1_time = interview_time, interviewer = args.interviewer, candidate_email = args.candidate_email, old_event_id = args.old_event_id, candidate_name = args.candidate_name)
                elif args.old_event_id:
                    (app_id, app_secret) = get_app_credentials()
                    token = get_tenant_token(app_id, app_secret)
                    calendar_id = os.environ.get('FEISHU_CALENDAR_ID', DEFAULT_CALENDAR_ID).strip()
                    ok = delete_calendar_event(token, calendar_id, args.old_event_id)
                    print('删除旧日历事件 {}: {}'.format(args.old_event_id, '成功' if ok else '失败/不存在'))
                msg = create_round2_event(talent_id = args.talent_id, round2_time = interview_time, interviewer = args.interviewer, candidate_email = args.candidate_email, candidate_name = args.candidate_name, mode = args.mode, meeting_link = args.meeting_link, meeting_provider = args.meeting_provider)
                print(msg)
                return 0
                except Exception:
                    e = None
                    print('ERROR: ' + str(e), file = sys.stderr)
                    e = None
                    del e
                    return 1
                    e = None
                    del e
            except Exception:
                e = None
                print('ERROR: ' + str(e), file = sys.stderr)
                e = None
                del e
                return 1
                e = None
                del e



if __name__ == '__main__':
    raise SystemExit(main())
