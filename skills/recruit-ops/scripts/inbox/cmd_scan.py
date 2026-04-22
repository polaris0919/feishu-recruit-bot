#!/usr/bin/env python3
"""inbox/cmd_scan.py —— v3.3 拉取 IMAP 新邮件 → 入 talent_emails。

【职责（只干这一件）】
  对 DB 里的每个候选人，按 candidate_email 搜 IMAP，把没入过表的 inbound 邮件
  插入 talent_emails（direction='inbound', status='received', analyzed_at=NULL）。

【绝不做】
  - 不调 LLM（那是 cmd_analyze 的事）
  - 不推飞书业务通知
  - 不动 talents.* 任何字段
  - 不生成 reply_id（v3.3 已去掉这个概念）

【幂等 / 去重】
  依赖 talent_emails (talent_id, message_id) UNIQUE 约束。
  ON CONFLICT DO NOTHING 保证多 cron 并发跑同一邮件只会落一行。

【自验证（D5）】
  扫完后对本次"声称新增"的 email_id 全部跑 assert_emails_inserted。

【调用示例】
  # 增量扫最近 3 天，全量候选人
  PYTHONPATH=scripts python3 -m inbox.cmd_scan --since 2026-04-18 --json

  # 只扫某个候选人（修历史用）
  PYTHONPATH=scripts python3 -m inbox.cmd_scan --talent-id t_abc123 --since 2025-01-01

  # 干跑（不写 DB）
  PYTHONPATH=scripts python3 -m inbox.cmd_scan --dry-run
"""
from __future__ import print_function

import argparse
import email as email_lib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

from lib import talent_db
from lib import email_attachments
from lib.cli_wrapper import run_with_self_verify, UserInputError
from lib.self_verify import assert_emails_inserted

# ─── 复用 IMAP 拉取 / MIME 解码 / 正文提取（v3.5 拆到 lib/exam_imap）──────────
from lib.exam_imap import (  # noqa: E402
    connect_imap,
    _decode_mime_header,
    _extract_body,
)


# ─── 引用块剥离（复用 followup_scanner 的规则）───────────────────────────────
_QUOTE_BLOCK_PATTERNS = [
    re.compile(r"-{2,}\s*原始邮件\s*-{2,}"),
    re.compile(r"-{4,}\s*回复的原邮件\s*-{4,}"),
    re.compile(r"-{2,}\s*Original\s+Message\s*-{2,}", re.IGNORECASE),
    re.compile(r"-{2,}\s*Original\s*-{2,}", re.IGNORECASE),
    re.compile(r"-{4,}\s*Replied\s+Message\s*-{4,}", re.IGNORECASE),
    re.compile(r"-{2,}\s*Forwarded\s+message\s*-{2,}", re.IGNORECASE),
    re.compile(r"^\s*>?\s*On\s+.{1,200}\bwrote\s*:\s*$",
               re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*在\s*.{1,80}(写道|wrote)\s*[:：]\s*$", re.MULTILINE),
    re.compile(r"^\s*\|\s*(发件人|From)\s*\|", re.MULTILINE),
]

_BODY_EXCERPT_LIMIT = 500  # talent_emails.body_excerpt 的截断长度

# 退信 / 自动回复发件人黑名单关键词（subject / from 任一命中就跳过）
_JUNK_KEYWORDS = (
    "postmaster", "mailer-daemon", "退信", "undelivered",
    "delivery failure", "mail delivery", "undeliverable",
    "auto-reply", "out of office", "自动回复",
)

# 仅做记录，不做"终态过滤" —— 即使 candidate 已被拒，只要未删就继续收邮件
# （老板有时候拒后还要扯几封，保留证据）
_SKIP_STAGES = frozenset()  # 留空 = 全量


def _strip_quoted_reply(text):
    # type: (Optional[str]) -> str
    if not text:
        return text or ""
    cleaned = text.replace("&nbsp;", " ")
    cut = len(cleaned)
    for pat in _QUOTE_BLOCK_PATTERNS:
        m = pat.search(cleaned)
        if m and m.start() < cut:
            cut = m.start()
    if cut <= 10:
        return text
    head = cleaned[:cut].rstrip()
    lines = head.splitlines()
    while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith(">")):
        lines.pop()
    out = "\n".join(lines).rstrip()
    return out or text


