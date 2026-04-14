#!/usr/bin/env python3
"""
统一配置中心：所有配置加载逻辑收口到此模块。
一次加载，全局复用，不再让每个模块各自搜索配置文件。
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from recruit_paths import config_candidates, first_existing

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

    if not (feishu_app_id and feishu_app_secret):
        try:
            acct = openclaw["channels"]["feishu"]["accounts"]["feishubot"]
            feishu_app_id = feishu_app_id or acct.get("appId", "")
            feishu_app_secret = feishu_app_secret or acct.get("appSecret", "")
            feishu_boss_open_id = feishu_boss_open_id or acct.get("ownerOpenId", "") or acct.get("bossOpenId", "")
        except (KeyError, TypeError):
            pass

    _cache["feishu"] = {
        "app_id": feishu_app_id,
        "app_secret": feishu_app_secret,
        "boss_open_id": feishu_boss_open_id or "ou_f8b858eb86fcb928386e836aa29c18dc",
        "hr_open_id": feishu_hr_open_id or "ou_06a323aae9f1a208153c1ca0b4c3d281",
        "calendar_id": feishu_calendar_id or "feishu.cn_vPEnd4yYlOvbjzLuY9Ye2e@group.calendar.feishu.cn",
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
