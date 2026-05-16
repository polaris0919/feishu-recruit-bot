#!/usr/bin/env python3
"""
飞书客户端：IM 消息 + 日历操作。
基于官方 lark-oapi SDK（pip install lark-oapi）。
"""
import json
import mimetypes
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
from lark_oapi.api.calendar.v4 import (
    Attachment,
    CreateCalendarEventRequest,
    DeleteCalendarEventRequest,
    CreateCalendarEventAttendeeRequest,
    CreateCalendarEventAttendeeRequestBody,
    CalendarEvent,
    CalendarEventAttendee,
    TimeInfo,
)
from lark_oapi.api.drive.v1 import (
    UploadAllMediaRequest,
    UploadAllMediaRequestBody,
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


def send_text_to_polaris(text):
    # type: (str) -> bool
    feishu = _cfg.get("feishu")
    return send_text(text, open_id=(
        feishu.get("polaris_open_id", "") or feishu.get("scheduler_open_id", "")))


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


# drive.v1.media.upload_all 全量上传单文件上限 20MB；日程附件总量限制另有 25MB。
_CALENDAR_ATTACHMENT_LIMIT_BYTES = 20 * 1024 * 1024


def _lookup_candidate_cv_path(talent_id):
    # type: (str) -> str
    try:
        from lib import talent_db as _tdb
        if not _tdb._is_enabled():
            return ""
        return (_tdb.get_talent_field(talent_id, "cv_path") or "").strip()
    except Exception as e:
        print("[feishu] 查询候选人 CV 路径失败: {}".format(e), file=sys.stderr)
        return ""


def _find_candidate_cv_file(talent_id):
    # type: (str) -> tuple
    """返回 (Path|None, status_source)。优先 DB cv_path，缺失时扫候选人 cv/ 目录。"""
    cv_path = _lookup_candidate_cv_path(talent_id)
    if cv_path:
        path = Path(cv_path).expanduser()
        try:
            path = path.resolve()
        except OSError:
            pass
        return path, "cv_path"

    try:
        from lib.candidate_storage import cv_dir
        base = cv_dir(talent_id)
        if base.is_dir():
            candidates = [
                p for p in base.iterdir()
                if p.is_file() and p.suffix.lower() in (".pdf", ".docx")
            ]
            if candidates:
                candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                return candidates[0].resolve(), "cv_dir"
    except Exception as e:
        print("[feishu] 扫描候选人 CV 目录失败: {}".format(e), file=sys.stderr)
    return None, "missing"


def _upload_calendar_attachment(client, calendar_id, file_path):
    # type: (object, str, Path) -> str
    """上传日程附件素材，返回 file_token。"""
    mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    extra = {"mime_type": mime_type}
    with open(str(file_path), "rb") as f:
        req = UploadAllMediaRequest.builder() \
            .request_body(UploadAllMediaRequestBody.builder()
                .file_name(file_path.name)
                .parent_type("calendar")
                .parent_node(calendar_id)
                .size(file_path.stat().st_size)
                .file(f)
                .extra(json.dumps(extra, ensure_ascii=False))
                .build()) \
            .build()
        resp = client.drive.v1.media.upload_all(req)
    if not resp.success():
        raise RuntimeError("上传日程附件失败: code={} msg={}".format(resp.code, resp.msg))
    token = getattr(resp.data, "file_token", "") if resp.data else ""
    if not token:
        raise RuntimeError("上传日程附件成功但未返回 file_token")
    return token


def _prepare_cv_calendar_attachment(client, calendar_id, talent_id, enabled=True):
    # type: (object, str, str, bool) -> tuple
    """尽力把候选人 CV 上传成日程附件，返回 (attachments, status_line)。"""
    if not enabled:
        return [], "CV附件：已关闭"
    path, source = _find_candidate_cv_file(talent_id)
    if path is None:
        return [], "CV附件：未找到 cv_path 或 candidates/<tid>/cv 文件，已跳过"

    if not path.is_file():
        return [], "CV附件：文件不存在，已跳过 ({})".format(path)

    size = path.stat().st_size
    if size > _CALENDAR_ATTACHMENT_LIMIT_BYTES:
        return [], "CV附件：文件超过 20MB，已跳过 ({:.1f}MB)".format(size / 1024.0 / 1024.0)

    try:
        token = _upload_calendar_attachment(client, calendar_id, path)
    except Exception as e:
        return [], "CV附件：上传失败，已跳过 ({}: {})".format(type(e).__name__, str(e)[:160])

    attachment = Attachment.builder() \
        .file_token(token) \
        .name(path.name) \
        .file_size(str(size)) \
        .build()
    return [attachment], "CV附件：已上传并挂载 ({}, source={})".format(path.name, source)


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
    attach_cv=True,
):
    # type: (str, str, int, str, str, str, Optional[list], Optional[int], bool) -> str
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
      attach_cv: 默认 True。创建面试日程时尽力把 talents.cv_path 指向的 CV
        上传为日程附件；缺失 / 上传失败不阻断建日历，只在返回消息中提示。
    """
    if side_effects_disabled():
        extra_count = len(extra_attendee_open_ids or [])
        return "测试模式：已跳过创建日历事件 talent_id={} round={} time={} extras={} duration={} attach_cv={}".format(
            talent_id, round_num, interview_time, extra_count, duration_minutes, bool(attach_cv))

    feishu = _cfg.get("feishu")
    boss_open_id = (feishu.get("boss_open_id", "") or "").strip()
    # Polaris 是固定日程安排者 / 运营观察者，不等同于任一面试官角色。
    polaris_open_id = (
        feishu.get("polaris_open_id", "")
        or feishu.get("scheduler_open_id", "")
        or ""
    ).strip()
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
        desc_parts.append("面试地点: 上海市浦东新区杨高中路丁香国际商业中心西塔21楼致邃投资")
    desc_parts.append("\n由 OpenClaw 招聘助手自动创建")

    cv_attachments, cv_status = _prepare_cv_calendar_attachment(
        client, calendar_id, talent_id, enabled=attach_cv)

    event_builder = CalendarEvent.builder() \
        .summary(summary) \
        .description("\n".join(desc_parts)) \
        .start_time(TimeInfo.builder()
            .timestamp(start_ts)
            .timezone("Asia/Shanghai")
            .build()) \
        .end_time(TimeInfo.builder()
            .timestamp(end_ts)
            .timezone("Asia/Shanghai")
            .build()) \
        .need_notification(False)
    if cv_attachments:
        event_builder = event_builder.attachments(cv_attachments)

    create_req = CreateCalendarEventRequest.builder() \
        .calendar_id(calendar_id) \
        .request_body(event_builder.build()) \
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
    msg += "\n  - {}".format(cv_status)

    # 收集所有 attendee：boss + Polaris（固定日程安排者）+ extras，去重 + 跳占位符。
    # 一面 extras 应为路由出的真实面试官；二面通常不传 extras。
    attendee_open_ids = []
    seen = set()
    if boss_open_id and not boss_open_id.startswith(_PLACEHOLDER_PREFIX):
        attendee_open_ids.append(("boss", boss_open_id))
        seen.add(boss_open_id)
    skipped_placeholders = []
    if polaris_open_id:
        if polaris_open_id.startswith(_PLACEHOLDER_PREFIX):
            skipped_placeholders.append(polaris_open_id)
        elif polaris_open_id not in seen:
            attendee_open_ids.append(("polaris", polaris_open_id))
            seen.add(polaris_open_id)
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
                if tag == "boss":
                    tags.append("老板")
                elif tag == "polaris":
                    tags.append("Polaris（日程安排者）")
                else:
                    tags.append("面试官")
            msg += "\n  - 已成功邀请参与者：{}（共 {} 人）".format(
                "、".join(tags), len(attendee_open_ids))
        else:
            msg += "\n  - 邀请参与者失败: {}".format(att_resp.msg)

    if skipped_placeholders:
        msg += "\n  - ⚠️ 跳过 {} 个占位符 open_id（未配置真实账号）".format(
            len(skipped_placeholders))

    # v3.8.5：之前这里 except Exception: pass 把 "DB calendar_event_id 写不进去"
    # 静默吞掉，后续 reschedule 找不到旧 event_id 就无法 delete + 重建。现在
    # 改成"日历事件已建（不能撤）+ DB 写失败 → 立刻推飞书 critical 告警，让老板
    # / 维护者手动跑 talent.cmd_update --round{N}-calendar-event-id 补救"。
    # 不 raise 是为了让 caller 仍能拿到 msg 返回值（caller 需要展示日历已建）。
    try:
        from lib import talent_db as _tdb
        if _tdb._is_enabled():
            _tdb.update_calendar_event_id(talent_id, round_num, event_id)
    except Exception as e:
        warn_line = (
            "🔥 [feishu] 日历事件已建（event_id={ev}），但 DB calendar_event_id "
            "写入失败：talent_id={tid} round={r} err={t}: {m}\n"
            "→ 手动补救：talent.cmd_update --talent-id {tid} "
            "--round{r}-calendar-event-id {ev}"
        ).format(ev=event_id, tid=talent_id, r=round_num,
                 t=type(e).__name__, m=str(e)[:200])
        print(warn_line, file=sys.stderr)
        try:
            send_text(warn_line)
        except Exception:
            pass  # 二级补救告警失败只能 stderr 哀嚎，不嵌套异常

    return msg