def _truncate(text, limit=_BODY_EXCERPT_LIMIT):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _safe_parsedate(date_str):
    # type: (str) -> Optional[datetime]
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        return None


def _stage_to_context(stage):
    # type: (Optional[str]) -> str
    """把候选人当前 stage 映射到 talent_emails.context 字段。与 outbound/cmd_send
    一致，方便后续 BI / consistency check 双向对齐。"""
    if not stage:
        return "intake"
    if stage.startswith("ROUND2"):
        return "round2"
    if stage.startswith("ROUND1"):
        return "round1"
    if stage.startswith("EXAM"):
        return "exam"
    if "OFFER" in stage or stage == "POST_OFFER_FOLLOWUP":
        return "followup"
    return "intake"


# ─── 按候选人扫 IMAP ──────────────────────────────────────────────────────────

def _list_all_candidates(talent_id_filter=None):
    # type: (Optional[str]) -> List[Dict[str, Any]]
    """读全量候选人的 (talent_id, candidate_email, current_stage)。

    inbox/cmd_scan 想覆盖所有 stage（哪怕已拒未删的候选人回邮件也要收），所以
    不做任何 stage / followup_status 过滤——直接走 raw SQL 全表。
    """
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from lib import config as _cfg

    where = ""
    params = []
    if talent_id_filter:
        where = "WHERE talent_id = %s"
        params.append(talent_id_filter)

    rows = []
    with psycopg2.connect(**_cfg.db_conn_params()) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT talent_id, candidate_name, candidate_email, current_stage "
                "FROM talents {} ORDER BY talent_id".format(where),
                params,
            )
            rows = [dict(r) for r in cur.fetchall()]
    return rows


def _fetch_messages_for_email(imap, candidate_email, since_dt, max_fetch=50):
    # type: (Any, str, Optional[datetime], int) -> List[Tuple[bytes, Any]]
    """从 INBOX 拉取该邮箱的邮件（SINCE 过滤）。"""
    try:
        criteria = ["FROM", '"{}"'.format(candidate_email)]
        if since_dt:
            # IMAP SINCE 格式：01-Apr-2026
            criteria.extend(["SINCE", since_dt.strftime("%d-%b-%Y")])
        status, data = imap.search(None, *criteria)
    except Exception as e:
        print("[inbox/cmd_scan] IMAP search 失败 {}: {}".format(candidate_email, e),
              file=sys.stderr)
        return []
    if status != "OK" or not data or not data[0]:
        return []
    mids = data[0].split()
    if not mids:
        return []

    fetched = []
    # 只拉最近 max_fetch 封，防止历史邮件全量拖下来
    for mid in mids[-max_fetch:]:
        try:
            status, raw = imap.fetch(mid, "(RFC822)")
            if status != "OK":
                continue
            msg = email_lib.message_from_bytes(raw[0][1])
            from_hdr = str(msg.get("From") or "").lower()
            if candidate_email.lower() not in from_hdr:
                # IMAP FROM search 有时会匹配 To/Cc 里的地址，这里再确认一次
                continue
            fetched.append((mid, msg))
        except Exception:
            continue
    return fetched


