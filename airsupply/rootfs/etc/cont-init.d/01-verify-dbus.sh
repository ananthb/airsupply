#!/command/with-contenv bashio
# shellcheck shell=bash

if ! bashio::fs.socket_exists "/run/dbus/system_bus_socket"; then
    bashio::log.fatal "No D-Bus system socket at /run/dbus/system_bus_socket."
    bashio::log.fatal "config.yaml needs 'host_dbus: true'."
    bashio::exit.nok
fi

bashio::log.info "D-Bus socket present."
