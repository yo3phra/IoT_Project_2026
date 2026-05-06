# AI Agent Guide for IoT_Project_2026

## Quick Facts

- **Project**: Coral IoT Bicycle Security System — biometric (face recognition) + IoT sensor integration
- **Tech Stack**: Python 3.13 (Coral), Python 3.11-slim docker image(mqtt-bridge, web-app), Flask, TensorFlow, Azure (IoT Hub, CosmosDB)
- **Structure**: 3 independent Python services (Coral, mqtt-bridge, web-app), Docker containerized
- **Stage**: Early (7 commits, minimal docs). Context lives in code comments.

## Architecture

### 1. Coral (ML/Biometrics)

**Purpose**: Face detection, recognition, liveness check, enrollment pipeline

**Key Modules** (`Coral/`):
- `auth_controller.py` — Orchestrates auth flow (detector → recognizer → liveness → decision); supports cloud/pytrack/local signal sources
- `cloud_signaling_interface.py` — Bidirectional Azure IoT Hub signaling (Direct Methods for commands, D2C telemetry for progress)
- `pytrack_interface.py` — Local PyTrack integration for theft detection (orthogonal to cloud signaling)
- `face_detector.py` — Detects faces (cascade-based, fallback to YOLO)
- `face_recognizer.py` — Identifies users (ONNX embeddings model, L2 normalized)
- `liveness_detector.py` — Detects spoofing (eye blink + texture analysis)
- `config.py` — Centralized config (thresholds, model paths, Azure connection string)
- `admin_interface.py` — CLI for user enrollment/testing/deletion; initializes cloud signaling in mock mode
- `user_storage.py`, `enrollment_storage.py` — Local database (SQLite)
- `embedding_storage.py` — Cloud integration
- `mock_mode.py` — Simulates enrollment/auth when camera unavailable

**Deployment Targets**:
- Windows development (USB camera)
- Coral TPU board (with Google Coral Edge TPU)

**Known Gotchas**:
- Tests require relative path fixes (pytest fixtures need adjustment)
- Mock mode is active in development; disable when camera/Coral connected
- Embeddings must be L2-normalized (recent refactor ensures this)
- Liveness thresholds tuned for development; may need adjustment in production
- Cloud signaling requires `AZURE_IOT_CONNECTION_STRING` env var; gracefully degrades if missing
- Idle mode uses source tracking: "cloud" (Azure), "pytrack" (local), "local" (CLI). Multiple sources orthogonal; no conflicts.
- Azure connection is lazy-initialized (only on first send); Direct Methods block until Azure ACK, telemetry is fire-and-forget

### 2. mqtt-bridge

**Purpose**: Aggregates IoT sensor data (TheThingsNetwork LoRaWAN MQTT) → Azure IoT Hub → CosmosDB

**Key Files** (`mqtt-bridge/`):
- `bridge.py` — Main loop: TTN MQTT subscribe → decode → Azure IoT Hub publish → CosmosDB persist
- `config.py` — Credentials, endpoints, topics
- `docker-compose.yml` — Container setup

**Integration Points**:
- Subscribes to TTN MQTT (topic pattern: `v3/{ttn_id}/as/up/...`)
- Publishes to Azure IoT Hub
- Writes sensor readings + metadata to CosmosDB collection `bicycle_telemetry`

**Known Gotchas**:
- Requires `.env` with TTN + Azure credentials
- No device connected; data are simulated for dev
- Fallback: queries simulated data if CosmosDB unavailable

### 3. web-app (Dashboard)

**Purpose**: Flask-based UI for real-time sensor monitoring and bicycle status

**Key Files** (`web-app/`):
- `app.py` — Flask app; 3 routes (index, navigation, parked)
- `static/` — CSS/JS for frontend
- `templates/` — Jinja2 HTML templates
- Queries CosmosDB for accelerometer history; displays live updates

**Known Gotchas**:
- Falls back to simulated accelerometer data if disconnected
- No error pages (500s uncaught); debug mode on by default

## Setup & Build

### Prerequisites

```bash
# Python 3.11+; 3.13 for Coral development
python --version

# Azure/TTN credentials in .env file (see Environment section below)
```

### Install Dependencies

```bash
# Each service has its own requirements.txt
cd Coral && pip install -r requirements.txt
cd ../mqtt-bridge && pip install -r requirements.txt
cd ../web-app && pip install -r requirements.txt
```

### Build & Run (Development)

**Coral**:
```bash
cd Coral
python admin_interface.py  # CLI menu for enroll/test/delete
```

**mqtt-bridge** (Docker):
```bash
cd mqtt-bridge
docker-compose up -d
# or without Docker:
python bridge.py
```

**web-app** (Flask):
```bash
cd web-app
python app.py  # Runs on http://localhost:5000
```

### Docker Deployment

Each service except Coral now has a `Dockerfile` (Python 3.11-slim base):

```bash
# Build individual images
docker build -t mqtt-bridge:latest ./mqtt-bridge
docker build -t web-app:latest ./web-app

# Or use docker-compose for mqtt-bridge/web-app
cd mqtt-bridge && docker-compose up -d
cd web-app && docker-compose up -d
```

