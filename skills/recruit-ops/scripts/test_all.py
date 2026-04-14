#!/usr/bin/env python3
"""
招聘系统全量测试套件 — 聚合入口。
运行方式：
    python3 test_all.py              # 全部测试
    python3 -m pytest tests/         # 同等效果

各模块测试文件：
    tests/test_candidate.py  — 候选人基础操作（新建 / 状态 / 搜索 / 删除）
    tests/test_intake.py     — CV 导入（cmd_ingest_cv）
    tests/test_round1.py     — 一面结果（cmd_round1_result）
    tests/test_exam.py       — 笔试（cmd_exam_result / exam_prereview / daily_exam_review）
    tests/test_round2.py     — 二面（cmd_round2_result / 调度流程）
    tests/test_common.py     — 公共跨阶段操作（改期请求 / 改期扫描）
    tests/test_infra.py      — 基础设施（core_state / talent_db / feishu）
"""
import sys
import unittest

from tests.test_candidate import TestNewCandidate, TestStatus, TestSearch, TestRemove
from tests.test_intake import TestIngestCv
from tests.test_round1 import TestRound1Result, TestRound1SchedulingFlow
from tests.test_exam import TestExamResult, TestExamPrereview, TestDailyExamReview
from tests.test_round2 import TestRound2Result, TestRound2SchedulingFlow
from tests.test_common import TestRescheduleRequest
from tests.test_infra import TestCoreState, TestDbFallback, TestFeishu

if __name__ == "__main__":
    loader = unittest.TestLoader()
    loader.sortTestMethodsUsing = None
    suite = unittest.TestSuite()
    for cls in [
        TestNewCandidate, TestStatus, TestSearch,
        TestIngestCv,
        TestRound1Result, TestRound1SchedulingFlow,
        TestExamResult, TestExamPrereview, TestDailyExamReview,
        TestRound2Result, TestRound2SchedulingFlow,
        TestRemove,
        TestRescheduleRequest,
        TestCoreState, TestDbFallback, TestFeishu,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
