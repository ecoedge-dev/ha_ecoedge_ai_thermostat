# EcoEdge AI Thermostat

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.12%2B-blue.svg)](https://www.home-assistant.io/)

**Stop heating more than you need.** EcoEdge AI Thermostat connects your Home Assistant thermostats to the EcoEdge cloud, where a physics engine learns how your home heats and cools and recommends energy-saving setpoints using real weather forecasts — visible on your dashboard and as native sensor entities in Home Assistant.

---

## How it works

1. Your thermostats report state changes to the EcoEdge cloud
2. EcoEdge fits a thermal model to your home (how fast it loses heat, how the wind affects it)
3. The AI looks ahead at the next 24 hours of weather forecasts
4. Optimal setpoints are computed and stored — ready to view at [dashboard.ecoedge.io](https://dashboard.ecoedge.io/)

> **Note:** Automatic setpoint push-back to Home Assistant is on the roadmap. The current version focuses on data collection, thermal learning, and AI-driven recommendations.

---

## Features

- **AI-powered setpoint recommendations** — 24-hour predictive horizon updated continuously
- **Thermal learning** — adapts to your home's insulation, size, and heating system
- **Weather-aware** — uses real forecasts (temperature, wind, precipitation)
- **Native HA sensors** — AI setpoint, model, heat loss, confidence and saving estimate per thermostat, usable in your own automations
- **Multi-thermostat** — select which thermostats to include
- **Outdoor sensor support** — required for thermal modeling; use a local sensor for highest accuracy
- **Secure** — all data is sent over encrypted HTTPS to the EcoEdge API (`ha.ecoedge.io`)
- **Token refresh** — access tokens renew automatically, no re-authentication needed
- **Reliable delivery:** readings taken while EcoEdge or your internet is unavailable are kept for up to 24 hours (5,000 readings) and sent when the connection returns, even across Home Assistant restarts.

---

## Requirements

- Home Assistant 2024.12 or newer
- An EcoEdge account — [Sign up](https://dashboard.ecoedge.io/) or [Sign in](https://dashboard.ecoedge.io/)
- At least one `climate` entity in Home Assistant

---

## Installation

### Option A — HACS (recommended)

1. Open **HACS** in your Home Assistant sidebar
2. Go to **Integrations** → click the three-dot menu → **Custom repositories**
3. Add the repository URL:
   ```
   https://github.com/ecoedge-dev/ha_ecoedge_ai_thermostat
   ```
   and set the category to **Integration**
4. Find **EcoEdge AI Thermostat** in the HACS integration list and click **Download**
5. Restart Home Assistant

### Option B — Manual (VS Code terminal on Home Assistant)

Open the VS Code terminal in Home Assistant and run:

```bash
cd /config
git clone https://github.com/ecoedge-dev/ha_ecoedge_ai_thermostat.git
mkdir -p custom_components
cp -r ha_ecoedge_ai_thermostat/custom_components/ha_ecoedge_ai_thermostat custom_components/
rm -rf ha_ecoedge_ai_thermostat
```

The result will look like this:

```
/config/
└── custom_components/
    └── ha_ecoedge_ai_thermostat/
        ├── __init__.py
        ├── config_flow.py
        ├── config_schema.py
        ├── const.py
        ├── coordinator.py
        ├── manifest.json
        ├── sensor.py
        ├── strings.json
        ├── util.py
        └── translations/
            └── en.json
```

Restart Home Assistant when done.

---

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **EcoEdge AI Thermostat**
3. Enter your EcoEdge account email and password
4. Select the thermostats you want to optimize
5. Select an outdoor temperature sensor — **required** for thermal modeling
6. Click **Submit** — the integration connects and starts learning immediately

> Your receiver endpoint is configured automatically. No manual URL entry needed.

---

## Options

After setup you can adjust settings via **Settings → Devices & Services → EcoEdge AI Thermostat → Configure**:

| Option | Description |
|---|---|
| **Select Thermostats** | Add or remove thermostats from optimization |
| **Exclude entities** | Entities to always ignore |
| **Outdoor temperature sensor** | Required for thermal modeling — use a local sensor for highest accuracy |
| **Batching window (seconds)** | State changes are collected for this long and sent together (default: 20 s). Every reading is kept, and a batch is sent at the latest 60 s after its first change. |
| **Rotate access token** | Issue a new token — enter your password to confirm |

---

## Troubleshooting

**Diagnostics.** Go to **Settings → Devices & Services → EcoEdge AI Thermostat → ⋮ → Download diagnostics**. The file shows:
- the upload queue (waiting readings, dropped readings, last result, last delivery);
- the last profile update;
- the API address and versions;
- which thermostats are present or missing.

Tokens, your e-mail and the device ID are removed from it. Attach it to a bug report.

**System information.** Go to **Settings → System → Repairs → ⋮ → System information → EcoEdge AI Thermostat**. It shows whether the EcoEdge API is reachable, the integration version, the readings waiting to be sent, and the last delivery and profile update.

**Repair issues** appear under **Settings → System → Repairs**. All except the YAML notice clear themselves once the cause is gone.

| Issue | Meaning |
|---|---|
| EcoEdge has been unreachable | Nothing could be delivered or loaded for more than a day. Readings are kept for up to 24 hours (5,000 readings). Check the internet connection. |
| EcoEdge readings will be dropped soon | The upload queue is close to its limit, or its oldest reading is nearly 24 hours old. |
| Some EcoEdge readings were dropped | Readings were lost, because EcoEdge was unreachable for too long or rejected them. The counts are in the diagnostics. |
| EcoEdge AI Thermostat needs an update | EcoEdge needs a newer version of the integration. Update it in HACS. |
| EcoEdge YAML configuration is deprecated | Remove the `ha_ecoedge_ai_thermostat:` block from `configuration.yaml`. |

**Thermostats that are gone.**
- When EcoEdge stops reporting a thermostat, its sensors show *unavailable*. EcoEdge reports a thermostat while it has a reading from the last 7 days.
- A device is removed automatically only when its thermostat has been **deleted or renamed in Home Assistant** and EcoEdge hasn't reported it for 7 days. A thermostat that still exists, for example one switched off all summer, is never removed; its sensors come back with its next reading.
- A thermostat you deselect or exclude in the options is removed at the next start.
- You can delete a device yourself on its device page while EcoEdge doesn't report its thermostat. An active thermostat can't be deleted, because it would come straight back.
- Only successful updates count, so an EcoEdge outage never removes devices.

**Diagnostic sensors.** *Model*, *k* and *confidence* are diagnostic entities. They are listed on the device page, but not on auto-generated dashboards.

---

## Removing the integration

1. Go to **Settings → Devices & Services → EcoEdge AI Thermostat → ⋮ → Delete**. This also removes the devices, sensors and any readings still waiting to be sent from this Home Assistant.
2. If you set it up through YAML, remove the `ha_ecoedge_ai_thermostat:` block from `configuration.yaml`.
3. To remove the files as well, uninstall it in HACS (or delete `custom_components/ha_ecoedge_ai_thermostat`), then restart Home Assistant.

This does not delete your data at EcoEdge. In the EcoEdge dashboard, delete the home under *Home settings → Delete this home*, or your whole account under *My profile → Delete account*.

---

## YAML configuration (deprecated)

YAML configuration is deprecated since v0.7.0 and will be removed in a future
release: it stores your password in plain text. Set the integration up under
**Settings → Devices & services** instead. If `configuration.yaml` still has an
`ha_ecoedge_ai_thermostat:` block, Home Assistant shows a Repair issue. An
existing UI setup is never re-imported from YAML, so you can simply delete the
block and restart.

---

## Privacy

EcoEdge receives the state of the selected thermostats and the outdoor sensor, limited to the attributes the thermal model uses (temperatures, set-points, HVAC action and modes, limits, units and the display name). Your home's location (latitude/longitude) and timezone are sent only when you sign in, so the weather forecast matches your home. This data is used exclusively for thermal modeling and setpoint optimization. It is never sold or shared with third parties. See the full [Privacy Policy](https://ecoedge.io/privacy).

---

## Development

Tests run in Docker, with pinned Python 3.14 and Home Assistant from `requirements_test.txt`. CI runs the same tests on every push and pull request.

```bash
make test                          # full suite
make test ARGS="-k debounce -v"    # a subset, verbose
make lint                          # ruff on custom_components/ and tests/
```

Known issues can be recorded as `xfail` tests (`pytest.ini` sets `xfail_strict`). A fix that makes one pass must also remove its marker. None are open at the moment; see `CHANGELOG.md`. `custom_components/ha_ecoedge_ai_thermostat/quality_scale.yaml` tracks the Home Assistant quality-scale checklist.

---

## Support

- **Bug reports & feature requests:** [GitHub Issues](https://github.com/ecoedge-dev/ha_ecoedge_ai_thermostat/issues)
- **General questions:** [info@ecoedge.io](mailto:info@ecoedge.io)
- **Website:** [ecoedge.io](https://ecoedge.io)

---

## License

MIT License — see [LICENSE](LICENSE) for details.
