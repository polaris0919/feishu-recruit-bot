"""模板默认上下文常量。

放在这里集中维护，防止"地址"/"公司名"硬编码在 6 个 _send_xxx 函数里
被各自漂移修改。修改公司搬家、改名都只动这一个文件。
"""
COMPANY = "示例科技公司"
LOCATION = "公司办公地址（按实际填写）"


def round_label(round_num):
    # type: (int) -> str
    """系统 round_num → 候选人语言。

    候选人邮件的语言体系：一面 = 第一轮、笔试 = 第二轮、二面 = 第三轮。
    reschedule / defer 只作用于线下面试，所以系统 round=1 → 候选人"第一轮"，
    系统 round=2 → 候选人"第三轮"。这与 round1_invite / round2_invite 模板里
    process_overview fragment 给候选人埋下的"三轮"命名一致。
    """
    if round_num == 1:
        return "第一轮"
    if round_num == 2:
        return "第三轮"
    raise ValueError("round_num 必须是 1 或 2，实际拿到: {!r}".format(round_num))
