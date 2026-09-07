# The image behind the Home Assistant add-on. The add-on itself -- options,
# AppArmor profile, the run script that turns options into environment -- is
# packaged in ananthb/hass-addons and layers on top of this; see release.yml
# for how it gets to ghcr.io/ananthb/airsupply.
#
# Built on the Home Assistant base image rather than plain Alpine so that the
# add-on wrapper inherits s6-overlay and bashio from here, and so that the
# musl libairmini.so built below matches the Python that loads it.
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.21

# ---------------------------------------------------------------------------
# Stage 1: build libairmini, and fetch the one front-end dependency.
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

# htmx is the whole front end: the page is server-rendered HTML that htmx
# polls and swaps. Pinned by version and checksum, fetched here rather than
# committed, so the repository carries no minified blob. 0BSD licence.
ARG HTMX_VERSION=4.0.0
ARG HTMX_SHA256=e484d9171a9db30a39c8f16e3d709d4137f3211c659f8e6125816635033d593f
RUN curl -fsSL "https://raw.githubusercontent.com/bigskysoftware/htmx/v${HTMX_VERSION}/dist/htmx.min.js" \
       -o /out/htmx.min.js \
    && echo "${HTMX_SHA256}  /out/htmx.min.js" | sha256sum -c -

# ---------------------------------------------------------------------------
# Stage 2: the runtime.
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

COPY src/ /usr/src/
COPY --from=libairmini /out/htmx.min.js /usr/src/airsupply/static/htmx.min.js

ENV PYTHONPATH="/usr/src" \
    PYTHONUNBUFFERED="1"

# Fail the build, not the first start, if the .so does not load against this
# image's libcrypto or the Python side does not import.
RUN python3 -m compileall -q /usr/src \
    && python3 -c 'import ctypes; ctypes.CDLL("/usr/lib/libairmini.so").airmini_crypto_openssl' \
    && python3 -c 'import dbus_next, aiohttp, airsupply.airmini, airsupply.bluez, airsupply.web, airsupply.views'
    && test -s /usr/src/airsupply/static/htmx.min.js

ARG BUILD_VERSION=dev
ENV AIRSUPPLY_VERSION="${BUILD_VERSION}"

LABEL \
    org.opencontainers.image.title="airsupply" \
    org.opencontainers.image.description="Read a ResMed AirMini CPAP over Bluetooth Classic" \
    org.opencontainers.image.source="https://github.com/ananthb/airsupply" \
    org.opencontainers.image.licenses="GPL-3.0-only" \
    org.opencontainers.image.version="${BUILD_VERSION}"

# Configuration is environment: AIRSUPPLY_LOG_LEVEL and AIRSUPPLY_PORT. The
# page is on that port; under Home Assistant it is reached through ingress.
# Outside Home Assistant, mount the host's /run/dbus/system_bus_socket and
# publish the port by hand.
EXPOSE 8099
CMD ["python3", "-m", "airsupply"]
