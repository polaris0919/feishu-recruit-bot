#!/usr/bin/env python3
"""lib/cv_parser.py —— PDF 简历解析共享模块 (A4.1, v3.8.7)

═══════════════════════════════════════════════════════════════════════════════
为什么独立成一个 lib 模块
═══════════════════════════════════════════════════════════════════════════════
- intake/cmd_parse_cv.py 历史上既是 CLI 入口又是函数库, 已经在 SKILL.md
  里被标记 deprecated 但其内部 4 个 `_` 函数仍被 intake/cmd_ingest_cv.py
  跨模块 import。结果是: 老 wrapper CLI 名字还在仓库里, 但已经没有人调
  它的 main(); 仅有 4 个 utility 函数活着。
- A4.1 把这 4 个函数挪到 lib/, 让它们获得正式公开 API 地位; cmd_parse_cv.py
  本体在同一提交里整体删除。

═══════════════════════════════════════════════════════════════════════════════
本模块职责
═══════════════════════════════════════════════════════════════════════════════
- 从飞书 IM 下载 PDF 附件 (download_pdf_from_feishu)
- 从 PDF 字节流提取正文 / 元数据 (extract_text_from_pdf / extract_pdf_metadata)
- 调 DashScope LLM 把简历正文映射到候选人字段 (llm_parse_cv_fields)
- 把解析结果格式化成给 HR 的预览 + cmd_new_candidate.py 调用命令 (format_preview)

本模块不读 RECRUIT_DRY_RUN 也不做副作用 guard——caller (cmd_ingest_cv) 自己
决定 dry_run 短路。这与 cli_subprocess 的"哑执行器"原则一致。
"""
from __future__ import print_function

import io
import json
import os
import re
import sys
import urllib.error
import urllib.request

from lib.recruit_paths import config_candidates, first_existing

