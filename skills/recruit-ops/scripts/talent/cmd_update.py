#!/usr/bin/env python3
"""talent/cmd_update.py —— v3.3 候选人状态机【唯一】写入入口。

【职责】
  1. 更新 talents.current_stage（natural transitions 自由跨；非常规跳转需 --force）
  2. 更新 talents 的字段（白名单内，可一次原子更新多个字段）
  3. 写 talent_events 审计
  4. 自验证：assert_talent_state(talent_id, expected_stage / expected_fields)

【绝对不做】
  - 不发邮件（要发邮件 caller 自行调 outbound/cmd_send.py）
  - 不调 LLM、不动 talent_emails

【两种用法】
  阶段切换：
      --talent-id X --stage NEW_STAGE [--reason 文案] [--force]
      默认只允许 natural transitions（见下方白名单）；--force 跨任意 stage 但写入审计 forced=true。

  字段编辑（v3.4：推荐 --set，可重复，可与 --stage 同时使用）：
      --talent-id X --set FIELD=VALUE [--set FIELD2=VALUE2 ...] [--reason 文案]
      支持占位符：
        VALUE='__NULL__'  → 写 NULL
        VALUE='__NOW__'   → 写当前 CST 时间（ISO 8601, +08:00）

  兼容旧用法（仅改单字段）：
      --talent-id X --field FIELD --value VALUE
      内部转成单条 --set；行为不变，但会打 DeprecationWarning（stderr）。

  支持同时改 stage 和多字段（一次原子调用）。

【调用示例】
  # v3.5 一面排期：agent 用 lib.run_chain 把 outbound.cmd_send + 本命令串起来
  # （旧 cmd_round1_schedule wrapper 已彻底下线）：
  PYTHONPATH=scripts python3 -m talent.cmd_update --talent-id t_xxx \\
      --stage ROUND1_SCHEDULING \\
      --set round1_time="2026-04-25 14:00" \\
      --set round1_invite_sent_at=__NOW__ \\
      --set round1_confirm_status=PENDING \\
      --set round1_calendar_event_id=__NULL__ \\
      --set wait_return_round=__NULL__ \\
      --reason "boss 安排一面"

  # 老板手动把候选人挪到下一轮
  PYTHONPATH=scripts python3 -m talent.cmd_update \\
      --talent-id t_abc --stage ROUND2_SCHEDULING --reason "笔试通过"

  # 修候选人电话
  PYTHONPATH=scripts python3 -m talent.cmd_update \\
      --talent-id t_abc --set phone=13800001111

  # 强制跨阶段（少见，需写明原因）
  PYTHONPATH=scripts python3 -m talent.cmd_update \\
      --talent-id t_abc --stage POST_OFFER_FOLLOWUP --force --reason "电话敲定，跳过 round2"
"""
from __future__ import print_function

import argparse
import json
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from lib import talent_db
from lib.cli_wrapper import run_with_self_verify, UserInputError
from lib.self_verify import assert_talent_state


# ─── natural transitions 白名单（D2）─────────────────────────────────────────
#
# 设计原则：
#   - 列出"业务上自然路径"。能合理走到下一站的 (from, to) 都列上。
#   - REJECT_DELETE 类终态不允许从这里"复活"（要复活就老板加 --force 自负其责）。
#   - WAIT_RETURN 是 special：可以从 WAIT_RETURN 回到 ROUND1_SCHEDULING / ROUND2_SCHEDULING
#     （取决于 wait_return_round），脚本内做特殊判定。
#
# 任何不在表里的转换需要 --force，并且飞书审计里会标 forced=true 让老板看见。

