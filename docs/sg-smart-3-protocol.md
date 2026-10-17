# SG Smart 3.0 — Bluetooth Mesh vendor protocol

Notes on how SG Armaturen's "SG Smart 3.0" devices are driven, worked out for
interoperability with Home Assistant. Every message below was verified on real
hardware on 2026-09-27 — a LEDDim Smart Pill Micro 3.0 (product `0x00A2`,
firmware `0x0041`; first taken for the plain Pill 3.0, which is `0x00A1`) and
SG Smart Wireless 3.0 dimmer wheels — unless marked otherwise.
The implementation is [`src/btmesh/sg_smart.py`](../src/btmesh/sg_smart.py).

This is an unofficial description. It is not endorsed by SG Armaturen AS and
contains no SG code.

## The devices

- Standard **Bluetooth SIG Mesh** nodes on a Telink stack. Company ID **`0x0EE8`**.
- Provisioning: PB-GATT, **No OOB**. A pill is factory reset with its R button,
  or over the mesh with Config Node Reset (`0x8049`).
- Pill Micro 3.0 composition: Configuration / Health servers, Mesh DFU
  (Firmware Update, BLOB Transfer), **Generic OnOff Server `0x1000`**,
  **vendor models `0x0EE8:0x0000`** (server) and **`0x0EE8:0x0001`** (client).
  No Light Lightness or Generic Level server: dimming goes through the vendor
  model. Mains nodes support the Proxy and Relay features.
- Dimmer wheel: battery powered, no Proxy feature, sleeps between uses. It has
  a Generic OnOff server of its own and publishes Generic OnOff Status
  (`8204 01`) alongside its button events.

## Vendor opcodes

All SG messages use two 3-byte vendor opcodes, unacknowledged at the access
layer:

| Opcode | On air | Direction |
| --- | --- | --- |
| `0xE0` | `E0 E8 0E` | command, to a node |
| `0xF0` | `F0 E8 0E` | status / event, from a node |

The first parameter byte is the message kind. Most commands carry the target
node's address inside the payload (big-endian); the SG app sends them to
`0xFFFF`, but addressing the node's unicast works as well.

TID/SEQ: every command carries a transaction id and a sequence byte. A node
drops a repeat of the TID it just handled; use a new TID per command. Using the
same value for both works.

The application key must be bound to vendor model `0x0EE8:0x0000` (commands)
and, for switch pairing, to `0x0EE8:0x0001`.

## Power / level — kind `0x07`

```
E0 E8 0E  07 TID SEQ NODE_HI NODE_LO LEVEL CCT HUE_HI HUE_LO WHITE SAT
```

| Field | Meaning |
| --- | --- |
| `LEVEL` | `0` off, `1`–`100` percent, `101` (`0x65`) on at the previous level |
| `CCT`, `HUE`, `WHITE`, `SAT` | `FF` = unchanged (a plain dimmer ignores them) |

Examples for node `0x0003`: 50 % `07 01 01 00 03 32 FF FF FF FF FF`, off
`07 04 01 00 03 00 FF FF FF FF FF`, on at last level `07 05 01 00 03 65 FF FF FF FF FF`.

On/off also works with a standard Generic OnOff Set.

## Status — kind `0x09` (reply) and `0x03` (unsolicited)

Request:

```
E0 E8 0E  09 TID SEQ NODE_HI NODE_LO
```

Reply (opcode `0xF0`), e.g. `09 01 73 73 7F FF 01 35 19 00 …`:

```
09 01 TID SEQ ADDR_HI ADDR_LO POWER LEVEL CCT …
```

A node also publishes kind `0x03` on its own after a local change (a wheel, the
wall switch), e.g. `03 A6 84 FF FF 01 1E 19 …`:

```
03 TID SEQ FF FF POWER LEVEL CCT …
```

`POWER` is `00`/`01`; `LEVEL` is 0–100 and is **kept while off** (off at 30 %
reads `00 1E`). Point the vendor server's publication at a group HA listens to
(this integration uses `0xCEE8`).

## Pairing a switch or wheel to a dimmer — kind `0x13`

The dimmer keeps a table of switches it reacts to; once paired, the wheel talks
to the dimmer directly, with no gateway or Home Assistant involved. Sent to the
dimmer:

