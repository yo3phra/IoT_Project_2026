"""
Quick test of debug menu.
"""

import sys
import os

coral_dir = os.path.join(os.path.dirname(__file__), "Coral")
if coral_dir not in sys.path:
    sys.path.insert(0, coral_dir)

from admin_interface import AdminInterface
from embedding_store import EmbeddingStore
from user_manager import UserManager
import time

# Setup
db_path = "test_debug_menu.db"
if os.path.exists(db_path):
    os.remove(db_path)

print("Creating test user with embeddings...")
store = EmbeddingStore(db_path=db_path)
mgr = UserManager(store)

# Enroll test user
from enrollment_controller import EnrollmentController
enroll = EnrollmentController(embedding_store=store, mock_mode=True)

try:
    session_id = enroll.start_enrollment("test_001", "Test_User", {"role": "test"})
    print(f"[OK] Enrollment started: {session_id}")
except Exception as e:
    print(f"[ERROR] Enrollment start failed: {e}")
    # User probably already exists - just use it
    pass

for i in range(30):
    try:
        status = enroll.capture_enrollment_frame()
        if status["state"] == "success":
            print(f"[OK] Enrolled with {status['embeddings_captured']} embeddings")
            break
    except Exception as e:
        print(f"[WARN] Enrollment frame {i} failed: {e}")
        break

# Test debug menu logic (without UI)
admin = AdminInterface(mock_mode=True)
users = admin.user_manager.get_all_users()
print(f"[OK] Found {len(users)} users")

# Find the test user we just created
test_user = None
for user in users:
    if user["user_id"] == "test_001":
        test_user = user
        break

if not test_user:
    print("[ERROR] Test user not found!")
    sys.exit(1)

user_id = test_user["user_id"]
user_info = store.get_user_info(user_id)
stored_embeddings = store.get_user_embeddings(user_id)

if not test_user:
    print("[ERROR] Test user not found!")
    sys.exit(1)

user_id = test_user["user_id"]
user_info = store.get_user_info(user_id)
stored_embeddings = store.get_user_embeddings(user_id)

print(f"[OK] User: {user_info['username']}")
print(f"[OK] Stored embeddings: {len(stored_embeddings)}")

# Simulate face capture
admin.camera.open()
frame, _ = admin.camera.get_frame()

faces = admin.enrollment_controller.face_detector.detect(frame)
print(f"[OK] Faces detected: {len(faces)}")

if faces:
    face = faces[0]
    face_crop = face.crop_from_frame(frame)
    test_embedding = admin.enrollment_controller.face_recognizer.generate_embedding(face_crop)
    print(f"[OK] Test embedding generated: {test_embedding.dimension}D")

    # Compare
    distances = []
    for stored_emb in stored_embeddings:
        dist = test_embedding.distance_to(stored_emb)
        distances.append(dist)

    best_dist = min(distances)
    best_conf = 1.0 - (best_dist / 2.0)
    print(f"[OK] Best distance: {best_dist:.4f}")
    print(f"[OK] Best confidence: {best_conf:.1%}")

    if best_conf > 0.6:
        print("[OK] Recognition WOULD PASS")
    else:
        print("[WARN] Recognition WOULD FAIL")

admin.camera.close()

# Cleanup
if os.path.exists(db_path):
    os.remove(db_path)

print("\n[OK] Debug menu test completed successfully")
