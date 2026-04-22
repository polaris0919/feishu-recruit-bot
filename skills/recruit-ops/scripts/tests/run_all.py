#!/usr/bin/env python3
"""
招聘系统全量测试套件 — 聚合入口（v3.5）。

运行方式：
    cd <RECRUIT_WORKSPACE>/skills/recruit-ops
    PYTHONPATH=scripts python3 scripts/tests/run_all.py    # 全部测试
    uv run python3 -m pytest scripts/tests/                # 同等效果

v3.5 调整：
    - 删除 TestRound1SchedulingFlow / TestRound2SchedulingFlow（wrapper 已下线）
    - 删除 tests/test_v34_phase2.py（followup wrapper 全删，整文件下线）
    - 新增 tests/test_v35_phase1_inbox_general.py（统一 intent enum）
    - 新增 tests/test_v35_phase3_exam_grader.py（lib.exam_grader）
    - 新增 tests/test_v35_phase4_notify.py（feishu.cmd_notify）
    - 新增 tests/test_agent_chain.py（v3.5 5 条 + v3.5.1 2 条 = 7 条端到端 agent chain）
"""
import unittest

from tests.test_candidate import TestNewCandidate, TestStatus, TestSearch, TestRemove
from tests.test_intake import TestIngestCv, TestAttachCvImportsToCandidateDir
from tests.test_candidate_storage import (  # v3.5.8 候选人统一目录
    TestPathCalc, TestAttachmentDirRouting, TestEnsureCandidateDirs, TestImportCv,
)
from tests.test_candidate_aliases import (  # v3.5.9 by_name 软链
    TestSanitize, TestRebuildAlias, TestRemoveAlias, TestRebuildAll,
)
from tests.test_round1 import TestRound1Result
from tests.test_exam import TestExamResult
from tests.test_round2 import TestRound2Result
from tests.test_common import TestTodayInterviews, TestAtomicCLIRegression
from tests.test_infra import TestCoreState, TestDbFallback, TestFeishu, TestEmailWatch
from tests.test_followup import (
    TestStripQuotedReply, TestFlattenHeader,
    TestHttpRetry, TestSideEffectGuardDB,
)
from tests.test_email_templates import (
    TestRendererEngine, TestTemplateContents, TestCallSitesUseRenderer,
)
from tests.test_auto_reject import (
    TestFindTimeoutCandidates, TestScanMain,
)
from tests.test_v33_phase1 import (
    TestSelfVerify, TestCmdSend, TestCmdUpdate, TestCmdDelete,
)
from tests.test_run_chain import TestRunChainBasic
from tests.test_v34_phase1 import (
    TestPromptsModule, TestAnalyzerRouting, TestScrubDraft,
    TestCmdSendUseCachedDraft,
)
from tests.test_v34_phase5 import (
    TestCmdCalendarCreate, TestCmdCalendarDelete, TestBgHelpersCalendarDispatch,
)
from tests.test_v35_phase1_inbox_general import (
    TestInboxGeneralPromptSchema, TestStageAwareRouting,
)
from tests.test_v35_phase3_exam_grader import (
    TestExamGraderLibrary, TestCmdExamAiReviewIntegration,
)
from tests.test_v35_phase4_notify import (
    TestCmdNotifyBoss, TestCmdNotifyHr, TestOldPushAlertGone,
    TestCmdNotifyInterviewer,  # v3.5.7 §5.11
)
from tests.test_agent_chain import (
    TestRound1ScheduleChain, TestRound1RescheduleChain,
    TestDeferUntilReturnChain, TestExamPassToRound2Chain,
    TestPostOfferOneClickSendChain,
    TestExamRejectKeepChain, TestWaitReturnPokeChain,
    TestOnboardingOfferChain,
    TestRound1DispatchChain,  # v3.5.7 §5.11 端到端
)
from tests.test_email_attachments import (
    TestSafeName, TestExtractMetadata, TestExtractAndSave,
    TestExtractAndSaveValidation,
)
from tests.test_auto_attachments import (  # v3.5.10 onboarding_offer 默认附件
    TestAutoAttachmentsRegistry, TestCmdSendAutoAttach,
)
from tests.test_route_interviewer import (  # v3.5.7 §5.11 路由 atomic
    TestRouteInterviewerCppFirst,
    TestRouteInterviewerEducation,
    TestRouteInterviewerAmbiguous,
    TestRouteInterviewerConfigError,
    TestRouteInterviewerInputErrors,
    TestRouteInterviewerNoSideEffects,
)

if __name__ == "__main__":
    loader = unittest.TestLoader()
    loader.sortTestMethodsUsing = None
    suite = unittest.TestSuite()
    for cls in [
        TestNewCandidate, TestStatus, TestSearch,
        TestIngestCv, TestAttachCvImportsToCandidateDir,
        # v3.5.8 候选人统一目录
        TestPathCalc, TestAttachmentDirRouting,
        TestEnsureCandidateDirs, TestImportCv,
        # v3.5.9 by_name 软链
        TestSanitize, TestRebuildAlias, TestRemoveAlias, TestRebuildAll,
        TestRound1Result,
        TestExamResult,
        TestRound2Result,
        TestRemove,
        TestTodayInterviews, TestAtomicCLIRegression,
        TestCoreState, TestDbFallback, TestFeishu, TestEmailWatch,
        TestStripQuotedReply, TestFlattenHeader,
        TestHttpRetry, TestSideEffectGuardDB,
        TestRendererEngine, TestTemplateContents, TestCallSitesUseRenderer,
        TestFindTimeoutCandidates, TestScanMain,
        TestSelfVerify, TestCmdSend, TestCmdUpdate, TestCmdDelete,
        TestRunChainBasic,
        TestPromptsModule, TestAnalyzerRouting, TestScrubDraft,
        TestCmdSendUseCachedDraft,
        TestCmdCalendarCreate, TestCmdCalendarDelete,
        TestBgHelpersCalendarDispatch,
        TestInboxGeneralPromptSchema, TestStageAwareRouting,
        TestExamGraderLibrary, TestCmdExamAiReviewIntegration,
        TestCmdNotifyBoss, TestCmdNotifyHr, TestOldPushAlertGone,
        TestCmdNotifyInterviewer,
        TestRound1ScheduleChain, TestRound1RescheduleChain,
        TestDeferUntilReturnChain, TestExamPassToRound2Chain,
        TestPostOfferOneClickSendChain,
        TestExamRejectKeepChain, TestWaitReturnPokeChain,
        TestOnboardingOfferChain,
        TestRound1DispatchChain,
        TestSafeName, TestExtractMetadata, TestExtractAndSave,
        TestExtractAndSaveValidation,
        # v3.5.10 onboarding_offer 默认附件
        TestAutoAttachmentsRegistry, TestCmdSendAutoAttach,
        TestRouteInterviewerCppFirst,
        TestRouteInterviewerEducation,
        TestRouteInterviewerAmbiguous,
        TestRouteInterviewerConfigError,
        TestRouteInterviewerInputErrors,
        TestRouteInterviewerNoSideEffects,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
