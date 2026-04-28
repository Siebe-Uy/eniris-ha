"""Constants for the Eniris SmartgridOne integration."""

from __future__ import annotations

from datetime import timedelta

try:
    from homeassistant.const import Platform
except ModuleNotFoundError:
    class Platform:
        """Fallback platform values for tests without Home Assistant installed."""

        SENSOR = "sensor"

DOMAIN = "eniris_smartgridone"
PLATFORMS: list[Platform] = [Platform.SENSOR]

AUTH_BASE_URL = "https://authentication.eniris.be"
API_BASE_URL = "https://api.eniris.be"

CONF_REFRESH_TOKEN = "refresh_token"
CONF_USERNAME = "username"
CONF_CONTROLLER_ID = "controller_id"
CONF_CONTROLLER_SERIAL = "controller_serial"

DEFAULT_SCAN_INTERVAL = timedelta(seconds=60)

RETENTION_POLICIES = ("rp_one_s", "rp_one_m")

ATTR_CONTROLLER_ID = "controller_id"
ATTR_DEVICE_ID = "eniris_device_id"
ATTR_MEASUREMENT = "measurement"
ATTR_RETENTION_POLICY = "retention_policy"

POWER_FIELDS = {
    "actualPowerTot_W": ("W", "power"),
    "childrenStoragePower_W": ("W", "power"),
    "childrenProducedPower_W": ("W", "power"),
    "childrenEVPower_W": ("W", "power"),
    "childrenConsumedPower_W": ("W", "power"),
    "childrenOtherPower_W": ("W", "power"),
    "powerSetpoint_W": ("W", "power"),
    "reactivePowerSetpoint_VAr": ("VAr", "reactive_power"),
    "reacPowerSetpoint_VAr": ("VAr", "reactive_power"),
    "setpoint_W": ("W", "power"),
    "importLimit_W": ("W", "power"),
    "exportLimit_W": ("W", "power"),
}

ENERGY_FIELDS = {
    "importedAbsEnergyTot_Wh": ("Wh", "energy"),
    "exportedAbsEnergyTot_Wh": ("Wh", "energy"),
    "producedAbsEnergyTot_Wh": ("Wh", "energy"),
    "consumedAbsEnergyTot_Wh": ("Wh", "energy"),
    "chargedAbsEnergyTot_Wh": ("Wh", "energy"),
    "dischargedAbsEnergyTot_Wh": ("Wh", "energy"),
    "importedEnergyDeltaTot_Wh": ("Wh", "energy"),
    "exportedEnergyDeltaTot_Wh": ("Wh", "energy"),
    "childrenStorageChargedEnergyDeltaTot_Wh": ("Wh", "energy"),
    "childrenStorageDischargedEnergyDeltaTot_Wh": ("Wh", "energy"),
    "producedEnergyDeltaTot_Wh": ("Wh", "energy"),
    "chargedEnergyDeltaTot_Wh": ("Wh", "energy"),
    "dischargedEnergyDeltaTot_Wh": ("Wh", "energy"),
}

OTHER_FIELDS = {
    "currentL1_A": ("A", "current"),
    "currentL2_A": ("A", "current"),
    "currentL3_A": ("A", "current"),
    "currentN_A": ("A", "current"),
    "voltageL1N_V": ("V", "voltage"),
    "voltageL2N_V": ("V", "voltage"),
    "voltageL3N_V": ("V", "voltage"),
    "voltageL1L2_V": ("V", "voltage"),
    "voltageL2L3_V": ("V", "voltage"),
    "voltageL3L1_V": ("V", "voltage"),
    "frequency_Hz": ("Hz", "frequency"),
    "powerFactorTot": (None, "power_factor"),
    "powerFactorL1": (None, "power_factor"),
    "powerFactorL2": (None, "power_factor"),
    "powerFactorL3": (None, "power_factor"),
    "voltageDC_V": ("V", "voltage"),
    "currentDC_A": ("A", "current"),
    "status": (None, "enum"),
    "operationMode": (None, "enum"),
    "stateOfCharge_frac": ("%", "battery"),
    "stateOfHealth_frac": ("%", "battery"),
    "childrenStorageStateOfCharge_frac": ("%", "battery"),
    "batteryCurrent_A": ("A", "current"),
    "batteryVoltage_V": ("V", "voltage"),
    "evRequiringCharge": (None, "boolean"),
    "policy": (None, "enum"),
    "strategy": (None, "enum"),
    "signalActive": (None, "boolean"),
    "constraint_ph0_label": (None, "enum"),
}

TELEMETRY_FIELDS = {
    **POWER_FIELDS,
    **ENERGY_FIELDS,
    **OTHER_FIELDS,
}

DEFAULT_MEASUREMENTS = (
    "batteryMetrics",
    "boilerMetrics",
    "evChargerMetrics",
    "externalSignalMetrics",
    "gridMetrics",
    "heatPumpMetrics",
    "hybridInverterMetrics",
    "installationMetrics",
    "planning",
    "solarInstallationMetrics",
    "solarInverterMetrics",
    "solarOptimizerMetrics",
    "solarStringMetrics",
    "submeteringMetrics",
    "switchedLoadMetrics",
)
