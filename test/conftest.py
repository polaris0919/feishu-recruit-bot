"""
公共测试配置：把 scripts/ 目录加入 sys.path，确保所有测试都能 import core_state 等模块。
"""
import sys
import os

SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "skills", "recruit-ops", "scripts")
)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
