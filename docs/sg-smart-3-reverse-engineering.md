# SG Smart 3.0 — how the protocol was worked out

This page records how the SG Smart 3.0 vendor protocol in
[`sg-smart-3-protocol.md`](sg-smart-3-protocol.md) was found: how the SG Smart
app was taken apart, where in it each command lives, and every test made on
real hardware. It is written so the work can be repeated and carried on, for
example on a newer app or firmware.

The goal was interoperability: driving SG Armaturen devices the owner already
has from Home Assistant, without SG's hub. This is an unofficial description,
not endorsed by SG Armaturen AS. **The repository holds no SG code or binaries**
— no APK, no decompiled source, no disassembly. What is here is the method,
the names of the places in the app that matter, and the results.

The first pass over the app, in Norwegian, is
[`sg-smart-app-report.nb.md`](sg-smart-app-report.nb.md) (unchanged). It covers
the app's architecture (Flutter, CSRmesh and Telink SIG Mesh SDKs, hub, cloud,
Matter) and the BLE UUIDs. The decompiled files its section 8 lists are not in
this repository.

## 1. The app

| | |
| --- | --- |
| App | SG Smart, package `com.sgas.leddimapp`, version **5.0.958** |
| Publisher | SG Armaturen AS; developed by ideaslab (Hong Kong) |
| Framework | Flutter: the logic is Dart, compiled ahead of time to native code in `libapp.so` |
| Dart | **3.11.0**, snapshot hash `78da37fed6bf1489361a312568249f3f`, android arm64, compressed pointers |

The Java side (the Android shell and plugins, the Telink SIG Mesh SDK
`com.telink.*`, the CSRmesh SDK `com.csr.*`, SG's thin bridge
`hk.ideaslab.flutter_sg_sig_mesh`) decompiles cleanly with jadx; that is what
the first report is based on. Everything SG-specific — which command is sent
when, and with what bytes — is in the Dart code, which jadx cannot see.

## 2. Getting an arm64 build

blutter (below) analyses **arm64** snapshots only.

- An XAPK from a mirror site turned out to hold only the 32-bit split
  (`config.armeabi_v7a.apk`): no use.
