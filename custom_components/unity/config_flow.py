"""Config flow: one hub, and one subentry per child instance."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlowWithReload,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import climate as climate_logic
from .const import (
    CONF_AREA_PREFIX,
    CONF_BLOCKED_COVER_CLASSES,
    CONF_CLIMATE_ENTITY,
    CONF_COMMON_FLOORS,
    CONF_COMMON_LABEL,
    CONF_CURRENT_SENSOR,
    CONF_EXPIRE_AFTER,
    CONF_EXPIRE_MODE,
    CONF_HUB_STATUS_TOPIC,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_OBJECT,
    CONF_TARGET_SENSOR,
    CONF_UNIT,
    CONF_UNIT_FLOORS,
    CONF_UNIT_LABEL,
    COVER_CLASSES,
    DEFAULT_BLOCKED,
    DEFAULT_HUB_STATUS,
    DOMAIN,
    SUBENTRY_CHILD,
    SUBENTRY_CLIMATE,
)
from .topics import valid_hub_status_topic, valid_object, valid_unit

import re


def _hub_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                CONF_COMMON_LABEL, description={"suggested_value": defaults.get(CONF_COMMON_LABEL)}
            ): selector.LabelSelector(selector.LabelSelectorConfig()),
            vol.Optional(
                CONF_COMMON_FLOORS, description={"suggested_value": defaults.get(CONF_COMMON_FLOORS, [])}
            ): selector.FloorSelector(selector.FloorSelectorConfig(multiple=True)),
            vol.Required(
                CONF_HUB_STATUS_TOPIC, default=defaults.get(CONF_HUB_STATUS_TOPIC, DEFAULT_HUB_STATUS)
            ): selector.TextSelector(),
            vol.Optional(
                CONF_AREA_PREFIX, default=defaults.get(CONF_AREA_PREFIX, "")
            ): selector.TextSelector(),
            vol.Required(
                CONF_BLOCKED_COVER_CLASSES, default=defaults.get(CONF_BLOCKED_COVER_CLASSES, DEFAULT_BLOCKED)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(options=COVER_CLASSES, multiple=True)
            ),
        }
    )


def _validate_hub(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not valid_hub_status_topic(user_input[CONF_HUB_STATUS_TOPIC]):
        errors[CONF_HUB_STATUS_TOPIC] = "invalid_topic"
    prefix = user_input.get(CONF_AREA_PREFIX) or ""
    if prefix:
        try:
            re.compile(prefix)
        except re.error:
            errors[CONF_AREA_PREFIX] = "invalid_regex"
    # The common label and floors work as a pair: one without the other exposes nothing.
    if bool(user_input.get(CONF_COMMON_LABEL)) != bool(user_input.get(CONF_COMMON_FLOORS)):
        errors["base"] = "common_pair"
    return errors


class UnityConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_hub(user_input)
            if not errors:
                return self.async_create_entry(title="Unity", data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=_hub_schema(user_input or {}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowWithReload:
        return UnityOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_CHILD: ChildSubentryFlow, SUBENTRY_CLIMATE: ClimateSubentryFlow}


class UnityOptionsFlow(OptionsFlowWithReload):
    """Edit the hub settings (stored in the entry's data)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_hub(user_input)
            if not errors:
                self.hass.config_entries.async_update_entry(self.config_entry, data=user_input)
                return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="init",
            data_schema=_hub_schema(user_input or dict(self.config_entry.data)),
            errors=errors,
        )


def _child_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_UNIT, default=defaults.get(CONF_UNIT, "")): selector.TextSelector(),
            vol.Required(
                CONF_UNIT_LABEL, description={"suggested_value": defaults.get(CONF_UNIT_LABEL)}
            ): selector.LabelSelector(selector.LabelSelectorConfig()),
            vol.Required(
                CONF_UNIT_FLOORS, description={"suggested_value": defaults.get(CONF_UNIT_FLOORS, [])}
            ): selector.FloorSelector(selector.FloorSelectorConfig(multiple=True)),
        }
    )


