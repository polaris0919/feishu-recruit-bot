#!/usr/bin/env python3
"""email_templates/auto_attachments.py —— v3.5.10 模板默认附件注册表。

【动机】
  某些邮件模板（最典型的是 `onboarding_offer`）每次发都要带固定附件
  （实习协议 + 入职登记表）。让 agent 每次手动 `--attach` 极易漏发，所以
  在 `outbound.cmd_send` 里按模板名查这张表，自动追加。

【约定】
  注册表 value 支持两种形态：
  1. List[str] —— 相对 `data_root()` 的固定路径列表（典型：onboarding_offer）。
     文件 **必须存在**，否则 `auto_attachments_for()` 抛 RuntimeError，fail-fast：
     offer 邮件没带合同发出去比晚发几分钟严重得多。
     路径相对 `RECRUIT_DATA_ROOT`（默认 /home/admin/recruit-workspace/data），
     HR 想换文件 / 换版本：直接把同名文件覆盖即可，不必改代码。

  2. Callable[[], List[Path]] —— 动态 resolver，返回绝对路径列表（典型：exam_invite）。
     用于"题目可能放在多个候选目录、文件名也可能换"的场景（HR 直接 cp 题包到
     data/exam_txt/ 即可，不必改代码）。resolver 自己负责候选探测；任一返回
     非空列表都视为成功。返回空列表会被本函数转成 RuntimeError，fail-fast。

【何处调用】
  outbound/cmd_send.py：在收完 `--attach` 之后、SMTP 发送之前调一次。
  interview/cmd_result.py：发笔试邀请前也调一次（exam_invite resolver）。
  自动追加的附件会标 `auto=true` 写入 `talent_emails.attachments` 元数据。

【加新条目】
  1. 把新文件塞进 `data/<sub>/<filename>`（保证 git-tracked，不在 .gitignore 里）
  2. 在下面 `_REGISTRY` 加一行（静态路径用 list，动态探测用 callable）
  3. 跑 `tests/test_auto_attachments.py`
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Dict, List, Union

from lib.candidate_storage import data_root


_AttachmentSpec = Union[List[str], Callable[[], List[Path]]]


def _resolve_exam_invite_attachments():
    # type: () -> List[Path]
    """笔试邀请的题包探测（v3.8.4 从 interview/cmd_result._get_exam_attachments 移入）。

    历史上题包文件名 / 格式不固定（.tar.gz / .zip / .tar 都见过），
    所以走"多候选挑第一个能用的"策略，而不是 onboarding_offer 那种固定路径。
    """
    from lib.recruit_paths import exam_archive_dir
    archive = Path(exam_archive_dir())
    exam_files = (Path(__file__).resolve().parent.parent.parent / "exam_files").resolve()
    candidates = [
        archive / "笔试题.tar.gz",
        archive / "笔试题.zip",
        exam_files / "exam_package.zip",
        archive / "笔试题.tar",
    ]
    for p in candidates:
        if p.is_file() and p.stat().st_size > 0:
            return [p]
    return []


_REGISTRY = {
    "onboarding_offer": [
        "onoffer_data/致邃实习协议-2026年4月版.docx",
        "onoffer_data/致邃投资-实习生入职信息登记表-2026年版.docx",
    ],
    "exam_invite": _resolve_exam_invite_attachments,
}  # type: Dict[str, _AttachmentSpec]


def list_registered_templates():
    # type: () -> List[str]
    """返回所有注册了默认附件的模板名（供 doc / 测试用）。"""
    return sorted(_REGISTRY.keys())


def auto_attachments_for(template_name):
    # type: (str) -> List[Path]
    """模板名 → 绝对路径列表。

    - 模板没注册 → 返回 []
    - 模板注册了但文件不存在 / resolver 返回空 → 抛 RuntimeError（fail-fast）
    """
    spec = _REGISTRY.get(template_name)
    if spec is None:
        return []

    if callable(spec):
        out = list(spec())
        if not out:
            raise RuntimeError(
                "模板 {!r} 的默认附件未找到（resolver 返回空），拒绝发送。\n"
                "（请检查题包是否已放进 data/exam_txt/笔试题.tar.gz 等候选路径）".format(
                    template_name))
        bad = [str(p) for p in out if not p.is_file()]
        if bad:
            raise RuntimeError(
                "模板 {!r} 的 resolver 返回了不存在的文件：{}".format(template_name, bad))
        return out

    root = data_root()
    out = []
    missing = []
    for rel in spec:
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
