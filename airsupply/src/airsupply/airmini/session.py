"""An asyncio session over an open RFCOMM file descriptor.

The division of labour, which is libairmini's design rather than ours:

    BlueZ           gives us a file descriptor for the serial channel
    this module     moves bytes between that descriptor and the library
    libairmini      owns everything above the bytes

So there is no protocol logic here. There is a read callback that hands bytes
to airmini_feed(), a send callback that writes whatever the library produces,
and the bookkeeping to turn synchronous C response callbacks back into awaitable
futures.

Read-only, by policy rather than by lock. No airmini_set_* prototype is
declared and `call()` refuses any method that is not a read -- not because a
write would necessarily fail, but because the write path is unverified
upstream and this is a machine someone sleeps attached to. Lifting the
restriction means editing READ_METHODS, deliberately, having read
docs/verify.md experiment 6.
"""

import asyncio
import ctypes
import json
import logging
import os

from . import _lib
from ._lib import AirminiError

log = logging.getLogger("airsupply.airmini")

# Methods this module is willing to send. Everything else -- Set, EraseData,
# EnterTherapy, the lot -- is refused here rather than at the device.
READ_METHODS = frozenset({
    "GetVersion",
    "GetDateTime",
    "Get",
    "GetLoggedData",
    "GetHistory",
})


