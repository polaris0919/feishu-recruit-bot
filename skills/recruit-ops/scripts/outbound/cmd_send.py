#!/usr/bin/env python3
"""outbound/cmd_send.py —— v3.3 出站邮件【唯一】发送入口。

【三种模式】
  模板模式：
      --template T [--vars k=v ...]
      渲染 email_templates/<T>.txt → SMTP 发送

  自由文本模式：
      --subject S (--body "..." | --body-file PATH)
      [--cleanup-body-file/--no-cleanup-body-file]   默认 cleanup ON
      不渲染模板；body 直接作为正文。用于 agent 起草+老板确认后发送。

  缓存草稿模式（v3.4 Phase 1）：
      --use-cached-draft EMAIL_ID
      从 talent_emails(EMAIL_ID).ai_payload.draft 读 LLM 起草的回信当 body；
      自动设 subject = "Re: " + 原邮件 subject、in_reply_to = 原 message_id、
      references = 原 references_chain（线程头自动续上）。
      老板确认 inbox/cmd_analyze 推的飞书卡片后，一行命令发出回复。
      [--override-subject S]      覆盖默认的 "Re: 原 subject"
      [--cc CC]                   叠加抄送

【共用参数】
  --talent-id X   必填；收件人取自 talents.candidate_email（不允许 --to）
  --in-reply-to / --references   线程头（自由文本模式回信时必带）
  --cc CC                        额外抄送
  --attach FILE                  附件文件路径（可重复）。每个 ≤ 20MB；不存在 / 超大直接 fail-fast

【绝对零业务副作用】
  - 不动 talents.current_stage
  - 不动任何业务字段（exam_sent_at / round1_invite_sent_at 等都【不动】）
  - 仅写一行 talent_emails (direction='outbound')

【自验证（D5）】
  发送 + 入库后立刻 assert_email_sent(talent_id, message_id)；
  失败 → cli_wrapper 推飞书 + 非零退出。

【调用示例】
  # 模板模式：发一面邀请
  PYTHONPATH=scripts python3 -m outbound.cmd_send \\
    --talent-id t_abc123 \\
    --template round1_invite \\
    --vars candidate_name="张三" round1_time="2026-04-22 14:00"

  # 自由文本模式：回候选人的邮件
  PYTHONPATH=scripts python3 -m outbound.cmd_send \\
    --talent-id t_abc123 \\
    --subject "Re: 关于薪资的疑问" \\
    --body-file /tmp/draft_zhangsan.txt \\
    --in-reply-to '<abc@mail.example.com>'
"""
from __future__ import print_function

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional, Tuple

from lib import config as _cfg
from lib import smtp_sender, talent_db
from lib.cli_wrapper import run_with_self_verify, UserInputError
from lib.self_verify import assert_email_sent


# ─── body 兜底 normalize（v3.5.13 / 2026-04-22）────────────────────────────────
#
# 事故背景：飞书侧 agent 起草的"入职时间确认"邮件，body 里含字面 `\n\n` 与
# `**...**`，原样进 SMTP（plain text），收件人看到反斜杠 + 字面星号，飞书卡
# 上却渲染成漂亮换行 + 粗体 → 双方信息不对称、老板不察觉就发出去了。
#
# 根因：argparse 不会解码 `\n` 转义（`--body "a\nb"` 拿到的是 4 个字符），
# SMTP 也不渲染 markdown。上游（agent / Hermes 飞书侧）把"看起来像 markdown
# 的人话"原封不动当 body 传进来。
#
# 修复：cmd_send 是出站邮件唯一入口，在这做兜底归一化最稳——所有上游
# （--body / --body-file / --use-cached-draft / --template 渲染结果）一律走
# 同一条路径。`--no-body-normalize` 是安全阀，万一以后真要发字面 `\n` 字符
# 串可以关掉。
#
# 保守边界：只剥**双星号粗体** + **下划线粗体** + **行首标题前缀**；不碰
# 单星号斜体 / 反引号代码 / 列表项 —— 中文邮件里这些标记容易误伤。

# `**X**` / `__X__`：贪婪匹配但不跨段（段内最近一对收尾即可）
_MD_BOLD_STAR = re.compile(r"\*\*([^*\n]+?)\*\*")
_MD_BOLD_UNDER = re.compile(r"__([^_\n]+?)__")
# 行首 `# ` / `## ` / `### ` 标题
_MD_HEADER = re.compile(r"^(#{1,6})\s+", re.MULTILINE)