```
E0 E8 0E  13 TID SEQ NODE_HI NODE_LO SWITCH_HI SWITCH_LO BUTTON  P PT  H HT  R1 R1T  R2 R2T
```

- `NODE` the dimmer, `SWITCH` the wheel/switch unicast, `BUTTON` its button
  index — `04` for the dimmer wheel.
- Four (command, target) pairs: short press, hold, then two rotation slots.
  Which rotation slot the wheel reads has not been isolated; set both.
  Targets are `FF` except for scenes.
- Commands: off `00`, on `01`, dim up `02`, dim down `03`, CCT up `04`,
  CCT down `05`, toggle on/off `0A`, dim up/down `0B`, toggle CCT `0C`,
  scene `13`, none `FF`. All `FF` removes the pairing.
- The wheel must publish its events where the dimmer hears them: `0xFFFF`.

Verified: press toggles, turning dims both ways, hold does nothing —

```
E0 E8 0E  13 0A 0A 00 03 00 04 04  0A FF  FF FF  0B FF  0B FF
```

## Button events — kind `0x2A`

Published by a wheel or switch (opcode `0xF0`, to `0xFFFF`):

```
2A TID SEQ SRC_HI SRC_LO BATTERY BUTTON ACTION V1 V2 V3 V4 V5
```

- `TID` identifies one gesture (a press, a hold, one turn); `SEQ` counts its
  frames from 1. Each frame is usually sent twice.
- `BATTERY` is the cell voltage in 0.1 V (`1D` = 2.9 V; it sags under load).
- `ACTION`: `01` short press, `02` hold (frames keep coming while held),
  `05` rotation.
- `V1` is the new frame's value; `V2`… repeat the earlier frames' values, newest
  first, so a lost frame costs nothing. For a rotation it is a signed step since
  the previous frame (`01`, `08`, `09` up; `FF`, `FB`, `F8` down). The unit is
  finer than one detent.

Examples: press `2A 0A 01 00 04 1D 04 01 01 00 00 00 00`; a fast turn up
`… 05 08`, `… 05 09 08`, `… 05 01 09 08`; down `… 05 FB`, `… 05 F8 FB`.

## Settings the node stores itself

Worked out from the SG Smart app 5.0.958 (arm64, decompiled with blutter) on
2026-10-17. They hold for a wheel and the wall button as much as for Home
Assistant. A node answers each with `KIND STATUS TID SEQ ADDR_HI ADDR_LO …` on
`0xF0`, `ADDR` being whoever asked and the parameters echoed after it.
`STATUS` is `01` accepted, `02` refused: a fade-in of 6 s, twice the app's
longest, was answered `0e 02 29 29 7fff 3c 01` (2026-10-17).

### Trim — kind `0x06` (verified)

```
read   06 TID SEQ NODE_HI NODE_LO 04 00
set    06 TID SEQ NODE_HI NODE_LO TYPE VALUE
reply  06 01 TID SEQ ADDR_HI ADDR_LO MIN START_UP MAX
```

| TYPE | Level | VALUE | App range |
| --- | --- | --- | --- |
| `01` | minimum (what 1 % maps to) | half percent | 0.5–40 % |
| `02` | start-up (a short kick when a cold lamp is switched on, then the last level) | half percent | minimum–40 % |
| `03` | maximum (what 100 % maps to) | whole percent | 40–100 % |

`ADDR` in the reply is whoever asked. Verified on a LEDDim Smart Pill Micro 3.0
(`0x0008`, firmware `0x0041`): the read `06 21 21 0008 04 00` was answered
`06 01 21 21 7fff 14 3c 64` — min 10 %, start-up 30 %, max 100 %, the factory
values — and `06 22 22 0008 03 32` (max 50 %) visibly capped the lamp.

An earlier note here said the pill ignores trim. That test (2026-09-27) wrote
the minimum as raw bytes 35, 50 and 128: 17.5 % and 25 % (still dark on a load
that lights at about 50 %) and 64 %, beyond the 40 % the app ever sends.

### External push switch — kind `0x0D`

```
0D TID SEQ NODE_HI NODE_LO OPT
```

