# Sole2Garmin 🏃‍♂️💨 ➡️ ⌚

Automated sync service that bridges workouts from **Sole Fitness equipment** (treadmills, ellipticals, bikes via the SOLE+ / Dyaco cloud API or drop folder), captures second-by-second telemetry, compiles enhanced `.fit` files with native **Garmin Epix Gen 2** (or **Forerunner 965**) device signatures, and uploads them directly to **Garmin Connect** to unlock full Garmin **Physio TrueUp** (Training Load, Running Training Effect, Running VO2 Max, and Recovery Time).

Packaged as a lightweight, containerized Docker service suitable for running on home servers, NAS devices (Synology, Unraid, TrueNAS), Raspberry Pis, or any system with Docker.

---

## 🌟 Key Features

* **🔄 Automated Synchronization**: Set-and-forget background daemon polling at configurable intervals (e.g., every 60 minutes).
* **☁️ Direct SOLE+ / Dyaco Cloud API**: Connects directly to your SOLE+ user account using your login credentials (`SOLE_EMAIL` & `SOLE_PASSWORD`), discovering recent workouts and telemetry.
* **🎯 Garmin Physio TrueUp & Running Metrics**: Third-party manual uploads do not award Running Training Effect or VO2 Max. **Sole2Garmin** compiles workouts using `fit-tool` with Garmin multisport device signatures (defaulting to **Garmin Epix Gen 2** Product ID `3943` or **Forerunner 965** Product ID `4315`), unlocking full physiological training metrics in Garmin Connect.
* **⛰️ Incline & Elevation Modeling**: Converts treadmill incline percentages into realistic grade and cumulative vertical ascent streams ($\Delta \text{altitude} = \Delta \text{distance} \times \frac{\text{incline}\%}{100}$), ensuring hill workouts reflect total elevation gain.
* **🛡️ Multi-Tier Duplicate Prevention**:
  1. **Persistent SQLite Store**: Remembers synced activity IDs across reboots and container restarts.
  2. **Garmin Connect Time Window**: Checks Garmin Connect for activities matching within a $\pm 2$-hour window (preventing duplicates if you also recorded with your watch).
  3. **HTTP 409 Conflict Protection**: Gracefully catches Garmin's upload duplicate response.
* **🔑 Secure Token Caching**: Stores Garmin session tokens in `data/garmin_tokens` and SOLE session tokens in `data/sole_tokens` with automatic refresh.
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
│       (Auto-Sync to Cloud)      │
└────────────────┬────────────────┘
                 │ Cloud Sync
                 ▼
┌─────────────────────────────────┐
│     Dyaco / SOLE+ Cloud API     │
│   - User Workouts & Records     │
└────────────────┬────────────────┘
                 │ Workout Telemetry
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

## 🚀 Quick Start with Docker Compose (Recommended)

### 1. Configure `docker-compose.yml`

Edit `docker-compose.yml` directly under the `environment:` section with your Sole and Garmin credentials:

```yaml
services:
  sole2garmin:
    build: .
    image: sole2garmin:latest
    container_name: sole2garmin
    restart: unless-stopped
    environment:
      # Ingestion Provider: cloud (Sole Fitness / Dyaco API)
      - INGESTION_PROVIDER=cloud

      # Sole / Dyaco Credentials
      - SOLE_EMAIL=your_sole_email@example.com
      - SOLE_PASSWORD=your_sole_password
      - SOLE_BASE_URL=https://api.dyaco.com/v1

      # Garmin Connect Credentials
      - GARMIN_USERNAME=your_garmin_email@example.com
      - GARMIN_PASSWORD=your_garmin_password

      # Storage directories
      - DATA_DIR=/data
      - GARMIN_TOKEN_DIR=/data/garmin_tokens
      - SOLE_TOKEN_DIR=/data/sole_tokens
      - SAMPLE_DIR=/data/sample_activities

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

### 3. Inspecting Logs

```bash
docker compose logs -f
```

---

## 💡 Dyaco / SOLE+ Endpoint Configuration

By default, `Sole2Garmin` targets `https://api.dyaco.com/v1`. If your mobile app connects to a region-specific or AWS gateway domain, you can specify the exact URL using `SOLE_BASE_URL`:

1. On Android, run a capture tool like **PCAPdroid** (free, no root required) or **HTTP Toolkit**.
2. Open the **SOLE+** app and pull down to refresh workout history.
3. Note the API domain in the request list and set `SOLE_BASE_URL=https://<domain>/v1` in your `docker-compose.yml` or `.env` file.
4. **Local Drop Folder Fallback**: If cloud connectivity is temporarily unavailable, dropping `.fit` or `.json` files into `./data/sample_activities/` will immediately be processed, device-spoofed, and uploaded to Garmin.

---

## ⚙️ Configuration Reference

All settings can be configured via `docker-compose.yml`, environment variables, or a `.env` file:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `INGESTION_PROVIDER` | `cloud` | Ingestion mode: `cloud` (Sole / Dyaco API), `strava`, or `local` |
| `SOLE_EMAIL` | *Required* | Your SOLE+ mobile app account email |
| `SOLE_PASSWORD` | *Required* | Your SOLE+ account password |
| `SOLE_BASE_URL` | `https://api.dyaco.com/v1` | Dyaco backend API base endpoint |
| `GARMIN_USERNAME` | *Optional* | Your Garmin Connect email (required if tokens are not yet cached) |
| `GARMIN_PASSWORD` | *Optional* | Your Garmin Connect password |
| `DATA_DIR` | `/data` | Root directory for SQLite database and tokens |
| `GARMIN_TOKEN_DIR` | `/data/garmin_tokens` | Directory storing Garmin session tokens |
| `SOLE_TOKEN_DIR` | `/data/sole_tokens` | Directory storing SOLE session tokens |
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
