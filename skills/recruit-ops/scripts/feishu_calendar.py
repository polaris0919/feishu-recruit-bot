#!/usr/bin/env python3
"""
飞书日历集成：在 OpenClaw 日历上创建二面事件并邀请老板（attendee）。

依赖：
  - FEISHU_APP_ID / FEISHU_APP_SECRET  （或从 ~/.openclaw/openclaw.json 读取）
  - FEISHU_BOSS_OPEN_ID                （老板的 open_id，格式 ou_xxx）
  - FEISHU_CALENDAR_ID                 （机器人的日历 ID，默认内置）

用法示例（直接调用）：
  python3 feishu_calendar.py \
    --talent-id test001 \
    --round2-time "2026-03-20 14:00" \
    --interviewer "老板A" \
    --candidate-email "xxx@example.com"
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from typing import Optional

# ─── 常量 ──────────────────────────────────────────────────────────────────────

OPENCLAW_CONFIG = os.path.expanduser("~/.openclaw/openclaw.json")

# 机器人自己的 OpenClaw 日历 ID（已通过 API 确认）
DEFAULT_CALENDAR_ID = "feishu.cn_vPEnd4yYlOvbjzLuY9Ye2e@group.calendar.feishu.cn"

FEISHU_API = "https://open.feishu.cn/open-apis"


# ─── 认证 ──────────────────────────────────────────────────────────────────────

def get_app_credentials():
    # type: () -> tuple
    """从环境变量或 openclaw.json 获取 app_id / app_secret。"""
    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    if app_id and app_secret:
        return app_id, app_secret

    try:
        with open(OPENCLAW_CONFIG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        acct = cfg["channels"]["feishu"]["accounts"]["feishubot"]
        return acct["appId"], acct["appSecret"]
    except Exception as e:
        raise RuntimeError("无法读取飞书应用凭据，请设置 FEISHU_APP_ID / FEISHU_APP_SECRET 环境变量: " + str(e))


def get_tenant_token(app_id, app_secret):
    # type: (str, str) -> str
    """获取 tenant_access_token。"""
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(
        FEISHU_API + "/auth/v3/tenant_access_token/internal",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.loads(r.read())
    if d.get("code") != 0:
        raise RuntimeError("获取 token 失败: " + str(d))
    return d["tenant_access_token"]


# ─── 时间工具 ───────────────────────────────────────────────────────────────────

def parse_time_to_timestamp(time_str, duration_minutes=60):
    # type: (str, int) -> tuple
    """
    将 '2026-03-20 14:00' 解析为 (start_ts, end_ts) Unix 时间戳（CST = UTC+8）。
    """
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y/%m/%d %H:%M"):
        try:
            naive = datetime.strptime(time_str.strip(), fmt)
            # CST = UTC+8，减去 8 小时得到 UTC 时间戳
            utc = naive - timedelta(hours=8)
            start_ts = int((utc - datetime(1970, 1, 1)).total_seconds())
            end_ts = start_ts + duration_minutes * 60
            return str(start_ts), str(end_ts)
        except ValueError:
            continue
    raise ValueError("无法解析时间格式: " + time_str + "，请使用 'YYYY-MM-DD HH:MM'")


# ─── 日历操作 ───────────────────────────────────────────────────────────────────

def feishu_request(token, method, path, body=None):
    # type: (str, str, str, Optional[dict]) -> dict
    """通用飞书 API 请求。"""
    url = FEISHU_API + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def create_calendar_event(token, calendar_id, summary, description, start_ts, end_ts):
    # type: (str, str, str, str, str, str) -> dict
    """在指定日历创建事件。"""
    payload = {
        "summary": summary,
        "description": description,
        "start_time": {"timestamp": start_ts, "timezone": "Asia/Shanghai"},
        "end_time": {"timestamp": end_ts, "timezone": "Asia/Shanghai"},
        "visibility": "default",
        "free_busy_status": "busy",
    }
    return feishu_request(
        token, "POST",
        "/calendar/v4/calendars/{}/events".format(calendar_id),
        payload,
    )


def add_attendee(token, calendar_id, event_id, boss_open_id):
    # type: (str, str, str, str) -> dict
    """
    单独调用 attendees 接口邀请老板，必须带 user_id_type=open_id。
    文档：POST /calendar/v4/calendars/{calendar_id}/events/{event_id}/attendees
    """
    payload = {
        "attendees": [{"type": "user", "user_id": boss_open_id}],
        "need_notification": True,
    }
    return feishu_request(
        token, "POST",
        "/calendar/v4/calendars/{}/events/{}/attendees?user_id_type=open_id".format(
            calendar_id, event_id
        ),
        payload,
    )


# ─── 主逻辑 ────────────────────────────────────────────────────────────────────

def create_round2_event(talent_id, round2_time, interviewer="", candidate_email=""):
    # type: (str, str, str, str) -> str
    """
    创建二面日历事件。返回事件的 app_link（飞书日历直达链接）。
    """
    boss_open_id = os.environ.get("FEISHU_BOSS_OPEN_ID", "").strip()
    # 从 openclaw.json 读取 boss open_id（如未设置环境变量）
    if not boss_open_id:
        try:
            cfg_path = os.path.expanduser("~/.openclaw/openclaw.json")
            with open(cfg_path, "r", encoding="utf-8") as _f:
                _cfg = json.load(_f)
            _acct = _cfg["channels"]["feishu"]["accounts"]["feishubot"]
            boss_open_id = (_acct.get("ownerOpenId") or _acct.get("bossOpenId") or "").strip()
        except Exception:
            pass
    # 兜底：使用已知的 boss open_id
    if not boss_open_id:
        boss_open_id = "ou_f8b858eb86fcb928386e836aa29c18dc"
    calendar_id = os.environ.get("FEISHU_CALENDAR_ID", DEFAULT_CALENDAR_ID).strip()

    app_id, app_secret = get_app_credentials()
    token = get_tenant_token(app_id, app_secret)

    start_ts, end_ts = parse_time_to_timestamp(round2_time)

    summary = "[二面] 候选人 {}".format(talent_id)
    if interviewer:
        summary += "  ×  {}".format(interviewer)

    desc_parts = [
        "talent_id: {}".format(talent_id),
        "面试时间: {}".format(round2_time),
    ]
    if interviewer:
        desc_parts.append("面试官: {}".format(interviewer))
    if candidate_email:
        desc_parts.append("候选人邮箱: {}".format(candidate_email))
    desc_parts.append("\n由 OpenClaw 招聘助手自动创建")

    # Step 1: 创建事件
    result = create_calendar_event(
        token=token,
        calendar_id=calendar_id,
        summary=summary,
        description="\n".join(desc_parts),
        start_ts=start_ts,
        end_ts=end_ts,
    )

    if result.get("code") != 0:
        raise RuntimeError("创建日历事件失败: " + json.dumps(result, ensure_ascii=False))

    event = result["data"]["event"]
    app_link = event.get("app_link", "")
    event_id = event.get("event_id", "")

    msg = "已在飞书日历创建二面事件：{}\n  - 时间: {}\n  - 事件ID: {}".format(summary, round2_time, event_id)
    if app_link:
        msg += "\n  - 直达链接: {}".format(app_link)

    # Step 2: 单独调用 attendees 接口邀请老板（需要 user_id_type=open_id）
    if boss_open_id:
        att_result = add_attendee(token, calendar_id, event_id, boss_open_id)
        att_code = att_result.get("code", -1)
        if att_code == 0:
            msg += "\n  - 已成功邀请老板（{}）为参与者，老板将收到日历邀请通知".format(boss_open_id)
        else:
            msg += "\n  - ⚠ 邀请老板失败（code={}）: {}".format(
                att_code, att_result.get("msg", str(att_result))
            )
    else:
        msg += "\n  ⚠ 未配置 FEISHU_BOSS_OPEN_ID，老板未被邀请，请手动添加"

    return msg


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="创建飞书日历二面事件")
    p.add_argument("--talent-id", required=True)
    p.add_argument("--round2-time", required=True, help="例如: '2026-03-20 14:00'")
    p.add_argument("--interviewer", default="")
    p.add_argument("--candidate-email", default="")
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    args = parse_args(argv)
    try:
        msg = create_round2_event(
            talent_id=args.talent_id,
            round2_time=args.round2_time,
            interviewer=args.interviewer,
            candidate_email=args.candidate_email,
        )
        print(msg)
        return 0
    except Exception as e:
        print("ERROR: " + str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
