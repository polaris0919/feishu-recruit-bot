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


def recruit_env() -> str:
    """v3.8.5: 当前环境标签，用于多环境配置隔离。

    取值：
      - "prod"     默认。读 config/<filename>.json。
      - "dev"      读 config/<filename>.dev.json，优先级最高。
      - "staging"  读 config/<filename>.staging.json，优先级最高。
      - 任意其它   读 config/<filename>.<env>.json。

    配套：env-specific 文件不存在时, fallback 到通用 config/<filename>.json
    （避免新建 dev 时强制把 5 个 json 都先翻一遍）。生产部署不设
    RECRUIT_ENV → 自动走 prod 路径, 行为与 v3.8.5 之前完全一致。
    """
    return (os.environ.get("RECRUIT_ENV") or "prod").strip().lower() or "prod"


def _env_suffixed(filename: str, env: str) -> str:
    """把 'talent-db-config.json' 变成 'talent-db-config.dev.json'。
    扩展名（.json / .yaml 等）保留, env 标签插在主名末尾。
    """
    if not filename or env in ("prod", ""):
        return filename
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        return "{}.{}".format(filename, env)
    return "{}.{}.{}".format(stem, env, ext)


def config_candidates(filename: str) -> List[Path]:
    """v3.8.5: 多环境查找顺序（高优先级在前）：

      1. <scripts>/<env-suffixed-filename>     (历史兼容：scripts 根目录的 env 覆盖)
      2. <scripts>/<filename>                  (历史兼容：scripts 根目录通用)
      3. <config_dir>/<env-suffixed-filename>  (主路径：config/ 下 env 覆盖)
      4. <config_dir>/<filename>               (主路径：config/ 下通用 = 旧默认)

    prod / 空 env 时 1=2、3=4 自动去重, 行为完全等价于 v3.8.5 之前。
    """
    env = recruit_env()
    out: List[Path] = []
    candidates_raw = [
        SCRIPTS_DIR / _env_suffixed(filename, env),
        SCRIPTS_DIR / filename,
        config_dir() / _env_suffixed(filename, env),
        config_dir() / filename,
    ]
    seen = set()
    for p in candidates_raw:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def first_existing(paths: Iterable[Path]) -> Optional[Path]:
    for path in paths:
        if path.is_file():
            return path
    return None
