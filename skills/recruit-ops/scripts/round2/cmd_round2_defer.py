#!/usr/bin/env python3
"""兼容入口：转发到 `interview/cmd_defer.py --round 2`。"""
import sys
import interview.cmd_defer as _mod
_REAL_SEND = _mod._send_defer_email
_REAL_DELETE = _mod._spawn_calendar_delete_bg


def _send_defer_email(to_email, talent_id, candidate_name=""):
    return _REAL_SEND(to_email, talent_id, 2, candidate_name=candidate_name)


def _spawn_calendar_delete_bg(event_id):
    return _REAL_DELETE(event_id, 2)


def main(argv=None):
    args = argv or sys.argv[1:]
    old_send = _mod._send_defer_email
    old_delete = _mod._spawn_calendar_delete_bg
    try:
        _mod._send_defer_email = lambda to_email, talent_id, round_num, candidate_name="": _send_defer_email(
            to_email,
            talent_id,
            candidate_name=candidate_name,
        )
        _mod._spawn_calendar_delete_bg = lambda event_id, round_num: _spawn_calendar_delete_bg(event_id)
        return _mod.main(["--round", "2"] + list(args))
    finally:
        _mod._send_defer_email = old_send
        _mod._spawn_calendar_delete_bg = old_delete


if __name__ == "__main__":
    raise SystemExit(main())
