# Sole2Garmin 🏃‍♂️💨 ➡️ ⌚

Automated sync service that bridges workouts from **Sole Fitness** (treadmills, ellipticals, bikes via the SOLE+ app's native Strava sync), captures second-by-second telemetry streams, compiles them into enhanced `.fit` files with native **Garmin Epix Gen 2** (or **Forerunner 965**) device signatures, and uploads them directly to **Garmin Connect** to unlock full Garmin **Physio TrueUp** (Training Load, Running Training Effect, Running VO2 Max, and Recovery Time).

Packaged as a lightweight, containerized Docker service suitable for running on home servers, NAS devices (Synology, Unraid, TrueNAS), Raspberry Pis, or any system with Docker.

---

## 🌟 Key Features

* **🔄 Automated Synchronization**: Set-and-forget background daemon polling at configurable intervals (e.g., every 60 minutes).
* **🌉 Seamless Strava Intermediary Bridge**: Leverages the official SOLE+ app's native auto-sync (`Settings -> Health Apps -> Strava`). When you step off your Sole treadmill, the workout syncs to Strava, and **Sole2Garmin** retrieves the full telemetry streams to build a native Garmin activity.
* **🎯 Garmin Physio TrueUp & Running Metrics**: Third-party manual uploads do not award Running Training Effect or VO2 Max. **Sole2Garmin** compiles workouts using `fit-tool` with Garmin multisport device signatures (defaulting to **Garmin Epix Gen 2** Product ID `3943` or **Forerunner 965** Product ID `4315`), unlocking full physiological training metrics in Garmin Connect.
* **⛰️ Incline & Elevation Modeling**: Converts treadmill incline percentages into realistic grade and cumulative vertical ascent streams ($\Delta \text{altitude} = \Delta \text{distance} \times \frac{\text{incline}\%}{100}$), ensuring hill workouts reflect total elevation gain.
* **🛡️ Multi-Tier Duplicate Prevention**:
  1. **Persistent SQLite Store**: Remembers synced activity IDs across reboots and container restarts.
  2. **Garmin Connect Time Window**: Checks Garmin Connect for activities matching within a $\pm 2$-hour window (preventing duplicates if you also recorded with your watch).
  3. **HTTP 409 Conflict Protection**: Gracefully catches Garmin's upload duplicate response.
* **🔑 Secure Token Caching**: Stores Garmin session tokens in `data/garmin_tokens` and Strava OAuth tokens in `data/strava_tokens` with automatic refresh.
* **📁 Offline & Drop-Folder Support**: Supports reading sample or dropped `.json` and raw `.fit` files from `data/sample_activities` for local manual uploads or offline testing.
* **🐳 Docker & Docker Compose Ready**: Single-command startup with minimal resource overhead and only a single volume mount (`./data:/data`).

---

## 🏗️ Architecture

```
┌─────────────────────────────────┐
│     Sole Machine Console        │
│   (F63, F80, F85, TT8, etc.)    │
└────────────────┬────────────────┘
                 │ Bluetooth (BLE)
                 ▼
┌─────────────────────────────────┐
│           SOLE+ App             │
│   (Settings -> Health -> Strava)│
└────────────────┬────────────────┘
                 │ Auto-Sync
                 ▼
┌─────────────────────────────────┐
│          Strava Cloud           │
│   - Activities & Streams API    │
└────────────────┬────────────────┘
                 │ Telemetry Streams
                 ▼
┌─────────────────────────────────┐
│       FIT Binary Builder        │
│   - fit-tool generator          │
│   - Incline to Elevation curve  │
│   - Epix Gen 2 / FR965 spoof    │
│   - Treadmill SubSport tagged   │
└────────────────┬────────────────┘
                 │ Enhanced .fit
                 ▼
┌─────────────────────────────────┐
│         Garmin Connect          │
│   - Cached OAuth Auth           │
│   - ±2h Duplicate Window Check  │
│   - FIT Binary Upload           │
└────────────────┬────────────────┘
                 │ Upload Confirmation
                 ▼
┌─────────────────────────────────┐
│        SQLite Sync Store        │
│      (/data/sync_state.db)      │
└─────────────────────────────────┘
```

---

## 🔑 Getting Your Strava API Credentials (1-Time Setup)

1. Log into [Strava](https://www.strava.com).
2. Go to [Strava API Settings](https://www.strava.com/settings/api).
3. Create a free API application (e.g., Application Name: `Sole2Garmin`, Category: `Personal`, Website: `http://localhost`).
4. Note your **Client ID** and **Client Secret**.
5. Generate an initial refresh token with activity read permissions (`activity:read_all`):
   - In your browser, open this authorization URL (replace `YOUR_CLIENT_ID`):
     ```
     https://www.strava.com/oauth/authorize?client_id=YOUR_CLIENT_ID&response_type=code&redirect_uri=http://localhost&approval_prompt=force&scope=read,activity:read_all
     ```
   - Click **Authorize**. Your browser will redirect to `http://localhost/?state=&code=AUTHORIZATION_CODE&scope=read,activity:read_all`.
   - Copy the `AUTHORIZATION_CODE` from the URL.
   - Exchange it for a refresh token using `curl`:
     ```bash
     curl -X POST https://www.strava.com/oauth/token \
       -F client_id=YOUR_CLIENT_ID \
       -F client_secret=YOUR_CLIENT_SECRET \
       -F code=AUTHORIZATION_CODE \
       -F grant_type=authorization_code
     ```
   - In the JSON response, copy the `refresh_token`.

---

## 🚀 Quick Start with Docker Compose (Recommended)

### 1. Configure `docker-compose.yml`

Edit `docker-compose.yml` directly under the `environment:` section:

```yaml
services:
  sole2garmin:
    build: .
    image: sole2garmin:latest
    container_name: sole2garmin
    restart: unless-stopped
    environment:
      # Ingestion Provider
      - INGESTION_PROVIDER=strava

      # Strava API Credentials
      - STRAVA_CLIENT_ID=your_client_id
      - STRAVA_CLIENT_SECRET=your_client_secret
      - STRAVA_REFRESH_TOKEN=your_refresh_token
      - STRAVA_FILTER_SOLE_ONLY=true

      # Garmin Connect Credentials
      - GARMIN_USERNAME=your_garmin_email@example.com
      - GARMIN_PASSWORD=your_garmin_password

      # Storage directories
      - DATA_DIR=/data
      - GARMIN_TOKEN_DIR=/data/garmin_tokens
      - STRAVA_TOKEN_DIR=/data/strava_tokens

      # Sync interval (minutes)
      - SYNC_INTERVAL_MINUTES=60
      - DEVICE_PRODUCT_ID=3943  # 3943 = Epix Gen 2, 4315 = Forerunner 965
    volumes:
      - ./data:/data
```

### 2. Launch the Container

```bash
docker compose up -d
```

View the logs in real time:

```bash
docker compose logs -f
```

---

## ⚙️ Configuration Reference

All settings can be configured via `docker-compose.yml`, environment variables, or a `.env` file:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `INGESTION_PROVIDER` | `strava` | Ingestion mode: `strava` (recommended), `cloud`, or `local` |
| `STRAVA_CLIENT_ID` | *Required* | Your Strava API Client ID |
| `STRAVA_CLIENT_SECRET` | *Required* | Your Strava API Client Secret |
| `STRAVA_REFRESH_TOKEN` | *Required* | Your Strava initial OAuth Refresh Token |
| `STRAVA_FILTER_SOLE_ONLY`| `true` | When true, only syncs activities with "Sole" in name or device |
| `GARMIN_USERNAME` | *Optional* | Your Garmin Connect email (required if tokens are not yet cached) |
| `GARMIN_PASSWORD` | *Optional* | Your Garmin Connect password |
| `DATA_DIR` | `/data` | Root directory for SQLite database and tokens |
| `GARMIN_TOKEN_DIR` | `/data/garmin_tokens` | Directory storing Garmin session tokens |
| `STRAVA_TOKEN_DIR` | `/data/strava_tokens` | Directory storing Strava session tokens |
| `SAMPLE_DIR` | `/data/sample_activities` | Directory for drop-folder / sample activity JSON or FIT files |
| `SYNC_INTERVAL_MINUTES`| `60` | Polling frequency in minutes when running in daemon mode |
| `SYNC_BATCH_SIZE` | `10` | Max recent activities to evaluate per cycle |
| `CHECK_DUPLICATES` | `true` | Verify if workout is already in Garmin Connect |
| `DUPLICATE_THRESHOLD_HOURS` | `2` | Matching time window around workout timestamp |
| `SPOOF_GARMIN_DEVICE`| `true` | Patch FIT file with Garmin multisport device signatures |
| `DEVICE_PRODUCT_ID` | `3943` | Garmin Device ID (`3943` = Epix Gen 2, `4315` = Forerunner 965, `3927` = Edge 1040) |
| `DEVICE_SOFTWARE_VERSION` | `18.16` | Device firmware version reported in FIT header |
| `DEFAULT_SPORT` | `RUNNING` | Default sport (`RUNNING`, `CYCLING`, `FITNESS_EQUIPMENT`) |
| `DEFAULT_SUB_SPORT` | `TREADMILL` | Default subsport (`TREADMILL`, `ELLIPTICAL`, `INDOOR_CYCLING`) |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
