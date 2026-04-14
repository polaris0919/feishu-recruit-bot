#!/usr/bin/env python3
"""复杂邮件场景测试辅助层。"""
from __future__ import annotations

import contextlib
import datetime as dt
import os
import sys
import types
from email.mime.text import MIMEText
from email.utils import format_datetime
from unittest import mock

from core_state import load_candidate
from tests.helpers import call_main, mem_tdb, new_candidate, wipe_state


def make_reply_email(from_addr, subject, body, message_id, sent_at=None):
    # type: (str, str, str, str, dt.datetime | None) -> bytes
    msg = MIMEText(body, _charset="utf-8")
    msg["From"] = from_addr
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    if sent_at is None:
        sent_at = dt.datetime(2026, 4, 1, 10, 0, tzinfo=dt.timezone.utc)
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=dt.timezone.utc)
    msg["Date"] = format_datetime(sent_at)
    return msg.as_bytes()


class FakeMailbox:
    """用 scan 次数控制邮件何时出现的假邮箱。"""

    def __init__(self):
        self._messages = []
        self._search_count = 0

    def reset(self):
        self._messages = []
        self._search_count = 0

    def deliver_now(self, raw_bytes):
        self.deliver_on_scan(1, raw_bytes)

    def deliver_on_scan(self, scan_no, raw_bytes):
        self._messages.append({
            "id": str(len(self._messages) + 1).encode("utf-8"),
            "scan_no": scan_no,
            "raw": raw_bytes,
        })

    def _visible_ids(self):
        return [m["id"] for m in self._messages if m["scan_no"] <= self._search_count]

    def _raw_for(self, msg_id):
        for item in self._messages:
            if item["id"] == msg_id:
                return item["raw"]
        return None

    def connect(self):
        mailbox = self

        class _FakeIMAP:
            def select(self, folder):
                return ("OK", [b"1"])

            def search(self, charset, criterion):
                mailbox._search_count += 1
                visible = mailbox._visible_ids()
                return ("OK", [b" ".join(visible)])

            def fetch(self, mid, parts):
                raw = mailbox._raw_for(mid)
                if raw is None:
                    return ("NO", [])
                return ("OK", [(b"1 (RFC822 {100})", raw)])

            def logout(self):
                return ("OK", [])

        return _FakeIMAP()


class ScenarioRunner:
    """声明式驱动多步招聘邮件场景。"""

    def __init__(self):
        wipe_state()
        self.mailbox = FakeMailbox()
        self.sent_reports = []

    def create_round2_pending_candidate(self, name="场景候选人", email="scenario@example.com",
                                        round2_time="2026-04-20 14:00"):
        tid = new_candidate(name=name, email=email)
        call_main("cmd_round1_result", [
            "--talent-id", tid, "--result", "pass", "--email", email,
        ])
        call_main("cmd_exam_result", [
            "--talent-id", tid, "--result", "pass", "--round2-time", round2_time,
        ])
        return tid

    def create_round1_pending_candidate(self, name="一面场景人", email="round1scenario@example.com",
                                        round1_time="2026-04-20 10:00"):
        tid = new_candidate(name=name, email=email)
        call_main("cmd_round1_schedule", ["--talent-id", tid, "--time", round1_time])
        return tid

    def create_confirmed_round2_candidate(self, name="已确认二面人", email="confirmed@example.com",
                                          round2_time="2026-04-20 14:00"):
        tid = self.create_round2_pending_candidate(name=name, email=email, round2_time=round2_time)
        import interview.cmd_confirm as cmd_confirm
        with mock.patch.object(cmd_confirm, "_spawn_calendar_bg", return_value=2468):
            call_main("cmd_round2_confirm", ["--talent-id", tid])
        return tid

    def create_confirmed_round1_candidate(self, name="已确认一面人", email="confirmed-r1@example.com",
                                          round1_time="2026-04-20 10:00"):
        tid = self.create_round1_pending_candidate(name=name, email=email, round1_time=round1_time)
        import interview.cmd_confirm as cmd_confirm
        with mock.patch.object(cmd_confirm, "_spawn_calendar_bg", return_value=1357):
            call_main("cmd_round1_confirm", ["--talent-id", tid])
        return tid

    def set_invite_sent_at(self, talent_id, round_num, invite_sent_at):
        cand = mem_tdb._state["candidates"][talent_id]
        cand["round{}_invite_sent_at".format(round_num)] = invite_sent_at

    def set_last_email_id(self, talent_id, context, email_id):
        cand = mem_tdb._state["candidates"][talent_id]
        cand["{}_last_email_id".format(context)] = email_id

    def candidate(self, talent_id):
        return load_candidate(talent_id)

    def assert_boss_pending(self, talent_id, round_num, time):
        pending = mem_tdb.get_boss_confirm_pending(talent_id, round_num)
        assert pending["pending"] is True, pending
        assert pending["time"] == time, pending

    def assert_last_email_id_updated(self, talent_id, context, email_id):
        cand = self.candidate(talent_id)
        actual = cand.get("{}_last_email_id".format(context))
        assert actual == email_id, (actual, email_id)

    @contextlib.contextmanager
    def patch_daily_exam_review(self, review_mod, llm_side_effect=None):
        fake_feishu = types.SimpleNamespace(
            send_text=lambda text: self.sent_reports.append(text) or True
        )
        with mock.patch.object(review_mod, "connect_imap", side_effect=self.mailbox.connect), \
             mock.patch.dict(sys.modules, {"feishu": fake_feishu}):
            if llm_side_effect is None:
                yield
            else:
                with mock.patch.object(review_mod, "_llm_analyze_reply", side_effect=llm_side_effect):
                    yield


def subprocess_result_from_call_main(cmd, *args, **kwargs):
    # type: (list[str], object, object) -> types.SimpleNamespace
    script_name = os.path.basename(cmd[1]).replace(".py", "")
    out, err, rc = call_main(script_name, cmd[2:])
    return types.SimpleNamespace(
        stdout=(out or "").encode("utf-8"),
        stderr=(err or "").encode("utf-8"),
        returncode=rc,
    )
