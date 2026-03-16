#!/usr/bin/env python3
"""
直接调用飞书 IM API 发文本消息，绕过 OpenClaw Gateway 中继。
适用于 cron 任务在无 Cursor 连接时主动推送通知。
"""
import json
import os
import sys

try:
    import urllib.request as _urllib_request
    import urllib.error as _urllib_error
except ImportError:
    _urllib_request = None
    _urllib_error = None

FEISHU_BOSS_OPEN_ID = os.environ.get(
    "FEISHU_BOSS_OPEN_ID", "ou_f8b858eb86fcb928386e836aa29c18dc"
)


def _get_feishu_app_credentials():
    """读取飞书 App ID / Secret，优先从 openclaw.json 读取。"""
    # 1. 环境变量
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    if app_id and app_secret:
        return app_id, app_secret

    # 2. openclaw.json（与 feishu_calendar.py 共享同一来源）
    openclaw_config = os.path.expanduser("~/.openclaw/openclaw.json")
    try:
        with open(openclaw_config, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        acct = cfg["channels"]["feishu"]["accounts"]["feishubot"]
        return acct["appId"], acct["appSecret"]
    except Exception:
        pass

    # 3. 独立配置文件
    _here = os.path.dirname(os.path.abspath(__file__))
    config_paths = [
        os.path.join(_here, "feishu-config.json"),
        os.path.expanduser("~/.openclaw/feishu-config.json"),
        os.path.expanduser("~/.openclaw/workspace/feishu-config.json"),
    ]
    for p in config_paths:
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    d = json.load(f)
                app_id = d.get("app_id") or d.get("FEISHU_APP_ID") or d.get("appId") or ""
                app_secret = d.get("app_secret") or d.get("FEISHU_APP_SECRET") or d.get("appSecret") or ""
                if app_id and app_secret:
                    return app_id, app_secret
            except Exception:
                continue
    return "", ""


def _get_tenant_access_token():
    app_id, app_secret = _get_feishu_app_credentials()
    if not app_id or not app_secret:
        return None
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, _here)
        import feishu_calendar
        token = feishu_calendar.get_tenant_access_token(app_id, app_secret)
        return token
    except Exception:
        pass
    # 回退：直接调用 API
    try:
        payload = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8")
        req = _urllib_request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with _urllib_request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("tenant_access_token")
    except Exception as e:
        print("[feishu_notify] 获取 token 失败: {}".format(e), file=sys.stderr)
        return None


def send_text(text, open_id=None):
    # type: (str, str) -> bool
    """发送文本消息到指定飞书用户（默认老板）。返回是否发送成功。"""
    if not text or not text.strip():
        return True
    open_id = open_id or FEISHU_BOSS_OPEN_ID
    token = _get_tenant_access_token()
    if not token:
        print("[feishu_notify] 无法获取 token，消息未发送", file=sys.stderr)
        return False
    payload = json.dumps({
        "receive_id": open_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}),
    }).encode("utf-8")
    try:
        req = _urllib_request.Request(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer {}".format(token),
            },
        )
        with _urllib_request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("code") == 0:
                return True
            print("[feishu_notify] API 返回错误: {}".format(result), file=sys.stderr)
            return False
    except Exception as e:
        print("[feishu_notify] 发送失败: {}".format(e), file=sys.stderr)
        return False
