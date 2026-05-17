"""
Test cloud interface in mock mode.
Verifies telemetry (one-way) integration without requiring Azure credentials.
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
from cloud_signaling_interface import CloudInterface
from embedding_store import EmbeddingStore


def test_cloud_interface_mock_mode():
    """Test cloud interface in mock mode (telemetry only)."""
    print("\n" + "="*70)
    print("Testing Cloud Interface (Mock Mode - Telemetry Only)")
    print("="*70)

    # Initialize cloud interface in mock mode
    cloud_interface = CloudInterface(mock_mode=True)
    print(f"\nCloudInterface initialized: {cloud_interface}")

    # Initialize auth controller (no cloud_signaling parameter)
    embedding_store = EmbeddingStore()
    auth_controller = AuthenticationController(
        embedding_store=embedding_store,
        mock_mode=True
    )
    print(f"AuthenticationController initialized")

    # Test 1: Start authentication locally (physical button)
    print("\n[Test 1] Starting local authentication...")
    session_id = auth_controller.start_authentication()
    print(f"Session started: {session_id}")

    # Send auth_started telemetry
    success = cloud_interface.send_auth_started(session_id)
    assert success, "Failed to send auth_started"
    print(f"auth_started telemetry sent")

    # Verify message was queued in mock mode
    queued_messages = cloud_interface.get_message_queue()
    assert len(queued_messages) > 0, "Expected auth_started message in queue"
    auth_started_msg = queued_messages[0]
    assert auth_started_msg["type"] == "auth_started"
    assert auth_started_msg["payload"]["session_id"] == session_id
    print(f"auth_started message queued: {auth_started_msg['payload']}")

    # Test 2: Send auth progress telemetry
    print("\n[Test 2] Testing auth progress telemetry...")
    cloud_interface.clear_message_queue()

    success = cloud_interface.send_auth_progress(
        session_id=session_id,
        state="recognized",
        confidence_bool=True,
        liveness_status={"status": "in_progress", "completed_challenges": 1, "total_challenges": 2}
    )
    assert success, "Failed to send auth_progress"
    print(f"Auth progress telemetry sent")

    progress_msgs = cloud_interface.get_message_queue()
    assert len(progress_msgs) > 0, "Expected progress message in queue"
    progress_msg = progress_msgs[0]
    assert progress_msg["type"] == "auth_progress"
    assert progress_msg["payload"]["state"] == "recognized"
    print(f"Progress message queued: {progress_msg['payload']}")

    # Test 3: Send auth result telemetry
    print("\n[Test 3] Testing final auth result telemetry...")
    cloud_interface.clear_message_queue()

    success = cloud_interface.send_auth_result(
        session_id=session_id,
        result="success",
        user_id="user_123",
        confidence=0.95
    )
    assert success, "Failed to send auth_result"
    print(f"Auth result telemetry sent")

    result_msgs = cloud_interface.get_message_queue()
    assert len(result_msgs) > 0, "Expected result message in queue"
    result_msg = result_msgs[0]
    assert result_msg["type"] == "auth_result"
    assert result_msg["payload"]["result"] == "success"
    assert result_msg["payload"]["user_id"] == "user_123"
    print(f"Result message queued: {result_msg['payload']}")

    # Test 4: Idle mode verification
    print("\n[Test 4] Verifying idle mode...")
    auth_controller.end_session()
    assert auth_controller.current_session is None, "Expected no active session in idle mode"
    print(f"Idle mode verified: {auth_controller}")

    # Test 5: Multiple auth sessions
    print("\n[Test 5] Testing multiple sequential sessions...")
    cloud_interface.clear_message_queue()

    for i in range(3):
        sid = auth_controller.start_authentication()
        cloud_interface.send_auth_started(sid)
        cloud_interface.send_auth_result(sid, "failure", None, 0.0)
        auth_controller.end_session()

    all_msgs = cloud_interface.get_message_queue()
    started_count = sum(1 for m in all_msgs if m["type"] == "auth_started")
    result_count = sum(1 for m in all_msgs if m["type"] == "auth_result")
    assert started_count == 3, f"Expected 3 auth_started, got {started_count}"
    assert result_count == 3, f"Expected 3 auth_result, got {result_count}"
    print(f"Multiple sessions handled: {started_count} started, {result_count} results")

    print("\n" + "="*70)
    print("ALL TESTS PASSED ")
    print("="*70)
    print("\nCloud interface is operational in mock mode (telemetry only).")
    print("Ready for real Azure IoT Hub integration with actual credentials.\n")


if __name__ == "__main__":
    try:
        test_cloud_interface_mock_mode()
    except Exception as e:
        print(f"\nTEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
