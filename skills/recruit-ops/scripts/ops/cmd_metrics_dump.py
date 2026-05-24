#!/usr/bin/env python3
"""ops/cmd_metrics_dump.py —— 最小可观测性 (C2, v3.8.7)。

═══════════════════════════════════════════════════════════════════════════════
为什么有这个 CLI
═══════════════════════════════════════════════════════════════════════════════
ops.cmd_health_check 检测的是"系统能不能跑"(连通性 / 配额), 答的是 yes/no。
本 CLI 检测的是"系统在跑什么"(业务计数器), 答的是数字快照。

两件事正交:
  - health_check fail = 立刻报警, hard 依赖断了
  - metrics_dump 异常分布 = 信号, 老板自己看趋势, 不一定立刻报警

═══════════════════════════════════════════════════════════════════════════════
输出
═══════════════════════════════════════════════════════════════════════════════
人类格式 (默认 stdout):
    [recruit-metrics] 2026-05-16 00:00:00
    stage_count:
      NEW                       3
      ROUND1_SCHEDULING         2
      ...
    last_24h_emails:
      inbound  inserted=15  analyzed=14  pending=1
      outbound sent=8
    exam_timeout_pending  ≥3d 未交卷未处理: 0
    db_query_latency_ms   12.3

JSON 格式 (--json):
    {"ts":"2026-...", "stage_count":{...}, "last_24h_emails":{...},
     "exam_timeout_pending":0, "db_query_latency_ms":12.3}

═══════════════════════════════════════════════════════════════════════════════
退出码
═══════════════════════════════════════════════════════════════════════════════
0 = DB 可读 + 全部指标抽取成功
1 = DB 抖动 / SQL 失败, 至少一个指标缺失
2 = 参数错误

写入路径: 0 (本 CLI 是纯读)。

═══════════════════════════════════════════════════════════════════════════════
何处调用
═══════════════════════════════════════════════════════════════════════════════
- 老板手动: PYTHONPATH=scripts python3 -m ops.cmd_metrics_dump
- cron_runner: 每天 09:xx 跑一次 (与 health_check 同节奏, 不浪费配额)
- 飞书报表 / dashboard: 取 --json 后续画图
"""
from __future__ import print_function

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from lib import config as _cfg
from lib.cli_wrapper import UserInputError
from lib.core_state import STAGES


def _stage_counts(cur):
    # type: (Any) -> Dict[str, int]
    """每个 stage 当前候选人数。SSOT = DB chk_current_stage CHECK 约束,
    本函数把 13 个 STAGES 列出, DB 没数据的填 0,保持输出 schema 稳定。
    """
    cur.execute("SELECT current_stage, COUNT(*) FROM talents GROUP BY current_stage")
    rows = dict(cur.fetchall())
    return {st: int(rows.get(st, 0)) for st in sorted(STAGES)}


def _last_24h_email_counters(cur, since):
    # type: (Any, datetime) -> Dict[str, Dict[str, int]]
    """近 24h talent_emails 计数:
        inbound  inserted=  analyzed=  pending=
        outbound sent=
    """
    cur.execute(
        """
        SELECT direction, status, COUNT(*) AS n
        FROM talent_emails
        WHERE sent_at >= %s
        GROUP BY direction, status
        """,
        (since,),
    )
    rows = cur.fetchall()

    inbound = {"inserted": 0, "analyzed": 0, "pending": 0}
    outbound = {"sent": 0}
    for direction, status, n in rows:
        n = int(n)
        if direction == "inbound":
            inbound["inserted"] += n
            if status in ("analyzed", "replied", "ignored"):
                inbound["analyzed"] += n
            else:
                inbound["pending"] += n
        elif direction == "outbound":
            outbound["sent"] += n
    return {"inbound": inbound, "outbound": outbound}


