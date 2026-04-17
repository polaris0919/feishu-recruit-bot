"""Compatibility shim for installed `cmd_exam_result` imports."""

import sys

from exam import cmd_exam_result as _impl

sys.modules[__name__] = _impl
