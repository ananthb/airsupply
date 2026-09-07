"""libairmini, reached from Python.

The protocol is not ours. psychoticbeef/libairmini has the framing, SRP-6a
pairing and the AES-256-CBC session verified against real hardware; this package
is a binding to it, pinned to a commit in libairmini.pin and built into the
add-on image as a shared library.

Reads only. See session.Session for why.
"""

from ._lib import AirminiError
from .session import READ_METHODS, Session

__all__ = ["AirminiError", "READ_METHODS", "Session"]
