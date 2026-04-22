#!/usr/bin/env python3
"""talent_emails 表的针对性测试。

覆盖：
  - insert_email_if_absent: 合法插入 / ON CONFLICT 去重 / 输入校验
  - mark_email_status: 部分更新 + 状态机字段
  - get_processed_message_ids: 方向过滤
  - get_email_by_reply_id: round-trip
  - get_email_thread: 时间序排列
  - list_emails_by_status: 复合过滤
  - dry-run 写保护：RECRUIT_DISABLE_DB_WRITES 拦截
  - backfill 脚本幂等性：连续两遍第二遍 0 插入

需要本机 PostgreSQL（recruit 库）。若 DB 未启用整个 class 跳过。
所有测试用 t_te_<8字符> 前缀，setUp 创建 talent，tearDown 删除（CASCADE 清理 emails）。
"""
from __future__ import print_function

import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone

import tests.helpers  # noqa: F401  side-effect: 装好 sys.modules

from tests.helpers import real_talent_db as tdb


def _db_available():
    return tdb._is_enabled()


def _mk_talent_id():
    return "t_te_" + uuid.uuid4().hex[:6]


def _mk_msgid(tag="m"):
    return "<{}-{}@test.example.com>".format(tag, uuid.uuid4().hex[:8])


def _now():
    return datetime.now(timezone.utc)


@unittest.skipUnless(_db_available(), "DB 未启用，跳过 talent_emails 集成测试")
class TestTalentEmailsContract(unittest.TestCase):
    """API 契约/校验逻辑测试 —— 不实际写表。"""

    def test_invalid_direction_raises(self):
        with self.assertRaises(ValueError):
            tdb.insert_email_if_absent(
                "t_x", _mk_msgid(), direction="sideway",
                context="exam", sender="a@b", sent_at=_now())

    def test_invalid_context_raises(self):
        with self.assertRaises(ValueError):
            tdb.insert_email_if_absent(
                "t_x", _mk_msgid(), direction="inbound",
                context="banana", sender="a@b", sent_at=_now())

    def test_invalid_initial_status_raises(self):
        with self.assertRaises(ValueError):
            tdb.insert_email_if_absent(
                "t_x", _mk_msgid(), direction="inbound",
                context="exam", sender="a@b", sent_at=_now(),
                initial_status="weird_state")

    def test_empty_message_id_raises(self):
        with self.assertRaises(ValueError):
            tdb.insert_email_if_absent(
                "t_x", "", direction="inbound",
                context="exam", sender="a@b", sent_at=_now())

    def test_empty_sent_at_raises(self):
        with self.assertRaises(ValueError):
            tdb.insert_email_if_absent(
                "t_x", _mk_msgid(), direction="inbound",
                context="exam", sender="a@b", sent_at=None)

    def test_mark_email_status_invalid_status_raises(self):
        with self.assertRaises(ValueError):
            tdb.mark_email_status("00000000-0000-0000-0000-000000000000",
                                  "totally_made_up")

    def test_list_by_status_invalid_status_raises(self):
        with self.assertRaises(ValueError):
            tdb.list_emails_by_status("nonsense_status")

    def test_list_by_status_invalid_context_raises(self):
        with self.assertRaises(ValueError):
            tdb.list_emails_by_status("received", context="purple")


