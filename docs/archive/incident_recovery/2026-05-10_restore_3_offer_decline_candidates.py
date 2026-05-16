#!/usr/bin/env python3
"""[ARCHIVED 2026-05-12] 一次性恢复脚本，已完成使命。

事故源：INCIDENT_RULES.md §12（2026-05-10 agent 跳过双轮 confirm 把 3 位
POST_OFFER_FOLLOWUP 候选人物理删档）。本脚本当天用过一次完成数据恢复，
之后归档于此供事故复盘 / 历史参考——**不要再直接调用**。

如果未来要做类似恢复，请参考其逻辑后另写一个新的、带"事故 ID + 候选人列表"
明示输入的脚本，**不要 fork 本文件**，因为它把 talent_id 硬编码在内部。

历史调用方式（仅供归档参考）：
  cd /home/admin/recruit-workspace/skills/recruit-ops
  PYTHONPATH=scripts python3 ../../tools/restore_3_offer_decline_candidates.py --dry-run
  PYTHONPATH=scripts python3 ../../tools/restore_3_offer_decline_candidates.py --apply

执行内容（仅作记录）：
  1. INSERT INTO talents 用 archive["talent"] 全字段
  2. INSERT INTO talent_emails 用 archive["emails"] 数组（保留原 email_id / message_id）
  3. mv archive_dir/<tid>__dir_<ts>/ → data/candidates/<tid>/（恢复 cv / email 附件 / exam_answer）
  4. 写一条 talent_events 审计：action='talent.restored_from_archive'
"""
from __future__ import print_function

import sys

# v3.8.5 归档守卫：直接 import 本模块只读 docstring 可以，但
# `python3 .../2026-05-10_restore_3_offer_decline_candidates.py` 必须明示
# --i-know-this-is-archived 才能往下走，防止运维误调到一次性脚本。
if __name__ == "__main__" and "--i-know-this-is-archived" not in sys.argv:
    sys.stderr.write(
        "[ARCHIVED] 这是 2026-05-10 那次事故的一次性恢复脚本，已经归档。\n"
        "如确有恢复需要，请：\n"
        "  1) 阅读 docs/archive/incident_recovery/ 下的复盘记录\n"
        "  2) 参考本脚本逻辑另写新脚本，不要 fork 本文件\n"
        "  3) 真要强行跑：加 --i-know-this-is-archived 旗标\n")
    sys.exit(2)

import argparse
import json
import os
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

# 让脚本可以从工作区任意位置跑（只要 PYTHONPATH=scripts 已设置）
WORKSPACE_ROOT = Path("/home/admin/recruit-workspace")
ARCHIVE_DIR = WORKSPACE_ROOT / "data" / "deleted_archive" / "2026-05"
CANDIDATES_DIR = WORKSPACE_ROOT / "data" / "candidates"

# 3 个误删候选人——脚本会**自动**找每个 talent_id 在 ARCHIVE_DIR 里
# 时间戳最新的一份归档（JSON + __dir_）。这样如果同一候选人被删了多次,
# 永远恢复到最近一次删除前的状态。
TARGET_TALENT_IDS = ["t_lmu39m", "t_z04u9v", "t_256klz"]


def _resolve_latest_archive(talent_id):
    """在 ARCHIVE_DIR 里找最新的 talent_id_<ts>.json + 同 ts 的 __dir_ 配对。

    返回 dict(json_file=..., dir_archive=...,（可选）)。
    没有任何归档则抛异常。
    """
    pattern_json = "{}_*.json".format(talent_id)
    candidates = sorted(ARCHIVE_DIR.glob(pattern_json), reverse=True)
    if not candidates:
        raise FileNotFoundError(
            "ARCHIVE_DIR 里找不到 {} 的归档 JSON: {}".format(talent_id, ARCHIVE_DIR))
    latest_json = candidates[0]
    # stem 形如 "t_lmu39m_20260510T204247";去掉 "t_lmu39m_" 前缀剩 ts
    ts = latest_json.stem[len(talent_id) + 1:]
    dir_name = "{}__dir_{}".format(talent_id, ts)
    return {
        "talent_id": talent_id,
        "json_file": latest_json.name,
        "dir_archive": dir_name,
    }

