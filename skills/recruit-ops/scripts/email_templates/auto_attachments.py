#!/usr/bin/env python3
"""email_templates/auto_attachments.py —— v3.5.10 模板默认附件注册表。

【动机】
  某些邮件模板（最典型的是 `onboarding_offer`）每次发都要带固定附件
  （实习协议 + 入职登记表）。让 agent 每次手动 `--attach` 极易漏发，所以
  在 `outbound.cmd_send` 里按模板名查这张表，自动追加。

【约定】
  - 注册表是模板名 → 相对 `data_root()` 的路径列表
  - 路径相对 `RECRUIT_DATA_ROOT`（默认 <RECRUIT_WORKSPACE>/data），
    HR 想换文件 / 换版本：直接把同名文件覆盖即可，不必改代码。
  - 文件 **必须存在**，否则 `auto_attachments_for()` 抛 RuntimeError。这是有意
    fail-fast：offer 邮件没带合同发出去比晚发几分钟严重得多。

【何处调用】
  outbound/cmd_send.py：在收完 `--attach` 之后、SMTP 发送之前调一次。
  自动追加的附件会标 `auto=true` 写入 `talent_emails.attachments` 元数据。

【加新条目】
  1. 把新文件塞进 `data/<sub>/<filename>`（保证 git-tracked，不在 .gitignore 里）
  2. 在下面 `_REGISTRY` 加一行
  3. 跑 `tests/test_auto_attachments.py`
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from lib.candidate_storage import data_root


# 相对 data_root() 的路径。文件名带版本号是有意为之 —— 换版本走 PR review，
# 防止悄悄换合同条款没人察觉（v3.5.9 老板拍板）。
_REGISTRY = {
    "onboarding_offer": [
        "onoffer_data/模板-示例科技实习协议-2026年4月版.docx",
        "onoffer_data/示例科技-实习生入职信息登记表-2026年版.docx",
    ],
}  # type: Dict[str, List[str]]


def list_registered_templates():
    # type: () -> List[str]
    """返回所有注册了默认附件的模板名（供 doc / 测试用）。"""
    return sorted(_REGISTRY.keys())


def auto_attachments_for(template_name):
    # type: (str) -> List[Path]
    """模板名 → 绝对路径列表。

    - 模板没注册 → 返回 []
    - 模板注册了但文件不存在 → 抛 RuntimeError（fail-fast）
    """
    rels = _REGISTRY.get(template_name)
    if not rels:
        return []
    root = data_root()
    out = []
    missing = []
    for rel in rels:
        p = root / rel
        if not p.is_file():
            missing.append(str(p))
        else:
            out.append(p)
    if missing:
        raise RuntimeError(
            "模板 {!r} 的默认附件文件缺失，拒绝发送：{}\n"
            "（请把文件放回 data/onoffer_data/ 或检查 RECRUIT_DATA_ROOT）".format(
                template_name, missing))
    return out