def _normalize_body(raw):
    # type: (str) -> Tuple[str, dict]
    """归一化 SMTP body，返回 (normalized_text, stats_dict)。

    stats: {"esc_n": 转义解码处数, "esc_t": 同上, "bold": 剥粗体处数,
            "header": 剥标题处数}。stats 全 0 表示啥都没改。
    """
    if not raw:
        return raw, {"esc_n": 0, "esc_t": 0, "bold": 0, "header": 0}

    text = raw
    stats = {"esc_n": 0, "esc_t": 0, "bold": 0, "header": 0}

    # ① 解码反斜杠转义。注意顺序：先 \r\n 后 \r 后 \n，避免重复解码。
    #    同时只解码"已出现过实际换行字符"之外的字面转义；如果 raw 里既有真
    #    换行又有字面 \n，两种都按"上游想表达换行"处理（统一成真换行）。
    n_before = text.count("\\n") + text.count("\\r")
    if n_before:
        text = text.replace("\\r\\n", "\n").replace("\\r", "\n").replace("\\n", "\n")
        stats["esc_n"] = n_before
    n_tab = text.count("\\t")
    if n_tab:
        text = text.replace("\\t", "\t")
        stats["esc_t"] = n_tab

    # ② 剥 markdown 粗体（双星号 / 双下划线）
    def _strip_bold_star(m):
        stats["bold"] += 1
        return m.group(1)
    text = _MD_BOLD_STAR.sub(_strip_bold_star, text)
    text = _MD_BOLD_UNDER.sub(_strip_bold_star, text)

    # ③ 剥行首 ATX 标题前缀（# / ## / ###）
    def _strip_header(m):
        stats["header"] += 1
        return ""
    text = _MD_HEADER.sub(_strip_header, text)

    return text, stats


def _maybe_normalize_body_inplace(body, label, enabled):
    # type: (str, str, bool) -> str
    """对外的薄包装：跑 normalize，把 stats 打到 stderr 留 journal。"""
    if not enabled or not body:
        return body
    normalized, stats = _normalize_body(body)
    changed = sum(stats.values())
    if changed:
        print(
            "[outbound.cmd_send] body 正规化（{}）：解码 \\n×{}, \\t×{}, "
            "剥粗体×{}, 剥标题×{}（用 --no-body-normalize 可关闭）".format(
                label, stats["esc_n"], stats["esc_t"], stats["bold"], stats["header"]),
            file=sys.stderr)
    return normalized


# ─── stage → context 推断 ─────────────────────────────────────────────────────

# talent_emails.context 取值（与 lib.talent_db._EMAIL_VALID_CONTEXTS 保持同步）：
#   exam / round1 / round2 / followup / intake / rejection / unknown
# 这张表是 stage → 默认 context 的兜底；显式 --context 优先（auto_reject
# 用 --context rejection 覆盖此表）。
# v3.6 (2026-04-27/28)：删除 OFFER_HANDOFF / *_DONE_REJECT_DELETE 相关行。
# v3.5.11 (2026-04-22)：注释加上 rejection——它是 auto_reject.cmd_scan_exam_timeout
# 显式传入的 context；本表里没有 stage 默认映射到 rejection 的，是合理的（拒信只
# 由 auto_reject 路径主动发起）。
_STAGE_TO_CONTEXT = {
    "NEW": "intake",
    "ROUND1_SCHEDULING": "round1",
    "ROUND1_SCHEDULED": "round1",
    "EXAM_SENT": "exam",
    "EXAM_REVIEWED": "exam",
    "EXAM_REJECT_KEEP": "exam",
    "WAIT_RETURN": "round2",
    "ROUND2_SCHEDULING": "round2",
    "ROUND2_SCHEDULED": "round2",
    "ROUND2_DONE_REJECT_KEEP": "round2",
    "POST_OFFER_FOLLOWUP": "followup",
}


def _infer_context(stage):
    # type: (Optional[str]) -> str
    return _STAGE_TO_CONTEXT.get(stage or "", "unknown")


# ─── 参数解析工具 ─────────────────────────────────────────────────────────────

def _parse_kv_pairs(items):
    # type: (list) -> dict
    """把 ['k1=v1', 'k2=v2'] 解析成 dict。值里允许 = 号。"""
    out = {}
    for it in items or []:
        if "=" not in it:
            raise UserInputError("--vars 参数格式必须是 KEY=VALUE，拿到: {!r}".format(it))
        k, v = it.split("=", 1)
        k = k.strip()
        if not k:
            raise UserInputError("--vars 的 KEY 不能为空: {!r}".format(it))
        out[k] = v
    return out


