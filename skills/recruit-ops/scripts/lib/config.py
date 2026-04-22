#!/usr/bin/env python3
"""
统一配置中心：所有配置加载逻辑收口到此模块。
一次加载，全局复用，不再让每个模块各自搜索配置文件。
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from lib.recruit_paths import config_candidates, first_existing

_cache = {}  # type: Dict[str, Any]


def _load_json(path):
    # type: (Path) -> dict
    with open(str(path), "r", encoding="utf-8") as f:
        return json.load(f)


def _find_and_load(filename):
    # type: (str) -> dict
    p = first_existing(config_candidates(filename))
    if p:
        try:
            return _load_json(p)
        except Exception:
            pass
    return {}


def _ensure_loaded():
    if _cache:
        return
    openclaw = _find_and_load("openclaw.json")
    db_cfg = _find_and_load("talent-db-config.json")
    email_cfg = _find_and_load("recruit-email-config.json")
    dashscope_cfg = _find_and_load("dashscope-config.json")
    smtp_cfg = _find_and_load("email-send-config.json")

    # --- DB ---
    _cache["db"] = {
        "host": db_cfg.get("TALENT_DB_HOST") or os.environ.get("TALENT_DB_HOST") or "127.0.0.1",
        "port": int(db_cfg.get("TALENT_DB_PORT") or os.environ.get("TALENT_DB_PORT") or "5432"),
        "dbname": db_cfg.get("TALENT_DB_NAME") or os.environ.get("TALENT_DB_NAME") or "recruit",
        "user": db_cfg.get("TALENT_DB_USER") or os.environ.get("TALENT_DB_USER") or "recruit_app",
        "password": (
            db_cfg.get("TALENT_DB_PASSWORD")
            or os.environ.get("TALENT_DB_PASSWORD")
            or ""
        ),
    }

    # --- Feishu ---
    feishu_app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    feishu_app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    feishu_boss_open_id = os.environ.get("FEISHU_BOSS_OPEN_ID", "").strip()
    feishu_hr_open_id = os.environ.get("FEISHU_HR_OPEN_ID", "").strip()
    feishu_calendar_id = os.environ.get("FEISHU_CALENDAR_ID", "").strip()

    # v3.5.7: 三位一面面试官的飞书 open_id（master=硕士面试官，
    # bachelor=本科面试官，cpp=C++ 面试官）。
    # 用法：intake.cmd_route_interviewer 根据 talents.{education,has_cpp}
    # 在三者之间路由（cpp_first 优先级，详见 AGENT_RULES §5.11）。
    # 配置缺失时 cmd_route_interviewer 直接报 config_error，由 chain
    # 转 ASK_HR 分支推飞书；不会 fallback 到 boss/任意人，避免漏邀请。
    feishu_interviewer_master_open_id = os.environ.get(
        "FEISHU_INTERVIEWER_MASTER_OPEN_ID", "").strip()
    feishu_interviewer_bachelor_open_id = os.environ.get(
        "FEISHU_INTERVIEWER_BACHELOR_OPEN_ID", "").strip()
    feishu_interviewer_cpp_open_id = os.environ.get(
        "FEISHU_INTERVIEWER_CPP_OPEN_ID", "").strip()

    try:
        acct = openclaw["channels"]["feishu"]["accounts"]["feishubot"]
    except (KeyError, TypeError):
        acct = {}

    feishu_app_id = feishu_app_id or acct.get("appId", "")
    feishu_app_secret = feishu_app_secret or acct.get("appSecret", "")
    feishu_boss_open_id = feishu_boss_open_id or acct.get("ownerOpenId", "") or acct.get("bossOpenId", "")
    feishu_hr_open_id = feishu_hr_open_id or acct.get("hrOpenId", "")
    feishu_calendar_id = feishu_calendar_id or acct.get("calendarId", "")
    feishu_interviewer_master_open_id = (
        feishu_interviewer_master_open_id
        or acct.get("interviewerMasterOpenId", "")
    )
    feishu_interviewer_bachelor_open_id = (
        feishu_interviewer_bachelor_open_id
        or acct.get("interviewerBachelorOpenId", "")
    )
    feishu_interviewer_cpp_open_id = (
        feishu_interviewer_cpp_open_id
        or acct.get("interviewerCppOpenId", "")
    )

    # v3.5.7 占位符：当没有任何来源（env/openclaw config）配齐时，
    # 写入显眼的占位字符串。lib.feishu / cmd_route_interviewer 看到
    # 以 "ou_PLACEHOLDER_" 开头的 open_id 时会拒绝真实推送（fail closed），
    # 避免在配齐之前误发到真人账号。
    _PLACEHOLDER_PREFIX = "ou_PLACEHOLDER_"
    feishu_interviewer_master_open_id = (
        feishu_interviewer_master_open_id
        or _PLACEHOLDER_PREFIX + "INTERVIEWER_MASTER"
    )
    feishu_interviewer_bachelor_open_id = (
        feishu_interviewer_bachelor_open_id
        or _PLACEHOLDER_PREFIX + "INTERVIEWER_BACHELOR"
    )
    feishu_interviewer_cpp_open_id = (
        feishu_interviewer_cpp_open_id
        or _PLACEHOLDER_PREFIX + "INTERVIEWER_CPP"
    )

    _cache["feishu"] = {
        "app_id": feishu_app_id,
        "app_secret": feishu_app_secret,
        # Fail closed: missing IDs should block side effects instead of
        # silently sending to a hardcoded production account.
        "boss_open_id": feishu_boss_open_id,
        "hr_open_id": feishu_hr_open_id,
        "calendar_id": feishu_calendar_id,
        # v3.5.7：三位一面面试官（详见 AGENT_RULES §5.11）。
        "interviewer_master_open_id":   feishu_interviewer_master_open_id,
        "interviewer_bachelor_open_id": feishu_interviewer_bachelor_open_id,
        "interviewer_cpp_open_id":      feishu_interviewer_cpp_open_id,
    }

    # --- DashScope LLM ---
    ds_key = (
        os.environ.get("DASHSCOPE_API_KEY", "").strip()
        or dashscope_cfg.get("DASHSCOPE_API_KEY", "").strip()
        or dashscope_cfg.get("dashscope_api_key", "").strip()
        or (openclaw.get("llm") or {}).get("api_key", "").strip()
    )
    _cache["dashscope"] = {
        "api_key": ds_key,
        "model": os.environ.get("DASHSCOPE_MODEL", "qwen3-max-2026-01-23"),
        "url": "https://coding.dashscope.aliyuncs.com/v1/chat/completions",
    }

    # --- Email (IMAP) ---
    _cache["email_imap"] = {
        "host": email_cfg.get("RECRUIT_EXAM_IMAP_HOST") or os.environ.get("RECRUIT_EXAM_IMAP_HOST", ""),
        "user": email_cfg.get("RECRUIT_EXAM_IMAP_USER") or os.environ.get("RECRUIT_EXAM_IMAP_USER", ""),
        "password": email_cfg.get("RECRUIT_EXAM_IMAP_PASS") or os.environ.get("RECRUIT_EXAM_IMAP_PASS", ""),
    }

    # --- Email (SMTP, via email-send skill) ---
    _cache["email_smtp"] = smtp_cfg


def get(section, key=None):
    # type: (str, Optional[str]) -> Any
    _ensure_loaded()
    sect = _cache.get(section, {})
    if key is None:
        return sect
    return sect.get(key)


def db_enabled():
    # type: () -> bool
    if (os.environ.get("RECRUIT_DISABLE_DB") or "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        return False
    return bool((get("db", "password") or "").strip())


def db_conn_params():
    # type: () -> dict
    cfg = get("db")
    return {
        "host": cfg["host"],
        "port": cfg["port"],
        "dbname": cfg["dbname"],
        "user": cfg["user"],
        "password": cfg["password"],
        "connect_timeout": 10,
    }


def reload():
    """Force reload all config (useful for tests)."""
    _cache.clear()
    _ensure_loaded()
