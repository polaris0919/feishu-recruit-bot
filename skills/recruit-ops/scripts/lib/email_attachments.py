#!/usr/bin/env python3
"""lib/email_attachments.py —— v3.5.6 邮件附件落盘与元数据提取（拆自
exam/fetch_exam_submission._download_message + 通用化）。

【职责（只干这一件）】
  - 接收 email.message.Message 对象（来自 `email_lib.message_from_bytes`）
  - 遍历 multipart，识别 attachment / inline 文件
  - 解码 MIME 文件名（含中文/Quoted-Printable/Base64）
  - 安全化文件名（防路径穿越、防奇怪字符）
  - 拒绝超大、空、黑名单文件名（winmail.dat、ATT00001.txt）
  - 落盘到 lib.candidate_storage.attachment_dir(talent_id, context, email_id)
    即 candidates/<tid>/{exam_answer|email}/em_<email_id>/<filename>
    （context='exam' 走 exam_answer/，其他走 email/）
  - 文件权限 0o600，目录 0o700（仅 owner 可读）
  - 不解压压缩包（zip/rar 原样存储；解压逻辑保留在 exam/fetch_exam_submission，
    那是按需手动评审用的，跟 cmd_scan 落盘语义不同）
  - 返回每个附件的元数据 dict：{name,size,mime,path,sha256,saved,note}

【绝不做】
  - 不调用 IMAP（msg 由调用方传入）
  - 不写 talent_emails / talent_events（调用方自己写）
  - 不做 LLM / OCR / 解析（那是 cmd_analyze 的事）
  - 不解压（避免 zip bomb / 路径穿越，cmd_scan 是高频自动跑，零容错）
  - 不发飞书 / 邮件

【为什么独立成 lib/】
  - inbox/cmd_scan 需要在每封新 inbound 邮件入表后立刻落附件
  - exam/fetch_exam_submission 也涉及附件下载，但它是 on-demand 拉到 /tmp 的
    "评审现场"用法；语义和持久化策略不同，故不复用，只共享 _safe_name 这种
    纯函数级别的工具
  - 将来 followup 阶段（候选人入职后回邮件）也可能想存档附件，统一入口

【dry-run 行为】
  - extract_metadata(msg)：永远只算元数据，不写盘（saved=false, path=None）
  - extract_and_save(msg, ...)：若 lib.side_effect_guard.side_effects_disabled()
    返回 True，则等价于 extract_metadata —— 元数据照算，但绝不写盘。

【调用示例】
  from lib import email_attachments
  meta = email_attachments.extract_and_save(
      msg, talent_id="t_abc123", email_id="ec...uuid")
  # meta == [{"name": "候选人F_简历.pdf", "size": 235123, ...}]
"""
from __future__ import print_function

import hashlib
import mimetypes
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from lib.exam_imap import _decode_mime_header
from lib.side_effect_guard import side_effects_disabled


# ─── 常量 ─────────────────────────────────────────────────────────────────────

# v3.5.8：附件落盘路径全部由 lib.candidate_storage 决定，按 talent_emails.context
# 分流到 candidates/<tid>/exam_answer/ 或 candidates/<tid>/email/。
# 旧的 ATTACHMENT_ROOT = data/candidate_answer 已下线；调用方一律通过
# extract_and_save(..., context=...) 让 candidate_storage 算路径。
# 元数据 path 字段写「相对 data_root() 的路径」，便于跨机器迁移和审计。

# 单文件最大体积。超过则跳过（saved=false, note='oversize'）。
# 25MB：覆盖大多数简历/作品集 PDF + 短代码 zip；过大的笔试代码包通常异常。
MAX_FILE_BYTES = 25 * 1024 * 1024

# 单封邮件最多落多少个附件。防异常邮件 / 自动化群发塞爆磁盘。
MAX_FILES_PER_EMAIL = 20

# 文件名黑名单（Outlook winmail.dat、Apple Mail ATT00001.txt 等噪音）
_SKIP_FILENAMES = frozenset({
    "winmail.dat", "smime.p7s", "smime.p7m",
    "ATT00001.txt", "ATT00001.htm", "ATT00001.html",
    "image001.png", "image001.gif", "image001.jpg",  # Outlook 签名图
})

# 仅作 _safe_name 兜底：剥离任何不安全字符
_UNSAFE_NAME_RE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')


# ─── 内部工具 ─────────────────────────────────────────────────────────────────