`OPT` `01` Normal (the manual's momentary push button: short press toggles,
holding dims), `02` Sensor (presumably a maintained contact, such as a motion
sensor's relay). The app hides the choice while the node is in pulse mode.
There is no command to read it back.

### Fade-in — kind `0x0E`

```
0E TID SEQ NODE_HI NODE_LO TENTHS 01
```

`TENTHS` is the fade-in time in tenths of a second; the app offers 5, 10, 20
and 30 (two of those, 5 and 30, for the colour controllers). The last byte is
always `01`: the "slider responsiveness" the app logs next to it never reaches
a 3.0 node. No read-back.

Acknowledged (2026-10-17, pill `0x0008`): `0e 25 25 0008 1e 01` was answered
`0e 01 25 25 7fff 1e 01` — kind, `01`, TID, SEQ, the asker's address, then the
parameters echoed. Turning the lamp on with a paired wheel then sent its `03`
status about 2.2 s after the press, at level 1 %: a fade up to 1 % is not
something one sees, so the fade itself still wants a check from a high level.

The dimmer also published `2d TID SEQ 0007 00` right after acting on a press
from wheel `0x0007` (meaning of the last byte unknown).

### Mode — kind `0x01` (not used by this integration)

```
01 TID SEQ NODE_HI NODE_LO MODE
```

`00` dimming, `01` tunable white, `02` on/off, `03` countdown, `04` RGB,
`05` RGBW, `06` pulse. The app clears timers, scenes and shortcuts on a change.

### Also in the app, not mapped further

`11` firmware version request, `32` clear all settings, `3A FF FF IDX` read
the node's switch pairings (short/long/double press, rotation per switch).
The app sends every command to `0xFFFF` with the node in the payload, except
kinds `0x35` and `0x38`.

## Products

The composition data PID says which product a node is (the app's
`sigMeshProductType` / `getDeviceTypeName`), and so which settings its page
offers. In the default (dimming) mode:

| PID | Product | Trim, fade-in | Push input |
| --- | --- | --- | --- |
| `A0` | LEDDim Smart Push 3.0 | yes | yes |
| `A1` | LEDDim Smart Pill 3.0 | yes | yes |
| `A2` | LEDDim Smart Pill Micro 3.0 | yes | yes |
| `A3` | LEDDim Smart Floor 3.0 | yes | – |
| `A4` | LEDDim Smart Ceiling 3.0 | yes | yes |
| `A5` | LEDDim Smart 3.0 | yes | yes |
| `A6` | LEDDim Smart 2-pole 3.0 | yes | yes |
| `A7` | LEDDim Smart Master 3.0 | yes | yes |
| `A8` | LEDDim Smart DIN 2-pole 3.0 | yes | yes |
| `A9` | LEDDim Smart DIN 1000 3.0 | yes | – |
| `AA` | Smart Pill ON-OFF Micro PF 3.0 | – | yes |
| `AB` | Smart Pill ON-OFF Micro 3.0 | – | yes |
| `AC` / `AD` / `AE` | Smart Plug / Socket / DIN 3600 3.0 | – | – |
| `AF` / `B0` | Smart Switch / Smart Switch DK 3.0 | – | – |
| `B1` | Smart Wireless 3.0 (the dimmer wheel) | – | – |
| `B2` | LEDDim Smart Color Controller 3.0 | yes | – |
| `B3` | LEDDim Smart Pill DALI 3.0 | yes | yes |
| `B4` | HeatReg Smart 3.0 | – | – |
| `B5` / `B6` | Smart Gateway Wall / Smart Gateway 3.0 | – | – |
| `B7` / `B8` / `BE` | LEDDim Smart Driver 140mA / 350mA / 100W 3.0 | yes | yes |
| `B9` | LEDDim Smart Driver Pill 3.0 | yes | yes |
| `BA` | LEDDim Smart ZIP Track Dimmer 20VA 3.0 | yes | – |
| `BB` / `BC` / `BD` | TW LEDDim Smart Push / Pill / 3.0 | yes | yes |

Mains frequency (50/60 Hz) is offered for the TW products, the DIN 1000 and
the ceiling only; output type for the drivers only.

## Also relevant: the proxy nonce

Proxy configuration PDUs (Set Filter Type, Add Addresses) are encrypted with
the **proxy nonce** — `03 00 SEQ(3) SRC(2) 00 00 IV_INDEX(4)` (Mesh Profile
§3.8.5.4) — not the network nonce. An SG node drops a filter message sealed
with the network nonce, keeps an empty accept list, and forwards nothing to the
client: commands work, but no reply ever arrives.