FEISHU_API = "https://open.feishu.cn/open-apis"
_OPENCLAW_CONFIG = first_existing(config_candidates("openclaw.json"))

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
        with open(str(_OPENCLAW_CONFIG), "r", encoding="utf-8") as f:
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
    """通过当前消息 ID 查询飞书 API, 获取被引用 (父) 消息的 message_id。

    用于: 用户回复文件消息时, 从文字回复消息 -> 反查文件消息 message_id。
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


def download_pdf_from_feishu(message_id, file_key, reply_message_id=""):
    # type: (str, str, str) -> bytes
    """从飞书 IM 下载消息中的文件附件, 返回二进制内容。

    若 message_id 为空但有 reply_message_id, 则通过查询 reply_message_id
    的父消息来获取文件 message_id。
    API: GET /im/v1/messages/{message_id}/resources/{file_key}?type=file
    """
    app_id, app_secret = _get_feishu_credentials()
    token = _get_tenant_token(app_id, app_secret)

    if not message_id and reply_message_id:
        print(
            "      [自动查找] 从回复消息 {} 反查文件消息 ID...".format(reply_message_id),
            file=sys.stderr,
        )
        try:
            parent_id = _get_parent_message_id(token, reply_message_id)
            if parent_id:
                message_id = parent_id
                print(
                    "      [自动查找] 找到文件消息 ID: {}".format(message_id),
                    file=sys.stderr,
                )
        except Exception as e:
            print("      [自动查找] 反查失败: {}".format(e), file=sys.stderr)

    if not message_id:
        raise RuntimeError(
            "无法定位飞书消息 ID。\n"
            "请提供 --reply-message-id <当前消息ID> 让脚本自动反查, \n"
            "或直接提供 --message-id <文件消息ID>。\nfile_key={}".format(file_key)
        )

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

def extract_text_from_pdf(pdf_bytes):
    # type: (bytes) -> str
    """用 pdfminer.six 从 PDF 二进制内容提取纯文本。"""
    try:
        from pdfminer.high_level import extract_text_to_fp
        from pdfminer.layout import LAParams
    except ImportError:
        raise RuntimeError(
            "未安装 pdfminer.six, 请先执行: uv sync; "
            "如仍缺失, 可执行: uv pip install 'pdfminer.six>=20231228'"
        )

    output = io.StringIO()
    input_fp = io.BytesIO(pdf_bytes)
    extract_text_to_fp(
        input_fp, output, laparams=LAParams(), output_type="text", codec=None
    )
    text = output.getvalue()
    return text.strip()


def extract_pdf_metadata(pdf_bytes):
    # type: (bytes) -> dict
    """提取 PDF 文档属性 (Title、Author 等元数据)。

    Word/WPS 另存为 PDF 时会自动写入文档标题, 用作 LLM 解析时的辅助上下文。
    返回 {"title": str, "author": str}, 字段可能为空字符串。
    """
    meta = {"title": "", "author": ""}
    try:
        from pdfminer.pdfparser import PDFParser
        from pdfminer.pdfdocument import PDFDocument

        fp = io.BytesIO(pdf_bytes)
        parser = PDFParser(fp)
        doc = PDFDocument(parser)
        info_list = doc.info
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

def llm_parse_cv_fields(cv_text, filename="", pdf_title="", pdf_author=""):
    # type: (str, str, str, str) -> dict
    """调 DashScope LLM 从中文简历文本中提取候选人关键字段, 返回字段字典。

    filename: 飞书文件名 (如"张三_复旦大学_简历.pdf")
    pdf_title: PDF 文档属性中的 Title 字段
    pdf_author: PDF 文档属性中的 Author 字段
    """
    _load_dashscope_key()
    if not _DASHSCOPE_KEY:
        raise RuntimeError(
            "DashScope API Key 未配置, 请检查工作区 config/dashscope-config.json 或环境变量"
        )

    extra_context_lines = []
    if filename:
        extra_context_lines.append("- 文件名: {}".format(filename))
    if pdf_title:
        extra_context_lines.append("- PDF文档标题 (属性): {}".format(pdf_title))
    if pdf_author:
        extra_context_lines.append("- PDF文档作者 (属性): {}".format(pdf_author))

    extra_context = ""
    if extra_context_lines:
        extra_context = (
            "\n附加参考信息 (来自文件名和PDF属性, 可辅助识别姓名、职位等): \n"
            + "\n".join(extra_context_lines)
            + "\n"
        )

    truncated = cv_text[:4000]

    prompt = (
        "你是一个专业的招聘助手, 请从以下中文候选人简历文本中提取关键信息, 严格以 JSON 格式输出。\n\n"
        "需要提取的字段: \n"
        "- name: 候选人姓名 (字符串) \n"
        "- email: 邮箱地址 (字符串, 若无则填 null) \n"
        "- phone: 手机号 (字符串, 若无则填 null) \n"
        "- wechat: 微信号 (字符串, 若无则填 null) \n"
        "- position: 应聘职位 (字符串, 若简历未明确填写则填 null) \n"
        "- education: 最高学历, 只填「本科」「硕士」「博士」之一 (字符串) \n"
        "- school: 最高学历所在院校名称 (字符串) \n"
        "- work_years: 工作年限, 整数, 应届生或实习生填 0 (整数) \n"
        "- source: 来源渠道, 若简历未提及则填 null (字符串) \n"
        "- resume_summary: 100字以内的候选人背景摘要, 包含核心技能、项目经历和亮点 (字符串) \n"
        "- has_cpp: 候选人是否会 C++ (true / false / null) 。判断依据: \n"
        "    true  = 简历的「技能/掌握语言/项目经历」里明确写了 C++ (或 cpp、C++11/14/17/20) ; \n"
        "    false = 简历明确列出了编程语言但**没有** C++ (例如只有 Python/Java/Go) ; \n"
        "    null  = 简历没列任何编程语言、或只字未提技能栈, 无法判断。\n"
        "    注意: 「C」语言不算 C++; 「C/C++」算 C++。宁可填 null 也不要瞎猜。\n"
        "{extra}"
        "\n简历文本: \n"
        "```\n{text}\n```\n\n"
        "注意: \n"
        "- 只返回 JSON 对象, 不要任何其他内容\n"
        "- 所有字段必须存在, 无法提取的填 null\n"
        "- resume_summary 用中文, 客观简洁, 不要夸大\n"
        "- 邮箱格式必须是有效邮箱 (含@) , 否则填 null\n"
        "- 文件名和PDF属性仅作参考, 简历正文内容优先"
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
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        fields = json.loads(content)
        return fields
    except Exception as e:
        raise RuntimeError("LLM 解析失败: {}".format(str(e)[:200]))


# ─── 格式化预览输出 ──────────────────────────────────────────────────────────────

def format_preview(fields, pdf_path=None):
    # type: (dict, str) -> str
    """格式化字段成【新候选人】模板预览字符串, 末尾附 cmd_new_candidate.py 命令。

    虽然 v3.6+ 的主路径 (cmd_ingest_cv) 自己构造命令, 但本函数仍保留供
    任何想直接用 PDF → 预览字符串这条捷径的脚本 / 测试调用。
    """
    def v(key, default=""):
        val = fields.get(key)
        return str(val) if val is not None else default

    lines = [
        "[简历解析预览]",
        "以下信息由 LLM 从 PDF 自动提取, 请 HR 核对后回复「确认录入」: ",
        "━━━━━━━━━━━━━━━━━━━━",
        "【新候选人】",
        "姓名: {}".format(v("name", " (未识别) ")),
        "邮箱: {}".format(v("email", " (未识别, 请手动补充) ")),
        "电话: {}".format(v("phone", "")),
        "微信: {}".format(v("wechat", "")),
        "应聘职位: {}".format(v("position", "")),
        "学历: {}".format(v("education", "")),
        "毕业院校: {}".format(v("school", "")),
        "工作年限: {}".format(v("work_years", "")),
        "是否会 C++: {}".format(
            "是" if fields.get("has_cpp") is True else
            "否" if fields.get("has_cpp") is False else
            " (未判断) "),
        "来源渠道: {}".format(v("source", "")),
        "简历摘要: {}".format(v("resume_summary", "")),
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "如信息正确, 请回复「确认录入」。",
        "如需修改, 请直接告知要改的字段和新值 (例如: 「邮箱改为 xx@xx.com」) , 我会更新后重新展示。",
    ]

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
    if fields.get("has_cpp") is True:
        cmd_args.append("--has-cpp true")
    elif fields.get("has_cpp") is False:
        cmd_args.append("--has-cpp false")
    if pdf_path:
        cmd_args.append("--cv-path \"{}\"".format(pdf_path.replace('"', '\\"')))

    lines.append("")
    lines.append("[OC_CMD_ON_CONFIRM]")
    lines.append(
        "uv run python3 scripts/intake/cmd_new_candidate.py {}".format(
            " ".join(cmd_args)
        )
    )

    return "\n".join(lines)


# ─── 向后兼容 (A4.1 transition aliases) ────────────────────────────────────────
# 历史代码用 _ 私有前缀; 新公开 API 把下划线去掉。下面保留一段时间的别名,
# 让任何还没切到新名字的外部脚本不至于立即破。v4.0 评估删。
_download_pdf_from_feishu = download_pdf_from_feishu
_extract_text_from_pdf = extract_text_from_pdf
_extract_pdf_metadata = extract_pdf_metadata
_llm_parse_cv_fields = llm_parse_cv_fields
_format_preview = format_preview
