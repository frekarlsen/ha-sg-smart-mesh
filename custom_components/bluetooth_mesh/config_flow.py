"""Config flow for the Bluetooth Mesh integration.

The user imports a mesh network provisioned by the ThingOS / Häfele Connect
Mesh app, which exports it as a ``.connect`` JSON file. Rather than a native
file-upload widget (unreliable across the various HA front ends), the flow
takes the *contents* of that file pasted into a multiline text field, parses
it with :func:`btmesh.network_model.Network.from_connect`, and — on success —
stores the raw JSON verbatim in the config entry. The runtime (a later task)
re-parses it at setup time, so no key bytes need to be serialised here.
"""

from __future__ import annotations

import json
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .btmesh.network_model import Network, NetworkModelError
from .const import (
    CONF_CONNECT_JSON,
    CONF_INVERTED_CTL,
    CONF_RETIRED_UNICASTS,
    CONF_SG_MIN_LEVEL,
    CONF_SG_SWITCHES,
    CONF_KEEPALIVE,
    CONF_SRC_ADDR,
    DEFAULT_KEEPALIVE,
    DEFAULT_SRC_ADDR,
    DOMAIN,
    MODEL_LIGHT_CTL,
    MODEL_SG_VENDOR,
)


def _parse(text: str) -> tuple[Network | None, str, str]:
    """Parse a pasted ``.connect`` export.

    Returns ``(network, "", "")`` on success and ``(None, error_key, detail)``
    otherwise, where ``error_key`` selects the translated form error and
    ``detail`` is interpolated into it.

    The two failures are separated because their fixes differ: text that is
    not JSON at all is usually a truncated or word-wrapped paste, whereas a
    well-formed document that is not an export means the wrong file. Only the
    sentence around the detail is translated — the detail itself stays in the
    parser's words, since it names JSON fields that are English in the file.
    """
    try:
        return Network.from_connect(json.loads(text)), "", ""
    except json.JSONDecodeError as exc:
        return None, "invalid_json", str(exc)
    except NetworkModelError as exc:
        return None, "invalid_connect", str(exc)


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CONNECT_JSON): TextSelector(
            TextSelectorConfig(multiline=True, type=TextSelectorType.TEXT)
        ),
    }
)


class BluetoothMeshConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle importing a ``.connect`` mesh network into a config entry."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> "BluetoothMeshOptionsFlow":
        """Expose the options flow (keep-alive tuning)."""
        return BluetoothMeshOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Import a ``.connect`` network export pasted as JSON text."""
        errors: dict[str, str] = {}

        reason = ""
        if user_input is not None:
            text = user_input[CONF_CONNECT_JSON]
            network, error_key, reason = _parse(text)
            if network is None:
                errors["base"] = error_key
            else:
                await self.async_set_unique_id(network.identifier)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=network.name or "Bluetooth Mesh",
                    data={CONF_CONNECT_JSON: text},
                    # Present and empty: no lamp is mirrored until the user
                    # ticks it. Left absent, the setup took the entry for one
                    # that predates the option and seeded the pre-0.5.1
                    # vendor rule, pre-ticking every Häfele CTL lamp on a
                    # fresh install, the very guess issue #7 proved wrong.
                    options={CONF_INVERTED_CTL: []},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"error": reason},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Replace the stored export with a freshly exported one.

        Networks change: a node is added, a key is refreshed. Without this the
        only way to import the new export was to delete the entry and re-add
        it, which costs every entity id and the history behind it.
        """
        errors: dict[str, str] = {}
        reason = ""

        if user_input is not None:
            text = user_input[CONF_CONNECT_JSON]
            network, error_key, reason = _parse(text)
            if network is None:
                errors["base"] = error_key
            else:
                await self.async_set_unique_id(network.identifier)
                # Pasting a *different* network here would silently repoint
                # every entity at nodes that are not theirs.
                self._abort_if_unique_id_mismatch(reason="wrong_network")
                return self.async_update_reload_and_abort(
                    self._get_reconfigure_entry(),
                    data_updates={CONF_CONNECT_JSON: text},
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"error": reason},
        )


# "Device" choice in the add-device form that just searches again.
RESCAN = "rescan"