Coral will be containerized later. Large model files should be mounted as shared volume. Saved data should also be in a shared volume to persist when containers are restarted. Camera access may require additional permissions.


### Testing

```bash
# Coral tests (pytest)
cd Coral && pytest tests/  # Note: May need fixture path adjustments
```

## Environment Variables

We have `.env` file in each service directory containing sensitive credentials and configuration. Example:

```bash
# TTN (TheThingsNetwork) credentials
TTN_USERNAME=<app-id>
TTN_PASSWORD=<api-key>

# Azure IoT Hub (for cloud signaling)
AZURE_IOT_CONNECTION_STRING=HostName=<hub>.azure-devices.net;DeviceId=<id>;SharedAccessKey=<key>

# Azure CosmosDB
COSMOS_CONNECTION_STRING=...

# Optional (can be implemented)
MQTT_BROKER_URL=mqtt.uplink.lora.cloud  # Default TTN broker
CLOUD_API_URL=<your-api>
CLOUD_PROTOCOL=mqtt  # mqtt, rest, websocket
LOG_LEVEL=INFO
MODEL_DIR=/path/to/models  # For Coral TPU deployment
```

## File Structure & Key Files

```
IoT_Project_2026/
├── Coral/                           # Biometric auth system
│   ├── auth_controller.py           # Main auth flow orchestration (multi-source: cloud, pytrack, local)
│   ├── cloud_signaling_interface.py # Azure IoT Hub bidirectional signaling
│   ├── pytrack_interface.py         # Local PyTrack theft detection
│   ├── config.py                    # Centralized config + Azure connection string
│   ├── admin_interface.py           # CLI for enrollment/testing (cloud signaling enabled)
│   ├── face_detector.py
│   ├── face_recognizer.py           # ONNX embeddings model
│   ├── liveness_detector.py         # Eye blink + texture spoofing detection
│   ├── user_storage.py              # SQLite user database
│   ├── embedding_storage.py
│   ├── mock_mode.py                 # Simulated auth for dev
│   ├── CLOUD_SIGNALING_GUIDE.md     # Cloud signaling integration docs
│   ├── requirements.txt
│   └── tests/
│
├── mqtt-bridge/                     # IoT data aggregation
│   ├── bridge.py                    # TTN MQTT → Azure IoT Hub → CosmosDB
│   ├── config.py
│   ├── requirements.txt
│   ├── docker-compose.yml
│   └── Dockerfile
│
├── web-app/                    # Dashboard frontend
│   ├── app.py                  # Flask app (3 routes)
│   ├── static/
│   ├── templates/
│   ├── requirements.txt
│   ├── docker-compose.yml
│   └── Dockerfile
│
└── README.md
```

## Development Conventions

1. **Configuration**: Use `config.py` in each service; avoid hardcoded values
2. **Logging**: Import `logging` module; use `logger.info/debug/error` (not print)
3. **Error handling**: Wrap external API calls (Azure, TTN) in try-except; log and fallback gracefully
4. **Imports**: Relative imports within service modules (e.g., `from .config import Config`)
5. **Database**: Use local SQLite (Coral, for user biometrics storage) or cloud CosmosDB (mqtt-bridge/web-app for telemetry)



## How to Approach Tasks

**Adding a feature**: Start in `Coral/config.py` or `mqtt-bridge/config.py` to understand current thresholds/settings.

**Debugging authentication**: Trace flow through `Coral/auth_controller.py` → detector → recognizer → liveness. Check `session.source` to see signal origin (cloud/pytrack/local).

**Cloud signaling issues**: Check `Coral/cloud_signaling_interface.py` for Azure connection state. Verify `AZURE_IOT_CONNECTION_STRING` env var set. Review `CLOUD_SIGNALING_GUIDE.md` for integration patterns.

**Multi-source auth**: Auth controller supports concurrent signals (cloud, pytrack, local). Each creates independent session with `source` field. No conflicts; signals processed orthogonally.

**Adding sensors**: Extend `mqtt-bridge/bridge.py` to parse new TTN uplink fields; persist to CosmosDB collection.

**UI changes**: Modify Flask route handlers in `web-app/app.py` and templates in `web-app/templates/`.

**Azure patterns**: Reference `mqtt-bridge/bridge.py` for IoTHubDeviceClient patterns. Reuse in other services. Cloud signaling uses lazy-init, retry logic, and graceful degradation.

## Links

- [Azure IoT Hub](https://learn.microsoft.com/en-us/azure/iot-hub/)
- [TheThingsNetwork](https://www.thethingsnetwork.org/)
- [TensorFlow ONNX Runtime](https://onnx.ai/)
- [Flask Quickstart](https://flask.palletsprojects.com/)

---

*Updated 05-05-2026. Includes Azure cloud signaling (Direct Methods + D2C telemetry), multi-source auth (cloud/pytrack/local), idle mode with preloaded models. Update this file as project evolves.*

