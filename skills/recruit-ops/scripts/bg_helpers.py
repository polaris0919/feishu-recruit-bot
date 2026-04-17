"""Compatibility shim for installed `bg_helpers` imports."""

import sys

from lib import bg_helpers as _impl

sys.modules[__name__] = _impl
