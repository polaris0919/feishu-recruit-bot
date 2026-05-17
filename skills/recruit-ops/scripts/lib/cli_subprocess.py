#!/usr/bin/env python3
"""通用 atomic CLI 调度层。

═══════════════════════════════════════════════════════════════════════════════
为什么独立成一个模块（而不是塞进 lib.bg_helpers）
═══════════════════════════════════════════════════════════════════════════════
- `bg_helpers.send_outbound_template()` 是邮件语义 helper；
- `auto_reject.executor._delete_talent()` 走 `talent.cmd_delete`，根本不是邮件；
- 把 `talent.cmd_delete` 通过邮件 helper 启子进程，会让"删档"反向出现在
  邮件模块的 import 拓扑里——语义边界被污染。
所以"通用 sync subprocess 调度"独立成本模块，让两类业务都能干净地复用，
而 helper 文件本身保持各自的语义边界。

═══════════════════════════════════════════════════════════════════════════════
设计原则（不要绕过）
═══════════════════════════════════════════════════════════════════════════════
- 哑执行器：不读 RECRUIT_DISABLE_SIDE_EFFECTS、不识别业务 dry_run、不制造
  fake 成功。所有 fake / 短路逻辑由调用方按业务语义自己处理（例如
  `send_outbound_template()` 的 `side_effects_disabled()` 早返回，
  `_delete_talent(dry_run=True)` 的早返回）。
- JSON 解析必须用反向扫描语义：从 stdout 末尾倒着找第一条以 `{` 开头的
  非空行解析。**不要**简化为"取最后一行"——atomic CLI 的 stdout 末尾常
  跟着空行 / debug 行，简单取最后一行会 silently regression。
- 子进程 env 行为与 `bg_helpers._recruit_subprocess_env()` 对齐：
  注入 PYTHONPATH=<scripts_dir>:existing 与 RECRUIT_WORKSPACE_ROOT。
"""
from __future__ import print_function

import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

from lib.recruit_paths import scripts_dir, workspace_path


def build_subprocess_env():
    # type: () -> Dict[str, str]
    """子进程环境的 SSOT(Single Source of Truth)。

    B3 (v3.8.7): 之前 bg_helpers._recruit_subprocess_env 与本模块
    _build_env 是同体双轨,理由是"跨模块依赖私有函数是反模式"。结果
    每次调一处又得手维护另一处。本轮把规范实现搬到这里(公开 API),
    bg_helpers 改成 thin re-export, 真正只此一份。

    返回的 env 注入两件事:
      - PYTHONPATH 头部追加 scripts_dir(), 让 `python -m <module>` 能 import lib.*
      - RECRUIT_WORKSPACE_ROOT 缺省值 = workspace_path(), 让 atomic CLI
        在脱离 git 上下文(systemd / cron)时也能定位仓库根

    其他副作用开关 (RECRUIT_DRY_RUN 等) 由 os.environ.copy() 自然透传,
    本函数不主动注入也不主动剥离。
    """
    env = os.environ.copy()
    scripts_path = scripts_dir()
    existing = (env.get("PYTHONPATH") or "").strip()
    env["PYTHONPATH"] = (
        scripts_path if not existing else scripts_path + os.pathsep + existing
    )
    env.setdefault("RECRUIT_WORKSPACE_ROOT", str(workspace_path()))
    return env


_build_env = build_subprocess_env


def _scan_last_json_line(stdout):
    # type: (str) -> Optional[Dict[str, Any]]
    """反向扫描 stdout，取最后一条以 `{` 开头的非空行作 JSON 解析。

    为什么不是 `splitlines()[-1]`：atomic CLI 的 stdout 末尾经常追加空行
    或 debug 一行（例如 self_verify 的 trace），简单取最后一行解析失败
    率高。反向扫描兼容这种情况。

    返回 None 表示 stdout 里没有任何 JSON 行,**不抛异常**——这是哑执行
    器契约：解析失败由调用方按业务语义判断（多半是子进程异常退出,业务
    层会先看 returncode）。
    """
    if not stdout:
        return None
    for line in reversed([ln for ln in stdout.splitlines() if ln.strip()]):
        if line.lstrip().startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                return None
    return None


def run_module(module, args, timeout=120, parse_json=False):
    # type: (str, List[str], int, bool) -> Dict[str, Any]
    """同步执行 `python -m <module> <args...>` 并返回统一结构。

    Args:
        module: atomic CLI 模块名,例如 "outbound.cmd_send" / "talent.cmd_delete"。
        args: CLI 参数列表（不含 module 本身）。
        timeout: 子进程上限秒数,默认 120。**cron_runner 等 300s 调度场景必须
            显式传 `timeout=...`,不要依赖默认值**（见 plan §"暂不迁移 cron_runner"）。
        parse_json: True 时尝试反向扫描 stdout 末尾 JSON 行,把解析结果挂到
            返回 dict 顶层（与 stdout/stderr/returncode 字段并列）。

    Returns:
        统一返回结构：
            {
                "ok": bool,                     # returncode == 0
                "returncode": int,              # 子进程返回码;timeout/exception 时 -1
                "stdout": str,
                "stderr": str,
                "cmd": list[str],               # 实际执行的 argv,便于失败时排查
                "json": dict | None,            # parse_json=True 时的解析结果;失败为 None
            }

    哑执行器契约：
        - 不读 RECRUIT_DISABLE_SIDE_EFFECTS,不识别 dry_run。
        - JSON 解析失败不抛,只把 "json" 设为 None。
        - timeout / 启动失败也不抛,统一进 returncode=-1 的 ok=False 分支,
          让调用方通过返回 dict 决定告警 / 重试 / 业务侧 fallback。
    """
    cmd = [sys.executable, "-m", module] + list(args)
    env = build_subprocess_env()

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            cwd=scripts_dir(),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "timeout after {}s".format(timeout),
            "cmd": cmd,
            "json": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "{}: {}".format(type(exc).__name__, exc),
            "cmd": cmd,
            "json": None,
        }

    stdout = proc.stdout.decode("utf-8", "replace") if proc.stdout else ""
    stderr = proc.stderr.decode("utf-8", "replace") if proc.stderr else ""
    parsed = _scan_last_json_line(stdout) if parse_json else None
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "cmd": cmd,
        "json": parsed,
    }
