# Sole2Garmin 🏃‍♂️💨 ➡️ ⌚

Automated sync service that downloads and converts your workout sessions from **Sole Fitness** (Dyaco / SOLE+ platform), compiles or enhances them into `.fit` files with native Garmin device signatures (**Garmin Epix Gen 2** or **Forerunner 965**) to unlock Garmin **Physio TrueUp** (Training Load, Training Effect, Running VO2 Max, Recovery Time), and uploads them directly to **Garmin Connect**.

Packaged as a lightweight, containerized Docker service suitable for running on home servers, NAS devices (Synology, Unraid, TrueNAS), Raspberry Pis, or any system with Docker.

---

## 🌟 Key Features

* **🔄 Automated Synchronization**: Runs as a set-and-forget background daemon polling the SOLE+ platform at configurable intervals (e.g., every 60 minutes).
* **🎯 Garmin Physio TrueUp & Running Metrics**: Standard manual indoor treadmill uploads do not calculate Running Training Effect or VO2 Max. **Sole2Garmin** compiles workouts using `fit-tool` with Garmin device signatures (defaulting to **Garmin Epix Gen 2** Product ID `3943` or **Forerunner 965** Product ID `4315`), unlocking full physiological training metrics in Garmin Connect.
* **⛰️ Incline & Elevation Modeling**: Converts treadmill incline percentages into realistic grade and cumulative vertical ascent streams ($\Delta \text{altitude} = \Delta \text{distance} \times \frac{\text{incline}\%}{100}$), ensuring hill workouts reflect total elevation gain.
* **🛡️ Multi-Tier Duplicate Prevention**:
  1. **Persistent SQLite Store**: Remembers synced activity IDs across reboots and container restarts.
  2. **Garmin Connect Time Window**: Checks Garmin Connect for activities matching within a $\pm 2$-hour window (preventing duplicates if you also recorded with your watch).
  3. **HTTP 409 Conflict Protection**: Gracefully catches Garmin's upload duplicate response.
* **🔑 Secure Token Caching**: Stores Garmin session tokens inside `data/garmin_tokens` and SOLE+ tokens in `data/sole_tokens`, avoiding repetitive 2FA prompts and preventing lockouts.
* **📁 Offline & Drop-Folder Support**: Supports reading sample or dropped `.json` and raw `.fit` files from `data/sample_activities` for local manual uploads or offline testing.
* **🐳 Docker & Docker Compose Ready**: Single-command startup with minimal resource overhead and only a single volume mount (`./data:/data`).
* **🛠️ Flexible CLI Controls**: Supports single-workout sync (`--sync-latest`), batch backfills (`--batch 10`), database inspection (`--status`), and simulations (`--dry-run`).

---

## 🏗️ Architecture

```
┌─────────────────────────────────┐
│     SOLE+ / Dyaco Cloud API     │
│   - JWT Bearer Authentication   │
│   - Workout Summary / Streams   │
│   (or local drop JSON / FIT)    │
└────────────────┬────────────────┘
                 │ Workout Records
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

Edit `docker-compose.yml` directly under the `environment:` section:

```yaml
services:
  sole2garmin:
    build: .
    image: sole2garmin:latest
    container_name: sole2garmin
    restart: unless-stopped
    environment:
      # SOLE+ / Dyaco Credentials
      - SOLE_EMAIL=your_sole_email@example.com
      - SOLE_PASSWORD=your_sole_password

      # Garmin Connect Credentials
      - GARMIN_USERNAME=your_garmin_email@example.com
      - GARMIN_PASSWORD=your_garmin_password

      # Storage directories
      - DATA_DIR=/data
      - GARMIN_TOKEN_DIR=/data/garmin_tokens
      - SOLE_TOKEN_DIR=/data/sole_tokens

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

## 🐳 Running with Docker CLI

### Build the Image:

```bash
docker build -t sole2garmin:latest .
```

### Run as a Background Daemon (60m interval):

```bash
docker run -d \
  --name sole2garmin \
  --restart unless-stopped \
  -e SOLE_EMAIL="your_email@example.com" \
  -e SOLE_PASSWORD="your_password" \
  -e GARMIN_USERNAME="your_garmin@example.com" \
  -e GARMIN_PASSWORD="your_garmin_password" \
  -v $(pwd)/data:/data \
  sole2garmin:latest
```

### Run a One-Off Sync (Latest Workout):

```bash
docker run --rm \
  -e SOLE_EMAIL="your_email@example.com" \
  -e SOLE_PASSWORD="your_password" \
  -v $(pwd)/data:/data \
  sole2garmin:latest --sync-latest
```

### View Synchronization Status:

```bash
docker run --rm \
  -v $(pwd)/data:/data \
  sole2garmin:latest --status
```

---

## ⚙️ Configuration Reference

All settings can be configured via `docker-compose.yml`, environment variables, or a `.env` file:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `SOLE_EMAIL` | *Required* | Your SOLE+ / Dyaco account email |
| `SOLE_PASSWORD` | *Required* | Your SOLE+ / Dyaco account password |
| `SOLE_BASE_URL` | `https://api.dyaco.com/v1` | Base URL for Dyaco / SOLE+ cloud services |
| `GARMIN_USERNAME` | *Optional* | Your Garmin Connect email (required if tokens are not yet cached) |
| `GARMIN_PASSWORD` | *Optional* | Your Garmin Connect password |
| `DATA_DIR` | `/data` | Root directory for SQLite database and tokens |
| `GARMIN_TOKEN_DIR` | `/data/garmin_tokens` | Directory storing Garmin session tokens |
| `SOLE_TOKEN_DIR` | `/data/sole_tokens` | Directory storing SOLE+ session tokens |
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

---

## 💻 Local Installation (Without Docker)

### 1. Requirements

* Python 3.10+
* Virtual environment (`venv`)

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Run Commands

```bash
# Check installed version
python -m src.main --version

# Run dry-run simulation
python -m src.main --dry-run

# Sync only the single latest activity
python -m src.main --sync-latest

# Batch sync the last 10 activities
python -m src.main --batch 10

# Inspect database status
python -m src.main --status
```