# v3.6 (2026-04-27/28) 状态机瘦身影响：
#   - 删除所有指向 *_DONE_REJECT_DELETE 的 edge —— 这两个 stage 已下线。
#     reject_delete 经 interview.cmd_result / exam.cmd_exam_result 内部直接走
#     talent_db.delete_talent()，不再经停任何 stage。
#   - ROUND2_SCHEDULED → OFFER_HANDOFF → POST_OFFER_FOLLOWUP 合并为
#     ROUND2_SCHEDULED → POST_OFFER_FOLLOWUP 一步。
_NATURAL_TRANSITIONS = frozenset({
    # 入库 → 一面
    ("NEW",                       "ROUND1_SCHEDULING"),
    # v3.5.7 §5.11：HR 一步排一面（intake.cmd_route_interviewer 派单 + 建日历 + 发邀约
    # 一气呵成），跳过 ROUND1_SCHEDULING 直接进 ROUND1_SCHEDULED
    ("NEW",                       "ROUND1_SCHEDULED"),

    # 一面流程
    ("ROUND1_SCHEDULING",         "ROUND1_SCHEDULED"),
    ("ROUND1_SCHEDULED",          "ROUND1_SCHEDULING"),          # 改期回调
    ("ROUND1_SCHEDULED",          "EXAM_SENT"),                  # 一面通过 → 发笔试
    ("ROUND1_SCHEDULED",          "WAIT_RETURN"),                # 暂缓

    # 笔试流程
    ("EXAM_SENT",                 "EXAM_REVIEWED"),
    ("EXAM_SENT",                 "EXAM_REJECT_KEEP"),           # 笔试不答自动拒留池
    ("EXAM_REVIEWED",             "ROUND2_SCHEDULING"),          # 笔试通过 → 二面
    ("EXAM_REVIEWED",             "EXAM_REJECT_KEEP"),

    # 二面流程
    ("ROUND2_SCHEDULING",         "ROUND2_SCHEDULED"),
    ("ROUND2_SCHEDULED",          "ROUND2_SCHEDULING"),          # 改期回调
    ("ROUND2_SCHEDULED",          "POST_OFFER_FOLLOWUP"),        # 二面通过 → 进 offer 沟通
    ("ROUND2_SCHEDULED",          "ROUND2_DONE_REJECT_KEEP"),
    ("ROUND2_SCHEDULED",          "WAIT_RETURN"),

    # WAIT_RETURN 出口（按 wait_return_round 决定）
    ("WAIT_RETURN",               "ROUND1_SCHEDULING"),
    ("WAIT_RETURN",               "ROUND2_SCHEDULING"),
})


def _is_natural(from_stage, to_stage):
    # type: (str, str) -> bool
    return (from_stage, to_stage) in _NATURAL_TRANSITIONS


# ─── argparse ────────────────────────────────────────────────────────────────

def _build_parser():
    p = argparse.ArgumentParser(
        prog="talent.cmd_update",
        description="v3.3 候选人状态/字段更新（natural transitions + --force）",
    )
    p.add_argument("--talent-id", required=True)
    p.add_argument("--stage", help="新 current_stage")
    # v3.4：多字段原子更新
    p.add_argument("--set", dest="set_pairs", action="append", default=[],
                   metavar="FIELD=VALUE",
                   help="字段编辑（白名单内），可重复。VALUE='__NULL__' 设为 NULL，"
                        "VALUE='__NOW__' 设为当前 CST 时间。")
    # 兼容旧用法（单字段；内部转成单条 --set）
    p.add_argument("--field",
                   help="[DEPRECATED] 单字段编辑，请用 --set FIELD=VALUE")
    p.add_argument("--value",
                   help="[DEPRECATED] --field 的值；'__NULL__' 表示设为 NULL")
    p.add_argument("--force", action="store_true",
                   help="允许 natural-transitions 之外的 stage 跳转")
    p.add_argument("--reason", default="",
                   help="审计原因（推荐填，方便事后追溯）")
    p.add_argument("--actor", default="cli",
                   help="审计 actor（默认 cli）")
    p.add_argument("--dry-run", action="store_true",
                   help="模拟流程，不真的写 DB")
    p.add_argument("--json", action="store_true")
    return p


