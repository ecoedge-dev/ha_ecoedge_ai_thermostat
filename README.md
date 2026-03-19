# EcoEdge AI Thermostat

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2023.1%2B-blue.svg)](https://www.home-assistant.io/)

**Stop heating an empty house.** EcoEdge AI Thermostat connects your Home Assistant thermostats to the EcoEdge cloud, where an AI engine learns how your home heats and cools, then automatically optimizes your setpoints using real-time weather forecasts — so you're always comfortable and never wasting energy.

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
- **Multi-thermostat** — select which thermostats to include
- **Outdoor sensor support** — use a local sensor for higher accuracy
- **Secure** — all data is sent over encrypted HTTPS to the EcoEdge API (`ha.ecoedge.io`)
- **Token refresh** — access tokens renew automatically, no re-authentication needed

---

## Requirements

- Home Assistant 2023.1 or newer
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

### Option B — Manual

1. Clone this repository:
   ```bash
   git clone https://github.com/ecoedge-dev/ha_ecoedge_ai_thermostat.git
   ```
2. Copy the `custom_components/ha_ecoedge_ai_thermostat` folder from the cloned repo into your Home Assistant config:
   ```bash
   cp -r ha_ecoedge_ai_thermostat/custom_components/ha_ecoedge_ai_thermostat /config/custom_components/
   ```
   The result should look like this:
   ```
   config/
   └── custom_components/
       └── ha_ecoedge_ai_thermostat/
           ├── __init__.py
           ├── config_flow.py
           ├── config_schema.py
           ├── const.py
           ├── manifest.json
           ├── strings.json
           └── translations/
               └── en.json
   ```
   > The folder must be named `ha_ecoedge_ai_thermostat` — this is the integration domain that Home Assistant uses to identify it. Do not rename it.
3. Restart Home Assistant

---

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **EcoEdge AI Thermostat**
3. Enter your EcoEdge account email and password
4. Select the thermostats you want to optimize
5. Optionally select an outdoor temperature sensor for better accuracy
6. Click **Submit** — the integration connects and starts learning immediately

> Your receiver endpoint is configured automatically. No manual URL entry needed.

---

## Options

After setup you can adjust settings via **Settings → Devices & Services → EcoEdge AI Thermostat → Configure**:

| Option | Description |
|---|---|
| **Select Thermostats** | Add or remove thermostats from optimization |
| **Exclude entities** | Entities to always ignore |
| **Outdoor temperature sensor** | Local sensor to improve forecast accuracy |
| **Debounce (seconds)** | How long to wait before batching state changes (default: 3s) |
| **Rotate access token** | Issue a new token — enter your password to confirm |

---

## YAML configuration (advanced)

If you prefer YAML over the UI, add this to `configuration.yaml`:

```yaml
ha_ecoedge_ai_thermostat:
  endpoint: https://ha.ecoedge.io
  email: you@example.com
  password: your_password
  home_id: my_home          # optional, defaults to HA location name
  include_entities:
    - climate.living_room
    - climate.bedroom
  outdoor_sensor: sensor.outdoor_temperature   # optional
  debounce_seconds: 3
```

---

## Privacy

EcoEdge receives thermostat state data, your home's location (latitude/longitude), and timezone. This data is used exclusively for thermal modeling and setpoint optimization. It is never sold or shared with third parties. See the full [Privacy Policy](https://ecoedge.io/privacy).

---

## Support

- **Bug reports & feature requests:** [GitHub Issues](https://github.com/ecoedge-dev/ha_ecoedge_ai_thermostat/issues)
- **General questions:** [info@ecoedge.io](mailto:info@ecoedge.io)
- **Website:** [ecoedge.io](https://ecoedge.io)

---

## License

MIT License — see [LICENSE](LICENSE) for details.
