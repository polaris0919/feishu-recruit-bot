#!/usr/bin/env python3
"""lib/run_chain.py —— v3.4 跨命令编排 helper。

【目标】
让 agent 编排器（v3.5 起 round1/round2/followup/interview 下的剧本 wrapper
全部下线，由 agent 用本 helper 在进程内拼链）把多个 v3.3 atomic CLI
链起来，像写 shell 脚本一样直观，但：
  1. 不 fork subprocess，直接进程内调用 main(argv)
  2. 每一步的 --json 输出可作为后续步骤的变量（占位符 {step_name.field}）
  3. 任意一步失败立刻短路，并把已经成功的步骤一起回报
  4. dry-run / actor 等共享参数自动透传

【设计取舍】
  * 不假装是 transaction 引擎：DB 真正的原子性靠各 cmd_* 内部用一次 connection；
    run_chain 只保证"前一步失败时不会跑后续步骤"。已经发出的邮件（cmd_send 成
    功而 cmd_update 失败）需要靠 self_verify + 飞书告警让人看到——v3.4 不试图
    做自动 rollback，因为发邮件本身就不可逆。
  * 不抢 cli_wrapper 的活：每个被链的 cmd_* 自己有 self_verify；run_chain 只在
    chain 整体失败时把上下文摘要打到 stderr / 返回 dict。

【典型用法】
    from lib.run_chain import Step, run_chain

    chain_result = run_chain([
        Step("send", "outbound.cmd_send", args=[
            "--talent-id", tid,
            "--template", "round1_invite",
            "--vars", "round1_time={}".format(t),
        ]),
        Step("update", "talent.cmd_update", args=[
            "--talent-id", tid,
            "--stage", "ROUND1_SCHEDULING",
            "--set", "round1_time={}".format(t),
            "--set", "round1_invite_sent_at={send.sent_at}",  # ← 占位符
            "--set", "round1_confirm_status=PENDING",
            "--set", "round1_calendar_event_id=__NULL__",
        ]),
    ], dry_run=False)

    # chain_result["ok"] == True
    # chain_result["steps"]["send"]["message_id"]
    # chain_result["steps"]["update"]["transition"]["to"]

【失败行为】
  * 任意 step 抛异常或 main 返回非零 → chain 立即停
  * 返回 {"ok": False, "failed_at": step_name, "error": str(e), "steps": {...已完成的}}
  * 调用方决定是 raise 还是只 log
"""
from __future__ import print_function

import importlib
import io
import json
import re
import sys
from contextlib import redirect_stdout, redirect_stderr
from typing import Any, Dict, List, Optional


class ChainStepError(Exception):
    """run_chain 内某一步失败时抛出（如果调用方选择 raise_on_failure=True）。"""
    def __init__(self, step_name, exit_code, stderr_tail, partial_results):
        # type: (str, int, str, Dict[str, dict]) -> None
        self.step_name = step_name
        self.exit_code = exit_code
        self.stderr_tail = stderr_tail
        self.partial_results = partial_results
        super(ChainStepError, self).__init__(
            "chain step {!r} failed (exit={}): {}".format(
                step_name, exit_code, stderr_tail[-300:].strip()))


class Step(object):
    """一个 chain step。

    Args:
        name:        chain 内步骤标识，后续 step 用 {name.field} 引用其 JSON 输出
        module:      点分模块路径，例如 "outbound.cmd_send" / "talent.cmd_update"
        args:        argv 列表（不含 sys.argv[0]）。元素允许 {step_name.json_field} 占位符
        require_json: 是否要求该步骤 --json 输出可解析（默认 True；自动追加 --json）
        optional:    True 时该步失败不算 chain 失败（仅 stderr 提示）。默认 False
    """
    __slots__ = ("name", "module", "args", "require_json", "optional")

    def __init__(self, name, module, args, require_json=True, optional=False):
        # type: (str, str, List[str], bool, bool) -> None
        if not name or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            raise ValueError("Step name {!r} 必须是合法 Python 标识符".format(name))
        self.name = name
        self.module = module
        self.args = list(args)
        self.require_json = require_json
        self.optional = optional


# ─── 占位符解析 ──────────────────────────────────────────────────────────────

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_.]*)\}")


def _resolve_placeholder(s, results):
    # type: (str, Dict[str, dict]) -> str
    """把 '{step.field}' 或 '{step.nested.field}' 替换成 results 中实际值。

    未解析到的占位符会抛 KeyError，方便 chain 早失败。
    """
    if not isinstance(s, str) or "{" not in s:
        return s

    def _sub(m):
        step_name, field_path = m.group(1), m.group(2)
        if step_name not in results:
            raise KeyError(
                "占位符 {{{}.{}}}  引用了尚未执行（或失败）的 step".format(
                    step_name, field_path))
        cur = results[step_name]
        for key in field_path.split("."):
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                raise KeyError(
                    "占位符 {{{}.{}}}  在 step {!r} 输出里找不到字段 {!r}".format(
                        step_name, field_path, step_name, key))
        if cur is None:
            return "__NULL__"  # 与 cmd_update __NULL__ 占位符语义对齐
        return str(cur)

    return _PLACEHOLDER_RE.sub(_sub, s)


