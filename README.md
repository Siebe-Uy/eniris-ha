# Eniris SmartgridOne for Home Assistant

Warning: this is a community integration and is not officially supported by Eniris. If you need help, please create an issue on GitHub. Do not contact Eniris support for issues with this integration.

This custom Home Assistant integration connects to the Eniris cloud API, discovers SmartgridOne controllers linked to your Eniris account, and exposes controller devices such as power meters, inverters, batteries, and optimizers as Home Assistant devices and entities.

## Features

- Login with an Eniris SmartgridOne account.
- Automatic discovery of all SmartgridOne controllers available to that account.
- One Home Assistant integration entry per controller, named after the controller serial number.
- Devices are grouped under their controller hub.
- Infrastructure nodes such as `smartgridController`, `smartgridControllerSite`, and `switchboard` are hidden.
- Entities are created from available `rp_one_s` and `rp_one_m` telemetry fields only.
- Entity names show the retention policy, for example `Actual Power Total (s)` and `Actual Power Total (m)`.
- Cumulative energy entities are used directly when available.
- If a device only provides instantaneous power, the integration automatically creates a derived energy sensor suitable for the Home Assistant Energy dashboard.

## Installation

### HACS

1. Add this repository as a custom repository in HACS.
2. Select the Integration category.
3. Install `Eniris SmartgridOne`.
4. Restart Home Assistant.
5. Add the integration from Settings > Devices & services.

HACS should install this integration from a semantic GitHub release tag such as `v0.1.0`. Installing from a raw commit hash can trigger HACS version validation errors.

### Manual

1. Copy `custom_components/eniris_smartgridone` into your Home Assistant `custom_components` folder.
2. Restart Home Assistant.
3. Add `Eniris SmartgridOne` from Settings > Devices & services.
4. Enter your Eniris credentials.

## Configuration

During setup, enter the Eniris account credentials that have access to your SmartgridOne controllers. If multiple controllers are linked to the account, the integration creates a separate Home Assistant config entry for each controller.

Each controller entry contains the devices belonging to that controller. Each device receives entities for the telemetry fields that return real data.

## Energy Dashboard

Home Assistant's Energy dashboard requires cumulative energy sensors with state class `total_increasing`.

This integration handles that automatically:

- Eniris-provided Wh totals and deltas are not exposed as entities.
- Imported and exported energy sensors are derived from `actualPowerTot_W` using a Riemann-sum style integration.
- Positive power is accumulated as imported energy; negative power is accumulated as exported energy.
- Derived energy sensors are attached to the same Home Assistant device as the source power sensor.
- Derived values are restored across Home Assistant restarts.

For best results, source power sensors should update at least once per minute.

## Troubleshooting

- If expected devices do not appear, reload the integration after confirming the Eniris account has access to the controller.
- If stale controller or device names remain after upgrading from an older version, remove the old device registry entries or re-add the integration once.
- If an entity is missing, the corresponding Eniris telemetry field may not be available for that device or retention policy.
- If an account requires two-factor authentication, setup will show an unsupported 2FA message.

## Contributing

Pull requests and issues are welcome. Please include relevant device metadata when reporting discovery or naming problems, but remove secrets, tokens, and personal information first.

## License

MIT
