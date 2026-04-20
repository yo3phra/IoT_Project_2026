# Coral Face Recognition System - Project Context

**Project:** IoT Project 2026 - Coral Biometric Authentication  
**Status:** Active Development  

## System Summary

Biometric auth platform for Coral Edge TPU:
- Face enrollment with embeddings
- Real-time authentication with liveness checks
- Encrypted local embedding storage (AES-256-GCM)
- Pluggable embedding backends (ArcFace, TensorFlow, ONNX, Mock)
- Admin CLI for enrollment, auth testing, user lifecycle
- PyTrack event integration

## Runtime Flows

**Enrollment:**
Frames → Face Detection → Crop → Embedding (selected backend) → Encrypted Store

**Authentication:**
Face → Detection → Crop → Embedding → Compare with stored embeddings → Liveness → Result

**Storage:**
SQLite + AES-256-GCM encryption + PBKDF2 key derivation (100k iterations)

## Architecture Map

**Core Config + Infra**
- `config.py` - Central settings, Coral/Windows mode detection, 50+ config items
- `logger.py` - Sanitized logging (filters biometric data), per-module loggers
- `errors.py` - Custom exceptions (15 types), event/status codes

**Embedding Layer**
- `embedding_model.py` - Backend interface + implementations (ArcFace, TensorFlow, ONNX, Mock)
- `face_recognizer.py` - Unified embedding interface, `FaceEmbedding` class (vector + metadata)

**Vision + Liveness**
- `face_detector.py` - Face bounding box detection, low-cost operation
- `liveness_detector.py` - Anti-spoofing (head pose, blinks), temporal analysis
- `challenge_manager.py` - Liveness challenge sequencing (head turn, blink)

**Hardware + Storage**
- `camera_interface.py` - USB/Coral camera abstraction, frame capture
- `camera_display.py` - OpenCV overlays and real-time feedback UI
- `embedding_store.py` - Encrypted SQLite embeddings DB, AES-256-GCM + PBKDF2

**User + Session Control**
- `user_manager.py` - User lifecycle (create, remove, query)
- `enrollment_controller.py` - Enrollment session orchestration, multi-embedding capture
- `auth_controller.py` - Authentication orchestration, liveness challenge flow

**Interface + Integration**
- `admin_interface.py` - CLI menu (enroll, test, remove, list, debug)
- `pytrack_interface.py` - Event reporting to tracking system
- Cloud integration placeholder configured in `config.py`

## Embedding Backends

**Switch backend:** `FaceRecognizer(backend_type="onnx")`  
**Add backend:** inherit `EmbeddingModelBackend`, register in factory.

### ArcFaceBackend (`embedding_model.py`)
- Library: `arcface`
- error 404, to remove from codebase

### TensorFlowBackend (`embedding_model.py`)
- Models: `.tflite` (TPU)
- Input: 160×160 RGB, normalized [0,1]
- Output: 128D embedding vector
- Use case: Resource-constrained environments
- Status: Fallback support

### ONNXBackend (`embedding_model.py`)
- Model: `webface_r50.onnx`
- Runtime: ONNX Runtime
- Input: 112×112 RGB, normalized [-1,1]
- Output: 512D embedding vector
- Use case: Fast CPU inference
- Install: `pip install onnxruntime`
- Status: DEFAULT

### MockBackend (`embedding_model.py`)
- Output: Random 512D vector
- Use case: Testing without model files
- Status: Always available

## Security Model

✓ Encrypted embeddings at rest (AES-256-GCM)  
✓ No raw face images stored  
✓ Sanitized logging (no biometric payloads)  
✓ No cloud transmission of biometrics  
✓ PBKDF2-based secure key derivation

## Admin Interface

```bash
python admin_interface.py [--mock]
```

## Operational Defaults and Thresholds

**Authentication**
- Distance threshold: 0.6 (L2)
- Confidence threshold: 0.60
- Matching strategy: Compare against all stored embeddings

**Enrollment**
- Embeddings per user: 8
- Min frames between captures: 10 (angle variation)
- Session timeout: 120 seconds

**Liveness**
- Challenge timeout: 10 seconds
- Max attempts: 3
- Head turn requirement: 15 degrees
- Blink requirement: 2 blinks

**Storage**
- Encryption: AES-256-GCM
- Key iterations: 100,000 (PBKDF2)
- Estimated embedding footprint: ~16KB per user (8 × ~2KB)
- Estimated DB size: ~1MB for 50 users

## Roadmap

- Mobile app integration
- Encrypted cloud sync
- More hardware acceleration
- Performance testing and profiling

---
