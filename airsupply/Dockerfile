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
# Stage 2: the page.
#
# Elm, compiled here rather than committed, so `web/src` is the only source of
# truth and a page that does not compile fails the build instead of shipping.
# On Debian rather than the Alpine base because the elm binary the npm package
# fetches is linked against glibc; only its output crosses into the image, so
# nothing of this stage ships.
# ---------------------------------------------------------------------------
FROM node:22-bookworm-slim AS web

# The elm compiler fetches the packages in elm.json over HTTPS and verifies
# against the system CA store, which the slim image does not ship. npm does not
# need this -- node carries its own CA list -- so the build gets as far as
# `elm make` and then blames your internet connection for an unknown CA.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /web

COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/elm.json ./
COPY web/index.html ./
COPY web/src/ ./src/
COPY web/js/ ./js/
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 3: the runtime.
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
COPY tests/ /tmp/tests/
COPY --from=web /web/dist/ /usr/src/airsupply/static/

ENV PYTHONPATH="/usr/src" \
    PYTHONUNBUFFERED="1"

# Fail the build, not the first start: the .so has to load against this image's
# libcrypto, the Python side has to import, the page has to have been compiled,
# and the state the page decodes has to still carry every field it decodes.
RUN python3 -m compileall -q /usr/src \
    && python3 -c 'import ctypes; ctypes.CDLL("/usr/lib/libairmini.so").airmini_crypto_openssl' \
    && python3 -c 'import dbus_next, aiohttp, airsupply.airmini, airsupply.bluez, airsupply.api, airsupply.web' \
    && test -s /usr/src/airsupply/static/index.html \
    && test -s /usr/src/airsupply/static/assets/elm.js \
    && test -s /usr/src/airsupply/static/assets/app.js \
    && python3 -m unittest discover -s /tmp/tests -q \
    && rm -rf /tmp/tests

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