def _read_body_source(args):
    # type: (argparse.Namespace) -> Tuple[str, Optional[str]]
    """返回 (body_text, body_file_path_to_cleanup_or_None)。"""
    if args.body is not None:
        return args.body, None
    if args.body_file:
        path = os.path.abspath(args.body_file)
        if not os.path.isfile(path):
            raise UserInputError("--body-file 不存在: {}".format(path))
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        cleanup_path = path if args.cleanup_body_file else None
        return content, cleanup_path
    raise UserInputError("自由文本模式需要 --body 或 --body-file")


# ─── 主流程 ───────────────────────────────────────────────────────────────────

def _build_parser():
    p = argparse.ArgumentParser(
        prog="outbound.cmd_send",
        description="v3.3 出站邮件唯一入口（模板 / 自由文本两种模式）",
    )
    p.add_argument("--talent-id", required=True)

    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--template", help="模板模式：email_templates/<T>.txt")
    mode.add_argument("--subject",
                      help="自由文本模式：邮件主题（必须配 --body 或 --body-file）")
    mode.add_argument("--use-cached-draft", dest="use_cached_draft",
                      help="缓存草稿模式：从指定 inbound 邮件的 ai_payload.draft 读回信")

    p.add_argument("--vars", nargs="*", default=[],
                   help="模板变量：KEY1=VAL1 KEY2=VAL2 ...")

    p.add_argument("--body", help="自由文本模式：正文（命令行直接传）")
    p.add_argument("--body-file",
                   help="自由文本模式：从文件读正文（推荐，避免 shell 转义）")
    p.add_argument("--cleanup-body-file", dest="cleanup_body_file",
                   action="store_true", default=True,
                   help="发送成功后删除 --body-file 临时文件（默认 ON）")
    p.add_argument("--no-cleanup-body-file", dest="cleanup_body_file",
                   action="store_false")

    # v3.5.13：默认开启 body 兜底归一化（解码字面 \n / \t、剥 markdown 粗体 +
    # 行首标题前缀）。极端情况想发字面 `\n` 字符串可关掉。详见模块顶部
    # _normalize_body 注释。
    p.add_argument("--body-normalize", dest="body_normalize",
                   action="store_true", default=True,
                   help="对 body 做兜底归一化（解码字面 \\n / \\t、剥 markdown 粗体 + 行首标题）；默认 ON")
    p.add_argument("--no-body-normalize", dest="body_normalize",
                   action="store_false",
                   help="关闭兜底归一化（极少用：真要发字面 \\n 字符串时）")

    p.add_argument("--in-reply-to", help="线程头：原邮件 Message-ID")
    p.add_argument("--references", help="线程头：References 链")
    p.add_argument("--cc", help="抄送地址")
    p.add_argument("--attach", action="append", default=[],
                   metavar="FILE",
                   help="附件文件路径，可重复（如 onboarding 资料 docx）。每个 ≤ 20MB")
    p.add_argument("--override-subject", dest="override_subject",
                   help="--use-cached-draft 模式下覆盖默认的 'Re: 原 subject'")
    p.add_argument("--from-name",
                   help="发件人显示名（默认取 config.email_smtp.from_name）")

    p.add_argument("--context",
                   choices=list(set(_STAGE_TO_CONTEXT.values()) | {"unknown", "rejection"}),
                   help="覆盖按 stage 推断的 talent_emails.context"
                        "（rejection 用于 auto_reject 发拒信）")
    p.add_argument("--dry-run", action="store_true",
                   help="渲染 + 校验 + 模拟入库，但不真的发邮件、不写 talent_emails")
    p.add_argument("--json", action="store_true", help="结构化 JSON 输出")
    return p


def _resolve_recipient(talent_id):
    # type: (str) -> Tuple[str, str, str]
    """从 DB 取 (candidate_email, candidate_name, current_stage)。"""
    snap = talent_db.get_one(talent_id)
    if not snap:
        raise UserInputError("候选人 {} 不存在".format(talent_id))
    email = (snap.get("candidate_email") or "").strip()
    if not email or "@" not in email:
        raise UserInputError(
            "候选人 {} 的 candidate_email 缺失或非法: {!r}".format(talent_id, email))
    name = snap.get("candidate_name") or ""
    stage = snap.get("current_stage") or snap.get("stage") or ""
    return email, name, stage


