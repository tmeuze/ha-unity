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

from .const import (
    CONF_AREA_PREFIX,
    CONF_BLOCKED_COVER_CLASSES,
    CONF_COMMON_FLOORS,
    CONF_COMMON_LABEL,
    CONF_HUB_STATUS_TOPIC,
    CONF_UNIT,
    CONF_UNIT_FLOORS,
    CONF_UNIT_LABEL,
    COVER_CLASSES,
    DEFAULT_BLOCKED,
    DEFAULT_HUB_STATUS,
    DOMAIN,
    SUBENTRY_CHILD,
)
from .topics import valid_hub_status_topic, valid_unit

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
        return {SUBENTRY_CHILD: ChildSubentryFlow}


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
