"""Compatibility shim for installed `config` imports."""

import sys

from lib import config as _impl

sys.modules[__name__] = _impl
