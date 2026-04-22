#!/usr/bin/env python3
"""lib/candidate_storage.py —— v3.5.8 候选人统一资料目录。

【目标】
  每个候选人在 $RECRUIT_DATA_ROOT/candidates/<talent_id>/ 下有一个独立目录，
  内部三个固定子目录：
      cv/            候选人 CV 原件（cmd_attach_cv 复制 / 移动进来）
      exam_answer/   笔试答案附件（context='exam' 的邮件附件）
      email/         其他邮件附件（context!='exam' 的邮件附件）

  不再用旧的 data/candidate_answer/t_t_<tid>/em_<eid>/ 布局（v3.5.6 写歪
  多了一个 t_ 前缀，借这次顺手修），也不再让 CV 留在 data/media/inbound/
  的 OpenClaw 入口缓冲区里。

【为什么不入库 BYTEA】
  - 老板要直接 xdg-open / unzip 看，BYTEA 要先 dump 中间步多
  - exam/fetch_exam_submission.py 的 unzip 工作流依赖 FS 路径
  - 备份走 rsync 比 pg_dump 便宜
  - DB row 体积膨胀会 toast 化，AI 查询变慢
  DB 只存元数据：talent_emails.attachments JSONB（v3.5.6 已加）+ talents.cv_path。

【纯函数 + 一组 mkdir helper】
  路径由 talent_id 算出，不入 DB（candidate_dir(tid) 永远可推导）。
  ensure_candidate_dirs() 是唯一会动盘的入口；其他都是纯计算。

【Env】
  RECRUIT_DATA_ROOT       数据根目录（默认 <RECRUIT_WORKSPACE>/data）。
                          测试用 setUp 把它指向 tempfile.mkdtemp() 即可隔离。
  RECRUIT_DISABLE_SIDE_EFFECTS=1
                          dry-run 时不真 mkdir，ensure_candidate_dirs() 返回
                          {'dry_run': True}，cmd_new_candidate / 迁移脚本要识别。

【典型调用】
  from lib.candidate_storage import (
      candidate_dir, cv_dir, exam_answer_dir, email_dir,
      attachment_dir, ensure_candidate_dirs, import_cv,
  )

  # 录入候选人时：
  result = ensure_candidate_dirs("t_xxx")  # warn-continue：失败不抛，看 result['error']

  # cmd_attach_cv 时：
  new_path = import_cv("t_xxx", "/path/to/source.pdf", mode="move")

  # cmd_scan 落附件时：
  base = attachment_dir("t_xxx", context="exam", email_id="em_yyy")
"""
from __future__ import annotations

import errno
import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from lib.side_effect_guard import side_effects_disabled


# ─── 常量 ─────────────────────────────────────────────────────────────────────

_DEFAULT_DATA_ROOT = "<RECRUIT_WORKSPACE>/data"

# 三个固定子目录名（顺序固定，便于 ensure_candidate_dirs 输出 stable）
_SUBDIRS = ("cv", "exam_answer", "email")

# 目录权限：仅 owner（ops 用户）可读写。CV 和笔试答案都涉及隐私 / 候选人作品,
# 默认 0o700。文件权限单独由调用方（email_attachments.py 用 0o600）控制。
_DIR_MODE = 0o700

# 飞书 Gateway 把附件落盘时会前缀 `doc_<12 hex>_`，纯属内部 ID，不属于候选人原文件名。
# 我们落到 candidates/<tid>/cv/ 时统一剥掉，省得档案路径里挂一串 hash。
# 兼容 8~32 位 hex，避免后续飞书改长度时炸掉。
_FEISHU_DOC_PREFIX_RE = re.compile(r"^doc_[0-9a-f]{8,32}_+")


# ─── 路径计算（纯函数） ───────────────────────────────────────────────────────

def data_root():
    # type: () -> Path
    """读 env，每次调用都重读以兼容测试 monkeypatch（不 cache）。"""
    return Path(os.environ.get("RECRUIT_DATA_ROOT", _DEFAULT_DATA_ROOT))


def candidates_root():
    # type: () -> Path
    """所有候选人目录的公共父目录。"""
    return data_root() / "candidates"