def _process_candidate(imap, candidate, since_dt, dry_run=False):
    # type: (Any, Dict[str, Any], Optional[datetime], bool) -> Dict[str, Any]
    """返回 {"inserted": [email_id...], "scanned": N, "skipped_dup": M,
              "skipped_junk": K, "errors": E,
              "attachments_saved": A, "attachments_skipped": S,
              "attachments_errors": EA}。"""
    talent_id = candidate["talent_id"]
    cand_email = (candidate.get("candidate_email") or "").strip()
    stage = candidate.get("current_stage") or ""
    if not cand_email or "@" not in cand_email:
        return {"inserted": [], "scanned": 0, "skipped_dup": 0,
                "skipped_junk": 0, "errors": 1,
                "attachments_saved": 0, "attachments_skipped": 0,
                "attachments_errors": 0, "reason": "bad_email"}

    fetched = _fetch_messages_for_email(imap, cand_email, since_dt)
    inserted = []               # email_id 列表
    inserted_message_ids = []   # 本轮真插入的 message_id（self-verify 用）
    skipped_dup = 0
    skipped_junk = 0
    att_saved = 0
    att_skipped = 0
    att_errors = 0

    context = _stage_to_context(stage)

    for _mid, msg in fetched:
        msg_id = (msg.get("Message-ID") or "").strip()
        if not msg_id:
            continue
        subject = _decode_mime_header(msg.get("Subject") or "")
        sender = _decode_mime_header(msg.get("From") or "")

        # junk filter（退信 / 自动回复）
        bag = (subject or "").lower() + " " + (sender or "").lower()
        if any(kw in bag for kw in _JUNK_KEYWORDS):
            skipped_junk += 1
            continue

        body_full = _extract_body(msg) or ""
        body_excerpt = _truncate(_strip_quoted_reply(body_full))
        sent_at = _safe_parsedate(msg.get("Date") or "") or datetime.now(timezone.utc)
        references = (msg.get("References") or "").strip() or None
        in_reply_to = (msg.get("In-Reply-To") or "").strip() or None
        to_hdr = _decode_mime_header(msg.get("To") or "")
        recipients = [x.strip() for x in to_hdr.split(",")] if to_hdr else None

        if dry_run:
            # dry-run 路径：不写 DB，也不声明"插入"
            continue

        try:
            email_id = talent_db.insert_email_if_absent(
                talent_id=talent_id,
                message_id=msg_id,
                direction="inbound",
                context=context,
                sender=sender or cand_email,
                sent_at=sent_at,
                subject=subject,
                in_reply_to=in_reply_to,
                references_chain=references,
                recipients=recipients,
                received_at=datetime.now(timezone.utc),
                body_full=body_full,
                body_excerpt=body_excerpt,
                stage_at_receipt=stage,
                initial_status="received",
                analyzed_at=None,  # 显式：待 cmd_analyze 处理
            )
        except Exception as e:
            print("[inbox/cmd_scan] insert_email_if_absent 失败 tid={} mid={}: {}".format(
                talent_id, msg_id[:40], e), file=sys.stderr)
            continue

        if email_id is None:
            skipped_dup += 1
            continue

        inserted.append(email_id)
        inserted_message_ids.append(msg_id)

        # ── v3.5.6 附件落盘 ──
        # 仅对真正新插入的邮件做（命中 ON CONFLICT 的不重复落盘）。
        # 出错绝不阻塞邮件入库 —— 邮件主体 SQL 是事实，附件落盘是衍生。
        try:
            att_meta = email_attachments.extract_and_save(
                msg, talent_id=talent_id, email_id=email_id, context=context)
        except Exception as e:
            print("[inbox/cmd_scan] 附件提取异常 tid={} eid={}: {}".format(
                talent_id, email_id, e), file=sys.stderr)
            att_errors += 1
            att_meta = []

        if att_meta:
            try:
                talent_db.update_email_attachments(email_id, att_meta)
            except Exception as e:
                print("[inbox/cmd_scan] update_email_attachments 失败 eid={}: {}".format(
                    email_id, e), file=sys.stderr)
                att_errors += 1
            for m in att_meta:
                if m.get("saved"):
                    att_saved += 1
                else:
                    att_skipped += 1

    return {
        "talent_id": talent_id,
        "candidate_email": cand_email,
        "inserted": inserted,
        "inserted_message_ids": inserted_message_ids,
        "scanned": len(fetched),
        "skipped_dup": skipped_dup,
        "skipped_junk": skipped_junk,
        "errors": 0,
        "attachments_saved": att_saved,
        "attachments_skipped": att_skipped,
        "attachments_errors": att_errors,
    }


# ─── 主流程 ───────────────────────────────────────────────────────────────────

