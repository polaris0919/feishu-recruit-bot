"""邮件模板渲染引擎。

模板文件约定（纯文本，UTF-8）：

    SUBJECT: 【面试邀请】示例科技公司 - 一面邀请

    您好，$candidate_name，

    ...正文...

    $$include(footer)$$

约定：
  - 第一行必须是 `SUBJECT: <主题>`，其后是一个空行，再接 body。
  - 变量用 stdlib `string.Template` 的 `$name` / `${name}` 语法。
  - 共享 fragment 用 `$$include(<fragment_name>)$$` 占一行（前后可有空白）。
    fragment 解析在变量替换之前完成，并支持嵌套（最多 5 层，防循环）。
  - 缺失变量抛 KeyError；多余变量直接忽略（允许调用方传上下文 bag）。

为什么不用 safe_substitute：candidate 看见 "$candidate_name" 是事故。
为什么不用 Jinja2：再加一个 pip 依赖换 if/for 不值，邮件模板该简单。
"""
from __future__ import annotations

import re
from pathlib import Path
from string import Template
from typing import Tuple

_TEMPLATE_ROOT = Path(__file__).resolve().parent
_FRAGMENTS_DIR = _TEMPLATE_ROOT / "_fragments"
_INCLUDE_RE = re.compile(r"^[ \t]*\$\$include\(([a-zA-Z0-9_]+)\)\$\$[ \t]*$", re.MULTILINE)
_MAX_INCLUDE_DEPTH = 5

# 模板按用途分到子目录管理（rejection / reschedule / invite / exam）。
# 调用方传扁平名（如 "rejection_generic"），下面这个查找顺序负责找到具体文件：
#   1. 直接在 _TEMPLATE_ROOT 下找 <name>.txt（向后兼容旧的扁平布局）
#   2. 递归在所有子目录里找第一个 <name>.txt（不含 _fragments / __pycache__）
# 子目录是组织文件用的，不是 namespace —— 模板名仍要全局唯一。
_LOOKUP_SUBDIRS = ("rejection", "reschedule", "invite", "exam")


class TemplateNotFoundError(FileNotFoundError):
    pass


class TemplateRenderError(RuntimeError):
    pass


def _resolve_template_path(template_name: str) -> Path:
    """按子目录约定查找模板文件，找不到抛 TemplateNotFoundError。"""
    fname = "{}.txt".format(template_name)
    direct = _TEMPLATE_ROOT / fname
    if direct.is_file():
        return direct
    for sub in _LOOKUP_SUBDIRS:
        cand = _TEMPLATE_ROOT / sub / fname
        if cand.is_file():
            return cand
    # 兜底：递归扫一遍（容忍未来新增子目录），但跳过私有目录
    for cand in _TEMPLATE_ROOT.rglob(fname):
        if any(part.startswith("_") or part == "__pycache__" for part in cand.relative_to(_TEMPLATE_ROOT).parts):
            continue
        return cand
    raise TemplateNotFoundError("模板文件不存在: {} (查找目录: {} 及子目录 {})".format(
        fname, _TEMPLATE_ROOT, list(_LOOKUP_SUBDIRS)))


def _read(path: Path) -> str:
    if not path.is_file():
        raise TemplateNotFoundError("模板文件不存在: {}".format(path))
    return path.read_text(encoding="utf-8")


def _expand_includes(text: str, depth: int = 0) -> str:
    if depth > _MAX_INCLUDE_DEPTH:
        raise TemplateRenderError(
            "fragment include 嵌套超过 {} 层（疑似循环引用）".format(_MAX_INCLUDE_DEPTH)
        )

    def _sub(m: "re.Match[str]") -> str:
        name = m.group(1)
        frag_path = _FRAGMENTS_DIR / "{}.txt".format(name)
        if not frag_path.is_file():
            raise TemplateNotFoundError(
                "fragment 不存在: {} (来源: $$include({})$$)".format(frag_path, name)
            )
        # strip 首尾换行：让模板用空白行控制 fragment 周围的间距，
        # 避免 fragment 文件末尾必带的 \n 与模板里的空行叠加成双空行。
        body = _expand_includes(frag_path.read_text(encoding="utf-8"), depth + 1)
        return body.strip("\n")

    return _INCLUDE_RE.sub(_sub, text)


def _split_subject_body(rendered: str) -> Tuple[str, str]:
    lines = rendered.splitlines()
    if not lines or not lines[0].startswith("SUBJECT:"):
        raise TemplateRenderError(
            "模板首行必须是 'SUBJECT: ...'，实际拿到: {!r}".format(lines[0] if lines else "")
        )
    subject = lines[0][len("SUBJECT:"):].strip()
    if not subject:
        raise TemplateRenderError("SUBJECT 行为空")
    # 跳过 SUBJECT 后允许有 0 或 1 行空行；其后均视为 body
    body_lines = lines[1:]
    if body_lines and body_lines[0].strip() == "":
        body_lines = body_lines[1:]
    return subject, "\n".join(body_lines).rstrip() + "\n"


def render(template_name: str, **variables) -> Tuple[str, str]:
    """渲染模板，返回 (subject, body)。

    Args:
      template_name: 模板文件名（不含 .txt 后缀），相对 email_templates/ 目录。
      **variables: 模板变量。所有 $name 占位符必须由 variables 提供，否则抛 KeyError。

    Raises:
      TemplateNotFoundError: 模板或 fragment 文件不存在。
      TemplateRenderError: SUBJECT 行格式错或 fragment 嵌套过深。
      KeyError: 模板用到的变量未传入。
    """
    raw = _read(_resolve_template_path(template_name))
    expanded = _expand_includes(raw)
    rendered = Template(expanded).substitute(**variables)
    return _split_subject_body(rendered)
