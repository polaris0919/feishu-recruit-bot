#!/usr/bin/env python3

"""
简历 PDF 自动解析脚本。

HR 通过飞书发送候选人简历 PDF 后，OC 调用此脚本：
  1. 从飞书 API 下载 PDF 文件
  2. 用 pdfminer.six 提取文本
  3. 调用 DashScope LLM 解析全部候选人字段
  4. 输出格式化预览，供 OC 展示给 HR 确认

用法：
  python3 cmd_parse_cv.py --message-id <飞书消息ID> --file-key <文件key>
  python3 cmd_parse_cv.py --pdf-path /tmp/cv.pdf          # 本地测试用

HR 确认后，OC 用解析出的字段调用 cmd_new_candidate.py 写入数据库。
"""
import argparse
import io
import json
import os
import re
import sys
import urllib.request
import urllib.error

from recruit_paths import config_candidates, first_existing

FEISHU_API = "https://open.feishu.cn/open-apis"
OPENCLAW_CONFIG = first_existing(config_candidates("openclaw.json"))

_DASHSCOPE_URL = "https://coding.dashscope.aliyuncs.com/v1/chat/completions"
_DASHSCOPE_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
_LLM_MODEL = os.environ.get("DASHSCOPE_MODEL", "qwen3-max-2026-01-23")


# ─── 鉴权 ──────────────────────────────────────────────────────────────────────

def _load_dashscope_key():
    global _DASHSCOPE_KEY
    if _DASHSCOPE_KEY:
        return
    config_paths = [
        str(p) for p in config_candidates("dashscope-config.json")
    ] + [
        str(p) for p in config_candidates("openclaw.json")
    ]
    for path in config_paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            key = (
                data.get("DASHSCOPE_API_KEY")
                or data.get("dashscope_api_key")
                or (data.get("llm") or {}).get("api_key")
                or ""
            )
            if key:
                _DASHSCOPE_KEY = key.strip()
                return
        except Exception:
            continue


def _get_feishu_credentials():
    # type: () -> tuple
    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    if app_id and app_secret:
        return app_id, app_secret
    try:
        with open(str(OPENCLAW_CONFIG), "r", encoding="utf-8") as f:
            cfg = json.load(f)
        acct = cfg["channels"]["feishu"]["accounts"]["feishubot"]
        return acct["appId"], acct["appSecret"]
    except Exception as e:
        raise RuntimeError("无法读取飞书应用凭据: " + str(e))


