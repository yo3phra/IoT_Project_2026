"""
Integration test for enrollment system and admin interface.
Tests all components work together correctly (with mock mode).
"""

import sys
import time
from enrollment_controller import EnrollmentController, EnrollmentResult
from auth_controller import AuthenticationController, AuthenticationResult
from user_manager import UserManager
from embedding_store import EmbeddingStore


def test_enrollment_flow():
    """Test new user enrollment workflow."""
    print("\n=== TEST 1: Enrollment Flow ===")

    # Initialize components
    store = EmbeddingStore(db_path="test_enrollment.db")
    user_mgr = UserManager(store)
    enroll = EnrollmentController(embedding_store=store, mock_mode=True)

    # Start enrollment
    print("Starting enrollment for new user...")
    try:
        session_id = enroll.start_enrollment(
            user_id="test_user_001",
            username="Test User",
            metadata={"role": "owner"}
        )
        print(f"✓ Session created: {session_id}")
    except Exception as e:
        print(f"✗ Failed to start enrollment: {e}")
        return False

    # Simulate frame capture
    print("Simulating face capture...")
    capture_count = 0
    for i in range(20):  # Simulate 20 frames
        status = enroll.capture_enrollment_frame()

        if status["state"] == "success":
            print(f"✓ Enrollment complete: {status['embeddings_captured']} embeddings captured")
            capture_count = status['embeddings_captured']
            break

        if i % 5 == 0:
            print(f"  Frame {i}: {status['embeddings_captured']}/{status['embeddings_target']} embeddings")

    if enroll.current_session.result != EnrollmentResult.SUCCESS:
        print("✗ Enrollment did not complete successfully")
        return False

    print(f"✓ Test 1 PASSED: User enrolled with {capture_count} embeddings\n")
    return True


def test_user_retrieval():
    """Test user data retrieval."""
    print("=== TEST 2: User Retrieval ===")

    store = EmbeddingStore(db_path="test_enrollment.db")
    user_mgr = UserManager(store)

    # Get enrolled user
    users = user_mgr.get_all_users()
    if not users:
        print("✗ No users found in database")
        return False

    user = users[0]
    print(f"✓ User found: {user['username']} ({user['user_id']})")
    print(f"✓ Embeddings: {user['embedding_count']}")

    # Get specific user
    user_info = user_mgr.get_user("test_user_001")
    if not user_info:
        print("✗ User lookup failed")
        return False

    print(f"✓ User info retrieved successfully")
    print(f"✓ Test 2 PASSED\n")
    return True


def test_auth_workflow():
    """Test authentication workflow with enrolled user."""
    print("=== TEST 3: Authentication Workflow ===")

    store = EmbeddingStore(db_path="test_enrollment.db")
    auth = AuthenticationController(embedding_store=store, mock_mode=True)

    # Start auth session
    try:
        session_id = auth.start_authentication()
        print(f"✓ Auth session created: {session_id}")
    except Exception as e:
        print(f"✗ Failed to start auth: {e}")
        return False

    # Simulate frame processing
    print("Simulating face recognition...")
    for i in range(15):  # Simulate 15 frames
        status = auth.process_frame()

        if status["state"] == "success":
            print(f"✓ Authentication SUCCESS")
            print(f"  User: {status['user_id']}")
            print(f"  Confidence: {status['confidence']:.1%}")
            return True

        if status["state"] == "failure":
            print(f"✗ Authentication FAILED: {status['error_code']}")
            return False

        if i % 5 == 0:
            print(f"  Processing frame {i}...")

    # End session if not complete
    result = auth.end_session()
    if result == AuthenticationResult.SUCCESS:
        print(f"✓ Test 3 PASSED\n")
        return True
    else:
        print(f"✗ Auth session did not complete: {result.value}")
        return False


def test_user_removal():
    """Test user removal workflow."""
    print("=== TEST 4: User Removal ===")

    store = EmbeddingStore(db_path="test_enrollment.db")
    user_mgr = UserManager(store)

    # Check user exists
    user_info = user_mgr.get_user("test_user_001")
    if not user_info:
        print("✗ User not found for removal test")
        return False

    print(f"✓ User found: {user_info['username']}")

    # Get embedding count before removal
    embedding_count_before = store.get_embedding_count("test_user_001")
    print(f"✓ Embeddings before removal: {embedding_count_before}")

    # Remove user
    try:
        user_mgr.remove_user("test_user_001")
        print(f"✓ User removed successfully")
    except Exception as e:
        print(f"✗ Failed to remove user: {e}")
        return False

    # Verify user is gone
    user_info = user_mgr.get_user("test_user_001")
    if user_info:
        print("✗ User still exists after removal")
        return False

    print(f"✓ User verified deleted")
    print(f"✓ Test 4 PASSED\n")
    return True


def main():
    """Run all integration tests."""
    print("\n" + "="*60)
    print("Coral Face Recognition - Integration Tests")
    print("(Running in MOCK MODE - no hardware required)")
    print("="*60)

    tests = [
        ("Enrollment Flow", test_enrollment_flow),
        ("User Retrieval", test_user_retrieval),
        ("Authentication", test_auth_workflow),
        ("User Removal", test_user_removal),
    ]

    results = []
    for name, test_func in tests:
        try:
            passed = test_func()
            results.append((name, passed))
        except Exception as e:
            print(f"✗ {name} raised exception: {e}\n")
            results.append((name, False))

    # Summary
    print("="*60)
    print("TEST SUMMARY")
    print("="*60)

    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {name}")

    total = len(results)
    passed = sum(1 for _, p in results if p)

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n✓ All tests PASSED!")
        return 0
    else:
        print(f"\n✗ {total - passed} test(s) FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
