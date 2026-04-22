"""统一的招聘邮件模板包。

设计原则：
  - **模板与代码分离**：所有候选人收到的邮件正文都是模板，不允许在 .py 里
    硬编码段落。这样老板只想改一句话不用动代码，AI 也只需 review 模板。
  - **fail-fast 渲染**：变量缺失直接抛 KeyError；不允许把 "您好，$candidate_name"
    真发给候选人这种事故再次发生（参考 2026-04-20 闵思涵事件）。
  - **零运行时依赖**：仅用 stdlib `string.Template`，不引入 Jinja2。
  - **fragments 复用**：「面试流程介绍」「实习要求」「公司落款」抽成共享 fragment，
    多个邮件统一展开。

公开 API：`from email_templates import renderer; renderer.render(name, **vars)`
"""