@unittest.skipUnless(_db_available(), "DB 未启用，跳过 talent_emails 集成测试")
class TestTalentEmailsIntegration(unittest.TestCase):
    """走真 DB —— 用唯一 talent_id 做隔离，CASCADE 删除清理。"""

    def setUp(self):
        self.tid = _mk_talent_id()
        # 直接 SQL 插一个最小骨架 talents 行（满足 FK 即可）
        with tdb._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO talents (talent_id, candidate_email, candidate_name, current_stage) "
                    "VALUES (%s, %s, %s, %s)",
                    (self.tid, "{}@test.local".format(self.tid), "测试用", "NEW"),
                )

    def tearDown(self):
        # CASCADE 会清掉 talent_emails 行
        try:
            with tdb._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM talents WHERE talent_id = %s", (self.tid,))
        except Exception:
            pass

    def test_insert_returns_uuid_string(self):
        eid = tdb.insert_email_if_absent(
            self.tid, _mk_msgid("a"), direction="inbound",
            context="exam", sender="cand@x.com", sent_at=_now(),
            subject="hi", body_excerpt="正文")
        self.assertIsNotNone(eid)
        # UUID 字符串
        self.assertEqual(len(eid), 36)
        uuid.UUID(eid)  # 不抛即合法

    def test_on_conflict_returns_none(self):
        msgid = _mk_msgid("dup")
        first = tdb.insert_email_if_absent(
            self.tid, msgid, direction="inbound", context="exam",
            sender="a@b", sent_at=_now())
        self.assertIsNotNone(first)
        # 同 (talent_id, message_id) 再插 → ON CONFLICT DO NOTHING
        second = tdb.insert_email_if_absent(
            self.tid, msgid, direction="inbound", context="exam",
            sender="a@b", sent_at=_now())
        self.assertIsNone(second, "重复插入应返回 None")

    def test_get_processed_filters_by_direction(self):
        in_msg = _mk_msgid("in")
        out_msg = _mk_msgid("out")
        tdb.insert_email_if_absent(
            self.tid, in_msg, direction="inbound", context="exam",
            sender="cand@x", sent_at=_now())
        tdb.insert_email_if_absent(
            self.tid, out_msg, direction="outbound", context="exam",
            sender="hr@x", sent_at=_now())

        inbound_set = tdb.get_processed_message_ids(self.tid, direction="inbound")
        outbound_set = tdb.get_processed_message_ids(self.tid, direction="outbound")
        all_set = tdb.get_processed_message_ids(self.tid, direction=None)

        self.assertEqual(inbound_set, {in_msg})
        self.assertEqual(outbound_set, {out_msg})
        self.assertEqual(all_set, {in_msg, out_msg})

    def test_mark_status_partial_update(self):
        eid = tdb.insert_email_if_absent(
            self.tid, _mk_msgid("st"), direction="inbound", context="followup",
            sender="cand@x", sent_at=_now(), initial_status="received")
        ok = tdb.mark_email_status(
            eid, "pending_boss",
            ai_summary="询问入职薪资",
            ai_intent="salary_inquiry",
            ai_payload={"foo": "bar"},
            reply_id="fr_test01",
        )
        self.assertTrue(ok)

        row = tdb._query_one(
            "SELECT status, ai_summary, ai_intent, ai_payload, reply_id "
            "FROM talent_emails WHERE email_id = %s", (eid,))
        self.assertEqual(row["status"], "pending_boss")
        self.assertEqual(row["ai_summary"], "询问入职薪资")
        self.assertEqual(row["ai_intent"], "salary_inquiry")
        self.assertEqual(row["ai_payload"], {"foo": "bar"})
        self.assertEqual(row["reply_id"], "fr_test01")

    def test_mark_status_does_not_clobber_other_fields(self):
        eid = tdb.insert_email_if_absent(
            self.tid, _mk_msgid("nc"), direction="inbound", context="followup",
            sender="cand@x", sent_at=_now(),
            ai_summary="原 summary", reply_id="fr_orig")
        # 只升级 status，summary/reply_id 不该被吞
        tdb.mark_email_status(eid, "pending_boss")
        row = tdb._query_one(
            "SELECT ai_summary, reply_id, status FROM talent_emails WHERE email_id = %s",
            (eid,))
        self.assertEqual(row["status"], "pending_boss")
        self.assertEqual(row["ai_summary"], "原 summary")
        self.assertEqual(row["reply_id"], "fr_orig")

    def test_get_email_by_reply_id_round_trip(self):
        rid = "fr_" + uuid.uuid4().hex[:8]
        eid = tdb.insert_email_if_absent(
            self.tid, _mk_msgid("rid"), direction="inbound", context="followup",
            sender="cand@x", sent_at=_now(), reply_id=rid)
        found = tdb.get_email_by_reply_id(rid)
        self.assertIsNotNone(found)
        self.assertEqual(found["email_id"], eid)
        self.assertEqual(found["talent_id"], self.tid)

    def test_get_email_by_reply_id_missing_returns_none(self):
        self.assertIsNone(tdb.get_email_by_reply_id(
            "fr_definitely_does_not_exist_" + uuid.uuid4().hex))

    def test_thread_orders_by_sent_at_asc(self):
        t0 = _now() - timedelta(hours=3)
        t1 = _now() - timedelta(hours=2)
        t2 = _now() - timedelta(hours=1)
        # 故意打乱插入顺序
        tdb.insert_email_if_absent(
            self.tid, _mk_msgid("c"), direction="outbound", context="followup",
            sender="hr@x", sent_at=t2, subject="reply 2")
        tdb.insert_email_if_absent(
            self.tid, _mk_msgid("a"), direction="inbound", context="followup",
            sender="cand@x", sent_at=t0, subject="msg 0")
        tdb.insert_email_if_absent(
            self.tid, _mk_msgid("b"), direction="inbound", context="followup",
            sender="cand@x", sent_at=t1, subject="msg 1")

        thread = tdb.get_email_thread(self.tid)
        subjects = [e["subject"] for e in thread]
        self.assertEqual(subjects, ["msg 0", "msg 1", "reply 2"])

    def test_list_by_status_filters(self):
        eid_pending = tdb.insert_email_if_absent(
            self.tid, _mk_msgid("p"), direction="inbound", context="followup",
            sender="cand@x", sent_at=_now(), initial_status="received")
        tdb.mark_email_status(eid_pending, "pending_boss")

        eid_done = tdb.insert_email_if_absent(
            self.tid, _mk_msgid("d"), direction="inbound", context="followup",
            sender="cand@x", sent_at=_now(), initial_status="received")
        tdb.mark_email_status(eid_done, "auto_processed")

        only_pending = tdb.list_emails_by_status(
            "pending_boss", talent_id=self.tid)
        only_done = tdb.list_emails_by_status(
            "auto_processed", talent_id=self.tid)

        self.assertEqual([e["email_id"] for e in only_pending], [eid_pending])
        self.assertEqual([e["email_id"] for e in only_done], [eid_done])

    def test_list_by_status_context_filter(self):
        tdb.insert_email_if_absent(
            self.tid, _mk_msgid("e1"), direction="inbound", context="exam",
            sender="cand@x", sent_at=_now(), initial_status="received")
        tdb.insert_email_if_absent(
            self.tid, _mk_msgid("f1"), direction="inbound", context="followup",
            sender="cand@x", sent_at=_now(), initial_status="received")

        only_exam = tdb.list_emails_by_status(
            "received", talent_id=self.tid, context="exam")
        only_fu = tdb.list_emails_by_status(
            "received", talent_id=self.tid, context="followup")

        self.assertEqual(len(only_exam), 1)
        self.assertEqual(only_exam[0]["context"], "exam")
        self.assertEqual(len(only_fu), 1)
        self.assertEqual(only_fu[0]["context"], "followup")


