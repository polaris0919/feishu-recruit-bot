# `test_all.py` 测试说明

本文档对应路径：`scripts/test_all.py`。运行方式：

```bash
cd /home/admin/recruit-workspace/skills/recruit-ops/scripts
python3 test_all.py
```

---

## 测试环境与机制（读本文档前必看）

| 项目 | 说明 |
|------|------|
| **执行方式** | 全量 **进程内** 调用各模块的 `main(argv)`，不 fork 子进程，避免 OOM。 |
| **数据库** | 默认用内存里的 **`_InMemoryTdb`** 注入 `sys.modules["talent_db"]`，**不连真实 PostgreSQL**。 |
| **副作用** | 环境变量 `RECRUIT_DISABLE_SIDE_EFFECTS=1`，并可能 pop 掉 `TALENT_DB_PASSWORD`，减少真实外呼。 |
| **真实 DB 模块** | 仍 `import talent_db as _real_talent_db`，供 `TestDbFallback` 等少数用例直接测真实模块行为。 |
| **辅助函数** | `_call_main(module_name, argv)` 捕获 stdout/stderr 与返回码；`_new_candidate(...)` 调 `cmd_new_candidate` 造人；`_wipe_state()` 每用例前清空内存库。 |

因此：**这些测试验证的是「脚本逻辑 + core_state + 与 mock 的交互」**，不是「你机器上 psql + 真飞书」的端到端验收。

---

## 一、基础设施（非 TestCase）

### `_InMemoryTdb`

模拟 `talent_db` 的最小接口：`load_state_from_db`、`sync_state_to_db`、`get_one`、`upsert_one`、`delete_talent`，并补齐扫描链路所需的 `get_pending_confirmations`、`get_confirmed_candidates`、`update_last_email_id` 等能力。

### `_call_main(module_name, argv)`

统一入口，返回 `(stdout, stderr, returncode)`。

### `_new_candidate(...)`

调用 `cmd_new_candidate` 创建候选人并从输出解析 `talent_id`。

### `tests/scenario_helpers.py`

复杂邮件场景的辅助层，核心能力：

- `FakeMailbox`：支持 `deliver_now(...)` 与 `deliver_on_scan(n, ...)`，可模拟“第几次扫描时才出现新邮件”。
- `make_reply_email(...)`：快速构造带 `From` / `Message-ID` / `Date` 的回复邮件。
- `ScenarioRunner`：封装“造候选人、设置 invite_sent_at、跑扫描、断言 boss pending / last_email_id”等常用步骤，避免每个测试手搓 `FakeIMAP + patch + call_main`。
- `subprocess_result_from_call_main(...)`：把 `daily_exam_review` 里的 `subprocess.run(...)` 重定向到进程内 `call_main(...)`，便于做真实状态落库的端到端场景测试。

---

## 二、按测试类说明

### `TestNewCandidate` — `cmd_new_candidate`

| 测试方法 | 验证内容 |
|----------|----------|
| `test_creates_candidate` | 能成功创建候选人，输出含 talent_id。 |
| `test_talent_id_is_unique` | 同一邮箱第二次创建会失败或拒绝重复（依实现）。 |
| `test_talent_id_format` | 生成的 `talent_id` 符合 `t_xxxxxx` 格式。 |
| `test_optional_fields` | 可选字段（岗位等）可写入。 |
| `test_missing_name_fails` | 缺少姓名时命令失败。 |
| `test_missing_email_fails` | 缺少邮箱时命令失败。 |

---

### `TestStatus` — `cmd_status`

| 测试方法 | 验证内容 |
|----------|----------|
| `test_status_shows_name` | `--talent-id` 输出包含姓名与 talent_id。 |
| `test_status_shows_stage` | 新建人默认阶段 `NEW` 出现在输出中。 |
| `test_status_not_found` | 不存在的 talent_id 返回非 0。 |
| `test_status_all_lists_all` | `--all` 列出多人且文案含人数统计。 |
| `test_status_shows_round2_after_exam_pass` | 走完一面 pass → 笔试 pass 后，状态里出现二面时间与 `ROUND2_SCHEDULING`。 |
| `test_status_shows_round1_time_and_reschedule_pending` | 安排一面后人工写入审计 `round1_reschedule_requested`，`cmd_status` 能显示一面时间与改期申请相关文案。 |
| `test_status_shows_wait_return_round` | 进入 `WAIT_RETURN` 后，`cmd_status` 会显示统一暂缓状态与对应轮次。 |

