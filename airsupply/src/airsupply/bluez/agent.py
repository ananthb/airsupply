"""A BlueZ pairing agent, so the link-layer bond happens from the add-on.

The Bluetooth Classic bond used to need `bluetoothctl pair` from a shell on
the host, which is not something to ask of anyone. BlueZ asks the agent
registered by whichever application called Device1.Pair to answer the
link-layer questions -- a PIN to type, a passkey to confirm -- and this is
that agent. Each question becomes a prompt the web page shows; the answer
comes back through `Prompt.answer`.

Which question the AirMini asks is not known yet. It is a 2016-era Classic
device with a display, so legacy PIN pairing (RequestPinCode) is the guess,
but every method is implemented and the page renders whichever one arrives.
"""

import asyncio
import logging

from dbus_next import DBusError
from dbus_next.service import ServiceInterface, method

from . import constants as c

log = logging.getLogger(__name__)

AGENT_PATH = "/org/ananthb/airsupply/agent"
AGENT_MANAGER = "org.bluez.AgentManager1"
AGENT = "org.bluez.Agent1"

# BlueZ waits 60 seconds for an agent to answer. Give up a little sooner so
# that the failure is ours to report rather than a timeout under us.
ANSWER_TIMEOUT = 55

REJECTED = "org.bluez.Error.Rejected"
CANCELED = "org.bluez.Error.Canceled"


class Prompt:
    """One question from BlueZ, waiting on a person."""

    def __init__(self, kind, device, value=None):
        self.kind = kind  # pincode | passkey | confirm | authorize | display
        self.device = device
        self.value = value
        self.future = asyncio.get_running_loop().create_future()

    def answer(self, value=None, accept=True):
        if self.future.done():
            return
        if not accept:
            self.future.set_exception(DBusError(REJECTED, "rejected on the page"))
        else:
            self.future.set_result(value)

    def to_json(self):
        return {"kind": self.kind, "device": self.device, "value": self.value}


class PairingAgent(ServiceInterface):
    def __init__(self, on_change):
        super().__init__(AGENT)
        self._on_change = on_change
        self.prompt = None

    # --- plumbing ----------------------------------------------------------

    def _set(self, prompt):
        self.prompt = prompt
        self._on_change(prompt)

    def clear(self):
        """Drop whatever is showing. Called once pairing has finished."""
        if self.prompt is not None and not self.prompt.future.done():
            self.prompt.future.set_exception(DBusError(CANCELED, "pairing ended"))
        self._set(None)

    async def _ask(self, kind, device, value=None):
        if self.prompt is not None and not self.prompt.future.done():
            self.prompt.future.set_exception(DBusError(CANCELED, "superseded"))
        prompt = Prompt(kind, device, value)
        self._set(prompt)
        log.info("BlueZ asks: %s%s", kind, f" ({value})" if value else "")
        try:
            return await asyncio.wait_for(prompt.future, ANSWER_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("No answer to the %s prompt within %ds.", kind, ANSWER_TIMEOUT)
            raise DBusError(CANCELED, "no answer in time")
        finally:
            if self.prompt is prompt:
                self._set(None)

    def _show(self, device, value):
        """Information, not a question: a code to enter on the machine."""
        prompt = Prompt("display", device, value)
        prompt.future.set_result(None)
        self._set(prompt)
        log.info("BlueZ says: enter %s on the machine.", value)

    # --- org.bluez.Agent1 --------------------------------------------------

    @method()
    def Release(self):  # noqa: N802
        log.info("Pairing agent released by BlueZ.")

    @method()
    async def RequestPinCode(self, device: "o") -> "s":  # noqa: N802
        return str(await self._ask("pincode", device))

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):  # noqa: N802
        self._show(device, pincode)

    @method()
    async def RequestPasskey(self, device: "o") -> "u":  # noqa: N802
        return int(await self._ask("passkey", device))

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # noqa: N802
        self._show(device, f"{passkey:06d}")

    @method()
    async def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: N802
        await self._ask("confirm", device, f"{passkey:06d}")

    @method()
    async def RequestAuthorization(self, device: "o"):  # noqa: N802
        await self._ask("authorize", device)

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: N802
        # We are the side that asked for the connection.
        log.debug("AuthorizeService %s %s: allowed", device, uuid)

    @method()
    def Cancel(self):  # noqa: N802
        log.info("BlueZ cancelled the pending prompt.")
        if self.prompt is not None and not self.prompt.future.done():
            self.prompt.future.set_exception(DBusError(CANCELED, "cancelled by BlueZ"))


async def register(bus, agent):
    """Export the agent and make it the default, so BlueZ routes to it."""
    bus.export(AGENT_PATH, agent)
    introspection = await bus.introspect(c.BLUEZ, c.BLUEZ_ROOT)
    obj = bus.get_proxy_object(c.BLUEZ, c.BLUEZ_ROOT, introspection)
    manager = obj.get_interface(AGENT_MANAGER)
    await manager.call_register_agent(AGENT_PATH, "KeyboardDisplay")
    try:
        await manager.call_request_default_agent(AGENT_PATH)
    except DBusError as err:
        # Not fatal: BlueZ still prefers the agent of the app that called Pair.
        log.warning("Could not become the default agent: %s", err)
    log.info("Pairing agent registered at %s", AGENT_PATH)