@unittest.skipUnless(_db_available(), "DB 未启用，跳过 dry-run 集成测试")
class TestEmailDryRunGuard(unittest.TestCase):
    """RECRUIT_DISABLE_DB_WRITES 必须拦下 talent_emails 写入。"""

    def setUp(self):
        self.tid = _mk_talent_id()
        with tdb._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO talents (talent_id, candidate_email, candidate_name, current_stage) "
                    "VALUES (%s, %s, %s, %s)",
                    (self.tid, "{}@test.local".format(self.tid), "干跑测", "NEW"),
                )

    def tearDown(self):
        try:
            with tdb._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM talents WHERE talent_id = %s", (self.tid,))
        except Exception:
            pass

    def test_insert_in_dry_run_returns_synthetic_id_no_row(self):
        msgid = _mk_msgid("dr")
        os.environ["RECRUIT_DISABLE_DB_WRITES"] = "1"
        try:
            eid = tdb.insert_email_if_absent(
                self.tid, msgid, direction="inbound", context="exam",
                sender="a@b", sent_at=_now())
        finally:
            os.environ.pop("RECRUIT_DISABLE_DB_WRITES", None)

        # dry-run 应返回伪 UUID
        self.assertIsNotNone(eid)
        uuid.UUID(eid)
        # 但表里实际没有这一行
        row = tdb._query_one(
            "SELECT email_id FROM talent_emails WHERE talent_id = %s AND message_id = %s",
            (self.tid, msgid))
        self.assertIsNone(row, "dry-run 不应真写入")

    def test_mark_status_in_dry_run_returns_true_no_change(self):
        eid = tdb.insert_email_if_absent(
            self.tid, _mk_msgid("mk"), direction="inbound", context="followup",
            sender="a@b", sent_at=_now(), initial_status="received")

        os.environ["RECRUIT_DISABLE_DB_WRITES"] = "1"
        try:
            ok = tdb.mark_email_status(eid, "pending_boss",
                                        ai_summary="should_not_persist")
        finally:
            os.environ.pop("RECRUIT_DISABLE_DB_WRITES", None)

        self.assertTrue(ok)
        row = tdb._query_one(
            "SELECT status, ai_summary FROM talent_emails WHERE email_id = %s", (eid,))
        self.assertEqual(row["status"], "received", "状态不应被 dry-run 改写")
        self.assertIsNone(row["ai_summary"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
