Unsupported opcode: CALL_FUNCTION_EX (170)
Unsupported opcode: CALL_FUNCTION_EX (170)
# Source Generated with Decompyle++
# File: recruit_paths.cpython-312.pyc (Python 3.12)

'''Shared path helpers for running recruit-ops under Hermes.'''
from __future__ import annotations
import os
from pathlib import Path
from typing import Iterable, List
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = Path(os.path.expanduser(os.environ.get('RECRUIT_WORKSPACE_ROOT', str(SKILL_ROOT.parent.parent)))).resolve()
COMPAT_HOME = Path(os.path.expanduser(os.environ.get('RECRUIT_COMPAT_HOME', '~/.openclaw'))).resolve()

def workspace_path(*parts):
    pass
# WARNING: Decompyle incomplete


def compat_path(*parts):
    pass
# WARNING: Decompyle incomplete


def config_dir():
    raw = os.environ.get('RECRUIT_CONFIG_DIR', str(workspace_path('config')))
    return Path(os.path.expanduser(raw))


def state_path():
    raw = os.environ.get('RECRUIT_STATE_PATH', str(workspace_path('state', 'recruit_state.json')))
    return Path(os.path.expanduser(raw))


def media_inbound_dir():
    raw = os.environ.get('RECRUIT_MEDIA_INBOUND_DIR', str(workspace_path('data', 'media', 'inbound')))
    return Path(os.path.expanduser(raw))


def exam_archive_dir():
    raw = os.environ.get('RECRUIT_EXAM_ARCHIVE_DIR', str(workspace_path('data', 'exam_txt')))
    return Path(os.path.expanduser(raw))


def config_candidates(filename = None):
    paths = [
        SCRIPT_DIR / filename,
        config_dir() / filename]
    if not os.environ.get('RECRUIT_COMPAT_CONFIG_DIR'):
        os.environ.get('RECRUIT_COMPAT_CONFIG_DIR')
    compat_config_dir = ''.strip()
    if compat_config_dir:
        paths.append(Path(os.path.expanduser(compat_config_dir)) / filename)
    return paths


def first_existing(paths = None):
    for path in paths:
        if not path.is_file():
            continue
        
        return paths, path

