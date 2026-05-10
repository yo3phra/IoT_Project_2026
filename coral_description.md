# Coral Edge TPU Biometric Authentication System

## 1. Problem Statement

### Global Project Context
The IoT Bicycle Security System addresses bike theft through multi-layered monitoring: cloud-based tracking (mqtt-bridge aggregates LoRaWAN sensor telemetry), real-time fleet visibility (Flask web dashboard), and **last-mile biometric access control** (Coral subsystem).

### Coral-Specific Problem
Physical bike locks require on-device authentication when cloud connectivity is unavailable or for offline theft prevention. While the system includes PySense (accelerometer-based motion detection) and PyTrack (GPS/cellular location) sensors, these provide *event logging*, not *access control*. Coral was selected to implement local, fast, and privacy-preserving biometric authentication directly on the edge device using face recognition, eliminating dependency on continuous cloud connectivity for unlock decisions. The Google Coral Edge TPU accelerates real-time facial embedding computation with <100ms latency, critical for user-facing unlock workflows.

**Design Trade-offs**: Hardware acceleration (Coral TPU) was chosen over CPU-only inference to achieve sub-second face recognition response times. Raw face images are never transmitted to cloud or stored locally—only encrypted embeddings and telemetry are persisted, meeting privacy-by-design requirements.

## 2. Architectural Solution

### System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Coral Edge TPU Biometric Controller                            │
├─────────────────────────────────────────────────────────────────┤
│  Input: Physical button (user alert) / Frame stream (camera)   │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  AuthController: Orchestrates end-to-end auth flow      │  │
│  │  ├─ Camera (USB/Coral OV5645)                           │  │
│  │  │  └─ Frame capture (720×480, 15 FPS)                 │  │
│  │  ├─ FaceDetector (cascade-based, GPU fallback)         │  │
│  │  │  └─ Bounding box detection per frame               │  │
│  │  ├─ FaceRecognizer (pluggable embedding backend)       │  │
│  │  │  ├─ **ONNX WebFace R50** (default, 512D embedding) │  │
│  │  │  ├─ TensorFlow Lite (TPU, 128D embedding)          │  │
│  │  │  └─ Mock backend (testing)                         │  │
│  │  ├─ LivenessDetector (anti-spoofing)                  │  │
│  │  │  ├─ Head pose analysis                             │  │
│  │  │  └─ Blink detection (temporal eye tracking)        │  │
│  │  ├─ ChallengeManager (interactive liveness)           │  │
│  │  │  └─ Sequential head turn + blink prompts           │  │
│  │  └─ EmbeddingStore (encrypted SQLite persistence)     │  │
│  │     └─ AES-256-GCM + PBKDF2 (100k iterations)        │  │
│  │                                                         │  │
│  │  Output: Auth result + cloud telemetry                │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  CloudInterface: Azure IoT Hub integration (bidirectional)     │
│  ├─ Sends: auth_progress, auth_result (D2C telemetry)         │
│  └─ Receives: Requests for auth_result & image retrieval      │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
     ↓
  Azure IoT Hub ←→ Cloud Backend (Fleet Management)
     (Coral responds to data queries; NO commands trigger auth)
