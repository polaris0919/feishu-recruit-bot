#!/usr/bin/env python3
"""cron/cron_runner.py —— v3.3 周期任务编排器（替代旧 scripts/cron_runner.py）。

【与旧版差异】
  ① 任务表更新到 v3.3 解耦后的脚本：
       - 新增 inbox.cmd_scan / inbox.cmd_analyze（替代 daily_exam_review 的入站扫描）
       - 新增 ops.cmd_health_check（每日 09:00 ping 一次 DB/IMAP/SMTP/Feishu）
       - auto_reject.cmd_scan_exam_timeout（替代 exam.cmd_exam_timeout_scan，
         2026-04-23 起即触即终；v3.5.11 / 2026-04-22 改成"拒+推 EXAM_REJECT_KEEP 留池"，
         不再 cmd_delete 物理删档，详见 lib/migrations/20260422_v3511_*.sql）
  ② 走 _run_and_report：
       - 失败永远推飞书告警（[CRON FAIL] xxx）
       - 成功**默认静默**：stdout 进 systemd journal 留档，不推飞书。
         真事件（新邮件、笔试超时、催问触发）由各任务自己内部精准推飞书，
         避免 cron_runner 又把整段 stdout 双发一遍噪音老板。
       - 个别任务确实想让 cron_runner 代为整段推送，可在 _TASKS 里把
         notify_stdout=True 打开（后门，目前没人用）。
  ③ 互斥锁 + heartbeat 与旧版一致（避免重叠运行 / 静默失效）。

【v3.5.12 / 2026-04-22 静默改造】
  之前老板反馈每 10min 都收到"扫描了 N 封 / 暂无需催问"等无营养消息。
  根因：cron_runner 第 239 行 if stdout: feishu.send_text(stdout)。
  实际上 inbox.cmd_analyze / common.cmd_interview_reminder /
  auto_reject.cmd_scan_exam_timeout 三家在内部已经单独推过（一邮件一条 /
  一催问一条 / 一拒一条），cron_runner 再推一遍就是噪音双发。
  inbox.cmd_scan 自己不推，但它真有 inserted>0 时下一秒 inbox.cmd_analyze
  就会推，所以不需要 cron_runner 代推。
  ops.cmd_health_check hard_fail 时 exit=1 → 走 [CRON FAIL] 报警；
  健康时报告只进 journal。

【任务表（每次本进程被 cron 唤醒会全部跑一遍）】
  T1 inbox.cmd_scan                          扫所有候选人新入站邮件（含 POST_OFFER_FOLLOWUP）
  T2 inbox.cmd_analyze                       LLM 分析 + 推飞书（stage-aware；POST_OFFER_FOLLOWUP 自带草稿）
  T3 common.cmd_interview_reminder           面试结束催问
  T4 auto_reject.cmd_scan_exam_timeout       笔试 ≥3 天未回复 → 拒信 + 推 EXAM_REJECT_KEEP 留池 + 飞书通知
  T5 ops.cmd_health_check                    系统体检（每天只在 09:xx 跑一次）

【v3.4 变更】
  原 T3 `followup.followup_scanner --auto` 已下线：
    - inbox.cmd_scan 已天然覆盖所有 stage（含 POST_OFFER_FOLLOWUP）；
    - inbox.cmd_analyze 在 v3.4 走 stage-aware prompt 路由，POST_OFFER_FOLLOWUP
      自动用 post_offer_followup prompt 出带草稿的 LLM 结果。

【v3.5 变更】
  followup/cmd_followup_reply 等剧本 wrapper 已彻底删除（followup/ 目录整体下架）。
  老板回信链路：飞书审阅 → agent 直接 `outbound.cmd_send --use-cached-draft`，
  无需独立的 pending_store 文件目录。

【触发节奏建议】
  - 每 10 min 跑一次 cron_runner（系统 crontab）
  - 任务 7 内部用「当前小时 == 9」短路，避免每次都 ping LLM
  - heartbeat 阈值 25h（>= 25h 没成功就报警）

【调用】
  python3 -m cron.cron_runner            # 完整一轮
  python3 -m cron.cron_runner --dry-run  # 只列任务表，不真跑
  python3 -m cron.cron_runner --task inbox_scan   # 只跑某个任务
"""
from __future__ import print_function

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


_LOCK_PATH = Path("/tmp/recruit-cron-runner.lock")
_HEARTBEAT_PATH = Path("<RECRUIT_WORKSPACE>/data/.cron_heartbeat")
_TASK_TIMEOUT_SEC = 300  # 单个子任务上限


