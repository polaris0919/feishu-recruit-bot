"""Compatibility shim for installed `daily_exam_review` imports."""

import sys

from exam import daily_exam_review as _impl

sys.modules[__name__] = _impl
