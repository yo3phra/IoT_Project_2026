"""
PyTrack interface - communication with motion/GPS tracking module.
Provides authentication state for theft detection logic.
PLACEHOLDER: Mock implementation for development.
"""

import time
from typing import Optional, Dict
from config import get_config
from logger import logger_pytrack
from errors import PyTrackError


class PyTrackMessage:
    """Message format for PyTrack communication."""

    MSG_TYPE_AUTH_STATE = "auth_state"
    MSG_TYPE_TRACKING_REQUEST = "tracking_request"
    MSG_TYPE_THEFT_ALERT = "theft_alert"

    def __init__(self, msg_type: str, payload: Dict):
        """
        Args:
            msg_type: Message type
            payload: Message payload dict
        """
        self.type = msg_type
        self.payload = payload
        self.timestamp = time.time()

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "type": self.type,
            "timestamp": self.timestamp,
            "payload": self.payload
        }

    def __repr__(self):
        return f"PyTrackMessage(type={self.type})"


class PyTrackInterface:
    """
    Interface to PyTrack module.

    Responsibilities:
    - Send authentication state periodically
    - Receive tracking triggers from PyTrack
    - Coordinate theft alerts
    - Mock implementation for Development (Windows)
    """

    def __init__(self, auth_controller=None, mock_mode: bool = False):
        """
        Initialize PyTrack interface.

        Args:
            auth_controller: Reference to AuthenticationController
            mock_mode: Use mock mode for Windows development
        """
        self.config = get_config().pytrack
        self.auth_controller = auth_controller
        self.mock_mode = mock_mode or self.config.enable_mock_mode
        self.last_state_sent = None
        self.message_queue = []

        logger_pytrack.info(f"PyTrack interface initialized (mock={self.mock_mode})")

    def send_auth_state(self) -> bool:
        """
        Send current authentication state to PyTrack.

        Returns:
            True if successful
        """
        if not self.auth_controller:
            logger_pytrack.warning("Auth controller not set")
            return False

        try:
            payload = {
                "last_auth_timestamp": self.auth_controller.last_auth_timestamp,
                "last_auth_user_id": self.auth_controller.last_auth_user_id,
                "last_auth_confidence": self.auth_controller.last_auth_confidence,
                "is_recently_authenticated": self.auth_controller.is_recently_authenticated(),
                "auth_timeout_threshold_sec": self.config.timeout_for_auth_sec
            }

            message = PyTrackMessage(PyTrackMessage.MSG_TYPE_AUTH_STATE, payload)

            if self.mock_mode:
                return self._send_mock(message)
            else:
                return self._send_actual(message)

        except Exception as e:
            logger_pytrack.error(f"Failed to send auth state: {e}")
            raise PyTrackError(f"Send failed: {e}")

    def _send_mock(self, message: PyTrackMessage) -> bool:
        """Mock send (development/testing)."""
        self.message_queue.append(message.to_dict())
        logger_pytrack.debug(f"Mock sent: {message}")
        return True

    def _send_actual(self, message: PyTrackMessage) -> bool:
        """
        Actual send to PyTrack (placeholder).
        In production: would use serial communication, shared memory, or IPC.
        """
        logger_pytrack.info(f"Would send to PyTrack: {message}")
        # TODO: Implement actual communication
        return True

    def request_tracking_activation(self, reason: str = "theft_suspected") -> bool:
        """
        Request PyTrack to activate tracking mode.

        Args:
            reason: Reason for tracking activation

        Returns:
            True if request successful
        """
        try:
            payload = {
                "command": "start_tracking",
                "reason": reason,
                "timestamp": time.time()
            }

            message = PyTrackMessage(PyTrackMessage.MSG_TYPE_TRACKING_REQUEST, payload)

            if self.mock_mode:
                return self._send_mock(message)
            else:
                return self._send_actual(message)

        except Exception as e:
            logger_pytrack.error(f"Tracking activation failed: {e}")
            raise PyTrackError(f"Activation failed: {e}")

    def report_theft_alert(self, accelerometer_data: Dict = None) -> bool:
        """
        Report potential theft to PyTrack.

        Args:
            accelerometer_data: Optional accelerometer data from PyTrack

        Returns:
            True if successful
        """
        try:
            payload = {
                "alert_type": "suspicious_movement_no_auth",
                "timestamp": time.time(),
                "last_auth_timeout_sec": self.auth_controller.seconds_since_last_auth if self.auth_controller else None,
                "accelerometer_data": accelerometer_data or {}
            }

            message = PyTrackMessage(PyTrackMessage.MSG_TYPE_THEFT_ALERT, payload)

            if self.mock_mode:
                return self._send_mock(message)
            else:
                return self._send_actual(message)

        except Exception as e:
            logger_pytrack.error(f"Theft alert failed: {e}")
            raise PyTrackError(f"Alert failed: {e}")

    def poll_suspicious_activity(self) -> Optional[Dict]:
        """
        Poll PyTrack for suspicious accelerometer activity.
        (In production, this might be push-based via callbacks)

        Returns:
            Activity dict or None if none detected
        """
        if self.mock_mode:
            # Return mock activity (for testing)
            return None

        # TODO: Implement actual polling
        return None

    def get_message_queue(self) -> list:
        """Get queued messages (for testing)."""
        return self.message_queue

    def clear_message_queue(self):
        """Clear message queue (for testing)."""
        self.message_queue = []


class TheftDetectionCoordinator:
    """
    Coordinates theft detection logic between BiometricAuth and PyTrack.

    Threat Model:
    - Unauthorized person tries to move bike (accelerometer spike)
    - If NO recent user authentication → raise theft alert
    - If recent valid auth → allow movement (authorized user)
    """

    def __init__(self, auth_controller, pytrack_interface: PyTrackInterface):
        """
        Args:
            auth_controller: AuthenticationController instance
            pytrack_interface: PyTrackInterface instance
        """
        self.auth_controller = auth_controller
        self.pytrack = pytrack_interface

    def evaluate_suspicious_movement(self, accelerometer_data: Dict) -> bool:
        """
        Evaluate if suspicious movement should trigger theft alert.

        Args:
            accelerometer_data: Acceleration readings from PyTrack

        Returns:
            True if theft alert should be triggered
        """
        # Check if recently authenticated
        if self.auth_controller.is_recently_authenticated():
            logger_pytrack.debug("Suspicious movement but recently authenticated. Allowing.")
            return False

        # No recent auth + suspicious movement = THEFT ALERT
        logger_pytrack.warning("Suspicious movement detected without recent authentication!")
        self.pytrack.report_theft_alert(accelerometer_data)
        self.pytrack.request_tracking_activation(reason="unexpected_movement_no_auth")

        return True

    def __repr__(self):
        return "TheftDetectionCoordinator"
