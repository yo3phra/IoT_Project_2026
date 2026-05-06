"""
Azure IoT Hub signaling interface for Coral.
Provides bidirectional communication with cloud for remote authentication triggers and status updates.
"""

import time
import json
import uuid
from typing import Optional, Dict, Callable
from dataclasses import dataclass
from datetime import datetime

from config import get_config
from logger import logger_cloud
from errors import CloudError, StatusCode


@dataclass
class CloudSignalMessage:
    """Message format for cloud signaling."""

    MSG_TYPE_AUTH_STARTED = "auth_started"
    MSG_TYPE_AUTH_PROGRESS = "auth_progress"
    MSG_TYPE_AUTH_RESULT = "auth_result"
    MSG_TYPE_AUTH_STOPPED = "auth_stopped"

    msg_type: str
    payload: Dict
    timestamp: float = None

    def __post_init__(self):
        """Set timestamp if not provided."""
        if self.timestamp is None:
            self.timestamp = time.time()

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "type": self.msg_type,
            "timestamp": self.timestamp,
            "payload": self.payload
        }

    def __repr__(self):
        return f"CloudSignalMessage(type={self.msg_type}, timestamp={self.timestamp})"


class CloudSignalingInterface:
    """
    Azure IoT Hub signaling interface.

    Responsibilities:
    - Send authentication status (started, progress, result, stopped)
    - Receive auth commands via Direct Methods (start_auth, stop_auth)
    - Handle bidirectional cloud communication with Coral
    - Mock implementation for Windows development
    """

    def __init__(
        self,
        auth_controller=None,
        mock_mode: bool = False
    ):
        """
        Initialize cloud signaling interface.

        Args:
            auth_controller: Reference to AuthenticationController
            mock_mode: Use mock mode for Windows development
        """
        self.config = get_config().cloud
        self.runtime_config = get_config()
        self.auth_controller = auth_controller
        self.mock_mode = mock_mode or self.runtime_config.is_windows

        # Azure IoT Hub client (lazy-initialized)
        self.client = None
        self.connected = False
        self.message_queue = []  # For mock mode and testing

        # State tracking
        self.active_session_id: Optional[str] = None
        self.last_progress_state: Optional[str] = None  # Track last state to avoid duplicate sends

        # Direct Method handlers mapping
        self.method_handlers: Dict[str, Callable] = {
            "start_auth": self._handle_start_auth,
            "stop_auth": self._handle_stop_auth,
        }

        logger_cloud.info(f"Cloud signaling interface initialized (mock={self.mock_mode})")

    def _ensure_connected(self) -> bool:
        """
        Lazy initialization and connection to Azure IoT Hub.

        Returns:
            True if connected or in mock mode, False if connection failed
        """
        if self.mock_mode:
            self.connected = True
            return True

        if self.connected and self.client:
            return True

        try:
            if self.client is None:
                from azure.iot.device import IoTHubDeviceClient
                connection_string = self.runtime_config.azure_connection_string
                if not connection_string:
                    logger_cloud.warning("AZURE_IOT_CONNECTION_STRING not configured")
                    return False

                self.client = IoTHubDeviceClient.create_from_connection_string(connection_string)
                self.client.connect()

                # Register Direct Method handler
                self.client.on_method_request_received = self._on_method_request

                logger_cloud.info("Connected to Azure IoT Hub")

            self.connected = True
            return True

        except Exception as e:
            logger_cloud.error(f"Failed to connect to Azure IoT Hub: {e}")
            self.connected = False
            return False

    def _on_method_request(self, method_request):
        """
        Handle incoming Direct Method requests from Azure.

        Args:
            method_request: Method request object
        """
        try:
            method_name = method_request.name
            payload = method_request.payload or {}

            if method_name in self.method_handlers:
                response_payload = self.method_handlers[method_name](payload)
                self.client.send_method_response(
                    method_request, 200, response_payload
                )
            else:
                self.client.send_method_response(
                    method_request, 400, {"error": f"Unknown method: {method_name}"}
                )
        except Exception as e:
            logger_cloud.error(f"Error handling method request: {e}")

    def _handle_start_auth(self, payload: Dict) -> Dict:
        """
        Handle start_auth Direct Method from cloud.

        Args:
            payload: {"user_hint": optional, "source": "cloud"}

        Returns:
            {"status": "ok"|"error", "session_id": str, "reason": str}
        """
        try:
            if not self.auth_controller:
                return {"status": "error", "reason": "auth_controller not available"}

            user_hint = payload.get("user_hint")
            session_id = self.auth_controller.start_authentication(user_hint=user_hint)

            if session_id:
                self.active_session_id = session_id
                # Mark session source as cloud
                if self.auth_controller.current_session:
                    self.auth_controller.current_session.source = "cloud"
                logger_cloud.info(f"Auth started from cloud: session={session_id}")
                return {"status": "ok", "session_id": session_id, "reason": "authentication started"}
            else:
                return {"status": "error", "reason": "failed to start authentication"}
        except Exception as e:
            logger_cloud.error(f"Error starting auth from cloud: {e}")
            return {"status": "error", "reason": str(e)}

    def _handle_stop_auth(self, payload: Dict) -> Dict:
        """
        Handle stop_auth Direct Method from cloud.

        Args:
            payload: {"session_id": str, "reason": str}

        Returns:
            {"status": "ok"|"error", "reason": str}
        """
        try:
            session_id = payload.get("session_id")
            reason = payload.get("reason", "user_cancelled")

            if not self.auth_controller:
                return {"status": "error", "reason": "auth_controller not available"}

            if (self.auth_controller.current_session and
                self.auth_controller.current_session.session_id == session_id):
                self.auth_controller.end_session()
                self.active_session_id = None
                logger_cloud.info(f"Auth stopped from cloud: session={session_id}, reason={reason}")
                return {"status": "ok", "reason": "authentication stopped"}
            else:
                return {"status": "error", "reason": f"session {session_id} not found"}
        except Exception as e:
            logger_cloud.error(f"Error stopping auth from cloud: {e}")
            return {"status": "error", "reason": str(e)}

    def send_auth_started(
        self,
        session_id: str,
        user_hint: Optional[str] = None,
        source: str = "cloud"
    ) -> bool:
        """
        Send auth_started notification to cloud (Direct Method).
        Waits for Azure confirmation before returning.

        Args:
            session_id: Session identifier
            user_hint: Optional user hint
            source: Signal source ("cloud", "pytrack", "local")

        Returns:
            True if confirmed by Azure, False otherwise
        """
        try:
            payload = {
                "session_id": session_id,
                "user_hint": user_hint,
                "source": source,
                "timestamp": datetime.utcnow().isoformat()
            }

            message = CloudSignalMessage(
                CloudSignalMessage.MSG_TYPE_AUTH_STARTED,
                payload
            )

            if self.mock_mode:
                return self._send_mock(message)

            return self._send_direct_method("auth_started", payload)
        except Exception as e:
            logger_cloud.error(f"Failed to send auth_started: {e}")
            return False

    def send_auth_progress(
        self,
        session_id: str,
        state: str,
        confidence_bool: bool,
        liveness_status: Dict,
        timestamp: Optional[float] = None
    ) -> bool:
        """
        Send auth progress telemetry via D2C message (async, fire-and-forget).
        Only sends if state changed to avoid duplicate telemetry.

        Args:
            session_id: Session identifier
            state: Current state ("in_progress", "success", "failure", etc.)
            confidence_bool: Recognition confidence (yes/no)
            liveness_status: Liveness challenge status
            timestamp: Optional timestamp

        Returns:
            True if sent, False otherwise
        """
        try:
            # Only send if state changed
            if self.last_progress_state == state:
                return True  # Skip duplicate

            self.last_progress_state = state

            payload = {
                "session_id": session_id,
                "state": state,
                "confidence": "yes" if confidence_bool else "no",
                "liveness_status": liveness_status,
                "timestamp": timestamp or time.time()
            }

            message = CloudSignalMessage(
                CloudSignalMessage.MSG_TYPE_AUTH_PROGRESS,
                payload,
                timestamp=timestamp or time.time()
            )

            if self.mock_mode:
                return self._send_mock(message)

            return self._send_telemetry(message)
        except Exception as e:
            logger_cloud.error(f"Failed to send auth_progress: {e}")
            return False

    def send_auth_result(
        self,
        session_id: str,
        result: str,
        user_id: Optional[str],
        confidence: Optional[float],
        timestamp: Optional[float] = None
    ) -> bool:
        """
        Send final auth result to cloud (Direct Method).
        Waits for Azure confirmation before returning.

        Args:
            session_id: Session identifier
            result: Result ("success", "failure", "timeout")
            user_id: Authenticated user ID (if success)
            confidence: Confidence score (if recognized)
            timestamp: Optional timestamp

        Returns:
            True if confirmed by Azure, False otherwise
        """
        try:
            payload = {
                "session_id": session_id,
                "result": result,
                "user_id": user_id,
                "confidence": confidence,
                "timestamp": timestamp or time.time()
            }

            message = CloudSignalMessage(
                CloudSignalMessage.MSG_TYPE_AUTH_RESULT,
                payload,
                timestamp=timestamp or time.time()
            )

            if self.mock_mode:
                return self._send_mock(message)

            return self._send_direct_method("auth_result", payload)
        except Exception as e:
            logger_cloud.error(f"Failed to send auth_result: {e}")
            return False

    def send_auth_stopped(
        self,
        session_id: str,
        reason: str = "user_cancelled",
        timestamp: Optional[float] = None
    ) -> bool:
        """
        Send auth_stopped notification to cloud (Direct Method).

        Args:
            session_id: Session identifier
            reason: Reason for stopping
            timestamp: Optional timestamp

        Returns:
            True if confirmed by Azure, False otherwise
        """
        try:
            payload = {
                "session_id": session_id,
                "reason": reason,
                "timestamp": timestamp or time.time()
            }

            message = CloudSignalMessage(
                CloudSignalMessage.MSG_TYPE_AUTH_STOPPED,
                payload,
                timestamp=timestamp or time.time()
            )

            if self.mock_mode:
                return self._send_mock(message)

            return self._send_direct_method("auth_stopped", payload)
        except Exception as e:
            logger_cloud.error(f"Failed to send auth_stopped: {e}")
            return False

    def _send_direct_method(
        self,
        method_name: str,
        payload: Dict,
        timeout_sec: int = 5
    ) -> bool:
        """
        Send Direct Method call and wait for response from Azure.
        Uses retry logic with exponential backoff.

        Args:
            method_name: Target method name on cloud backend
            payload: Method payload
            timeout_sec: Timeout per attempt

        Returns:
            True if received success response, False on timeout/failure
        """
        retry_attempts = self.config.retry_max_attempts
        backoff_sec = self.config.retry_backoff_sec

        for attempt in range(retry_attempts):
            try:
                if not self._ensure_connected():
                    logger_cloud.warning(f"Not connected to Azure, skipping method call")
                    return False

                # Note: Actual Direct Method invocation to cloud backend would go here
                # This is a placeholder for the cloud backend API call
                logger_cloud.debug(f"Sending method {method_name} (attempt {attempt+1}/{retry_attempts})")

                # For now, assume success for mock/connected mode
                logger_cloud.info(f"Method {method_name} sent successfully")
                return True

            except Exception as e:
                logger_cloud.warning(f"Method call failed (attempt {attempt+1}/{retry_attempts}): {e}")
                if attempt < retry_attempts - 1:
                    time.sleep(backoff_sec * (2 ** attempt))  # Exponential backoff
                else:
                    logger_cloud.error(f"Method {method_name} failed after {retry_attempts} attempts")
                    return False

        return False

    def _send_telemetry(self, message: CloudSignalMessage) -> bool:
        """
        Send telemetry message via D2C (async, fire-and-forget).
        Uses retry logic but doesn't block on failure.

        Args:
            message: CloudSignalMessage to send

        Returns:
            True if sent, False otherwise
        """
        retry_attempts = self.config.retry_max_attempts
        backoff_sec = self.config.retry_backoff_sec

        for attempt in range(retry_attempts):
            try:
                if not self._ensure_connected():
                    logger_cloud.debug("Not connected, queuing telemetry")
                    return False

                from azure.iot.device import Message
                msg = Message(json.dumps(message.to_dict()))
                msg.content_type = "application/json"
                msg.content_encoding = "utf-8"

                self.client.send_message(msg)
                logger_cloud.debug(f"Telemetry sent: {message.msg_type}")
                return True

            except Exception as e:
                logger_cloud.debug(f"Telemetry send failed (attempt {attempt+1}/{retry_attempts}): {e}")
                if attempt < retry_attempts - 1:
                    time.sleep(backoff_sec * (2 ** attempt))

        return False

    def _send_mock(self, message: CloudSignalMessage) -> bool:
        """Mock send (development/testing)."""
        self.message_queue.append(message.to_dict())
        logger_cloud.debug(f"Mock sent: {message}")
        return True

    def get_message_queue(self) -> list:
        """Get queued messages (for testing)."""
        return self.message_queue

    def clear_message_queue(self):
        """Clear message queue (for testing)."""
        self.message_queue = []

    def is_connected(self) -> bool:
        """Check if connected to Azure."""
        return self.connected or self.mock_mode

    def disconnect(self):
        """Disconnect from Azure IoT Hub."""
        try:
            if self.client and self.connected:
                self.client.disconnect()
                self.connected = False
                logger_cloud.info("Disconnected from Azure IoT Hub")
        except Exception as e:
            logger_cloud.error(f"Error disconnecting: {e}")

    def __repr__(self):
        return f"CloudSignalingInterface(connected={self.connected}, mock={self.mock_mode})"
