---
name: recruit-ops
description: >
  飞书招聘运营 skill，服务 HR 与 Boss 两侧。HR 侧：CV 附件自动识别、解析、去重与录入；
  Boss 侧：候选人查询、面试安排、笔试流转、结果记录、Offer 交接。PostgreSQL 为唯一数据源，
  所有操作通过 recruit-ops 仓库的稳定 CLI 完成。任何涉及候选人、简历、面试、笔试、Offer、
  人才库、招聘进度的消息都必须优先读本 skill 后再路由。
triggers:
  # 招聘核心话题
  - 招聘
  - 候选人
  - 人才库
  - 招人
  - 录用
  - 录入
  - 新候选人
  - 导入候选人
  # 阶段
  - 面试
  - 一面
  - 二面
  - 笔试
  - offer
  - 安排一面
  # 结果 / 自然语言判断
  - 通过了
  - 拒了
  - 不合适
  - 面了个
  - 面了一
  - 面完了
  # 简历 / 附件
  - 简历
  - 发简历
  - 附件简历
  - 简历文件
  - 简历.pdf
  - 简历.docx
  - .pdf
  - .docx
  # 常见岗位与场景关键词
  - 应届生
  - 实习生
  - 量化研究员
  - 量化研究实习
  - 元_天
  - 元/天
  # 查询短语
  - 查一下
  - 看看候选
  - 加个候选
  - 有个人
---
# Recruit Ops

> **Open-source deployment mapping**:
> - `<workspace_root>` = the repository root that contains `README.md`, `docs/`, and `skills/`
> - Scripts directory: `<workspace_root>/skills/recruit-ops/scripts/`
> - Runtime interpreter: `<workspace_root>/skills/recruit-ops/.venv/bin/python3` (or `uv run python3`, both equivalent)
> - Hermes Gateway can load this skill from a runtime import directory such as `~/.hermes/skills/openclaw-imports/recruit-ops/`
> - File paths inside Hermes messages may already be absolute. If so, pass them through to CLI `--file-path` args verbatim unless your deployment requires an explicit path remap.

Operate the `recruit-ops` workflow through stable CLI commands in the repository. PostgreSQL is the single source of truth; never summarize state from memory.

This skill is an **agent contract** used by two distinct actors:
- **HR** sends CV attachments and `【新候选人】` / `【导入候选人】` templates in the Feishu group. The skill auto-identifies CVs (§4.1.1), parses them (§2.1.5), and walks HR through a deduped register flow.
- **Boss** uses natural language to query status, drive schedule, record results, and request hand-offs.

The skill is designed to be:
- strict about routing, side-effects, and result presentation
- portable in body text (use `<workspace_root>` placeholders everywhere except the deployment-mapping block above)
- consistent with the code — when body and code disagree, code wins

---

## 1. Execution Contract

### 1.1 Canonical invocation form

Every command in this skill uses exactly one form:

```bash
uv run python3 scripts/<group>/<command>.py ...
```

Always run from the repo root:

```bash
<workspace_root>/skills/recruit-ops
```

