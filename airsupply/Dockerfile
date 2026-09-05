ARG BUILD_FROM
FROM ${BUILD_FROM}

# bluez is not strictly required -- everything goes through BlueZ's D-Bus API on
# the host, not through binaries in here -- but bluetoothctl and sdptool are
# worth having for hand debugging inside the container.
RUN apk add --no-cache \
    bluez \
    dbus-libs \
    python3 \
    py3-pip

COPY requirements.txt /tmp/
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY rootfs /
COPY src/ /usr/src/

# COPY through git can strip execute bits.
RUN chmod +x /etc/cont-init.d/01-verify-dbus.sh \
    && chmod +x /etc/s6-overlay/s6-rc.d/airsupply/run

ENV PYTHONPATH="/usr/src" \
    PYTHONUNBUFFERED="1"

ARG BUILD_ARCH
ARG BUILD_VERSION
LABEL \
    io.hass.name="airsupply" \
    io.hass.type="addon" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.version="${BUILD_VERSION}"