- The Google Play download with [apkeep](https://github.com/EFForg/apkeep)
  gave the split APKs, among them `com.sgas.leddimapp.config.arm64_v8a.apk`,
  which carries `lib/arm64-v8a/libapp.so` and `libflutter.so`.

apkeep's Google Play source needs a Google account token (an AAS token).
**Treat it as a password**: do not paste it into a chat, a ticket or a commit,
and clear the shell history after the download (in zsh:
`rm ~/.zsh_history && fc -p`).

```bash
apkeep -a com.sgas.leddimapp -d google-play -e <account e-mail> -t <AAS token> -o split_apk=true sgplay/
unzip sgplay/com.sgas.leddimapp/com.sgas.leddimapp.config.arm64_v8a.apk 'lib/*' -d a64/
```

## 3. Decompiling the Dart code with blutter

[blutter](https://github.com/worawit/blutter) (commit `4a60ac6`, 2026-08-18)
rebuilds Dart's class and function names from an AOT snapshot. It builds the
matching Dart VM first (here 3.11.0, found from the snapshot by itself), then
writes:

- `asm/` — one file per Dart library, each function disassembled with its
  name, e.g. `asm/sg_flutter/mesh_model/device_manager.dart`;
- `pp.txt` (the object pool: constants, strings), `objs.txt`;
- `blutter_frida.js`, a Frida script for hooking the live app.

```bash
python3 blutter.py a64/lib/arm64-v8a out/
```

The output is disassembly, not source, but every function keeps its Dart name
and file, so the commands can be found by name and their bytes read from the
array literal each one builds. A small integer appears there tagged (Dart
stores it doubled): `r16 = 116` in the first slot of a command's array is kind
`0x3A`.

## 4. Where each command lives in the app

All SIG Mesh commands go out through `DeviceManager._sendSigMeshVendorCommand`
(`sg_flutter/mesh_model/device_manager.dart`), on vendor opcode `E0 E8 0E`,
addressed to `0xFFFF` with the target node inside the payload — except kinds
`0x35` and `0x38`, which go to the node. Each `send…Command` builds
`KIND TID SEQ NODE_HI NODE_LO …`; a task in `sg_flutter/mesh_model/tasks/`
sends it and waits for the answer.

### Used by this integration

| Kind | App function (`DeviceManager`) | Task file | Status here |
| --- | --- | --- | --- |
| `07` | `sendSigMeshPowerLevelCommand` | `power_level_task.dart` | verified 2026-09-27 |
| `09` | `sendRequestSigMeshDeviceStatusCommand` | `request_status_task.dart` | verified 2026-09-27 |
| `13` | `sendSetSigMeshDeviceControllerSwitchCommand` | `set_controller_switch_task.dart` (`SetControllerSwitchTask`) | verified 2026-09-27 |
| `06` | `sendSetSigMeshDeviceTrimLevelCommand`, `sendRequestSigMeshDeviceTrimLevelCommand` | `trim_level_tasks.dart`; reply parsed by `handleSigMeshTrimLevelResponse` (`mesh_device.dart`) | verified 2026-10-17 |
| `0D` | `sendSetSigMeshDeviceExternalPushSwitchCommand` | `set_external_push_switch_option_task.dart` | acknowledged 2026-10-17 |
| `0E` | `sendSetSigMeshDeviceFadingTimeCommand` | `set_start_up_fading_task.dart` | acknowledged 2026-10-17 |

`2A` (button events) is what a switch sends; the app's own
`sendSigMeshSwitchPressedCommand` builds the same kind.

Which settings a device's page offers is decided by `getDeviceItems`, next to
the product type (`sigMeshProductType`, `getDeviceTypeName`) in
`sg_flutter/db/entity/device.dart`, and shown by `DeviceDetailCubit`
(`sg_flutter/bloc/device_detail_cubit.dart`). That is the source of the
product table in the protocol notes.

### Found, not used

| Kind | App function | Notes |
| --- | --- | --- |
| `01` | `sendSetSigMeshDeviceModeCommand` (`set_device_mode_task.dart`) | mode; values in the protocol notes |
| `11` | `sendRequestSigMeshDeviceFirmwareCommand` (`request_firmware_task.dart`) | reply format not mapped |
| `32` | `sendSigMeshClearSettingsCommand` (`clear_all_settings_task.dart`) | clears all settings |
| `3A` | `sendRequestSigMeshDeviceControllerSettingsCommand` (`request_sig_mesh_controller_settings_task.dart`) | switch table read; only reachable from the hidden debug page (`bloc/debug_cubit.dart`). **Unanswered** on firmware `0x0041` |
| `23` | `sendRemoveSigMeshDeviceControllerSwitchCommand` | a separate "remove switch"; this integration unpairs with all-`FF` commands instead |

The kinds below were read from the first slot of each function's array and
**have not been sent to a device**. They are leads, not facts:

| Kind | App function |
| --- | --- |
| `04` | `sendSetSigMeshDeviceTimeCommand` |
| `0A` | `sendSetSigMeshDeviceCountdownCommand` |
| `0B` | `sendSetSigMeshDeviceSceneCommand` |
| `0C` | `sendSetSigMeshDeviceMasterFunctionCommand` |
| `0F` | `sendSigMeshApplySceneCommand` |
| `12` | `sendSigMeshDynamicModeCommand` |
| `20` | `sendSetSigMeshDeviceLatLonCommand` |
| `21` | `sendSetSigMeshDeviceTimerCommand` |
| `26` | `sendSetSigMeshDeviceCircadianIdCommand` |
| `36` | `sendSetSigMeshDeviceWakeUpCommand` |
| `39` | `sendSetSigMeshDeviceMainsFrequencyCommand` |
| `3D` | `sendSetSigMeshDeviceOutputTypeCommand` |

`DeviceManager` also has thermostat (HeatReg), gateway (Wi-Fi, registration,
Matter fabrics) and OTA commands, not looked at.

Two settings never reach a 3.0 node: *Slider responsiveness* (the app logs it,
but the fade-in payload always ends in `01`) and *LED type* (kept in the app).

## 5. Tests on hardware

Hardware: a **LEDDim Smart Pill Micro 3.0** (PID `A2`, firmware `0x0041`) and
two **Smart Wireless 3.0** wheels, through an ESPHome Bluetooth proxy (ESP32),
with Home Assistant sending from `0x7FFF`. Commands were sent with the
integration's `bluetooth_mesh.send_raw` action, and the answers watched as
`bluetooth_mesh_message` events.

### 2026-09-27 — before the decompile

- Provisioning (PB-GATT, No OOB), configuration and Config Node Reset.
- Power/level (kind `07`), status read (`09`) and unsolicited status (`03`).
- Pairing a wheel to the pill (kind `13`): press toggles, turning dims, hold
  does nothing — `e0e80e 13 0a 0a 0003 0004 04 0aff ffff 0bff 0bff`.
- Button events (kind `2A`), battery voltage, rotation steps.
- A trim test that said nothing: the minimum was written as raw 35, 50 and
  128, which in the node's half-percent units is 17.5 %, 25 % and 64 %. The
  first two are dark on a load that lights near 50 %, and 64 % is beyond the
  app's 40 % limit. It was taken for "the pill ignores trim" until the decompile
  showed the units.

### 2026-10-17 — the settings, pill `0x0008`

| Sent (`e0e80e` + …) | Answer (`f0e80e` + …) | Result |
| --- | --- | --- |
| `06 21 21 0008 04 00` (read trim) | `06 01 21 21 7fff 14 3c 64` | min 10 %, start-up 30 %, max 100 % (factory) |
| `06 22 22 0008 03 32` (max 50 %) | not recorded | the lamp was visibly dimmer at 100 % in HA: trim works |
| `0e 25 25 0008 1e 01` (fade-in 3 s) | `0e 01 25 25 7fff 1e 01` | accepted; the answer echoes the parameters |
| `0e 29 29 0008 3c 01` (fade-in 6 s) | `0e 02 29 29 7fff 3c 01` | **refused**: status `02` |
| `3a 2c 2c 0008 ff ff 01` (table page 1), to `0x0008` | none | |
| `3a 2d 2d 0008 ff ff 02` (page 2), to `0x0008` | none | |
| `3a 2e 2e 0008 ff ff 01`, to `0xFFFF` as the app does | none | the firmware most likely lacks kind `3A` |

Also seen: after acting on a press from wheel `0x0007`, the pill published
`2d TID SEQ 0007 00` (last byte unknown). A fade-in is hard to judge by eye on a
lamp switched on to 1 %, and the warm/cold state of the light source matters;
3 s was accepted and is offered as is.

The settings were then exercised end to end through the integration
(0.12.0rc2: the trim levels, push input and fade-in entities) and worked.

## 6. Open questions

- **Rotation slots** in the pairing command: which of the two the wheel reads.
  Both are written.
- **Reading the switch table** (`3A`): try a newer firmware. Until then the
  integration shows the pairings it sent itself.
- **Firmware version** (`11`): reply format.
- **Push input "Sensor"**: what the node does differently, on a real sensor.
- **Kind `2D`**'s last byte.
- **SG Smart Gen1 (CSRmesh)** devices: a different stack altogether; the Java
  CSRmesh SDK in the app is the place to start.

## 7. Repeating it on a newer app

1. Download the arm64 split from Google Play with apkeep (section 2).
2. Run blutter on `lib/arm64-v8a/` (section 3); it builds the Dart VM the
   snapshot needs.
3. Open `asm/sg_flutter/mesh_model/device_manager.dart` and look up the
   function names in section 4. The first element of each command's array
   (halved) is the kind; the rest is the layout.
4. Check each finding on a device with `bluetooth_mesh.send_raw`, watching
   `bluetooth_mesh_message` in *Developer tools → Events*, before building on
   it. Status `01` is accepted, `02` refused; silence is not a refusal.
