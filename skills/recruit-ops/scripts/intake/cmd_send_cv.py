#!/usr/bin/env python3
from __future__ import print_function

"""
发送候选人简历 PDF 到飞书。

用法：
  python3 intake/cmd_send_cv.py --talent-id t_abc123
  python3 intake/cmd_send_cv.py --name 张三
  python3 intake/cmd_send_cv.py --name 张三 --to hr     # 发给 HR（默认发给老板）
"""
import argparse
import json
import mimetypes
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
import uuid

try:
    import urllib.request as _urllib_req
    import urllib.error as _urllib_err
except ImportError:
    _urllib_req = None
    _urllib_err = None

import config as _cfg
from core_state import get_tdb

FEISHU_API = "https://open.feishu.cn/open-apis"


# ─── 飞书鉴权 ─────────────────────────────────────────────────────────────────

def _get_feishu_credentials():
    app_id = (_cfg.get("feishu", "app_id") or "").strip()
    app_secret = (_cfg.get("feishu", "app_secret") or "").strip()
    if app_id and app_secret:
        return app_id, app_secret
    raise RuntimeError("未配置飞书应用凭据，请检查 FEISHU_APP_ID / FEISHU_APP_SECRET 或 openclaw 配置。")


def _get_tenant_token():
    app_id, app_secret = _get_feishu_credentials()
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = _urllib_req.Request(
        FEISHU_API + "/auth/v3/tenant_access_token/internal",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _urllib_req.urlopen(req, timeout=10) as r:
        d = json.loads(r.read())
    if d.get("code") != 0:
        raise RuntimeError("获取飞书 token 失败: " + str(d))
    return d["tenant_access_token"]


# ─── 飞书文件上传 ──────────────────────────────────────────────────────────────

def _upload_file_to_feishu(token, file_path):
    # type: (str, str) -> str
    """
    上传本地 PDF 到飞书，返回 file_key。
    API: POST /im/v1/files  (multipart/form-data)
    """
    file_name = os.path.basename(file_path)
    # 清理文件名里的 uuid 后缀（xxx---uuid.pdf -> xxx.pdf）
    if "---" in file_name:
        clean_name = file_name.rsplit("---", 1)[0] + ".pdf"
    else:
        clean_name = file_name

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    boundary = uuid.uuid4().hex
    CRLF = b"\r\n"

    def _part_text(name, value):
        return (
            b"--" + boundary.encode() + CRLF
            + b'Content-Disposition: form-data; name="' + name.encode() + b'"' + CRLF
            + CRLF
            + value.encode("utf-8") + CRLF
        )

    def _part_file(name, filename, data):
        return (
            b"--" + boundary.encode() + CRLF
            + b'Content-Disposition: form-data; name="' + name.encode()
            + b'"; filename="' + filename.encode("utf-8") + b'"' + CRLF
            + b"Content-Type: application/pdf" + CRLF
            + CRLF
            + data + CRLF
        )

    body = (
        _part_text("file_type", "pdf")
        + _part_text("file_name", clean_name)
        + _part_file("file", clean_name, file_bytes)
        + b"--" + boundary.encode() + b"--" + CRLF
    )

    req = _urllib_req.Request(
        FEISHU_API + "/im/v1/files",
        data=body,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
        },
        method="POST",
    )
    with _urllib_req.urlopen(req, timeout=30) as r:
        result = json.loads(r.read())

    if result.get("code") != 0:
        raise RuntimeError("飞书文件上传失败: " + json.dumps(result, ensure_ascii=False))

    file_key = result["data"]["file_key"]
    return file_key, clean_name


# ─── 飞书消息发送 ──────────────────────────────────────────────────────────────

