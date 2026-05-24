#!/usr/bin/env python3
"""Private runtime log helpers for sensitive recruiting automation output."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from lib.candidate_storage import data_root


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def runtime_log_dir() -> Path:
    path = data_root() / "logs"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(str(path), 0o700)
    except OSError:
        pass
    return path


def safe_label(raw: str, fallback: str = "log") -> str:
    value = _SAFE_NAME_RE.sub("_", str(raw or "").strip()).strip("._")
    return (value or fallback)[:80]


def private_log_path(prefix: str, suffix: str = ".log") -> Path:
    name = "{}_{}_{}{}".format(
        safe_label(prefix),
        int(time.time()),
        os.getpid(),
        suffix,
    )
    return runtime_log_dir() / name


def write_private_text(path: Path, text: str) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text or "")
    finally:
        try:
            os.chmod(str(path), 0o600)
        except OSError:
            pass


def append_private_log(name: str, line: str) -> None:
    path = runtime_log_dir() / safe_label(name)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(line if line.endswith("\n") else line + "\n")
    finally:
        try:
            os.chmod(str(path), 0o600)
        except OSError:
            pass