# ─── 任务表 ──────────────────────────────────────────────────────────────────
# 每个任务：
#   id              独立 --task 用的 id
#   label           人话说明
#   module          python -m <module>
#   args            额外 CLI 参数
#   only_hours      仅在 datetime.now().hour 在此集合时跑（None = 任何时段）
#   notify_stdout   成功时把 stdout 整段推飞书（默认 False；见模块头注【v3.5.12】）。
#                   ⚠ 大多数任务都内部自己推飞书了（一邮件一条 / 一催问一条 /
#                   一拒一条），所以这里默认 False。万一以后真有任务希望让
#                   runner 代推整段汇总（极少见），把这里改 True 即可。
_TASKS = [
    {
        "id": "inbox_scan",
        "label": "入站邮件扫描（IMAP → talent_emails）",
        "module": "inbox.cmd_scan",
        "args": [],
        "only_hours": None,
        "notify_stdout": False,  # noop 时打 'inserted=0 dup=N'；inserted>0 由
                                 # 下一个任务 inbox.cmd_analyze 单独推飞书
    },
    {
        "id": "inbox_analyze",
        "label": "LLM 分析未读入站邮件 + 推飞书",
        "module": "inbox.cmd_analyze",
        "args": [],
        "only_hours": None,
        "notify_stdout": False,  # 内部对每封新邮件单独 feishu.send_text
    },
    {
        "id": "interview_reminder",
        "label": "面试结束催问",
        "module": "common.cmd_interview_reminder",
        "args": [],
        "only_hours": None,
        "notify_stdout": False,  # 内部对每条催问单独 feishu.send_text
    },
    {
        "id": "exam_timeout_scan",
        "label": "笔试超时拒+留池",
        "module": "auto_reject.cmd_scan_exam_timeout",
        "args": ["--auto"],
        "only_hours": None,
        "notify_stdout": False,  # 内部对每个被拒人 + 总结单独 feishu.send_text；
                                 # noop 时 stdout 已空（v3.5.11 改造）
    },
    {
        "id": "health_check",
        "label": "系统体检",
        "module": "ops.cmd_health_check",
        "args": ["--skip", "dashscope"],  # 体检本身不浪费 LLM 配额
        "only_hours": {9},
        "notify_stdout": False,  # 健康时报告进 journal；hard-fail → exit=1 → [CRON FAIL] 报警
    },
]


# ─── 锁 / heartbeat ─────────────────────────────────────────────────────────

def _try_acquire_lock():
    try:
        import fcntl
    except ImportError:
        return object()
    try:
        fd = os.open(str(_LOCK_PATH), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as e:
        print("[cron_runner] 无法打开锁文件 {}: {}".format(_LOCK_PATH, e), file=sys.stderr)
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    try:
        os.ftruncate(fd, 0)
        os.write(fd, "{}\n".format(os.getpid()).encode("utf-8"))
    except OSError:
        pass
    return fd


def _release_lock(handle):
    if handle is None:
        return
    if isinstance(handle, int):
        try:
            os.close(handle)
        except OSError:
            pass


def _update_heartbeat():
    try:
        _HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _HEARTBEAT_PATH.write_text(datetime.now().isoformat() + "\n", encoding="utf-8")
    except OSError as e:
        print("[cron_runner] heartbeat 写入失败: {}".format(e), file=sys.stderr)


def _check_heartbeat_gap(threshold_hours=25.0):
    if not _HEARTBEAT_PATH.is_file():
        return
    try:
        last = datetime.fromisoformat(_HEARTBEAT_PATH.read_text(encoding="utf-8").strip())
    except Exception:
        return
    gap_h = (datetime.now() - last).total_seconds() / 3600.0
    if gap_h >= threshold_hours:
        _alert_boss(
            "[CRON HEARTBEAT GAP]\n"
            "上次成功运行：{}\n"
            "距今：{:.1f} 小时（阈值 {:.0f}h）\n"
            "可能原因：上一轮 cron 静默失败 / 系统宕机 / cron 服务被禁。".format(
                last.strftime("%Y-%m-%d %H:%M"), gap_h, threshold_hours))


# ─── 告警 ───────────────────────────────────────────────────────────────────

def _alert_boss(text):
    try:
        from lib import feishu
        ok = feishu.send_text(text)
        if not ok:
            print("[cron_runner][ALERT-FAIL] 告警 Feishu 投递失败：\n{}".format(text),
                  file=sys.stderr)
    except Exception as e:
        print("[cron_runner][ALERT-FAIL] {}: {}".format(type(e).__name__, e),
              file=sys.stderr)


# ─── 任务执行 ───────────────────────────────────────────────────────────────

def run_module(module_name, args):
    # type: (str, List[str]) -> Dict[str, Any]
    cmd = [sys.executable, "-m", module_name] + list(args)
    started = time.time()
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_TASK_TIMEOUT_SEC,
        )
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": (result.stdout or b"").decode("utf-8", "replace").strip(),
            "stderr": (result.stderr or b"").decode("utf-8", "replace").strip(),
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False, "exit_code": -1,
            "stdout": (e.stdout or b"").decode("utf-8", "replace") if e.stdout else "",
            "stderr": "TimeoutExpired after {}s".format(_TASK_TIMEOUT_SEC),
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    except Exception as e:
        return {
            "ok": False, "exit_code": -2,
            "stdout": "",
            "stderr": "{}: {}".format(type(e).__name__, e),
            "elapsed_ms": int((time.time() - started) * 1000),
        }