def _resolve_args(args, results):
    # type: (List[str], Dict[str, dict]) -> List[str]
    return [_resolve_placeholder(a, results) for a in args]


# ─── 主入口 ──────────────────────────────────────────────────────────────────

def run_chain(steps, dry_run=False, raise_on_failure=False, verbose=False):
    # type: (List[Step], bool, bool, bool) -> Dict[str, Any]
    """串行执行 steps；任一失败短路。

    Args:
        steps:            Step 列表
        dry_run:          True 时给所有 step 自动加 --dry-run
        raise_on_failure: True 时抛 ChainStepError；False 时返回 ok=False 的 dict
        verbose:          True 时打印每步的 stderr 到 host stderr

    Returns:
        {"ok": True, "steps": {step_name: parsed_json, ...}}              成功
        {"ok": False, "failed_at": name, "error": msg,
         "exit_code": rc, "stderr_tail": ..., "steps": {...partial...}}   失败
    """
    results = {}  # type: Dict[str, dict]

    for step in steps:
        # 1) 解析占位符
        try:
            resolved_args = _resolve_args(step.args, results)
        except KeyError as e:
            err_msg = "step {!r} placeholder error: {}".format(step.name, e)
            if raise_on_failure:
                raise ChainStepError(step.name, -1, err_msg, results)
            return {"ok": False, "failed_at": step.name, "error": err_msg,
                    "exit_code": -1, "stderr_tail": err_msg, "steps": results}

        # 2) 自动追加 --json / --dry-run
        argv = list(resolved_args)
        if step.require_json and "--json" not in argv:
            argv.append("--json")
        if dry_run and "--dry-run" not in argv:
            argv.append("--dry-run")

        # 3) 进程内调 main
        out_buf, err_buf = io.StringIO(), io.StringIO()
        try:
            mod = importlib.import_module(step.module)
        except ImportError as e:
            err_msg = "import {!r} failed: {}".format(step.module, e)
            if raise_on_failure:
                raise ChainStepError(step.name, -1, err_msg, results)
            return {"ok": False, "failed_at": step.name, "error": err_msg,
                    "exit_code": -1, "stderr_tail": err_msg, "steps": results}

        if not hasattr(mod, "main"):
            err_msg = "module {!r} 没有 main(argv) 函数".format(step.module)
            if raise_on_failure:
                raise ChainStepError(step.name, -1, err_msg, results)
            return {"ok": False, "failed_at": step.name, "error": err_msg,
                    "exit_code": -1, "stderr_tail": err_msg, "steps": results}

        rc = 0
        exc = None  # type: Optional[BaseException]
        try:
            with redirect_stdout(out_buf), redirect_stderr(err_buf):
                ret = mod.main(argv)
            rc = int(ret) if isinstance(ret, int) else 0
        except SystemExit as e:
            rc = int(e.code) if isinstance(e.code, int) else (1 if e.code else 0)
        except Exception as e:
            rc = 1
            exc = e

        out_text = out_buf.getvalue()
        err_text = err_buf.getvalue()

        if verbose and err_text:
            print("[run_chain] {} stderr:\n{}".format(step.name, err_text),
                  file=sys.stderr)

        # 4) 处理失败
        if rc != 0 or exc is not None:
            err_tail = err_text or (str(exc) if exc else "")
            if step.optional:
                if verbose:
                    print("[run_chain] optional step {!r} failed (rc={}); 继续".format(
                        step.name, rc), file=sys.stderr)
                results[step.name] = {"ok": False, "exit_code": rc,
                                      "stderr_tail": err_tail[-400:]}
                continue
            err_msg = "step {!r} exited rc={} err={}".format(
                step.name, rc, err_tail[-400:].strip())
            if raise_on_failure:
                raise ChainStepError(step.name, rc, err_tail, results)
            return {"ok": False, "failed_at": step.name, "error": err_msg,
                    "exit_code": rc, "stderr_tail": err_tail[-2000:],
                    "steps": results}

        # 5) 解析 JSON 输出
        if step.require_json:
            try:
                parsed = json.loads(out_text.strip().splitlines()[-1])
            except (ValueError, IndexError) as e:
                err_msg = ("step {!r} stdout 不是合法 JSON（require_json=True）: "
                           "{}\nstdout=\n{}").format(step.name, e, out_text[:600])
                if raise_on_failure:
                    raise ChainStepError(step.name, rc, err_msg, results)
                return {"ok": False, "failed_at": step.name, "error": err_msg,
                        "exit_code": rc, "stderr_tail": err_msg,
                        "steps": results}
            results[step.name] = parsed
        else:
            results[step.name] = {"ok": True, "stdout": out_text}

    return {"ok": True, "steps": results}
