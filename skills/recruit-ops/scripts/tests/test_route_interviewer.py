#!/usr/bin/env python3
"""tests/test_route_interviewer.py —— v3.5.7 §5.11 一面派单路由测试。

【验证】
  1. cpp_first 优先：has_cpp=True → cpp，无视学历
  2. 硕士/博士 + has_cpp != True → master
  3. 本科 + has_cpp != True → bachelor
  4. has_cpp=null + 学历不可识别 → ambiguous=true（fail closed）
  5. config 缺面试官 open_id 或仍是占位符 → config_error=true（fail closed）
  6. 候选人不存在 → UserInputError，rc=1
  7. 不存在 fallback：never `fallback_used=true`
  8. zero side effect：不写 DB、不发飞书
"""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import tests.helpers as helpers  # noqa: F401  side-effect: stub talent_db / env

os.environ["RECRUIT_SUPPRESS_SELF_VERIFY_ALERT"] = "1"

from lib import config as _cfg  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# 配置 mock：直接把 lib.config._cache["feishu"] 替换成测试值
# ────────────────────────────────────────────────────────────────────────────

_REAL_OPEN_IDS = {
    "interviewer_master_open_id":   "ou_master_real",
    "interviewer_bachelor_open_id": "ou_bachelor_real",
    "interviewer_cpp_open_id":      "ou_cpp_real",
}

_PLACEHOLDER_OPEN_IDS = {
    "interviewer_master_open_id":   "ou_PLACEHOLDER_INTERVIEWER_MASTER",
    "interviewer_bachelor_open_id": "ou_PLACEHOLDER_INTERVIEWER_BACHELOR",
    "interviewer_cpp_open_id":      "ou_PLACEHOLDER_INTERVIEWER_CPP",
}

_BASE_FEISHU = {
    "app_id": "test", "app_secret": "test",
    "boss_open_id": "ou_boss", "hr_open_id": "ou_hr",
    "calendar_id": "cal_test",
}


def _install_feishu_config(open_ids):
    _cfg._ensure_loaded()
    new_cfg = dict(_BASE_FEISHU)
    new_cfg.update(open_ids)
    _cfg._cache["feishu"] = new_cfg


# ────────────────────────────────────────────────────────────────────────────
# 候选人 fixtures
# ────────────────────────────────────────────────────────────────────────────

def _seed(talent_id, education=None, has_cpp=None, name="测试候选人"):
    helpers.mem_tdb._state.setdefault("candidates", {})[talent_id] = {
        "talent_id": talent_id,
        "candidate_name": name,
        "candidate_email": "{}@example.com".format(talent_id),
        "stage": "NEW",
        "education": education,
        "has_cpp": has_cpp,
    }


def _call(talent_id, json_out=True):
    argv = ["--talent-id", talent_id]
    if json_out:
        argv.append("--json")
    return helpers.call_main("intake.cmd_route_interviewer", argv)


# ════════════════════════════════════════════════════════════════════════════

class TestRouteInterviewerCppFirst(unittest.TestCase):
    """has_cpp=True 永远走 cpp，无视学历。"""

    def setUp(self):
        helpers.wipe_state()
        _install_feishu_config(_REAL_OPEN_IDS)

    def test_cpp_master_routes_to_cpp(self):
        _seed("t_cpp_master", education="硕士", has_cpp=True)
        out, err, rc = _call("t_cpp_master")
        self.assertEqual(rc, 0, "stderr=" + err)
        p = json.loads(out)
        self.assertTrue(p["ok"])
        self.assertEqual(p["interviewer_roles"], ["cpp"])
        self.assertEqual(p["interviewer_open_ids"], ["ou_cpp_real"])
        self.assertFalse(p["ambiguous"])
        self.assertFalse(p["config_error"])
        self.assertFalse(p["fallback_used"])

    def test_cpp_bachelor_routes_to_cpp(self):
        _seed("t_cpp_bach", education="本科", has_cpp=True)
        out, _, rc = _call("t_cpp_bach")
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(p["interviewer_roles"], ["cpp"])
        self.assertEqual(p["interviewer_open_ids"], ["ou_cpp_real"])

    def test_cpp_unknown_education_still_routes_to_cpp(self):
        """has_cpp=True 时，即使学历未识别也派 cpp（C++ 优先级最高）。"""
        _seed("t_cpp_null_edu", education=None, has_cpp=True)
        out, _, rc = _call("t_cpp_null_edu")
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(p["interviewer_roles"], ["cpp"])
        self.assertFalse(p["ambiguous"])


class TestRouteInterviewerEducation(unittest.TestCase):
    """has_cpp != True 时按学历分。"""

    def setUp(self):
        helpers.wipe_state()
        _install_feishu_config(_REAL_OPEN_IDS)

    def test_master_routes_to_master(self):
        _seed("t_m", education="硕士", has_cpp=False)
        out, _, rc = _call("t_m")
        p = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(p["interviewer_roles"], ["master"])
        self.assertEqual(p["interviewer_open_ids"], ["ou_master_real"])

    def test_phd_routes_to_master(self):
        _seed("t_phd", education="博士", has_cpp=False)
        out, _, rc = _call("t_phd")
        p = json.loads(out)
        self.assertEqual(p["interviewer_roles"], ["master"])

    def test_bachelor_routes_to_bachelor(self):
        _seed("t_b", education="本科", has_cpp=False)
        out, _, rc = _call("t_b")
        p = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(p["interviewer_roles"], ["bachelor"])
        self.assertEqual(p["interviewer_open_ids"], ["ou_bachelor_real"])

    def test_has_cpp_null_with_master_still_routes(self):
        """has_cpp=None（未判断）+ 硕士 → 仍能派 master（学历是已知字段）。"""
        _seed("t_null_m", education="硕士", has_cpp=None)
        out, _, rc = _call("t_null_m")
        p = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(p["interviewer_roles"], ["master"])


