#!/usr/bin/env python3
"""
标记一面时间已确认，阶段推进到 ROUND1_SCHEDULED，后台创建老板飞书日历。

用法：
  # 候选人主动确认
  python3 cmd_round1_confirm.py --talent-id t_xxx

  # 超过48小时未回复，系统自动默认确认
  python3 cmd_round1_confirm.py --talent-id t_xxx --auto
"""
import argparse
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


def _spawn_calendar_bg(talent_id, round1_time, interviewer, candidate_email):
    # type: (str, str, str, str) -> int
    """后台创建一面飞书日历事件，返回 PID。"""
    script = os.path.join(_HERE, "feishu_calendar.py")
    cmd = [
        "python3", script,
        "--talent-id", talent_id,
        "--round2-time", round1_time,  # feishu_calendar 复用此参数
        "--event-round", "1",
    ]
    if interviewer:
        cmd += ["--interviewer", interviewer]
    if candidate_email:
        cmd += ["--candidate-email", candidate_email]

    log_path = "/tmp/feishu_cal_round1_{}_{}.log".format(talent_id, int(time.time()))
    log_fp = open(log_path, "w")
    proc = subprocess.Popen(
        cmd,
        start_new_session=True,
        stdout=log_fp,
        stderr=log_fp,
        close_fds=True,
    )
    log_fp.close()
    with open("/tmp/feishu_calendar_bg.log", "a") as f:
        f.write("[{}] round1 cal PID={} log={}\n".format(
            time.strftime("%Y-%m-%d %H:%M:%S"), proc.pid, log_path))
    return proc.pid


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="标记一面时间已确认")
    p.add_argument("--talent-id", required=True, help="候选人 talent_id")
    p.add_argument("--auto", action="store_true", help="超时默认确认（48h无回复）")
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    args = parse_args(argv)
    talent_id = args.talent_id.strip()

    try:
        import talent_db as _tdb
        if not _tdb._is_enabled():
            print("ERROR: DB 未配置，无法标记确认", file=sys.stderr)
            return 1

        db_state = _tdb.load_state_from_db()
        cand = (db_state.get("candidates") or {}).get(talent_id)
        if not cand:
            print("ERROR: 未找到候选人 {}".format(talent_id), file=sys.stderr)
            return 1
        if cand.get("stage") != "ROUND1_SCHEDULING":
            print(
                "ERROR: 候选人 {} 当前阶段为 {}，不处于 ROUND1_SCHEDULING".format(
                    talent_id, cand.get("stage")
                ),
                file=sys.stderr,
            )
            return 1

        # 更新 DB：round1_confirmed=TRUE, current_stage='ROUND1_SCHEDULED'
        _tdb.mark_round1_confirmed(talent_id, auto=args.auto)

        round1_time = cand.get("round1_time") or "（未知）"
        candidate_name = cand.get("candidate_name") or talent_id
        interviewer = cand.get("round1_interviewer") or ""
        candidate_email = cand.get("candidate_email") or ""
        confirm_type = "超时默认确认（48h无回复）" if args.auto else "候选人主动确认"

        lines = [
            "[一面时间已确认]",
            "- talent_id: {}".format(talent_id),
            "- 候选人: {}".format(candidate_name),
            "- 确认方式: {}".format(confirm_type),
            "- 一面时间: {}".format(round1_time),
            "- 当前阶段: ROUND1_SCHEDULED",
        ]

        # 后台创建飞书日历
        if round1_time and round1_time != "（未知）":
            cal_pid = _spawn_calendar_bg(talent_id, round1_time, interviewer, candidate_email)
            lines.append("- 飞书日历: 创建中（后台 PID={}），约10秒后完成".format(cal_pid))

        print("\n".join(lines))
        return 0

    except Exception as e:
        print("ERROR: {}".format(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
