# Azure Cloud Signaling Interface for Coral - Implementation Summary

## Overview

Implemented an Azure IoT Hub signaling interface for Coral biometric authentication system. Cloud signaling is ONE of THREE authentication triggers:

1. **Physical Button Press** - User presses button at bike
2. **PyTrack Movement Trigger** - Local theft detection asks for auth (when in range)
3. **Cloud Auth Command** - Remote signal when PyTrack out of range or user prefers biometric

For app-authenticated users: cloud sets unlock status directly (no Coral involved). Models preloaded in idle mode for fast UX.

**Status:** ✓ Complete - Ready for Azure integration testing

## What Was Built

### 1. **CloudSignalingInterface** (`Coral/cloud_signaling_interface.py`)
New module for bidirectional Azure IoT Hub communication:

#### Features:
- **Lazy initialization:** Connects to Azure only on first send (reduces startup overhead)
- **Dual messaging patterns:**
  - **Direct Methods** (synchronous): Commands (`start_auth`, `stop_auth`) and critical state notifications (`auth_started`, `auth_result`) with guaranteed delivery confirmation
  - **D2C Telemetry** (asynchronous): Progress updates (`auth_progress`) for lightweight streaming
- **Retry logic:** Exponential backoff with configurable attempts (default 5)
- **Mock mode:** Full testing without Azure credentials
- **State-change based telemetry:** Only sends on state transitions to reduce cloud traffic

#### Key Methods:
```python
# Commands from cloud → Coral
handle_start_auth()       # Direct Method: start authentication
handle_stop_auth()        # Direct Method: stop authentication

# Notifications Coral → Cloud (with Azure confirmation)
send_auth_started()       # Notify cloud auth session started
send_auth_result()        # Notify final result

# Telemetry Coral → Cloud (fire-and-forget)
send_auth_progress()      # Stream progress (timestamp, confidence yes/no, liveness status)
```

### 2. **Extended AuthenticationController** (`Coral/auth_controller.py`)

#### Changes:
- **Added `source` field** to `AuthenticationSession`: Tracks signal origin ("cloud" | "pytrack" | "local")
- **Cloud signaling dependency injection:** Optional `cloud_signaling` parameter in `__init__`
- **Signal hooks:**
  - `start_authentication(source="cloud")` → notifies cloud when session starts
  - `_send_auth_progress()` → streams progress updates only for cloud-initiated sessions
  - `_finalize_session()` → sends final result to cloud

#### Idle Mode:
- No changes needed; already works as absence-of-session
- Models preloaded at FaceRecognizer init (existing behavior)
- Idle when `current_session is None`

### 3. **Configuration Updates** (`Coral/config.py`)

- Added `azure_connection_string` property
- Loads from `AZURE_IOT_CONNECTION_STRING` environment variable
- Existing `CloudInterfaceConfig` used for retry settings

### 4. **Admin Interface Integration** (`Coral/admin_interface.py`)

- Imports `CloudSignalingInterface`
- Initializes cloud interface in mock mode
- Passes to auth controller for bidirectional communication

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    Azure IoT Hub (Cloud)                     │
│  ┌──────────────────┐          ┌──────────────────┐         │
│  │ Direct Methods   │          │ D2C Telemetry    │         │
│  │ ├─ start_auth    │          │ ├─ auth_progress │         │
│  │ ├─ stop_auth     │          │ └─ (state-change)│         │
│  │ ├─ auth_started  │          └──────────────────┘         │
│  │ └─ auth_result   │                                        │
│  └──────────────────┘                                        │
└────────────┬──────────────────────────────────────┬──────────┘
             │ (Direct Method responses: sync)      │ (Telemetry: async)
             │                                      │
    ┌────────▼────────────────────────┐           │
    │ CloudSignalingInterface          │◄──────────┘
    │ ├─ lazy connect                  │
    │ ├─ retry with backoff            │
    │ ├─ mock mode support             │
    │ └─ state-based telemetry         │
    └────────┬─────────────────────────┘
             │
    ┌────────▼────────────────────────┐
    │ AuthenticationController          │
    │ ├─ source: "cloud"|"pytrack"     │
    │ ├─ signal hooks                  │
    │ ├─ progress tracking             │
    │ └─ idle mode (no session)        │
    └────────┬──────────┬──────────────┘
             │          │
    ┌────────▼────┐     └─────► PyTrack Interface (existing)
    │ Camera      │              (local fallback)
    │ Detector    │
    │ Recognizer  │
    │ Liveness    │
    └─────────────┘
