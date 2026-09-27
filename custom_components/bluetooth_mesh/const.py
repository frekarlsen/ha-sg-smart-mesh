"""Constants for the Bluetooth Mesh integration."""

from __future__ import annotations

DOMAIN = "bluetooth_mesh"

# Raw ``.connect`` network export JSON, stored verbatim in the config entry.
# The runtime (B3) re-parses it via ``Network.from_connect`` at setup time.
CONF_CONNECT_JSON = "connect_json"

# Options-flow key: how long (seconds) to keep the proxy connection open after
# the last command before dropping it to free the lamp's single proxy slot.
# 0 = keep it always open (most responsive; the Häfele app then cannot connect
# to the lamp while the integration is loaded). A positive value trades a
# multi-second cold-start after that idle period for letting the vendor app
# reclaim the lamp when Home Assistant is not actively driving it.
CONF_KEEPALIVE = "keepalive_seconds"
DEFAULT_KEEPALIVE = 0

# Options-flow key: the unicast address the integration transmits FROM.
# 0 means "derive it" — the top of the unicast range, stepping down past any
# address the imported network already uses.
#
# It is overridable because the one thing an export cannot tell us is which
# address the vendor app gave ITSELF: exports carry no provisioner node. If that
# address happens to be ours, every message we send is discarded as a replay by
# nodes that hold a sequence number for it, before any model sees it — with
# nothing in any log to say so. Moving off it is the only way to find out.
CONF_SRC_ADDR = "src_addr"
DEFAULT_SRC_ADDR = 0

# Options-flow key: the unicast addresses whose Light CTL server maps colour
# temperature INVERSELY — asking for warm produces cool. The requested Kelvin is
# mirrored around the exposed range before being sent to those, and to those
# only: mirroring a spec-conformant lamp inverts warm and cool end to end.
#
# Per lamp, not per vendor. It was gated on the company identifier until 0.5.1,
# when issue #7 produced a Häfele lamp the mirror was itself inverting — so the
# quirk varies WITHIN a vendor, by model or firmware, and a CID cannot predict
# it. Stored as a list of addresses rather than a dict of booleans because
# options are serialised to JSON, which has no integer keys: a dict comes back
# keyed by strings after a restart and every lookup silently misses.
#
# Absent and empty mean different things. Absent is an entry that predates the
# option, and the first setup seeds it from the old vendor rule so no working
# install flips on upgrade; empty is a user who unchecked every lamp, and must
# be left alone.
CONF_INVERTED_CTL = "inverted_ctl"

# Options key: unicasts of SG Smart switches / dimmer wheels heard on the mesh.
# Filled automatically on a switch's first button event; each gets an event
# entity and a battery sensor. A list, since options round-trip through JSON.
CONF_SG_SWITCHES = "sg_switches"

# Options key: per SG dimmer, the lowest SG level (percent) that still lights
# the load. Home Assistant's 1..100 % is spread over min..100 so the bottom of
# the slider is not dead. A dict keyed by the unicast as a decimal STRING,
# since options round-trip through JSON (no integer keys).
CONF_SG_MIN_LEVEL = "sg_min_level"

# Options key: unicast addresses of nodes removed from the network. Never handed
# out again: every other node still remembers the sequence number it last saw
# from such an address, and would drop a newcomer's messages as replays.
CONF_RETIRED_UNICASTS = "retired_unicasts"

# SIG model identifiers the integration drives (spec Mesh Model §6/§7). They
# live here rather than in ``light.py`` because the coordinator needs them too:
# the AppKey to encrypt with is the one THESE models are bound to.
MODEL_GENERIC_ONOFF = 0x1000
MODEL_LIGHT_LIGHTNESS = 0x1300
MODEL_LIGHT_CTL = 0x1303
# The Light CTL Temperature server lives on its OWN (secondary) element and sets
# temperature WITHOUT touching lightness — unlike Light CTL Set on element 0.
MODEL_LIGHT_CTL_TEMP = 0x1306

# Every model a command may be addressed to. Used to resolve which application
# key the network binds to the things we actually drive.
# SG Armaturen "SG Smart 3.0" vendor model (company 0x0EE8, model 0x0000),
# stored as company << 16 | model like every vendor model id here.
MODEL_SG_VENDOR = 0x0EE80000

CONTROLLED_MODEL_IDS = (
    MODEL_SG_VENDOR,
    MODEL_GENERIC_ONOFF,
    MODEL_LIGHT_LIGHTNESS,
    MODEL_LIGHT_CTL,
    MODEL_LIGHT_CTL_TEMP,
)
