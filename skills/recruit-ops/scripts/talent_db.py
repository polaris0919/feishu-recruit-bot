"""Compatibility shim for installed `talent_db` imports."""

import sys

from lib import talent_db as _impl

sys.modules[__name__] = _impl