def _run_and_report(task):
    # type: (Dict[str, Any]) -> bool
    """跑单个任务。

    - 失败：永远推飞书 [CRON FAIL] 报警。
    - 成功 + notify_stdout=True：把 stdout 整段推飞书（后门，目前无人用）。
    - 成功 + notify_stdout=False（默认）：**完全静默**，stdout/stderr 仅打到
      本进程的 stdout/stderr，让 systemd journal 留档（journalctl --user
      -u recruit-cron-runner.service）。这是 v3.5.12 改造重点：避免每 10min
      把"扫了 N 封 / 暂无需催问"等 noop 噪音推给老板。
    """
    label = task["label"]
    module = task["module"]
    args = task.get("args") or []
    notify_stdout = bool(task.get("notify_stdout", False))
    res = run_module(module, args)

    if not res["ok"]:
        body = [
            "[CRON FAIL] {}".format(label),
            "module : {}".format(module),
            "exit   : {}".format(res["exit_code"]),
            "elapsed: {}ms".format(res["elapsed_ms"]),
        ]
        if res["stderr"]:
            body.append("stderr :")
            body.append(res["stderr"][-1500:])
        if res["stdout"]:
            body.append("stdout :")
            body.append(res["stdout"][-800:])
        _alert_boss("\n".join(body))
        # 同时把完整输出写到本进程 stderr，留 journal 备查
        if res["stderr"]:
            print("[{}] STDERR:\n{}".format(module, res["stderr"]), file=sys.stderr)
        if res["stdout"]:
            print("[{}] STDOUT:\n{}".format(module, res["stdout"]), file=sys.stderr)
        return False

    # 成功路径：先把 stdout/stderr 转写到本进程，让 systemd journal 收到，
    # 方便老板/我事后 journalctl 翻日志（即便这次没推飞书）。
    if res["stdout"]:
        print("[{}] {}".format(module, res["stdout"]))
    if res["stderr"]:
        # 子任务非致命警告写在 stderr，原样转写
        print("[{}] {}".format(module, res["stderr"]), file=sys.stderr)

    if notify_stdout and res["stdout"]:
        try:
            from lib import feishu
            ok = feishu.send_text(res["stdout"])
            if not ok:
                _alert_boss(
                    "[CRON DELIVER FAIL] {} 子任务成功，但 Feishu 推送失败。\n"
                    "请到 cron 日志中查看原始内容（module={}）".format(label, module))
        except Exception as e:
            _alert_boss("[CRON DELIVER ERR] {}: {}".format(label, e))

    return True


def _select_tasks(args):
    # type: (argparse.Namespace) -> List[Dict[str, Any]]
    if args.task:
        for t in _TASKS:
            if t["id"] == args.task:
                return [t]
        raise SystemExit("[cron_runner] unknown --task {!r}; 可选: {}".format(
            args.task, ", ".join(t["id"] for t in _TASKS)))

    out = []
    cur_hour = datetime.now().hour
    for t in _TASKS:
        only = t.get("only_hours")
        if only is not None and cur_hour not in only:
            continue
        out.append(t)
    return out


def _build_parser():
    p = argparse.ArgumentParser(prog="cron.cron_runner",
                                description="v3.3 周期任务编排器")
    p.add_argument("--task", default=None,
                   help="只跑指定任务 id 一次（绕过 only_hours 限制）")
    p.add_argument("--dry-run", action="store_true",
                   help="只打印任务表，不真跑子进程")
    p.add_argument("--no-lock", action="store_true",
                   help="不加文件锁（仅用于人工调试）")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    tasks = _select_tasks(args)

    if args.dry_run:
        print("[cron_runner] dry-run; 本轮将运行 {} 个任务:".format(len(tasks)))
        for t in tasks:
            print("  · {:<30} {} {}".format(
                t["id"], t["module"], " ".join(t.get("args") or [])))
        return 0

    handle = None
    if not args.no_lock:
        handle = _try_acquire_lock()
        if handle is None:
            print("[cron_runner] 已有运行实例持锁，本次跳过。", file=sys.stderr)
            return 0

    try:
        try:
            from lib import feishu  # noqa: F401  早测 import 错
        except ImportError as e:
            print("[cron_runner] 无法 import feishu，cron 任务无法上报。退出。",
                  file=sys.stderr)
            print("  原因: {}".format(e), file=sys.stderr)
            return 1

        _check_heartbeat_gap()

        any_failed = False
        for t in tasks:
            ok = _run_and_report(t)
            if not ok:
                any_failed = True

        _update_heartbeat()
        return 0 if not any_failed else 1
    finally:
        if handle is not None:
            _release_lock(handle)


if __name__ == "__main__":
    sys.exit(main() or 0)
