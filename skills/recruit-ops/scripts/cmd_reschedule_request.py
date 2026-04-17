"""Compatibility shim for installed `cmd_reschedule_request` imports."""

import sys

from common import cmd_reschedule_request as _impl

sys.modules[__name__] = _impl