---

### `TestSearch` — `cmd_search`

| 测试方法 | 验证内容 |
|----------|----------|
| `test_search_by_email` | 按邮箱关键词能搜到结果（JSON `found > 0`）。 |
| `test_search_by_name` | 按姓名能搜到。 |
| `test_search_by_talent_id` | 按 talent_id 精确命中第一条。 |
| `test_search_no_result` | 无匹配时返回非 0。 |
| `test_search_all_active` | `--all-active` 至少包含进行中候选人。 |
| `test_search_includes_round1_time_and_confirmed_flags_via_real_flow` | 通过真实排期与确认流程后，JSON 中含单字段 `round1_time` 与 `round1_confirm_status`。 |

---

### `TestRound1Result` — `cmd_round1_result`（转发 `interview/cmd_result` round=1）

| 测试方法 | 验证内容 |
|----------|----------|
| `test_exam_attachments_prefer_shared_tar` | **单元**：`cmd_result._get_exam_attachments` 在存在共享 `笔试题.tar` 时优先返回该路径（mock `os.path.isfile`）。 |
| `test_round1_pass_creates_exam` | 一面通过会生成 `exam-` 相关输出/状态。 |
| `test_round1_reject_keep` | `reject_keep` 成功，状态含 `ROUND1_DONE_REJECT_KEEP`。 |
| `test_round1_reject_delete` | `reject_delete` 成功，输出含彻底删除类提示。 |
| `test_round1_pass_without_email_fails` | 一面 pass 未给 `--email` 应失败。 |
| `test_round1_wrong_stage_fails` | 已在 reject 阶段后再 pass，阶段不对应失败。 |
| `test_round1_defer_enters_wait_return_and_sends_email` | `cmd_round1_defer` 进入 `WAIT_RETURN`，并记录 `wait_return_round=1`。 |

---

### `TestExamResult` — `cmd_exam_result`

前置 `_setup_exam`：新建人 → 一面 pass（带邮箱）进入笔试链路。

| 测试方法 | 验证内容 |
|----------|----------|
| `test_exam_pass_transitions` | `--result pass --round2-time` 后阶段到 `ROUND2_SCHEDULING`。 |
| `test_exam_pass_requires_round2_time_and_does_not_reuse_old_time` | 未提供 `--round2-time` 时失败；且**不会**用库里已有 `round2_time` 自动顶替（阶段应保持 `EXAM_SENT`，原单字段时间保留）。 |
| `test_exam_pass_defers_boss_calendar_until_confirmed` | mock `send_round2_notification`：笔试通过后发二面邀请邮件，输出提示候选人确认后再建日历等（线下面试文案）。 |
| `test_exam_reject_keep` | 笔试 reject_keep 后状态符合预期（含 `ROUND1_DONE_REJECT_KEEP` 等展示）。 |
| `test_exam_reject_delete` | 笔试 reject_delete 命令成功返回 0。 |
| `test_exam_wrong_stage_fails` | 仍在 NEW 未进笔试流程时调用 `cmd_exam_result pass` 应失败。 |

---

### `TestRound2Result` — `cmd_round2_result`

前置 `_setup_r2`：新建 → 一面 pass → 笔试 pass 带二面时间，进入 `ROUND2_SCHEDULING`。

| 测试方法 | 验证内容 |
|----------|----------|
| `test_round2_pending` | `--result pending` → `ROUND2_DONE_PENDING`。 |
| `test_round2_pass` | `--result pass` → `OFFER_HANDOFF`。 |
| `test_round2_reject_keep` | reject_keep 成功。 |
| `test_round2_reject_delete` | reject_delete 成功。 |
| `test_round2_wrong_stage_fails` | NEW 阶段直接跑二面结果应失败。 |

---

### `TestRound2SchedulingFlow` — 二面排期 / 确认 / 改期 / 暂缓

前置 `_setup_r2` 同上。

