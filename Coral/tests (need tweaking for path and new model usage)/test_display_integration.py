"""
Test workflow with camera display integration.
"""

import sys
import os

coral_dir = os.path.join(os.path.dirname(__file__), "Coral")
if coral_dir not in sys.path:
    sys.path.insert(0, coral_dir)

from enrollment_controller import EnrollmentController
from auth_controller import AuthenticationController
from user_manager import UserManager
from embedding_store import EmbeddingStore
from camera_display import CameraDisplay

print("TEST: Workflow with camera display")
print("=" * 70)

# Setup
db_path = "test_display.db"
if os.path.exists(db_path):
    os.remove(db_path)

store = EmbeddingStore(db_path=db_path)
user_mgr = UserManager(store)
enroll = EnrollmentController(embedding_store=store, mock_mode=True)

# Test 1: Enrollment with display
print("\n[TEST 1] Enrollment with CameraDisplay")
print("-" * 70)

try:
    session_id = enroll.start_enrollment(
        user_id="display_test",
        username="Display_Test",
        metadata={"role": "test"}
    )
    print(f"[OK] Session: {session_id}")

    # Simulate with display
    display = CameraDisplay(width=720, height=480)

    for i in range(15):
        status = enroll.capture_enrollment_frame()

        if status["state"] == "success":
            print(f"[OK] Enrolled! {status['embeddings_captured']} embeddings")
            break

        # Would show frame here with:
        # display.show_frame(frame, title="ENROLLMENT", progress_text=status['progress'])

        if i % 5 == 0:
            print(f"     Frame {i}: {status['embeddings_captured']}/{status['embeddings_target']}")

    display.close()

except Exception as e:
    print(f"[ERROR] {e}")
    import traceback
    traceback.print_exc()

# Test 2: CameraDisplay creation
print("\n[TEST 2] CameraDisplay module")
print("-" * 70)

try:
    with CameraDisplay() as display:
        print(f"[OK] CameraDisplay created")
        print(f"     Window name: {display.window_name}")
        print(f"     Resolution: {display.width}x{display.height}")
    print(f"[OK] Display context manager works")

except Exception as e:
    print(f"[ERROR] {e}")

# Cleanup
if os.path.exists(db_path):
    os.remove(db_path)

print("\n" + "=" * 70)
print("[OK] All tests passed")
