#!/usr/bin/env python3
"""架构契约测试：防止已收敛的入口再次漂移。"""
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def _text(path):
    return path.read_text(encoding="utf-8")


def test_candidate_email_sending_only_uses_outbound_cmd_send_boundary():
    """业务 CLI 不得直接调用 send_bg_email 给候选人发信。"""
    allowed = {
        ROOT / "lib" / "bg_helpers.py",
    }
    offenders = []
    for path in ROOT.rglob("*.py"):
        if ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        if path in allowed or "tests" in path.parts:
            continue
        text = _text(path)
        if "send_bg_email(" in text or "import send_bg_email" in text:
            offenders.append(str(path.relative_to(REPO)))

    assert offenders == [], (
        "候选人出站邮件必须走 outbound.cmd_send；禁止业务脚本直接 send_bg_email: "
        + ", ".join(offenders)
    )


def test_known_candidate_email_callers_use_outbound_cmd_send():
    for rel in [
        "interview/cmd_result.py",
        "exam/cmd_exam_result.py",
        "auto_reject/executor.py",
    ]:
        text = _text(ROOT / rel)
        assert "outbound.cmd_send" in text or "send_outbound_template" in text


def test_auto_reject_executor_does_not_redefine_run_cmd():
    """v3.8.x 分层后, auto_reject/executor.py 不应自己再实现 _run_cmd()；
    所有 sync atomic CLI 调度统一走 lib.cli_subprocess.run_module()。

    背景：v3.4 时 executor 有自己的 _run_cmd()，与 bg_helpers 里的同名能力
    重复且 PYTHONPATH/RECRUIT_WORKSPACE_ROOT 注入不一致。本契约防止它被
    "顺手"重新引入。
    """
    text = _text(ROOT / "auto_reject" / "executor.py")
    assert "def _run_cmd(" not in text, (
        "auto_reject/executor.py 不得重新定义 _run_cmd()；请改用 "
        "lib.cli_subprocess.run_module()。"
    )
    assert "from lib.cli_subprocess import run_module" in text, (
        "auto_reject/executor.py 必须通过 lib.cli_subprocess.run_module() "
        "起 atomic CLI 子进程（_delete_talent 等路径）。"
    )
    assert "from lib.bg_helpers import send_outbound_template" in text, (
        "auto_reject/executor.py 必须通过 send_outbound_template() 发拒信，"
        "不要绕过候选人邮件语义边界。"
    )


def test_manual_reject_delete_uses_talent_cmd_delete_boundary():
    """人工 reject_delete 必须走 talent.cmd_delete，避免绕过归档和自验证。"""
    for rel in [
        "interview/cmd_result.py",
        "exam/cmd_exam_result.py",
    ]:
        text = _text(ROOT / rel)
        assert "talent.cmd_delete" in text
        assert ".delete_talent(" not in text, (
            "{} 不得直接调用 talent_db.delete_talent；请走 talent.cmd_delete "
            "以保留归档、自验证和 hard guard。".format(rel)
        )


def test_atomic_cli_subprocess_only_in_whitelisted_files():
    """除白名单外，业务 CLI 不得自己手写 [sys.executable, "-m", ...] subprocess 调度。

    任何 sync atomic CLI 调度必须走 lib.cli_subprocess.run_module()；任何
    async fire-and-forget 调度暂时走 lib.bg_helpers 的专用 helper（P2 会抽
    lib.cli_subprocess.popen_module 通用层）。

    白名单：
      - lib/cli_subprocess.py    通用 sync 调度层本身
      - lib/bg_helpers.py        后台 Popen helper（spawn_calendar / send_bg_email 等）
      - cron/cron_runner.py      cron 调度器，自带 timeout/告警/heartbeat 语义，
                                 暂不下沉到通用层（plan §"暂不迁移 cron_runner"）
      - tests/                   测试本身可以直接验证子进程行为
    """
    allowed = {
        ROOT / "lib" / "cli_subprocess.py",
        ROOT / "lib" / "bg_helpers.py",
        ROOT / "cron" / "cron_runner.py",
    }
    offenders = []
    for path in ROOT.rglob("*.py"):
        if ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        if path in allowed or "tests" in path.parts:
            continue
        text = _text(path)
        # 同时触发两个条件才算违规：
        #   1) 文件里出现 [sys.executable, "-m" 的 atomic CLI 调度模式；
        #   2) 文件里出现 subprocess.run / subprocess.Popen 调用。
        # 单独出现任一关键字（例如 docstring 里写 "python -m"）都不会误中。
        if 'sys.executable, "-m"' not in text:
            continue
        if "subprocess.run" in text or "subprocess.Popen" in text:
            offenders.append(str(path.relative_to(REPO)))

    assert offenders == [], (
        "atomic CLI 子进程调度必须走 lib.cli_subprocess.run_module() / "
        "lib.bg_helpers 后台 helper；禁止业务脚本自己手写 "
        "[sys.executable, '-m', ...] subprocess 调用: "
        + ", ".join(offenders)
    )


