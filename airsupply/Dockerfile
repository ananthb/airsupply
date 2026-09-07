ARG BUILD_FROM

# ---------------------------------------------------------------------------
# Stage 1: build libairmini.
#
# libairmini is BSD-2 C that owns the AirMini protocol -- framing, SRP-6a
# pairing, the AES-256-CBC session. We consume it rather than reimplement it,
# and we build it here from a pinned commit rather than vendoring the source,
# so that bumping it is a visible one-line diff in libairmini.pin.
#
# Two flags are load-bearing:
#   BUILD_SHARED_LIBS=ON   -- upstream defaults to a static archive, and ctypes
#                             needs a .so.
#   AIRMINI_BUILD_CLI=OFF  -- the CLI target is on by default but references
#                             tools/ sources that are not in the tree; leaving
#                             it on fails the build at link time.
# ---------------------------------------------------------------------------
FROM ${BUILD_FROM} AS libairmini

RUN apk add --no-cache build-base cmake curl openssl-dev

COPY libairmini.pin /tmp/libairmini.pin
RUN . /tmp/libairmini.pin \
    && mkdir -p /src \
    && curl -fsSL "${LIBAIRMINI_REPO}/archive/${LIBAIRMINI_REF}.tar.gz" \
       | tar -xz -C /src --strip-components=1 \
    && cmake -S /src -B /build \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_SHARED_LIBS=ON \
        -DAIRMINI_WITH_OPENSSL=ON \
        -DAIRMINI_BUILD_CLI=OFF \
        -DAIRMINI_BUILD_EXAMPLES=OFF \
    && cmake --build /build -j"$(nproc)" \
    && ctest --test-dir /build --output-on-failure \
    && install -D /build/libairmini.so /out/usr/lib/libairmini.so

# ---------------------------------------------------------------------------
# Stage 2: the add-on.
# ---------------------------------------------------------------------------
FROM ${BUILD_FROM}

# bluez is not strictly required -- everything goes through BlueZ's D-Bus API on
# the host, not through binaries in here -- but bluetoothctl and sdptool are
# worth having for hand debugging inside the container.
# libcrypto is: libairmini's crypto backend is injected, and the one we inject
# is its own OpenSSL implementation rather than anything written here.
RUN apk add --no-cache \
    bluez \
    dbus-libs \
    libcrypto3 \
    python3 \
    py3-pip

COPY --from=libairmini /out/usr/lib/libairmini.so /usr/lib/libairmini.so

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