class TestRouteInterviewerAmbiguous(unittest.TestCase):
    """has_cpp 不为 True 且学历不可识别 → ambiguous=true。"""

    def setUp(self):
        helpers.wipe_state()
        _install_feishu_config(_REAL_OPEN_IDS)

    def test_null_edu_null_cpp_is_ambiguous(self):
        _seed("t_amb1", education=None, has_cpp=None)
        out, _, rc = _call("t_amb1")
        p = json.loads(out)
        self.assertEqual(rc, 0)  # 退出码 0（chain 仍要消费 JSON）
        self.assertFalse(p["ok"])
        self.assertTrue(p["ambiguous"])
        self.assertIsNotNone(p["ambiguous_reason"])
        self.assertEqual(p["interviewer_roles"], [])
        self.assertEqual(p["interviewer_open_ids"], [])
        self.assertFalse(p["fallback_used"])

    def test_unknown_edu_string_is_ambiguous(self):
        """学历是 LLM 写歪了的字符串（如「专科」）+ has_cpp=False → ambiguous。"""
        _seed("t_amb2", education="专科", has_cpp=False)
        out, _, rc = _call("t_amb2")
        p = json.loads(out)
        self.assertTrue(p["ambiguous"])
        self.assertEqual(p["interviewer_open_ids"], [])

    def test_empty_string_edu_is_ambiguous(self):
        _seed("t_amb3", education="", has_cpp=False)
        out, _, rc = _call("t_amb3")
        p = json.loads(out)
        self.assertTrue(p["ambiguous"])


class TestRouteInterviewerConfigError(unittest.TestCase):
    """interviewer open_id 缺失 / 占位符 → config_error=true。"""

    def setUp(self):
        helpers.wipe_state()

    def test_placeholder_cpp_blocks_routing(self):
        _install_feishu_config(_PLACEHOLDER_OPEN_IDS)
        _seed("t_cfg1", education="本科", has_cpp=True)
        out, _, rc = _call("t_cfg1")
        p = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertFalse(p["ok"])
        self.assertTrue(p["config_error"])
        self.assertIn("PLACEHOLDER", p["config_error_detail"])
        self.assertEqual(p["interviewer_open_ids"], [])  # fail closed

    def test_missing_master_open_id(self):
        broken = dict(_REAL_OPEN_IDS)
        broken["interviewer_master_open_id"] = ""  # 显式清空
        _install_feishu_config(broken)
        _seed("t_cfg2", education="硕士", has_cpp=False)
        out, _, rc = _call("t_cfg2")
        p = json.loads(out)
        self.assertTrue(p["config_error"])
        self.assertIn("interviewer_master_open_id", p["config_error_detail"])

    def test_ambiguous_does_not_check_config(self):
        """ambiguous 在 config check 之前就把 open_ids 清空，
        config_error 应保持 false（不淹没真正的根因）。"""
        _install_feishu_config(_PLACEHOLDER_OPEN_IDS)
        _seed("t_amb_with_cfg_broken", education=None, has_cpp=None)
        out, _, rc = _call("t_amb_with_cfg_broken")
        p = json.loads(out)
        self.assertTrue(p["ambiguous"])
        self.assertFalse(p["config_error"])


class TestRouteInterviewerInputErrors(unittest.TestCase):

    def setUp(self):
        helpers.wipe_state()
        _install_feishu_config(_REAL_OPEN_IDS)

    def test_missing_talent_returns_rc1(self):
        out, err, rc = _call("t_nonexistent")
        self.assertEqual(rc, 1)
        self.assertIn("不存在", err)


class TestRouteInterviewerNoSideEffects(unittest.TestCase):
    """zero side effect 守护：调用后 talents 不应被修改、不应有飞书 / 邮件 / 日历调用。"""

    def setUp(self):
        helpers.wipe_state()
        _install_feishu_config(_REAL_OPEN_IDS)

    def test_no_db_writes(self):
        _seed("t_nse", education="本科", has_cpp=True, name="原名")
        before = dict(helpers.mem_tdb._state["candidates"]["t_nse"])
        _call("t_nse")
        after = helpers.mem_tdb._state["candidates"]["t_nse"]
        self.assertEqual(before, after)

    def test_no_feishu_calls(self):
        _seed("t_nse2", education="本科", has_cpp=True)
        with mock.patch("lib.feishu.send_text") as m_send, \
             mock.patch("lib.feishu.send_text_to_hr") as m_hr, \
             mock.patch("lib.feishu.create_interview_event") as m_cal:
            _call("t_nse2")
        m_send.assert_not_called()
        m_hr.assert_not_called()
        m_cal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
