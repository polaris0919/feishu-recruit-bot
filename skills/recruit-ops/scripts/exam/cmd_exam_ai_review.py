#!/usr/bin/env python3
"""
手动 AI 笔试评审命令。

典型用法（推荐分两步：先终端预览，再推飞书+写审计；评审结果会自动缓存复用，不重复扣 LLM 费）：
    # 步骤 1：终端预览（首次会调一次 LLM，结果缓存到 cache_dir/<talent_id>/_ai_review_result.json）
    python3 exam/cmd_exam_ai_review.py --talent-id t_xxx

    # 步骤 2：你看完报告觉得 OK → 推飞书 + 写 talent_events 审计（自动复用缓存，不再调 LLM）
    python3 exam/cmd_exam_ai_review.py --talent-id t_xxx --feishu --save-event

    # 候选人重新提交了答案，需要彻底重新评审：重拉 + 重跑 LLM
    python3 exam/cmd_exam_ai_review.py --talent-id t_xxx --refetch --rerun

    # 用本地已有目录，不去 IMAP（用于离线 / 手动整理过的场景）
    python3 exam/cmd_exam_ai_review.py --talent-id t_xxx --code-dir /path/to/local --no-fetch

    # dry-run：只构造 prompt，不调 LLM，用于自检
    python3 exam/cmd_exam_ai_review.py --talent-id t_xxx --no-llm --save-prompt /tmp/p.txt

不修改候选人状态机字段。最终通过/不通过仍需老板使用 cmd_exam_result.py 决定。
"""
from __future__ import print_function

import argparse
import json
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from lib.core_state import get_tdb

from lib.exam_grader import (
    DEFAULT_RUBRIC_PATH,
    LLMError,
    RubricError,
    build_prompt,
    format_report_for_feishu,
    load_rubric,
    review_submission,
)
from exam.fetch_exam_submission import fetch_for as _imap_fetch_for


# ---------------------------------------------------------------------------
# 文件收集（白名单后缀，避免把候选人误传的二进制塞给 LLM）
# ---------------------------------------------------------------------------

_CODE_EXT = {".py", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".ipynb"}
_DOC_EXT  = {".md", ".markdown", ".txt", ".rst"}
_OUT_EXT  = {".csv", ".json", ".tsv"}

_MAX_FILE_BYTES = 1_000_000   # 单文件最大读 1MB，超过截断
_MAX_OUTPUT_PREVIEW_BYTES = 50_000
_MAX_OUTPUT_FILE_BYTES = 500_000   # CSV 单文件 >500KB 视为输入数据，跳过

# 候选人提交里凡是命中这些子目录名（不区分大小写、子串匹配）一律视为「输入原始数据」目录，
# 不收集其中的 CSV/JSON/TSV，避免把官方逐笔 CSV 也喂给 LLM。
_INPUT_DIR_HINTS = {
    "data", "raw", "input", "inputs", "raw_data",
    "原始数据", "源数据", "题目", "题目数据", "exam_data",
    "hs_data", "sh_data", "sz_data",
}

# 常见输出/结果目录名（仅做为日志提示，不强制白名单）
_OUTPUT_DIR_HINTS = {
    "output", "outputs", "result", "results",
    "结果", "输出", "盘口结果", "盘口撮合结果",
}


def _is_text_file(path):
    # type: (str) -> bool
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except Exception:
        return False
    if b"\x00" in head:
        return False
    return True


def _read_text(path, limit=_MAX_FILE_BYTES):
    # type: (str, int) -> str
    try:
        with open(path, "rb") as f:
            data = f.read(limit + 1)
    except Exception as e:
        return "[read error: {}]".format(e)
    truncated = len(data) > limit
    data = data[:limit]
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except Exception:
            continue
    else:
        text = data.decode("utf-8", errors="replace")
    if truncated:
        text += "\n\n[... truncated by cmd_exam_ai_review (>{} bytes) ...]\n".format(limit)
    return text