def candidate_dir(talent_id):
    # type: (str) -> Path
    """单个候选人的根目录。不创建，仅算路径。"""
    _validate_talent_id(talent_id)
    return candidates_root() / talent_id


def cv_dir(talent_id):
    # type: (str) -> Path
    return candidate_dir(talent_id) / "cv"


def exam_answer_dir(talent_id):
    # type: (str) -> Path
    return candidate_dir(talent_id) / "exam_answer"


def email_dir(talent_id):
    # type: (str) -> Path
    return candidate_dir(talent_id) / "email"


def attachment_dir(talent_id, context, email_id):
    # type: (str, Optional[str], str) -> Path
    """根据 talent_emails.context 决定附件落 exam_answer/ 还是 email/。

    context 归一化（大小写 / 前后空格不敏感）：
      'exam'              → exam_answer/em_<email_id>/
      其他（包括 None / '') → email/em_<email_id>/

    只算路径，不 mkdir。调用方（email_attachments.extract_and_save）自己
    mkdir parents=True，因为它要在 mkdir 之后立刻写文件，没必要走两步。
    """
    if not email_id or not str(email_id).strip():
        raise ValueError("attachment_dir 需要非空 email_id")
    ctx_norm = (context or "").strip().lower()
    base = exam_answer_dir(talent_id) if ctx_norm == "exam" else email_dir(talent_id)
    return base / "em_{}".format(str(email_id).strip())


# ─── 写入入口（唯一会动盘的函数） ─────────────────────────────────────────────

def ensure_candidate_dirs(talent_id):
    # type: (str) -> Dict[str, object]
    """幂等 mkdir 候选人三件套。warn-continue 风格：失败不抛。

    返回：
      {
        "talent_id":       <tid>,
        "candidate_dir":   "<abs path>",
        "created":         [<dirname>, ...],   # 本次新建的子目录
        "already_existed": [<dirname>, ...],   # 之前已存在
        "error":           <str>|None,         # mkdir 失败时的根因（caller 决定 warn 还是 fail）
        "dry_run":         True|False,         # side_effects_disabled() 时为 True 且不真 mkdir
      }

    设计取舍：
      - 失败不抛：上游 cmd_new_candidate 选 warn-continue 模式（候选人照入库，
        飞书推 warn 让运维事后补目录），所以这里把 OSError 兜成 error 字段返回
      - 幂等：重复录入同一个 talent_id 不会报错
      - dry-run：只算路径不动盘，避免测试 / chain dry-run 污染真实 data 根
    """
    _validate_talent_id(talent_id)

    payload = {
        "talent_id": talent_id,
        "candidate_dir": str(candidate_dir(talent_id)),
        "created": [],
        "already_existed": [],
        "error": None,
        "dry_run": False,
    }

    if side_effects_disabled():
        payload["dry_run"] = True
        return payload

    try:
        candidate_dir(talent_id).mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)
        for sub in _SUBDIRS:
            sub_path = candidate_dir(talent_id) / sub
            if sub_path.exists():
                payload["already_existed"].append(sub)
            else:
                sub_path.mkdir(parents=True, exist_ok=False, mode=_DIR_MODE)
                payload["created"].append(sub)
    except OSError as e:
        payload["error"] = "mkdir 失败: errno={} ({}): {}".format(
            e.errno, errno.errorcode.get(e.errno, "?"), e.strerror or str(e))
    except Exception as e:
        payload["error"] = "mkdir 异常 {}: {}".format(type(e).__name__, e)

    return payload


