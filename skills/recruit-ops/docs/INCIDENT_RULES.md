<!--
audience: agent, maintainer
read_when: chain dispatch 命中事故标签 / 反模式扩展查询 / 维护者要审某条规则的来源
do_not_put_here: 通用反模式（留在 SKILL.md §8）/ stage 定义（→ AGENT_RULES.md §2）/ 部署（→ OPERATIONS.md）
sibling_docs: SKILL.md, AGENT_RULES.md, CLI_REFERENCE.md, OPERATIONS.md, PROJECT_OVERVIEW.md, INDEX.md
last_updated: 2026-05-16
-->

> **v3.8.7 (2026-05-16) 注**: 本文档历史引用的形如
> `scripts/lib/migrations/_applied/<日期>_v<ver>_*.sql` 或裸文件名
> (`20260422_v3511_talent_emails_context_rejection.sql` 等) 的 11 个增量 migration
> 文件已统一删档。schema.sql 完整内联了它们的最终态。文件名保留作为 git 历史 anchor:
>
> ```bash
> git log --diff-filter=D -- scripts/lib/migrations/
> ```

# Recruit-Ops 事故规则录

> 这份文件按**事故时间倒序**列出所有"由真实事故沉淀出来的硬规则"。每条都有**日期 / 版本号、触发现象、根因、现行规则**四要素，方便维护者审来源、agent 在反模式扩展查询时快速定位。
>
> SKILL.md §8 只保留 12 条最高危**通用**反模式；事故型反模式全部归这里。AGENT_RULES.md 各 chain 的"硬规则"块仍保留**简短引用**（一行 + 指向本文件 anchor），不重复复述。

---

## 16. 2026-05-11 — 两条 inbound 路径分权（候选人 confirm + 终态扫描）

**版本**：v3.8.4 修订（产品复盘场景 8 + 场景 12 后的两点分权决策）
**触发背景**：上一轮 14 场景对照检查发现两处实现与产品语义不齐：

1. **场景 8（"老板最后确认面试时间已安排好"）**：v3.8.1~v3.8.3 期间候选人发 `confirm_interview` 邮件被 §1.1 白名单判作"低风险写动作",`inbox.cmd_analyze` 默认 `need_boss_action=false` → agent 直接跑 §4.2 chain 建日历 + 升级 stage。产品意图是"建日历这一最终确认动作必须老板拍板"——候选人发的时间可能跟老板/HR 已知的其他安排冲突,这一步不应由候选人邮件单方面驱动 agent 写动作。
2. **场景 12（"转入 offer 已发放阶段，后续不再扫邮件"）**：v3.8.3 之前 `inbox.cmd_scan._SKIP_STAGES = frozenset()` 全量覆盖,`ONBOARDED` + `OFFER_DECLINED_KEEP` 两个终态的候选人邮件仍被 IMAP 拉、LLM 分析,§5 表 5.19 / 5.20 行让 agent 退化为只推 info 卡——但**邮件仍流过 LLM 一遍 + 推 info 卡**,还是会打扰老板。产品意图是"已入职 / 已拒 offer 后,这两类候选人邮件**根本不该进 agent 视野**——他们走 HR / 同事通道,跟招聘流程脱钩"。

**两条无前车之鉴的"诱发性事故"**（虽然没真触发,但产品复盘时被识别为反模式）：

- 场景 8 的潜在事故：候选人 confirm 一个老板临时改了主意但还没告诉 agent 的时间 → agent 自动建日历 → 老板看到飞书"已建日历"才发现冲突 → 已发出的日历邀请邮件**不可撤销**,只能改期重发,候选人体验差。
- 场景 12 的潜在事故：已入职候选人发"我下周一开始休假报销谁报"这种业务无关邮件 → LLM 推 info 卡 → 老板分不清这是入职后的日常沟通还是新流程信号,容易误读为"候选人有问题需要 agent 介入"。

**修订（v3.8.4，2026-05-11）**：

### 场景 8 修订

- **`scripts/inbox/cmd_analyze.py`**：新增 `_STAGE_AWARE_NEED_BOSS = {("confirm_interview", "ROUND1_SCHEDULING"), ("confirm_interview", "ROUND2_SCHEDULING")}` 后置 override 表;在 `_analyze_one` 内 `analyzer.analyze()` 返回后,写 DB 前命中即强制 `result["need_boss_action"] = True`。理由：analyzer.analyze() 是无状态 LLM 包装,不知道 stage——把 stage-aware 逻辑放在 cmd_analyze 这一层。
- **`AGENT_RULES.md §1.1 候选人 inbound 白名单`**：`confirm_interview` 从"低风险写动作 → §4.2 chain"挪到"不直接触发建日历——仅推 warn 卡问老板",理由列明"建日历是面试时间的最终确认动作"。
- **`AGENT_RULES.md §4.2 触发条件`**：从"`intent=confirm_interview`" 改为"两条件全部满足——(1) 候选人发了 confirm_interview 邮件 **AND** (2) 老板在飞书显式说出 'OK 建日历' / 'X 时间确认了' / '给 X 安排日历' 等指令"。硬规则块新增第一条"必须由老板飞书消息触发"。
- **`AGENT_RULES.md §5 表 5.9b`**（新增）：`ROUND{N}_SCHEDULING` + `confirm_interview` → 不直接触发,推 warn 卡等老板拍板。
- **`tests/test_v384_terminal_split.py::TestConfirmInterviewStageOverride`**（新增 5 个 case）：验 ROUND1/ROUND2 SCHEDULING override 生效 + SCHEDULED（5.10 重复确认）不被误伤 + 非 confirm intent 不受影响 + 表内容显式断言。

### 场景 12 修订

