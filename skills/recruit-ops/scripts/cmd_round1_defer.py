"""Compatibility shim for installed `cmd_round1_defer` imports."""

import sys

from round1 import cmd_round1_defer as _impl

sys.modules[__name__] = _impl
