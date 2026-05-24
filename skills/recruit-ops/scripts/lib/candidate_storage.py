#!/usr/bin/env python3
"""lib/candidate_storage.py —— v3.5.8 候选人统一资料目录。

【目标】
  每个候选人在 $RECRUIT_DATA_ROOT/candidates/<talent_id>/ 下有一个独立目录，
  内部两个固定子目录：
      exam_answer/   旧版 / 手动拉取兼容目录（新入站笔试附件不再写这里）
      email/         其他邮件附件（context!='exam' 的邮件附件）

  CV 原件统一按候选人姓名 + talent_id 归档到：
      $RECRUIT_DATA_ROOT/candidate_cv/<candidate_name>__<talent_id>/

  新入站笔试答案附件统一按候选人分组落到：
      $RECRUIT_DATA_ROOT/exam_submissions/<candidate_name>__<talent_id>/
  笔试题包统一放到：
      $RECRUIT_DATA_ROOT/exam_package/

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
  RECRUIT_DATA_ROOT       数据根目录（默认 <workspace_root>/data）。
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
import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from lib.side_effect_guard import side_effects_disabled


# ─── 常量 ─────────────────────────────────────────────────────────────────────

_DEFAULT_DATA_ROOT = Path("/home/admin/recruit-files")

# 候选人目录固定子目录名（顺序固定，便于 ensure_candidate_dirs 输出 stable）。
# CV 已迁出 candidates/<tid>/cv，统一放到 candidate_cv/<name>__<tid>/。
_SUBDIRS = ("exam_answer", "email")

# 目录权限：仅 owner（ops 用户）可读写。CV 和笔试答案都涉及隐私 / 候选人作品,
# 默认 0o700。文件权限单独由调用方（email_attachments.py 用 0o600）控制。
_DIR_MODE = 0o700

# 飞书 Gateway 把附件落盘时会前缀 `doc_<12 hex>_`，纯属内部 ID，不属于候选人原文件名。
# 我们落到 candidate_cv/<name>__<tid>/ 时统一剥掉，省得档案路径里挂一串 hash。
# 兼容 8~32 位 hex，避免后续飞书改长度时炸掉。
_FEISHU_DOC_PREFIX_RE = re.compile(r"^doc_[0-9a-f]{8,32}_+")
_UNSAFE_COMPONENT_RE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')


# ─── 路径计算（纯函数） ───────────────────────────────────────────────────────

def data_root():
    # type: () -> Path
    """读 env，每次调用都重读以兼容测试 monkeypatch（不 cache）。"""
    return Path(os.environ.get("RECRUIT_DATA_ROOT", str(_DEFAULT_DATA_ROOT)))


def candidates_root():
    # type: () -> Path
    """所有候选人目录的公共父目录。"""
    return data_root() / "candidates"


def candidate_cv_root():
    # type: () -> Path
    """候选人 CV 原件的人工浏览根目录。"""
    return data_root() / "candidate_cv"


def candidate_dir(talent_id):
    # type: (str) -> Path
    """单个候选人的根目录。不创建，仅算路径。"""
    _validate_talent_id(talent_id)
    return candidates_root() / talent_id


def cv_folder_name(talent_id, candidate_name=None):
    # type: (str, Optional[str]) -> str
    """CV 目录名：<candidate_name>__<talent_id>。"""
    _validate_talent_id(talent_id)
    name = _safe_dir_component(candidate_name, fallback="未命名")
    return "{}__{}".format(name, talent_id.strip())


def cv_dir(talent_id, candidate_name=None):
    # type: (str, Optional[str]) -> Path
    """候选人 CV 目录。

    传入 candidate_name 时按新规范返回 candidate_cv/<姓名>__<tid>/。
    未传姓名时优先复用已有的 *__<tid> 目录；用于旧 caller 和 fallback。
    """
    if candidate_name:
        return candidate_cv_root() / cv_folder_name(talent_id, candidate_name)
    existing = _find_existing_cv_dir(talent_id)
    if existing:
        return existing
    return candidate_cv_root() / cv_folder_name(talent_id, None)


def legacy_cv_dir(talent_id):
    # type: (str) -> Path
    """旧 CV 目录 candidates/<tid>/cv，仅用于迁移 / fallback。"""
    return candidate_dir(talent_id) / "cv"


def exam_answer_dir(talent_id):
    # type: (str) -> Path
    return candidate_dir(talent_id) / "exam_answer"


def exam_submissions_dir():
    # type: () -> Path
    """所有候选人笔试答案附件的人工浏览根目录。"""
    return data_root() / "exam_submissions"


def exam_submission_dir(talent_id, candidate_name=None):
    # type: (str, Optional[str]) -> Path
    """单个候选人的统一笔试提交目录。"""
    return exam_submissions_dir() / cv_folder_name(talent_id, candidate_name)


def exam_package_dir():
    # type: () -> Path
    """发给候选人的笔试题包目录。"""
    return data_root() / "exam_package"


def email_dir(talent_id):
    # type: (str) -> Path
    return candidate_dir(talent_id) / "email"


def attachment_dir(talent_id, context, email_id):
    # type: (str, Optional[str], str) -> Path
    """根据 talent_emails.context 决定附件落 exam_submissions/ 还是 email/。

    context 归一化（大小写 / 前后空格不敏感）：
      'exam'              → exam_submissions/
      其他（包括 None / '') → email/em_<email_id>/

    只算路径，不 mkdir。调用方（email_attachments.extract_and_save）自己
    mkdir parents=True，因为它要在 mkdir 之后立刻写文件，没必要走两步。
    """
    if not email_id or not str(email_id).strip():
        raise ValueError("attachment_dir 需要非空 email_id")
    ctx_norm = (context or "").strip().lower()
    if ctx_norm == "exam":
        return exam_submissions_dir()
    return email_dir(talent_id) / "em_{}".format(str(email_id).strip())


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


def import_cv(talent_id, src_path, mode="move", candidate_name=None):
    # type: (str, str, str, Optional[str]) -> Path
    """把 src_path 的 CV 文件搬 / 复制到 cv_dir(talent_id, candidate_name) 下。

    返回新文件的绝对 Path。

    mode：
      'move' （默认）：原位置删除（生产路径用，data/media/inbound 是 OpenClaw 缓冲）
      'copy'         ：保留原文件（迁移脚本 dry-run / 极少数怕丢原件场景）

    幂等 / 重名处理：
      - src_path 已经在 cv_dir(talent_id, candidate_name) 下 → no-op，直接返回 src_path
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

    target_dir = cv_dir(talent_id, candidate_name=candidate_name)
    # 已经在目标目录里 → no-op
    try:
        src.relative_to(target_dir)
        if not side_effects_disabled():
            _write_cv_manifest(talent_id, candidate_name, src, source_path=src)
        return src
    except ValueError:
        pass

    target = target_dir / strip_feishu_prefix(src.name)

    if side_effects_disabled():
        # dry-run：返回最终落点路径，但不创建目录、不复制
        return target

    # 真正落盘：先确保目录在
    ensure_candidate_dirs(talent_id)
    target_dir.mkdir(parents=True, mode=_DIR_MODE, exist_ok=True)
    original_target = target
    target = _resolve_collision(target, src)
    if target == src:  # _resolve_collision 判定幂等
        _write_cv_manifest(talent_id, candidate_name, original_target, source_path=src)
        return original_target

    if mode == "move":
        # shutil.move 跨设备时退化为 copy + remove；同 fs 是原子 rename
        shutil.move(str(src), str(target))
    else:
        shutil.copy2(str(src), str(target))
    try:
        os.chmod(str(target), 0o600)
    except OSError:
        pass  # 权限设置失败不致命
    _write_cv_manifest(talent_id, candidate_name, target, source_path=src)
    return target


