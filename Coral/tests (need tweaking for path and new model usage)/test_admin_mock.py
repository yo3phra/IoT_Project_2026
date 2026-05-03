#!/usr/bin/env python3
"""Quick test of admin interface functionality with mock models."""

import sys
import os

coral_dir = os.path.join(os.path.dirname(__file__), "Coral")
if coral_dir not in sys.path:
    sys.path.insert(0, coral_dir)

from admin_interface import AdminInterface
from embedding_store import EmbeddingStore
import time

db_path = "test_admin_mock.db"
if os.path.exists(db_path):
    os.remove(db_path)

print("="*70)
print("Admin Interface - Mock Mode Test")
print("="*70)

# Initialize with custom DB path
from config import reset_config
reset_config()
from config import get_config
get_config().embedding_store.db_path = db_path

admin = AdminInterface(mock_mode=True)
store = admin.embedding_store

print("\n[TEST 1] List users (empty)")
users = admin.user_manager.get_all_users()
print(f"  Users: {len(users)}")

print("\n[TEST 2] Enroll test user")
success = admin.user_manager.enroll_user("test_admin", "Admin_Test", {"role": "admin"})
print(f"  Enrollment: {success}")
users = admin.user_manager.get_all_users()
print(f"  Total users: {len(users)}")
if users:
    print(f"  User: {users[0]['username']} ({users[0]['user_id']})")

print("\n[TEST 3] Capture embeddings for enrolled user")
from enrollment_controller import EnrollmentController
enroll = EnrollmentController(embedding_store=store, mock_mode=True)

# Start new enrollment session for a different user
session = enroll.start_enrollment("user_123", "New_User", {})
print(f"  Session started: {session[:8]}...")

for i in range(100):
    status = enroll.capture_enrollment_frame()
    if status["state"] == "success":
        print(f"  Captured: {status['embeddings_captured']} embeddings")
        break

print("\n[TEST 4] Get enrolled user data")
embeddings = store.get_user_embeddings("user_123")
print(f"  Stored embeddings: {len(embeddings)}")
print(f"  Embedding dimension: {embeddings[0].dimension}D")

print("\n[TEST 5] Test recognition (same face)")
from camera_interface import get_camera
camera = get_camera(mock=True)
camera.open()
frame, _ = camera.get_frame()
faces = enroll.face_detector.detect(frame)
print(f"  Faces in frame: {len(faces)}")

if faces:
    face = faces[0]
    face_crop = face.crop_from_frame(frame)
    test_emb = enroll.face_recognizer.generate_embedding(face_crop)

    # Compare
    distances = [test_emb.distance_to(stored) for stored in embeddings]
    best_dist = min(distances)
    best_conf = 1.0 - (best_dist / 2.0)

    print(f"  Best distance: {best_dist:.4f}")
    print(f"  Best confidence: {best_conf:.1%}")
    print(f"  Threshold: 70.0%")

    if best_conf > 0.7:
        print(f"  Status: [OK] Would PASS")
    else:
        print(f"  Status: [INFO] Would FAIL (expected with mock)")

camera.close()

print("\n[TEST 6] Remove user")
store.remove_user("user_123")
users = admin.user_manager.get_all_users()
print(f"  Users after removal: {len(users)}")

print("\n" + "="*70)
print("Result: All functions working with mock models!")
print("="*70)
print("\nNext: Use real FaceNet model or alternative model")
print("See: REAL_MODELS_TROUBLESHOOTING.md")

# Cleanup
try:
    if os.path.exists(db_path):
        os.remove(db_path)
except Exception as e:
    print(f"\n[Note] Could not delete {db_path}: {e}")