```

## Signal Flow Examples

### Example 1: App-Authenticated User (NO Coral)
```
1. User launches app → logs in (phone trusted device)
2. App requests unlock
3. Cloud backend checks authentication status
4. Cloud backend sets unlock status in database
5. Bike receives unlock command directly from cloud (NOT via Coral)
6. Coral remains in idle mode (no session created)
7. No biometric authentication needed
```

### Example 2: Physical Button Press (Local)
```
1. User at bike presses authentication button on Coral
2. Coral: auth_controller.start_authentication(source="local")
3. User faces camera
4. Coral: face detection → embedding → comparison
5. Coral: liveness challenges (head turn, blink)
6. Coral: auth success/failure
7. Result: unlock or deny
```

### Example 3: PyTrack Movement Trigger (Local Connection)
```
1. PyTrack detects suspicious movement (accelerometer spike)
2. PyTrack checks: is user recently authenticated?
3. If NO: PyTrack asks Coral to verify
4. Coral: auth_controller.start_authentication(source="pytrack")
5. User faces camera at bike (or nearby with phone)
6. Coral: face detection → liveness → auth result
7. If authenticated: theft prevented, tracking disabled
8. If NOT authenticated: theft alert, tracking activated
```

### Example 4: Cloud Auth (PyTrack Out of Range)
```
1. PyTrack loses connection to user's phone (moved out of range)
2. OR user explicitly requests biometric challenge via app
3. Cloud sends start_auth() Direct Method to Coral
4. Coral: auth_controller.start_authentication(source="cloud")
5. Coral → IoT Hub: send_auth_started() [Direct Method response]
6. User faces camera
7. Coral → IoT Hub: send_auth_progress() [Telemetry, state-change only]
8. Coral: liveness checks
9. Coral → IoT Hub: send_auth_progress() [if state changed]
10. Coral: auth success/failure
11. Coral → IoT Hub: send_auth_result() [Direct Method response]
12. Cloud app shows result to user
```

### Example 5: Idle Mode
```
1. Coral boots → models preloaded (eager load)
2. No active session → idle state
3. Camera off (handled by camera_interface config)
4. Waiting for signals from: button press, PyTrack, or cloud
5. Signal received → session created (source: "local"|"pytrack"|"cloud")
6. Auth flow executes...
```

## Configuration

### Environment Variables
```bash
# Azure IoT Hub connection
AZURE_IOT_CONNECTION_STRING="HostName=...;SharedAccessKey=..."

# Optional cloud broker (for future REST/MQTT protocols)
MQTT_BROKER_URL="your-mqtt-broker"
CLOUD_API_URL="your-api-url"
CLOUD_PROTOCOL="mqtt"  # or "rest", "websocket"

# Retry behavior (in Coral/config.py)
retry_max_attempts = 5  # max retries
retry_backoff_sec = 2   # exponential backoff base
```

### Runtime Config (`Coral/config.py`)
```python
config = get_config()
config.cloud.protocol              # "mqtt"
config.cloud.retry_max_attempts    # 5
config.cloud.retry_backoff_sec     # 2
config.azure_connection_string     # from env var
```

## Testing

### Mock Mode (No Azure Credentials Needed)
```python
from cloud_signaling_interface import CloudSignalingInterface
from auth_controller import AuthenticationController

# Initialize in mock mode
cloud = CloudSignalingInterface(mock_mode=True)
auth = AuthenticationController(cloud_signaling=cloud, mock_mode=True)

# Start cloud auth
session_id = auth.start_authentication(source="cloud")

# Check queued messages
messages = cloud.get_message_queue()
# [{"type": "auth_started", "payload": {...}, ...}, ...]
```

### Real Azure (Requires Credentials)
1. Set `AZURE_IOT_CONNECTION_STRING` environment variable
2. Initialize normally (not mock mode)
3. Azure IoT Hub will invoke Direct Methods on Coral device
4. Telemetry appears in IoT Hub metrics

## Integration Points

### With PyTrack Interface (Unchanged)
- `PyTrackInterface` remains independent
- Both can trigger auth simultaneously
- Auth controller supports multiple signal sources
- No conflict; orthogonal signal paths

### With Models & Performance
- **Idle mode:** Camera stays off, models preloaded
- **Fast UX:** ~0 startup time once session initiated
- **No regression:** Eager model loading unchanged

## Next Steps for Real Azure Integration

### 1. **Deploy Coral with Azure Credentials**
```bash
export AZURE_IOT_CONNECTION_STRING="<your-connection-string>"
python admin_interface.py  # Cloud signaling now active
```

### 2. **Set Up Cloud Backend to Invoke Direct Methods**
- Cloud service needs to call: `cloud_interface._handle_start_auth()` and `_handle_stop_auth()`
- Or wrap in cloud API that invokes these methods via Azure SDK

### 3. **Monitor Telemetry**
- View `auth_progress` messages in Azure IoT Hub → Metrics
- View `auth_result` messages for final outcomes
- Set up alerts for failed authentications

### 4. **Handle Direct Method Responses**
- `start_auth` returns: `{"status": "ok"|"error", "session_id": str, "reason": str}`
- `stop_auth` returns: `{"status": "ok"|"error", "reason": str}`
- Use these responses to update mobile app UI

## Error Handling

All Azure failures gracefully degrade:
- Connection errors logged, auth continues locally
- Retry logic with exponential backoff (up to max_attempts)
- If all retries fail, auth proceeds without cloud notification
- No crash; local auth remains unaffected

## Security Notes

- ✓ No biometric data sent to cloud (only results)
- ✓ No face images transmitted (only auth results)
- ✓ Confidence scores sent but no confidence % (only yes/no)
- ✓ Connection string in environment (not hardcoded)
- ✓ Liveness status summarized (not detailed)
- ✓ Session tracking prevents unauthorized stops