# ─── 公共辅助 ────────────────────────────────────────────────────────────────

def strip_feishu_prefix(filename):
    # type: (str) -> str
    """剥离飞书 Gateway 附件 ID 前缀 `doc_<hex>_`，返回原始文件名。

    飞书把消息里的附件落盘时会自动加 `doc_<12hex>_<original>` 前缀，比如
    `doc_bfcbf2a1a335_车光明简历.pdf`。我们希望候选人 cv/ 目录里只见原始名，
    避免档案路径混进飞书内部 ID。

    没匹配到前缀时原样返回；匹配到就剥掉。前后空白也顺手 trim
    （不动文件名中间的空格 / 中文 / 全角符号）。

    >>> strip_feishu_prefix("doc_bfcbf2a1a335_张三.pdf")
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

def _safe_dir_component(raw, fallback):
    # type: (Optional[str], str) -> str
    """把姓名等外部文本清洗成单个安全目录名片段。"""
    value = str(raw or "").strip()
    value = _UNSAFE_COMPONENT_RE.sub("_", value)
    value = value.strip(" .")
    if not value:
        value = fallback
    encoded = value.encode("utf-8")
    if len(encoded) <= 80:
        return value
    return encoded[:80].decode("utf-8", errors="ignore") or fallback


def _find_existing_cv_dir(talent_id):
    # type: (str) -> Optional[Path]
    """按 *__<tid> 查找已有 candidate_cv 目录。"""
    _validate_talent_id(talent_id)
    root = candidate_cv_root()
    if not root.is_dir():
        return None
    matches = [p for p in root.glob("*__{}".format(talent_id.strip())) if p.is_dir()]
    if not matches:
        return None
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0]


def _file_sha256(path):
    # type: (Path) -> str
    h = hashlib.sha256()
    with open(str(path), "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_cv_manifest(talent_id, candidate_name, cv_path, source_path=None):
    # type: (str, Optional[str], Path, Optional[Path]) -> None
    """在 candidate_cv/<name>__<tid>/_manifest.json 写当前 CV 元数据。"""
    try:
        path = Path(cv_path).expanduser().resolve()
        manifest = path.parent / "_manifest.json"
        data = {}
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        history = data.get("history") if isinstance(data.get("history"), list) else []
        entry = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "file": str(path),
            "file_name": path.name,
            "size": path.stat().st_size,
            "sha256": _file_sha256(path),
            "source_path": str(source_path) if source_path else None,
        }
        history.append(entry)
        data = {
            "talent_id": talent_id,
            "candidate_name": candidate_name,
            "current_cv": str(path),
            "updated_at": entry["at"],
            "history": history[-20:],
        }
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(str(manifest), 0o600)
        except OSError:
            pass
    except Exception:
        # manifest 是人工浏览辅助，不影响主流程。
        return


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