def _do_send(args):
    # type: (argparse.Namespace) -> int
    talent_id = args.talent_id

    to_email, candidate_name, stage = _resolve_recipient(talent_id)
    context = args.context or _infer_context(stage)

    # ── 渲染 / 取正文 ────────────────────────────────────────────────────
    cleanup_path = None
    in_reply_to = args.in_reply_to
    references = args.references

    if args.template:
        try:
            from email_templates import renderer
            from email_templates.constants import COMPANY
        except Exception as e:
            raise RuntimeError("加载 email_templates 失败: {}".format(e))
        try:
            template_vars = _parse_kv_pairs(args.vars)
            template_vars.setdefault("candidate_name", candidate_name)
            template_vars.setdefault("company", COMPANY)
            template_vars.setdefault("talent_id", talent_id)
            subject, body = renderer.render(args.template, **template_vars)
        except KeyError as e:
            raise UserInputError(
                "模板 {} 缺变量: {}（请用 --vars 提供）".format(args.template, e))
        template_label = args.template
    elif args.use_cached_draft:
        # v3.4 Phase 1：从指定 inbound 邮件 ai_payload.draft 读草稿
        src_email = talent_db.fetch_email(args.use_cached_draft)
        if not src_email:
            raise UserInputError(
                "--use-cached-draft 找不到 email_id={!r}".format(args.use_cached_draft))
        if str(src_email.get("talent_id") or "") != talent_id:
            raise UserInputError(
                "email {} 属于 talent {}，与 --talent-id {} 不一致".format(
                    args.use_cached_draft, src_email.get("talent_id"), talent_id))
        if str(src_email.get("direction") or "") != "inbound":
            raise UserInputError(
                "--use-cached-draft 仅支持 inbound 邮件，但 email {} direction={!r}".format(
                    args.use_cached_draft, src_email.get("direction")))
        ai_payload = src_email.get("ai_payload") or {}
        if isinstance(ai_payload, str):
            try:
                ai_payload = json.loads(ai_payload)
            except Exception:
                ai_payload = {}
        draft = (ai_payload or {}).get("draft") or ""
        if not draft.strip():
            raise UserInputError(
                "email {} 的 ai_payload 里没有 draft 字段；请确认该邮件已被 inbox.cmd_analyze "
                "走过 post_offer_followup prompt。".format(args.use_cached_draft))
        body = draft
        # subject：默认 "Re: 原 subject"（去重避免 "Re: Re: ..."）
        orig_subject = (src_email.get("subject") or "").strip()
        if args.override_subject:
            subject = args.override_subject
        elif orig_subject.lower().startswith("re:"):
            subject = orig_subject
        else:
            subject = "Re: {}".format(orig_subject) if orig_subject else "Re: (无主题)"
        # 线程头：自动续上原 Message-ID 与 References
        if not in_reply_to:
            in_reply_to = src_email.get("message_id")
        if not references:
            old_refs = (src_email.get("references_chain") or "").strip()
            old_msgid = (src_email.get("message_id") or "").strip()
            references = " ".join(filter(None, [old_refs, old_msgid])).strip() or None
        template_label = "cached_draft"
    else:
        if not args.subject:
            raise UserInputError("自由文本模式需要 --subject")
        subject = args.subject
        body, cleanup_path = _read_body_source(args)
        template_label = "freeform"

    # ── body 兜底归一化（v3.5.13）────────────────────────────────────────
    # 三种模式都走同一道：解码字面 \n / \t、剥 markdown 粗体 + 行首标题。
    # 模板渲染出来的正文本来就是真换行无 markdown，跑一遍 stats 全 0 几乎零成本；
    # cached_draft（LLM 起草）和 freeform（飞书侧 agent 起草）才是真正受益的两路。
    body = _maybe_normalize_body_inplace(body, template_label, args.body_normalize)

    # ── 附件预校验（dry-run 也跑，让问题在演练阶段就暴露）────────────────
    # 顺序：先收 --attach 手传的，再追加模板默认附件（v3.5.10：onboarding_offer
    # 自动带实习协议 + 入职登记表，agent 不必再手动 --attach）。两条来源都走同
    # 一套 size / 存在性校验。
    attach_paths = []  # type: list
    attach_meta = []   # type: list

    def _add_attachment(raw_path, auto):
        ap = os.path.abspath(raw_path)
        if not os.path.isfile(ap):
            raise UserInputError(
                "{} 文件不存在: {}".format(
                    "模板默认附件" if auto else "--attach", ap))
        size = os.path.getsize(ap)
        if size > 20 * 1024 * 1024:
            raise UserInputError(
                "{} 超过 20MB 上限 ({} bytes): {}".format(
                    "模板默认附件" if auto else "--attach", size, ap))
        attach_paths.append(ap)
        attach_meta.append({
            "path": ap,
            "name": os.path.basename(ap),
            "size": size,
            "auto": auto,
        })

    for a in (args.attach or []):
        _add_attachment(a, auto=False)

    if args.template:
        try:
            from email_templates import auto_attachments as _auto_att
            for p in _auto_att.auto_attachments_for(args.template):
                # 防重：如果 agent 已经手动 --attach 了同一个文件就不再重复追加
                if any(m["path"] == str(p) for m in attach_meta):
                    continue
                _add_attachment(str(p), auto=True)
        except RuntimeError as e:
            # 默认附件文件缺失属于配置事故，按 UserInputError 让 cli_wrapper 转发
            raise UserInputError(str(e))

    # ── SMTP 发送 ────────────────────────────────────────────────────────
    smtp_cfg = _cfg.get("email_smtp") or {}
    from_name = args.from_name or (smtp_cfg.get("from_name") or smtp_cfg.get("smtp", {}).get("from_name"))

    if args.dry_run:
        message_id = "<dry-run-{}@local>".format(int(datetime.now(timezone.utc).timestamp()))
        attach_repr = "; attachments={}".format(
            [("{} (auto)".format(m["name"]) if m.get("auto") else m["name"]) for m in attach_meta]
        ) if attach_meta else ""
        print("[cmd_send] DRY-RUN 渲染 OK：to={} subject={!r}{}".format(to_email, subject, attach_repr),
              file=sys.stderr)
    else:
        message_id = smtp_sender.send_email_with_threading(
            to_email=to_email,
            subject=subject,
            body=body,
            in_reply_to=in_reply_to,
            references=references,
            cc=args.cc,
            from_name=from_name,
            normalize_subject=False,  # subject 由调用方/模板已确定，不要二次加 "Re: "
            attachments=attach_paths or None,
        )

    # ── 入 talent_emails ─────────────────────────────────────────────────
    smtp_user = (smtp_cfg.get("from_email")
                 or smtp_cfg.get("username")
                 or smtp_cfg.get("smtp", {}).get("from_email")
                 or smtp_cfg.get("smtp", {}).get("username")
                 or "unknown@local")

    body_excerpt = body[:500] if body else None
    sent_at_utc = datetime.now(timezone.utc)
    # ISO-8601 with timezone：上游 agent chain（lib.run_chain 串起来的
    # outbound.cmd_send → talent.cmd_update --set round1_invite_sent_at=__NOW__
    # 等链路）直接拿这个值作为 round1_invite_sent_at / round2_invite_sent_at 的源
    # （v3.4 Phase 0.2 引入；v3.5 wrapper 已下线）
    sent_at_iso = sent_at_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    if args.dry_run:
        # dry-run 路径：不写 talent_emails，也不做 self-verify；仅渲染/校验演练
        email_id = None
    else:
        email_id = talent_db.insert_email_if_absent(
            talent_id=talent_id,
            message_id=message_id,
            direction="outbound",
            context=context,
            sender=smtp_user,
            sent_at=sent_at_utc,
            subject=subject,
            in_reply_to=in_reply_to,
            references_chain=references,
            recipients=[to_email] + ([args.cc] if args.cc else []),
            body_full=body,
            body_excerpt=body_excerpt,
            stage_at_receipt=stage,
            initial_status="auto_processed",
            template=template_label,
        )

        # ── 自验证（D5）───────────────────────────────────────────────────
        assert_email_sent(talent_id, message_id)

    # ── 清理临时 body 文件（D4）──────────────────────────────────────────
    cleanup_done = False
    if cleanup_path and not args.dry_run:
        try:
            os.remove(cleanup_path)
            cleanup_done = True
        except OSError as e:
            print("[cmd_send] 清理 body-file 失败 path={} err={}".format(cleanup_path, e),
                  file=sys.stderr)

    # ── 输出 ─────────────────────────────────────────────────────────────
    result = {
        "ok": True,
        "talent_id": talent_id,
        "to_email": to_email,
        "subject": subject,
        "message_id": message_id,
        "email_id": email_id,
        "sent_at": sent_at_iso,
        "context": context,
        "template": template_label,
        "dry_run": bool(args.dry_run),
        "cleanup_body_file": cleanup_done,
        "attachments": [{"name": m["name"], "size": m["size"], "auto": m.get("auto", False)}
                        for m in attach_meta],
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("[cmd_send] OK talent={} to={} template={} message_id={} email_id={}".format(
            talent_id, to_email, template_label, message_id, email_id))
    return 0


def main(argv=None):
    args = _build_parser().parse_args(argv)
    return _do_send(args)


if __name__ == "__main__":
    run_with_self_verify("outbound.cmd_send", main)