- **`scripts/inbox/cmd_scan.py`**：`_SKIP_STAGES` 从 `frozenset()` 改为 `frozenset({"ONBOARDED", "OFFER_DECLINED_KEEP"})`;`_process_candidate` 入口检查命中即 return `{"skipped_stage": stage}`,不调 IMAP `_fetch_messages_for_email`。聚合层 `_do_scan` 加 `skipped_stage_total` 计数透传到最终 result JSON 给 cron 日志可见。
- **AGENT_RULES.md §2 stage 表**：`ONBOARDED` 行 + `OFFER_DECLINED_KEEP` 行各加注"v3.8.4 起 `inbox.cmd_scan` 不再扫此 stage"。
- **AGENT_RULES.md §3 atomic CLI 表**：`inbox.cmd_scan` 描述补"v3.8.4 起跳过两个终态"。
- **AGENT_RULES.md §5 表 5.19 / 5.20 / 5.20b**：
  - 5.19 `ONBOARDED` 行注明"v3.8.4 起 cmd_scan 不扫——本行只对历史已入库邮件适用"
  - 5.20 拆分：现在只覆盖 `EXAM_REJECT_KEEP` / `ROUND2_DONE_REJECT_KEEP`（仍扫，因为候选人没拒我们）
  - 新增 5.20b `OFFER_DECLINED_KEEP` 行：cmd_scan 不扫,激活必须先 §4.9 force-jump
- **`skills/recruit-ops/README.md`** cron 表 `inbox.cmd_scan` / `inbox.cmd_analyze` 行同步说明。
- **`tests/test_v384_terminal_split.py::TestInboxScanTerminalSkip`**（新增 6 个 case）：验 `ONBOARDED` + `OFFER_DECLINED_KEEP` 跳过 + 其他终态（`EXAM_REJECT_KEEP` / `ROUND2_DONE_REJECT_KEEP` / `WAIT_RETURN`）仍扫 + `_SKIP_STAGES` 内容显式断言。

**为什么这两条分权是安全的**：

- **场景 8 分权语义保守**：白名单收紧是**只多一道老板确认环节**,不删除任何能力。极端情况下"老板长时间不回飞书"会让 SCHEDULING stage 滞留更久,但 cron `cmd_interview_reminder` 已有催问机制（仅对临近开始的 `ROUND{N}_SCHEDULED` 发,不 cover 本场景的 SCHEDULING——若产品发现 SCHEDULING 滞留是问题,可以扩 reminder 加新 cron）。
- **场景 12 跳过有兼容性边界**：
  - **不跳过的三个叶子态**（`EXAM_REJECT_KEEP` / `ROUND2_DONE_REJECT_KEEP` / `WAIT_RETURN`）都属于"候选人没拒我们/还在等他信号"语义,他们的邮件仍可能携带回归/追问信号,必须扫。
  - **跳过的两个终态语义对称**：`ONBOARDED` = 招聘流程胜利收尾后转入工作沟通；`OFFER_DECLINED_KEEP` = 候选人主动拒绝。两者都是"候选人不需要 agent 招聘流程介入"的状态。
  - **数据可恢复性**：被跳过期间的候选人邮件**不入库**（不是入了库但被忽略）,这是有意为之——避免 talent_emails 被业务无关邮件污染。如果将来某个候选人被 §4.9 force-jump 重新激活,本扫描的 IMAP SINCE 默认只回拉最近 `max_fetch=50` 封,届时旧邮件可能漏掉——但 §4.9 force-jump 的典型上下文是老板手上有候选人原始邮件需要 agent 跟进,**不依赖** `inbox.cmd_scan` 主动拉到这封邮件（这是产品复盘时的明示约定）。

**未来回归 / 验证**：

- 已通过 `cd skills/recruit-ops && PYTHONPATH=scripts uv run python3 -m unittest tests.test_v384_terminal_split -v`（11 tests OK,涵盖 5 + 6 个 case）。
- 全量 `tests/run_all.py` 待跑（紧随本条修订）。
- **场景 8 维护**：将来若要扩 SCHEDULING confirm 的自动化（例如老板提前飞书写好一条"X 来确认我就 OK"模板让 agent 直接走 §4.2），新加入口必须**显式**标在 §4.2 触发条件第二项里,不要"悄悄"恢复 §1.1 白名单的自动触发——避免回到 v3.8.3 的潜在事故面。
- **场景 12 维护**：`_SKIP_STAGES` 是产品决策,不要把 `WAIT_RETURN` 误塞进去——WAIT_RETURN 必须扫才能收到 §4.7 的回归信号；同理也不要把 `EXAM_REJECT_KEEP` / `ROUND2_DONE_REJECT_KEEP` 加进去（这两类候选人不是"主动拒我们"的语义,他们可能回头）。

**现条文位置**：`scripts/inbox/cmd_analyze.py::_STAGE_AWARE_NEED_BOSS` + `scripts/inbox/cmd_scan.py::_SKIP_STAGES` + AGENT_RULES.md §1.1 / §2 / §3 / §4.2 / §5.9b / §5.19 / §5.20 / §5.20b + skills/recruit-ops/README.md 自动化任务表 + tests/test_v384_terminal_split.py。

---

## 15. 2026-05-11 — cron auto_reject 从"留池"回退到物理删档

**版本**：v3.8.3 回退（v3.5.11 决策反转）
**触发背景**：产品复盘"笔试 3 天 DDL 自动拒"场景时，发现 v3.5.11 主动收紧把这条 cron 路径从"拒信 + `talent.cmd_delete` 物理删档"改成了"拒信 + 推 `EXAM_REJECT_KEEP` 留池"——这是 §2(2026-04-22) 那次事故的**应激修复**，但当时**留池**这一改动只是事故防御的副产物，并不是产品意图。

**事故面回顾（§2 同一事故，从本规则视角）**：v3.5 前的 cron 流程本来就是"先发拒信 → 立刻 `talent.cmd_delete`"。事故源是：
1. `outbound.cmd_send` 实际**已经把拒信发出去了**（SMTP 200 OK），
2. 但回写 `talent_emails` 失败——因为 DB CHECK constraint 不接受 `context='rejection'`（Python 端 `_EMAIL_VALID_CONTEXTS` 也漏了这个值）；
3. executor 把这个**写库**失败误判成**发邮件**失败，于是没执行后续的 `talent.cmd_delete`；
4. 候选人继续留在 `EXAM_SENT`，下个 cron tick 又被扫到，**重发**了第二封拒信。