class BluetoothMeshOptionsFlow(OptionsFlowWithReload):
    """Tune runtime behaviour: how long to hold the proxy connection open.

    A mesh node offers a single proxy connection slot. Holding it open makes
    commands instant but locks the vendor (Häfele Connect Mesh) app out of the
    lamp; dropping it after an idle period hands the slot back at the cost of a
    multi-second reconnect on the next command. ``0`` keeps it always open.

    ``OptionsFlowWithReload`` reloads the entry itself when the options change.
    A config-entry update listener did that job before; Home Assistant reports
    that pattern as deprecated and stops honouring it in 2026.12, and the two
    are mutually exclusive — registering a listener makes this class raise.
    """

    _beacons: list = []
    _add_task = None
    _add_name = ""
    _add_result: dict | None = None
    _add_error = ""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Menu: connection settings, add a device, or back up the network."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "add_device", "pair_switch", "remove_device", "backup", "settings",
            ],
        )

    # ------------------------------------------------------- pair switch

    def _sg_nodes(self):
        """(dimmers, switches) as (unicast, label) lists, from the live network."""
        coordinator = self.config_entry.runtime_data
        known_switches = set(coordinator.sg_switches)
        dimmers, switches = [], []
        for node in coordinator.network.nodes:
            label = f"{node.name or 'SG Smart'} ({node.unicast:04x})"
            if node.unicast in known_switches:
                switches.append((node.unicast, label))
            elif node.has_model(MODEL_SG_VENDOR):
                dimmers.append((node.unicast, label))
            elif node.cid in (0, 0x0EE8):
                # Not configured through HA yet (e.g. added with nRF Mesh):
                # no composition stored, so it could be a switch.
                switches.append((node.unicast, label))
        listed = {u for u, _ in switches} | {u for u, _ in dimmers}
        for unicast in sorted(known_switches - listed):
            switches.append((unicast, f"SG Smart switch ({unicast:04x})"))
        return dimmers, switches

    async def async_step_pair_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Make a dimmer react directly to a wheel / switch (or undo it)."""
        from .btmesh.sg_smart import SgSwitchCommand
        from .services import SWITCH_COMMANDS

        coordinator = getattr(self.config_entry, "runtime_data", None)
        if coordinator is None:
            return self.async_abort(reason="not_loaded")
        dimmers, switches = self._sg_nodes()
        if not dimmers or not switches:
            return self.async_abort(reason="nothing_to_pair")

        errors: dict[str, str] = {}
        if user_input is not None:
            node = int(user_input["node"], 16)
            switch = int(user_input["switch"], 16)
            if user_input["mode"] == "unpair":
                press = hold = rotate = SgSwitchCommand.NONE
            else:
                press = SWITCH_COMMANDS[user_input["press"]]
                hold = SWITCH_COMMANDS[user_input["hold"]]
                rotate = SWITCH_COMMANDS[user_input["rotate"]]
            ok = await coordinator.async_sg_pair_switch(
                node, switch, 4, press=press, hold=hold, rotate=rotate
            )
            if ok:
                names = dict(dimmers) | dict(switches)
                return self.async_abort(
                    reason="unpaired" if user_input["mode"] == "unpair" else "paired",
                    description_placeholders={
                        "switch": names.get(switch, f"{switch:04x}"),
                        "node": names.get(node, f"{node:04x}"),
                    },
                )
            errors["base"] = "send_failed"

        def select(options, translation_key=None):
            config = SelectSelectorConfig(
                options=options, mode=SelectSelectorMode.DROPDOWN
            )
            if translation_key:
                config["translation_key"] = translation_key
            return SelectSelector(config)

        def units(pairs):
            return [SelectOptionDict(value=f"{u:04x}", label=l) for u, l in pairs]

        commands = ["toggle_on_off", "on", "off", "dim", "dim_up", "dim_down", "none"]
        schema = vol.Schema(
            {
                vol.Required("switch", default=f"{switches[0][0]:04x}"): select(units(switches)),
                vol.Required("node", default=f"{dimmers[0][0]:04x}"): select(units(dimmers)),
                vol.Required("mode", default="pair"): select(["pair", "unpair"], "pair_mode"),
                vol.Required("press", default="toggle_on_off"): select(commands, "switch_command"),
                vol.Required("rotate", default="dim"): select(["dim", "none"], "switch_command"),
                vol.Required("hold", default="none"): select(commands, "switch_command"),
            }
        )
        return self.async_show_form(
            step_id="pair_switch", data_schema=schema, errors=errors
        )

    # ----------------------------------------------------- remove device

    _remove_task = None
    _remove_label = ""
    _remove_result: dict | None = None

    async def async_step_remove_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a device to factory reset and remove, like the SG app does."""
        coordinator = getattr(self.config_entry, "runtime_data", None)
        if coordinator is None:
            return self.async_abort(reason="not_loaded")
        dimmers, switches = self._sg_nodes()
        choices = dict(dimmers) | dict(switches)
        for node in coordinator.network.nodes:
            choices.setdefault(node.unicast, f"{node.name or 'Mesh'} ({node.unicast:04x})")
        if not choices:
            return self.async_abort(reason="nothing_to_remove")
        if user_input is not None:
            self._remove_unicast = int(user_input["device"], 16)
            self._remove_force = bool(user_input.get("force"))
            self._remove_label = choices.get(self._remove_unicast, user_input["device"])
            return await self.async_step_remove_device_run()
        schema = vol.Schema(
            {
                vol.Required("device"): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=f"{u:04x}", label=label)
                            for u, label in sorted(choices.items())
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional("force", default=False): bool,
            }
        )
        return self.async_show_form(step_id="remove_device", data_schema=schema)

    async def async_step_remove_device_run(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        from .provisioning import async_remove_device

        if self._remove_task is None:
            self._remove_task = self.hass.async_create_task(
                async_remove_device(
                    self.hass, self.config_entry, self._remove_unicast,
                    force=self._remove_force,
                ),
                "bluetooth_mesh remove device",
            )
        if not self._remove_task.done():
            return self.async_show_progress(
                step_id="remove_device_run",
                progress_action="removing",
                progress_task=self._remove_task,
                description_placeholders={"name": self._remove_label},
            )
        task, self._remove_task = self._remove_task, None
        try:
            self._remove_result = task.result()
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self._add_error = str(exc) or type(exc).__name__
            return self.async_show_progress_done(next_step_id="remove_device_failed")
        return self.async_show_progress_done(next_step_id="remove_device_done")

    async def async_step_remove_device_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        result = self._remove_result or {}
        return self.async_abort(
            reason="device_removed" if result.get("reset") else "device_forgotten",
            description_placeholders={"name": self._remove_label},
        )

    async def async_step_remove_device_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_abort(
            reason="remove_device_failed",
            description_placeholders={"name": self._remove_label, "error": self._add_error},
        )

    # ------------------------------------------------------------ backup

    async def async_step_backup(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        from .provisioning import async_write_backup

        path = await async_write_backup(self.hass, self.config_entry)
        return self.async_abort(
            reason="backup_written", description_placeholders={"path": path}
        )

    # -------------------------------------------------------- add device

    async def async_step_add_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick an unprovisioned device HA can see, and name it."""
        from .provisioning import discovered_unprovisioned

        if getattr(self.config_entry, "runtime_data", None) is None:
            return self.async_abort(reason="not_loaded")
        errors: dict[str, str] = {}
        if user_input is not None and user_input.get("device") not in (None, RESCAN):
            chosen = [b for b in self._beacons if b.address == user_input["device"]]
            if chosen:
                self._add_name = (user_input.get("name") or "").strip() or chosen[0].label
                self._add_beacon = chosen[0]
                return await self.async_step_add_device_run()
        self._beacons = discovered_unprovisioned(self.hass)
        if not self._beacons:
            errors["base"] = "no_devices"
            return self.async_show_form(
                step_id="add_device", data_schema=vol.Schema({}), errors=errors
            )
        schema = vol.Schema(
            {
                vol.Required("device", default=RESCAN): SelectSelector(
                    SelectSelectorConfig(
                        options=[SelectOptionDict(value=RESCAN, label="🔄 Search again")]
                        + [
                            SelectOptionDict(value=b.address, label=b.label)
                            for b in self._beacons
                        ],
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Optional("name", default=""): TextSelector(),
            }
        )
        return self.async_show_form(step_id="add_device", data_schema=schema, errors=errors)

    async def async_step_add_device_run(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Provision + configure in the background, with a progress spinner."""
        from .provisioning import async_add_device

        if self._add_task is None:
            self._add_task = self.hass.async_create_task(
                async_add_device(
                    self.hass, self.config_entry, self._add_beacon, self._add_name
                ),
                "bluetooth_mesh add device",
            )
        if not self._add_task.done():
            return self.async_show_progress(
                step_id="add_device_run",
                progress_action="adding",
                progress_task=self._add_task,
                description_placeholders={"name": self._add_name},
            )
        try:
            self._add_result = self._add_task.result()
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self._add_error = str(exc) or type(exc).__name__
            self._add_task = None
            return self.async_show_progress_done(next_step_id="add_device_failed")
        self._add_task = None
        return self.async_show_progress_done(next_step_id="add_device_done")

    async def async_step_add_device_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        result = self._add_result or {}
        placeholders = {
            "name": result.get("name", self._add_name),
            "unicast": result.get("unicast", "?"),
            "error": result.get("error", ""),
        }
        if not result.get("configured"):
            return self.async_abort(
                reason="device_added_unconfigured",
                description_placeholders=placeholders,
            )
        return self.async_abort(
            reason="device_added_switch" if result.get("kind") == "switch"
            else "device_added",
            description_placeholders=placeholders,
        )

    async def async_step_add_device_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_abort(
            reason="add_device_failed",
            description_placeholders={"error": self._add_error},
        )

    # ---------------------------------------------------------- settings

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show/store the keep-alive timeout."""
        if user_input is not None:
            data = {
                CONF_KEEPALIVE: int(user_input[CONF_KEEPALIVE]),
                CONF_SRC_ADDR: int(user_input[CONF_SRC_ADDR]),
            }
            # The field is absent from the form when the network has no CTL
            # lamp. Carry the stored value through rather than letting it go:
            # this replaces the whole options dict, and a dropped key is not
            # read as "empty" but as "never configured" — so the next setup
            # would seed the vendor default back over the user's choice.
            if CONF_INVERTED_CTL in user_input:
                listed = {node.unicast for node in self._ctl_nodes()}
                data[CONF_INVERTED_CTL] = sorted(
                    {int(value, 16) for value in user_input[CONF_INVERTED_CTL]}
                    # An address the form could not show was not unticked:
                    # keep it rather than drop a choice nobody saw.
                    | {
                        address
                        for address in self.config_entry.options.get(
                            CONF_INVERTED_CTL, []
                        )
                        if address not in listed
                    }
                )
            elif CONF_INVERTED_CTL in self.config_entry.options:
                data[CONF_INVERTED_CTL] = self.config_entry.options[
                    CONF_INVERTED_CTL
                ]
            # Not on the form: the SG switches heard on the mesh (dropping them
            # would orphan their entities) and the SG dimmers' minimum levels.
            for key in (CONF_SG_SWITCHES, CONF_SG_MIN_LEVEL, CONF_RETIRED_UNICASTS):
                if key in self.config_entry.options:
                    data[key] = self.config_entry.options[key]
            return self.async_create_entry(data=data)

        current = self.config_entry.options.get(
            CONF_KEEPALIVE, DEFAULT_KEEPALIVE
        )
        current_src = self.config_entry.options.get(
            CONF_SRC_ADDR, DEFAULT_SRC_ADDR
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_KEEPALIVE, default=current): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=3600, step=1, unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_SRC_ADDR, default=current_src
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=0x7FFF, step=1, mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        if ctl_nodes := self._ctl_nodes():
            listed = {node.unicast for node in ctl_nodes}
            # Only what the list can show. A stored address whose lamp has
            # left the export (re-imported without it) made the default hold
            # a value the selector does not offer, and the form then refused
            # every submit, keep-alive and source address included.
            current_inverted = [
                address
                for address in self.config_entry.options.get(CONF_INVERTED_CTL, [])
                if address in listed
            ]
            schema = schema.extend(
                {
                    vol.Optional(
                        CONF_INVERTED_CTL,
                        default=[f"{a:04x}" for a in current_inverted],
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=f"{node.unicast:04x}",
                                    label=node.name
                                    or f"Mesh {node.unicast:04x}",
                                )
                                for node in ctl_nodes
                            ],
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            )
        return self.async_show_form(step_id="settings", data_schema=schema)

    def _ctl_nodes(self) -> list:
        """The nodes whose colour temperature the mirror could apply to.

        Read from the stored export rather than from the running coordinator so
        the form still lists the lamps when no proxy is in range. A damaged
        export is not this step's problem — the setup already refuses the entry
        with a message pointing at the reconfigure flow — so it degrades to an
        empty list and simply omits the field.
        """
        try:
            network = Network.from_connect(
                json.loads(self.config_entry.data[CONF_CONNECT_JSON])
            )
        except (json.JSONDecodeError, NetworkModelError, KeyError):
            return []
        return [n for n in network.nodes if n.has_model(MODEL_LIGHT_CTL)]