Rules:
- Do **not** mix `uv run python3 scripts/...` and bare `python3 <group>/...`. Pick the canonical form above.
- Do **not** rely on shell aliases or machine-local absolute paths (e.g. `/home/admin/...`).
- For cron / systemd usage that needs imports, set `PYTHONPATH=scripts` explicitly (see [CLI_REFERENCE.md](../skills/recruit-ops/docs/CLI_REFERENCE.md#cron_runnerpy)).

### 1.2 Source of truth

- The PostgreSQL-backed `recruit-ops` state is canonical. All stage names, counts, and schedules must come from a command, not from the model's memory.
- When a query command already exposes a view, do **not** reconstruct that view by hand. The canonical DB-backed views are:
  - `common/cmd_status.py` — full candidate list, or detail for one candidate
  - `common/cmd_search.py` — keyword search, or the active-only view
  - `common/cmd_today_interviews.py` — day-scoped interview schedule
- Canonical stage labels live in `scripts/lib/core_state.py` (`STAGE_LABELS`). This skill mirrors them in §6; if code and skill disagree, code wins and the skill must be updated.

### 1.3 Primary reference

- [skills/recruit-ops/docs/CLI_REFERENCE.md](../skills/recruit-ops/docs/CLI_REFERENCE.md) — complete argument tables, edge cases, and cron notes.

### 1.4 Who talks to this skill

Two actors, two distinct message shapes:

| Actor | Typical message | Skill's job |
|---|---|---|
| **HR** | CV attachment (PDF/DOCX), `【新候选人】` / `【导入候选人】` text templates, status-fix requests ("笔试已手工发出"), candidate reschedule forwards | Identify, parse, dedup, produce a preview for HR to confirm, then execute the confirmed command |
| **Boss** | Natural-language queries and directives ("今天谁有面试", "张三一面改到明天下午三点", "李陆斌一面被拒保留") | Resolve identity + time, propose the command, wait for confirmation, execute |

The skill does not track "who is speaking" explicitly — the message shape is enough to disambiguate. Both actors are trusted; the §2.2.1 confirmation protocol applies to both.

**Identities**: the deployment configures two Feishu identities via env var — `FEISHU_BOSS_OPEN_ID` (boss) and `FEISHU_HR_OPEN_ID` (HR). `intake/cmd_send_cv.py --to boss|hr` routes on these. The skill never reads or echoes these open_ids — it only uses the abstract `boss` / `hr` labels.

---

## 2. Safety Model

Every command falls into one of three safety classes. A command is classified by its **maximum possible side-effect**, not by the common case.

### 2.1 Read-only (safe to run speculatively)

Strictly no DB writes and no outbound email / Feishu / calendar calls under any flag combination.

- `common/cmd_status.py`
- `common/cmd_search.py`
- `common/cmd_today_interviews.py`

### 2.1.5 Auto-triggered preview (CV intake)

A narrow intermediate class: these commands are triggered automatically when the user (typically HR) drops a CV-shaped message, they **do not mutate state**, but they incur real cost (LLM parse, DB read, file I/O) and produce a structured preview that is itself a proposal for the next mutating step.

- `intake/cmd_ingest_cv.py` — the canonical example. See §4.1.1 for when/how to trigger.

Rules:
- **Run automatically** without §2.2.1 confirmation when the incoming message matches the CV-detection rules in §4.1.1. The command's own output is the "proposal" shown to HR.
- **Forward the output verbatim** to HR. Do not paraphrase field diffs or summarize them.
- The output carries one or more `[OC_CMD_ON_CONFIRM*]` payloads that are **proposals for §2.2 mutating commands** (`cmd_attach_cv.py` / `cmd_new_candidate.py` / `cmd_import_candidate.py`). Those downstream commands **do** require the full §2.2.1 confirmation protocol; HR's natural-language reply to the preview counts as that confirmation.
- Never invoke `cmd_ingest_cv.py` speculatively on non-CV messages, and never invoke it on the Boss side unless the Boss explicitly asks to ingest a file.

### 2.2 Mutating (changes DB state and/or sends external messages)

Any command in this class writes DB state, sends an outbound message, or both. **Every mutating command requires a pre-execution confirmation from the user — without exception.**

Commands in this class:

- **DB + external**: `intake/cmd_ingest_cv.py`, `intake/cmd_attach_cv.py` (requires `--confirm`), `intake/cmd_new_candidate.py`, `intake/cmd_import_candidate.py`
- **External only (sends Feishu / email)**: `intake/cmd_send_cv.py`, `common/cmd_interview_reminder.py`
- **DB + external**: `round1/cmd_round1_schedule.py`, `common/cmd_finalize_interview_time.py`
- **DB + external**: `interview/cmd_result.py`, `interview/cmd_reschedule.py`, `interview/cmd_defer.py`
- **DB + external**: `exam/cmd_exam_result.py`, `exam/daily_exam_review.py`
- **DB + external**: `common/cmd_wait_return_resume.py`, `common/cmd_reschedule_request.py`
- **DB only (destructive)**: `common/cmd_remove.py` — see §2.3

### 2.2.1 Pre-execution confirmation protocol (mandatory)

Before running **any** command from §2.2, follow this loop:

1. **Resolve** a unique `talent-id` (via §3) and all required args, including `--time` in `YYYY-MM-DD HH:MM` Asia/Shanghai.
2. **Present** the fully-resolved command to the user, verbatim, in a code block. Include:
   - the exact `uv run python3 scripts/...` invocation with every arg filled in,
   - the candidate's name + `talent-id` + current stage (from a prior read-only query if needed),
   - a one-line description of what will happen (DB change, email sent, Feishu message, etc.).
3. **Wait** for an explicit user confirmation in the next turn. Acceptable affirmatives: `yes` / `ok` / `go` / `执行` / `确认` / `好` (plus paraphrases). Silence, clarifying questions, or any change of subject count as "not confirmed".
4. **Execute only after confirmation**, and only the command that was shown. If the user changes a parameter in their reply, re-present the updated command and go back to step 3.
5. **Never batch**. One confirmation authorizes one command. Multi-step flows (e.g. search → finalize → result) require a fresh confirmation at each mutating step.

Exceptions: **none**. This protocol applies even for "obvious" cases like `cmd_result.py ... --result pass`, even inside long-running recruiting workflows, and even when the user's request already contained the full command.

### 2.3 Destructive (strict superset of §2.2 confirmation)

These commands permanently destroy data or move a candidate into a terminal rejection state. They require all of §2.2.1 **plus**:

- A generic "yes / ok / 好" is **not sufficient**. The confirmation must explicitly name the destructive action — e.g. `"是，删除 t_xxx"`, `"confirm reject_delete for t_xxx"`, `"yes, remove 张三 (t_xxx)"`.
- The confirmation must be in the same turn as the proposed command; never rely on prior-turn intent.
- If the user's reply is affirmative but does not name the destructive action, re-ask.

Commands in this class:

- `common/cmd_remove.py` — permanent removal from the candidate list.
- `interview/cmd_result.py ... --result reject_delete` — rejection with removal from talent pool.
- `exam/cmd_exam_result.py ... --result reject_delete` — same, exam branch.
- `intake/cmd_attach_cv.py` — requires `--confirm` on the CLI AND a same-turn natural-language confirmation of the match.

**Rule (single-valued)**: without an explicit delete instruction from the user, the only permissible rejection is `reject_keep`. `reject_delete` is never chosen by default.

---

## 3. Ambiguity Resolution Rules

Most production errors come from acting on an under-specified request. Apply these rules **before** choosing a mutating command.

| Missing / ambiguous | Required resolution |
|---|---|
| Identity (name only, no `talent-id`) | Run `common/cmd_search.py --query <name>`, then use the returned `talent-id`. |
| Multiple search hits | Show the matches to the user and ask which one. Never pick by alphabetical order, recency, or vibes. |
| Referential phrases (`他`, `她`, `上周那个候选人`, `那个女生`) | Only accept if a unique candidate was already established earlier in the same turn. Otherwise, search. |
| Round unspecified for interview ops | Ask. Do not infer from stage alone unless the stage uniquely determines the round (`ROUND1_*` → 1, `ROUND2_*` → 2). |
| Time given as natural language (`明天下午三点`) | Resolve to an explicit `YYYY-MM-DD HH:MM` in **Asia/Shanghai (+08:00)** — the hardcoded server timezone used by `scripts/lib/core_state.py` when it stamps times. Echo the resolved time back to the user in the reply. |
| `--result` unspecified (pass / reject_keep / reject_delete / pass_direct) | Ask. Do not default. |
| Reject without a keep/delete hint | See §2.3: `reject_delete` requires explicit same-turn user confirmation; otherwise use `reject_keep`. |

**Hard rule**: Any mutating command must (a) target a **unique** `talent-id` and (b) pass the confirmation protocol in §2.2.1 before execution. If the input doesn't already provide a unique `talent-id`, resolve identity first — then present the resolved command and wait.

---

## 4. Intent Routing

One unified table. Groups (`intake/`, `round1/`, `interview/`, `exam/`, `common/`) are directories under `scripts/`.

### 4.1 Candidate intake

| Intent | Command |
|---|---|
| `【新候选人】` text template from HR | `uv run python3 scripts/intake/cmd_new_candidate.py --template "<raw multi-line message>"` |
| `【导入候选人】` historical candidate | `uv run python3 scripts/intake/cmd_import_candidate.py --template "<raw multi-line message>"` |
| CV attachment (PDF/DOCX) — **auto-triggered**, see §4.1.1 | `uv run python3 scripts/intake/cmd_ingest_cv.py --file-path <path> --filename <filename>` |
| Attach CV to an existing candidate (after `cmd_ingest_cv` preview) | `uv run python3 scripts/intake/cmd_attach_cv.py --talent-id <id> --cv-path <path> --confirm [--field key=value ...]` |
| Send CV PDF to Boss (default) or HR | `uv run python3 scripts/intake/cmd_send_cv.py --name "<name>" [--to boss\|hr]` *(default: `boss`; `--to hr` sends to HR)* |

Notes:
- For multi-line `--template`, pass a real newline-bearing string. In bash, use heredoc: `--template "$(cat <<'EOF' ... EOF)"`, or `$'line1\nline2'`. Do **not** pass a double-quoted `"\n"` literal — bash will not expand it.
- `cmd_parse_cv.py` is **deprecated**. Do not use it.
- When Boss says "看简历 / 把某某的简历发过来" — use `cmd_send_cv.py` **without** `--to` (default is boss). Only pass `--to hr` when explicitly asked to send to HR.

### 4.1.1 CV auto-detection & routing

When an inbound message in the Feishu group contains a file attachment, decide whether it is a candidate CV **before** deciding what to run.

**Step 1 — Is this a CV?** Treat the file as a candidate CV if **any** of the following holds:

- The filename matches the CV shape: job title / city / salary + candidate name + `XX年应届生` or `实习生` (e.g. `量化研究员实习-上海-500元_天-张三-2026年应届生.pdf`).
- The file body (after opening) contains multiple of: job title, candidate name, `应届生` / `实习生`, an email address, a phone number, school / education.
- The message context clearly frames the file as a resume (HR says "这是简历" / "新候选人" / "请入库" alongside the attachment).

If none of the above holds, do **not** route to `cmd_ingest_cv.py`. Ask HR to clarify, or fall through to generic file handling.

**Step 2 — Extract the file path from the Hermes Gateway message** (priority-ordered):

| Priority | Inbound message shape | Args to pass |
|---|---|---|
| 0 | `[The user sent a document: 'xxx.pdf'. The file is saved at: /.../xxx.pdf ...]` | `--file-path "/.../xxx.pdf" --filename "xxx.pdf"` |
| 1 | `[media attached: <absolute-path>.pdf]` | `--file-path "<absolute-path>.pdf" --filename "<basename>"` |
| 2 | Reply / quote referring to a message with a `file_key` | First look in `<workspace_root>/data/media/inbound/` for a local file by key/name; if found use `--file-path`; otherwise `--file-key <key>` |

The path produced by the Hermes message is already absolute. Pass it through **verbatim**; do not rewrite with `<workspace_root>`.

**Step 3 — Run `cmd_ingest_cv.py`** (auto-triggered, see §2.1.5). Forward its output to HR verbatim, then wait for HR's reply before any further action (§9.3 describes the two possible output shapes).

### 4.2 Interview operations

| Intent | Command |
|---|---|
| Schedule round 1 | `uv run python3 scripts/round1/cmd_round1_schedule.py --talent-id <id> --time "YYYY-MM-DD HH:MM"` |
| Boss finalizes an interview time (auto-detects round) | `uv run python3 scripts/common/cmd_finalize_interview_time.py --talent-id <id> [--round 1\|2] [--time "..."]` |
| Record result (unified for round 1 & round 2) | `uv run python3 scripts/interview/cmd_result.py --talent-id <id> --round 1\|2 --result pass\|pass_direct\|reject_keep\|reject_delete [--email ...] [--round2-time "..."]` |
| Round 1 passed but the exam email was sent manually (state-only transition, do NOT send email) | `uv run python3 scripts/interview/cmd_result.py --talent-id <id> --round 1 --result pass --skip-email` |
| Reschedule an interview | `uv run python3 scripts/interview/cmd_reschedule.py --talent-id <id> --round 1\|2 --time "YYYY-MM-DD HH:MM"` |
| Defer an interview | `uv run python3 scripts/interview/cmd_defer.py --talent-id <id> --round 1\|2 [--reason "..."]` |
| Candidate requested reschedule (inbound) | `uv run python3 scripts/common/cmd_reschedule_request.py --talent-id <id> ...` |
| Candidate returned from overseas, resume scheduling | `uv run python3 scripts/common/cmd_wait_return_resume.py --talent-id <id>` |

Conditional-required args (from [CLI_REFERENCE.md](../skills/recruit-ops/docs/CLI_REFERENCE.md)):
- `cmd_result.py --round 1 --result pass` → `--email` is **required** (exam invite email body), unless `--skip-email` is also passed.
- `cmd_result.py --round 1 --result pass_direct` → `--round2-time` is **required**.
- `cmd_result.py --round 2 --result pass` → no extra args; routes to offer handoff.
- `--skip-email` only makes sense with `--round 1 --result pass` and means "operator already sent the exam email manually; only advance state".

### 4.3 Exam operations

| Intent | Command |
|---|---|
| Exam pass → schedule round 2 | `uv run python3 scripts/exam/cmd_exam_result.py --talent-id <id> --result pass --round2-time "YYYY-MM-DD HH:MM"` |
| Exam reject (keep in pool) | `uv run python3 scripts/exam/cmd_exam_result.py --talent-id <id> --result reject_keep` |
| Exam reject (remove from pool) | `uv run python3 scripts/exam/cmd_exam_result.py --talent-id <id> --result reject_delete` *(confirmation-required)* |
| Manual IMAP exam scan (fallback only) | `uv run python3 scripts/exam/daily_exam_review.py` |

Notes:
- `--round2-time` is **mandatory** when `--result pass`. The script rejects reusing old times.
- The daily exam review runs automatically via systemd/cron. Only invoke manually as a fallback when the user explicitly asks or when automation is suspected broken.

### 4.4 Queries

| Intent | Command |
|---|---|
| All candidates | `uv run python3 scripts/common/cmd_status.py --all` |
| One candidate's full detail | `uv run python3 scripts/common/cmd_status.py --talent-id <id>` |
| Keyword search | `uv run python3 scripts/common/cmd_search.py --query <keyword>` |
| Active / in-progress candidates only | `uv run python3 scripts/common/cmd_search.py --all-active` |
| Today's interviews | `uv run python3 scripts/common/cmd_today_interviews.py` |
| Specific day | `uv run python3 scripts/common/cmd_today_interviews.py --date YYYY-MM-DD` |
| Confirmed-only schedule | `uv run python3 scripts/common/cmd_today_interviews.py --confirmed-only` |
| Pending interview-result reminders | `uv run python3 scripts/common/cmd_interview_reminder.py` |

---

## 5. Query Rules

Match the user's intent to the narrowest canonical command. Do not compose a derived view from a broader command.

| User intent | Command | Do not |
|---|---|---|
| "Show me everyone" / "所有候选人" | `cmd_status.py --all` | Regroup into custom buckets. Change counts. Infer active/inactive from stage text. |
| "Who's still in progress?" / "活跃候选人" | `cmd_search.py --all-active` | Run `cmd_status.py --all` and manually guess which stages are active. |
| "When is X's interview?" | `cmd_search.py --query <name>` → if unique, `cmd_status.py --talent-id <id>` | Skip the search step and guess the ID. |
| "Any interviews today/tomorrow/on <date>?" | `cmd_today_interviews.py [--date ...]` | Reconstruct from `--all-active`. |

**Grouping guardrail for `cmd_status.py --all`**:

- Default behavior: return the flat list exactly as the command returned it.
- If the user explicitly asks for a grouped summary, group only by the **exact current stage label**, not by a hand-invented broader bucket.
- Never place any `*_DONE_*` stage under an in-progress bucket such as `一面阶段`, `二面阶段`, or `笔试阶段`.
- Concretely: `ROUND2_DONE_REJECT_KEEP / 二面未通过（保留）` is **not** part of `二面阶段`; `ROUND1_DONE_REJECT_KEEP / 一面未通过（保留）` is **not** part of `一面阶段`.
- If unsure whether a stage is in-progress or terminal, quote the exact stage label and stop. Do not improvise a bucket.

---

## 6. Stage Interpretation

Canonical source: `scripts/lib/core_state.py` (`STAGE_LABELS`). Mirror below for quick reference.

### 6.1 Active stages

| Stage | Label | Meaning |
|---|---|---|
| `NEW` | 新建 | Candidate created, no action taken yet |
| `ROUND1_SCHEDULING` | 一面排期中 | Round 1 invite sent, awaiting candidate confirmation |
| `ROUND1_SCHEDULED` | 一面已安排 | Round 1 confirmed |
| `EXAM_SENT` | 笔试已发送 | Exam sent, awaiting submission |
| `EXAM_REVIEWED` | 笔试已审阅 | Exam reviewed, awaiting next step |
| `WAIT_RETURN` | 待回国后再约 | Paused until candidate is back |
| `ROUND2_SCHEDULING` | 二面排期中 | Round 2 being coordinated |
| `ROUND2_SCHEDULED` | 二面已确认 | Round 2 confirmed |
| `OFFER_HANDOFF` | 等待发放 Offer | Passed to offer handling |

### 6.2 Terminal / done stages — **not in-progress**

These are finished states. Never present them as "still in that stage".

| Stage | Label | What it means |
|---|---|---|
| `ROUND1_DONE_PASS` | 一面通过 | Round 1 finished, passed; typically moved on to exam |
| `ROUND1_DONE_REJECT_KEEP` | 一面未通过（保留） | Round 1 failed, kept in pool |
| `ROUND1_DONE_REJECT_DELETE` | 一面未通过（移除） | Round 1 failed, removed from pool |
| `ROUND2_DONE_PENDING` | 二面结束待定 | Round 2 finished, decision pending |
| `ROUND2_DONE_PASS` | 二面通过 | Round 2 passed |
| `ROUND2_DONE_REJECT_KEEP` | 二面未通过（保留） | Round 2 failed, kept in pool |
| `ROUND2_DONE_REJECT_DELETE` | 二面未通过（移除） | Round 2 failed, removed from pool |

**Rule**: a candidate in `ROUND2_DONE_REJECT_KEEP` is **not** "still in round 2". They are finished and retained in the talent pool.

---

## 7. Mutation Preconditions

Before running any command in §2.2 or §2.3, verify:

1. A **unique** `talent-id` has been resolved (via §3).
2. Required conditional args are present (see §4.2 notes, §4.3 notes).
3. Times are in `YYYY-MM-DD HH:MM`, Asia/Shanghai (+08:00), not in the past, and not a rehash of a previously-rejected time.
4. The candidate's current stage allows the operation.
5. **The §2.2.1 confirmation protocol has been completed** — the resolved command was shown to the user in a prior turn and the user replied with an explicit affirmative (for §2.3 commands, the affirmative must also name the destructive action).

### 7.1 Stage-gate policy — code is canonical

The CLI itself enforces stage-transition rules. The skill does **not** reimplement the state machine. The rough guide below is a **hint set**, not an authoritative specification:

| Operation | Typical allowed stages (code wins) |
|---|---|
| Round 1 schedule | `NEW`, `ROUND1_SCHEDULING` |
| Round 1 result | `ROUND1_SCHEDULING`, `ROUND1_SCHEDULED` |
| Exam result | `EXAM_SENT`, `EXAM_REVIEWED` |
| Round 2 schedule / finalize | `EXAM_REVIEWED`, or after `pass_direct` from round 1 |
| Round 2 result | `ROUND2_SCHEDULING`, `ROUND2_SCHEDULED` |

Rules:
- When unsure whether the current stage allows the action, run `common/cmd_status.py --talent-id <id>` first and let the CLI's own validator reject invalid transitions.
- If code and this table disagree, code is canonical. Update the skill, not the other way around.
- Never invent a "status-only" transition outside of what the CLI explicitly supports (e.g. `--skip-email` in §4.2 is a supported status-only transition; there is no equivalent for arbitrary stages).

If any precondition fails, **do not run the command**. Echo the CLI diagnostic verbatim and suggest the next valid step (typically "先 `cmd_status.py --talent-id ...` 看当前阶段").

---

## 8. Failure Handling

Classify every non-zero exit or error into one of four buckets and respond accordingly.

| Class | Detection | Correct next action |
|---|---|---|
| **Not found** | `ERROR: 未找到候选人` / empty search result | Ask the user to clarify identity. Offer a `cmd_search.py` query. |
| **Ambiguous** | Search returns >1 candidate | List the matches (name + `talent-id` + stage) and ask the user to choose. Do not act. |
| **Invalid state / args** | `ERROR: 当前阶段不允许` / `argparse` usage error / constraint violations | Report the error verbatim. Suggest the correct command or the query that would reveal current state. Never retry blindly with altered args. |
| **Infra / transient** | DB connection error, Feishu API error, IMAP error, traceback with network keywords | Report the failure as infrastructure-level. Do **not** suggest retrying a business command; suggest checking config / connectivity instead. |

Never wrap a failure in an optimistic summary. If the command failed, the user's state did not advance.

---

## 9. Result Presentation

Preserve command semantics. Prefer verbatim command wording over paraphrase when precision matters.

**Universal rule**: only include fields the command actually returned. Never fill in "last action", "next action", "pending action", or any derived signal that the command did not output. If a caller asks for something the command did not return, run a more specific command (e.g. `cmd_status.py --talent-id <id>`) — do not infer.

### 9.1 Candidate list

For each row in a list response, include exactly the fields the query command returned. At minimum those are:
- Display name
- `talent-id`
- Exact stage label (bilingual acceptable: `ROUND2_DONE_REJECT_KEEP / 二面未通过（保留）`)

Additional fields (scheduled time, confirmation status, email, etc.) are included **only if** the command returned them for that row. Do not group by custom buckets unless the user explicitly asked.

If the user explicitly asks for grouping, the safe order is:

1. group by exact stage label;
2. only merge labels into a broader bucket when the command itself already returned that bucket, or the skill explicitly defines the bucket as lossless;
3. for any `*_DONE_*` stage, prefer a terminal bucket such as `已结束`, `保留人才池`, or `其他状态` — never an in-progress round bucket.

### 9.2 Single candidate detail

Reply with the fields returned by `cmd_status.py --talent-id <id>`. Do not synthesize a "next action" from the stage. If the user asked "what's next", either:
- quote the stage and let them decide, or
- offer the candidate's stage-matching commands from §4 as options to choose from.

For PII, see §10.

### 9.2.1 Time phrasing guardrail

Do **not** add any unchecked natural-language calendar interpretation to a time field returned by a command.

This includes:

- `本周日`
- `下周一`
- `明天下午`
- `周几`
- and any similar natural-language date phrasing

These phrases are allowed **only if**:

1. the command itself already returned that phrasing or field, or
2. the agent first verified it with a deterministic date calculation / real calendar lookup

Otherwise, repeat only the raw returned timestamp, e.g.:

- `面试时间：2026-04-20 09:30`

Not allowed:

- `面试时间：2026-04-20 09:30（本周日）`
- `面试时间：2026-04-20 09:30（下周一上午）`
- `面试时间：明天下午 09:30`

If a verified natural-language rendering is added, keep the original timestamp visible as the primary source of truth.

### 9.3 Mutation result

After a successful mutating command, echo back only what the command itself reported: candidate name, `talent-id`, the new stage as printed by the command, and any scheduling / notification confirmations the command explicitly logged.

### 9.4 CV ingest preview (the two branches)

`cmd_ingest_cv.py` (§2.1.5) emits one of two structured preview shapes. **Forward the preview body to HR verbatim** and treat the embedded payloads as proposals for the next §2.2 mutating command.

**Branch A — existing candidate (DB match)**: the preview contains a full-field diff table and two payload markers:

- `[OC_CMD_ON_CONFIRM_UPDATE]` — the `cmd_attach_cv.py ... --confirm --field key=value ...` command that will write all detected changes and archive the CV.
- `[OC_CMD_ON_CONFIRM_ARCHIVE]` — the `cmd_attach_cv.py ... --confirm` command that will archive the CV without changing any fields.

HR's reply selects one of:
- **"确认更新"** → execute the `UPDATE` payload.
- **"仅存档"** → execute the `ARCHIVE` payload.
- **"只更新X / Y / ..."** → take the `UPDATE` payload, remove the `--field` args that HR excluded, execute the trimmed command.
- **"把X改成..."** → take the `UPDATE` payload, replace the value of that `--field`, re-show the preview, wait again.
- **"忽略"** → do nothing.

**Branch B — new candidate (no match)**: the preview contains the parsed fields and two markers:

- `[OC_CMD_ON_CONFIRM]` — the `cmd_new_candidate.py --template ...` command ready to run if HR confirms the candidate is genuinely new and the starting stage is `NEW`.
- `[OC_NOTE]` — a note prompting HR to confirm both the fields and the intended starting stage.

HR's reply selects one of:
- **"修正 X=..."** → edit the relevant field, **re-show the preview**, wait again. Never execute until HR confirms after seeing the edited preview.
- **"确认 + 阶段 NEW"** (or the equivalent) → execute the `[OC_CMD_ON_CONFIRM]` payload.
- **"确认 + 阶段 <其他>"** (e.g. `ROUND1_SCHEDULED`, `EXAM_SENT`) → switch to `intake/cmd_import_candidate.py --template ... --stage <stage>` and execute.
- **"不是候选人 / 忽略"** → do nothing.

Either branch: the downstream `cmd_attach_cv.py` / `cmd_new_candidate.py` / `cmd_import_candidate.py` execution still goes through the §2.2.1 protocol — HR's explicit reply to the preview **is** that confirmation; do not ask for a second one unless HR changes any parameter.

---

## 10. Privacy / PII

Recruitment data is personal data. Apply minimum-necessary disclosure.

- Default list views: `name + talent-id + stage [+ next action]`. No email, phone, or WeChat unless the user specifically asked.
- When a single candidate's contact is needed (e.g. for the boss to write directly), disclose **one** channel at a time, preferably the one implied by the task.
- Never paste full CV text, ID numbers, or bank-adjacent fields into a reply unless the user explicitly requested them.
- For search results, one-line-per-candidate is the default. Expand only on follow-up.

---

## 11. Anti-Patterns

Do not do any of the following:

- Ask the user for Feishu links, Bitable links, or spreadsheet links. The repo owns the workflow.
- Regroup command output into custom buckets that the command did not return.
- Present a `*_DONE_*` stage as "still in that stage".
- Put `ROUND2_DONE_REJECT_KEEP`, `ROUND2_DONE_PENDING`, or `ROUND2_DONE_PASS` under a bucket named `二面阶段`.
- Put `ROUND1_DONE_REJECT_KEEP`, `ROUND1_DONE_REJECT_DELETE`, or `ROUND1_DONE_PASS` under a bucket named `一面阶段`.
- Append `周几` / `本周几` / `明天` / `下周一` style calendar wording to a returned timestamp without a deterministic date check.
- Use `cmd_parse_cv.py` — it is deprecated. Use `cmd_ingest_cv.py`.
- Hardcode local absolute paths into the skill or the suggested command.
- Mix `uv run python3 scripts/...` with bare `python3 <group>/...` in the same reply.
- Run a mutating command without a resolved unique `talent-id`.
- **Run any §2.2 mutating command without first presenting the resolved command and receiving an explicit user confirmation (§2.2.1). This applies to every mutating command, no exceptions.**
- Treat a user's original request as pre-authorization; always present the resolved command and wait for a fresh affirmative before executing.
- Batch multiple mutating commands under a single confirmation. One confirmation = one command.
- Run a §2.3 destructive command on a generic "yes"; require the affirmative to name the destructive action.
- Choose `reject_delete` without explicit same-turn user confirmation. The only permissible default for "reject" is `reject_keep` (see §2.3).
- Wrap a command failure in an optimistic "done" phrasing.
- Trigger `cmd_ingest_cv.py` on a file that does not match §4.1.1 detection rules. Ask HR if uncertain.
- Paraphrase or summarize `cmd_ingest_cv.py` output. Forward the preview body verbatim to HR.
- Execute both `[OC_CMD_ON_CONFIRM_UPDATE]` and `[OC_CMD_ON_CONFIRM_ARCHIVE]` from the same preview — they are mutually exclusive branches; run exactly one based on HR's reply (§9.4).
- Rewrite the absolute file path from a Hermes Gateway message into a `<workspace_root>`-relative form. Pass the path through verbatim.
- Pass `--to hr` on `cmd_send_cv.py` when the Boss just said "看简历" or "把简历发过来". Default (no `--to`) sends to Boss, which is correct.

---

## 12. Routing Examples (eval cases)

These are canonical input → route mappings. Use them as sanity checks and as regression test anchors.

Read-only queries are shown as a single step. Mutating commands are shown as two steps: **(a) propose** the resolved command to the user, **(b) execute** on explicit confirmation (§2.2.1). Never collapse (a) and (b) into one turn.

| User says | Correct route |
|---|---|
| "今天有谁有面试" | read-only: `cmd_today_interviews.py` |
| "看看所有候选人" | read-only: `cmd_status.py --all` |
| "还在进行中的候选人" | read-only: `cmd_search.py --all-active` |
| "查一下张三现在到哪一步了" | read-only: `cmd_search.py --query 张三` → if unique, `cmd_status.py --talent-id <id>` |
| HR sends PDF named `量化研究员-上海-500元_天-李四-2026年应届生.pdf` | §4.1.1 matches → auto-triggered `cmd_ingest_cv.py --file-path <path> --filename <name>` → forward preview verbatim → wait for HR reply → execute selected `[OC_CMD_ON_CONFIRM*]` payload per §9.4. |
| HR sends a generic PDF `会议纪要.pdf` with no candidate context | §4.1.1 does not match → **do not** run `cmd_ingest_cv.py`; ask HR to confirm it's a CV, or fall through to generic file handling. |
| "冯屹哲笔试已发，只改状态" | resolve id → **propose** `interview/cmd_result.py --talent-id <id> --round 1 --result pass --skip-email` → wait for confirm → execute. Only status-only transition supported; do not invent others. |
| "李陆斌一面被拒，保留人才库" | resolve id → **propose** `interview/cmd_result.py --talent-id t_vxunkj --round 1 --result reject_keep` → wait for confirm → execute. |
| "把张三一面改到明天下午三点" | resolve id → resolve time to `YYYY-MM-DD HH:MM` Asia/Shanghai → **propose** `interview/cmd_reschedule.py --talent-id <id> --round 1 --time "<resolved>"` → wait for confirm → execute. |
| "发简历给老板 张三" | resolve unique candidate → **propose** `intake/cmd_send_cv.py --name 张三` (or `--talent-id`) → wait for confirm → execute. |
| "删掉这个候选人" | §2.3 destructive: resolve id → **propose** `common/cmd_remove.py --talent-id <id>` with candidate name and current stage → wait for a confirmation that names the destructive action (e.g. "是，删除 t_xxx") → execute. Generic "yes" is not sufficient. |
| "他二面通过" with no prior unique candidate in turn | **do not act**; ask who "他" refers to. Never propose or execute. |

---

## 13. Runtime

### 13.1 Where this skill runs

This skill is loaded by **Hermes Gateway** and is not an independent process. It reads Feishu messages over WebSocket and routes them to the `recruit-ops` CLI in the workspace. It assumes the runtime is already configured; it does not install dependencies, create the DB, or set env vars — if any of those are missing, report the failure (§8 Infra class) and stop.

### 13.2 Hard prerequisites (assumed present)

- **Python**: 3.10+
- **Dependencies installed**: either `uv sync` has been run under `<workspace_root>/skills/recruit-ops/`, or the project's `.venv` is present. `uv run python3` and `<workspace_root>/skills/recruit-ops/.venv/bin/python3` are equivalent — pick one and use it consistently in a single reply.
- **Database**: a configured `talent-db` is reachable; `scripts/lib/talent_db.py` resolves connection from `scripts/lib/talent-db-config.json` or equivalent env vars.
- **Feishu identities**: `FEISHU_BOSS_OPEN_ID` and `FEISHU_HR_OPEN_ID` env vars (or openclaw account fields `ownerOpenId` / `hrOpenId`) are set.
- **Cron / systemd**: daily scanners (`exam/daily_exam_review.py`, `cron_runner.py`) are scheduled externally and require `PYTHONPATH=scripts` when invoked outside `uv run`.

### 13.3 Open-source / plug-and-play target

For future distribution, the skill body stays portable by design:

- No machine-local absolute paths in the body; use `<workspace_root>` placeholders.
- The only place with a concrete local path is the **Local deployment mapping** block at the top of this file — remove or re-map it per deployment.
- Hermes Gateway loading path (`~/.hermes/skills/openclaw-imports/recruit-ops/`) is also deployment-specific and lives only in that top block.
- Point at CLI commands, never at operator shell aliases or private scripts.
- Deployment-specific overrides belong in a **separate** ops note, not in this skill.

---

## 14. Summary Checklist

Before replying, confirm:

- [ ] Intent is mapped to a real CLI command listed in §4.
- [ ] The command's safety class (§2) has been respected: read-only run freely; auto-triggered previews (§2.1.5) only on §4.1.1 CV matches; **every mutating command was presented to the user first and executed only after an explicit same-turn affirmative (§2.2.1)**; §2.3 destructive commands received an affirmative that named the destructive action.
- [ ] For CV intake: `cmd_ingest_cv.py` was auto-triggered only on a §4.1.1 match; its output was forwarded verbatim; exactly one of `UPDATE` / `ARCHIVE` / `CONFIRM` was executed based on HR's reply (§9.4).
- [ ] A unique `talent-id` has been resolved for any mutating command.
- [ ] One confirmation authorized exactly one command (no batching).
- [ ] Any `--time` is in `YYYY-MM-DD HH:MM` Asia/Shanghai (+08:00) and has been echoed back to the user.
- [ ] Stage labels used in the reply match §6 (and therefore `core_state.py`).
- [ ] Query requests use the canonical query command, not a derived view.
- [ ] No `*_DONE_*` stage is described as in-progress.
- [ ] No field was added to the reply that the command did not explicitly return (§9).
- [ ] PII is disclosed at minimum-necessary level (§10).
- [ ] Command form is `uv run python3 scripts/...` throughout the reply.

---

## Appendix: Primary reference

- [skills/recruit-ops/docs/CLI_REFERENCE.md](../skills/recruit-ops/docs/CLI_REFERENCE.md) — full CLI reference with argument tables, conditional-required args, cron notes, and per-command examples.
- `skills/recruit-ops/scripts/lib/core_state.py` — canonical `STAGE_LABELS`.