# ─── 占位符 / value 解析 ─────────────────────────────────────────────────────

_NULL_TOKEN = "__NULL__"
_NOW_TOKEN = "__NOW__"


def _resolve_token(raw):
    # type: (Any) -> Any
    """把 CLI 字符串 value 转换成实际值。
    '__NULL__' → None；'__NOW__' → 当前 CST ISO 字符串；其他原样返回。
    """
    if raw is None:
        return None
    if raw == _NULL_TOKEN:
        return None
    if raw == _NOW_TOKEN:
        return datetime.now().replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    return raw


def _parse_set_pairs(set_pairs):
    # type: (List[str]) -> List[Tuple[str, Any]]
    """解析 --set FIELD=VALUE 列表。
    保持顺序（让 caller 能预测原子写入顺序）；同 FIELD 出现两次取后者并 stderr 警告。
    """
    seen_keys = {}  # type: Dict[str, int]
    pairs = []  # type: List[Tuple[str, Any]]
    for raw in set_pairs:
        if "=" not in raw:
            raise UserInputError(
                "--set 格式必须为 FIELD=VALUE：{!r}".format(raw))
        key, _, val = raw.partition("=")
        key = key.strip()
        if not key:
            raise UserInputError("--set 字段名不能为空：{!r}".format(raw))
        if key in seen_keys:
            print("[cmd_update] WARN --set {} 出现多次，取最后一次".format(key),
                  file=sys.stderr)
            pairs[seen_keys[key]] = (key, _resolve_token(val))
        else:
            seen_keys[key] = len(pairs)
            pairs.append((key, _resolve_token(val)))
    return pairs


# ─── 主流程 ──────────────────────────────────────────────────────────────────

