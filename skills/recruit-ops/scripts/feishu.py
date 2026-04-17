"""Compatibility shim for installed `feishu` imports."""

import sys

from lib import feishu as _impl

sys.modules[__name__] = _impl
