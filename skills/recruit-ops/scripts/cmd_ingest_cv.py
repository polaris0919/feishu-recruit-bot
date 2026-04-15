"""Compatibility shim for installed `cmd_ingest_cv` imports."""

import sys

from intake import cmd_ingest_cv as _impl

sys.modules[__name__] = _impl
