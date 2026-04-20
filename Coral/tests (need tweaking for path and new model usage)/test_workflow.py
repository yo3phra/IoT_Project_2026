"""
Test admin interface workflows - enroll, auth, and removal
"""

import sys
import time
import os

# Add Coral directory to path
coral_dir = os.path.join(os.path.dirname(__file__), "Coral")
if coral_dir not in sys.path:
    sys.path.insert(0, coral_dir)

from enrollment_controller import EnrollmentController
from auth_controller import AuthenticationController
from user_manager import UserManager
from embedding_store import EmbeddingStore


def test_complete_workflow():
    """Test: Enroll → Recognize → Remove"""
    print("\n" + "="*70)
    print("ADMIN INTERFACE - COMPLETE WORKFLOW TEST")
    print("="*70)

    # Initialize with fresh database
    db_path = "test_workflow.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    store = EmbeddingStore(db_path=db_path)
    user_mgr = UserManager(store)
    enroll_ctrl = EnrollmentController(embedding_store=store, mock_mode=True)
    auth_ctrl = AuthenticationController(embedding_store=store, mock_mode=True)

    print("\n[STEP 1] ENROLL NEW USER")
    print("-" * 70)

    try:
        session_id = enroll_ctrl.start_enrollment(
            user_id="test_admin_001",
            username="Admin_Test_User",
            metadata={"role": "admin", "email": "test@example.com"}
        )
        print(f"[OK] Enrollment started: {session_id}")
    except Exception as e:
        print(f"[ERROR] Enrollment start failed: {e}")
        return False

    # Simulate frame capture (30 frames to be safe)
    print("Capturing face embeddings...")
    enroll_ctrl.camera.open()
    enroll_ctrl.camera.start_capture()

    try:
        for i in range(100):  # Increased for 8 embeddings
            status = enroll_ctrl.capture_enrollment_frame()

            if status["state"] == "success":
                print(f"[OK] Enrollment complete!")
                print(f"     Embeddings captured: {status['embeddings_captured']}")
                print(f"     Frames processed: {status['frames_processed']}")
                break

            if status.get("error_code"):
                print(f"     Frame {i}: ERROR - {status['error_code']}")
                if i == 0:
                    # Print first error in detail
                    print(f"     Full status: {status}")
            elif i % 5 == 0:
                print(f"     Frame {i}: {status['embeddings_captured']}/{status['embeddings_target']} embeddings")
        else:
            print(f"[ERROR] Enrollment did not complete after 100 frames")
            if enroll_ctrl.current_session:
                print(f"Final status: {enroll_ctrl.current_session}")
            return False
    finally:
        try:
            enroll_ctrl.camera.stop_capture()
            enroll_ctrl.camera.close()
        except:
            pass

    # Verify user was created
    users = user_mgr.get_all_users()
    if not users or users[0]["user_id"] != "test_admin_001":
        print("[ERROR] User not found in database")
        return False

    print(f"[OK] User enrolled: {users[0]['username']} ({users[0]['embedding_count']} embeddings)")

    print("\n[STEP 2] TEST AUTHENTICATION")
    print("-" * 70)

    try:
        auth_session = auth_ctrl.start_authentication()
        print(f"[OK] Auth session started: {auth_session}")
    except Exception as e:
        print(f"[ERROR] Auth start failed: {e}")
        return False

    # Simulate frame processing (15 frames)
    print("Processing frames for face recognition...")
    for i in range(20):
        status = auth_ctrl.process_frame()

        if status["state"] == "success":
            print(f"[OK] Authentication SUCCESS!")
            print(f"     User: {status['user_id']}")
            print(f"     Confidence: {status['confidence']:.1%}")
            print(f"     Liveness verified: {status['liveness_passed']}")
            break

        if status["state"] == "failure":
            print(f"[ERROR] Authentication failed: {status['error_code']}")
            return False

        if i % 5 == 0:
            print(f"     Frame {i}: {status['user_id']} (confidence: {status['confidence']:.1%})")
    else:
        print("[ERROR] Auth did not complete")
        return False

    print("\n[STEP 3] REMOVE USER")
    print("-" * 70)

    try:
        user_mgr.remove_user("test_admin_001")
        print(f"[OK] User removed: test_admin_001")
    except Exception as e:
        print(f"[ERROR] User removal failed: {e}")
        return False

    # Verify user was deleted
    remaining_users = user_mgr.get_all_users()
    if remaining_users:
        print(f"[ERROR] User still in database: {remaining_users}")
        return False

    print(f"[OK] Verified: User completely deleted")

    # Cleanup
    if os.path.exists(db_path):
        os.remove(db_path)

    print("\n" + "="*70)
    print("RESULT: ALL TESTS PASSED")
    print("="*70)
    return True


if __name__ == "__main__":
    success = test_complete_workflow()
    sys.exit(0 if success else 1)
