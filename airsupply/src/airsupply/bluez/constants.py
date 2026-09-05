"""BlueZ D-Bus names and the AirMini's serial profile."""

BLUEZ = "org.bluez"
ROOT = "/"
BLUEZ_ROOT = "/org/bluez"

ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"
PROFILE_MANAGER = "org.bluez.ProfileManager1"
PROFILE = "org.bluez.Profile1"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"

# Serial Port Profile. ResMed's own Android app registers exactly this UUID --
# it is the whole reason this is not a BLE project.
SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"

# libairmini's notes put the AirMini's serial service on RFCOMM channel 5.
# BlueZ normally resolves the channel itself via SDP, so this is a fallback and
# a thing to confirm, not something to rely on.
SPP_CHANNEL = 5

# Where we export our Profile1 implementation on the bus.
PROFILE_PATH = "/org/ananthb/airsupply/spp"

# Substrings that make a device worth a second look during the survey.
NAME_HINTS = ("airmini", "resmed")
