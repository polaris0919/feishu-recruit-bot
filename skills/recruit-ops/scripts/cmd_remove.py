#!/usr/bin/env python3
"""从人才库中物理删除候选人（PostgreSQL）。必须加 --confirm 才执行。"""
import argparse
import json
import sys

from core_state import load_state


def main(argv=None):
    p = argparse.ArgumentParser(description="从人才库彻底移除候选人")
    p.add_argument("--talent-id", "--talent_id", required=True)
    p.add_argument("--confirm", action="store_true", help="确认执行物理删除（不可恢复）")
    args = p.parse_args(argv or sys.argv[1:])

    talent_id = (args.talent_id or "").strip()
    if not talent_id:
        print(json.dumps({"ok": False, "error": "talent_id 不能为空"}, ensure_ascii=False))
        return 1

    if not args.confirm:
        print(json.dumps({"ok": False, "error": "必须加 --confirm 才能执行物理删除（此操作不可恢复）"}, ensure_ascii=False))
        return 1

    state = load_state()
    cand = (state.get("candidates") or {}).get(talent_id)
    if not cand:
        print(json.dumps({"ok": False, "error": "候选人 {} 不存在".format(talent_id)}, ensure_ascii=False))
        return 1

    stage = cand.get("stage", "NEW")
    email = cand.get("candidate_email") or "—"

    deleted_from_db = False
    try:
        import talent_db
        if talent_db._is_enabled():
            deleted_from_db = talent_db.delete_talent_from_db(talent_id)
    except Exception as e:
        print(json.dumps({"ok": False, "error": "DB 删除失败: {}".format(e)}, ensure_ascii=False))
        return 1

    if not deleted_from_db:
        candidates = state.get("candidates", {})
        candidates.pop(talent_id, None)
        from core_state import save_state
        save_state(state)

    print(json.dumps({
        "ok": True,
        "talent_id": talent_id,
        "stage_was": stage,
        "email_was": email,
        "message": "候选人 {} 已从人才库中移除。".format(talent_id),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