class Session:
    def __init__(self, fd, notify=None):
        self._fd = fd
        self._am = None
        self._loop = None
        self._closed = False
        self._notify_hook = notify

        # Response futures, keyed by the token handed to C as the `user`
        # pointer. Tokens start at 1: ctypes turns a zero c_void_p back into
        # None, which would be indistinguishable from "no user pointer".
        self._pending = {}
        self._next_token = 1

        # Callbacks C holds pointers to. These must outlive every call into the
        # library; dropping one is a use-after-free, not a Python error.
        self._send_cb = _lib.SEND_FN(self._on_send)
        self._log_cb = _lib.LOG_FN(self._on_log)
        self._response_cb = _lib.RESPONSE_FN(self._on_response)
        self._notify_cb = _lib.NOTIFY_FN(self._on_notify)
        self._config = None

    # --- lifecycle ---------------------------------------------------------

    def open(self):
        lib = _lib.lib()
        self._loop = asyncio.get_running_loop()

        crypto = lib.airmini_crypto_openssl()
        if not crypto:
            raise AirminiError(_lib.ERR_CRYPTO, "airmini_crypto_openssl")

        self._config = _lib.AirminiConfig(
            send=self._send_cb,
            send_user=None,
            crypto=crypto,
            log=self._log_cb,
            log_user=None,
        )
        self._am = lib.airmini_create(ctypes.byref(self._config))
        if not self._am:
            raise AirminiError(_lib.ERR_NOMEM, "airmini_create")

        lib.airmini_set_notify(self._am, self._notify_cb, None)
        self._loop.add_reader(self._fd, self._on_readable)
        log.debug("Session open on fd %d, state %s.", self._fd, self.state)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._loop is not None:
            try:
                self._loop.remove_reader(self._fd)
            except (ValueError, OSError):
                pass
        self._fail_pending(ConnectionResetError("session closed"))
        if self._am:
            _lib.lib().airmini_destroy(self._am)
            self._am = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    @property
    def state(self):
        if not self._am:
            return "closed"
        return _lib.STATE_NAMES.get(
            _lib.lib().airmini_get_state(self._am), "unknown"
        )

    # --- transport ---------------------------------------------------------

    def _on_send(self, _user, data, length):
        """libairmini has bytes for the device."""
        try:
            payload = ctypes.string_at(data, length)
            written = 0
            while written < len(payload):
                written += os.write(self._fd, payload[written:])
            log.debug("-> %d bytes", len(payload))
            return 0
        except OSError as err:
            log.error("Write to the serial channel failed: %s", err)
            return -1

    def _on_readable(self):
        """The device has bytes for libairmini."""
        try:
            chunk = os.read(self._fd, 4096)
        except BlockingIOError:
            return
        except OSError as err:
            log.error("Read from the serial channel failed: %s", err)
            self._fail_pending(err)
            return

        if not chunk:
            log.warning("The machine closed the serial channel.")
            self._fail_pending(ConnectionResetError("device hung up"))
            return

        log.debug("<- %d bytes", len(chunk))
        buf = (ctypes.c_uint8 * len(chunk)).from_buffer_copy(chunk)
        # Response and notification callbacks fire from inside this call.
        status = _lib.lib().airmini_feed(self._am, buf, len(chunk))
        if status != _lib.OK:
            log.warning("airmini_feed: %s", _lib.strerror(status))

    # --- callbacks from C --------------------------------------------------

    def _on_log(self, _user, line):
        if line:
            log.debug("libairmini: %s", line.decode("utf-8", "replace"))

    def _on_response(self, user, status, result_json, error_json):
        try:
            future = self._pending.pop(user, None)
            if future is None or future.done():
                return
            if status == _lib.OK:
                future.set_result(_decode(result_json))
            else:
                detail = error_json.decode("utf-8", "replace") if error_json else None
                future.set_exception(AirminiError(status, "device", detail))
        except Exception:
            # Never let an exception unwind into C.
            log.exception("response callback failed")

    def _on_notify(self, _user, method, payload):
        try:
            name = method.decode("utf-8", "replace") if method else ""
            body = _decode(payload)
            log.debug("notification %s", name)
            if self._notify_hook:
                self._notify_hook(name, body)
        except Exception:
            log.exception("notification callback failed")

    def _fail_pending(self, err):
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(err)
        self._pending.clear()

    # --- requests ----------------------------------------------------------

    def _request(self, what, invoke):
        """Issue a request and return a future for its response.

        `invoke` receives (response_callback, user_pointer) and makes the actual
        libairmini call. The token is registered before the call because the
        library may answer synchronously -- a local failure does not wait for
        the device.
        """
        if self._am is None:
            raise AirminiError(_lib.ERR_STATE, what, "session is closed")

        token = self._next_token
        self._next_token += 1
        future = self._loop.create_future()
        self._pending[token] = future

        status = invoke(self._response_cb, ctypes.c_void_p(token))
        if status != _lib.OK:
            self._pending.pop(token, None)
            raise AirminiError(status, what)
        return future

    async def pair(self, passkey):
        """SRP-6a pairing with the PIN on the machine.

        The result carries a masterPairKey. Persist it: it is what makes every
        later connection PIN-free.
        """
        lib = _lib.lib()
        key = passkey.encode("utf-8")
        return await self._request(
            "airmini_pair",
            lambda cb, user: lib.airmini_pair(self._am, key, cb, user),
        )

    async def open_session(self, master_pair_key_hex):
        """Reconnect with a stored masterPairKey -- no PIN, no button press."""
        lib = _lib.lib()
        mpk = master_pair_key_hex.encode("utf-8")
        return await self._request(
            "airmini_open_session",
            lambda cb, user: lib.airmini_open_session(self._am, mpk, cb, user),
        )

    async def get_version(self):
        lib = _lib.lib()
        return await self._request(
            "airmini_get_version",
            lambda cb, user: lib.airmini_get_version(self._am, cb, user),
        )

    async def get_datetime(self):
        lib = _lib.lib()
        return await self._request(
            "airmini_get_datetime",
            lambda cb, user: lib.airmini_get_datetime(self._am, cb, user),
        )

    async def get_settings(self):
        lib = _lib.lib()
        return await self._request(
            "airmini_get_settings",
            lambda cb, user: lib.airmini_get_settings(self._am, cb, user),
        )

    async def get_metrics(self):
        lib = _lib.lib()
        return await self._request(
            "airmini_get_metrics",
            lambda cb, user: lib.airmini_get_metrics(self._am, cb, user),
        )

    async def get(self, keys):
        lib = _lib.lib()
        array, keepalive = _lib.string_array(keys)
        return await self._request(
            "airmini_get",
            lambda cb, user: lib.airmini_get(
                self._am, array, len(keepalive), cb, user
            ),
        )

    async def get_logged_data(self, signals, from_iso):
        """Ask for logged signals. Results stream in as notifications."""
        lib = _lib.lib()
        array, keepalive = _lib.string_array(signals)
        since = from_iso.encode("utf-8")
        return await self._request(
            "airmini_get_logged_data",
            lambda cb, user: lib.airmini_get_logged_data(
                self._am, array, len(keepalive), since, cb, user
            ),
        )

    async def get_history(self, from_iso):
        lib = _lib.lib()
        since = from_iso.encode("utf-8")
        return await self._request(
            "airmini_get_history",
            lambda cb, user: lib.airmini_get_history(self._am, since, cb, user),
        )

    async def call(self, method, params_json=None):
        """The escape hatch, narrowed to reads.

        libairmini will happily send Set. This will not: the write path is
        unverified upstream and untested here, and the first thing airsupply
        must not do is discover that on a machine in use.
        """
        if method not in READ_METHODS:
            raise ValueError(
                f"{method} is not a read; airsupply does not send it yet "
                "(see docs/verify.md experiment 6)"
            )
        lib = _lib.lib()
        name = method.encode("utf-8")
        params = params_json.encode("utf-8") if params_json else None
        return await self._request(
            "airmini_call",
            lambda cb, user: lib.airmini_call(self._am, name, params, cb, user),
        )


def _decode(raw):
    """A JSON fragment from C into Python, tolerating both empty and garbage."""
    if not raw:
        return None
    text = raw.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
