"""Compatibility shim for installed `cmd_round2_defer` imports."""

import sys

from round2 import cmd_round2_defer as _impl

sys.modules[__name__] = _impl
