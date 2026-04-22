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

from lib import config as _cfg
from lib.side_effect_guard import side_effects_disabled

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

class _FeishuTransientError(Exception):
    """飞书 SDK 瞬态错误，允许重试。"""


def _do_send_text(client, open_id, text):
    # type: (object, str, str) -> bool
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
        # 5xx / 限流当瞬态；其他 4xx 直接返回 False（caller 不重试）
        code = getattr(resp, "code", 0) or 0
        if code in (99991663, 99991400, 230020) or 500 <= int(code) < 600:
            raise _FeishuTransientError(
                "[feishu] 瞬态错误 code={} msg={}".format(code, resp.msg))
        print("[feishu] 发消息失败: code={} msg={}".format(code, resp.msg), file=sys.stderr)
        return False
    return True


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

    # 接入统一重试：仅对瞬态错误重试；4xx 业务错保持快速返回 False。
    try:
        from lib.http_retry import call_with_retry
        return call_with_retry(
            _do_send_text,
            args=(client, open_id, text),
            retries=2,
            base=0.8,
            cap=4.0,
            retriable=(_FeishuTransientError, ConnectionError, TimeoutError),
            label="feishu.send_text",
        )
    except _FeishuTransientError as e:
        print(str(e), file=sys.stderr)
        return False
    except Exception as e:
        print("[feishu] 发消息异常: {}".format(str(e)[:200]), file=sys.stderr)
        return False


def send_text_to_hr(text):
    # type: (str) -> bool
    feishu = _cfg.get("feishu")
    return send_text(text, open_id=feishu.get("hr_open_id", ""))


# ─── v3.5.7：三位一面面试官 sink wrapper ─────────────────────────────────────
# 用于 §5.11 一面派单（HR 触发）。每个 wrapper 是 send_text 的薄封装，
# 唯一职责是把 lib.config 里对应的 open_id 找出来透传。
# 看到占位符 open_id（ou_PLACEHOLDER_*）时会拒绝真实推送（fail closed），
# 避免在配齐之前误发到真人账号。
_PLACEHOLDER_PREFIX = "ou_PLACEHOLDER_"


def _send_text_to_interviewer(role, text):
    # type: (str, str) -> bool
    """role ∈ {'master','bachelor','cpp'}，对应 lib.config['feishu']['interviewer_*_open_id']。"""
    feishu = _cfg.get("feishu")
    key = "interviewer_{}_open_id".format(role)
    open_id = (feishu.get(key, "") or "").strip()
    if not open_id:
        print("[feishu] 未配置 {}，消息未发送".format(key), file=sys.stderr)
        return False
    if open_id.startswith(_PLACEHOLDER_PREFIX):
        print(
            "[feishu] 占位符 open_id 检测到（{}={}），拒绝推送。"
            "请先配置 FEISHU_INTERVIEWER_{}_OPEN_ID 环境变量或 openclaw config "
            "里的 interviewer{}OpenId 字段。".format(
                key, open_id, role.upper(), role.capitalize()),
            file=sys.stderr)
        return False
    return send_text(text, open_id=open_id)


def send_text_to_interviewer_master(text):
    # type: (str) -> bool
    return _send_text_to_interviewer("master", text)


def send_text_to_interviewer_bachelor(text):
    # type: (str) -> bool
    return _send_text_to_interviewer("bachelor", text)


def send_text_to_interviewer_cpp(text):
    # type: (str) -> bool
    return _send_text_to_interviewer("cpp", text)


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
    extra_attendee_open_ids=None,
    duration_minutes=None,
):
    # type: (str, str, int, str, str, str, Optional[list], Optional[int]) -> str
    """
    创建面试日历事件。round_num=1 为一面，round_num=2 为二面。
    返回操作结果消息。若 old_event_id 非空则先删旧事件。

    v3.5.7 新增参数：
      extra_attendee_open_ids: 额外参与者的飞书 open_id 列表（与 boss 并列）。
        典型用途：§5.11 一面派单时把面试官也加进日历。重复 / 与 boss 同 ID
        会被去重；以 `ou_PLACEHOLDER_` 开头的占位符 ID 会被跳过（fail closed），
        但不会让整个事件创建失败。
      duration_minutes: 事件时长（分钟）。None 表示走默认 60 分钟。
        §5.11 一面用 30 分钟。
    """
    if side_effects_disabled():
        extra_count = len(extra_attendee_open_ids or [])
        return "测试模式：已跳过创建日历事件 talent_id={} round={} time={} extras={} duration={}".format(
            talent_id, round_num, interview_time, extra_count, duration_minutes)

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

    duration = int(duration_minutes) if duration_minutes else 60
    start_ts, end_ts = _parse_time_to_timestamp(interview_time, duration_minutes=duration)
    display_name = (candidate_name or "").strip() or talent_id
    round_label = "一面" if round_num == 1 else "二面"
    summary = "[{}] {}".format(round_label, display_name)

    desc_parts = [
        "talent_id: {}".format(talent_id),
        "面试时间: {}".format(interview_time),
        "时长: {} 分钟".format(duration),
    ]
    if candidate_email:
        desc_parts.append("候选人邮箱: {}".format(candidate_email))
    if round_num == 2:
        desc_parts.append("面试地点: 公司办公地址（按实际填写）")
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

    msg = "已在飞书日历创建{}事件：{}\n  - 时间: {}\n  - 时长: {} 分钟\n  - 事件ID: {}".format(
        round_label, summary, interview_time, duration, event_id)
    if app_link:
        msg += "\n  - 直达链接: {}".format(app_link)

    # 收集所有 attendee：boss + extras，去重 + 跳占位符
    attendee_open_ids = []
    seen = set()
    if boss_open_id and not boss_open_id.startswith(_PLACEHOLDER_PREFIX):
        attendee_open_ids.append(("boss", boss_open_id))
        seen.add(boss_open_id)
    skipped_placeholders = []
    for oid in (extra_attendee_open_ids or []):
        oid = (oid or "").strip()
        if not oid or oid in seen:
            continue
        if oid.startswith(_PLACEHOLDER_PREFIX):
            skipped_placeholders.append(oid)
            continue
        attendee_open_ids.append(("extra", oid))
        seen.add(oid)

    if attendee_open_ids:
        att_objs = [
            CalendarEventAttendee.builder()
                .type("user")
                .user_id(oid)
                .is_optional(False)
                .build()
            for _, oid in attendee_open_ids
        ]
        att_req = CreateCalendarEventAttendeeRequest.builder() \
            .calendar_id(calendar_id) \
            .event_id(event_id) \
            .user_id_type("open_id") \
            .request_body(CreateCalendarEventAttendeeRequestBody.builder()
                .attendees(att_objs)
                .need_notification(True)
                .build()) \
            .build()
        att_resp = client.calendar.v4.calendar_event_attendee.create(att_req)
        if att_resp.success():
            tags = []
            for tag, _ in attendee_open_ids:
                tags.append("老板" if tag == "boss" else "面试官")
            msg += "\n  - 已成功邀请参与者：{}（共 {} 人）".format(
                "、".join(tags), len(attendee_open_ids))
        else:
            msg += "\n  - 邀请参与者失败: {}".format(att_resp.msg)

    if skipped_placeholders:
        msg += "\n  - ⚠️ 跳过 {} 个占位符 open_id（未配置真实账号）".format(
            len(skipped_placeholders))

    try:
        from lib import talent_db as _tdb
        if _tdb._is_enabled():
            _tdb.update_calendar_event_id(talent_id, round_num, event_id)
    except Exception:
        pass

    return msg