# talents 表所有列（schema.sql 真源）
TALENT_COLUMNS = [
    "talent_id", "candidate_email", "candidate_name", "current_stage",
    "wait_return_round", "exam_id",
    "round1_confirm_status", "round1_time", "round1_invite_sent_at",
    "round1_calendar_event_id", "round1_reminded_at", "round1_confirm_prompted_at",
    "round2_confirm_status", "round2_time", "round2_invite_sent_at",
    "round2_calendar_event_id", "round2_reminded_at", "round2_confirm_prompted_at",
    "exam_sent_at",
    "source", "position", "education", "work_years", "experience",
    "school", "phone", "wechat", "cv_path", "has_cpp",
    "created_at", "updated_at",
]


def _load_archive(target):
    path = ARCHIVE_DIR / target["json_file"]
    if not path.exists():
        raise FileNotFoundError("Archive 不存在: {}".format(path))
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _restore_talent_row(cur, talent, dry_run):
    """INSERT INTO talents 把 archive["talent"] 还原。"""
    talent_id = talent["talent_id"]

    cur.execute("SELECT 1 FROM talents WHERE talent_id = %s", (talent_id,))
    if cur.fetchone():
        print("  [skip] talents 行已存在: {}".format(talent_id))
        return False

    values = [talent.get(col) for col in TALENT_COLUMNS]
    placeholders = ", ".join(["%s"] * len(TALENT_COLUMNS))
    cols_csv = ", ".join(TALENT_COLUMNS)

    if dry_run:
        print("  [dry-run] INSERT INTO talents (..) for {} stage={}".format(
            talent_id, talent.get("current_stage")))
        return True

    cur.execute(
        "INSERT INTO talents ({}) VALUES ({})".format(cols_csv, placeholders),
        values,
    )
    print("  [ok] talents 行已恢复: {} stage={}".format(
        talent_id, talent.get("current_stage")))
    return True


def _restore_emails(cur, talent_id, emails, dry_run):
    """INSERT INTO talent_emails 把 archive["emails"] 全部还原。

    保留原 email_id（UUID）；ON CONFLICT (talent_id, message_id) DO NOTHING。
    """
    if not emails:
        print("  [skip] 该候选人无邮件")
        return 0

    inserted = 0
    skipped = 0
    for em in emails:
        cur.execute(
            "SELECT 1 FROM talent_emails WHERE talent_id = %s AND message_id = %s",
            (talent_id, em.get("message_id")),
        )
        if cur.fetchone():
            skipped += 1
            continue

        if dry_run:
            inserted += 1
            continue

        # archive 里部分字段缺失（recipients/references_chain/body_full/processed_at/
        # received_at/replied_by_email_id/attachments/stage_at_receipt）→ 全填 NULL
        cur.execute("""
            INSERT INTO talent_emails (
                email_id, talent_id, message_id, in_reply_to,
                direction, sender, subject,
                sent_at, context,
                status, body_excerpt,
                ai_summary, ai_intent,
                reply_id, template
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s
            )
        """, (
            em.get("email_id") or str(uuid.uuid4()),
            talent_id,
            em.get("message_id"),
            em.get("in_reply_to"),
            em.get("direction"),
            em.get("sender"),
            em.get("subject"),
            em.get("sent_at"),
            em.get("context"),
            em.get("status") or "received",
            em.get("body_excerpt"),
            em.get("ai_summary"),
            em.get("ai_intent"),
            em.get("reply_id"),
            em.get("template"),
        ))
        inserted += 1

    if dry_run:
        print("  [dry-run] talent_emails 将插入 {} 行（{} 行已存在跳过）".format(
            inserted, skipped))
    else:
        print("  [ok] talent_emails 插入 {} 行（{} 行已存在跳过）".format(
            inserted, skipped))
    return inserted


def _restore_candidate_dir(target, dry_run):
    """把 archive/<tid>__dir_<ts>/ 搬回 data/candidates/<tid>/。"""
    src = ARCHIVE_DIR / target["dir_archive"]
    dst = CANDIDATES_DIR / target["talent_id"]

    if not src.exists():
        print("  [skip] FS 归档源目录不存在: {}".format(src))
        return False
    if dst.exists():
        print("  [skip] 目标目录已存在,不覆盖: {}".format(dst))
        return False

    if dry_run:
        print("  [dry-run] mv {} → {}".format(src, dst))
        return True

    shutil.move(str(src), str(dst))
    print("  [ok] FS 已恢复: {}".format(dst))
    return True


