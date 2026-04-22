"""talent/ — 候选人 CRUD + 状态机入口（v3.3）。

  cmd_add.py     —— 新建候选人（替代旧 intake/cmd_import_candidate）
  cmd_show.py    —— 查看候选人详情（read-only）
  cmd_list.py    —— 列举候选人（按 stage/搜索）
  cmd_update.py  —— 候选人状态机的【唯一】写入入口
                    natural transitions 自由跨；非常规跳转需 --force
                    支持单字段 --field K --value V 编辑
  cmd_delete.py  —— 删除候选人（默认备份到 data/deleted_archive/）

绝对不发邮件。要发邮件必须 caller 自行调 outbound/cmd_send.py。
"""
