"""Compatibility shim for installed `core_state` imports."""

import sys

from lib import core_state as _impl

sys.modules[__name__] = _impl
