"""The broker, and keeping a connection to it up.

Nothing here is configured by hand. On Home Assistant OS the Mosquitto add-on
is a Supervisor service, so `services: mqtt:want` in config.yaml has the host
and the credentials handed over at start-up; the options exist only for a
broker that is not an add-on.

Why a broker rather than the core API the add-on can already reach: retained
messages and discovery. Home Assistant builds a real device per machine that
can be renamed and put in an area, its entities have unique ids, and both the
discovery and the last reading survive a Home Assistant restart -- so the
device still shows last night's numbers instead of going blank until the next
scheduled read. The core API gives none of that.

Publishing never blocks a read. Messages go on a queue and a task owns the
connection, because the machine is the thing with a ten-second window and the
broker is not allowed to spend it.
"""

import asyncio
import json
import logging
import os

from . import entities

log = logging.getLogger("airsupply.mqtt")

HOST = os.environ.get("AIRSUPPLY_MQTT_HOST", "")
PORT = int(os.environ.get("AIRSUPPLY_MQTT_PORT") or 1883)
USERNAME = os.environ.get("AIRSUPPLY_MQTT_USERNAME") or None
PASSWORD = os.environ.get("AIRSUPPLY_MQTT_PASSWORD") or None

RETRY_SECONDS = 30
QUEUE_DEPTH = 200


def configured():
    """Is there a broker to talk to?

    Without one the add-on still runs: the page works and the machine can be
    read, and only the publishing is off. That is the right way round -- a
    missing broker should not stop somebody reading their own CPAP.
    """
    return bool(HOST)


class Publisher:
    def __init__(self, version):
        self.version = version
        self.connected = False
        self.problem = None
        self._queue = asyncio.Queue(maxsize=QUEUE_DEPTH)
        self._task = None

        # What has been announced, so a machine is described again only when
        # what it reports changes -- a new entity after a mode change, a
        # rename, a person moving machines.
        self._announced = {}

    def start(self):
        if not configured():
            log.info("No MQTT broker configured; nothing will be published to Home Assistant.")
            return
        self._task = asyncio.create_task(self._run())

    async def _run(self):
        import aiomqtt

        will = aiomqtt.Will(entities.STATUS_TOPIC, entities.OFFLINE, qos=1, retain=True)
        while True:
            try:
                async with aiomqtt.Client(
                    hostname=HOST, port=PORT, username=USERNAME, password=PASSWORD, will=will
                ) as client:
                    self.connected = True
                    self.problem = None
                    log.info("Connected to the MQTT broker at %s:%d.", HOST, PORT)
                    await client.publish(
                        entities.STATUS_TOPIC, entities.ONLINE, qos=1, retain=True
                    )
                    # Anything announced before the connection dropped is
                    # retained by the broker, so there is nothing to replay.
                    while True:
                        topic, payload, retain = await self._queue.get()
                        await client.publish(topic, payload, qos=1, retain=retain)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 -- aiomqtt raises its own
                self.connected = False
                self.problem = str(err) or type(err).__name__
                log.warning("MQTT: %s. Trying again in %ds.", self.problem, RETRY_SECONDS)
                # Drop what is queued rather than deliver a stale reading on
                # reconnect; the next read is a few minutes away at most.
                self._drain()
                await asyncio.sleep(RETRY_SECONDS)

    def _drain(self):
        while not self._queue.empty():
            self._queue.get_nowait()

    def _send(self, topic, payload, retain=True):
        if not configured():
            return
        message = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True)
        try:
            self._queue.put_nowait((topic, message, retain))
        except asyncio.QueueFull:
            log.warning("MQTT queue is full; dropping a message for %s.", topic)

    # --- what the rest of the add-on calls ---------------------------------

    def publish(self, address, machine_name, person, results, read_at):
        """One machine's reading, and its description if that has changed."""
        if not configured():
            return
        state = entities.state(results, read_at)
        firmware = entities.firmware_of(results)
        person_name = person.name if person else None
        shape = (machine_name, person_name, firmware, tuple(sorted(state)))

        if self._announced.get(address) != shape:
            for topic, payload in entities.discovery(
                address, machine_name, person_name, firmware, self.version, set(state)
            ):
                self._send(topic, payload)
            self._announced[address] = shape
            log.info("Described %s to Home Assistant as %d entities.", address, len(state))

        self._send(entities.attributes_topic(address), entities.attributes(address, person, firmware))
        self._send(entities.state_topic(address), state)

    def forget(self, address):
        """Take a machine's entities out of Home Assistant.

        An empty retained message on a discovery topic is how Home Assistant
        is told an entity is gone; without it the device stays on the page
        for ever, unavailable, with nothing able to remove it.
        """
        if not configured():
            return
        state_keys = set(self._announced.pop(address, (None, None, None, ()))[3])
        for topic, _ in entities.discovery(address, "", None, None, self.version, state_keys):
            self._send(topic, "")
        self._send(entities.state_topic(address), "")
        self._send(entities.attributes_topic(address), "")
        log.info("Removed %s from Home Assistant.", address)

    async def stop(self):
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