def _safe_name(raw_name):
    # type: (str) -> str
    """把任意来源的文件名清洗成可安全落盘的 basename。

    规则：
      1. 取 basename（防止"../.."路径穿越）
      2. 去掉控制字符 + Windows 禁用字符
      3. 头尾去空白与点（防 ".."、"foo."）
      4. 限制长度 ≤ 200 字节（NTFS/ext4 都能跑）
      5. 空 → 'unnamed.bin'
    """
    base = os.path.basename((raw_name or "").strip())
    base = base.replace("/", "_").replace("\\", "_")
    base = _UNSAFE_NAME_RE.sub("_", base)
    base = base.strip(" .")
    if not base:
        return "unnamed.bin"
    # 字节长度截断（中文 UTF-8 是 3 字节/字，要按编码后字节算）
    encoded = base.encode("utf-8")
    if len(encoded) <= 200:
        return base
    # 保留扩展名
    stem, ext = os.path.splitext(base)
    ext_b = ext.encode("utf-8")
    keep = max(1, 200 - len(ext_b))
    truncated_stem = stem.encode("utf-8")[:keep].decode("utf-8", errors="ignore")
    return (truncated_stem + ext) or "unnamed.bin"


def _decode_filename(raw):
    # type: (Optional[str]) -> str
    if not raw:
        return ""
    try:
        return _decode_mime_header(raw)
    except Exception:
        return raw


def _guess_mime(filename, fallback):
    # type: (str, Optional[str]) -> str
    """优先用 Content-Type，否则按扩展名猜，最后回落到 application/octet-stream。"""
    if fallback and "/" in fallback:
        return fallback.split(";", 1)[0].strip().lower()
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _iter_attachment_parts(msg):
    """生成 (filename_raw, content_type, payload_bytes) 三元组。

    判定标准：
      - Content-Disposition 带 'attachment'  ✅
      - 或 part 自身有 filename 且 Content-Type 不是纯 text/* multipart/*  ✅
      - 跳过 multipart 容器、纯 text/plain 正文、纯 text/html 正文
    """
    for part in msg.walk():
        ct = (part.get_content_type() or "").lower()
        if ct.startswith("multipart/"):
            continue
        disposition = str(part.get("Content-Disposition") or "").lower()
        filename_raw = part.get_filename()
        is_attachment = "attachment" in disposition or "inline" in disposition
        # part.get_filename() 命中也算附件（有些客户端不发 Content-Disposition）
        if filename_raw:
            is_attachment = True
        if not is_attachment:
            continue
        # 显式跳过被当成附件的纯邮件正文（防把 text/plain 当成附件落盘）
        if ct in ("text/plain", "text/html") and not filename_raw:
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception as e:
            print("[email_attachments] payload decode 失败 ct={}: {}".format(ct, e),
                  file=sys.stderr)
            continue
        yield filename_raw, ct, payload


def _ensure_dir(path):
    # type: (Path) -> None
    """递归建目录，权限 0o700（仅 owner 可读）。"""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    # mkdir 的 mode 在已存在的祖先目录上不会再改，这里显式 chmod 末级
    try:
        os.chmod(str(path), 0o700)
    except OSError:
        pass


def _unique_path(directory, filename):
    # type: (Path, str) -> Path
    """同邮件多附件同名时给后续的加 (2)/(3) 后缀，防覆盖。"""
    target = directory / filename
    if not target.exists():
        return target
    stem, ext = os.path.splitext(filename)
    for i in range(2, 1000):
        candidate = directory / "{}({}){}".format(stem, i, ext)
        if not candidate.exists():
            return candidate
    raise RuntimeError(
        "文件名冲突无法解决: {} 在 {} 已有上千同名".format(filename, directory))


def _sha256(data):
    # type: (bytes) -> str
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


# ─── 公共 API ─────────────────────────────────────────────────────────────────

def extract_metadata(msg):
    # type: (Any) -> List[Dict[str, Any]]
    """只算附件元数据，不写盘。用于 dry-run / 预检 / 测试。

    返回的元素和 extract_and_save 同 schema：
      {name, size, mime, path=None, sha256=None, saved=False, note='dry-run'}
    """
    out = []
    count = 0
    for filename_raw, ct, payload in _iter_attachment_parts(msg):
        if count >= MAX_FILES_PER_EMAIL:
            out.append({
                "name": None, "size": 0, "mime": None,
                "path": None, "sha256": None, "saved": False,
                "note": "skipped: exceeded MAX_FILES_PER_EMAIL={}".format(
                    MAX_FILES_PER_EMAIL),
            })
            break
        count += 1
        decoded = _safe_name(_decode_filename(filename_raw))
        size = len(payload) if payload else 0
        out.append({
            "name": decoded,
            "size": size,
            "mime": _guess_mime(decoded, ct),
            "path": None,
            "sha256": None,
            "saved": False,
            "note": "dry-run (extract_metadata)",
        })
    return out


