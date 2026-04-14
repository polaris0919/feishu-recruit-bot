Unsupported argument found for LIST_EXTEND
Unsupported opcode: CALL_INTRINSIC_1 (243)
# Source Generated with Decompyle++
# File: recruit_script_paths.cpython-312.pyc (Python 3.12)

'''招聘工作区 scripts 目录布局：lib/ 为公共模块，各子目录为分类入口脚本。'''
from __future__ import print_function
import os
__all__ = [
    'scripts_dir',
    'lib_dir',
    'subscript_path']

def scripts_dir():
    '''skills/recruit-ops/scripts 的绝对路径（lib 的父目录）。'''
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def lib_dir():
    return os.path.join(scripts_dir(), 'lib')


def subscript_path(*parts):
    '''例如 subscript_path("round1", "cmd_round1_confirm.py")。'''
    pass
# WARNING: Decompyle incomplete

