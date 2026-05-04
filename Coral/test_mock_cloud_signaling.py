"""
Quick test of cloud signaling interface in mock mode.
Verifies azure signaling integration without requiring actual Azure credentials.
"""

import sys
import os
import time
from pathlib import Path

# Add Coral directory to path
coral_dir = os.path.dirname(__file__)
if coral_dir not in sys.path:
    sys.path.insert(0, coral_dir)

from config import get_config
from auth_controller import AuthenticationController, AuthenticationResult
from cloud_signaling_interface import CloudSignalingInterface
from embedding_store import EmbeddingStore


def test_cloud_signaling_mock_mode():
    """Test cloud signaling in mock mode without Azure connection."""
    print("\n" + "="*70)
    print("Testing Cloud Signaling Interface (Mock Mode)")
    print("="*70)

    # Initialize cloud signaling in mock mode
    cloud_interface = CloudSignalingInterface(mock_mode=True)
    print(f"\n✓ CloudSignalingInterface initialized: {cloud_interface}")

    # Initialize auth controller with cloud signaling
    embedding_store = EmbeddingStore()
    auth_controller = AuthenticationController(
        embedding_store=embedding_store,
        mock_mode=True,
        cloud_signaling=cloud_interface
    )
    print(f"✓ AuthenticationController initialized with cloud signaling")

    # Update cloud interface with auth controller reference
    cloud_interface.auth_controller = auth_controller

    # Test 1: Start authentication from cloud
    print("\n[Test 1] Starting cloud-initiated authentication...")
    session_id = auth_controller.start_authentication(
        user_hint="test_user",
        source="cloud"
    )
    print(f"✓ Session started: {session_id}")
    print(f"  Session source: {auth_controller.current_session.source}")

    # Verify auth_started message was queued
    queued_messages = cloud_interface.get_message_queue()
    assert len(queued_messages) > 0, "Expected auth_started message in queue"
    auth_started_msg = queued_messages[0]
    assert auth_started_msg["type"] == "auth_started"
    assert auth_started_msg["payload"]["session_id"] == session_id
    print(f"✓ auth_started message queued: {auth_started_msg['payload']}")

    # Test 2: Direct Method - start_auth from cloud
    print("\n[Test 2] Testing Direct Method: start_auth...")
    cloud_interface.clear_message_queue()
    response = cloud_interface._handle_start_auth({
        "user_hint": "cloud_user",
        "source": "cloud"
    })
    print(f"✓ start_auth response: {response}")
    assert response["status"] == "ok"
    assert response["session_id"] is not None

    # Test 3: Send auth progress
    print("\n[Test 3] Testing auth progress telemetry...")
    cloud_interface.clear_message_queue()

    # Simulate progress update
    success = cloud_interface.send_auth_progress(
        session_id=session_id,
        state="recognized",
        confidence_bool=True,
        liveness_status={"status": "in_progress", "completed_challenges": 1, "total_challenges": 2}
    )
    print(f"✓ Auth progress sent: {success}")
    progress_msgs = cloud_interface.get_message_queue()
    if progress_msgs:
        print(f"✓ Progress message queued: {progress_msgs[0]}")

    # Test 4: Send auth result
    print("\n[Test 4] Testing final auth result...")
    cloud_interface.clear_message_queue()

    # Manually finalize session to trigger result send
    auth_controller.current_session.user_detected_id = "user_123"
    auth_controller.current_session.user_detected_confidence = 0.95
    auth_controller.current_session.result = AuthenticationResult.SUCCESS
    auth_controller._finalize_session()

    result_msgs = [m for m in cloud_interface.get_message_queue()
                   if m.get("type") == "auth_result"]
    if result_msgs:
        print(f"✓ Auth result message queued: {result_msgs[0]['payload']}")

    # Test 5: Direct Method - stop_auth
    print("\n[Test 5] Testing Direct Method: stop_auth...")
    new_session_id = auth_controller.start_authentication(source="cloud")
    response = cloud_interface._handle_stop_auth({
        "session_id": new_session_id,
        "reason": "user_cancelled"
    })
    print(f"✓ stop_auth response: {response}")
    assert response["status"] == "ok"

    # Test 6: Idle mode verification
    print("\n[Test 6] Verifying idle mode...")
    auth_controller.end_session()
    assert auth_controller.current_session is None, "Expected no active session in idle mode"
    print(f"✓ Idle mode verified: {auth_controller}")

    print("\n" + "="*70)
    print("ALL TESTS PASSED ✓")
    print("="*70)
    print("\nCloud signaling interface is operational in mock mode.")
    print("Ready for real Azure IoT Hub integration with actual credentials.\n")


if __name__ == "__main__":
    try:
        test_cloud_signaling_mock_mode()
    except Exception as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
