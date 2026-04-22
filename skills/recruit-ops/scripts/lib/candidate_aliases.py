#!/usr/bin/env python3
"""lib/candidate_aliases.py —— v3.5.9 候选人姓名软链层。

【目标】
  HR 想在文件管理器里按"候选人姓名"浏览资料目录，但 talent_id 是整套系统的主键，
  不能改动 data/candidates/<tid>/ 这条规范路径，否则 DB 里的 cv_path /
  attachments[].path 全部断链。

  折中方案：在 data/candidates/by_name/<name>__<tid>/ 维护一组只读 symlink
  指向 ../../<tid>/。HR 浏览友好，DB / 代码全部不动。

【目录约定】
  data/candidates/                            ← 真目录（talent_id key）
    t_demo02/cv/...
    t_demo02/exam_answer/em_xxx/...
    by_name/                                  ← 全自动维护，HR 不要手编
      候选人H__t_demo02  →  symlink ../t_demo02
      候选人H__t_xxx     →  symlink ../t_xxx       （重名时用 tid 区分）
      未命名__t_yyy     →  symlink ../t_yyy       （DB 里没姓名时的 placeholder）

【纯函数 + 一组写入入口】
  - sanitized_name(name)         姓名→文件名安全字符串（去 / \\ NUL，多空格压成 1）
  - alias_dir_for(name, tid)     → Path（不 mkdir，仅算路径）
  - by_name_root()               → data/candidates/by_name/

  - rebuild_alias_for(tid, name) 幂等：如果已有正确 symlink 跳过；如果指向错则
                                  删旧建新；姓名变了就把旧 alias remove 再建新。
  - remove_alias_for(tid)        删 by_name 下所有指向该 tid 的软链。
  - rebuild_all_aliases()        全量重建：扫 talents 表，对每个 talent
                                  rebuild_alias_for；同时清理所有指向不存在
                                  目录 / 不在 DB 里的 dangling alias。

【何时调用】
  - cmd_new_candidate 收尾：rebuild_alias_for(tid, name)
  - cmd_attach_cv 收尾：rebuild_alias_for(tid, name)（万一姓名是 ingest 时改的）
  - talent.cmd_update 改了 candidate_name：rebuild_alias_for(tid, new_name)
  - talent.cmd_delete：remove_alias_for(tid)（在搬走 candidate_dir 之前）
  - 兜底：tools/rebuild_candidate_aliases.py 一条命令重建所有

【warn-continue】
  alias 不是关键路径，任何失败 swallow + log + 继续，不阻塞主流程。
  没有 alias 也不影响系统功能（只影响 HR 浏览体验）。

【dry-run】
  side_effects_disabled() 时所有写入函数 no-op + 返回 dry_run=True。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from lib.candidate_storage import candidate_dir, candidates_root
from lib.side_effect_guard import side_effects_disabled


# ─── 路径计算 ─────────────────────────────────────────────────────────────────

_ALIAS_SUBDIR = "by_name"
_ALIAS_SEPARATOR = "__"  # 姓名 + tid 之间用双下划线，肉眼一目了然

# 文件名禁字（POSIX + Windows 兼容 + 控制字符）。Linux 其实只禁 / 和 NUL，
# 但留点余量将来同步到 NAS 时不踩坑。
_BAD_CHARS = re.compile(r"[\x00/\\:*?\"<>|\r\n\t]")
_WS_RUN = re.compile(r"\s+")


def by_name_root():
    # type: () -> Path
    """data/candidates/by_name/，不 mkdir，仅算路径。"""
    return candidates_root() / _ALIAS_SUBDIR


def sanitized_name(name):
    # type: (Optional[str]) -> str
    """姓名 → 文件名安全字符串。

    规则：
      - None / 空 / 纯空白 → '未命名'
      - 把 / \\ : * ? " < > | NUL CR LF TAB 全替换成 '_'
      - 多个连续空白压成单个空格
      - 首尾空白 / '.' 去掉（防 '...../etc/passwd' 之类）
      - 长度截到 80（Linux 单段 255，留余量给 __<tid> 后缀和未来扩展）
    """
    if name is None:
        return "未命名"
    s = str(name).strip()
    if not s:
        return "未命名"
    s = _BAD_CHARS.sub("_", s)
    s = _WS_RUN.sub(" ", s).strip()
    s = s.lstrip(".").strip()  # 防隐藏文件 + 前导点
    if not s:
        return "未命名"
    return s[:80]


def alias_name_for(name, talent_id):
    # type: (Optional[str], str) -> str
    """生成 by_name/<sanitized>__<tid> 这一段的 basename。"""
    if not talent_id:
        raise ValueError("alias_name_for 需要非空 talent_id")
    return "{}{}{}".format(sanitized_name(name), _ALIAS_SEPARATOR, talent_id)


def alias_dir_for(name, talent_id):
    # type: (Optional[str], str) -> Path
    """完整 alias 路径，不创建。"""
    return by_name_root() / alias_name_for(name, talent_id)


# ─── 写入入口 ─────────────────────────────────────────────────────────────────

def rebuild_alias_for(talent_id, name):
    # type: (str, Optional[str]) -> Dict[str, object]
    """幂等重建单个候选人的 by_name 软链。

    返回 dict：{
      'talent_id':     <tid>,
      'alias_path':    '<abs>',       # 期望的最终路径
      'created':       True|False,    # 这次新建 / 改向了
      'already_ok':    True|False,    # 之前就是对的
      'removed_stale': [<old basename>, ...],  # 删掉的旧 / 重名 alias
      'error':         <str>|None,
      'dry_run':       True|False,
    }
    """
    if not talent_id:
        raise ValueError("rebuild_alias_for 需要非空 talent_id")

    target = candidate_dir(talent_id)
    desired = alias_dir_for(name, talent_id)

    payload = {
        "talent_id": talent_id,
        "alias_path": str(desired),
        "created": False,
        "already_ok": False,
        "removed_stale": [],
        "error": None,
        "dry_run": False,
    }

    if side_effects_disabled():
        payload["dry_run"] = True
        return payload

    try:
        by_name_root().mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as e:
        payload["error"] = "mkdir by_name 失败: {}".format(e)
        return payload

    # 1) 先扫一遍 by_name/，把所有指向同一个 tid 的旧 alias 收集起来
    #    （应对：候选人改名 → 旧名 alias 残留 / 重名后缀变化）
    stale = _collect_aliases_for_tid(talent_id)
    for stale_path in stale:
        if stale_path == desired and _is_alias_to(desired, target):
            # 完全正确，跳过
            payload["already_ok"] = True
            continue
        try:
            # symlink 用 unlink；万一手贱搞成真目录就别动，warn 出去
            if stale_path.is_symlink() or not stale_path.exists():
                stale_path.unlink(missing_ok=True)
                payload["removed_stale"].append(stale_path.name)
            else:
                print("[candidate_aliases] 拒绝 unlink 真实目录: {}".format(stale_path),
                      file=sys.stderr)
        except OSError as e:
            print("[candidate_aliases] unlink 失败 {}: {}".format(stale_path, e),
                  file=sys.stderr)

    # 2) desired 现在应该不存在（或已经是对的）
    if payload["already_ok"]:
        return payload

    if not target.exists():
        # 主目录都不在，alias 没意义，记 error 但不抛
        payload["error"] = "candidate_dir 不存在: {}（先跑 ensure_candidate_dirs）".format(target)
        return payload

    try:
        # 用相对路径 symlink，迁移目录树时不会失效
        rel_target = os.path.relpath(str(target), str(desired.parent))
        os.symlink(rel_target, str(desired))
        payload["created"] = True
    except FileExistsError:
        # 并发新建：另一个进程已经建了；如果指向对就当成 ok
        if _is_alias_to(desired, target):
            payload["already_ok"] = True
        else:
            payload["error"] = "alias 已存在但指向不对: {}".format(desired)
    except OSError as e:
        payload["error"] = "symlink 失败 {}: {}".format(desired, e)

    return payload


def remove_alias_for(talent_id):
    # type: (str) -> Dict[str, object]
    """删除 by_name 下所有指向该 talent_id 的软链。

    用于 talent.cmd_delete 的收尾。返回 {removed: [...], dry_run: bool}。
    """
    if not talent_id:
        raise ValueError("remove_alias_for 需要非空 talent_id")

    payload = {"talent_id": talent_id, "removed": [], "dry_run": False}

    if side_effects_disabled():
        payload["dry_run"] = True
        return payload

    if not by_name_root().exists():
        return payload

    for stale in _collect_aliases_for_tid(talent_id):
        try:
            if stale.is_symlink() or not stale.exists():
                stale.unlink(missing_ok=True)
                payload["removed"].append(stale.name)
        except OSError as e:
            print("[candidate_aliases] remove unlink 失败 {}: {}".format(stale, e),
                  file=sys.stderr)
    return payload


def rebuild_all_aliases(talents):
    # type: (List[Tuple[str, Optional[str]]]) -> Dict[str, object]
    """全量重建 by_name/。

    入参 talents：[(talent_id, candidate_name), ...]，由调用方传（避免本模块强依
    赖 talent_db，单测和迁移脚本都能直接用）。

    动作：
      1. 逐个 rebuild_alias_for
      2. 扫 by_name/，把不在入参 tid 集合里的所有 alias 一并 unlink（dangling 清理）
      3. 返回汇总 {built, removed, errors}
    """
    payload = {
        "built": [], "already_ok": [], "removed_dangling": [], "errors": [],
        "dry_run": False,
    }

    if side_effects_disabled():
        payload["dry_run"] = True
        return payload

    keep_tids = set()
    for tid, name in talents:
        if not tid:
            continue
        keep_tids.add(tid)
        r = rebuild_alias_for(tid, name)
        if r.get("error"):
            payload["errors"].append({"talent_id": tid, "error": r["error"]})
        elif r.get("created"):
            payload["built"].append(r["alias_path"])
        elif r.get("already_ok"):
            payload["already_ok"].append(r["alias_path"])

    # 清理 dangling：by_name/ 里的 entry 如果 tid 不在 keep_tids 就 unlink
    if by_name_root().exists():
        for entry in by_name_root().iterdir():
            tid = _tid_from_alias_name(entry.name)
            if tid is None:
                # 命名不符合规范的，留着不动，让 HR 自己看
                continue
            if tid in keep_tids:
                continue
            if entry.is_symlink() or not entry.exists():
                try:
                    entry.unlink(missing_ok=True)
                    payload["removed_dangling"].append(entry.name)
                except OSError as e:
                    payload["errors"].append({"talent_id": tid, "error": "unlink dangling 失败: {}".format(e)})

    return payload


# ─── 内部辅助 ────────────────────────────────────────────────────────────────

def _collect_aliases_for_tid(talent_id):
    # type: (str) -> List[Path]
    """by_name 下所有 basename 以 '__<tid>' 结尾的条目（含正常 + 旧名残留）。"""
    root = by_name_root()
    if not root.exists():
        return []
    suffix = _ALIAS_SEPARATOR + talent_id
    out = []
    for entry in root.iterdir():
        if entry.name.endswith(suffix):
            out.append(entry)
    return out


def _is_alias_to(alias_path, target):
    # type: (Path, Path) -> bool
    """alias_path 是 symlink 且 readlink 解析后等于 target。"""
    try:
        if not alias_path.is_symlink():
            return False
        # resolve(strict=False) 处理相对路径 + target 不存在场景
        return alias_path.resolve(strict=False) == target.resolve(strict=False)
    except OSError:
        return False


def _tid_from_alias_name(basename):
    # type: (str) -> Optional[str]
    """从 '<name>__<tid>' 反解 tid。不符合规范返回 None。"""
    idx = basename.rfind(_ALIAS_SEPARATOR)
    if idx < 0:
        return None
    tid = basename[idx + len(_ALIAS_SEPARATOR):]
    # talent_id 形如 t_xxxxxx；防御性校验：不空、不含路径分隔符、有 t_ 前缀
    if not tid or "/" in tid or "\\" in tid:
        return None
    if not tid.startswith("t_"):
        return None
    return tid