def _do_update(args):
    # type: (argparse.Namespace) -> int
    talent_id = args.talent_id

    # ── 兼容旧 --field/--value：折叠到 set_pairs ──
    legacy_pairs = []
    if args.field is not None or args.value is not None:
        if args.field is None or args.value is None:
            raise UserInputError("--field 和 --value 必须成对出现（建议改用 --set FIELD=VALUE）")
        print("[cmd_update] DeprecationWarning: --field/--value 已过时，"
              "请改用 --set {}=...".format(args.field), file=sys.stderr)
        legacy_pairs.append((args.field, _resolve_token(args.value)))

    # ── 解析 --set ──
    set_pairs = legacy_pairs + _parse_set_pairs(args.set_pairs)

    if not args.stage and not set_pairs:
        raise UserInputError("--stage 与 --set 至少要提供一个")

    snap = talent_db.get_one(talent_id)
    if not snap:
        raise UserInputError("候选人 {} 不存在".format(talent_id))
    current_stage = snap.get("current_stage") or snap.get("stage") or ""

    # ── transition 校验 ──
    transition_info = None
    forced = False
    if args.stage:
        new_stage = args.stage
        if new_stage == current_stage:
            print("[cmd_update] stage unchanged ({} == {})；no-op".format(
                current_stage, new_stage), file=sys.stderr)
        else:
            natural = _is_natural(current_stage, new_stage)
            if not natural and not args.force:
                raise UserInputError(
                    "stage 跳转 {} → {} 不在 natural transitions 白名单。"
                    "如果你确定要跨，请加 --force 并在 --reason 写清原因。".format(
                        current_stage, new_stage))
            forced = bool(args.force and not natural)
            transition_info = {"from": current_stage, "to": new_stage,
                               "natural": natural, "forced": forced}

    # ── 字段白名单预校验（v3.4 Phase 0.1）──
    # 早抛错：CLI 输入错误应在拿任何 DB 连接 / 拉旧值之前就拒绝，方便上游
    # 编排器（v3.5 起是 agent + lib.run_chain）把 UserInputError 干净冒上去。
    for field, _ in set_pairs:
        if field not in talent_db.TALENT_UPDATABLE_FIELDS:
            raise UserInputError(
                "字段 {!r} 不在白名单。current_stage 请用 --stage；"
                "其他字段需要先添加到 lib/talent_db.py 的 _TALENT_UPDATABLE_FIELDS。".format(field))

    # ── 字段变更摘要 ──
    field_changes = []  # type: List[Dict[str, Any]]
    for field, new_value in set_pairs:
        try:
            old_value = talent_db.get_talent_field(talent_id, field)
        except ValueError as e:
            raise UserInputError(str(e))
        field_changes.append({
            "field": field,
            "old": _safe(old_value),
            "new": _safe(new_value),
        })

    # ── 写 DB ─────────────────────────────────────────────────────────────
    if args.dry_run:
        print("[cmd_update] DRY-RUN no-op transition={} fields={}".format(
            transition_info, field_changes), file=sys.stderr)
    else:
        # 原子顺序：先字段，再 stage（stage 副作用如 clear_round_followup_fields
        # 可能依赖最新字段；先字段后 stage 让 reset 类副作用拥有最新字段视图）。
        for field, new_value in set_pairs:
            ok = talent_db.update_talent_field(talent_id, field, new_value)
            if not ok:
                # update_talent_field 在 "no-op" 时也返回 False（值没变 / talent 不存在）
                # 这里只在 talent 已经被 get_one 验证过存在的前提下出现，多半是值没变
                # → 不抛错，继续。
                pass
        if field_changes:
            talent_db.save_audit_event(
                talent_id, "field.changed",
                payload={
                    "changes": [{"field": c["field"], "new": c["new"]}
                                for c in field_changes],
                    "reason": args.reason,
                },
                actor=args.actor,
            )
        if transition_info:
            ok = talent_db.set_current_stage(
                talent_id,
                transition_info["to"],
                actor=args.actor,
                reason=args.reason or None,
            )
            if not ok:
                raise RuntimeError(
                    "set_current_stage 返回 False（候选人不存在）")

        # v3.5.9：candidate_name 改了 → 同步刷一下 by_name 软链
        # warn-continue：alias 不影响 DB 写入成功
        if any(f == "candidate_name" for f, _ in set_pairs):
            try:
                from lib import candidate_aliases as _ca
                new_name = next(v for f, v in set_pairs if f == "candidate_name")
                _ca.rebuild_alias_for(talent_id, new_name)
            except Exception as e:
                print("[cmd_update] alias 重建异常: {}".format(e), file=sys.stderr)
            if forced:
                talent_db.save_audit_event(
                    talent_id, "stage.forced",
                    payload={"from": transition_info["from"],
                             "to": transition_info["to"],
                             "reason": args.reason},
                    actor=args.actor,
                )

    # ── 自验证（D5）───────────────────────────────────────────────────────
    if not args.dry_run:
        expected_stage = transition_info["to"] if transition_info else None
        expected_fields = None
        if field_changes:
            expected_fields = {}
            for c in field_changes:
                expected_fields[c["field"]] = c["new"] if c["new"] is not None else None
        assert_talent_state(
            talent_id,
            expected_stage=expected_stage,
            expected_fields=expected_fields,
        )

    # ── 输出 ─────────────────────────────────────────────────────────────
    result = {
        "ok": True,
        "talent_id": talent_id,
        "transition": transition_info,
        "field_changes": field_changes,
        "dry_run": bool(args.dry_run),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("[cmd_update] OK talent={} transition={} fields={}".format(
            talent_id, transition_info, field_changes))
    return 0


def _safe(v):
    return v if v is None or isinstance(v, (str, int, float, bool)) else str(v)


def main(argv=None):
    args = _build_parser().parse_args(argv)
    return _do_update(args)


if __name__ == "__main__":
    run_with_self_verify("talent.cmd_update", main)
