#!/usr/bin/env python3
from __future__ import print_function

"""
简历附件统一入口：自动判断候选人是否已在库中，分支处理。

- 已在库：展示字段差异，询问 HR 是否更新信息并存档简历
- 不在库：展示解析信息，询问 HR 确认 + 当前所处阶段，再录入

用法（第一步，由 OC 在收到简历附件时调用）：
  python3 intake/cmd_ingest_cv.py --file-path <路径> [--filename <文件名>]
  python3 intake/cmd_ingest_cv.py --pdf-path <路径> [--filename <文件名>]  # 向后兼容
  python3 intake/cmd_ingest_cv.py --file-key <key> [--filename <文件名>]
"""
import argparse
import io
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
import zipfile
import xml.etree.ElementTree as ET

from intake import cmd_parse_cv as _parse_mod
from core_state import load_state

_FIELD_LABELS = {
    "candidate_name":  "姓名",
    "candidate_email": "邮箱",
    "phone":           "电话",
    "wechat":          "微信",
    "position":        "应聘职位",
    "education":       "学历",
    "school":          "毕业院校",
    "work_years":      "工作年限",
    "source":          "来源渠道",
    "experience":      "简历摘要",
}

_LLM_TO_DB = {
    "name":           "candidate_name",
    "email":          "candidate_email",
    "phone":          "phone",
    "wechat":         "wechat",
    "position":       "position",
    "education":      "education",
    "school":         "school",
    "work_years":     "work_years",
    "source":         "source",
    "resume_summary": "experience",
}


def _detect_file_type(file_path, filename):
    # type: (str, str) -> str
    lower = ((filename or "") + " " + (file_path or "")).lower()
    if ".docx" in lower:
        return "docx"
    return "pdf"


def _extract_text_from_docx(docx_bytes):
    # type: (bytes) -> str
    try:
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
            xml_bytes = zf.read("word/document.xml")
    except Exception as e:
        raise RuntimeError("DOCX 解压失败: {}".format(e))

    try:
        root = ET.fromstring(xml_bytes)
    except Exception as e:
        raise RuntimeError("DOCX XML 解析失败: {}".format(e))

    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for para in root.findall(".//w:p", ns):
        parts = []
        for node in para.iter():
            tag = node.tag.rsplit("}", 1)[-1] if "}" in node.tag else node.tag
            if tag == "t" and node.text:
                parts.append(node.text)
            elif tag == "tab":
                parts.append("\t")
            elif tag in ("br", "cr"):
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs).strip()


# ─── DB 查询 ──────────────────────────────────────────────────────────────────

