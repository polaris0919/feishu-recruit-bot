#!/usr/bin/env python3
import unittest
from unittest import mock

from tests.helpers import call_main, new_candidate, wipe_state
from lib.core_state import load_candidate, save_candidate


def _setup_post_offer():
    tid = new_candidate()
    cand = load_candidate(tid)
    cand["stage"] = "POST_OFFER_FOLLOWUP"
    cand["candidate_email"] = "offer@example.com"
    cand["candidate_name"] = "Offer候选人"
    save_candidate(tid, cand)
    return tid


class TestSendOnboardingOffer(unittest.TestCase):

    def setUp(self):
        wipe_state()

    def test_send_success_notifies_hr(self):
        tid = _setup_post_offer()
        send_res = {
            "ok": True,
            "message_id": "<offer-1@example.com>",
            "email_id": "eml_offer_1",
            "attachments": [{"name": "致邃实习协议-2026年4月版.docx", "auto": True}],
        }
        from offer import cmd_send_onboarding_offer as mod
        with mock.patch.object(mod, "send_outbound_template", return_value=send_res) as send_mock, \
                mock.patch.object(mod, "_notify_hr", return_value=True) as notify_mock:
            out, err, rc = call_main("offer.cmd_send_onboarding_offer", [
                "--talent-id", tid,
                "--onboard-date", "2026-06-01",
                "--json",
            ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn('"daily_rate": "350"', out)
        self.assertIn('"hr_notified": true', out)
        send_mock.assert_called_once()
        notify_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.kwargs["vars"]["daily_rate"], "350")
        self.assertEqual(send_mock.call_args.kwargs["vars"]["onboard_date"], "2026-06-01")

    def test_custom_daily_rate(self):
        tid = _setup_post_offer()
        from offer import cmd_send_onboarding_offer as mod
        with mock.patch.object(mod, "send_outbound_template", return_value={
            "ok": True, "message_id": "<offer-2@example.com>", "email_id": "eml_offer_2"
        }) as send_mock, mock.patch.object(mod, "_notify_hr", return_value=True):
            out, err, rc = call_main("offer.cmd_send_onboarding_offer", [
                "--talent-id", tid,
                "--onboard-date", "2026-06-01",
                "--daily-rate", "400",
                "--json",
            ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn('"daily_rate": "400"', out)
        self.assertEqual(send_mock.call_args.kwargs["vars"]["daily_rate"], "400")

    def test_wrong_stage_rejected(self):
        tid = new_candidate()
        from offer import cmd_send_onboarding_offer as mod
        with mock.patch.object(mod, "send_outbound_template") as send_mock, \
                mock.patch.object(mod, "_notify_hr") as notify_mock:
            _, err, rc = call_main("offer.cmd_send_onboarding_offer", [
                "--talent-id", tid,
                "--onboard-date", "2026-06-01",
            ])
        self.assertNotEqual(rc, 0)
        self.assertIn("POST_OFFER_FOLLOWUP", err)
        send_mock.assert_not_called()
        notify_mock.assert_not_called()

    def test_send_failure_does_not_notify_hr(self):
        tid = _setup_post_offer()
        from offer import cmd_send_onboarding_offer as mod
        with mock.patch.object(mod, "send_outbound_template", return_value={
            "ok": False, "returncode": 4, "stderr": "smtp failed", "stdout": ""
        }), mock.patch.object(mod, "_notify_hr") as notify_mock:
            _, err, rc = call_main("offer.cmd_send_onboarding_offer", [
                "--talent-id", tid,
                "--onboard-date", "2026-06-01",
            ])
        self.assertNotEqual(rc, 0)
        self.assertIn("未通知 HR", err)
        notify_mock.assert_not_called()

    def test_dry_run_skips_hr_notify(self):
        tid = _setup_post_offer()
        from offer import cmd_send_onboarding_offer as mod
        with mock.patch.object(mod, "_send_offer", return_value={
            "ok": True,
            "message_id": "<dry-run-1@local>",
            "email_id": None,
            "attachments": [{"name": "致邃实习协议-2026年4月版.docx", "auto": True}],
        }), mock.patch.object(mod, "_notify_hr") as notify_mock:
            out, err, rc = call_main("offer.cmd_send_onboarding_offer", [
                "--talent-id", tid,
                "--onboard-date", "2026-06-01",
                "--dry-run",
                "--json",
            ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn('"dry_run": true', out)
        self.assertIn('"hr_notified": false', out)
        notify_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