def _send_file_message(token, open_id, file_key):
    # type: (str, str, str) -> None
    if not (open_id or "").strip():
        raise RuntimeError("未配置飞书接收人 open_id，已取消发送。")
    payload = json.dumps({
        "receive_id": open_id,
        "msg_type": "file",
        "content": json.dumps({"file_key": file_key}),
    }).encode("utf-8")
    req = _urllib_req.Request(
        FEISHU_API + "/im/v1/messages?receive_id_type=open_id",
        data=payload,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with _urllib_req.urlopen(req, timeout=15) as r:
        result = json.loads(r.read())
    if result.get("code") != 0:
        raise RuntimeError("发送飞书文件消息失败: " + json.dumps(result, ensure_ascii=False))


# ─── 候选人查找 ───────────────────────────────────────────────────────────────

def _find_candidate(talent_id=None, name=None):
    # type: (str, str) -> dict
    """从 DB（或 state 文件）查找候选人，返回 cand dict（含 cv_path）。"""
    _tdb = get_tdb()
    if _tdb:
        try:
            import psycopg2
            conn = psycopg2.connect(**_tdb._conn_params())
            with conn.cursor() as cur:
                if talent_id:
                    cur.execute(
                        "SELECT talent_id, candidate_name, cv_path FROM talents WHERE talent_id = %s",
                        (talent_id,)
                    )
                else:
                    cur.execute(
                        "SELECT talent_id, candidate_name, cv_path FROM talents "
                        "WHERE candidate_name ILIKE %s ORDER BY created_at DESC LIMIT 5",
                        ("%" + name + "%",)
                    )
                rows = cur.fetchall()
            conn.close()
            if not rows:
                return None
            if len(rows) > 1 and not talent_id:
                return {"_multi": [{"talent_id": r[0], "candidate_name": r[1], "cv_path": r[2]} for r in rows]}
            r = rows[0]
            return {"talent_id": r[0], "candidate_name": r[1], "cv_path": r[2]}
        except Exception as e:
            print("[cmd_send_cv] DB 查询失败，回退到 state 文件: {}".format(e), file=sys.stderr)

    # 回退：从 JSON state 文件查找
    from core_state import load_state
    state = load_state()
    candidates = state.get("candidates", {})
    if talent_id:
        cand = candidates.get(talent_id)
        return cand if cand else None
    matches = [c for c in candidates.values()
               if name.lower() in (c.get("candidate_name") or "").lower()]
    if not matches:
        return None
    if len(matches) > 1:
        return {"_multi": [{"talent_id": c["talent_id"], "candidate_name": c.get("candidate_name"), "cv_path": c.get("cv_path")} for c in matches]}
    return matches[0]


# ─── 主逻辑 ───────────────────────────────────────────────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(description="通过飞书发送候选人简历 PDF")
    p.add_argument("--talent-id", default="", help="候选人 talent_id")
    p.add_argument("--name",      default="", help="候选人姓名（模糊匹配）")
    p.add_argument("--to",        default="boss", choices=["boss", "hr"], help="发给谁：boss（默认）或 hr")
    args = p.parse_args(argv or sys.argv[1:])

    if not args.talent_id.strip() and not args.name.strip():
        print("ERROR: 请提供 --talent-id 或 --name")
        return 1

    # 1. 查找候选人
    cand = _find_candidate(
        talent_id=args.talent_id.strip() or None,
        name=args.name.strip() or None,
    )

    if cand is None:
        name_hint = args.talent_id or args.name
        print("未找到候选人「{}」，请确认姓名或 talent_id 是否正确。".format(name_hint))
        return 1

    if "_multi" in cand:
        lines = ["找到多位匹配的候选人，请用 --talent-id 指定："]
        for c in cand["_multi"]:
            lines.append("  {} — {} （cv_path: {}）".format(
                c["talent_id"], c["candidate_name"] or "未知",
                c["cv_path"] or "无"
            ))
        print("\n".join(lines))
        return 1

    candidate_name = cand.get("candidate_name") or cand.get("talent_id") or "候选人"
    cv_path = cand.get("cv_path") or ""

    if not cv_path:
        print(
            "候选人「{}」（{}）暂无简历文件记录。\n"
            "提示：只有通过飞书 PDF 直接录入的候选人才会自动关联简历文件。".format(
                candidate_name, cand.get("talent_id", "")
            )
        )
        return 1

    if not os.path.isfile(cv_path):
        print(
            "简历文件不存在：{}\n"
            "可能已被手动删除，请重新发送简历 PDF 以更新记录。".format(cv_path)
        )
        return 1

    # 2. 获取飞书 token
    print("[1/3] 获取飞书授权...", file=sys.stderr)
    try:
        token = _get_tenant_token()
    except Exception as e:
        print("ERROR: {}".format(e))
        return 1

    # 3. 上传 PDF 到飞书
    print("[2/3] 上传简历文件「{}」...".format(os.path.basename(cv_path)), file=sys.stderr)
    try:
        file_key, clean_name = _upload_file_to_feishu(token, cv_path)
        print("      上传成功，file_key={}".format(file_key), file=sys.stderr)
    except Exception as e:
        print("ERROR: 上传失败：{}".format(e))
        return 1

    # 4. 发送文件消息
    open_id = (
        _cfg.get("feishu", "hr_open_id")
        if args.to == "hr" else
        _cfg.get("feishu", "boss_open_id")
    ) or ""
    to_label = "HR" if args.to == "hr" else "老板"
    print("[3/3] 发送文件消息给{}...".format(to_label), file=sys.stderr)
    try:
        _send_file_message(token, open_id, file_key)
    except Exception as e:
        print("ERROR: 发送失败：{}".format(e))
        return 1

    print("已通过飞书将「{}」的简历（{}）发送给{}。".format(candidate_name, clean_name, to_label))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
