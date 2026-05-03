"""
Test embedding recognition debug logic.
"""

import sys
import os

coral_dir = os.path.join(os.path.dirname(__file__), "Coral")
if coral_dir not in sys.path:
    sys.path.insert(0, coral_dir)

from embedding_store import EmbeddingStore
from user_manager import UserManager
from enrollment_controller import EnrollmentController
from camera_interface import get_camera

# Fresh database
db_path = "test_embedding_debug.db"
if os.path.exists(db_path):
    os.remove(db_path)

print("=== Embedding Recognition Debug Test ===\n")

# Setup
store = EmbeddingStore(db_path=db_path)
mgr = UserManager(store)
enroll = EnrollmentController(embedding_store=store, mock_mode=True)
camera = get_camera(mock=True)

# 1. Enroll user
print("[STEP 1] Enroll user with 8 embeddings")
session_id = enroll.start_enrollment("user_001", "Test_User", {})
print(f"  Session: {session_id}")

for i in range(100):
    status = enroll.capture_enrollment_frame()
    if status["state"] == "success":
        print(f"  Embeddings captured: {status['embeddings_captured']}")
        break

# 2. Load stored embeddings
print("\n[STEP 2] Load stored embeddings")
stored_embeddings = store.get_user_embeddings("user_001")
print(f"  Loaded: {len(stored_embeddings)} embeddings")
print(f"  Dimension: {stored_embeddings[0].dimension}D")

# 3. Generate test embedding (from mock camera)
print("\n[STEP 3] Generate test embedding from camera")
camera.open()
frame, _ = camera.get_frame()
print(f"  Frame shape: {frame.shape}")

faces = enroll.face_detector.detect(frame)
print(f"  Faces detected: {len(faces)}")

if faces:
    face = faces[0]
    face_crop = face.crop_from_frame(frame)
    test_embedding = enroll.face_recognizer.generate_embedding(face_crop)
    print(f"  Test embedding: {test_embedding.dimension}D")

    # 4. Compare distances
    print("\n[STEP 4] Compare test embedding vs stored")
    distances = []
    for idx, stored_emb in enumerate(stored_embeddings):
        dist = test_embedding.distance_to(stored_emb)
        confidence = 1.0 - (dist / 2.0)
        distances.append(dist)
        if idx < 3:  # Show first 3
            print(f"  #{idx+1}: Distance={dist:.4f}, Confidence={confidence:.1%}")
        elif idx == 3:
            print(f"  ... ({len(stored_embeddings)-3} more)")

    # 5. Results
    print("\n[STEP 5] Recognition results")
    best_dist = min(distances)
    best_idx = distances.index(best_dist)
    best_conf = 1.0 - (best_dist / 2.0)
    threshold_conf = 1.0 - (0.6 / 2.0)  # Default threshold

    print(f"  Best match: Embedding #{best_idx+1}")
    print(f"  Distance: {best_dist:.4f}")
    print(f"  Confidence: {best_conf:.1%}")
    print(f"  Threshold: {threshold_conf:.1%}")

    if best_conf > threshold_conf:
        print(f"  Status: [OK] RECOGNITION PASS")
    else:
        print(f"  Status: [FAIL] RECOGNITION FAIL")

camera.close()

# Cleanup
if os.path.exists(db_path):
    os.remove(db_path)

print("\n[OK] Debug test completed")