| 测试方法 | 验证内容 |
|----------|----------|
| `test_round2_reschedule_default_confirms_and_creates_calendar` | `cmd_round2_reschedule` 默认**已确认**路径：mock 邮件与 `spawn_calendar` 各调用一次，输出含「已直接确认」。 |
| `test_round2_reschedule_no_confirm_defers_calendar` | 带 `--no-confirm`：发改期邮件但不建日历，输出含「等待候选人确认」。 |
| `test_round2_confirm_creates_boss_calendar` | mock `talent_db` + `_spawn_calendar_bg`：`cmd_round2_confirm` 成功，`mark_confirmed` 被调用，输出含二面已确认、日历创建中。 |
| `test_round2_confirm_calendar_uses_offline_defaults` | 确认创建日历时传入的参数含线下面试、时间、邮箱、姓名（`_spawn_calendar_bg` 调用参数断言）。 |
| `test_round2_switch_mode_is_deprecated_stub` | `cmd_round2_switch_mode` 返回 0 且输出含「已废弃」。 |
| `test_round2_defer_enters_wait_return_and_sends_email` | `cmd_round2_defer`：mock defer 邮件，输出含暂缓与 `WAIT_RETURN`，并记录 `wait_return_round=2`。 |

---

### `TestIngestCv` — `cmd_ingest_cv`

| 测试方法 | 验证内容 |
|----------|----------|
| `test_ingest_cv_supports_docx_new_candidate_preview` | 构造最小合法 DOCX，mock 库中无重复人、mock LLM 解析字段；`cmd_ingest_cv` 输出含「新候选人 - 待确认」及姓名邮箱，stderr 含已读 DOCX。 |
| `test_ingest_cv_supports_real_pdf_preview` | 使用真实 PDF 文件路径作为 fixture，mock 该简历的抽取文本与 LLM 结果，验证 `cmd_ingest_cv` 能走完整预览链路并输出姓名、邮箱、职位、院校等关键字段。 |

---

### `TestRemove` — `cmd_remove`

| 测试方法 | 验证内容 |
|----------|----------|
| `test_remove_without_confirm_fails` | 无 `--confirm` 时非 0，且 stdout/stderr 合并字符串含 `confirm`。 |
| `test_remove_with_confirm` | 带 `--confirm` 返回 0，JSON `ok` 为 true。 |
| `test_remove_nonexistent_fails` | 不存在的 id 加 `--confirm` 仍失败（非 0）。 |

---

### `TestCoreState` — `core_state` 模块

| 测试方法 | 验证内容 |
|----------|----------|
| `test_stages_set_is_complete` | `STAGES` 集合包含文档中主要阶段（含笔试、二面、Offer 等）。 |
| `test_ensure_stage_transition_ok` | 允许从 `NEW` 迁到 `EXAM_SENT` 时返回 True 且阶段更新。 |
| `test_ensure_stage_transition_wrong_stage` | 当前阶段不在允许集合时返回 False 且不改阶段。 |
| `test_no_round1_score_field` | 确认 `core_state` 未再暴露 `round1_score` 一类旧字段名（字符串检查）。 |

---

### `TestDbFallback` — 真实 `talent_db` / `core_state` 与 DB 开关

| 测试方法 | 验证内容 |
|----------|----------|
| `test_save_state_raises_when_db_fails` | mock `sync_state_to_db` 抛错时，`save_state` **抛出**异常，不吞掉。 |
| `test_load_state_returns_db_result_directly` | mock DB 启用时 `load_state` 直接来自 `load_state_from_db`，不调 JSON 兜底。 |
| `test_import_candidate_syncs_to_db` | `cmd_import_candidate` 输出含「已同步」。 |
| `test_import_candidate_supports_wait_return_stage` | 补录 `WAIT_RETURN` 候选人时会同步 `wait_return_round`。 |
| `test_talent_db_is_enabled_respects_no_config` | 设置 `RECRUIT_DISABLE_DB=1` 时 `_is_enabled()` 为 False。 |
| `test_talent_db_load_state_disabled_returns_empty` | DB 禁用时 `load_state_from_db` 返回空 `candidates`。 |
| `test_talent_db_sync_state_disabled_returns_false` | DB 禁用时 `sync_state_to_db` 返回 False。 |

---

### `TestFeishu` — `lib/feishu`

| 测试方法 | 验证内容 |
|----------|----------|
| `test_import_feishu` | 模块可导入且存在 `send_text`。 |
| `test_send_text_no_client_returns_false` | mock 无 client 时 `send_text` 返回 False（未误报成功）。 |

---

### `TestDailyExamReview` — `exam/daily_exam_review`

