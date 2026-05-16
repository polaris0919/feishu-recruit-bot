"""lib/db/ —— talent_db.py 拆分后的内部组织（B1, v3.8.7 起）。

═══════════════════════════════════════════════════════════════════════════════
为什么有这个包
═══════════════════════════════════════════════════════════════════════════════
- 历史上 `lib/talent_db.py` 1546 行做了 4 件事:
  连接 / 候选人 CRUD / 审计事件 / talent_emails 表。
- 全部 23 个调用者都用 `from lib import talent_db as _tdb` 这一行 import,
  测试基建 (`tests/helpers._InMemoryTdb`) 也假设这个模块是单点。
- 一次性 4-way 拆分会破坏 23 个 import 与 200+ 测试用例的 mock 假设,
  收益与风险严重不对称。

═══════════════════════════════════════════════════════════════════════════════
策略: 渐进式拆, lib/talent_db.py 仍是公开 facade
═══════════════════════════════════════════════════════════════════════════════
- 把"逻辑独立的内部子层"挪进 `lib/db/<sub>.py`。
- `lib/talent_db.py` 不再实现这部分, 改成从 `lib.db.<sub>` re-export,
  保证 `from lib import talent_db as _tdb; _tdb._update(...)` 仍可用。
- 测试 mocker 替换 `sys.modules["lib.talent_db"]` 时, 这层 re-export
  跟着被替换, 不会引入新泄漏面。

═══════════════════════════════════════════════════════════════════════════════
当前已拆分
═══════════════════════════════════════════════════════════════════════════════
- `lib/db/connection.py` —— 连接 + 低层 _query/_update + DBWriteError

待拆 (评估后再做, 见 docs/PROJECT_OVERVIEW §5.8):
- `lib/db/talents.py` —— 候选人 CRUD + state 加载/同步
- `lib/db/events.py`  —— save_audit_event
- `lib/db/emails.py`  —— talent_emails 表全部读写
"""
