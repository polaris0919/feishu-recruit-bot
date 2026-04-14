# Source Generated with Decompyle++
# File: feishu_notify.cpython-312.pyc (Python 3.12)

'''向后兼容：所有调用转发到 feishu_client。'''
from feishu_client import send_text, send_text_to_hr
import config as _cfg
if not _cfg.db_enabled():
    pass
FEISHU_BOSS_OPEN_ID = _cfg.get('feishu', 'boss_open_id')
if not _cfg.db_enabled():
    pass
FEISHU_HR_OPEN_ID = _cfg.get('feishu', 'hr_open_id')