| 测试方法 | 验证内容 |
|----------|----------|
| `test_scan_no_imap_config` | 无 IMAP host 时 `scan_new_replies` 返回列表（不崩）。 |
| `test_format_report_uses_prereview` | `format_report` 优先用 `prereview.report_text`。 |
| `test_format_report_fallback` | 无预审时降级为「新笔试回复」简单模板。 |
| `test_request_online_report_guides_boss_to_switch_mode` | 二面确认报告里 intent 为 `request_online` 时，文案含改期类命令提示。 |
| `test_defer_report_guides_boss_to_wait_return` | intent `defer_until_shanghai` 时文案含 `cmd_round2_defer.py` 与 `WAIT_RETURN`。 |
| `test_main_auto_defers_round2_candidate` | mock 扫描结果 + subprocess：`main --auto --interview-confirm-only` 会对 defer 意图子进程调用 `cmd_round2_defer.py`。 |
| `test_scan_interview_confirmations_local_from_matching` | mock IMAP 两封邮件、仅目标邮箱匹配：`_scan_interview_confirmations` 只返回目标候选人一条，且 intent 为 confirm（验证 From 精确匹配不误判）。 |
| `test_scan_interview_confirmations_delayed_reply_on_second_scan` | 用 `FakeMailbox.deliver_on_scan(2, ...)` 验证第一轮无信、第二轮才收到确认回信，并更新 `round2_last_email_id`。 |
| `test_scan_reschedule_requests_delayed_reply_on_second_scan` | 已确认二面候选人第二轮扫描才收到改期回信，命中真实改期扫描链路。 |
| `test_scan_reschedule_requests_matches_multiple_candidates` | 多位已确认候选人同时回改期邮件时，能按 `From` 精确归因到正确候选人。 |
| `test_scan_interview_confirmations_dedup_by_message_id` | 同一 `Message-ID` 连续扫描两次，第二次不重复处理。 |
| `test_scan_interview_confirmations_skips_old_email_before_invite` | 邮件 `Date` 早于邀请发送时间时应被忽略。 |
| `test_scan_interview_confirmations_timeout_without_new_email` | 超过 48h 无回复时，不依赖新邮件也会返回 `timeout` 分支。 |
| `test_scan_interview_confirmations_skips_auto_reply_and_uses_real_mail` | 同一扫描里既有自动回复又有真实确认时，应跳过 auto-reply 并继续处理真实邮件。 |
| `test_scan_interview_confirmations_latest_valid_email_wins` | 同一候选人在同一轮扫描里连续发来两封有效邮件时，只处理最新一封，并以其 intent/new_time 覆盖前一封。 |

---

### `TestExamPrereview` — `exam_prereview`

| 测试方法 | 验证内容 |
|----------|----------|
| `test_analyze_response_time_normal` | 交卷时间相对发卷「正常」区间。 |
| `test_analyze_response_time_too_fast` | 「极快」标签。 |
| `test_analyze_response_time_overtime` | 「超时」标签。 |
| `test_analyze_response_time_missing` | 缺少时间戳时 `available` 为 False。 |
| `test_code_quality_no_code` | 空代码无分。 |
| `test_code_quality_good_code` | 较长正常代码有分数且检测到 pandas 等。 |
| `test_code_quality_detects_eval` | 代码含 `eval` 产生警告。 |
| `test_code_quality_detects_except_pass` | bare `except`/`pass` 类问题产生警告。 |
| `test_completeness_code_and_result` | 附件含代码与结果文件、正文有说明时完整性分析正确。 |
| `test_completeness_no_files` | 无附件无正文时完整性为 0。 |
| `test_run_prereview_full` | 端到段 `run_prereview` 返回含 score、report_text、db_summary 等。 |

---

### `TestRescheduleRequest` — `cmd_reschedule_request` + 改期报告

