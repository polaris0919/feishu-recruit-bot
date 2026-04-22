"""公开仓 pytest 钩子：缺真实运行时资源（SMTP 配置 / 公司模板 / 笔试压缩包）
的测试自动 skip，让 fresh clone 上来 `pytest` 直接绿。

公司仓（recruit-workspace）有真 config/data，这些测试会照常跑；公开仓
（recruit-workspace-public）默认不带这些资源，会按下方规则 skip。

需要跑被 skip 的测试时，按文件路径放好资源即可，无需改代码。
"""
import os

import pytest

# 公开仓 layout：scripts/tests/conftest.py → 仓根
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))

EMAIL_CONFIG_PATH = os.path.join(REPO_ROOT, "config", "email-send-config.json")
ONOFFER_DIR = os.path.join(REPO_ROOT, "data", "onoffer_data")
EXAM_TAR_PATH = os.path.join(REPO_ROOT, "data", "exam_txt", "笔试题.tar")

# 被这些测试模块 / 节点 ID 包含时，对应资源缺失就 skip
_NEEDS_EMAIL_CONFIG = ("test_agent_chain", "test_run_chain")
_NEEDS_ONOFFER = ("test_auto_attachments",)
_NEEDS_EXAM_TAR = ("test_round1.py::TestRound1Result::test_exam_attachments_prefer_shared_tar",)


def _onoffer_files_present():
    if not os.path.isdir(ONOFFER_DIR):
        return False
    docx = [f for f in os.listdir(ONOFFER_DIR) if f.endswith(".docx")]
    return len(docx) >= 2


def pytest_collection_modifyitems(config, items):
    has_email = os.path.isfile(EMAIL_CONFIG_PATH)
    has_onoffer = _onoffer_files_present()
    has_exam_tar = os.path.isfile(EXAM_TAR_PATH)

    for item in items:
        nodeid = item.nodeid
        if not has_email and any(stem in nodeid for stem in _NEEDS_EMAIL_CONFIG):
            item.add_marker(pytest.mark.skip(
                reason="公开仓未携带 config/email-send-config.json（只带 .example.json）；"
                       "拷贝并填好真实 SMTP 配置后该用例会自动恢复"))
            continue
        if not has_onoffer and any(stem in nodeid for stem in _NEEDS_ONOFFER):
            item.add_marker(pytest.mark.skip(
                reason="公开仓未携带 data/onoffer_data/*.docx 公司入职模板；"
                       "放入两个 docx（与 email_templates/auto_attachments.py 对齐）后自动恢复"))
            continue
        if not has_exam_tar and any(stem in nodeid for stem in _NEEDS_EXAM_TAR):
            item.add_marker(pytest.mark.skip(
                reason="公开仓未携带 data/exam_txt/笔试题.tar；放入压缩包后自动恢复"))
            continue
