#!/usr/bin/env python3
"""offer/cmd_send_onboarding_offer.py —— 发送入职前 Offer 邮件并通知 HR。

职责：
  1. 仅允许 POST_OFFER_FOLLOWUP 阶段候选人使用；
  2. 通过 outbound.cmd_send 发送 onboarding_offer 模板邮件；
  3. 邮件成功发送后，再通知 HR 候选人已进入 offer 发放阶段。
"""
from __future__ import print_function

import argparse
import json
import sys
from typing import Any, Dict, Optional

from lib import talent_db
from lib.bg_helpers import send_outbound_template
from lib.cli_wrapper import UserInputError, run_with_self_verify

DEFAULT_DAILY_RATE = "350"
DEFAULT_INTERVIEW_FEEDBACK = "您在面试过程中展现出了良好的专业能力和沟通表现，我们期待与您一起工作。"
DEFAULT_EVALUATION_CRITERIA = "入职后将结合实际项目参与情况、工作质量和团队协作表现进行持续评估。"


def _build_parser():
    p = argparse.ArgumentParser(
        prog="offer.cmd_send_onboarding_offer",
        description="发送 onboarding_offer 入职前邮件，成功后通知 HR",
    )
    p.add_argument("--talent-id", required=True)
    p.add_argument("--onboard-date", required=True, help="入职日期，例如 2026-06-01")
    p.add_argument("--daily-rate", default=DEFAULT_DAILY_RATE,
                   help="日薪，默认 350")
    p.add_argument("--position-title", default="量化研究",
                   help="模板中的岗位标题，默认 量化研究")
    p.add_argument("--interview-feedback", default=DEFAULT_INTERVIEW_FEEDBACK,
                   help="模板中的面试表现说明")
    p.add_argument("--evaluation-criteria", default=DEFAULT_EVALUATION_CRITERIA,
                   help="模板中的后续考核说明")
    p.add_argument("--actor", default="system")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    return p


def _load_candidate(talent_id):
    # type: (str) -> Dict[str, Any]
    cand = talent_db.get_one(talent_id) if talent_db._is_enabled() else None
    if not cand:
        raise UserInputError("未找到候选人 {}".format(talent_id))
    return cand


def _stage_of(cand):
    # type: (Dict[str, Any]) -> str
    return cand.get("current_stage") or cand.get("stage") or "NEW"


def _notify_hr(cand, talent_id, send_result):
    # type: (Dict[str, Any], str, Dict[str, Any]) -> bool
    from lib import feishu

    name = cand.get("candidate_name") or talent_id
    email = cand.get("candidate_email") or "未记录"
    message_id = send_result.get("message_id") or "?"
    text = (
        "[Offer 发放阶段通知]\n"
        "{name}（{tid}）已通过面试阶段，进入 offer 发放阶段。\n"
        "候选人邮箱：{email}\n"
        "入职前所需资料和信息已通过邮箱发送。\n"
        "邮件 message_id：{message_id}"
    ).format(name=name, tid=talent_id, email=email, message_id=message_id)
    return bool(feishu.send_text_to_hr(text))


def _send_offer(args, cand):
    # type: (argparse.Namespace, Dict[str, Any]) -> Dict[str, Any]
    vars_payload = {
        "position_title": args.position_title,
        "interview_feedback": args.interview_feedback,
        "daily_rate": str(args.daily_rate).strip() or DEFAULT_DAILY_RATE,
        "onboard_date": args.onboard_date.strip(),
        "evaluation_criteria": args.evaluation_criteria,
    }
    if args.dry_run:
        vars_payload["dry_run"] = "1"

    if args.dry_run:
        # send_outbound_template 的 side-effect guard 不理解业务 dry-run；
        # 这里直接调 outbound.cmd_send 的 dry-run 路径，确保附件/模板也被校验。
        from lib.cli_subprocess import run_module
        cmd_args = [
            "--talent-id", args.talent_id,
            "--template", "onboarding_offer",
            "--context", "followup",
            "--json",
            "--dry-run",
            "--vars",
        ]
        for key, value in vars_payload.items():
            if key == "dry_run":
                continue
            cmd_args.append("{}={}".format(key, value))
        res = run_module("outbound.cmd_send", cmd_args, timeout=120, parse_json=True)
        parsed = dict(res.get("json") or {})
        parsed.update({
            "ok": res["ok"],
            "returncode": res["returncode"],
            "stdout": res["stdout"],
            "stderr": res["stderr"],
            "cmd": res["cmd"],
        })
        return parsed

    return send_outbound_template(
        talent_id=args.talent_id,
        template="onboarding_offer",
        context="followup",
        vars=vars_payload,
        timeout=120,
    )


def main(argv=None):
    args = _build_parser().parse_args(argv)
    args.talent_id = args.talent_id.strip()
    args.onboard_date = args.onboard_date.strip()
    if not args.onboard_date:
        raise UserInputError("--onboard-date 不能为空")

    cand = _load_candidate(args.talent_id)
    stage = _stage_of(cand)
    if stage != "POST_OFFER_FOLLOWUP":
        raise UserInputError(
            "候选人 {} 当前 stage={}，只有 POST_OFFER_FOLLOWUP 才能发送入职前邮件。".format(
                args.talent_id, stage))

    send_result = _send_offer(args, cand)
    if not send_result.get("ok"):
        print(
            "ERROR: 入职前邮件发送失败，未通知 HR。returncode={} stderr={} stdout={}".format(
                send_result.get("returncode"),
                (send_result.get("stderr") or "").strip()[:500],
                (send_result.get("stdout") or "").strip()[:500],
            ),
            file=sys.stderr,
        )
        return 1

    hr_notified = False
    if not args.dry_run:
        hr_notified = _notify_hr(cand, args.talent_id, send_result)
        if not hr_notified:
            print(
                "WARN: 入职前邮件已发送，但 HR 飞书通知失败。请手动通知 HR。talent_id={}".format(
                    args.talent_id),
                file=sys.stderr,
            )

    result = {
        "ok": True,
        "talent_id": args.talent_id,
        "candidate_name": cand.get("candidate_name"),
        "candidate_email": cand.get("candidate_email"),
        "stage": stage,
        "template": "onboarding_offer",
        "onboard_date": args.onboard_date,
        "daily_rate": str(args.daily_rate).strip() or DEFAULT_DAILY_RATE,
        "message_id": send_result.get("message_id"),
        "email_id": send_result.get("email_id"),
        "attachments": send_result.get("attachments"),
        "hr_notified": hr_notified,
        "dry_run": bool(args.dry_run),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(
            "[onboarding_offer 已处理]\n"
            "- talent_id: {tid}\n"
            "- 入职时间: {date}\n"
            "- 日薪: {rate} 元/天\n"
            "- 邮件: 已发送（message_id={msg}）\n"
            "- HR 通知: {hr}".format(
                tid=args.talent_id,
                date=args.onboard_date,
                rate=result["daily_rate"],
                msg=result.get("message_id") or "?",
                hr="已通知" if hr_notified else ("dry-run 跳过" if args.dry_run else "失败"),
            )
        )
    return 0


if __name__ == "__main__":
    run_with_self_verify("offer.cmd_send_onboarding_offer", main)