v3.5.11 当时做了**两件事**：
- (a) 真的根因修复：`_EMAIL_VALID_CONTEXTS` + DB CHECK 都接受 `'rejection'`（v3.5.11 migration `20260422_v3511_talent_emails_context_rejection.sql`, v3.8.7 已删档）。
- (b) 应激双保险：cron 行为从"删档"改成"留池 EXAM_REJECT_KEEP"，并在 `find_timeout_candidates` 加 `has_outbound_rejection` 二次防护——这样即使 stage 没改成功，下次也不会重发拒信。

**回退决策（v3.8.3）**：产品视角下"笔试 3 天没回话"基本等同于"候选人放弃"，留在 `EXAM_REJECT_KEEP` 池里的实际复用价值很低（v3.5.11 至今未发生过一例 force-jump 反向激活），反而每年都在累积"看着像活跃候选人但其实早走了"的池子，污染老板对 `cmd_list / cmd_status` 数字的判读。决定恢复 v3.5 前的物理删档行为，但**保留 v3.5.11 中真正解决根因的两项**：

- **保留** (a)：DB CHECK + `_EMAIL_VALID_CONTEXTS` 都接受 `'rejection'`——根因层修复不能动。
- **保留** (b) 中的 `has_outbound_rejection` 二次防护——这次 `cmd_delete` 失败（DB 抖动 / archive 失败）时，下次 cron tick 依然要被它拦住，决不允许重发拒信。**只把 (b) 中"推 EXAM_REJECT_KEEP 占位"这一步换成 `talent.cmd_delete --confirm-delete-talent <tid>`。**

**修订（v3.8.3，2026-05-11）**：

- **`scripts/auto_reject/executor.py`**：`_mark_exam_rejected_keep`（v3.5.11 引入）下线，替换为 `_delete_talent`——通过 `subprocess` 调 `talent.cmd_delete`，传 `--confirm-delete-talent <tid>` hard guard、`--actor auto_reject.cmd_scan_exam_timeout`、`--reason 'exam timeout: no submission within 3 days'`；从 stdout 拿到 `archive_path` 透传给上层用于飞书通知。
- **`scripts/auto_reject/cmd_scan_exam_timeout.py`**：
  - `_do_scan` 把 send 成功后的下一步从 `executor._mark_exam_rejected_keep` 改成 `executor._delete_talent`；
  - `_format_feishu_notice` 多收一个 `archive_path` 参数，飞书卡片体明示"已自动拒+删档（archive: ...）"+ 给出人工恢复指引；
  - dry-run 日志措辞改成"将拒+删档"；
  - `_delete_talent` 失败时 rc 非 0、stderr 提示"拒信已发但删档失败 → 人工 `talent.cmd_delete --talent-id <tid> --confirm-delete-talent <tid> --reason 'auto_reject manual cleanup'`"——**不**自动重试，依赖 `has_outbound_rejection` 防 cron 重发。
  - `find_timeout_candidates` 命中 `has_outbound_rejection` 的跳过提示改成"建议 `talent.cmd_delete --confirm-delete-talent`"（之前提示是改 stage 到 `EXAM_REJECT_KEEP`，那条路径在 v3.8.3 已无意义）。