def extract_and_save(msg, talent_id, email_id, context=None):
    # type: (Any, str, str, Optional[str]) -> List[Dict[str, Any]]
    """遍历 msg 的附件，逐个落盘到候选人资料目录，返回元数据列表。

    v3.5.8：落盘根由 lib.candidate_storage.attachment_dir 决定：
      - context=='exam' → candidates/<tid>/exam_answer/em_<eid>/<file>
      - 其他            → candidates/<tid>/email/em_<eid>/<file>
    （顺手修了 v3.5.6 的 t_t_<tid> 多余前缀 bug。）

    幂等性：
      - 每个 email_id 是 UUID，base_dir 唯一
      - 同名附件用 _unique_path 加 (2) 后缀，不会覆盖
      - 调用方应只对"insert_email_if_absent 真插入了"的邮件调用本函数

    跳过策略（写一行 saved=false 元数据，不抛异常）：
      - 文件名命中 _SKIP_FILENAMES
      - 体积 = 0 / > MAX_FILE_BYTES / 数量 > MAX_FILES_PER_EMAIL
      - 落盘 OSError（盘满/权限错误等）

    dry-run（side_effects_disabled() == True）：
      - 不写盘
      - 元数据照算（path 设为预期路径供调试，saved=false, note='dry-run'）
    """
    if not talent_id or not email_id:
        raise ValueError("extract_and_save 需要 talent_id 和 email_id")

    from lib.candidate_storage import attachment_dir, data_root
    is_dry = side_effects_disabled()
    base_dir = attachment_dir(talent_id, context, email_id)
    _root = data_root()  # 用于把 path 算成相对 data_root 的相对路径

    out = []
    saved_count = 0

    if not is_dry:
        try:
            _ensure_dir(base_dir)
        except OSError as e:
            # 目录都建不出来就直接全部 saved=false，不抛
            print("[email_attachments] mkdir 失败 {}: {}".format(base_dir, e),
                  file=sys.stderr)
            for filename_raw, ct, payload in _iter_attachment_parts(msg):
                decoded = _safe_name(_decode_filename(filename_raw))
                out.append({
                    "name": decoded,
                    "size": len(payload) if payload else 0,
                    "mime": _guess_mime(decoded, ct),
                    "path": None,
                    "sha256": None,
                    "saved": False,
                    "note": "mkdir failed: {}".format(e),
                })
            return out

    for filename_raw, ct, payload in _iter_attachment_parts(msg):
        if saved_count >= MAX_FILES_PER_EMAIL:
            out.append({
                "name": None, "size": 0, "mime": None,
                "path": None, "sha256": None, "saved": False,
                "note": "skipped: exceeded MAX_FILES_PER_EMAIL={}".format(
                    MAX_FILES_PER_EMAIL),
            })
            break

        decoded = _safe_name(_decode_filename(filename_raw))
        size = len(payload) if payload else 0
        mime = _guess_mime(decoded, ct)

        # 黑名单 / 空文件 / 超大 → 写一行 saved=false 元数据
        if decoded in _SKIP_FILENAMES:
            out.append({
                "name": decoded, "size": size, "mime": mime,
                "path": None, "sha256": None, "saved": False,
                "note": "skipped: filename in blacklist",
            })
            continue
        if size == 0:
            out.append({
                "name": decoded, "size": 0, "mime": mime,
                "path": None, "sha256": None, "saved": False,
                "note": "skipped: empty payload",
            })
            continue
        if size > MAX_FILE_BYTES:
            out.append({
                "name": decoded, "size": size, "mime": mime,
                "path": None, "sha256": None, "saved": False,
                "note": "skipped: oversize ({} > {} bytes)".format(size, MAX_FILE_BYTES),
            })
            continue

        sha = _sha256(payload)

        if is_dry:
            # dry-run：算完就算（path 给相对 data_root 的预期路径）
            try:
                rel_dry = (base_dir / decoded).relative_to(_root)
            except ValueError:
                rel_dry = base_dir / decoded
            out.append({
                "name": decoded, "size": size, "mime": mime,
                "path": str(rel_dry),
                "sha256": sha,
                "saved": False,
                "note": "dry-run (side_effects_disabled)",
            })
            saved_count += 1
            continue

        target = _unique_path(base_dir, decoded)
        try:
            with open(str(target), "wb") as f:
                f.write(payload)
            os.chmod(str(target), 0o600)
        except OSError as e:
            print("[email_attachments] 写文件失败 {}: {}".format(target, e),
                  file=sys.stderr)
            out.append({
                "name": decoded, "size": size, "mime": mime,
                "path": None, "sha256": sha, "saved": False,
                "note": "write failed: {}".format(e),
            })
            continue

        try:
            rel_path = target.relative_to(_root)
        except ValueError:
            rel_path = target  # data_root 切换 / 软链等极端情况，写绝对
        out.append({
            "name": target.name,  # 用最终落盘名（可能含 (2) 后缀）
            "size": size,
            "mime": mime,
            "path": str(rel_path),
            "sha256": sha,
            "saved": True,
            "note": None,
        })
        saved_count += 1

    return out