```

### Core Modules

| Module | Responsibility |
|--------|-----------------|
| **config.py** | Centralized runtime settings (50+ thresholds, device detection) |
| **auth_controller.py** | Session orchestration: detector → recognizer → liveness → decision |
| **embedding_model.py** | Pluggable backend interface (ONNX/TensorFlow/Mock) |
| **face_recognizer.py** | Unified embedding API, L2 normalization |
| **face_detector.py** | Face bounding box extraction |
| **liveness_detector.py** | Anti-spoofing: head pose + blink tracking |
| **challenge_manager.py** | Interactive challenge sequencing |
| **embedding_store.py** | Encrypted SQLite persistence (AES-256-GCM) |
| **user_manager.py** | User lifecycle (enroll, auth, remove) |
| **camera_interface.py** | Hardware abstraction (USB/Coral camera) |
| **cloud_signaling_interface.py** | Azure IoT Hub D2C telemetry (fire-and-forget) |
| **bridge_api.py** | Flask REST API for external image/alert integration |
| **admin_interface.py** | CLI for enrollment, testing, user management |

### Cloud-to-Coral Communication (Limited Scope)
By design, **Coral does NOT receive authentication trigger commands from cloud**—authentication is initiated only by local physical button press or Flask API call. However, Coral **responds to cloud data-retrieval requests**:
- **Request: Auth Result Confirmation**: Cloud queries Coral for latest auth result (user_id, confidence, timestamp) for confirmation and additional data sync with fleet backend
- **Request: Auth Image Retrieval** (future): Cloud requests latest captured image from last auth session for fleet monitoring/review (image saving not yet implemented)

This read-only request pattern maintains local auth independence while enabling cloud-side audit and fleet-wide monitoring. Coral operates independently for offline resilience; cloud backend coordinates with mobile app and PyTrack.

## 3. Machine Learning Models & Specifications

### Primary Model: ONNX WebFace R50 (Default)
- **Architecture**: ResNet-50 trained on WebFace dataset
- **Framework**: ONNX Runtime (CPU inference)
- **Input**: 112×112 RGB image, normalized to [-1, 1]
- **Output**: 512-dimensional embedding vector (L2-normalized)
- **Inference Latency**: to be measured
- **Advantages**: Fast CPU inference, broad cross-platform support, robust face representation
- **Deployment**: Default in production; no external dependencies beyond onnxruntime

### Secondary Model: TensorFlow Lite (TPU-Optimized)
- **Architecture**: MobileNetV2-based face embeddings, quantized for Edge TPU
- **Deployment Target**: Coral Dev Board, Accelerator USB modules
- **Input**: 160×160 RGB, normalized to [0, 1]
- **Output**: 128-dimensional embedding vector
- **Inference Latency**: to be measured
- **Advantages**: Hardware acceleration, ultra-low latency, minimal power consumption
- **Trade-off**: Model file size optimized for edge (smaller than ONNX)
- **Status**: Loaded conditionally when `/sys/module/apex` (Coral kernel) detected

### Mock Backend (Development Only)
- **Output**: Random 512D vector (testing orchestration without model files)
- **Use Case**: CI/CD pipeline, local prototyping

### Model Selection Rationale
**Why ONNX as default**: ONNX + CPU provides reliable baseline (user-perceptible but functional). ONNX format is not best for Coral TPU, the TensorFlow Lite model maybe more optimized, but for development we chose onnx for ease of use.

### Recognition Thresholds (Tunable in config.py)
| Parameter | Value | Notes |
|-----------|-------|-------|
| Distance Threshold (L2) | 0.6 | Below = match; else reject |
| Confidence Threshold | 0.60 | Auth result confidence minimum |
| Embeddings per User | 2 | Captured during enrollment, compared against all stored |

## 4. Workflow & Operational Behavior

### Enrollment Flow (Initiated by Admin CLI)
1. User faces camera for 2 embeddings (varied angles)
2. Per embedding: capture → face detect → crop → embed (ONNX/TF) → store (encrypted SQLite)
3. Each embedding separated by 10 frames (~667ms) to ensure facial angle variation
4. Session timeout: 120 seconds total
5. Output: User record + 2× encrypted embeddings

### Authentication Flow (Triggered by Physical Button or API)
1. **Session Init**: Start frame capture, send `auth_started` telemetry to cloud
2. **Frame Processing Loop** (30s timeout):
   - Detect face in frame
   - If face found: crop, generate embedding
   - L2 distance comparison against all stored embeddings for this user (or all users if unknown)
   - If distance < 0.6: candidate match, proceed to liveness challenge
3. **Liveness Challenge** (if match):
   - Prompt: "Turn head left" (15° threshold)
   - Prompt: "Blink twice" 
   - Per prompt: 10s timeout, max 3 attempts
   - Track head pose and eye blinks across frames
4. **Auth Decision**:
   - If liveness passed + embedding match: `SUCCESS` → unlock signal
   - Else: `FAILURE` → deny
5. **Cloud Telemetry**: Send `auth_result` (user_id, confidence, result) to Azure IoT Hub (no image data)

### Liveness Detection (Anti-Spoofing)
- **Head Pose**: Estimate 3D head orientation (pitch, yaw, roll) from face landmarks; detect rotation >15°
- **Blink Tracking**: Temporal eye-aspect-ratio analysis; trigger on eye closure transitions
- **Texture Analysis**: Reject static images by analyzing face texture consistency across frames
- Rationale: Prevents spoofing via printed photos or video replay

### Session Timeout & Cleanup
- Auth session: 30s (user must complete biometric flow within window)
- Enrollment session: 120s (admin captures 2 embeddings)
- Expired sessions logged; no unlock issued

## 5. Current Implementation Status

### Completed
✓ Core auth orchestration (detector → recognizer → liveness → decision)  
✓ ONNX WebFace R50 embeddings (512D, L2-normalized)  
✓ TensorFlow Lite TPU model support (auto-detect Coral hardware)  
✓ Encrypted embedding storage (AES-256-GCM, PBKDF2 key derivation)  
✓ Interactive liveness challenges (head turn, blink)  
✓ Admin CLI (enroll, test auth, remove user, list users)  
✓ Azure IoT Hub telemetry (one-way D2C: auth_progress, auth_result)  
✓ Flask bridge API (REST endpoints: /alert, /result, /image)  
✓ Sanitized logging (no biometric payloads logged)  

### Planned Enhancements

**Phase 1 (High Priority)**
- **Image Monitoring to Cloud**: Capture and encrypt alert snapshots; transmit to Azure Blob Storage for fleet review (e.g., unauthorized access attempts)
  - Implement: Frame buffer + encryption + signed blob upload
  - Privacy: Blur faces, store only in compliance with GDPR
- **Message Queuing for Offline Resilience**: Buffer auth results and telemetry when cloud unavailable; sync on reconnect
  - Technology: SQLite queue (local) or MQTT with persistent subscriptions
  - Benefit: Unlocked bikes log attempts even without network

**Phase 2 (Medium Priority)**
- Bidirectional cloud commands (e.g., remote unlock override by admin)
- Enrollment verification via cloud (biometric template sync)
- Performance profiling on actual Coral hardware (latency/power)

**Phase 3 (Future)**
- Multi-modal biometrics (face + fingerprint fallback) for extended offline operation
- Model updates over-the-air (encrypted embedding model distribution)
- Federated learning (aggregate anonymous embeddings across fleet for model refinement without centralized data)

## 6. Integration Points

- **PyTrack** (Accelerometer/GPS): Transmits location and motion profile; Coral biometric result logged by cloud backend
- **Azure IoT Hub**: Coral sends telemetry (auth_progress, auth_result); receives data-retrieval requests (latest auth result confirmation, image retrieval for future)
- **Flask Web App**: Cloud backend queries biometric results via Azure; displays user unlock events on dashboard
- **Mobile App**: Authenticated users unlock via cloud (phone trust token); does not require or trigger Coral auth
- **Bridge API** (bridge_api.py): Local Flask app: /alert endpoint for auth triggering; /result for latest auth status

---

**Document Version**: 1.0  
**Last Updated**: 2026-05-10  
**Deployment Target**: Google Coral Edge TPU (Dev Board, USB camera, TPU head), Windows Prototype (USB camera)  
**Key Technologies**: ONNX Runtime, TensorFlow Lite, Azure IoT Hub, SQLite, AES-256-GCM, Flask
