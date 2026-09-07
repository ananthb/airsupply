"""ctypes bindings for libairmini.

A thin, mechanical translation of include/airmini/airmini.h -- no policy, no
convenience, nothing clever. Everything that decides what to do lives one layer
up in session.py.

Two things about this file are worth knowing before changing it.

First, libairmini is pure and non-blocking: it owns no socket and no thread.
Bytes leave through a `send` callback we supply and arrive through
airmini_feed(). Every response and notification callback fires *synchronously*
from inside the airmini_* call that provoked it -- there is no dispatch loop
hiding in the library.

Second, ctypes callbacks are only alive as long as something Python holds a
reference to the CFUNCTYPE object. Let one be garbage collected and the C side
keeps the pointer and calls into freed memory, which fails as a crash somewhere
unrelated. Every callback constructed here is stored on the object that owns
it, deliberately.
"""

import ctypes
import ctypes.util

LIB_NAME = "libairmini.so"

# --- status codes (airmini_status) -----------------------------------------

OK = 0
ERR_INVALID = 1
ERR_STATE = 2
ERR_NOMEM = 3
ERR_TRANSPORT = 4
ERR_CRYPTO = 5
ERR_FRAMING = 6
ERR_PROTOCOL = 7
ERR_DEVICE = 8

# --- session states (airmini_state) ----------------------------------------

STATE_DISCONNECTED = 0
STATE_UNPAIRED = 1
STATE_HANDSHAKING = 2
STATE_SESSION_OPEN = 3

STATE_NAMES = {
    STATE_DISCONNECTED: "disconnected",
    STATE_UNPAIRED: "unpaired",
    STATE_HANDSHAKING: "handshaking",
    STATE_SESSION_OPEN: "session-open",
}

# --- callback prototypes ----------------------------------------------------

SEND_FN = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t
)
LOG_FN = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p)
RESPONSE_FN = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p
)
NOTIFY_FN = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p)


class AirminiConfig(ctypes.Structure):
    _fields_ = [
        ("send", SEND_FN),
        ("send_user", ctypes.c_void_p),
        ("crypto", ctypes.c_void_p),
        ("log", LOG_FN),
        ("log_user", ctypes.c_void_p),
    ]


class AirminiError(Exception):
    """A libairmini call returned non-zero."""

    def __init__(self, status, what, detail=None):
        self.status = status
        self.detail = detail
        message = f"{what}: {strerror(status)}"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)


def _load():
    lib = ctypes.CDLL(LIB_NAME)

    lib.airmini_strerror.argtypes = [ctypes.c_int]
    lib.airmini_strerror.restype = ctypes.c_char_p

    lib.airmini_create.argtypes = [ctypes.POINTER(AirminiConfig)]
    lib.airmini_create.restype = ctypes.c_void_p

    lib.airmini_destroy.argtypes = [ctypes.c_void_p]
    lib.airmini_destroy.restype = None

    lib.airmini_get_state.argtypes = [ctypes.c_void_p]
    lib.airmini_get_state.restype = ctypes.c_int

    lib.airmini_feed.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
    ]
    lib.airmini_feed.restype = ctypes.c_int

    lib.airmini_set_notify.argtypes = [ctypes.c_void_p, NOTIFY_FN, ctypes.c_void_p]
    lib.airmini_set_notify.restype = None

    # The OpenSSL backend. Built into the .so by -DAIRMINI_WITH_OPENSSL=ON, so
    # no crypto is implemented in Python -- the five-function vtable the header
    # describes is filled in by libairmini itself.
    lib.airmini_crypto_openssl.argtypes = []
    lib.airmini_crypto_openssl.restype = ctypes.c_void_p

    # Handshake.
    lib.airmini_pair.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, RESPONSE_FN, ctypes.c_void_p
    ]
    lib.airmini_pair.restype = ctypes.c_int

    lib.airmini_open_session.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, RESPONSE_FN, ctypes.c_void_p
    ]
    lib.airmini_open_session.restype = ctypes.c_int

    # Reads. These are the calls libairmini's author verified against hardware.
    for name in (
        "airmini_get_version",
        "airmini_get_datetime",
        "airmini_get_settings",
        "airmini_get_metrics",
    ):
        fn = getattr(lib, name)
        fn.argtypes = [ctypes.c_void_p, RESPONSE_FN, ctypes.c_void_p]
        fn.restype = ctypes.c_int

    lib.airmini_get.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_size_t,
        RESPONSE_FN,
        ctypes.c_void_p,
    ]
    lib.airmini_get.restype = ctypes.c_int

    lib.airmini_get_logged_data.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_size_t,
        ctypes.c_char_p,
        RESPONSE_FN,
        ctypes.c_void_p,
    ]
    lib.airmini_get_logged_data.restype = ctypes.c_int

    lib.airmini_get_history.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, RESPONSE_FN, ctypes.c_void_p
    ]
    lib.airmini_get_history.restype = ctypes.c_int

    # The generic escape hatch. No prototype is declared for airmini_set_* --
    # not as a lock, since ctypes resolves any symbol in the .so on demand, but
    # so that reaching the unverified write path is something someone has to
    # write out on purpose rather than reach for by tab-completion.
    lib.airmini_call.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, RESPONSE_FN, ctypes.c_void_p
    ]
    lib.airmini_call.restype = ctypes.c_int

    return lib


_lib = None


def lib():
    """The loaded library, loaded on first use.

    Deferred so that importing airsupply works on a machine without the .so --
    the BlueZ survey is useful on its own and should not need the protocol
    library present to run.
    """
    global _lib
    if _lib is None:
        _lib = _load()
    return _lib


def strerror(status):
    try:
        return lib().airmini_strerror(status).decode("utf-8", "replace")
    except OSError:
        return f"status {status}"


def string_array(values):
    """A NULL-terminated-free `const char *const *` from a list of str.

    Returns (array, keepalive) -- hold on to the second or the encoded bytes are
    freed while C still points at them.
    """
    encoded = [v.encode("utf-8") for v in values]
    array = (ctypes.c_char_p * len(encoded))(*encoded)
    return array, encoded
