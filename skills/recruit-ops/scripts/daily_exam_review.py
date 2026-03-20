#!/usr/bin/env python3
"""
邮件自动扫描：每8小时被 cron 触发，检查笔试回复 + 一面/二面时间确认邮件。
新邮件发现时，生成报告并通过飞书推送给 Boss。

手动触发：python3 daily_exam_review.py [--auto]
  --auto: cron 模式（静默；无新邮件不输出）
"""
from __future__ import print_function

import argparse
import imaplib
import json
import os
import re
import subprocess
import sys
import time
import email as email_lib
from email.header import decode_header
from datetime import datetime
from typing import List, Optional, Dict, Any

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# ─── LLM 配置（DashScope，直连，绕过 Gateway）──────────────────────────────────
_DASHSCOPE_URL = "https://coding.dashscope.aliyuncs.com/v1/chat/completions"
_DASHSCOPE_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
_LLM_MODEL = os.environ.get("DASHSCOPE_MODEL", "qwen3-max-2026-01-23")


def _load_dashscope_key():
    """若环境变量未设置，从 openclaw.json 或配置文件中读取 API Key。"""
    global _DASHSCOPE_KEY
    if _DASHSCOPE_KEY:
        return
    config_paths = [
        os.path.expanduser("~/.openclaw/dashscope-config.json"),
        os.path.expanduser("~/.openclaw/openclaw.json"),
    ]
    for path in config_paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            key = (
                data.get("DASHSCOPE_API_KEY")
                or data.get("dashscope_api_key")
                or (data.get("llm") or {}).get("api_key")
                or ""
            )
            if key:
                _DASHSCOPE_KEY = key.strip()
                return
        except Exception:
            continue


_EMAIL_CONFIG_LOADED = False


def _load_email_config():
    """从配置文件加载 IMAP 环境变量（若环境变量尚未设置）。"""
    global _EMAIL_CONFIG_LOADED
    if _EMAIL_CONFIG_LOADED:
        return
    _EMAIL_CONFIG_LOADED = True
    if os.environ.get("RECRUIT_EXAM_IMAP_PASS", "").strip():
        return
    config_paths = [
        os.path.join(_HERE, "recruit-email-config.json"),
        os.path.expanduser("~/.openclaw/recruit-email-config.json"),
        os.path.expanduser("~/.openclaw/workspace/recruit-email-config.json"),
    ]
    for path in config_paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                if k.startswith("RECRUIT_EXAM_") and v:
                    os.environ.setdefault(k, str(v).strip())
            if os.environ.get("RECRUIT_EXAM_IMAP_PASS", "").strip():
                return
        except Exception:
            continue
    # 兜底：从 email-daily-summary-config.json 读取 IMAP 配置
    fallback_paths = [
        os.path.expanduser("~/.openclaw/email-daily-summary-config.json"),
    ]
    for path in fallback_paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            imap = data.get("imap", {})
            if imap.get("host"):
                os.environ.setdefault("RECRUIT_EXAM_IMAP_HOST", imap["host"])
            if imap.get("port"):
                os.environ.setdefault("RECRUIT_EXAM_IMAP_PORT", str(imap["port"]))
            if imap.get("username"):
                os.environ.setdefault("RECRUIT_EXAM_IMAP_USER", imap["username"])
            if imap.get("password"):
                os.environ.setdefault("RECRUIT_EXAM_IMAP_PASS", imap["password"])
            return
        except Exception:
            continue


def _get_env(key, default=""):
    _load_email_config()
    return (os.environ.get(key) or "").strip() or default


