#!/usr/bin/env python3
"""
飞书客户端：IM 消息 + 日历操作。
基于官方 lark-oapi SDK（pip install lark-oapi）。
"""
import json
import sys
from datetime import datetime, timedelta
from typing import Optional

import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
from lark_oapi.api.calendar.v4 import (
    CreateCalendarEventRequest,
    DeleteCalendarEventRequest,
    CreateCalendarEventAttendeeRequest,
    CreateCalendarEventAttendeeRequestBody,
    CalendarEvent,
    CalendarEventAttendee,
    TimeInfo,
)

import config as _cfg
from side_effect_guard import side_effects_disabled

# ─── Client（懒加载，首次使用时初始化）────────────────────────────────────────

_client = None  # type: Optional[lark.Client]


def _get_client():
    # type: () -> Optional[lark.Client]
    global _client
    if _client is not None:
        return _client
    feishu = _cfg.get("feishu")
    app_id = feishu.get("app_id", "")
    app_secret = feishu.get("app_secret", "")
    if not app_id or not app_secret:
        return None
    _client = lark.Client.builder() \
        .app_id(app_id) \
        .app_secret(app_secret) \
        .log_level(lark.LogLevel.ERROR) \
        .build()
    return _client


# ─── IM 消息 ──────────────────────────────────────────────────────────────────

def send_text(text, open_id=None):
    # type: (str, Optional[str]) -> bool
    if not text or not text.strip():
        return True
    if side_effects_disabled():
        return True
    feishu = _cfg.get("feishu")
    open_id = (open_id or feishu.get("boss_open_id", "") or "").strip()
    client = _get_client()
    if not client:
        print("[feishu] 未配置 app_id/app_secret，消息未发送", file=sys.stderr)
        return False
    if not open_id:
        print("[feishu] 未配置 open_id，消息未发送", file=sys.stderr)
        return False
    req = CreateMessageRequest.builder() \
        .receive_id_type("open_id") \
        .request_body(CreateMessageRequestBody.builder()
            .receive_id(open_id)
            .msg_type("text")
            .content(json.dumps({"text": text}))
            .build()) \
        .build()
    resp = client.im.v1.message.create(req)
    if not resp.success():
        print("[feishu] 发消息失败: code={} msg={}".format(resp.code, resp.msg), file=sys.stderr)
        return False
    return True


def send_text_to_hr(text):
    # type: (str) -> bool
    feishu = _cfg.get("feishu")
    return send_text(text, open_id=feishu.get("hr_open_id", ""))


# ─── 日历操作 ──────────────────────────────────────────────────────────────────

def _parse_time_to_timestamp(time_str, duration_minutes=60):
    # type: (str, int) -> tuple
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y/%m/%d %H:%M"):
        try:
            naive = datetime.strptime(time_str.strip(), fmt)
            utc = naive - timedelta(hours=8)
            start_ts = int((utc - datetime(1970, 1, 1)).total_seconds())
            return str(start_ts), str(start_ts + duration_minutes * 60)
        except ValueError:
            continue
    raise ValueError("无法解析时间格式: " + time_str)


def delete_calendar_event_by_id(event_id):
    # type: (str) -> bool
    if side_effects_disabled():
        return True
    client = _get_client()
    if not client:
        return False
    feishu = _cfg.get("feishu")
    calendar_id = (feishu.get("calendar_id", "") or "").strip()
    if not calendar_id:
        print("[feishu] 未配置 calendar_id，无法删除日历事件", file=sys.stderr)
        return False
    req = DeleteCalendarEventRequest.builder() \
        .calendar_id(calendar_id) \
        .event_id(event_id) \
        .need_notification("false") \
        .build()
    resp = client.calendar.v4.calendar_event.delete(req)
    return resp.success()


def create_interview_event(
    talent_id, interview_time, round_num=2,
    candidate_email="", candidate_name="",
    old_event_id="",
):
    # type: (str, str, int, str, str, str) -> str
    """
    创建面试日历事件。round_num=1 为一面，round_num=2 为二面。
    返回操作结果消息。若 old_event_id 非空则先删旧事件。
    """
    if side_effects_disabled():
        return "测试模式：已跳过创建日历事件 talent_id={} round={} time={}".format(
            talent_id, round_num, interview_time)

    feishu = _cfg.get("feishu")
    boss_open_id = (feishu.get("boss_open_id", "") or "").strip()
    calendar_id = (feishu.get("calendar_id", "") or "").strip()
    client = _get_client()
    if not client:
        raise RuntimeError("飞书未配置 app_id/app_secret")
    if not calendar_id:
        raise RuntimeError("飞书未配置 calendar_id")

    if old_event_id:
        delete_calendar_event_by_id(old_event_id)

    start_ts, end_ts = _parse_time_to_timestamp(interview_time)
    display_name = (candidate_name or "").strip() or talent_id
    round_label = "一面" if round_num == 1 else "二面"
    summary = "[{}] {}".format(round_label, display_name)

    desc_parts = [
        "talent_id: {}".format(talent_id),
        "面试时间: {}".format(interview_time),
    ]
    if candidate_email:
        desc_parts.append("候选人邮箱: {}".format(candidate_email))
    if round_num == 2:
        desc_parts.append("面试地点: 上海市浦东新区杨高中路丁香国际商业中心西塔21楼致邃投资")
    desc_parts.append("\n由 OpenClaw 招聘助手自动创建")

    create_req = CreateCalendarEventRequest.builder() \
        .calendar_id(calendar_id) \
        .request_body(CalendarEvent.builder()
            .summary(summary)
            .description("\n".join(desc_parts))
            .start_time(TimeInfo.builder()
                .timestamp(start_ts)
                .timezone("Asia/Shanghai")
                .build())
            .end_time(TimeInfo.builder()
                .timestamp(end_ts)
                .timezone("Asia/Shanghai")
                .build())
            .need_notification(False)
            .build()) \
        .build()

    create_resp = client.calendar.v4.calendar_event.create(create_req)
    if not create_resp.success():
        raise RuntimeError("创建日历事件失败: code={} msg={}".format(
            create_resp.code, create_resp.msg))

    event_id = create_resp.data.event.event_id
    app_link = getattr(create_resp.data.event, "app_link", "") or ""

    msg = "已在飞书日历创建{}事件：{}\n  - 时间: {}\n  - 事件ID: {}".format(
        round_label, summary, interview_time, event_id)
    if app_link:
        msg += "\n  - 直达链接: {}".format(app_link)

    if boss_open_id:
        att_req = CreateCalendarEventAttendeeRequest.builder() \
            .calendar_id(calendar_id) \
            .event_id(event_id) \
            .user_id_type("open_id") \
            .request_body(CreateCalendarEventAttendeeRequestBody.builder()
                .attendees([
                    CalendarEventAttendee.builder()
                        .type("user")
                        .user_id(boss_open_id)
                        .is_optional(False)
                        .build()
                ])
                .need_notification(True)
                .build()) \
            .build()
        att_resp = client.calendar.v4.calendar_event_attendee.create(att_req)
        if att_resp.success():
            msg += "\n  - 已成功邀请老板为参与者"
        else:
            msg += "\n  - 邀请老板失败: {}".format(att_resp.msg)

    try:
        import talent_db as _tdb
        if _tdb._is_enabled():
            _tdb.update_calendar_event_id(talent_id, round_num, event_id)
    except Exception:
        pass

    return msg
