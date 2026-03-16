# 龙虾面试管家 — 测试套件

## 目录结构

```
test/
├── README.md                       # 本文件
├── run_tests.sh                    # 一键运行脚本
├── conftest.py                     # pytest 公共路径配置
├── unit/                           # 单元测试（逐函数/逐脚本）
│   ├── test_core_state.py          # 状态机核心：load/save/transition
│   ├── test_round1_result.py       # /round1_result 命令所有分支
│   ├── test_exam_result.py         # /exam_result 命令所有分支
│   ├── test_round2_result.py       # /round2_result 命令所有分支
│   └── test_status.py              # /recruit_status 查询命令
└── integration/
    └── test_full_flow.py           # 完整流程集成测试（全路径）
```

## 快速运行

```bash
cd /home/admin/.openclaw/workspace/test
bash run_tests.sh          # 全部（单元 + 集成）
bash run_tests.sh unit     # 仅单元测试
bash run_tests.sh integration  # 仅集成测试
```

或直接使用 pytest：

```bash
PYTHONPATH=/home/admin/.openclaw/workspace/skills/recruit-ops/scripts \
  python3 -m pytest . -v
```

## 测试覆盖范围（对应流程图）

### 单元测试（50 个用例）

| 文件 | 覆盖点 |
|------|--------|
| `test_core_state.py` | load/save 状态文件、损坏文件容错、get_candidate 默认值、append_audit 字段、ensure_stage_transition 合法/非法转换 |
| `test_round1_result.py` | pass 分支：阶段更新、邮件发送、附件包含、幂等不重发、缺 email 报错；reject_keep/reject_delete：无邮件、审计写入；非法状态跳转报错 |
| `test_exam_result.py` | pass：阶段更新、R2 邮件发送、日历创建、日历失败不阻断；reject_keep/reject_delete：无邮件无日历、审计；非法状态跳转 |
| `test_round2_result.py` | pass → OFFER_HANDOFF、notes 写入审计；reject_keep/reject_delete 阶段转换；从 EXAM_REVIEWED/NEW/拒绝阶段非法执行 |
| `test_status.py` | 单个查询（各字段输出）、不存在返回 1、all 查询、空库输出、全部 STAGE_LABELS 中文映射 |

### 集成测试（13 个用例）

| 测试类 | 覆盖的流程图路径 |
|--------|----------------|
| `TestHappyPathFullFlow` | **主干 A**：R1 通过 → 发笔试邀请 → 笔试审阅（mock） → 笔试通过 → 安排 R2（含邮件+日历） → R2 通过 → OFFER_HANDOFF → 状态查询确认；并验证完整审计链 |
| `TestR1RejectPaths` | **B**：R1 reject_keep（保留）；**C**：R1 reject_delete（删除）；且拒绝后禁止执行 exam_result |
| `TestExamRejectPaths` | **D-1**：笔试 reject_keep；**D-2**：笔试 reject_delete；且拒绝后禁止执行 round2_result |
| `TestR2RejectPaths` | **E-1**：R2 reject_keep；**E-2**：R2 reject_delete |
| `TestIdempotency` | 重复执行 round1_result=pass 不重发邮件 |
| `TestIllegalCrossStageJumps` | OFFER_HANDOFF 阶段禁止 round1/exam_result；NEW 阶段禁止 round2；拒绝阶段禁止 round2 |
| `TestMultipleCandidates` | 3 位候选人各自独立推进，互不影响，all 查询能看到全部 |

## Mock 策略

- **邮件发送**：`subprocess.run` 被 mock，不真实发送 SMTP
- **飞书日历**：`cmd_exam_result._create_round2_event` 被 mock，不调用飞书 API
- **状态文件**：每个测试用例创建独立 `tempfile`，测试后自动删除，不影响生产状态

## 依赖

- Python 3.6+（与生产环境一致）
- `pytest`（`pip3 install pytest --user`）
- 无需配置 IMAP / SMTP / 飞书凭据
