"""Compatibility shim for installed `exam_prereview` imports."""

import sys

from exam import exam_prereview as _impl

sys.modules[__name__] = _impl
