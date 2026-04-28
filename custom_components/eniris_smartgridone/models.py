"""Models and discovery helpers for Eniris SmartgridOne."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from .const import DEFAULT_MEASUREMENTS, RETENTION_POLICIES


JsonObject = dict[str, Any]


@dataclass(slots=True, frozen=True)
class TelemetrySource:
    """A concrete Eniris telemetry source for a device."""

    measurement: str
    retention_policy: str
    tags: dict[str, str]
    database: str | None = None
    namespace: dict[str, Any] | None = None

    @property
    def key(self) -> str:
        """Return a stable key for this source."""
        namespace = self.namespace or {}
        scope = self.database or namespace.get("value") or namespace.get("bucket") or "default"
        tags = ",".join(f"{key}={value}" for key, value in sorted(self.tags.items()))
        return f"{self.measurement}:{self.retention_policy}:{scope}:{tags}"


@dataclass(slots=True)
class EnirisDevice:
    """A device returned by the Eniris metadata API."""

    id: int
    last_update: str | None
    properties: JsonObject
    user_rights: JsonObject = field(default_factory=dict)

    @property
    def node_id(self) -> str:
        """Return the Eniris node id if one is present."""
        return str(_first_value(self.properties, "nodeId", "node_id", "id") or self.id)

    @property
    def node_type(self) -> str:
        """Return the Eniris node type."""
        return str(_first_value(self.properties, "nodeType", "node_type", "type") or "")

    @property
    def name(self) -> str:
        """Return a user-facing device name."""
        return str(
            _first_value(
                self.properties,
                "name",
                "displayName",
                "display_name",
                "label",
                "description",
            )
            or _nested_value(self.properties, ("info", "name"))
            or _nested_value(self.properties, ("location", "name"))
            or self.node_id
        )

    @property
    def manufacturer(self) -> str | None:
        """Return manufacturer metadata if available."""
        value = _first_value(self.properties, "manufacturer", "brand", "vendor")
        return str(value) if value else None

    @property
    def model(self) -> str | None:
        """Return model metadata if available."""
        value = _first_value(self.properties, "model", "modelName", "deviceModel")
        return str(value) if value else None

    @property
    def serial_number(self) -> str:
        """Return the best serial number for this device."""
        value = _first_value(
            self.properties,
            "serialNumber",
            "serial_number",
            "serial",
            "serialNo",
            "controllerSerial",
            "controller_serial",
            "hardwareSerial",
            "hardware_serial",
        )
        if not value:
            value = _nested_value(self.properties, ("info", "serialNumber"))
        return clean_controller_serial(str(value or self.node_id))

    @property
    def is_controller(self) -> bool:
        """Return true if this device looks like a SmartgridOne controller."""
        haystack = " ".join(
            str(part).lower()
            for part in (
                self.node_type,
                self.name,
                self.model,
                _first_value(self.properties, "product", "productName", "deviceClass"),
            )
            if part
        )
        return any(
            token in haystack
            for token in (
                "smartgridone",
                "smartgrid one",
                "smartgrid-one",
                "controller",
                "rp_one",
            )
        )

    @property
    def controller_node_id(self) -> str | None:
        """Return the node id of the controller this device belongs to."""
        value = _first_value(
            self.properties,
            "controllerNodeId",
            "controller_node_id",
            "parentNodeId",
            "parent_node_id",
            "gatewayNodeId",
            "gateway_node_id",
            "edgeNodeId",
            "edge_node_id",
            "smartgridOneNodeId",
            "smartgridoneNodeId",
        )
        if not value:
            value = _nested_value(self.properties, ("controller", "nodeId"))
        return str(value) if value else None

    @property
    def tags(self) -> dict[str, str]:
        """Return telemetry tags found on the device."""
        for key in ("telemetryTags", "telemetry_tags", "tags", "monitoringTags"):
            raw = self.properties.get(key)
            if isinstance(raw, dict):
                return {str(tag): str(value) for tag, value in raw.items() if value is not None}

        tags: dict[str, str] = {}
        if self.node_id:
            tags["nodeId"] = self.node_id
        tags["deviceId"] = str(self.id)
        return tags

    @property
    def telemetry_sources(self) -> list[TelemetrySource]:
        """Return possible telemetry sources for this device."""
        explicit = _explicit_sources(self.properties)
        if explicit:
            return explicit

        database = _first_value(self.properties, "database", "telemetryDatabase", "db")
        namespace = _namespace_from_properties(self.properties)
        if not database and not namespace:
            return []

        measurements = _measurements_from_properties(self.properties)
        retention_policies = _retention_policies_from_properties(self.properties)
        return [
            TelemetrySource(
                measurement=measurement,
                retention_policy=retention_policy,
                tags=self.tags,
                database=str(database) if database else None,
                namespace=namespace,
            )
            for measurement in measurements
            for retention_policy in retention_policies
        ]


@dataclass(slots=True)
class EnirisController:
    """A SmartgridOne controller and its child devices."""

    device: EnirisDevice
    children: list[EnirisDevice] = field(default_factory=list)

    @property
    def id(self) -> str:
        """Return a stable controller id."""
        return self.device.node_id

    @property
    def name(self) -> str:
        """Return a display name."""
        return self.serial_number

    @property
    def serial_number(self) -> str:
        """Return the controller serial number."""
        return self.device.serial_number


def parse_devices(payload: JsonObject) -> list[EnirisDevice]:
    """Parse a `/v1/device/query` response."""
    devices = payload.get("device", [])
    if not isinstance(devices, list):
        return []

    parsed: list[EnirisDevice] = []
    for item in devices:
        if not isinstance(item, dict):
            continue
        properties = item.get("properties") if isinstance(item.get("properties"), dict) else {}
        parsed.append(
            EnirisDevice(
                id=int(item["id"]),
                last_update=item.get("lastUpdate"),
                properties=properties,
                user_rights=item.get("userRights") if isinstance(item.get("userRights"), dict) else {},
            )
        )
    return parsed


def group_controllers(devices: list[EnirisDevice]) -> list[EnirisController]:
    """Group devices under their discovered SmartgridOne controllers."""
    controllers = {device.node_id: EnirisController(device=device) for device in devices if device.is_controller}

    if not controllers and devices:
        # If the metadata does not expose controller devices, create a synthetic hub so
        # all entities still have a stable parent in the device registry.
        controllers["eniris"] = EnirisController(
            device=EnirisDevice(
                id=0,
                last_update=None,
                properties={"nodeId": "eniris", "name": "Eniris SmartgridOne"},
            )
        )

    for device in devices:
        if device.node_id in controllers:
            continue
        controller = _controller_for_device(device, controllers)
        if controller is not None:
            controller.children.append(device)

    return list(controllers.values())


def clean_controller_serial(value: str) -> str:
    """Return the controller serial without Eniris site suffixes."""
    return re.sub(r"_site_\d+$", "", value)


def _controller_for_device(
    device: EnirisDevice,
    controllers: dict[str, EnirisController],
) -> EnirisController | None:
    """Find the most likely controller for a child device."""
    controller_id = device.controller_node_id
    if controller_id and controller_id in controllers:
        return controllers[controller_id]

    if len(controllers) == 1:
        return next(iter(controllers.values()))

    device_values = _flatten_strings(device.properties)
    for controller in controllers.values():
        candidates = {
            controller.id,
            controller.serial_number,
            f"{controller.serial_number}_site_0",
        }
        if any(candidate and candidate in device_values for candidate in candidates):
            return controller
        if any(
            candidate and any(candidate in value for value in device_values)
            for candidate in candidates
        ):
            return controller

    return None


def _first_value(data: JsonObject, *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _nested_value(data: JsonObject, path: tuple[str, ...]) -> Any:
    current: Any = data
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _flatten_strings(value: Any) -> set[str]:
    """Return all string-ish values from nested metadata."""
    if isinstance(value, dict):
        result: set[str] = set()
        for key, item in value.items():
            result.add(str(key))
            result.update(_flatten_strings(item))
        return result
    if isinstance(value, list):
        result: set[str] = set()
        for item in value:
            result.update(_flatten_strings(item))
        return result
    if value is None:
        return set()
    return {str(value)}


def _explicit_sources(properties: JsonObject) -> list[TelemetrySource]:
    raw_sources = _first_value(properties, "telemetrySources", "telemetry_sources", "sources")
    if not isinstance(raw_sources, list):
        return []

    sources: list[TelemetrySource] = []
    for raw in raw_sources:
        if not isinstance(raw, dict):
            continue
        measurement = raw.get("measurement")
        retention_policy = raw.get("retentionPolicy") or raw.get("retention_policy")
        if not measurement or retention_policy not in RETENTION_POLICIES:
            continue
        raw_tags = raw.get("tags") if isinstance(raw.get("tags"), dict) else {}
        sources.append(
            TelemetrySource(
                measurement=str(measurement),
                retention_policy=str(retention_policy),
                tags={str(key): str(value) for key, value in raw_tags.items()},
                database=str(raw["database"]) if raw.get("database") else None,
                namespace=raw.get("namespace") if isinstance(raw.get("namespace"), dict) else None,
            )
        )
    return sources


def _measurements_from_properties(properties: JsonObject) -> tuple[str, ...]:
    raw = _first_value(properties, "measurements", "measurement", "telemetryMeasurements")
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, list):
        values = tuple(str(item) for item in raw if item)
        if values:
            return values

    node_type = str(_first_value(properties, "nodeType", "type") or "").lower()
    if "solar" in node_type and "string" in node_type:
        return ("solarStringMetrics",)
    if "solar" in node_type:
        return ("solarInverterMetrics",)
    if "battery" in node_type:
        return ("batteryMetrics",)
    if "ev" in node_type or "charger" in node_type:
        return ("evChargerMetrics",)
    if "hybrid" in node_type:
        return ("hybridInverterMetrics",)
    if "grid" in node_type or "submeter" in node_type:
        return ("submeteringMetrics",)
    return DEFAULT_MEASUREMENTS


def _retention_policies_from_properties(properties: JsonObject) -> tuple[str, ...]:
    raw = _first_value(properties, "retentionPolicy", "retention_policy", "retentionPolicies")
    if isinstance(raw, str) and raw in RETENTION_POLICIES:
        return (raw,)
    if isinstance(raw, list):
        values = tuple(str(item) for item in raw if item in RETENTION_POLICIES)
        if values:
            return values
    return RETENTION_POLICIES


def _namespace_from_properties(properties: JsonObject) -> dict[str, Any] | None:
    raw = _first_value(properties, "namespace", "telemetryNamespace")
    if isinstance(raw, dict):
        return raw

    organization = _first_value(properties, "organization", "telemetryOrganization")
    bucket = _first_value(properties, "bucket", "telemetryBucket")
    if organization and bucket:
        return {"version": "2", "organization": str(organization), "bucket": str(bucket)}

    namespace = _first_value(properties, "ioxNamespace", "telemetryIoxNamespace")
    if namespace:
        return {"version": "IOx", "value": str(namespace)}

    return None
