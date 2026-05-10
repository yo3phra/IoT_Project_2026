"""
Coral to Cloud telemetry interface.
One-way data send: auth progress, results. No commands received from cloud.
"""

import time
import json
from typing import Optional, Dict
from dataclasses import dataclass

from config import get_config
from logger import logger_cloud


@dataclass
class CloudTelemetry:
    """Telemetry message for cloud."""
    msg_type: str  # "auth_started", "auth_progress", "auth_result"
    payload: Dict
    timestamp: float = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()

    def to_dict(self) -> Dict:
        return {
            "type": self.msg_type,
            "timestamp": self.timestamp,
            "payload": self.payload
        }


class CloudInterface:
    """
    Coral → Cloud telemetry sender.
    One-way only: Coral sends auth data, no commands received.
    """

    def __init__(self, mock_mode: bool = False):
        """
        Initialize cloud interface.

        Args:
            mock_mode: Use mock mode for development (queues locally)
        """
        self.config = get_config().cloud
        self.runtime_config = get_config()
        self.mock_mode = mock_mode or self.runtime_config.is_windows

        self.connected = False
        self.message_queue = []  # For testing

        logger_cloud.info(f"Cloud interface initialized (mock={self.mock_mode})")

    def _ensure_connection(self) -> bool:
        """Lazy connection to Azure IoT Hub."""
        if self.mock_mode:
            self.connected = True
            return True

        if self.connected:
            return True

        try:
            from azure.iot.device import IoTHubDeviceClient
            connection_string = self.runtime_config.azure_connection_string
            if not connection_string:
                logger_cloud.warning("AZURE_IOT_CONNECTION_STRING not set")
                return False

            self.client = IoTHubDeviceClient.create_from_connection_string(connection_string)
            self.client.connect()
            self.connected = True
            logger_cloud.info("Connected to Azure IoT Hub")
            return True

        except Exception as e:
            logger_cloud.error(f"Connection failed: {e}")
            return False

    def send_auth_started(self, session_id: str, timestamp: Optional[float] = None) -> bool:
        """Send auth session started."""
        payload = {
            "session_id": session_id,
            "timestamp": timestamp or time.time()
        }
        return self._send_telemetry("auth_started", payload)

    def send_auth_progress(
        self,
        session_id: str,
        state: str,
        confidence_bool: bool,
        liveness_status: Dict,
        timestamp: Optional[float] = None
    ) -> bool:
        """Send auth progress update."""
        payload = {
            "session_id": session_id,
            "state": state,
            "confidence": "yes" if confidence_bool else "no",
            "liveness_status": liveness_status,
            "timestamp": timestamp or time.time()
        }
        return self._send_telemetry("auth_progress", payload)

    def send_auth_result(
        self,
        session_id: str,
        result: str,
        user_id: Optional[str],
        confidence: Optional[float],
        timestamp: Optional[float] = None
    ) -> bool:
        """Send final auth result."""
        payload = {
            "session_id": session_id,
            "result": result,
            "user_id": user_id,
            "confidence": confidence,
            "timestamp": timestamp or time.time()
        }
        return self._send_telemetry("auth_result", payload)

    def _send_telemetry(self, msg_type: str, payload: Dict) -> bool:
        """Send D2C telemetry (async, fire-and-forget)."""
        try:
            msg = CloudTelemetry(msg_type, payload)

            if self.mock_mode:
                self.message_queue.append(msg.to_dict())
                logger_cloud.debug(f"Mock queued: {msg_type}")
                return True

            if not self._ensure_connection():
                logger_cloud.debug("Not connected, skipping telemetry")
                return False

            from azure.iot.device import Message
            d2c_msg = Message(json.dumps(msg.to_dict()))
            d2c_msg.content_type = "application/json"
            d2c_msg.content_encoding = "utf-8"

            self.client.send_message(d2c_msg)
            logger_cloud.debug(f"Telemetry sent: {msg_type}")
            return True

        except Exception as e:
            logger_cloud.error(f"Telemetry send failed: {e}")
            return False

    def get_message_queue(self) -> list:
        """Get queued messages (testing)."""
        return self.message_queue

    def clear_message_queue(self):
        """Clear queue (testing)."""
        self.message_queue = []

    def __repr__(self):
        return f"CloudInterface(connected={self.connected}, mock={self.mock_mode})"
