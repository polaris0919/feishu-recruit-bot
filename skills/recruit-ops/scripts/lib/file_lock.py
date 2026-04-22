#!/usr/bin/env python3
"""文件锁 + 原子写工具。

用于多进程环境下安全访问 `data/followup_pending/*.json` 等共享 JSON 文件。

设计选择：
- 使用 fcntl.flock（Linux/Mac），Windows 不支持时降级为 no-op + warning。
- 原子写：先写 `<path>.tmp.<pid>` 再 `os.replace`，避免读到半截文件。
- 锁的范围放在专门的 `.lock` 文件上，避免误读时把锁文件本身打开成数据。
"""
from __future__ import print_function

import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterator, Optional, Union

try:
    import fcntl  # type: ignore
    _HAS_FCNTL = True
except ImportError:  # pragma: no cover - non-POSIX fallback
    _HAS_FCNTL = False


PathLike = Union[str, Path]


def _lock_path(target):
    # type: (PathLike) -> Path
    target = Path(target)
    return target.with_suffix(target.suffix + ".lock")


@contextlib.contextmanager
def file_lock(target, exclusive=True, timeout=None):
    # type: (PathLike, bool, Optional[float]) -> Iterator[None]
    """对 `<target>.lock` 加 fcntl 锁。

    Args:
        target: 要保护的实际数据文件路径。
        exclusive: True 走 LOCK_EX，否则 LOCK_SH。
        timeout: 仅记录用，flock 调用本身是阻塞的（LOCK_NB 不在此处用）。

    在没有 fcntl 的平台上退化为 no-op，并在 stderr 打一次 WARN。
    """
    if not _HAS_FCNTL:
        if not getattr(file_lock, "_warned", False):
            print("[file_lock] fcntl 不可用，文件锁退化为 no-op（仅在非 POSIX 平台预期）",
                  file=sys.stderr)
            file_lock._warned = True  # type: ignore[attr-defined]
        yield
        return

    lock_p = _lock_path(target)
    lock_p.parent.mkdir(parents=True, exist_ok=True)
    flag = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    fd = os.open(str(lock_p), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, flag)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def atomic_write_json(path, data, ensure_ascii=False, indent=2, sort_keys=True):
    # type: (PathLike, Any, bool, int, bool) -> Path
    """原子写入 JSON：先写到 `.tmp.<pid>`，再 os.replace。

    保证读端在任何时候要么看到旧内容、要么看到完整新内容，不会看到半截。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp.{}".format(os.getpid()))
    try:
        with open(str(tmp), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=ensure_ascii, indent=indent, sort_keys=sort_keys)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(str(tmp), str(path))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return path


def atomic_read_json(path):
    # type: (PathLike) -> Optional[Any]
    """加共享锁读取 JSON，失败返回 None。"""
    path = Path(path)
    if not path.is_file():
        return None
    with file_lock(path, exclusive=False):
        try:
            with open(str(path), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print("[file_lock] atomic_read_json 失败 {}: {}".format(path, e), file=sys.stderr)
            return None
