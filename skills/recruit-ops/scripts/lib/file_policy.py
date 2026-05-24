#!/usr/bin/env python3
"""Local file safety policy for outbound attachments and Feishu file sends."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional

from lib.candidate_storage import data_root
from lib.recruit_paths import workspace_path


_SENSITIVE_FILE_NAMES = {
    ".env",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "known_hosts",
    "authorized_keys",
}


class FilePolicyError(ValueError):
    pass


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def allowed_send_roots() -> List[Path]:
    """Roots that may be sent without an extra manual override."""
    root = _resolve(data_root())
    return [
        root / "candidate_cv",
        root / "exam_submissions",
        root / "exam_package",
        root / "onoffer_data",
        root / "candidates",
        _resolve(workspace_path("skills", "recruit-ops", "exam_files")),
    ]


def sensitive_roots() -> List[Path]:
    return [
        _resolve(workspace_path("config")),
        _resolve(Path.home() / ".ssh"),
    ]


def _is_sensitive(path: Path) -> bool:
    if path.name in _SENSITIVE_FILE_NAMES:
        return True
    for root in sensitive_roots():
        if _is_under(path, root):
            return True
    return False


def validate_sendable_file(path: str,
                           allow_unsafe: bool = False,
                           confirm_path: Optional[str] = None,
                           allowed_roots: Optional[Iterable[Path]] = None) -> Path:
    """Validate that a local file is safe to send outside this host.

    Normal operation is restricted to candidate artifacts and explicitly
    registered recruiting assets. Non-sensitive files outside those roots can
    still be sent, but only with an explicit confirmation equal to the resolved
    path. Secret/config/SSH paths are never allowed through this helper.
    """
    p = _resolve(Path(path))
    if not p.is_file():
        raise FilePolicyError("文件不存在: {}".format(p))

    if _is_sensitive(p):
        raise FilePolicyError("拒绝发送敏感路径: {}".format(p))

    roots = [_resolve(Path(r)) for r in (allowed_roots or allowed_send_roots())]
    if any(_is_under(p, root) for root in roots):
        return p

    confirmed = _resolve(Path(confirm_path)) if confirm_path else None
    if allow_unsafe and confirmed == p:
        return p

    raise FilePolicyError(
        "拒绝发送非白名单路径: {}。允许目录: {}。如确需发送，需显式传入确认参数。".format(
            p, ", ".join(str(r) for r in roots)
        )
    )
