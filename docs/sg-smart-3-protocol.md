# SG Smart 3.0 — Bluetooth Mesh vendor protocol

Notes on how SG Armaturen's "SG Smart 3.0" devices are driven, worked out for
interoperability with Home Assistant. Every message below was verified on real
hardware on 2026-09-27 — a LEDDim Smart Pill 3.0 (product `0x00A2`, firmware
`0x0041`) and SG Smart Wireless 3.0 dimmer wheels — unless marked otherwise.
The implementation is [`src/btmesh/sg_smart.py`](../src/btmesh/sg_smart.py).

This is an unofficial description. It is not endorsed by SG Armaturen AS and
contains no SG code.

## The devices

- Standard **Bluetooth SIG Mesh** nodes on a Telink stack. Company ID **`0x0EE8`**.
- Provisioning: PB-GATT, **No OOB**. A pill is factory reset with its R button,
  or over the mesh with Config Node Reset (`0x8049`).
- Pill 3.0 composition: Configuration / Health servers, Mesh DFU
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

## Trim level — kind `0x06` (no effect on Pill 3.0)

From the app: read `06 TID SEQ NODE_HI NODE_LO 04 00` (reply expected as
`06 01 …` with three levels at bytes 6–8), set
`06 TID SEQ NODE_HI NODE_LO TYPE VALUE` with type `01` min, `02` start-up,
`03` max. A Pill 3.0 answered no read, and setting the minimum to 35, 50 and 128
changed nothing. This integration therefore applies a minimum level itself.

## Also relevant: the proxy nonce

Proxy configuration PDUs (Set Filter Type, Add Addresses) are encrypted with
the **proxy nonce** — `03 00 SEQ(3) SRC(2) 00 00 IV_INDEX(4)` (Mesh Profile
§3.8.5.4) — not the network nonce. An SG node drops a filter message sealed
with the network nonce, keeps an empty accept list, and forwards nothing to the
client: commands work, but no reply ever arrives.