def _build_parser():
    p = argparse.ArgumentParser(
        description="拉取 IMAP 新邮件 → 入 talent_emails (inbound)",
    )
    p.add_argument("--since", default=None,
                   help="ISO 日期（YYYY-MM-DD），默认最近 7 天")
    p.add_argument("--limit", type=int, default=50,
                   help="每个候选人最多拉多少封（IMAP SEARCH 结果尾部），默认 50")
    p.add_argument("--talent-id", default=None,
                   help="只扫指定候选人（修历史 / 排查用）")
    p.add_argument("--dry-run", action="store_true",
                   help="只连 IMAP + 拉邮件；不写 talent_emails，不做 self-verify")
    p.add_argument("--json", action="store_true", help="结果以 JSON 输出")
    return p


def _do_scan(args):
    if args.since:
        try:
            since_dt = datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            raise UserInputError("--since 需要 YYYY-MM-DD 格式，拿到: {}".format(args.since))
    else:
        since_dt = datetime.now() - timedelta(days=7)

    candidates = _list_all_candidates(args.talent_id)
    if not candidates:
        if args.talent_id:
            raise UserInputError("候选人 {} 不存在".format(args.talent_id))
        print("[inbox/cmd_scan] 库里暂无候选人，直接退出", file=sys.stderr)
        result = {"ok": True, "candidates": 0, "scanned": 0, "inserted": 0,
                  "skipped_dup": 0, "skipped_junk": 0, "dry_run": bool(args.dry_run)}
        print(json.dumps(result, ensure_ascii=False) if args.json
              else "[inbox/cmd_scan] no candidates")
        return 0

    try:
        imap = connect_imap()
    except Exception as e:
        # IMAP 连不上就算 crash（真的 blocker）→ 让 cli_wrapper 推飞书
        raise RuntimeError("IMAP 连接失败: {}".format(e))

    inserted_all = []
    per_candidate = []
    scanned_total = dup_total = junk_total = err_total = 0
    att_saved_total = att_skipped_total = att_err_total = 0
    try:
        imap.select("INBOX")
        for cand in candidates:
            try:
                res = _process_candidate(imap, cand, since_dt, dry_run=args.dry_run)
            except Exception as e:
                print("[inbox/cmd_scan] 处理 {} 失败: {}".format(
                    cand.get("talent_id"), e), file=sys.stderr)
                err_total += 1
                continue
            per_candidate.append(res)
            inserted_all.extend(res.get("inserted") or [])
            scanned_total += res.get("scanned") or 0
            dup_total += res.get("skipped_dup") or 0
            junk_total += res.get("skipped_junk") or 0
            err_total += res.get("errors") or 0
            att_saved_total += res.get("attachments_saved") or 0
            att_skipped_total += res.get("attachments_skipped") or 0
            att_err_total += res.get("attachments_errors") or 0
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    # ── 自验证（D5）——按候选人分组验每个 message_id 都已入库 ──
    if not args.dry_run:
        for res in per_candidate:
            mids = res.get("inserted_message_ids") or []
            if mids:
                assert_emails_inserted(res["talent_id"], mids)

    result = {
        "ok": True,
        "since": since_dt.strftime("%Y-%m-%d"),
        "candidates": len(candidates),
        "scanned": scanned_total,
        "inserted": len(inserted_all),
        "inserted_ids": inserted_all,
        "skipped_dup": dup_total,
        "skipped_junk": junk_total,
        "errors": err_total,
        "attachments_saved": att_saved_total,
        "attachments_skipped": att_skipped_total,
        "attachments_errors": att_err_total,
        "dry_run": bool(args.dry_run),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(("[inbox/cmd_scan] candidates={} scanned={} inserted={} "
               "dup={} junk={} err={} att_saved={} att_skipped={} att_err={} since={}{}").format(
            result["candidates"], result["scanned"], result["inserted"],
            result["skipped_dup"], result["skipped_junk"], result["errors"],
            result["attachments_saved"], result["attachments_skipped"],
            result["attachments_errors"],
            result["since"], " [DRY-RUN]" if args.dry_run else ""))
    return 0


def main(argv=None):
    args = _build_parser().parse_args(argv)
    return _do_scan(args)


if __name__ == "__main__":
    run_with_self_verify("inbox.cmd_scan", main)
