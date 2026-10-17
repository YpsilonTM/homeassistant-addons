# Home Assistant Add-ons

Community add-on repository for [Home Assistant](https://www.home-assistant.io/) Supervisor.

Add this URL under **Settings → Add-ons → Add-on store → ⋮ → Repositories**:

`https://github.com/YpsilonTM/homeassistant-addons`

[![Open your Home Assistant instance and show the add app repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FYpsilonTM%2Fhomeassistant-addons)

## Add-ons

| Add-on | Description | Docs |
|--------|-------------|------|
| **ModQTT** | Modbus TCP → MQTT bridge | [DOCS.md](modqtt/DOCS.md) |
| **Sungrow Modbus to MQTT** | Unofficial Sungrow hybrid inverter Modbus TCP → MQTT. Not affiliated with Sungrow. | [DOCS.md](sungrowmod2mqtt/DOCS.md) |
| **Sigen EV Local to MQTT** | Unofficial local NearTerminal control (full EVAC catalog). Not affiliated with Sigenergy. | [DOCS.md](sigenevlocal2mqtt/DOCS.md) |

### Deprecated

These remain installable for existing users but are no longer maintained. Use **Sigen EV Local to MQTT** instead.

| Add-on | Description | Docs |
|--------|-------------|------|
| **OCPP SIGEN to MQTT** | OCPP 1.6 central system for Sigen EV chargers | [DOCS.md](ocppsigen2mqtt/DOCS.md) |
| **Sigen EV Cloud to MQTT** | Sigenergy cloud control (current, start/stop, phase mode) | [DOCS.md](sigenevcloud2mqtt/DOCS.md) |

Pre-built images: `ghcr.io/ypsilontm/homeassistant-addons/addon-<slug>`

## Repository layout

One git repository = one HA add-on **store** (`repository.yaml` at the root). Each top-level folder with a `config.yaml` is an installable add-on.

See [docs/REPOSITORY.md](docs/REPOSITORY.md) for structure and [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) for maintainer notes.

## License

This repository is licensed under the [MIT License](LICENSE).

## Metadata

Store metadata: [repository.yaml](repository.yaml)
