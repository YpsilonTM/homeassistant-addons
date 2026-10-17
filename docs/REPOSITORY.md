# Repository layout

This is a [Home Assistant add-on repository](https://developers.home-assistant.io/docs/add-ons/): one git repo, one `repository.yaml`, multiple installable add-ons.

```
hassaddons/
├── repository.yaml          # Store metadata (required at repo root)
├── README.md                # Store landing page
├── docs/                    # Maintainer docs (not shown in HA UI)
├── .github/workflows/       # CI: build and push images to GHCR
└── <addon-slug>/            # One folder per add-on
    ├── config.yaml          # Add-on manifest (required)
    ├── Dockerfile           # Container build (required)
    ├── run.sh               # Entrypoint; reads /data/options.json (required)
    ├── DOCS.md              # User documentation (shown in HA)
    ├── CHANGELOG.md         # Version history
    ├── icon.png             # 256×256 store icon (recommended)
    ├── .dockerignore        # Exclude dev files from image build
    └── src/                 # Application source
```

## Add-ons in this store

| Slug | Image | Description |
|------|-------|-------------|
| `modqtt` | `ghcr.io/ypsilontm/hassaddons/addon-modqtt` | Modbus TCP → MQTT bridge |
| `ocppsigen2mqtt` | `ghcr.io/ypsilontm/hassaddons/addon-ocppsigen2mqtt` | OCPP 1.6 → MQTT for Sigen EV |
| `sigenevcloud2mqtt` | `ghcr.io/ypsilontm/hassaddons/addon-sigenevcloud2mqtt` | Sigenergy cloud → MQTT for Sigen EV |
| `sigenevlocal2mqtt` | `ghcr.io/ypsilontm/hassaddons/addon-sigenevlocal2mqtt` | Sigenergy local NearTerminal → MQTT for Sigen EV |

## How Home Assistant uses this repo

1. User adds `https://github.com/YpsilonTM/hassaddons` under **Settings → Add-ons → Add-on store**.
2. Supervisor reads `repository.yaml` and scans top-level folders for `config.yaml`.
3. Each add-on’s `image:` field points at a pre-built image on GHCR (built by CI).
4. At runtime, `run.sh` maps add-on options to environment variables and starts the app.

## Local development

- **Python add-ons** (`modqtt`, `sigenevcloud2mqtt`, `sigenevlocal2mqtt`): venv + `.env` (see each add-on’s `.env.example`).
- **Bun add-on** (`ocppsigen2mqtt`): `bun install` in the add-on folder + `.env.example`.
- **HA Supervisor devcontainer**: `.devcontainer/` mounts this repo into a local Supervisor instance.

See [CONTRIBUTING.md](CONTRIBUTING.md) for release and CI notes.
