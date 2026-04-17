#!/usr/bin/env python3
"""招聘工作区路径工具。"""

import os
from pathlib import Path
from typing import Iterable, List, Optional

_LIB_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = _LIB_DIR.parent
WORKSPACE_ROOT = Path(
    os.path.expanduser(
        os.environ.get("RECRUIT_WORKSPACE_ROOT", str(SCRIPTS_DIR.parent.parent.parent))
    )
).resolve()


def workspace_path(*parts: str) -> Path:
    return WORKSPACE_ROOT.joinpath(*parts)


def scripts_dir() -> str:
    return str(SCRIPTS_DIR)


def config_dir() -> Path:
    raw = os.environ.get("RECRUIT_CONFIG_DIR", str(workspace_path("config")))
    return Path(os.path.expanduser(raw))


def exam_archive_dir() -> Path:
    raw = os.environ.get("RECRUIT_EXAM_ARCHIVE_DIR", str(workspace_path("data", "exam_txt")))
    return Path(os.path.expanduser(raw))


def config_candidates(filename: str) -> List[Path]:
    return [SCRIPTS_DIR / filename, config_dir() / filename]


def first_existing(paths: Iterable[Path]) -> Optional[Path]:
    for path in paths:
        if path.is_file():
            return path
    return None