def import_cv(talent_id, src_path, mode="move"):
    # type: (str, str, str) -> Path
    """把 src_path 的 CV 文件搬 / 复制到 cv_dir(talent_id) 下。

    返回新文件的绝对 Path。

    mode：
      'move' （默认）：原位置删除（生产路径用，data/media/inbound 是 OpenClaw 缓冲）
      'copy'         ：保留原文件（迁移脚本 dry-run / 极少数怕丢原件场景）

    幂等 / 重名处理：
      - src_path 已经在 cv_dir(talent_id) 下 → no-op，直接返回 src_path
      - cv_dir 下已有同名文件且内容不同 → 加 (2) / (3) ... 后缀
      - cv_dir 下已有同名且 size + mtime 一致 → 当作幂等重跑，no-op

    dry-run（side_effects_disabled()）：不动盘，但仍返回算出来的目标路径，
    供上游 echo / 审计。
    """
    _validate_talent_id(talent_id)
    src = Path(src_path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError("CV 源文件不存在: {}".format(src_path))
    if mode not in ("move", "copy"):
        raise ValueError("import_cv mode 必须是 'move' / 'copy'，给的是 {!r}".format(mode))

    target_dir = cv_dir(talent_id)
    # 已经在目标目录里 → no-op
    try:
        src.relative_to(target_dir)
        return src
    except ValueError:
        pass

    target = target_dir / strip_feishu_prefix(src.name)

    if side_effects_disabled():
        # dry-run：返回最终落点路径，但不创建目录、不复制
        return target

    # 真正落盘：先确保目录在
    ensure_candidate_dirs(talent_id)
    target = _resolve_collision(target, src)
    if target == src:  # _resolve_collision 判定幂等
        return target

    if mode == "move":
        # shutil.move 跨设备时退化为 copy + remove；同 fs 是原子 rename
        shutil.move(str(src), str(target))
    else:
        shutil.copy2(str(src), str(target))
    try:
        os.chmod(str(target), 0o600)
    except OSError:
        pass  # 权限设置失败不致命
    return target


# ─── 公共辅助 ────────────────────────────────────────────────────────────────

def strip_feishu_prefix(filename):
    # type: (str) -> str
    """剥离飞书 Gateway 附件 ID 前缀 `doc_<hex>_`，返回原始文件名。

    飞书把消息里的附件落盘时会自动加 `doc_<12hex>_<original>` 前缀，比如
    `doc_0123456789ab_候选人B简历.pdf`。我们希望候选人 cv/ 目录里只见原始名，
    避免档案路径混进飞书内部 ID。

    没匹配到前缀时原样返回；匹配到就剥掉。前后空白也顺手 trim
    （不动文件名中间的空格 / 中文 / 全角符号）。

    >>> strip_feishu_prefix("doc_0123456789ab_张三.pdf")
    '张三.pdf'
    >>> strip_feishu_prefix("张三.pdf")
    '张三.pdf'
    >>> strip_feishu_prefix("DOC_abc123_x.pdf")    # 大写不剥（飞书一直是小写）
    'DOC_abc123_x.pdf'
    """
    if not filename:
        return filename
    return _FEISHU_DOC_PREFIX_RE.sub("", filename, count=1).strip()


# ─── 内部辅助 ────────────────────────────────────────────────────────────────

def _validate_talent_id(talent_id):
    # type: (str) -> None
    """防御性检查：talent_id 必须是非空字符串、不含 / 和 \\ 等路径分隔符、
    不以 . 开头（防 ../../ 攻击）。"""
    if not talent_id or not isinstance(talent_id, str):
        raise ValueError("talent_id 必须是非空字符串，给的是 {!r}".format(talent_id))
    s = talent_id.strip()
    if not s:
        raise ValueError("talent_id 不能是纯空白")
    if "/" in s or "\\" in s or s.startswith("."):
        raise ValueError("talent_id 含非法字符: {!r}".format(talent_id))


def _resolve_collision(target, src):
    # type: (Path, Path) -> Path
    """如果 target 已存在：
       - 与 src 大小+mtime 一致 → 视为幂等，返回 src（caller 检测后 no-op）
       - 否则在文件名前追加 (2) / (3) / ... 找到第一个空位返回
    """
    if not target.exists():
        return target
    try:
        if (target.stat().st_size == src.stat().st_size
                and int(target.stat().st_mtime) == int(src.stat().st_mtime)):
            # 幂等：返回 src 让 caller 跳过实际操作
            return src
    except OSError:
        pass
    stem, suffix = target.stem, target.suffix
    n = 2
    while True:
        candidate = target.with_name("{} ({}){}".format(stem, n, suffix))
        if not candidate.exists():
            return candidate
        n += 1
        if n > 999:
            raise RuntimeError("CV 同名文件冲突过多: {}".format(target))


def list_known_subdirs():
    # type: () -> List[str]
    """暴露给测试 / 文档用的常量列表。"""
    return list(_SUBDIRS)