def _ipynb_to_text(path):
    # type: (str) -> str
    """把 .ipynb 拆成纯文本（按 cell 顺序，code/markdown 各自标头），便于 LLM 阅读。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            nb = json.load(f)
    except Exception as e:
        return "[ipynb parse error: {}]".format(e)
    chunks = []
    for i, cell in enumerate(nb.get("cells") or []):
        ct = cell.get("cell_type", "")
        src = cell.get("source") or []
        if isinstance(src, list):
            src = "".join(src)
        chunks.append("# === cell[{}] type={} ===\n{}".format(i, ct, src))
    return "\n\n".join(chunks)


def _path_hits_hint(parts, hints):
    # type: (List[str], set) -> bool
    """parts 中任一段名（lower）与 hints 中任一关键词存在子串重合则命中。"""
    for p in parts:
        pl = p.lower()
        for h in hints:
            if h.lower() in pl:
                return True
    return False


def _collect_dir(path, exts, exclude_input_dirs=False, max_file_bytes=None):
    # type: (str, set, bool, Optional[int]) -> List[Dict[str, str]]
    """
    递归扫描 path，按后缀白名单收集文件。

    exclude_input_dirs: 若 True，命中 _INPUT_DIR_HINTS 的子目录直接跳过（用于 CSV/JSON 这类
                        既可能是候选人产出又可能是题目输入的歧义文件）。代码 / 文档不需要这个保护。
    max_file_bytes:    单文件硬上限（字节），超出直接跳过（默认按 _MAX_FILE_BYTES 截断而不是跳过）。
    """
    out = []
    if not os.path.isdir(path):
        return out
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv", "node_modules", ".idea", ".vscode"}]
        rel = os.path.relpath(root, path)
        parts = [] if rel == "." else rel.split(os.sep)
        if exclude_input_dirs and _path_hits_hint(parts, _INPUT_DIR_HINTS):
            continue
        for fn in sorted(files):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in exts:
                continue
            full = os.path.join(root, fn)
            if max_file_bytes is not None:
                try:
                    sz = os.path.getsize(full)
                except OSError:
                    continue
                if sz > max_file_bytes:
                    continue
            if not _is_text_file(full) and ext != ".ipynb":
                continue
            content = _ipynb_to_text(full) if ext == ".ipynb" else _read_text(full)
            out.append({
                "path": os.path.relpath(full, path),
                "content": content,
            })
    return out


def _collect_files(paths, allow_exts=None):
    # type: (List[str], Optional[set]) -> List[Dict[str, str]]
    out = []
    for p in paths or []:
        if not os.path.isfile(p):
            print("[warn] 跳过不存在的文件: {}".format(p), file=sys.stderr)
            continue
        ext = os.path.splitext(p)[1].lower()
        if allow_exts and ext not in allow_exts:
            print("[warn] 跳过非白名单后缀: {}".format(p), file=sys.stderr)
            continue
        content = _ipynb_to_text(p) if ext == ".ipynb" else _read_text(p)
        out.append({"path": os.path.basename(p), "content": content})
    return out


# ---------------------------------------------------------------------------
# 候选人元数据加载（用作 candidate_label / exam_sent_at 默认值）
# ---------------------------------------------------------------------------

def _load_candidate_meta(talent_id):
    # type: (str) -> Dict[str, Any]
    if not talent_id:
        return {}
    tdb = get_tdb()
    if tdb is None:
        return {"candidate_label": talent_id}
    try:
        cand = tdb.get_one(talent_id)
    except Exception:
        cand = None
    if not cand:
        return {"candidate_label": talent_id}
    name = (cand.get("candidate_name") or "").strip()
    label = "{} ({})".format(name, talent_id) if name else talent_id
    return {
        "candidate_label": label,
        "candidate_name": name,
        "exam_sent_at": cand.get("exam_sent_at"),
        "stage": cand.get("stage"),
        "_db_loaded": True,
    }


def _hours_between(t1, t2):
    # type: (Optional[str], Optional[str]) -> Optional[float]
    if not t1 or not t2:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"):
        try:
            d1 = datetime.strptime(str(t1)[:19], fmt[:len(str(t1)[:19].replace("T","T"))])
            d2 = datetime.strptime(str(t2)[:19], fmt[:len(str(t2)[:19].replace("T","T"))])
            return round((d2 - d1).total_seconds() / 3600.0, 2)
        except Exception:
            continue
    try:
        d1 = datetime.fromisoformat(str(t1)[:19])
        d2 = datetime.fromisoformat(str(t2)[:19])
        return round((d2 - d1).total_seconds() / 3600.0, 2)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    # type: (Optional[List[str]]) -> argparse.Namespace
    p = argparse.ArgumentParser(
        description="对单个候选人笔试提交跑 AI 评审（rubric 驱动；不动候选人状态）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--talent-id", help="候选人 talent_id（用作 candidate_label，并从 DB 拉 exam_sent_at + 邮箱）")
    p.add_argument("--candidate-label", help="候选人显示名（默认 = '<姓名> (<talent_id>)'；显式传以覆盖）")
    p.add_argument("--code-dir", help="本地已有的提交目录（指定后默认不再去 IMAP 拉）")
    p.add_argument("--code-file", action="append", default=[], help="单个代码文件（可重复）")
    p.add_argument("--doc-file",  action="append", default=[], help="单个说明文档（可重复）")
    p.add_argument("--output-file", action="append", default=[], help="单个输出文件（可重复，仅前 50KB 入 prompt）")
    p.add_argument("--email-body", help="候选人邮件正文（可解释延迟原因等；自动从 IMAP 拉的会自动填入）")
    p.add_argument("--exam-sent-at", help="题目发出时间（ISO 字符串），覆盖 DB 推断")
    p.add_argument("--submitted-at", help="候选人首次提交时间（ISO 字符串），用于完成时间调节项；自动从邮件 Date 推断")
    p.add_argument("--rubric", default=DEFAULT_RUBRIC_PATH, help="rubric.json 路径（默认: {}）".format(DEFAULT_RUBRIC_PATH))

    g = p.add_argument_group("IMAP 自动拉取（默认行为）")
    g.add_argument("--cache-dir", default="/tmp/exam_submissions",
                   help="IMAP 拉取的本地缓存根目录（默认 /tmp/exam_submissions/<talent_id>）")
    g.add_argument("--refetch", action="store_true", help="强制重新从 IMAP 拉取（清掉缓存目录）")
    g.add_argument("--no-fetch", action="store_true", help="不去 IMAP 拉，仅用 --code-dir 给的本地目录")
    g.add_argument("--max-msgs", type=int, default=3, help="IMAP 最多拉最近 N 封匹配邮件（默认 3）")
    g.add_argument("--rerun", action="store_true",
                   help="强制重新调 LLM（默认会复用缓存的评审结果，避免重复扣费）")

    p.add_argument("--feishu", action="store_true", help="评审完成后把报告推到老板飞书")
    p.add_argument("--save-event", action="store_true", help="把评审结果写入 talent_events（需要 --talent-id）")
    p.add_argument("--json", action="store_true", help="只输出原始 JSON 评审结果（机器可读）")
    p.add_argument("--no-llm", action="store_true", help="不真调 LLM，仅校验输入与构造 prompt（用于干跑）")
    p.add_argument("--save-prompt", help="把构造的 prompt 写入指定文件，便于人工审阅")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# IMAP 自动拉取 + 邮件元数据自动填充
# ---------------------------------------------------------------------------

_DATE_HDR_RE = re.compile(r"^Date:\s*(.+)$", re.MULTILINE)


def _read_email_meta(meta_path):
    # type: (str) -> Dict[str, str]
    """从 fetch_exam_submission 写出的 _email_meta.txt 解析 Subject/From/Date/Message-ID。"""
    out = {}
    if not os.path.isfile(meta_path):
        return out
    try:
        with open(meta_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if ":" in line:
                    k, _, v = line.partition(":")
                    out[k.strip().lower()] = v.strip()
    except Exception:
        pass
    return out


def _parse_email_date_to_iso(date_str):
    # type: (str) -> Optional[str]
    if not date_str:
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        if dt is None:
            return None
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def _ensure_local_submission(args, meta):
    # type: (argparse.Namespace, Dict[str, Any]) -> Optional[str]
    """
    根据 args 决定提交目录：
      - 优先使用 --code-dir
      - 否则用 cache_dir/<talent_id>，必要时自动调 IMAP 拉
    返回最终的本地目录路径（或 None 表示无可用目录）。
    """
    if args.code_dir:
        if args.refetch:
            print("[warn] 同时指定了 --code-dir 与 --refetch；--refetch 仅在自动拉取模式下生效，已忽略",
                  file=sys.stderr)
        return args.code_dir

    if args.no_fetch:
        return None

    if not args.talent_id:
        # 没 talent_id 又没 code_dir，无法 IMAP 拉，让上层报错
        return None

    target_dir = os.path.join(args.cache_dir, args.talent_id)
    has_existing = os.path.isdir(target_dir) and any(os.scandir(target_dir))

    if args.refetch and os.path.isdir(target_dir):
        import shutil
        print("[info] --refetch: 清空缓存目录 {}".format(target_dir), file=sys.stderr)
        shutil.rmtree(target_dir, ignore_errors=True)
        has_existing = False

    if has_existing:
        print("[info] 复用本地缓存: {} （加 --refetch 强制重拉）".format(target_dir), file=sys.stderr)
        return target_dir

    print("[info] 自动从 IMAP 拉取笔试提交 → {}".format(target_dir), file=sys.stderr)
    try:
        out_dir, files = _imap_fetch_for(
            talent_id=args.talent_id,
            out=target_dir,
            max_msgs=args.max_msgs,
        )
    except Exception as e:
        print("[error] IMAP 自动拉取失败: {}".format(e), file=sys.stderr)
        return None

    if not files:
        print("[warn] IMAP 没拉到任何附件，目录可能为空: {}".format(out_dir), file=sys.stderr)
        return out_dir
    print("[info] IMAP 拉取完成：{} 个文件".format(len(files)), file=sys.stderr)
    return out_dir


def _autofill_from_local_dir(args, code_dir):
    # type: (argparse.Namespace, str) -> None
    """根据本地目录里 fetch 留下的 _email_body.txt / _email_meta.txt 自动补 args（仅当用户没显式传时）。"""
    if not code_dir or not os.path.isdir(code_dir):
        return

    body_path = os.path.join(code_dir, "_email_body.txt")
    if not args.email_body and os.path.isfile(body_path):
        try:
            with open(body_path, "r", encoding="utf-8", errors="replace") as f:
                args.email_body = f.read().strip()
            if args.email_body:
                print("[info] 自动从 _email_body.txt 读取邮件正文 ({} 字符)".format(
                    len(args.email_body)), file=sys.stderr)
        except Exception:
            pass

    meta = _read_email_meta(os.path.join(code_dir, "_email_meta.txt"))
    if not args.submitted_at and meta.get("date"):
        iso = _parse_email_date_to_iso(meta["date"])
        if iso:
            args.submitted_at = iso
            print("[info] 自动从邮件 Date 推断 submitted_at: {}".format(iso), file=sys.stderr)


def _build_candidate(args):
    # type: (argparse.Namespace) -> Dict[str, Any]
    meta = _load_candidate_meta(args.talent_id) if args.talent_id else {}

    code_dir = _ensure_local_submission(args, meta)
    if code_dir:
        args.code_dir = code_dir
        _autofill_from_local_dir(args, code_dir)

    code_files = []
    doc_files = []
    output_files = []

    if args.code_dir:
        if not os.path.isdir(args.code_dir):
            print("[error] --code-dir 不存在: {}".format(args.code_dir), file=sys.stderr)
            sys.exit(2)
        code_files.extend(_collect_dir(args.code_dir, _CODE_EXT))
        doc_files.extend(_collect_dir(args.code_dir, _DOC_EXT))
        # CSV/JSON：排除候选人原始输入数据目录（data/raw/原始数据 等），
        # 单文件 >500KB 直接跳过（输入数据通常大、自己跑出来的盘口快照通常小）
        for o in _collect_dir(args.code_dir, _OUT_EXT,
                              exclude_input_dirs=True,
                              max_file_bytes=_MAX_OUTPUT_FILE_BYTES):
            content = o["content"]
            if len(content) > _MAX_OUTPUT_PREVIEW_BYTES:
                content = content[:_MAX_OUTPUT_PREVIEW_BYTES] + "\n[... output preview truncated ...]\n"
            o["content"] = content
            output_files.append(o)

    code_files.extend(_collect_files(args.code_file, allow_exts=_CODE_EXT))
    doc_files.extend(_collect_files(args.doc_file, allow_exts=_DOC_EXT))
    for o in _collect_files(args.output_file, allow_exts=_OUT_EXT):
        if len(o["content"]) > _MAX_OUTPUT_PREVIEW_BYTES:
            o["content"] = o["content"][:_MAX_OUTPUT_PREVIEW_BYTES] + "\n[... preview truncated ...]\n"
        output_files.append(o)

    if not code_files and not doc_files:
        print("[error] 未收集到任何代码或文档文件，请检查 --code-dir / --code-file / --doc-file", file=sys.stderr)
        sys.exit(2)

    candidate_label = (
        args.candidate_label
        or meta.get("candidate_label")
        or args.talent_id
        or "candidate"
    )

    exam_sent_at = args.exam_sent_at or meta.get("exam_sent_at")
    submitted_at = args.submitted_at
    hours_used = _hours_between(exam_sent_at, submitted_at) if (exam_sent_at and submitted_at) else None

    extra_lines = []
    if meta.get("stage"):
        extra_lines.append("候选人当前阶段: {}".format(meta["stage"]))
    extra_lines.append("代码文件数: {} / 文档文件数: {} / 输出文件数: {}".format(
        len(code_files), len(doc_files), len(output_files)
    ))

    return {
        "candidate_label": candidate_label,
        "exam_sent_at": exam_sent_at,
        "submitted_at": submitted_at,
        "hours_used": hours_used,
        "email_body": args.email_body or "",
        "code_files": code_files,
        "doc_files": doc_files,
        "output_files": output_files,
        "extra_context": "\n".join(extra_lines),
    }


def _review_cache_path(args):
    # type: (argparse.Namespace) -> Optional[str]
    """评审结果缓存路径：talent_id 优先，否则 fallback 到 code_dir，再否则不缓存。"""
    if args.talent_id:
        return os.path.join(args.cache_dir, args.talent_id, "_ai_review_result.json")
    if args.code_dir and os.path.isdir(args.code_dir):
        return os.path.join(args.code_dir, "_ai_review_result.json")
    return None


def _load_or_run_review(args, candidate):
    # type: (argparse.Namespace, Dict[str, Any]) -> Dict[str, Any]
    """
    复用磁盘缓存避免重复扣 LLM 费用：
      - 缓存存在且非 --rerun：直接 json.load 返回
      - 否则跑 review_submission，成功后落盘
    """
    cache_path = _review_cache_path(args)

    if cache_path and os.path.isfile(cache_path) and not args.rerun:
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            print("[info] 复用缓存的 AI 评审结果: {} （加 --rerun 强制重跑）".format(cache_path),
                  file=sys.stderr)
            return cached
        except Exception as e:
            print("[warn] 读取评审缓存失败 ({}: {})，将重新调 LLM".format(cache_path, e),
                  file=sys.stderr)

    if args.rerun and cache_path and os.path.isfile(cache_path):
        print("[info] --rerun: 忽略 {} 重新调 LLM".format(cache_path), file=sys.stderr)

    result = review_submission(candidate, rubric_path=args.rubric)

    if cache_path and not result.get("_error"):
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print("[info] AI 评审结果已缓存: {}".format(cache_path), file=sys.stderr)
        except Exception as e:
            print("[warn] 写入评审缓存失败 ({}: {})".format(cache_path, e), file=sys.stderr)

    return result


def main(argv=None):
    args = parse_args(argv)

    if args.save_event and not args.talent_id:
        print("[error] --save-event 需要 --talent-id", file=sys.stderr)
        return 2

    candidate = _build_candidate(args)

    if args.no_llm:
        try:
            rubric = load_rubric(args.rubric)
        except RubricError as e:
            print("[error] rubric 加载失败: {}".format(e), file=sys.stderr)
            return 2
        prompt = build_prompt(rubric, candidate)
        print("[ok] dry-run 通过 (prompt {} chars, rubric {})".format(len(prompt), rubric.get("version")))
        if args.save_prompt:
            with open(args.save_prompt, "w", encoding="utf-8") as f:
                f.write(prompt)
            print("[ok] prompt 写入: {}".format(args.save_prompt))
        return 0

    result = _load_or_run_review(args, candidate)

    if args.save_prompt:
        try:
            rubric = load_rubric(args.rubric)
            with open(args.save_prompt, "w", encoding="utf-8") as f:
                f.write(build_prompt(rubric, candidate))
        except Exception:
            pass

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_report_for_feishu(result, candidate_label=candidate["candidate_label"]))

    if result.get("_error"):
        print("\n[warn] AI 评审失败: {} ({})".format(
            result.get("_error"), result.get("_message", "")
        ), file=sys.stderr)

    if args.feishu:
        try:
            from lib import feishu as _fs
        except Exception as e:
            print("[error] feishu 模块导入失败: {}".format(e), file=sys.stderr)
            return 1
        report_text = format_report_for_feishu(result, candidate_label=candidate["candidate_label"])
        ok = bool(_fs.send_text(report_text))
        print("[feishu] {}".format("已推送" if ok else "推送失败"), file=sys.stderr)

    if args.save_event and args.talent_id:
        tdb = get_tdb()
        if tdb is None:
            print("[warn] talent_db 未启用，--save-event 已跳过", file=sys.stderr)
        else:
            tdb.save_exam_ai_review(args.talent_id, result, actor="manual_review")
            print("[event] exam_ai_review 已写入 talent_events", file=sys.stderr)

    return 0 if not result.get("_error") else 1


if __name__ == "__main__":
    sys.exit(main())