def _write_audit(talent_db, talent_id, archive_path, dry_run):
    if dry_run:
        print("  [dry-run] talent_events: action=talent.restored_from_archive")
        return
    talent_db.save_audit_event(
        talent_id, "talent.restored_from_archive",
        payload={
            "archive_path": str(archive_path),
            "incident_ref": "INCIDENT_RULES.md §12",
            "restored_at": datetime.now().isoformat(),
            "reason": "agent 跳过双轮 confirm 误删,人工恢复",
        },
        actor="manual_restore",
    )
    print("  [ok] 审计事件已写入 talent_events")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="只 echo 计划,不写 DB / 不动 FS")
    parser.add_argument("--apply", action="store_true",
                        help="真跑（写 DB + 移动归档目录）")
    args = parser.parse_args()

    if not (args.dry_run or args.apply):
        print("ERROR: 必须显式指定 --dry-run 或 --apply", file=sys.stderr)
        return 2

    if args.dry_run and args.apply:
        print("ERROR: --dry-run 和 --apply 互斥", file=sys.stderr)
        return 2

    # 延迟导入：保证 PYTHONPATH=scripts 已设置
    try:
        from lib import talent_db
    except ImportError as e:
        print("ERROR: 无法 import lib.talent_db。请用 PYTHONPATH=scripts 跑。\n  {}".format(e),
              file=sys.stderr)
        return 2

    print("=" * 70)
    print("Restore mode: {}".format("DRY-RUN" if args.dry_run else "APPLY"))
    print("=" * 70)

    targets = []
    archive_paths = {}
    for tid in TARGET_TALENT_IDS:
        try:
            tg = _resolve_latest_archive(tid)
        except FileNotFoundError as e:
            print("ERROR: {}".format(e), file=sys.stderr)
            return 3
        targets.append(tg)
        archive_paths[tid] = ARCHIVE_DIR / tg["json_file"]
        print("  Resolved: {} → json={} dir={}".format(
            tid, tg["json_file"], tg["dir_archive"]))

    summary = {"talents_inserted": 0, "emails_inserted": 0, "dirs_moved": 0,
               "audits_written": 0}

    for tg in targets:
        talent_id = tg["talent_id"]
        print("\n--- {} ---".format(talent_id))
        archive = _load_archive(tg)
        talent = archive["talent"]
        emails = archive.get("emails", [])

        print("  候选人: {} stage={} candidate_email={}".format(
            talent.get("candidate_name"), talent.get("current_stage"),
            talent.get("candidate_email")))
        print("  归档原因: {}".format(archive.get("reason")))
        print("  归档时间: {}".format(archive.get("deleted_at")))

        if args.dry_run:
            with talent_db._connect() as conn:
                with conn.cursor() as cur:
                    if _restore_talent_row(cur, talent, dry_run=True):
                        summary["talents_inserted"] += 1
                    n = _restore_emails(cur, talent_id, emails, dry_run=True)
                    summary["emails_inserted"] += n
                conn.rollback()
            if _restore_candidate_dir(tg, dry_run=True):
                summary["dirs_moved"] += 1
            _write_audit(talent_db, talent_id, archive_paths[talent_id],
                         dry_run=True)
        else:
            with talent_db._connect() as conn:
                with conn.cursor() as cur:
                    if _restore_talent_row(cur, talent, dry_run=False):
                        summary["talents_inserted"] += 1
                    n = _restore_emails(cur, talent_id, emails, dry_run=False)
                    summary["emails_inserted"] += n
                conn.commit()
            if _restore_candidate_dir(tg, dry_run=False):
                summary["dirs_moved"] += 1
            _write_audit(talent_db, talent_id, archive_paths[talent_id],
                         dry_run=False)
            summary["audits_written"] += 1

    print("\n" + "=" * 70)
    print("Summary ({})".format("DRY-RUN" if args.dry_run else "APPLIED"))
    print("=" * 70)
    print("  talents 行恢复: {}".format(summary["talents_inserted"]))
    print("  talent_emails 邮件恢复: {}".format(summary["emails_inserted"]))
    print("  FS 归档目录恢复: {}".format(summary["dirs_moved"]))
    print("  talent_events 审计写入: {}".format(summary["audits_written"]))

    if args.dry_run:
        print("\n下一步: 用 --apply 跑实际恢复（事先建议人工 confirm 一遍）")
    else:
        print("\n建议跑一次健康验证:")
        print("  cd skills/recruit-ops && PYTHONPATH=scripts python3 -m talent.cmd_show --talent-id t_lmu39m")
        print("  PYTHONPATH=scripts python3 -m talent.cmd_show --talent-id t_z04u9v")
        print("  PYTHONPATH=scripts python3 -m talent.cmd_show --talent-id t_256klz")

    return 0


if __name__ == "__main__":
    sys.exit(main())