- **`scripts/tests/test_auto_reject.py`**：`TestExecutorMarkRejectedKeep` 整组改名 `TestExecutorDeleteTalent`，verify subprocess 收到的 `--confirm-delete-talent` = `--talent-id`；`TestScanMain` 用 `_fake_delete_factory` 替代 `_fake_mark_factory`；新增 `test_delete_failure_returns_nonzero_but_does_not_resend` 验证 v3.5.11 二次防护在 v3.8.3 回退后**真正发挥作用**的场景（拒信已发但 cmd_delete 失败 → 下次扫被 `has_outbound_rejection` 拦下）。
- **`AGENT_RULES.md §2 stage 表 / §3 atomic CLI 表 / §4.6 / §5.4 速查表 / "不存在的 stage" 备注**：把"cron `auto_reject` 出口推 `EXAM_REJECT_KEEP`"全部改成"v3.8.3 起物理删档"；`EXAM_REJECT_KEEP` stage 入口列删除 `cron auto_reject` 引用（但**保留**人工路径 §4.6——老板手动选 `reject_keep` 时仍然推留池，这条路径不变）；§4.6 chain 说明里"与 cron 的模板/终态差别"那句改成对比"`rejection_generic` + EXAM_REJECT_KEEP 留池"（人工） vs "`rejection_exam_no_reply` + 物理删档"（cron）。
- **`SKILL.md §2.5`**：cron 行为详写为三步（send / cmd_delete / 飞书事后通知含 archive 路径），说明 v3.5.11~v3.8.2 期间是留池形态、v3.8.3 回退到删档，明确"保留 v3.5.11 中两项防护"的设计意图。§7 错误对照表新增 `auto_reject scan: delete failed` 一行，明示 HR 应人工 `talent.cmd_delete` 而**不**应人工再发一遍拒信。
- **`skills/recruit-ops/README.md`**：自动化定时任务表 + 仓库内状态机 ASCII 图加旁路注释（"候选人 ≥3 天未交 → cron auto_reject 自动 拒信+物理删档"），与人工 §4.6 区分。

**为什么这次回退是安全的**：

- **根因（DB CHECK 不认 `rejection`）已堵死**：迁移 `20260422_v3511_talent_emails_context_rejection.sql` + 代码端 `_EMAIL_VALID_CONTEXTS` 包含 `rejection` 都在主线，不会回退。即使 `cmd_delete` 因任何原因失败，拒信的 `talent_emails` insert 依然能写成功。
- **重发防护已加固**：`find_timeout_candidates` 内对 `has_outbound_rejection` 的判断是 v3.5.11 引入、v3.8.3 显式**保留并加测试**（`test_delete_failure_returns_nonzero_but_does_not_resend`），是这次回退能成立的**前提条件**。任何未来 PR 想动这条 SQL / Python 路径都必须先来看 §2 + §15。
- **删档可恢复**：`talent.cmd_delete` 自动把候选人快照、邮件、CV 目录归档到 `data/deleted_archive/<tid>_<UTC>.json` + `<tid>_emails.json` + `<tid>_cv/`，飞书事后通知卡片体明示 archive 路径——产品判断错时可由老板 `talent.cmd_undelete --archive-path ...` 一行恢复，不存在数据"硬丢"风险。
- **不会再走 v3.5.11 之前那条"发了 → 没删 → 重发"的死循环**：v3.5.11 之前是 `cmd_delete` 没执行后**没有任何**幂等防护；v3.8.3 是 `cmd_delete` 没执行但 `has_outbound_rejection` 已经能拦下来。

**未来回归 / 验证**：

- 已通过 `cd skills/recruit-ops && PYTHONPATH=scripts uv run python3 -m unittest tests.test_auto_reject -v`（12 tests OK，含新增的 `_delete_talent` hard-guard 校验 + 删档失败二次防护回归）。
- 全量 `tests/run_all.py` 待跑（紧随本条修订）。
- **不要回退** `_EMAIL_VALID_CONTEXTS` 中的 `'rejection'` 或 DB CHECK 中的 `'rejection'`——它们是 v3.5.11 真正解决根因的一对修复，v3.8.3 完全依赖它们。任何 PR 想精简 `_EMAIL_VALID_CONTEXTS` 必须先看 §2 + §15。
- **不要砍** `find_timeout_candidates` 里那段 `has_outbound_rejection` 拦截——CI 已有 `test_skips_when_outbound_rejection_already_sent` + `test_delete_failure_returns_nonzero_but_does_not_resend` 双重看护，但人眼审 PR 时也必须意识到它是 v3.8.3 删档路径的最后一道防线。

**现条文位置**：`scripts/auto_reject/executor.py::_delete_talent` + `scripts/auto_reject/cmd_scan_exam_timeout.py::_do_scan` + AGENT_RULES.md §2 / §3 / §4.6 / §5.4 + SKILL.md §2.5 / §7 错误对照表 + skills/recruit-ops/README.md 自动化任务表。

---

## 14. 2026-05-11 — 拒 offer 留池语义混桶（OFFER_DECLINED_KEEP 拆出）

**版本**：v3.8.2 修复
**触发现象**：老板查 `talent.cmd_list --stage ROUND2_DONE_REJECT_KEEP` 看到 5 个候选人，发现里面**两类完全不同语义的人**被混在同一个桶：
- **真·二面失败留池**（2 人，t_d03noa / t_hpj6br，2026-04-14）：经 `interview.cmd_result --round 2 --result reject_keep` 推过来，audit action=`round2_reject_keep`。
- **拒 Offer 留池**（3 人，t_lmu39m / t_z04u9v / t_256klz，2026-05-10）：是 §13 误删事故复发后由 `tools/restore_3_offer_decline_candidates.py` 恢复时，按当时 §4.13 POST_OFFER_FOLLOWUP 分支让 agent 跑 `talent.cmd_update --stage ROUND2_DONE_REJECT_KEEP --force --reason "拒绝offer但保留在人才库"` 强制推过来的。

老板无法直接从 stage 字段区分这两类候选人，事后追溯只能靠 audit `talent_events` 反推；导致：

- `cmd_list / cmd_status` 自动 bucket 时把"我们 say no"和"候选人 say no"展示成一类，误导老板判断；
- §4.9 force-jump 反向激活语义对两类不一样（二面失败 → 重新约二面 / 笔试；拒 offer → 重发 offer 或重启沟通），但 stage 不区分时 agent 没法直接给老板正确建议；
- INCIDENT_RULES.md §11(.3) 早就指出"`AGENT_RULES.md §4.13 POST_OFFER_FOLLOWUP 分支提供了'删档'作为合法选项 2`：违反 v3.6 设计原意"——但当时只解决了"不该删档"，**没回头解决"不该和真·二面失败混桶"**。

**根因**（v3.6 设计欠账）：v3.6 状态机收口时只保留一个二面相关的留池叶子态 `ROUND2_DONE_REJECT_KEEP`，原本只承载 `ROUND2_SCHEDULED → reject_keep` 一条入边。后来 §4.13 POST_OFFER_FOLLOWUP 分支需要"留池 + 含拒信"出口时，文档**借用**了 `ROUND2_DONE_REJECT_KEEP` 当占位（squat），让 agent `--force` 跨 stage 推过去——这是显式的设计妥协（语义上是"我们 say no"的桶被借给"候选人 say no"用），SKILL.md §2.4 当时也写了 `talent/cmd_update.py --stage ROUND2_DONE_REJECT_KEEP --reason "拒绝offer但保留在人才库" --force`。

**修订（v3.8.2，2026-05-11）**：拆桶——

- **`scripts/lib/core_state.py`**：`STAGES` 加 `OFFER_DECLINED_KEEP`（"已拒 Offer（保留人才库）"）。状态机从 12 个扩到 13 个 stage。
- **v3.8.2 migration `20260511_v382_offer_declined_keep.sql`** (v3.8.7 已删档, git log 取)：CHECK constraint 加新枚举；同迁移内**自动反推回填**——把 `ROUND2_DONE_REJECT_KEEP` 中**没有** `round2_reject_keep` audit 事件的候选人迁到 `OFFER_DECLINED_KEEP`，并写一条 `actor='migration', action='stage.changed'` 审计事件方便事后追溯。线上 3 人正确分流。
- **`scripts/talent/cmd_update.py`**：`_NATURAL_TRANSITIONS` 加 `(POST_OFFER_FOLLOWUP, OFFER_DECLINED_KEEP)`——以后 §4.13 POST_OFFER_FOLLOWUP 分支推留池**不再需要 `--force`**。
- **`scripts/outbound/cmd_send.py`**：`_STAGE_TO_CONTEXT` 加 `OFFER_DECLINED_KEEP → followup` 映射。
- **`AGENT_RULES.md §2 / §4.13 / §5.15 / §5.20`**：stage 表新增；§4.13 POST_OFFER_FOLLOWUP 分支 chain 改为推 `OFFER_DECLINED_KEEP`（无 `--force`）；§5.20 速查表把三个 keep-pool 叶子态并列。
- **`SKILL.md §2.4`**：✅ CORRECT WORKFLOW FOR OFFER REJECTIONS 改写——明确推 `OFFER_DECLINED_KEEP`，老的 `--force --stage ROUND2_DONE_REJECT_KEEP` 写法 deprecated。
- **`SKILL.md §8`** 反模式条目 9：明确警告"v3.8.2 之前 `ROUND2_DONE_REJECT_KEEP` 同时承载两类语义是文档已知的 squat，自动 bucket 时把它们合并展示是事故级错误"。
- **`tests/test_infra.py::test_stages_set_is_complete`**：expected set 加 `OFFER_DECLINED_KEEP`。

**为什么有效**：

- 状态机层面物理拆开：`ROUND2_DONE_REJECT_KEEP` CHECK 约束之后只接受 `ROUND2_SCHEDULED → reject_keep` 一条来源（其他写入路径都被 _NATURAL_TRANSITIONS 之外，需要显式 `--force` + `--reason`），`OFFER_DECLINED_KEEP` 严格只接受 `POST_OFFER_FOLLOWUP → OFFER_DECLINED_KEEP`。
- 展示层自然分桶：`cmd_list / cmd_status` 不需要任何特殊代码，直接用 stage 字段 bucket 就能区分。
- force-jump 反向激活更准：以后老板说"X 之前拒了 offer，他改主意了" → agent 从 `OFFER_DECLINED_KEEP` 推回 `POST_OFFER_FOLLOWUP --force` 一步；老板说"X 二面没过，再约一次" → 从 `ROUND2_DONE_REJECT_KEEP` 推回 `ROUND2_SCHEDULING --force` 一步——两条路径不再混。

**未来回归 / 验证**：

- 已通过 `cd skills/recruit-ops && PYTHONPATH=scripts uv run python3 scripts/tests/run_all.py` (339 tests OK)。
- 生产 DB migration 已 apply（5 人 → 3 OFFER_DECLINED_KEEP + 2 ROUND2_DONE_REJECT_KEEP），3 条 backfill 审计事件已写入 `talent_events`（actor='migration'）。
- 待补回归：`tests/test_agent_chain.py` 加一条 §4.13 POST_OFFER_FOLLOWUP 分支推 `OFFER_DECLINED_KEEP` 的端到端 chain，验证不再 `--force`。

**现条文位置**：`scripts/lib/core_state.py::STAGES` + `scripts/lib/migrations/schema.sql` (chk_current_stage) + `scripts/talent/cmd_update.py::_NATURAL_TRANSITIONS` + AGENT_RULES.md §2 / §4.13 / §5.15 / §5.20 + SKILL.md §2.4 / §8 反模式 9。

---

## 1. 2026-04-23 — 自动拒"12h 软缓冲队列"全部移除

**版本**：v3.5.13 简化
**触发现象**：之前 agent 反复纠结"老板会不会取消队列里的待删项 / 这个候选人是不是合法改期 / 12h 窗口算到几点"。LLM 在多步缓冲逻辑里频繁出错。
**根因**：自动拒只有一种合理形态——笔试超时无回复。其它"自动拒"场景（迟到改期、面试缺席等）人工判断更可靠，软缓冲队列的复杂度收益为负。
**现行规则**：

- `auto_reject/` 当前**只有一个**脚本：`cmd_scan_exam_timeout.py`（cron 专用；agent 只跑 `--dry-run` 预览）。
- **没有** propose / cancel / execute_due / list / pending_store / llm_classify 一类「队列」概念。
- **没有** 12h 缓冲窗口或「合法改期白名单」。
- 任何"迟到改期"意图统一走 [AGENT_RULES.md §4.3 改期 chain](AGENT_RULES.md#43-候选人改期hr老板驱动回到-scheduling-等候选人-confirm)（`feishu.cmd_calendar_delete` → `outbound.cmd_send --template reschedule` → `talent.cmd_update`），由老板决策。
- agent 提议 `auto_reject.cmd_scan_exam_timeout` 时**必须**带 `--dry-run`；真跑只能由 cron。

**现条文位置**：SKILL.md §2.5。

---

## 2. 2026-04-22 — `reject_delete` 默认必须发拒信

**版本**：v3.5.x 修复
**触发现象**：候选人被 `reject_delete` 删档但完全没收到拒信通知。
**根因**：旧版 `interview/cmd_result.py --result reject_delete` 没有自动发拒信的副作用；老板默认它会发，agent 也以为它会发。
**现行规则**：

- `interview.cmd_result --result reject_delete` 与 `exam.cmd_exam_result --result reject_delete` **副作用**：自动先发 `rejection_generic.txt` 拒信，再删人。
- `--skip-email` **只**在老板已**线下手发**拒信时才传；**永远不要**为了"安全"建议加 `--skip-email`——会导致候选人无通知被删。
- 这条规则对 `talent.cmd_delete` / `common.cmd_remove` 不适用（这两条是纯删档，不发邮件，需要老板自己另起 `outbound.cmd_send`）。

**现条文位置**：SKILL.md §2.4 line "副作用（自 2026-04-22）"。

---

## 3. 2026-04-21 17:06 — 跨 stage 跳跃误走正常流程事故

**版本**：v3.5.4 修复
**触发现象**：老板说"直接进 ROUND2_SCHEDULED"，agent 试图按"走完整一面流程再到二面"那条路推进，结果真发了一面邀请邮件给候选人——邮件不可撤回。
**根因**：agent 把"直接跳到 X"当成"按正常流程推到 X"。语义上的跨 stage 跳跃**不是**业务流程的一步，它是修正状态机的一次性补丁。
**现行规则**：

- 当老板说出**带跨 stage 跳跃语义**的指令——`直接跳到 X` / `直接进 X 阶段` / `略过` / `跳过` / `强制` / `忽略前置` 等——**唯一**正确路径是 [AGENT_RULES.md §4.9 force-jump 单步 chain](AGENT_RULES.md#49-老板说直接跳到-x--§59-force-jump-单步)：

  ```
  talent.cmd_update --stage <target> --force --reason "boss原话: …"
  ```

- force-jump 单步 chain **不发**邮件、**不建**日历、**不更新**业务字段。
- 识别规则见 [AGENT_RULES.md §5 速查表](AGENT_RULES.md#5-表外的常见-intent)。
- **绝对禁止**：(a) 拿别的 chain 改改参数凑合；(b) 用多个 atomic CLI 试错式拼接；(c) 通过看 CLI 错误信息迭代修正参数。错的 chain 一旦执行，邮件 / 日历不可逆。

**现条文位置**：SKILL.md §2.3.1 关键纠错段。

---

## 4. v3.5.10 — "完整信息"被两个空标题敷衍事故

**触发现象**：老板问"把 X 的完整信息 / 档案 / 全部资料发给我"，agent 回复只贴了两个空标题（`📋 候选人档案` / `📂 文件状态`）外加一行 `cv_path`，就声称"信息已同步"。
**根因**：agent 把"完整信息"理解成了一个语义模板，而不是 `talent.cmd_show` 输出的字段全集。
**现行规则**：

- "完整信息" / "档案" / "全部资料" = `talent.cmd_show <id>` 输出里**所有非空字段一项不漏**；
- **不允许**编造空标题占位；
- **不允许**只贴 `cv_path` 就声称"已同步"；
- 字段量大时按 SKILL.md §7 / §8 PII 原则裁剪，但被裁剪的字段必须**显式说明被略过**，而不是当作不存在。

**现条文位置**：SKILL.md §8 反模式条目（精简版仅保留指针）。

---

## 5. v3.5.10 — `doc_<hex>_` 前缀污染 cv_path 事故

**触发现象**：飞书 Gateway 把附件落盘时给文件名加了 `doc_<hex>_` 前缀，agent 直接把这个前缀写进了 `cv/` 目录与 `talents.cv_path`。后续邮件发 CV 时附件名也带前缀，候选人收到一个奇怪文件名。
**根因**：Gateway 加前缀是为了避免本地碰撞，但前缀对外不应可见；旧代码没处理。
**现行规则**：

- `lib.candidate_storage.import_cv` 已自动剥前缀（任何形如 `doc_[0-9a-f]+_<filename>` 的文件名都会被 normalize）。
- 历史数据用 `talent.cmd_normalize_cv_filenames` 一次性补救。
- agent **不要**在 SQL / 提示里手工拼带前缀的文件名。

**现条文位置**：SKILL.md §8 反模式条目（精简版仅保留指针）。

---

## 6. v3.5 — `data/followup_pending/` 文件队列彻底废弃

**触发现象**：旧版本用磁盘文件夹（`data/followup_pending/` / `data/followup_archive/`）作为"待跟进"队列；agent 偶尔会"列出 pending 文件夹里有谁"，但磁盘文件早已删除，列出结果不一致。
**根因**：v3.5 把所有 follow-up 状态合并进 `talent_emails.status` / `ai_payload` / `replied_by_email_id` 字段，磁盘队列只是过渡期方案。
**现行规则**：

- **不要**引用 `data/followup_pending/` / `data/followup_archive/`——这些目录已不存在。
- **不要**在 SQL 里用 `talents.followup_*` 或 `talents.*_last_email_id` 字段——已 DROP。
- 邮件状态唯一真源：`talent_emails.status` / `ai_payload` / `replied_by_email_id`。

**现条文位置**：SKILL.md §8 反模式条目（精简版仅保留指针）。

---

## 7. v3.4 → v3.5 — 业务剧本包装层全部移除

**版本**：v3.5 重构
**触发现象**：旧版有一组"业务剧本"包装脚本（`cmd_round1_schedule` / `round2/` 整目录 / `followup/` 整目录 / `cmd_reschedule` / `cmd_defer` / `daily_exam_review` / `exam_ai_reviewer` / `cmd_finalize_interview_time` / `cmd_wait_return_resume` / `cmd_reschedule_request` / `ops/cmd_push_alert`），LLM prompt 历史里仍可能出现这些名字。
**根因**：包装脚本掩盖了原子动作的副作用；agent 调用一次就可能同时改 DB + 发邮件 + 建日历，confirm 协议失效。
**现行规则**：

- 任何包装脚本都**已不存在**——不要再提议它们。
- 所有多步流程按 [AGENT_RULES.md §4 chain](AGENT_RULES.md#4-scenarios) 重新规划，用 `lib/run_chain.py` 的 `Step(...)` 串原子 CLI。
- 完整 atomic CLI 清单见 [AGENT_RULES.md §3](AGENT_RULES.md#3-commandsatomic-cli) + [CLI_REFERENCE.md](CLI_REFERENCE.md)。

**现条文位置**：SKILL.md §1.4 架构概览段。

---

## 8. v3.5 → v3.8.7 — `cmd_parse_cv.py` 删除，解析搬 `lib/cv_parser.py`

**触发现象**：旧版 `cmd_parse_cv.py` 仅解析不去重，导致同一个候选人多份记录。
**现行规则**：

- 永远用 `intake.cmd_ingest_cv.py`（解析 + 去重 + 生成预览 payload）；
- `cmd_parse_cv.py` 文件本体在 v3.8.7 (A4.1) **整体删除**——原 5 个 utility 函数（`_download_pdf_from_feishu` / `_extract_text_from_pdf` / `_extract_pdf_metadata` / `_llm_parse_cv_fields` / `_format_preview`）搬到 `lib/cv_parser.py`，cmd_ingest_cv 内部 import 已切到 `from lib import cv_parser as _parse_mod`。
- 过渡期 `lib/cv_parser.py` 仍保留 `_` 前缀的别名，v4.0 评估删。

**现条文位置**：SKILL.md §4.1 注意 + §8 反模式（精简版仅保留指针）+ docs/CLI_REFERENCE.md §cmd_parse_cv.py 删除条目。

---

## 8.5 v3.8.7 — `test_v3*_phase*.py` 命名负担, 但**经测不冗余**

**触发现象**：仓库里有 7 个 `test_v{ver}_phase{N}.py` 测试文件 (test_v33_phase1 / test_v34_phase1 / test_v34_phase5 / test_v35_phase1_inbox_general / test_v35_phase3_exam_grader / test_v35_phase4_notify / test_v384_terminal_split)。从文件名看像是历史阶段交付的快照, 直觉上"应该可以归档了"。

**实测结论 (v3.8.7 A4.2)**：

| 文件 | 测试类 | 覆盖的模块 | 去掉后总覆盖率掉 |
|---|---|---|---|
| test_v33_phase1 | self_verify / cmd_send / cmd_update / cmd_delete | lib.self_verify + 3 个 atomic CLI | **−4%** |
| test_v34_phase1 | prompts / analyzer / scrub_draft / cmd_send 缓存 / location_locked | lib.prompts / lib.email_scrubber | **−2%** |
| test_v34_phase5 | calendar_create / cv_attach / calendar_delete / bg_helpers | feishu.cmd_calendar_* + bg_helpers | **−2%** |
| test_v35_phase1_inbox_general | inbox prompt schema / stage-aware routing | inbox.cmd_analyze | **−1%** |
| test_v35_phase3_exam_grader | exam_grader / cmd_exam_ai_review | lib.exam_grader | **−1%** |
| test_v35_phase4_notify | feishu.cmd_notify (boss/hr/interviewer) / old push_alert | feishu.cmd_notify | **−1%** |
| test_v384_terminal_split | confirm_interview stage override / inbox terminal skip | inbox.cmd_analyze + 终态约束 | **−1%** |

**现行规则**：

- **不要归档任何 phase 测试**——每个都带独立覆盖, 无任何两个文件功能重叠;归档任一个都会回退 1-4% 总覆盖率。
- 文件名里的 "v3.x_phase{N}" 是历史出货时的版本号, 不是 deprecation 标记;新看到这种命名直接当作正常测试套件读, 不要凭文件名"猜它过时"。
- 若想给它们换个更说明性的名字 (例如 test_v33_phase1 → test_self_verify_atomic), 走纯 rename git mv, 不要 git rm。

**现条文位置**：本文 §8.5 (v3.8.7 A4.2 实测结果)。

---

## 9. v3.5 — `exam.fetch_exam_submission` 重拉行为废弃

**触发现象**：agent 看到候选人提交笔试后，会跑 `exam.fetch_exam_submission` 想"重拉一次确保完整"，但 `inbox.cmd_scan` 已经在每次扫到候选人新邮件时自动落盘附件了，重拉只会撞 IMAP 配额并制造副本。
**现行规则**：

- 候选人笔试附件由 `inbox.cmd_scan` 自动落盘到 `data/exam_submissions/<姓名>__<tid>/`，普通邮件附件仍落到 `data/candidates/<tid>/email/em_<eid>/`；
- 元数据写在 `talent_emails.attachments` JSONB；
- agent **不要**调 `exam.fetch_exam_submission` 重拉。

**现条文位置**：SKILL.md §8 反模式（精简版仅保留指针）。

---

## 10. v3.4 之前 — 旧 stage 名（带 `_DONE_` / `OFFER_HANDOFF`）已合并

**触发现象**：agent prompt 历史里偶见 `OFFER_HANDOFF` 一类 stage 名，但当前 `core_state.py::STAGE_LABELS` 里查无此名。
**现行规则**：

- stage 标签的**唯一**真源是 `scripts/lib/core_state.py::STAGE_LABELS`；
- 速查表见 [AGENT_RULES.md §2 stage 状态机](AGENT_RULES.md#2-stages)；
- 引用任何 `STAGE_LABELS` 之外的 stage 名都是 bug，先停下问老板。

**现条文位置**：SKILL.md §8 反模式（精简版仅保留指针）。

---

## 12. 2026-05-10 — 3 人误删事故（POST_OFFER_FOLLOWUP confirm 跳过）

**触发现象**：老板在飞书发"傅雨涵 / 李志鹏 / 曾科源 拒了我们 offer，从人才库删除"——生产 agent **未先 propose 等待二轮 confirm**,直接对 3 个 candidate 各跑了一条 `talent.cmd_delete`,把 3 位 `POST_OFFER_FOLLOWUP` 阶段（=完整通过一面/笔试/二面、已发 offer 的高价值候选人）从 DB 物理删除。归档完整保留在 `data/deleted_archive/2026-05/t_lmu39m_*.json` / `t_z04u9v_*.json` / `t_256klz_*.json`。

**根因**（三层叠加）：

1. **SKILL.md §2.4 早期条文未显式禁止"单消息一体 confirm"**：原文虽要求"confirm 必须显式指名破坏动作"+"confirm 与 propose 同一轮"，但没有 spell-out "agent **不能**把老板首次表达意图的同一条消息同时当作 propose + confirm"。LLM 把"X / Y / Z 拒了 offer，**从人才库删除**" 解析为"意图陈述 + 授权词 = 一体 confirm"，跳过 propose 直接执行。
2. **SKILL.md §2.3.1 ad-hoc multi-command 禁止条款未覆盖到 destructive 场景**：原文针对的是"打包套餐"，没有 spell-out 多 candidate 的破坏性删档必须**逐一** propose-confirm。
3. **AGENT_RULES.md §4.13 POST_OFFER_FOLLOWUP 分支提供了"删档"作为合法选项 2**：违反 v3.6 设计原意（`ROUND2_DONE_REJECT_KEEP` 是为"留池等以后再捞"设计的），给 LLM 误删提供了 chain 内的合法路径。

**修订（v3.8 patch，**已部分被 v3.8.1 替换**——见 §13）**：

- **SKILL.md §2.3** 顶部新增"双轮硬规则"段：所有 §2.3 写动作必须**跨两条用户消息**完成 propose-confirm。**仍生效**。
- **SKILL.md §2.4** 加 3 条强化：双轮强制 / 多 candidate 必须分别 propose-confirm / 意图陈述 ≠ 授权。**仍生效**。
- ~~**AGENT_RULES.md §4.13 POST_OFFER_FOLLOWUP 分支**移除"物理删档"选项 2（仅保留"留池"+"挽留"）~~ → **v3.8.1 撤销**（chain 层 enforce 因 §13 复发被证明无效;改为代码层 hard guard 兜底,见 §13）。

**未来回归 / 验证**：

- 任何 destructive chain 在 propose 前就直接执行 → CI 红（待补 `tests/test_skill_confirm_protocol.py` 用 fixture 输入"X 拒了，删除"测 agent 必须先 propose）。
- §4.13 chain v3.8.1 简化后 `tests/test_agent_chain.py` 需要相应调整（POST_OFFER_FOLLOWUP 恢复"留池/删除"二选一）。

**现条文位置**：SKILL.md §2.3 双轮硬规则块 + §2.4 强化条款（doc 层）+ §13 代码层 hard guard（v3.8.1）+ AGENT_RULES.md §4.13。

---

## 13. 2026-05-10 — 3 人误删事故复发（doc 修订对运行中 agent 失效）

**触发现象**：§12 修订上线**约 30 分钟后**,生产 agent 又一次把同样的 2 位候选人（傅雨涵 t_lmu39m / 李志鹏 t_z04u9v）从 POST_OFFER_FOLLOWUP 物理删除。曾科源 t_256klz 因为已恢复在 DB 中而幸免（v3.5 cmd_delete 的 "candidate not found" 短路保护）。归档新一批 `*_20260510T204247.json` / `*_20260510T204251.json`,业务数据通过一次性脚本 `tools/restore_3_offer_decline_candidates.py` 自动幂等恢复（FS 归档目录 + 邮件 timeline 完整保留）。

**根因**：

1. **doc 修订对正在运行的 agent 不生效**：生产 Hermes Gateway agent 的 prompt context 在 §12 修订前就已加载完毕,新规则（"POST_OFFER_FOLLOWUP 不给删档" / "双轮 propose-confirm"）它**没有 reload**,继续按旧 prompt 行动。这是 agent 框架层的根本约束：**doc-only 修订对 long-running agent 进程零延迟接管几乎不可能**。
2. **§12 修订仅在 chain 层 enforce**：删除选项被从 chain 菜单里拿掉,但 `talent.cmd_delete` CLI 本身仍然接受任何调用——agent 只要能拼出有效命令就能跑过去,绕开 chain。
3. **LLM 把"用户原话"当 confirm**：老板飞书发"X / Y / Z 拒了 offer 不来了" → agent 把整句话识别为"意图陈述 + 已授权",直接调 cmd_delete（老办法又来了一遍）。

**修订（v3.8.1，2026-05-10 21:00）**：将 enforce 从 doc 层下沉到**代码层 hard guard**——

- **`talent/cmd_delete.py`**：新增 required argument `--confirm-delete-talent <talent_id>`，值必须严格等于 `--talent-id`。缺失 / 不匹配 → `UserInputError` 退出（rc=1）。原理：
  - LLM 在 propose 命令时必须把 talent_id 写**两次**——任何"凭意图猜出 talent_id 一次"都不够；
  - 老板看到 propose 必须能识别"为什么这个 ID 出现两次"才能 confirm；
  - cron / 系统调用方显式传一致的值 = 表明知情授权。
- **`interview/cmd_result.py`** + **`exam/cmd_exam_result.py`**：`--result reject_delete` 路径同款加 `--confirm-reject-delete <talent_id>` guard。
- **`auto_reject/executor.py`**：v3.5.11 起已不调 `cmd_delete`,no-op。
- **AGENT_RULES.md §4.13 chain 改动**：撤销 v3.8 chain 层"禁删档"收紧;chain 菜单恢复"留池/删除"二选一。所有"删除"路径示例都标注必须带新 hard guard 参数。
- **AGENT_RULES.md §3 / SKILL.md §2.4 命令清单**：`talent.cmd_delete` 行注明 hard guard 强制。

**为什么这次能拦住**：即使 LLM 仍按旧 prompt 行动,subprocess 进 CLI 会被 argparse 物理拒绝（unknown error → cli_wrapper.py 报 INPUT ERROR）。**这是不依赖 prompt reload 的最后一道防线**。

**未来回归 / 验证**：

- `tests/test_destructive_guards.py`（待补）：调 cmd_delete 不带 `--confirm-delete-talent` 必须 rc=1 + INPUT ERROR；调带 mismatched 值同样 rc=1。
- 生产 agent 的 prompt 在新 deploy 时一并 reload 新 SKILL.md / AGENT_RULES.md。
- 运行 `lib/cli_wrapper.py` 的飞书告警接收方需要确认能看到 `[cli_wrapper] INPUT ERROR: 缺失 --confirm-delete-talent` 这种新 error 模式（不是 crash,所以**不会**被 lib/cli_wrapper.py 自动推飞书 critical）。

**现条文位置**：`scripts/talent/cmd_delete.py:_build_parser` + `_do_delete` 头部 + `scripts/interview/cmd_result.py:parse_args` + `scripts/exam/cmd_exam_result.py:parse_args` + AGENT_RULES.md §4.13 硬规则段。

---

## 11. v3.5.13 — 默认时间格式硬规定 Asia/Shanghai

**触发现象**：早期 agent 在不同回复里用过 `+08:00`、`UTC+8`、`Asia/Shanghai` 三种写法，导致 `cmd_calendar_create` 偶尔接收到不预期的时区串。
**现行规则**：

- 任何 `--time` 参数**必须**是 `YYYY-MM-DD HH:MM` 格式 + Asia/Shanghai 隐式时区（这是 `core_state.py` 打时间戳时用的服务器硬时区）；
- 在 propose 命令时把解析后的时间**原样 echo** 给用户，让老板有机会纠正；
- 自然语言时间措辞（"本周日"、"明天下午"）**只**在命令自身已返回该字段、或 agent 已通过 `common.cmd_weekday` 等确定性日历查询核对过时才允许追加（详见 SKILL.md §7.2.1）。

**现条文位置**：SKILL.md §3 + §7.2.1。

---

## 维护

- 新事故出现时**追加**新条目到本文件顶部（保持时间倒序）；
- SKILL.md §8 / AGENT_RULES.md 各 chain 的"硬规则"块**只保留指针**，不重复正文；
- 发现条目过期 / 已被代码层修复到不需要规则约束时，**不要**直接删——加 `**已废弃**：xxx` 标记，保留事故史。