def _stages_from_db_schema():
    """从 migrations/schema.sql 的 chk_current_stage CHECK 约束里抽出 stage 集合。

    DB 是 stage 的最终事实源——任何 Python 代码偏离它都会被 PG 直接 reject
    INSERT/UPDATE。本函数解析 schema.sql 末态约束块, 拿到字符串集合。
    """
    schema_path = ROOT / "lib" / "migrations" / "schema.sql"
    text = _text(schema_path)
    # 抓 'chk_current_stage CHECK (current_stage IN ( ... ));' 块
    # schema.sql 有多个候选定义(为了升级时回退安全), 我们只关心最后一个,
    # 它就是当前生效的。
    blocks = re.findall(
        r"chk_current_stage\s+CHECK\s*\(\s*current_stage\s+IN\s*\(([^)]*)\)\s*\)\s*;",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert blocks, (
        "schema.sql 找不到 chk_current_stage CHECK(current_stage IN (...)); "
        "B2 SSOT 测试无法验证 DB↔Python 一致性。请检查 schema.sql 是否被改格式。"
    )
    last = blocks[-1]
    return {tok.strip().strip("'\"") for tok in last.split(",") if tok.strip()}


def test_python_stages_match_db_check_constraint():
    """B2 (v3.8.7): Python core_state.STAGES 必须与 schema.sql 的
    chk_current_stage CHECK 约束等价。

    任一方向不一致都会引发线上事故:
      - DB 缺 Python 多出来的 stage      → 业务推进时 PG reject INSERT
      - Python 缺 DB 多出来的 stage      → DB 有数据但 STAGES 校验拒读
      - 名字写错 / 大小写不一致           → 同上

    本测试把 DB schema 当作事实源, 用 schema.sql 的 CHECK 约束做对照。
    helpers._InMemoryTdb 在 sys.modules 里替换掉 talent_db, 所以这里
    必须独立解析 SQL 文本, 而不能依赖运行时 DB 状态。
    """
    from lib import core_state
    db_stages = _stages_from_db_schema()
    py_stages = set(core_state.STAGES)

    assert db_stages == py_stages, (
        "Python STAGES 与 schema.sql chk_current_stage 不一致:\n"
        "  仅 DB 有: {}\n"
        "  仅 Python 有: {}\n"
        "修复手段: 改 schema.sql 加 migration 或改 core_state.STAGES, "
        "同时同步 STAGE_LABELS 与 docs/PROJECT_OVERVIEW §3.1。".format(
            sorted(db_stages - py_stages),
            sorted(py_stages - db_stages),
        )
    )


def test_python_stage_labels_cover_all_stages():
    """STAGES 与 STAGE_LABELS 必须 1:1。

    实战教训: v3.8 加 ONBOARDED 时漏掉 STAGE_LABELS, 老板视图显示空字符
    串, 看不到一个候选人已入职(事故复盘见 INCIDENT_RULES §5)。本契约
    把这条规则锁住。
    """
    from lib import core_state
    missing_labels = set(core_state.STAGES) - set(core_state.STAGE_LABELS.keys())
    extra_labels = set(core_state.STAGE_LABELS.keys()) - set(core_state.STAGES)
    assert not missing_labels, (
        "下列 stage 缺少 STAGE_LABELS 映射: {}".format(sorted(missing_labels))
    )
    assert not extra_labels, (
        "STAGE_LABELS 出现 STAGES 不存在的 key: {}".format(sorted(extra_labels))
    )


def test_active_docs_do_not_describe_auto_reject_as_keep_pool_flow():
    allowed_parts = {
        ("docs", "INCIDENT_RULES.md"),
        ("docs", "archive"),
    }
    patterns = [
        "auto_reject.cmd_scan_exam_timeout` 会自动发拒信 + 推 stage 到 `EXAM_REJECT_KEEP`",
        "cmd_scan_exam_timeout（笔试 ≥3 天未交 → 即触发拒信 + 推 EXAM_REJECT_KEEP 留池）",
        "笔试 ≥3 天未交 → 即触发拒信 + 推 stage 到 `EXAM_REJECT_KEEP`",
    ]
    offenders = []
    for path in [REPO / "SKILL.md", *list((REPO / "docs").rglob("*.md"))]:
        rel_parts = path.relative_to(REPO).parts
        if any(rel_parts[:len(parts)] == parts for parts in allowed_parts):
            continue
        text = _text(path)
        if any(pattern in text for pattern in patterns):
            offenders.append(str(path.relative_to(REPO)))

    assert offenders == [], (
        "活跃文档不应把当前 auto_reject 描述为留池流程: "
        + ", ".join(offenders)
    )
