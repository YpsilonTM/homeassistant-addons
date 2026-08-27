# Contributing

## Adding or changing an add-on

1. Create or edit a top-level folder whose name matches the `slug` in `config.yaml`.
2. Bump `version` in `config.yaml` and add an entry to `CHANGELOG.md`.
3. Update `DOCS.md` for user-visible behaviour changes.
4. Push to `main` — CI builds `ghcr.io/<repo>/addon-<slug>:<version>` when that add-on’s files change.
5. In Home Assistant: **Supervisor → Add-on store → ⋮ → Check for updates**, then update the add-on.

## Required files per add-on

Per [Home Assistant add-on documentation](https://developers.home-assistant.io/docs/add-ons/configuration/):

| File | Purpose |
|------|---------|
| `config.yaml` | Name, version, options schema, `image`, arch, ports, etc. |
| `Dockerfile` | Container image |
| `run.sh` | Startup script (typically exports options → env) |
| `DOCS.md` | Documentation tab in the add-on UI |

Recommended: `CHANGELOG.md`, `icon.png` (256×256 PNG), `.dockerignore`.

## Do not commit

- `.env` or real credentials
- `modqtt/config_local/modqtt.yaml` (use `modqtt.example.yml`)
- `node_modules/`, `.venv/`, `__pycache__/`
- LAN-specific IPs in tracked config (use examples + add-on options)

## CI

Workflow `.github/workflows/build-addons.yml` builds only add-ons whose paths changed (or all on manual dispatch). Images are pushed to GitHub Container Registry on `main`.
