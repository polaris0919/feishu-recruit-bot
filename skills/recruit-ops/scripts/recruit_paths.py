"""Compatibility shim for installed `recruit_paths` imports."""

import sys

from lib import recruit_paths as _impl

sys.modules[__name__] = _impl