class ChildSubentryFlow(ConfigSubentryFlow):
    """Add or edit one child instance."""

    def _errors(self, user_input: dict[str, Any], current_unit: str | None) -> dict[str, str]:
        errors: dict[str, str] = {}
        unit = user_input[CONF_UNIT]
        if not valid_unit(unit):
            errors[CONF_UNIT] = "invalid_unit"
        elif unit != current_unit and any(
            s.data.get(CONF_UNIT) == unit for s in self._get_entry().subentries.values()
        ):
            errors[CONF_UNIT] = "duplicate_unit"
        if not user_input.get(CONF_UNIT_FLOORS):
            errors[CONF_UNIT_FLOORS] = "no_floors"
        return errors

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._errors(user_input, None)
            if not errors:
                return self.async_create_entry(title=user_input[CONF_UNIT], data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=_child_schema(user_input or {}), errors=errors
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        sub = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._errors(user_input, sub.data.get(CONF_UNIT))
            if not errors:
                return self.async_update_and_abort(
                    self._get_entry(), sub, title=user_input[CONF_UNIT], data=user_input
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_child_schema(user_input or dict(sub.data)),
            errors=errors,
        )


# --------------------------------------------------------------- thermostats
def _child_units(entry: ConfigEntry) -> list[str]:
    return [
        sub.data[CONF_UNIT]
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_CHILD and sub.data.get(CONF_UNIT)
    ]


def _climate_schema(units: list[str], defaults: dict[str, Any], fahrenheit: bool) -> vol.Schema:
    lo, hi = (64, 80) if fahrenheit else (18, 27)
    number = lambda mn, mx, step: selector.NumberSelector(  # noqa: E731
        selector.NumberSelectorConfig(min=mn, max=mx, step=step, mode=selector.NumberSelectorMode.BOX)
    )
    return vol.Schema(
        {
            vol.Required(CONF_UNIT, default=defaults.get(CONF_UNIT, units[0] if units else "")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=units, mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            vol.Required(
                CONF_CLIMATE_ENTITY, description={"suggested_value": defaults.get(CONF_CLIMATE_ENTITY)}
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="climate")),
            vol.Optional(CONF_OBJECT, description={"suggested_value": defaults.get(CONF_OBJECT)}): selector.TextSelector(),
            vol.Required(CONF_MIN_TEMP, default=defaults.get(CONF_MIN_TEMP, lo)): number(-30, 120, 0.5),
            vol.Required(CONF_MAX_TEMP, default=defaults.get(CONF_MAX_TEMP, hi)): number(-30, 120, 0.5),
            vol.Optional(
                CONF_CURRENT_SENSOR, description={"suggested_value": defaults.get(CONF_CURRENT_SENSOR)}
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                CONF_TARGET_SENSOR, description={"suggested_value": defaults.get(CONF_TARGET_SENSOR)}
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(CONF_EXPIRE_AFTER, default=defaults.get(CONF_EXPIRE_AFTER, 0)): number(0, 1440, 1),
            vol.Required(CONF_EXPIRE_MODE, default=defaults.get(CONF_EXPIRE_MODE, climate_logic.EXPIRE_NONE)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(climate_logic.EXPIRE_MODES),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key="expire_mode",
                )
            ),
        }
    )


class ClimateSubentryFlow(ConfigSubentryFlow):
    """Add or edit a thermostat: one hub climate entity granted to one child."""

    def _normalise(self, user_input: dict[str, Any]) -> dict[str, Any]:
        data = dict(user_input)
        entity = data[CONF_CLIMATE_ENTITY]
        data[CONF_OBJECT] = (data.get(CONF_OBJECT) or entity.split(".", 1)[1]).strip().lower()
        data[CONF_MIN_TEMP] = float(data[CONF_MIN_TEMP])
        data[CONF_MAX_TEMP] = float(data[CONF_MAX_TEMP])
        data[CONF_EXPIRE_AFTER] = int(data[CONF_EXPIRE_AFTER])
        for key in (CONF_CURRENT_SENSOR, CONF_TARGET_SENSOR):
            data[key] = data.get(key) or None
        return data

    def _errors(self, data: dict[str, Any], current: tuple[str, str] | None) -> dict[str, str]:
        errors: dict[str, str] = {}
        entry = self._get_entry()
        if data[CONF_UNIT] not in _child_units(entry):
            errors[CONF_UNIT] = "unknown_unit"
        if not valid_object(data[CONF_OBJECT]):
            errors[CONF_OBJECT] = "invalid_object"
        else:
            key = (data[CONF_UNIT], data[CONF_OBJECT])
            if key != current and any(
                sub.subentry_type == SUBENTRY_CLIMATE
                and (sub.data.get(CONF_UNIT), sub.data.get(CONF_OBJECT)) == key
                for sub in entry.subentries.values()
            ):
                errors[CONF_OBJECT] = "duplicate_thermostat"
        if not data[CONF_MIN_TEMP] < data[CONF_MAX_TEMP]:
            errors[CONF_MAX_TEMP] = "range"
        minutes, mode = data[CONF_EXPIRE_AFTER], data[CONF_EXPIRE_MODE]
        if minutes > 0 and mode == climate_logic.EXPIRE_NONE:
            errors[CONF_EXPIRE_MODE] = "expire_needs_mode"
        elif minutes == 0 and mode != climate_logic.EXPIRE_NONE:
            errors[CONF_EXPIRE_AFTER] = "expire_needs_minutes"
        state = self.hass.states.get(data[CONF_CLIMATE_ENTITY])
        if state is not None and state.state not in ("unavailable", "unknown"):
            e_lo, e_hi = state.attributes.get("min_temp"), state.attributes.get("max_temp")
            if (
                isinstance(e_lo, (int, float)) and isinstance(e_hi, (int, float))
                and (data[CONF_MAX_TEMP] < e_lo or data[CONF_MIN_TEMP] > e_hi)
            ):
                errors[CONF_MIN_TEMP] = "range_outside_entity"
            if mode == climate_logic.EXPIRE_PRESET_NONE and "none" not in (state.attributes.get("preset_modes") or []):
                errors[CONF_EXPIRE_MODE] = "no_none_preset"
        return errors

    def _form(self, step_id: str, user_input: dict[str, Any] | None, errors: dict[str, str]) -> SubentryFlowResult:
        units = _child_units(self._get_entry())
        fahrenheit = "F" in self.hass.config.units.temperature_unit
        return self.async_show_form(
            step_id=step_id, data_schema=_climate_schema(units, user_input or {}, fahrenheit), errors=errors
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if not _child_units(self._get_entry()):
            return self.async_abort(reason="no_children")
        errors: dict[str, str] = {}
        if user_input is not None:
            data = self._normalise(user_input)
            errors = self._errors(data, None)
            if not errors:
                return self.async_create_entry(title=f"{data[CONF_UNIT]}: {data[CONF_OBJECT]}", data=data)
        return self._form("user", user_input, errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        sub = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = self._normalise(user_input)
            errors = self._errors(data, (sub.data.get(CONF_UNIT), sub.data.get(CONF_OBJECT)))
            if not errors:
                return self.async_update_and_abort(
                    self._get_entry(), sub, title=f"{data[CONF_UNIT]}: {data[CONF_OBJECT]}", data=data
                )
        return self._form("reconfigure", user_input or dict(sub.data), errors)