def _exam_timeout_pending(cur, threshold_days=3):
    # type: (Any, int) -> int
    """≥ threshold_days 没交卷, 且 auto_reject 还没处理掉的候选人数。

    判定逻辑跟 auto_reject.cmd_scan_exam_timeout 对齐:
      - stage = EXAM_SENT
      - exam_sent_at <= now - threshold_days
      - 没有 inbound 邮件 / 没有 outbound rejection 这两条 auto_reject 自己处理,
        本 metrics 只看"超时且仍在 EXAM_SENT"这个粗粒度信号即可。
    """
    threshold = datetime.now(timezone.utc) - timedelta(days=int(threshold_days))
    cur.execute(
        "SELECT COUNT(*) FROM talents "
        "WHERE current_stage = 'EXAM_SENT' AND exam_sent_at IS NOT NULL "
        "AND exam_sent_at <= %s",
        (threshold,),
    )
    row = cur.fetchone()
    return int(row[0]) if row else 0


def _heartbeat_age_minutes():
    # type: () -> float
    """读 _cron_heartbeat 文件, 算"上次 cron_runner 跑完到现在"的分钟数。

    >= 25h 应该是 health_check 已经告警了; 这里把数值露出, 让 dashboard
    画曲线。文件不存在或解析失败返回 -1 (避免与"刚跑过 0 分钟"歧义)。
    """
    from lib.candidate_storage import data_root
    path = data_root() / ".cron_heartbeat"
    if not path.is_file():
        return -1.0
    try:
        last = datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except Exception:
        return -1.0
    return (datetime.now() - last).total_seconds() / 60.0


def _format_human(snapshot):
    # type: (Dict[str, Any]) -> str
    lines = [
        "[recruit-metrics] {}".format(snapshot["ts"]),
        "",
        "stage_count:",
    ]
    width = max(len(k) for k in snapshot["stage_count"])
    for stage in sorted(snapshot["stage_count"]):
        n = snapshot["stage_count"][stage]
        marker = " " if n == 0 else "*"
        lines.append("  {} {:<{w}} {:>4}".format(marker, stage, n, w=width))
    lines.append("")
    em = snapshot["last_24h_emails"]
    lines.append("last_24h_emails:")
    lines.append(
        "  inbound  inserted={} analyzed={} pending={}".format(
            em["inbound"]["inserted"],
            em["inbound"]["analyzed"],
            em["inbound"]["pending"],
        )
    )
    lines.append("  outbound sent={}".format(em["outbound"]["sent"]))
    lines.append("")
    lines.append(
        "exam_timeout_pending  >=3d 未交卷且仍在 EXAM_SENT: {}".format(
            snapshot["exam_timeout_pending"]
        )
    )
    hb = snapshot["heartbeat_age_minutes"]
    if hb < 0:
        lines.append("cron_heartbeat_age    (none)")
    else:
        lines.append("cron_heartbeat_age    {:.1f} min".format(hb))
    lines.append(
        "db_query_latency_ms   {:.1f}".format(snapshot["db_query_latency_ms"])
    )
    return "\n".join(lines)


def _collect():
    # type: () -> Dict[str, Any]
    """读全部指标, 返回 snapshot dict。DB 异常直接上抛, main() 决定退出码。"""
    import psycopg2

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    t0 = time.time()
    with psycopg2.connect(**_cfg.db_conn_params()) as conn:
        with conn.cursor() as cur:
            stage = _stage_counts(cur)
            emails = _last_24h_email_counters(cur, since)
            exam_timeout = _exam_timeout_pending(cur)
    dt_ms = (time.time() - t0) * 1000

    return {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "stage_count": stage,
        "last_24h_emails": emails,
        "exam_timeout_pending": exam_timeout,
        "heartbeat_age_minutes": _heartbeat_age_minutes(),
        "db_query_latency_ms": dt_ms,
    }


def main(argv=None):
    # type: (List[str]) -> int
    p = argparse.ArgumentParser(prog="ops.cmd_metrics_dump", add_help=True)
    p.add_argument("--json", action="store_true",
                   help="JSON 输出 (默认人类可读)")
    args = p.parse_args(argv or sys.argv[1:])

    if not _cfg.db_enabled():
        raise UserInputError(
            "DB 未启用 (RECRUIT_DISABLE_DB=1 或 RECRUIT_DRY_RUN=1 或缺密码), "
            "metrics_dump 是纯读 CLI, 无 DB 跑不动。"
        )

    try:
        snapshot = _collect()
    except Exception as e:
        print("[metrics_dump] 抽取失败: {}".format(e), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, sort_keys=True))
    else:
        print(_format_human(snapshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