def _llm_analyze_reply(email_body, round_label="一面"):
    # type: (str, str) -> Dict[str, Any]
    """
    调用 DashScope LLM 分析候选人邮件回复意图。
    返回 {"intent": "confirm|reschedule|unknown", "new_time": str|None, "summary": str}
    """
    _load_dashscope_key()
    if not _DASHSCOPE_KEY:
        return {"intent": "unknown", "new_time": None, "summary": "（LLM未配置，无法分析）"}

    prompt = (
        "你是一个招聘助手，请分析以下候选人邮件回复，判断候选人对{}邀请的意图。\n\n"
        "邮件内容：\n{}\n\n"
        "请用JSON格式回复，包含以下字段：\n"
        "- intent: 只能是 confirm（确认参加）/ reschedule（要求改期）/ unknown（意图不明）\n"
        "- new_time: 若候选人提出了新时间，填写时间字符串（如'2026-04-15 15:00'），否则填 null\n"
        "- summary: 一句话总结候选人意图（中文，20字以内）\n\n"
        "注意：\n"
        "- 候选人表示「可以」「没问题」「确认」「OK」「好的」等均视为 confirm\n"
        "- 候选人表示「不方便」「调整」「改一下」「换个时间」等均视为 reschedule\n"
        "- 候选人提出具体新时间，intent 必须是 reschedule\n"
        "只返回 JSON，不要其他内容。"
    ).format(round_label, email_body[:1500])

    try:
        import urllib.request as _req
        payload = json.dumps({
            "model": _LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        }).encode("utf-8")
        request = _req.Request(
            _DASHSCOPE_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer {}".format(_DASHSCOPE_KEY),
            },
        )
        with _req.urlopen(request, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        content = result["choices"][0]["message"]["content"].strip()
        # 去除 Markdown 代码块包裹
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        parsed = json.loads(content)
        return {
            "intent": parsed.get("intent", "unknown"),
            "new_time": parsed.get("new_time"),
            "summary": parsed.get("summary", ""),
        }
    except Exception as e:
        return {"intent": "unknown", "new_time": None, "summary": "LLM分析失败: {}".format(str(e)[:50])}


def connect_imap():
    """连接 IMAP 邮箱，含 30 秒超时。"""
    import socket
    _load_email_config()
    host = _get_env("RECRUIT_EXAM_IMAP_HOST")
    port = int(_get_env("RECRUIT_EXAM_IMAP_PORT", "993"))
    user = _get_env("RECRUIT_EXAM_IMAP_USER")
    pwd = _get_env("RECRUIT_EXAM_IMAP_PASS")

    if not host or not user or not pwd:
        raise ValueError("RECRUIT_EXAM_IMAP_HOST / IMAP_USER / IMAP_PASS 未配置")

    socket.setdefaulttimeout(30)
    try:
        imap = imaplib.IMAP4_SSL(host, port)
        imap.login(user, pwd)
        return imap
    finally:
        socket.setdefaulttimeout(None)


def _decode_mime_header(value):
    parts = []
    for chunk, charset in decode_header(value):
        if isinstance(chunk, bytes):
            cs = charset or "utf-8"
            if cs.lower() in ("unknown-8bit", "unknown"):
                cs = "utf-8"
            try:
                parts.append(chunk.decode(cs, errors="replace"))
            except (LookupError, UnicodeDecodeError):
                parts.append(chunk.decode("utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


def _extract_body(msg):
    body_parts = []
    for part in msg.walk():
        ct = part.get_content_type()
        disposition = str(part.get("Content-Disposition") or "")
        if ct == "text/plain" and "attachment" not in disposition:
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                if charset.lower() in ("unknown-8bit", "unknown"):
                    charset = "utf-8"
                body_parts.append(payload.decode(charset, errors="replace"))
    return "\n".join(body_parts)


_CODE_EXTS = {".py", ".ipynb", ".r", ".sql", ".java", ".cpp", ".go", ".js", ".ts",
              ".c", ".h", ".cs", ".rb", ".scala", ".kt", ".m", ".sh", ".txt", ".md"}


def _extract_code_from_archive(payload, archive_name):
    """
    解压 zip 或 rar 压缩包，返回 (inner_file_list, code_text)。
    inner_file_list: [{"filename": str, "size": int, "is_text": bool}]
    """
    import io
    inner_files = []
    code_parts = []
    ext = os.path.splitext(archive_name)[1].lower()

    try:
        if ext == ".zip":
            import zipfile
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                for name in zf.namelist():
                    if name.endswith("/"):
                        continue
                    fext = os.path.splitext(name)[1].lower()
                    data = zf.read(name)
                    is_text = fext in _CODE_EXTS
                    inner_files.append({"filename": name, "size": len(data), "is_text": is_text})
                    if is_text:
                        try:
                            text = data.decode("utf-8", errors="replace")
                            code_parts.append("# File: {}\n{}".format(name, text[:4000]))
                        except Exception:
                            pass

        elif ext == ".rar":
            try:
                import tempfile
                from unrar.cffi import rarfile as rar_mod
                with tempfile.NamedTemporaryFile(suffix=".rar", delete=False) as tmp:
                    tmp.write(payload)
                    tmp_path = tmp.name
                try:
                    rf = rar_mod.RarFile(tmp_path)
                    for info in rf.infolist():
                        name = info.filename
                        # 跳过目录项（尾斜杠 或 无扩展名的纯目录名）
                        if name.endswith("/") or name.endswith("\\"):
                            continue
                        fext = os.path.splitext(name)[1].lower()
                        if not fext:
                            continue
                        try:
                            data = rf.read(info)
                        except Exception:
                            continue
                        is_text = fext in _CODE_EXTS
                        inner_files.append({"filename": name, "size": len(data), "is_text": is_text})
                        if is_text:
                            try:
                                text = data.decode("utf-8", errors="replace")
                                code_parts.append("# File: {}\n{}".format(name, text[:4000]))
                            except Exception:
                                pass
                finally:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass
            except ImportError:
                pass
    except Exception:
        pass

    return inner_files, "\n\n".join(code_parts)


def _extract_attachment_info(msg):
    """
    提取所有附件信息，返回 (attachment_info_list, code_text)。
    attachment_info_list: [{"filename": str, "size": int, "is_text": bool}]
    code_text: 所有可读文本附件合并（用于代码分析，每个文件最多 4000 字符）
    自动解压 .zip / .rar，读取其中的代码文件。
    """
    attachment_info_list = []
    code_parts = []
    for part in msg.walk():
        disposition = str(part.get("Content-Disposition") or "")
        if "attachment" not in disposition:
            continue
        fname = part.get_filename() or ""
        if fname:
            try:
                fname = _decode_mime_header(fname)
            except Exception:
                pass
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        size = len(payload)

        fext = os.path.splitext(fname)[1].lower()

        # 压缩包：自动解压并读取内部代码文件
        if fext in (".zip", ".rar"):
            inner_files, archive_code = _extract_code_from_archive(payload, fname)
            attachment_info_list.append({
                "filename": fname,
                "size": size,
                "is_text": False,
                "archive_contents": [f["filename"] for f in inner_files],
            })
            if archive_code:
                code_parts.append(
                    "# Archive: {}\n{}".format(fname, archive_code)
                )
            continue

        is_text = False
        try:
            charset = part.get_content_charset() or "utf-8"
            if charset.lower() in ("unknown-8bit", "unknown"):
                charset = "utf-8"
            text = payload.decode(charset, errors="replace")
            is_text = True
            code_parts.append("# File: {}\n{}".format(fname, text[:4000]))
        except Exception:
            pass
        attachment_info_list.append({
            "filename": fname,
            "size": size,
            "is_text": is_text,
        })
    code_text = "\n\n".join(code_parts)
    return attachment_info_list, code_text


def _lookup_exam_sent_at_from_sent(imap, candidate_email):
    """
    从已发送文件夹查找发给指定候选人的笔试邀请邮件，返回最早那封的实际发送时间字符串。
    找不到时返回 None。
    """
    if not imap or not candidate_email:
        return None
    import re as _re
    import email as _email_lib

    # 提取纯邮箱地址（小写）
    m = _re.search(r'<([^>]+)>', candidate_email)
    addr = (m.group(1).strip() if m else candidate_email.strip()).lower()

    exam_subj_kws = ["笔试邀请", "笔试通知", "exam"]
    found = []

    try:
        imap.select('"Sent Messages"')
        status, data = imap.search(None, "ALL")
        if status != "OK" or not data or not data[0]:
            return None

        msg_ids = data[0].split()
        # 只看最近 80 封，避免遍历过多
        for mid in msg_ids[-80:]:
            try:
                status, raw = imap.fetch(mid, "(RFC822.HEADER)")
                if status != "OK":
                    continue
                msg = _email_lib.message_from_bytes(raw[0][1])
                to_header = _decode_mime_header(msg.get("To") or "").lower()
                if addr not in to_header:
                    continue
                subject = _decode_mime_header(msg.get("Subject") or "")
                if not any(kw in subject or kw in subject.lower() for kw in exam_subj_kws):
                    continue
                date_str = msg.get("Date") or ""
                if date_str:
                    found.append(date_str)
            except Exception:
                continue
    except Exception:
        pass
    finally:
        try:
            imap.select("INBOX")
        except Exception:
            pass

    if not found:
        return None
    # 返回最早那封（解析后排序，取最小值）
    import email.utils as _eu
    def _parse_ts(s):
        try:
            return _eu.parsedate_to_datetime(s)
        except Exception:
            return None
    parsed = [(s, _parse_ts(s)) for s in found]
    parsed = [(s, dt) for s, dt in parsed if dt is not None]
    if not parsed:
        return found[0]
    parsed.sort(key=lambda x: x[1])
    return parsed[0][0]


def _lookup_candidate_by_exam_id(exam_id, tdb):
    """根据 exam_id 从数据库查找候选人，返回候选人 dict 或 None。"""
    if not exam_id or not tdb:
        return None
    try:
        state = tdb.load_state_from_db()
        candidates = state.get("candidates") or {}
        for tid, cand in candidates.items():
            if cand.get("exam_id") == exam_id:
                return dict(cand, talent_id=tid)
    except Exception:
        pass
    return None


def _lookup_candidate_by_email(sender_email, tdb):
    """根据发件人邮箱从数据库查找候选人（兜底匹配），返回候选人 dict 或 None。"""
    if not sender_email or not tdb:
        return None
    # 从 "Name <addr@domain>" 提取纯地址
    m = re.search(r'<([^>]+)>', sender_email)
    addr = m.group(1).strip().lower() if m else sender_email.strip().lower()
    try:
        state = tdb.load_state_from_db()
        candidates = state.get("candidates") or {}
        for tid, cand in candidates.items():
            db_email = (cand.get("candidate_email") or "").strip().lower()
            if db_email and db_email == addr:
                return dict(cand, talent_id=tid)
    except Exception:
        pass
    return None


def scan_new_replies(auto_mode=False):
    """
    扫描收件箱中未读的笔试回复邮件。
    返回已处理的邮件列表（每项包含预审结果 prereview_result）。
    """
    import re
    import exam_prereview as _prereview

    try:
        import talent_db as _tdb
        db_enabled = _tdb._is_enabled()
    except Exception:
        _tdb = None
        db_enabled = False

    processed_ids = set()
    if db_enabled:
        try:
            processed_ids = _tdb.get_processed_email_ids()
        except Exception:
            pass

    try:
        imap = connect_imap()
    except ValueError as e:
        if not auto_mode:
            print("IMAP 未配置，无法扫描邮件：{}".format(e))
        return []
    except Exception as e:
        if not auto_mode:
            print("IMAP 连接失败：{}".format(e))
        return []

    results = []
    try:
        imap.select("INBOX")
        status, data = imap.search(None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            return results

        msg_ids = data[0].split()
        for mid in msg_ids[-20:]:  # 最多处理最新 20 封
            try:
                status, raw = imap.fetch(mid, "(RFC822)")
                if status != "OK":
                    continue
                raw_bytes = raw[0][1]
                msg = email_lib.message_from_bytes(raw_bytes)

                msg_id_header = (msg.get("Message-ID") or "").strip()
                if msg_id_header in processed_ids:
                    continue

                subject = _decode_mime_header(msg.get("Subject") or "")
                sender = _decode_mime_header(msg.get("From") or "")
                date_str = msg.get("Date") or ""
                body = _extract_body(msg)
                attachment_info_list, code_text = _extract_attachment_info(msg)

                # 过滤系统退信 / 非笔试邮件
                skip_keywords = [
                    "postmaster", "mailer-daemon", "退信", "undelivered",
                    "delivery failure", "mail delivery", "undeliverable",
                    "auto-reply", "out of office", "自动回复",
                ]
                subject_lower = subject.lower()
                sender_lower = sender.lower()
                if any(kw in subject_lower or kw in sender_lower for kw in skip_keywords):
                    continue

                # 过滤回复面试邀请的邮件（主题含"面试通知"/"一面"/"二面"且无附件）
                interview_reply_kws = ["面试通知", "一面邀请", "二面", "round1", "round2",
                                       "interview invitation"]
                if (any(kw in subject_lower for kw in interview_reply_kws)
                        and not attachment_info_list):
                    continue

                exam_keywords = ["exam-", "笔试邀请", "笔试通知", "笔试", "代码", "作业", "题目", "submission"]
                content_text = (subject + " " + body + " " + code_text).lower()
                # 附件须为代码/文档类型才算笔试相关（排除 inline 图片）
                code_ext = (".py", ".ipynb", ".zip", ".rar", ".pdf", ".docx", ".doc",
                            ".xlsx", ".xls", ".txt", ".java", ".cpp", ".c", ".js", ".ts",
                            ".r", ".m", ".sql", ".csv")
                has_code_attachment = any(
                    a["filename"].lower().endswith(code_ext)
                    for a in attachment_info_list
                )
                is_exam_related = (
                    any(kw in content_text for kw in exam_keywords)
                    or has_code_attachment
                )
                if not is_exam_related:
                    continue

                # 提取 exam_id
                exam_match = re.search(r"exam-([a-zA-Z0-9_-]+)", subject + " " + body)
                exam_id = "exam-" + exam_match.group(1) if exam_match else None

                # 查找候选人信息（获取 exam_sent_at）：先按 exam_id，再按发件人邮箱兜底
                cand_info = _lookup_candidate_by_exam_id(exam_id, _tdb if db_enabled else None)
                if not cand_info and db_enabled:
                    cand_info = _lookup_candidate_by_email(sender, _tdb)

                # 从已发送文件夹获取真实发送时间，覆盖 DB 中的 exam_sent_at
                if cand_info:
                    actual_sent_at = _lookup_exam_sent_at_from_sent(imap, cand_info.get("candidate_email", ""))
                    if actual_sent_at:
                        cand_info = dict(cand_info, exam_sent_at=actual_sent_at)

                # 如果找到候选人，但该候选人不在笔试阶段，说明这封邮件是面试回复邮件
                # 应由 scan_round1/2_confirmations 处理，笔试扫描直接跳过，避免混淆
                if cand_info:
                    cand_stage = cand_info.get("stage", "")
                    exam_stages = {"EXAM_SENT", "EXAM_REVIEWING", "EXAM_REVIEWED"}
                    if cand_stage not in exam_stages:
                        # 非笔试阶段的候选人来信，不作为笔试处理
                        if msg_id_header and db_enabled and _tdb:
                            try:
                                _tdb.mark_emails_processed(
                                    [(msg_id_header, cand_info.get("talent_id", ""))]
                                )
                            except Exception:
                                pass
                        continue

                # 运行预审
                email_data = {
                    "sender": sender,
                    "subject": subject,
                    "date": date_str,
                    "body_text": body,
                    "code_text": code_text,
                    "attachment_info_list": attachment_info_list,
                }
                prereview_result = _prereview.run_prereview(email_data, cand_info or {})

                result = {
                    "message_id": msg_id_header,
                    "sender": sender,
                    "subject": subject,
                    "date": date_str,
                    "exam_id": exam_id,
                    "candidate_info": cand_info,
                    "prereview": prereview_result,
                }
                results.append(result)

                # 标记已处理 + 写预审结果到 DB
                if db_enabled and _tdb:
                    try:
                        if msg_id_header:
                            # 优先用 talent_id，其次用 exam_id，都没有才用空串
                            proc_talent_id = (
                                (cand_info.get("talent_id") if cand_info else None)
                                or exam_id
                                or ""
                            )
                            _tdb.mark_emails_processed([(msg_id_header, proc_talent_id)])
                    except Exception:
                        pass
                    if cand_info and cand_info.get("talent_id"):
                        try:
                            _tdb.save_exam_prereview(
                                cand_info["talent_id"],
                                prereview_result["score"],
                                prereview_result["db_summary"],
                            )
                        except Exception:
                            pass

            except Exception as e:
                if not auto_mode:
                    print("处理邮件 {} 失败: {}".format(mid, e))
                continue
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    return results


def format_report(result):
    """格式化单封邮件的飞书推送报告，使用预审模块生成的完整报告。"""
    prereview = result.get("prereview")
    if prereview and prereview.get("report_text"):
        return prereview["report_text"]

    # 降级：没有预审结果时的简单格式
    lines = [
        "📧 新笔试回复",
        "- 发件人: {}".format(result.get("sender", "")),
        "- 主题: {}".format(result.get("subject", "")),
        "- 时间: {}".format(result.get("date", "")[:25] if result.get("date") else ""),
    ]
    if result.get("exam_id"):
        lines.append("- 笔试 ID: {}".format(result["exam_id"]))
    return "\n".join(lines)


def _scan_interview_confirmations(round_num, auto_mode=False):
    # type: (int, bool) -> List[Dict[str, Any]]
    """
    扫描一面（round_num=1）或二面（round_num=2）时间确认邮件。
    对每封回信用 LLM 分析意图，返回结果列表。
    """
    try:
        import talent_db as _tdb
        db_enabled = _tdb._is_enabled()
    except Exception:
        _tdb = None
        db_enabled = False

    if not db_enabled:
        return []

    if round_num == 1:
        pending_list = _tdb.get_round1_pending_confirmations()
        round_label = "一面"
    else:
        pending_list = _tdb.get_round2_pending_confirmations()
        round_label = "二面"

    if not pending_list:
        return []

    processed_ids = set()
    try:
        processed_ids = _tdb.get_processed_email_ids()
    except Exception:
        pass

    try:
        imap = connect_imap()
    except Exception as e:
        if not auto_mode:
            print("IMAP 连接失败（{}确认扫描）：{}".format(round_label, e))
        return []

    results = []
    now_dt = datetime.now()

    try:
        imap.select("INBOX")
        for cand in pending_list:
            talent_id = cand["talent_id"]
            candidate_email = cand["candidate_email"]
            interview_time = cand.get("round1_time" if round_num == 1 else "round2_time") or ""
            invite_sent_at_str = cand.get(
                "round1_invite_sent_at" if round_num == 1 else "round2_invite_sent_at"
            )

            # 超时判断：默认 48 小时（2880 分钟），可通过环境变量覆盖
            TIMEOUT_MINUTES = int(os.environ.get("INTERVIEW_CONFIRM_TIMEOUT_MINUTES", "2880"))
            timed_out = False
            if invite_sent_at_str:
                try:
                    # 兼容 Python 3.6（无 fromisoformat），用 dateutil 解析带时区字符串
                    from dateutil import parser as _dtparser
                    sent_dt = _dtparser.parse(invite_sent_at_str)
                    # 转为 naive datetime（去掉时区）进行比较
                    sent_dt_naive = sent_dt.replace(tzinfo=None)
                    minutes_elapsed = (now_dt - sent_dt_naive).total_seconds() / 60
                    if minutes_elapsed >= TIMEOUT_MINUTES:
                        timed_out = True
                except Exception as _te:
                    pass

            if timed_out:
                results.append({
                    "talent_id": talent_id,
                    "candidate_email": candidate_email,
                    "candidate_name": cand.get("candidate_name"),
                    "interview_time": interview_time,
                    "intent": "timeout",
                    "new_time": None,
                    "summary": "超时{}min未回复，自动默认确认".format(TIMEOUT_MINUTES),
                    "round": round_num,
                })
                continue

            # 搜索该候选人的邮件回复
            try:
                search_criterion = '(FROM "{}" SINCE "01-Jan-2026")'.format(candidate_email)
                status, data = imap.search(None, search_criterion)
                if status != "OK" or not data or not data[0]:
                    continue

                msg_ids = data[0].split()
                for mid in msg_ids[-5:]:  # 最多取最新5封
                    try:
                        status2, raw = imap.fetch(mid, "(RFC822)")
                        if status2 != "OK":
                            continue
                        raw_bytes = raw[0][1]
                        msg = email_lib.message_from_bytes(raw_bytes)

                        msg_id_header = (msg.get("Message-ID") or "").strip()
                        if msg_id_header in processed_ids:
                            continue

                        subject = _decode_mime_header(msg.get("Subject") or "")
                        body = _extract_body(msg)

                        # 跳过系统退信和自动回复
                        skip_kws = ["postmaster", "mailer-daemon", "退信", "auto-reply",
                                    "out of office", "自动回复", "undeliverable"]
                        if any(k in subject.lower() for k in skip_kws):
                            continue

                        # LLM 分析意图
                        analysis = _llm_analyze_reply(body or subject, round_label)

                        results.append({
                            "talent_id": talent_id,
                            "candidate_email": candidate_email,
                            "candidate_name": cand.get("candidate_name"),
                            "interview_time": interview_time,
                            "intent": analysis["intent"],
                            "new_time": analysis["new_time"],
                            "summary": analysis["summary"],
                            "subject": subject,
                            "message_id": msg_id_header,
                            "round": round_num,
                        })

                        # 标记已处理
                        if msg_id_header:
                            try:
                                _tdb.mark_emails_processed([(msg_id_header, talent_id)])
                                processed_ids.add(msg_id_header)
                            except Exception:
                                pass

                        break  # 每位候选人只处理最新一封
                    except Exception:
                        continue
            except Exception:
                continue
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    return results


def format_interview_confirmation_report(item):
    # type: (Dict[str, Any]) -> str
    """格式化一面/二面确认结果为飞书推送文本。"""
    round_num = item.get("round", 2)
    round_label = "一面" if round_num == 1 else "二面"
    talent_id = item["talent_id"]
    name = item.get("candidate_name") or talent_id
    interview_time = item.get("interview_time") or "（未知）"
    intent = item.get("intent", "unknown")
    summary = item.get("summary", "")

    if intent == "timeout":
        confirm_cmd = "cmd_round1_confirm" if round_num == 1 else "cmd_round2_confirm"
        return (
            "[{round_label}确认扫描]\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "候选人：{name}\n"
            "talent_id：{tid}（执行命令时必须用此ID）\n"
            "{round_label}时间：{t}\n"
            "{summary}，建议执行：\n"
            "  python3 {cmd}.py --talent-id {tid} --auto"
        ).format(round_label=round_label, name=name, tid=talent_id, t=interview_time,
                 summary=item.get("summary", "超时未回复"), cmd=confirm_cmd)

    elif intent == "confirm":
        confirm_cmd = "cmd_round1_confirm" if round_num == 1 else "cmd_round2_confirm"
        return (
            "[{round_label}确认扫描]\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "候选人：{name}\n"
            "talent_id：{tid}（执行命令时必须用此ID）\n"
            "{round_label}时间：{t}\n"
            "意图：{summary}\n"
            "建议执行确认：\n"
            "  python3 {cmd}.py --talent-id {tid}"
        ).format(round_label=round_label, name=name, tid=talent_id, t=interview_time,
                 summary=summary, cmd=confirm_cmd)

    elif intent == "reschedule":
        reschedule_cmd = "cmd_round1_reschedule" if round_num == 1 else "cmd_round2_reschedule"
        new_time = item.get("new_time")
        time_hint = ""
        reschedule_hint = ""
        if new_time:
            time_hint = "\n候选人建议时间：{}".format(new_time)
            reschedule_hint = "\n⚠️ 必须使用以下命令（talent_id={tid}，勿混淆其他候选人）：\n  python3 {cmd}.py --talent-id {tid} --time \"{t}\"".format(
                cmd=reschedule_cmd, tid=talent_id, t=new_time)
        else:
            reschedule_hint = "\n⚠️ 候选人未给出具体时间，请联系后执行（talent_id={tid}）：\n  python3 {cmd}.py --talent-id {tid} --time \"YYYY-MM-DD HH:MM\"".format(
                cmd=reschedule_cmd, tid=talent_id)
        return (
            "[{round_label}确认扫描]\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "候选人：{name}\n"
            "talent_id：{tid}（执行命令时必须用此ID）\n"
            "原{round_label}时间：{t}\n"
            "意图：{summary}{time_hint}{reschedule_hint}"
        ).format(round_label=round_label, name=name, tid=talent_id, t=interview_time,
                 summary=summary, time_hint=time_hint, reschedule_hint=reschedule_hint)

    else:
        return (
            "[{round_label}确认扫描]\n"
            "[候选人回信]\n"
            "候选人：{name}（{tid}）\n"
            "{round_label}时间：{t}\n"
            "意图不明确，请人工查阅邮件。\n"
            "摘要：{summary}"
        ).format(round_label=round_label, name=name, tid=talent_id, t=interview_time, summary=summary)


def scan_round1_confirmations(auto_mode=False):
    # type: (bool) -> List[Dict[str, Any]]
    """扫描一面时间确认邮件（ROUND1_SCHEDULING 候选人）。"""
    return _scan_interview_confirmations(round_num=1, auto_mode=auto_mode)


def scan_round2_confirmations(auto_mode=False):
    # type: (bool) -> List[Dict[str, Any]]
    """扫描二面时间确认邮件（ROUND2_SCHEDULED 候选人）。"""
    return _scan_interview_confirmations(round_num=2, auto_mode=auto_mode)


def main(argv=None):
    p = argparse.ArgumentParser(description="邮件自动扫描，检查笔试回复和面试时间确认")
    p.add_argument("--auto", action="store_true", help="cron 模式（无回复静默退出）")
    p.add_argument("--exam-only", action="store_true", help="只扫笔试回复邮件（8h一次）")
    p.add_argument("--interview-confirm-only", action="store_true",
                   help="只扫面试时间确认邮件（6h一次）")
    args = p.parse_args(argv or sys.argv[1:])

    # 决定运行哪些模块
    run_exam = not args.interview_confirm_only   # 默认跑，--interview-confirm-only 时跳过
    run_interview = not args.exam_only           # 默认跑，--exam-only 时跳过

    import feishu_notify as fn

    # 1. 扫描笔试回复（--exam-only 或无特定参数时运行）
    if run_exam:
        exam_results = scan_new_replies(auto_mode=args.auto)
        if exam_results:
            for r in exam_results:
                report = format_report(r)
                if args.auto:
                    fn.send_text(report)
                else:
                    print(report)
                    fn.send_text(report)
            if args.auto:
                print("[email_scan] 共发现 {} 封新笔试回复，已推送飞书。".format(len(exam_results)))
        elif not args.auto:
            print("[email_scan] 暂无新的笔试回复邮件。")

    # 2. 扫描一面确认（--interview-confirm-only 或无特定参数时运行）
    if not run_interview:
        return 0

    r1_results = scan_round1_confirmations(auto_mode=args.auto)
    if r1_results:
        for item in r1_results:
            intent = item.get("intent", "unknown")
            talent_id = item["talent_id"]
            confirm_script = os.path.join(_HERE, "cmd_round1_confirm.py")

            if intent in ("confirm", "timeout"):
                # 自动执行确认命令（含创建飞书日历）
                cmd = ["python3", confirm_script, "--talent-id", talent_id]
                if intent == "timeout":
                    cmd.append("--auto")
                try:
                    # 兼容 Python 3.6（无 capture_output 参数）
                    proc_result = subprocess.run(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=30
                    )
                    confirm_out = proc_result.stdout.decode("utf-8", errors="replace").strip()
                    confirm_err = proc_result.stderr.decode("utf-8", errors="replace").strip()
                    timeout_min = os.environ.get("INTERVIEW_CONFIRM_TIMEOUT_MINUTES", "2880")
                    auto_note = "（已自动确认）" if intent == "confirm" else "（超时{}min，已自动默认确认）".format(timeout_min)
                    report = format_interview_confirmation_report(item)
                    report += "\n{}\n{}".format(auto_note, confirm_out)
                    if confirm_err:
                        report += "\n⚠ {}".format(confirm_err)
                except Exception as e:
                    report = format_interview_confirmation_report(item)
                    report += "\n⚠ 自动确认失败: {}".format(e)
            else:
                report = format_interview_confirmation_report(item)

            if args.auto:
                fn.send_text(report)
            else:
                print(report)
                fn.send_text(report)
        if args.auto:
            print("[email_scan] 一面确认扫描：{} 条更新，已推送飞书。".format(len(r1_results)))
    elif not args.auto:
        print("[email_scan] 暂无一面确认待处理。")

    # 3. 扫描二面确认
    r2_results = scan_round2_confirmations(auto_mode=args.auto)
    if r2_results:
        for item in r2_results:
            intent = item.get("intent", "unknown")
            talent_id = item["talent_id"]
            confirm_script = os.path.join(_HERE, "cmd_round2_confirm.py")

            if intent in ("confirm", "timeout"):
                cmd = ["python3", confirm_script, "--talent-id", talent_id]
                if intent == "timeout":
                    cmd.append("--auto")
                try:
                    # 兼容 Python 3.6（无 capture_output 参数）
                    proc_result = subprocess.run(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=30
                    )
                    confirm_out = proc_result.stdout.decode("utf-8", errors="replace").strip()
                    confirm_err = proc_result.stderr.decode("utf-8", errors="replace").strip()
                    timeout_min = os.environ.get("INTERVIEW_CONFIRM_TIMEOUT_MINUTES", "2880")
                    auto_note = "（已自动确认）" if intent == "confirm" else "（超时{}min，已自动默认确认）".format(timeout_min)
                    report = format_interview_confirmation_report(item)
                    report += "\n{}\n{}".format(auto_note, confirm_out)
                    if confirm_err:
                        report += "\n⚠ {}".format(confirm_err)
                except Exception as e:
                    report = format_interview_confirmation_report(item)
                    report += "\n⚠ 自动确认失败: {}".format(e)
            else:
                report = format_interview_confirmation_report(item)

            if args.auto:
                fn.send_text(report)
            else:
                print(report)
                fn.send_text(report)
        if args.auto:
            print("[email_scan] 二面确认扫描：{} 条更新，已推送飞书。".format(len(r2_results)))
    elif not args.auto:
        print("[email_scan] 暂无二面确认待处理。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