def _lookup_existing(name, email):
    # type: (str, str) -> dict or None
    """
    按姓名+邮箱查找已有候选人。
    返回完整候选人 dict，或 None（未找到），或 {"_multi": [...]}（多个匹配）。
    """
    try:
        import talent_db as _tdb
        if _tdb._is_enabled():
            import psycopg2

            conn = psycopg2.connect(**_tdb._conn_params())
            rows = []
            with conn.cursor() as cur:
                if email:
                    cur.execute(
                        "SELECT talent_id, candidate_name, candidate_email, phone, wechat, "
                        "position, education, school, work_years, source, experience, cv_path "
                        "FROM talents WHERE candidate_email ILIKE %s",
                        (email,)
                    )
                    rows = cur.fetchall()
                if not rows and name:
                    cur.execute(
                        "SELECT talent_id, candidate_name, candidate_email, phone, wechat, "
                        "position, education, school, work_years, source, experience, cv_path "
                        "FROM talents WHERE candidate_name ILIKE %s ORDER BY created_at DESC LIMIT 5",
                        ("%" + name + "%",)
                    )
                    rows = cur.fetchall()
            conn.close()

            if not rows:
                return None
            if len(rows) > 1:
                return {"_multi": [
                    {"talent_id": r[0], "candidate_name": r[1], "candidate_email": r[2]}
                    for r in rows
                ]}
            r = rows[0]
            return {
                "talent_id":       r[0],
                "candidate_name":  r[1] or "",
                "candidate_email": r[2] or "",
                "phone":           r[3] or "",
                "wechat":          r[4] or "",
                "position":        r[5] or "",
                "education":       r[6] or "",
                "school":          r[7] or "",
                "work_years":      r[8],
                "experience":      r[10] or "",
                "source":          r[9] or "",
                "cv_path":         r[11] or "",
            }
    except Exception as e:
        print("[cmd_ingest_cv] DB 查询失败，回退 JSON 状态: {}".format(e), file=sys.stderr)

    state = load_state()
    candidates = list((state.get("candidates") or {}).values())
    email = (email or "").strip().lower()
    name = (name or "").strip()

    def _to_record(cand):
        return {
            "talent_id": cand.get("talent_id", ""),
            "candidate_name": cand.get("candidate_name") or "",
            "candidate_email": cand.get("candidate_email") or "",
            "phone": cand.get("phone") or "",
            "wechat": cand.get("wechat") or "",
            "position": cand.get("position") or "",
            "education": cand.get("education") or "",
            "school": cand.get("school") or "",
            "work_years": cand.get("work_years"),
            "experience": cand.get("experience") or "",
            "source": cand.get("source") or "",
            "cv_path": cand.get("cv_path") or "",
        }

    matches = []
    if email:
        matches = [
            _to_record(c) for c in candidates
            if (c.get("candidate_email") or "").strip().lower() == email
        ]
    if not matches and name:
        needle = name.lower()
        matches = [
            _to_record(c) for c in candidates
            if needle in (c.get("candidate_name") or "").strip().lower()
        ][:5]

    if not matches:
        return None
    if len(matches) > 1:
        return {"_multi": [
            {
                "talent_id": r["talent_id"],
                "candidate_name": r["candidate_name"],
                "candidate_email": r["candidate_email"],
            }
            for r in matches
        ]}
    return matches[0]


# ─── 预览：已有候选人 ──────────────────────────────────────────────────────────

