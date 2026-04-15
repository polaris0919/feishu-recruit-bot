"""Compatibility shim for installed `side_effect_guard` imports."""

import sys

from lib import side_effect_guard as _impl

sys.modules[__name__] = _impl