| 测试方法 | 验证内容 |
|----------|----------|
| `test_reschedule_request_revokes_r2_and_sends_email` | 构造二面已确认候选人，调用改期请求：mock ack 邮件与删日历，输出成功，status 含改期相关审计文案。 |
| `test_reschedule_request_revokes_r1_and_sends_email` | 一面已确认场景，改期请求成功并发邮件。 |
| `test_reschedule_request_wrong_stage_fails` | 仅 NEW 阶段调用改期请求应失败。 |
| `test_reschedule_report_with_new_time` | `format_reschedule_request_report` 含新时间与 `cmd_round2_reschedule` 提示。 |
| `test_reschedule_report_without_new_time` | 无新时间时报告含 `cmd_round1_reschedule` 与时间占位格式说明。 |
| `test_main_auto_handles_reschedule_scan` | `main --auto --reschedule-scan-only` mock 扫描到改期项时，子进程调用 `cmd_reschedule_request.py` 且参数含 talent_id 与 round。 |
| `test_main_interview_scan_sets_boss_pending_after_delayed_confirm` | 使用真实确认扫描路径，第二次扫描收到回信后写入老板待确认时间。 |
| `test_main_reschedule_scan_uses_real_scan_and_calls_reschedule_request` | 使用真实改期扫描路径，第二次扫描收到改期信后触发 `cmd_reschedule_request.py`。 |
| `test_main_reschedule_scan_rolls_back_only_target_candidate_and_keeps_time` | 已确认后二面候选人要求改期时，仅目标候选人回退到 `ROUND2_SCHEDULING`，且原时间保留。 |
| `test_main_reschedule_scan_matches_multiple_candidates_and_updates_each_correctly` | 多位已确认二面候选人同时改期时，各自回退并保留自己的原时间与 proposed time。 |
| `test_main_reschedule_scan_defer_moves_correct_candidate_to_wait_return` | 已确认后二面候选人来信表示不在国内时，仅目标候选人进入 `WAIT_RETURN`。 |
| `test_main_reschedule_scan_defer_matches_multiple_candidates_across_rounds` | 一面/二面候选人同时来信表示不在国内时，系统可跨轮次正确归因并分别进入 `WAIT_RETURN`。 |
| `test_multi_round_negotiation_with_interleaved_candidates` | 模拟多个候选人交叉协商面试时间，覆盖确认、改期、再次改期的连续状态流转。 |
| `test_wait_return_resume_round1` | 一面进入 `WAIT_RETURN` 后，可统一恢复到 `ROUND1_SCHEDULING`。 |
| `test_wait_return_resume_round2` | 二面进入 `WAIT_RETURN` 后，可统一恢复到 `ROUND2_SCHEDULING`。 |
| `test_wait_return_resume_then_candidate_can_continue_confirmation_flow` | `WAIT_RETURN` 恢复后重新约时间，后续确认扫描仍可继续推进。 |
| `test_main_reschedule_scan_latest_valid_email_wins_for_confirmed_candidate` | 已确认候选人连续发来两封有效改期类邮件时，主流程只吃最新一封，并按最新意图执行到正确状态（如进入 `WAIT_RETURN`）。 |
| `test_main_reschedule_scan_request_online_does_not_rollback_state` | 已确认候选人请求转线上时，只记录最新邮件，不应误回退到排期中或清空既定面试时间。 |

---

## 三、复杂场景测试写法

新增涉及“邮件延迟到达、重复扫描、旧邮件过滤、改期/暂缓自动处理”的测试时，优先使用：

1. `ScenarioRunner()` 建场景
2. `deliver_on_scan(n, ...)` 控制邮件在第几轮扫描出现
3. `patch_daily_exam_review(...)` 接入 `FakeMailbox` 和假飞书
4. 断言 5 类结果：
   - `stage`
   - `audit`
   - `roundN_last_email_id`
   - `boss_confirm_pending`
   - `subprocess.run` / 邮件 / 日历副作用参数

推荐结构：

```python
scenario = ScenarioRunner()
tid = scenario.create_round2_pending_candidate(...)
scenario.mailbox.deliver_on_scan(2, make_reply_email(...))
with scenario.patch_daily_exam_review(daily_exam_review, llm_side_effect=[...]):
    first = daily_exam_review._scan_interview_confirmations(round_num=2, auto_mode=True)
    second = daily_exam_review._scan_interview_confirmations(round_num=2, auto_mode=True)
assert first == []
assert second[0]["talent_id"] == tid
scenario.assert_last_email_id_updated(tid, "round2", "<msg@test>")
```

这样能把“多步异步行为”稳定压成确定性的单进程测试。

---

## 四、未在 `test_all.py` 中单独覆盖的 CLI（客观说明）

以下脚本可能**没有**本文件中的独立 `Test*` 类，或仅被其它用例间接带到：

- `cmd_round1_schedule`、`cmd_round1_confirm`（部分逻辑含在流程里但未逐条测）
- `cmd_finalize_interview_time`
- `cmd_interview_reminder`
- `cron_runner`
- `cmd_new_candidate` 除 `TestNewCandidate` 外的边界
- 真实 PostgreSQL、真实 IMAP、真实飞书 API

若要做「生产级验收」，需在**真实环境**用可控 `talent_id` 补手工或集成测试；本文件的定位是 **快速回归 + 逻辑锁死**。

---

## 五、测试数量

全文件共 **107** 个测试方法（以 `unittest` 加载结果为准，若后续增删以 `python3 test_all.py` 输出为准）。