def _preview_existing(cand, fields, file_path):
    # type: (dict, dict, str) -> str
    tid = cand["talent_id"]
    cname = cand["candidate_name"] or tid

    # 字段顺序定义：(llm_key, db_col, 显示标签)
    _ORDERED = [
        ("name",           "candidate_name",  "姓名"),
        ("email",          "candidate_email", "邮箱"),
        ("phone",          "phone",           "电话"),
        ("wechat",         "wechat",          "微信"),
        ("position",       "position",        "应聘职位"),
        ("education",      "education",       "学历"),
        ("school",         "school",          "毕业院校"),
        ("work_years",     "work_years",      "工作年限"),
        ("source",         "source",          "来源渠道"),
        ("resume_summary", "experience",      "简历摘要"),
    ]

    changed_cols = {}   # db_col -> new_val（有差异且 LLM 有值的字段）
    table_lines = []
    for llm_key, db_col, label in _ORDERED:
        new_val = fields.get(llm_key)
        new_str = str(new_val).strip() if new_val is not None else ""
        old_val = cand.get(db_col)
        old_str = str(old_val).strip() if old_val is not None else ""

        if new_str == "" and old_str == "":
            marker = "  "
            disp = "（未识别 / 原为空）"
        elif new_str == "":
            marker = "  "
            disp = "（未识别） / 现有：{}".format(old_str)
        elif old_str == "":
            marker = "🆕"
            disp = "{} / 原为空".format(new_str)
            changed_cols[db_col] = new_val
        elif new_str == old_str:
            marker = "✅"
            disp = new_str
        else:
            # 简历摘要过长时截断显示
            dn = new_str[:60] + "…" if len(new_str) > 60 else new_str
            do = old_str[:60] + "…" if len(old_str) > 60 else old_str
            marker = "✏️"
            disp = "{} / 原：{}".format(dn, do)
            changed_cols[db_col] = new_val

        table_lines.append("{} {}：{}".format(marker, label, disp))

    lines = [
        "📋 【已有候选人 - 全字段比对】",
        "",
        "人才库已有 **{}**（{}），简历解析值 vs 现有值：".format(cname, tid),
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "图例：✅ 相同  ✏️ 有变更  🆕 新增  （无标记 = 未识别）",
        "━━━━━━━━━━━━━━━━━━━━",
    ] + table_lines + [
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    if changed_cols:
        changed_names = "、".join(_FIELD_LABELS.get(c, c) for c in changed_cols)
        lines += [
            "检测到 **{}** 有变更或新增。".format(changed_names),
            "",
            "请选择操作（也可指定只更新某几个字段，例如「只更新简历摘要」）：",
            "  • 「**确认更新**」— 将所有 ✏️🆕 字段写入数据库并存档简历",
            "  • 「**仅存档**」— 只关联简历文件，不修改任何字段",
            "  • 「**忽略**」— 不做任何变更",
        ]
    else:
        lines += [
            "解析结果与库中信息完全一致，无差异字段。",
            "",
            "请选择操作：",
            "  • 「**存档简历**」— 将该 PDF 关联到候选人档案",
            "  • 「**忽略**」— 不做任何变更",
        ]

    # 构建「全部更新」命令
    cmd_update = [
        'python3 intake/cmd_attach_cv.py',
        '--talent-id "{}"'.format(tid),
        '--cv-path "{}"'.format(file_path.replace('"', '\\"') if file_path else ""),
        '--confirm',
    ]
    for col, new_val in changed_cols.items():
        cmd_update.append('--field "{}={}"'.format(col, str(new_val).replace('"', '\\"')))

    # 构建「仅存档」命令
    cmd_archive = [
        'python3 intake/cmd_attach_cv.py',
        '--talent-id "{}"'.format(tid),
        '--cv-path "{}"'.format(file_path.replace('"', '\\"') if file_path else ""),
        '--confirm',
    ]

    lines += [
        "",
        "[OC_CMD_ON_CONFIRM_UPDATE]",
        " ".join(cmd_update),
        "",
        "[OC_CMD_ON_CONFIRM_ARCHIVE]",
        " ".join(cmd_archive),
        "",
        "[OC_NOTE]",
        "若 HR 只更新部分字段，删除 [OC_CMD_ON_CONFIRM_UPDATE] 中不需要的 --field 参数后执行。",
        "若 HR 要手动修正某字段，将对应 --field 参数的值替换为 HR 给出的正确值后执行。",
    ]

    return "\n".join(lines)


# ─── 预览：新候选人 ───────────────────────────────────────────────────────────

def _preview_new(fields, file_path):
    # type: (dict, str) -> str

    def v(key, fallback="（未识别）"):
        val = fields.get(key)
        if val is None or str(val).strip() == "":
            return fallback
        return str(val)

    def v_flag(key):
        """有值返回 ✅，否则返回 ⚠️"""
        val = fields.get(key)
        return "✅" if (val is not None and str(val).strip() != "") else "⚠️"

    lines = [
        "📋 【新候选人 - 待确认】",
        "",
        "人才库中**未找到**该候选人，将作为新候选人录入。",
        "LLM 从简历提取的全字段如下，请核对：",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "{} ① 姓名：{}".format(v_flag("name"), v("name")),
        "{} ② 邮箱：{}".format(v_flag("email"), v("email", "（未识别，**请手动补充**）")),
        "{} ③ 电话：{}".format(v_flag("phone"), v("phone")),
        "{} ④ 微信：{}".format(v_flag("wechat"), v("wechat")),
        "{} ⑤ 应聘职位：{}".format(v_flag("position"), v("position")),
        "{} ⑥ 学历：{}".format(v_flag("education"), v("education")),
        "{} ⑦ 毕业院校：{}".format(v_flag("school"), v("school")),
        "{} ⑧ 工作年限：{}".format(v_flag("work_years"), v("work_years")),
        "{} ⑨ 来源渠道：{}".format(v_flag("source"), v("source")),
        "{} ⑩ 简历摘要：{}".format(v_flag("resume_summary"), v("resume_summary")),
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "✅ = 已识别  ⚠️ = 未识别（需手动填写）",
        "",
        "如需修正某字段，直接告知（例如：「⑤ 改为量化研究员」或「来源渠道：内推」）。",
        "所有字段确认后，请告知该候选人**当前所处阶段**：",
        "  • 「新录入」或「NEW」— 尚未开始面试流程",
        "  • 「等待一面」— 已安排一面但未面试",
        "  • 「一面通过/笔试中」— 正在笔试阶段",
        "  • 「等待二面」— 笔试通过，待安排二面",
        "  • 其他阶段请直接描述",
    ]

    # 构建新候选人确认命令（默认 NEW 阶段），所有字段都包含（含空值占位）
    def _esc(s):
        return str(s).replace('"', '\\"')

    cmd_args = ['--name "{}"'.format(_esc(v("name", "")))]
    if fields.get("email"):
        cmd_args.append('--email "{}"'.format(_esc(fields["email"])))
    if fields.get("phone"):
        cmd_args.append('--phone "{}"'.format(_esc(fields["phone"])))
    if fields.get("wechat"):
        cmd_args.append('--wechat "{}"'.format(_esc(fields["wechat"])))
    if fields.get("position"):
        cmd_args.append('--position "{}"'.format(_esc(fields["position"])))
    if fields.get("education"):
        cmd_args.append('--education "{}"'.format(_esc(fields["education"])))
    if fields.get("school"):
        cmd_args.append('--school "{}"'.format(_esc(fields["school"])))
    if fields.get("work_years") is not None:
        cmd_args.append('--work-years {}'.format(fields["work_years"]))
    if fields.get("source"):
        cmd_args.append('--source "{}"'.format(_esc(fields["source"])))
    if fields.get("resume_summary"):
        cmd_args.append('--resume-summary "{}"'.format(_esc(fields["resume_summary"])))
    if file_path:
        cmd_args.append('--cv-path "{}"'.format(_esc(file_path)))
    cmd_args.append('--feishu-notify')

    lines += [
        "",
        "[OC_CMD_ON_CONFIRM]",
        "uv run python3 scripts/intake/cmd_new_candidate.py {}".format(" ".join(cmd_args)),
        "",
        "[OC_NOTE]",
        "若 HR 修正了某字段，在 [OC_CMD_ON_CONFIRM] 中替换对应参数的值后执行。",
        "若 HR 指定了非 NEW 的阶段，改用 uv run python3 scripts/intake/cmd_import_candidate.py --template \"<消息原文>\"。",
    ]

    return "\n".join(lines)


# ─── 主逻辑 ───────────────────────────────────────────────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(description="简历附件统一入口：自动判断新/旧候选人并分支处理")
    p.add_argument("--file-path", default="", help="本地简历路径（支持 PDF/DOCX）")
    p.add_argument("--pdf-path",  default="", help="本地 PDF 路径（向后兼容）")
    p.add_argument("--file-key",  default="", help="飞书文件 key（fallback）")
    p.add_argument("--message-id",default="", help="飞书消息 ID（可选）")
    p.add_argument("--filename",  default="", help="附件文件名（辅助 LLM 解析）")
    args = p.parse_args(argv or sys.argv[1:])

    local_path = args.file_path.strip() or args.pdf_path.strip()
    if not local_path and not args.file_key:
        print("ERROR: 请提供 --file-path / --pdf-path 或 --file-key")
        return 1

    filename = args.filename.strip() or (
        os.path.basename(local_path) if local_path else ""
    )
    file_type = _detect_file_type(local_path, filename)

    # ── 步骤 1：获取附件内容 ───────────────────────────────────────────────────
    if local_path:
        try:
            with open(local_path, "rb") as f:
                file_bytes = f.read()
            print("[1/4] 已读取本地{}：{} ({} 字节)".format(
                file_type.upper(), local_path, len(file_bytes)), file=sys.stderr)
        except Exception as e:
            print("ERROR: 无法读取附件：{}".format(e))
            return 1
    else:
        try:
            file_bytes = _parse_mod._download_pdf_from_feishu(
                args.message_id, args.file_key, "")
            print("[1/4] 飞书下载成功，{} 字节".format(len(file_bytes)), file=sys.stderr)
        except Exception as e:
            print("ERROR: 飞书下载附件失败：{}".format(e))
            return 1

    # ── 文件大小检查 ──────────────────────────────────────────────────────────
    _PDF_SIZE_LIMIT = 4 * 1024 * 1024  # 4MB
    if file_type == "pdf" and len(file_bytes) > _PDF_SIZE_LIMIT:
        size_mb = len(file_bytes) / (1024 * 1024)
        msg = (
            "[简历上传提醒] 收到的 PDF 文件过大（{:.1f} MB），已拒绝处理。\n"
            "文件名：{}\n"
            "请使用 Adobe Acrobat 或在线工具（如 ilovepdf.com）压缩后重新发送，"
            "压缩目标：1MB 以内。"
        ).format(size_mb, filename or os.path.basename(local_path or ""))
        print("ERROR: PDF 文件过大（{:.1f} MB），请压缩后重新上传。".format(size_mb))
        try:
            import feishu
            feishu.send_text_to_hr(msg)
        except Exception:
            pass
        return 1

    # ── 步骤 2：提取元数据 ────────────────────────────────────────────────────
    if file_type == "pdf":
        print("[2/4] 提取 PDF 元数据...", file=sys.stderr)
        meta = _parse_mod._extract_pdf_metadata(file_bytes)
    else:
        print("[2/4] 提取 DOCX 元数据...", file=sys.stderr)
        meta = {"title": "", "author": ""}

    # ── 步骤 3：提取正文 ──────────────────────────────────────────────────────
    try:
        print("[3/4] 提取{}正文...".format(file_type.upper()), file=sys.stderr)
        if file_type == "pdf":
            cv_text = _parse_mod._extract_text_from_pdf(file_bytes)
        else:
            cv_text = _extract_text_from_docx(file_bytes)
        if not cv_text.strip():
            print("ERROR: 附件中未提取到文字")
            return 1
        print("      共 {} 字".format(len(cv_text)), file=sys.stderr)
    except Exception as e:
        print("ERROR: 文本提取失败：{}".format(e))
        return 1

    # ── 步骤 4：LLM 解析字段 ──────────────────────────────────────────────────
    try:
        print("[4/4] LLM 解析候选人信息...".format(), file=sys.stderr)
        fields = _parse_mod._llm_parse_cv_fields(
            cv_text,
            filename=filename,
            pdf_title=meta.get("title") or "",
            pdf_author=meta.get("author") or "",
        )
    except Exception as e:
        print("ERROR: LLM 解析失败：{}".format(e))
        return 1

    parsed_name  = (fields.get("name") or "").strip()
    parsed_email = (fields.get("email") or "").strip()

    # ── 步骤 5：DB 查询 ───────────────────────────────────────────────────────
    print("[DB] 查询人才库（姓名={}，邮箱={}）...".format(
        parsed_name or "未知", parsed_email or "未知"), file=sys.stderr)
    cand = _lookup_existing(name=parsed_name, email=parsed_email)

    # ── 步骤 6：分支输出预览 ──────────────────────────────────────────────────
    if cand and "_multi" in cand:
        # 多个匹配，让 OC 引导 HR 确认是哪一位
        lines = ["找到多位姓名相近的候选人，请确认是哪一位："]
        for c in cand["_multi"]:
            lines.append("  • {} — {} {}".format(
                c["talent_id"], c["candidate_name"] or "未知", c.get("candidate_email", "")))
        lines.append("")
        lines.append("请告知 talent_id，OC 将重新处理。")
        print("\n".join(lines))
        return 0

    if cand:
        # ── 已有候选人分支 ────────────────────────────────────────────────────
        print("[DB] 已找到候选人：{} ({})".format(
            cand["candidate_name"], cand["talent_id"]), file=sys.stderr)
        preview = _preview_existing(cand, fields, local_path)
    else:
        # ── 新候选人分支 ──────────────────────────────────────────────────────
        print("[DB] 未找到候选人，将作为新录入处理", file=sys.stderr)
        preview = _preview_new(fields, local_path)

    print(preview)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