def _get_tenant_token(app_id, app_secret):
    # type: (str, str) -> str
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(
        FEISHU_API + "/auth/v3/tenant_access_token/internal",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.loads(r.read())
    if d.get("code") != 0:
        raise RuntimeError("获取飞书 token 失败: " + str(d))
    return d["tenant_access_token"]


# ─── 飞书文件下载 ────────────────────────────────────────────────────────────────

def _get_parent_message_id(token, reply_message_id):
    # type: (str, str) -> str
    """
    通过当前消息 ID 查询飞书 API，获取被引用（父）消息的 message_id。
    用于：用户回复文件消息时，从文字回复消息 -> 反查文件消息 message_id。
    """
    url = "{}/im/v1/messages/{}".format(FEISHU_API, reply_message_id)
    req = urllib.request.Request(
        url,
        headers={"Authorization": "Bearer " + token},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        raise RuntimeError("查询消息失败: HTTP {} {}".format(e.code, body[:200]))
    except Exception as e:
        raise RuntimeError("查询消息失败: {}".format(e))

    items = (data.get("data") or {}).get("items") or []
    if items:
        return items[0].get("parent_id", "")
    return ""


def _download_pdf_from_feishu(message_id, file_key, reply_message_id=""):
    # type: (str, str, str) -> bytes
    """
    通过飞书 API 下载消息中的文件附件，返回二进制内容。
    若 message_id 为空但有 reply_message_id，则通过查询 reply_message_id 的父消息来获取文件 message_id。
    API: GET /im/v1/messages/{message_id}/resources/{file_key}?type=file
    """
    app_id, app_secret = _get_feishu_credentials()
    token = _get_tenant_token(app_id, app_secret)

    # 自动从回复消息查找原文件消息的 message_id
    if not message_id and reply_message_id:
        print("      [自动查找] 从回复消息 {} 反查文件消息 ID...".format(reply_message_id), file=sys.stderr)
        try:
            parent_id = _get_parent_message_id(token, reply_message_id)
            if parent_id:
                message_id = parent_id
                print("      [自动查找] 找到文件消息 ID: {}".format(message_id), file=sys.stderr)
        except Exception as e:
            print("      [自动查找] 反查失败: {}".format(e), file=sys.stderr)

    if not message_id:
        raise RuntimeError(
            "无法定位飞书消息 ID。\n"
            "请提供 --reply-message-id <当前消息ID> 让脚本自动反查，\n"
            "或直接提供 --message-id <文件消息ID>。\nfile_key={}".format(file_key)
        )
        print("      [自动查找] 找到 message_id={}".format(message_id), file=sys.stderr)

    url = "{}/im/v1/messages/{}/resources/{}?type=file".format(
        FEISHU_API, message_id, file_key
    )
    req = urllib.request.Request(
        url,
        headers={"Authorization": "Bearer " + token},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            content_type = r.headers.get("Content-Type", "")
            data = r.read()
            # 飞书返回 JSON 说明是错误
            if "application/json" in content_type:
                err = json.loads(data)
                raise RuntimeError("飞书下载文件失败: code={} msg={}".format(
                    err.get("code"), err.get("msg")))
            return data
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            err = json.loads(body)
            raise RuntimeError("飞书下载文件 HTTP 错误: code={} msg={}".format(
                err.get("code"), err.get("msg")))
        except (ValueError, KeyError):
            raise RuntimeError("飞书下载文件 HTTP {}: {}".format(e.code, body[:200]))


# ─── PDF 文本提取 ────────────────────────────────────────────────────────────────

def _extract_text_from_pdf(pdf_bytes):
    # type: (bytes) -> str
    """用 pdfminer.six 从 PDF 二进制内容提取纯文本。"""
    try:
        from pdfminer.high_level import extract_text_to_fp
        from pdfminer.layout import LAParams
    except ImportError:
        raise RuntimeError(
            "未安装 pdfminer.six，请先执行：uv sync；如仍缺失，可执行：uv pip install 'pdfminer.six==20200517' --no-deps"
        )

    output = io.StringIO()
    input_fp = io.BytesIO(pdf_bytes)
    extract_text_to_fp(input_fp, output, laparams=LAParams(), output_type="text", codec=None)
    text = output.getvalue()
    return text.strip()


def _extract_pdf_metadata(pdf_bytes):
    # type: (bytes) -> dict
    """
    提取 PDF 文档属性（Title、Author 等元数据）。
    Word/WPS 另存为 PDF 时会自动写入文档标题。
    返回 {"title": str, "author": str}，字段可能为空字符串。
    """
    meta = {"title": "", "author": ""}
    try:
        from pdfminer.pdfparser import PDFParser
        from pdfminer.pdfdocument import PDFDocument

        fp = io.BytesIO(pdf_bytes)
        parser = PDFParser(fp)
        doc = PDFDocument(parser)
        info_list = doc.info  # list of dicts
        if info_list:
            info = info_list[0]
            def _decode(v):
                if isinstance(v, bytes):
                    for enc in ("utf-8", "gbk", "latin-1"):
                        try:
                            return v.decode(enc).strip()
                        except Exception:
                            continue
                    return ""
                return str(v).strip() if v else ""
            meta["title"] = _decode(info.get("Title", ""))
            meta["author"] = _decode(info.get("Author", ""))
    except Exception:
        pass
    return meta


# ─── LLM 解析简历字段 ────────────────────────────────────────────────────────────

def _llm_parse_cv_fields(cv_text, filename="", pdf_title="", pdf_author=""):
    # type: (str, str, str, str) -> dict
    """
    调用 DashScope LLM，从中文简历文本中提取候选人关键字段，返回字段字典。
    filename: 飞书文件名（如"张三_复旦大学_简历.pdf"）
    pdf_title: PDF 文档属性中的 Title 字段
    pdf_author: PDF 文档属性中的 Author 字段
    """
    _load_dashscope_key()
    if not _DASHSCOPE_KEY:
        raise RuntimeError("DashScope API Key 未配置，请检查工作区 config/dashscope-config.json 或环境变量")

    # 构建辅助上下文（文件名、PDF标题、作者）
    extra_context_lines = []
    if filename:
        extra_context_lines.append("- 文件名：{}".format(filename))
    if pdf_title:
        extra_context_lines.append("- PDF文档标题（属性）：{}".format(pdf_title))
    if pdf_author:
        extra_context_lines.append("- PDF文档作者（属性）：{}".format(pdf_author))

    extra_context = ""
    if extra_context_lines:
        extra_context = (
            "\n附加参考信息（来自文件名和PDF属性，可辅助识别姓名、职位等）：\n"
            + "\n".join(extra_context_lines)
            + "\n"
        )

    # 截取前 4000 字，避免超出 token 限制
    truncated = cv_text[:4000]

    prompt = (
        "你是一个专业的招聘助手，请从以下中文候选人简历文本中提取关键信息，严格以 JSON 格式输出。\n\n"
        "需要提取的字段：\n"
        "- name: 候选人姓名（字符串）\n"
        "- email: 邮箱地址（字符串，若无则填 null）\n"
        "- phone: 手机号（字符串，若无则填 null）\n"
        "- wechat: 微信号（字符串，若无则填 null）\n"
        "- position: 应聘职位（字符串，若简历未明确填写则填 null）\n"
        "- education: 最高学历，只填「本科」「硕士」「博士」之一（字符串）\n"
        "- school: 最高学历所在院校名称（字符串）\n"
        "- work_years: 工作年限，整数，应届生或实习生填 0（整数）\n"
        "- source: 来源渠道，若简历未提及则填 null（字符串）\n"
        "- resume_summary: 100字以内的候选人背景摘要，包含核心技能、项目经历和亮点（字符串）\n"
        "{extra}"
        "\n简历文本：\n"
        "```\n{text}\n```\n\n"
        "注意：\n"
        "- 只返回 JSON 对象，不要任何其他内容\n"
        "- 所有字段必须存在，无法提取的填 null\n"
        "- resume_summary 用中文，客观简洁，不要夸大\n"
        "- 邮箱格式必须是有效邮箱（含@），否则填 null\n"
        "- 文件名和PDF属性仅作参考，简历正文内容优先"
    ).format(extra=extra_context, text=truncated)

    payload = json.dumps({
        "model": _LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }).encode("utf-8")

    req = urllib.request.Request(
        _DASHSCOPE_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + _DASHSCOPE_KEY,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        content = result["choices"][0]["message"]["content"].strip()
        # 去除 Markdown 代码块包裹（```json ... ```）
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        fields = json.loads(content)
        return fields
    except Exception as e:
        raise RuntimeError("LLM 解析失败: {}".format(str(e)[:200]))


# ─── 格式化预览输出 ──────────────────────────────────────────────────────────────

def _format_preview(fields, pdf_path=None):
    # type: (dict) -> str
    """
    将解析出的字段格式化为【新候选人】模板预览字符串，供 OC 展示给 HR 确认。
    同时输出可直接执行的 cmd_new_candidate.py 命令，供确认后使用。
    """
    def v(key, default=""):
        val = fields.get(key)
        return str(val) if val is not None else default

    lines = [
        "[简历解析预览]",
        "以下信息由 LLM 从 PDF 自动提取，请 HR 核对后回复「确认录入」：",
        "━━━━━━━━━━━━━━━━━━━━",
        "【新候选人】",
        "姓名：{}".format(v("name", "（未识别）")),
        "邮箱：{}".format(v("email", "（未识别，请手动补充）")),
        "电话：{}".format(v("phone", "")),
        "微信：{}".format(v("wechat", "")),
        "应聘职位：{}".format(v("position", "")),
        "学历：{}".format(v("education", "")),
        "毕业院校：{}".format(v("school", "")),
        "工作年限：{}".format(v("work_years", "")),
        "来源渠道：{}".format(v("source", "")),
        "简历摘要：{}".format(v("resume_summary", "")),
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "如信息正确，请回复「确认录入」。",
        "如需修改，请直接告知要改的字段和新值（例如：「邮箱改为 xx@xx.com」），我会更新后重新展示。",
    ]

    # 附上确认后 OC 可执行的命令（方便 OC 确认时直接用）
    # 以 JSON 格式隐藏在末尾，供 OC 提取
    cmd_args = []
    if fields.get("name"):
        cmd_args.append("--name \"{}\"".format(fields["name"]))
    if fields.get("email"):
        cmd_args.append("--email \"{}\"".format(fields["email"]))
    if fields.get("phone"):
        cmd_args.append("--phone \"{}\"".format(fields["phone"]))
    if fields.get("wechat"):
        cmd_args.append("--wechat \"{}\"".format(fields["wechat"]))
    if fields.get("position"):
        cmd_args.append("--position \"{}\"".format(fields["position"]))
    if fields.get("education"):
        cmd_args.append("--education \"{}\"".format(fields["education"]))
    if fields.get("school"):
        cmd_args.append("--school \"{}\"".format(fields["school"]))
    if fields.get("work_years") is not None:
        cmd_args.append("--work-years {}".format(fields["work_years"]))
    if fields.get("source"):
        cmd_args.append("--source \"{}\"".format(fields["source"]))
    if fields.get("resume_summary"):
        cmd_args.append("--resume-summary \"{}\"".format(
            fields["resume_summary"].replace('"', '\\"')))
    if pdf_path:
        cmd_args.append("--cv-path \"{}\"".format(pdf_path.replace('"', '\\"')))

    lines.append("")
    lines.append("[OC_CMD_ON_CONFIRM]")
    lines.append("uv run python3 scripts/intake/cmd_new_candidate.py {}".format(" ".join(cmd_args)))

    return "\n".join(lines)


# ─── 主逻辑 ────────────────────────────────────────────────────────────────────
# ⚠️  此脚本已废弃，请使用 cmd_ingest_cv.py（自动判断新/旧候选人）
# 此 main() 仅保留以防误调用时给出明确提示；模块函数供 cmd_ingest_cv.py import 使用。

def main(argv=None):
    print(
        "ERROR: cmd_parse_cv.py 已废弃。\n"
        "请改用 cmd_ingest_cv.py，它会自动判断候选人是否已在库中：\n"
        "  uv run python3 scripts/intake/cmd_ingest_cv.py --pdf-path <路径> --filename <文件名>",
        file=sys.stderr,
    )
    return 1


def _main_legacy(argv=None):
    p = argparse.ArgumentParser(description="解析飞书中的候选人简历 PDF")
    p.add_argument("--message-id",       default="", help="飞书文件消息 ID（直接提供可加速下载）")
    p.add_argument("--file-key",         default="", help="飞书文件 key（从消息附件中获取，必填）")
    p.add_argument("--reply-message-id", default="", help="用户回复文件时的当前消息 ID，脚本会自动反查文件消息 ID")
    p.add_argument("--pdf-path",         default="", help="本地 PDF 文件路径（用于本地测试）")
    p.add_argument("--filename",         default="", help="PDF 文件名（如「张三_简历.pdf」），作为辅助上下文传给 LLM")
    args = p.parse_args(argv or sys.argv[1:])

    if not args.pdf_path and not args.file_key:
        print(
            "ERROR: 请提供 --file-key（飞书下载，message-id 可选）"
            "或 --pdf-path（本地文件）",
            file=sys.stderr,
        )
        return 1

    # 文件名：优先用 --filename 参数，本地模式则从路径提取
    filename = args.filename.strip()
    if not filename and args.pdf_path:
        filename = os.path.basename(args.pdf_path)

    # 步骤 1：获取 PDF 内容
    if args.pdf_path:
        try:
            with open(args.pdf_path, "rb") as f:
                pdf_bytes = f.read()
            print("[1/4] 已读取本地 PDF：{}（{} 字节）".format(args.pdf_path, len(pdf_bytes)),
                  file=sys.stderr)
        except Exception as e:
            print("ERROR: 无法读取 PDF 文件：{}".format(e), file=sys.stderr)
            return 1
    else:
        try:
            print("[1/4] 正在从飞书下载 PDF（file_key={}）...".format(args.file_key),
                  file=sys.stderr)
            pdf_bytes = _download_pdf_from_feishu(args.message_id, args.file_key, args.reply_message_id)
            print("      下载成功，{} 字节".format(len(pdf_bytes)), file=sys.stderr)
        except Exception as e:
            print("ERROR: 飞书下载失败：{}".format(e), file=sys.stderr)
            return 1

    # 步骤 2：提取 PDF 元数据（Title、Author）
    print("[2/4] 正在提取 PDF 元数据...", file=sys.stderr)
    meta = _extract_pdf_metadata(pdf_bytes)
    if meta["title"]:
        print("      PDF 标题：{}".format(meta["title"]), file=sys.stderr)
    if meta["author"]:
        print("      PDF 作者：{}".format(meta["author"]), file=sys.stderr)
    if not meta["title"] and not meta["author"]:
        print("      无文档属性元数据", file=sys.stderr)

    # 步骤 3：提取正文文本
    try:
        print("[3/4] 正在提取 PDF 正文文本...", file=sys.stderr)
        cv_text = _extract_text_from_pdf(pdf_bytes)
        if not cv_text.strip():
            print("ERROR: PDF 中未提取到文字内容，可能是扫描图片版简历，暂不支持", file=sys.stderr)
            return 1
        print("      提取成功，共 {} 字".format(len(cv_text)), file=sys.stderr)
    except Exception as e:
        print("ERROR: PDF 文本提取失败：{}".format(e), file=sys.stderr)
        return 1

    # 步骤 4：LLM 解析字段（传入文件名 + PDF 元数据作为辅助上下文）
    try:
        print("[4/4] 正在调用 LLM 解析字段（文件名={} PDF标题={} PDF作者={}）...".format(
            filename or "无", meta["title"] or "无", meta["author"] or "无"), file=sys.stderr)
        fields = _llm_parse_cv_fields(
            cv_text,
            filename=filename,
            pdf_title=meta["title"],
            pdf_author=meta["author"],
        )
        print("      解析成功", file=sys.stderr)
    except Exception as e:
        print("ERROR: LLM 解析失败：{}".format(e), file=sys.stderr)
        return 1

    # 输出格式化预览（带上本地 PDF 路径，用于确认后写入 cv_path）
    local_pdf_path = args.pdf_path if args.pdf_path else ""
    preview = _format_preview(fields, pdf_path=local_pdf_path or None)
    print(preview)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
