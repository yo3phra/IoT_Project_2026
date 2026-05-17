"""
Coral to Cloud telemetry interface.
One-way data send: auth progress, results. No commands received from cloud.
"""

import time
import json
import threading
import re
from datetime import datetime, timezone
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
        # prefer explicit parameter, otherwise use config-driven mock flag
        self.mock_mode = bool(mock_mode) or bool(self.config.mock_mode)

        self.connected = False
        self.message_queue = []  # Pending queue entries: message + retry metadata
        self._queue_lock = threading.Condition()
        self._retry_worker_started = False

        logger_cloud.info(f"[INIT] Cloud interface initialized (mock={self.mock_mode}, win={self.runtime_config.is_windows})")

    def _ensure_connection(self) -> bool:
        """Lazy connection to Azure IoT Hub."""
        if self.mock_mode:
            self.connected = True
            logger_cloud.debug("Mock mode: connection assumed")
            return True

        if self.connected:
            logger_cloud.debug("Connection already established")
            return True

        try:
            from azure.iot.device import IoTHubDeviceClient
            connection_string = self.runtime_config._azure_connection_string
            print(f"Debug: Azure connection string: {connection_string}")
            if not connection_string:
                logger_cloud.warning("AZURE_IOT_CONNECTION_STRING not set - cloud telemetry disabled")
                return False

            logger_cloud.info("Attempting IoT Hub connection...")
            self.client = IoTHubDeviceClient.create_from_connection_string(connection_string)
            self.client.connect()
            self.connected = True
            logger_cloud.info("Connected to Azure IoT Hub")
            return True

        except Exception as e:
            self.connected = False
            logger_cloud.error(f"Connection failed: {type(e).__name__}: {e}", exc_info=True)
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
            logger_cloud.debug(f"→ Send telemetry: type={msg_type}, payload={payload}")

            # Telemetry verbosity control
            telemetry_level = int(get_config().cloud.telemetry_level)
            if telemetry_level == 1:
                pass  # allow all
            elif telemetry_level == 2:
                if msg_type not in ("auth_started", "auth_result"):
                    logger_cloud.debug(f"Telemetry suppressed by level=2: {msg_type}")
                    return True
            elif telemetry_level == 3:
                if msg_type != "auth_result":
                    logger_cloud.debug(f"Telemetry suppressed by level=3: {msg_type}")
                    return True

            if self.mock_mode:
                self._enqueue_message(msg, retry_after_sec=0.0, last_error=None)
                logger_cloud.debug(f"  [MOCK] Queued: {msg_type}")
                return True

            self._flush_due_messages()

            if not self._ensure_connection():
                logger_cloud.warning(f"  [NO CONN] Queuing for retry: {msg_type}")
                self._enqueue_message(
                    msg,
                    retry_after_sec=self._default_retry_delay(0),
                    last_error="Connection unavailable"
                )
                self._ensure_retry_worker()
                return False

            self._send_message(msg)
            logger_cloud.info(f"Telemetry sent: {msg_type}")
            return True

        except Exception as e:
            retry_after_sec = self._extract_retry_delay(e)
            self._enqueue_message(msg, retry_after_sec=retry_after_sec, last_error=str(e))
            self._ensure_retry_worker()
            logger_cloud.error(f"Telemetry send failed: {type(e).__name__}: {e}", exc_info=True)
            return False

    def get_message_queue(self) -> list:
        """Get queued messages (testing)."""
        with self._queue_lock:
            return [entry["message"] for entry in self.message_queue]

    def clear_message_queue(self):
        """Clear queue (testing)."""
        with self._queue_lock:
            self.message_queue = []
            self._queue_lock.notify_all()

    def __repr__(self):
        return f"CloudInterface(connected={self.connected}, mock={self.mock_mode}, queued={len(self.message_queue)})"

    def get_status(self) -> Dict:
        """Get cloud interface status for debugging."""
        return {
            "mock_mode": self.mock_mode,
            "connected": self.connected,
            "queue_length": len(self.message_queue),
            "retry_worker_active": self._retry_worker_started,
            "queued_messages": [
                {
                    "type": entry["message"]["type"],
                    "attempts": entry["attempts"],
                    "retry_in_sec": max(0.0, entry["next_attempt_at"] - time.time()),
                    "last_error": entry["last_error"]
                }
                for entry in self.message_queue
            ]
        }

    def _send_message(self, msg: CloudTelemetry):
        """Send one telemetry message through Azure IoT Hub."""
        from azure.iot.device import Message

        json_str = json.dumps(msg.to_dict())
        logger_cloud.debug(f"  [AZURE] Building D2C message: {json_str[:100]}...")
        
        d2c_msg = Message(json_str)
        d2c_msg.content_type = "application/json"
        d2c_msg.content_encoding = "utf-8"

        try:
            logger_cloud.debug(f"  [AZURE] Sending to IoT Hub...")
            self.client.send_message(d2c_msg)
            logger_cloud.info(f"  [AZURE] Message sent successfully (type={msg.msg_type})")
        except Exception as e:
            logger_cloud.error(f"  [AZURE] Send failed: {type(e).__name__}: {e}", exc_info=True)
            raise

    def _enqueue_message(self, msg: CloudTelemetry, retry_after_sec: float, last_error: Optional[str], attempts: int = 0):
        """Store a telemetry message for later delivery."""
        next_attempt_at = time.time() + max(0.0, float(retry_after_sec))
        entry = {
            "message": msg.to_dict(),
            "next_attempt_at": next_attempt_at,
            "retry_after_sec": max(0.0, float(retry_after_sec)),
            "attempts": attempts,
            "last_error": last_error,
            "created_at": time.time(),
        }

        with self._queue_lock:
            self.message_queue.append(entry)
            queue_len = len(self.message_queue)
            self._queue_lock.notify_all()

        logger_cloud.info(
            f"[QUEUE] Enqueued msg (type={msg.msg_type}, retry_in={retry_after_sec:.1f}s, "
            f"queue_len={queue_len}, error={last_error})"
        )

    def _ensure_retry_worker(self):
        """Start the retry worker once for deferred sends."""
        if self.mock_mode or self._retry_worker_started:
            return

        with self._queue_lock:
            if self._retry_worker_started:
                return
            self._retry_worker_started = True

        logger_cloud.info("[WORKER] Starting retry background worker...")
        worker = threading.Thread(target=self._retry_worker_loop, daemon=True)
        worker.start()

    def _retry_worker_loop(self):
        """Background loop that retries queued telemetry when due."""
        logger_cloud.info("[WORKER] Retry worker started (daemon)")
        
        while True:
            due_entries = self._drain_due_entries()

            if not due_entries:
                with self._queue_lock:
                    if not self.message_queue:
                        logger_cloud.debug("[WORKER] Queue empty, waiting...")
                        self._queue_lock.wait(timeout=1.0)
                        continue

                    next_due = min(entry["next_attempt_at"] for entry in self.message_queue)
                    wait_for = max(0.0, next_due - time.time())
                    logger_cloud.debug(f"[WORKER] Next retry in {wait_for:.1f}s, waiting...")
                    self._queue_lock.wait(timeout=wait_for if wait_for > 0 else 0.5)
                continue

            logger_cloud.debug(f"[WORKER] Processing {len(due_entries)} due entries")
            for entry in due_entries:
                self._deliver_queued_entry(entry)

    def _flush_due_messages(self):
        """Attempt to deliver any queued messages whose retry time has arrived."""
        if self.mock_mode:
            return

        due_entries = self._drain_due_entries()
        if due_entries:
            logger_cloud.debug(f"[FLUSH] Processing {len(due_entries)} due messages")
            for entry in due_entries:
                self._deliver_queued_entry(entry)

    def _drain_due_entries(self) -> list:
        """Remove due queue entries and return them for delivery."""
        now = time.time()
        due_entries = []

        with self._queue_lock:
            remaining = []
            for entry in self.message_queue:
                if entry["next_attempt_at"] <= now:
                    due_entries.append(entry)
                else:
                    remaining.append(entry)
            self.message_queue = remaining

        return due_entries

    def _deliver_queued_entry(self, entry: Dict):
        """Try to send one queued message and reschedule it on failure."""
        msg = CloudTelemetry(
            entry["message"]["type"],
            entry["message"]["payload"],
            entry["message"].get("timestamp")
        )
        attempts = entry.get("attempts", 0)
        logger_cloud.debug(f"[RETRY] Attempting delivery (type={msg.msg_type}, attempt={attempts + 1})")

        try:
            if not self._ensure_connection():
                raise RuntimeError("Connection unavailable")

            self._send_message(msg)
            logger_cloud.info(f"[RETRY] Queued msg delivered after {attempts} retries: {msg.msg_type}")
        except Exception as e:
            retry_max_attempts = self.runtime_config.cloud.retry_max_attempts
            entry["attempts"] = attempts + 1

            if entry["attempts"] >= retry_max_attempts:
                logger_cloud.error(
                    f"[RETRY] Dropping msg after {entry['attempts']} attempts (max={retry_max_attempts}): "
                    f"{type(e).__name__}: {e}"
                )
                return

            retry_after_sec = self._extract_retry_delay(e, fallback_attempts=entry["attempts"])
            entry["retry_after_sec"] = retry_after_sec
            entry["next_attempt_at"] = time.time() + retry_after_sec
            entry["last_error"] = str(e)

            logger_cloud.warning(
                f"[RETRY] Requeuing (type={msg.msg_type}, attempt={entry['attempts']}, "
                f"retry_in={retry_after_sec:.1f}s, error={type(e).__name__})"
            )

            with self._queue_lock:
                self.message_queue.append(entry)
                self._queue_lock.notify_all()

    def _extract_retry_delay(self, error: Exception, fallback_attempts: int = 0) -> float:
        """Extract a retry interval from Azure-style errors when available."""
        for attr in ("retry_after_seconds", "retry_after_sec", "retry_after", "retry_delay_seconds"):
            value = getattr(error, attr, None)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)

        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None)
        if headers:
            header_value = None
            if hasattr(headers, "get"):
                header_value = headers.get("retry-after") or headers.get("Retry-After")
            if header_value:
                try:
                    return float(header_value)
                except (TypeError, ValueError):
                    pass

        error_text = str(error)
        until_match = re.search(r"until:\s*([0-9T:\-.+Z]+)", error_text)
        if until_match:
            until_text = until_match.group(1).replace("Z", "+00:00")
            try:
                until_dt = datetime.fromisoformat(until_text)
                if until_dt.tzinfo is None:
                    until_dt = until_dt.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                return max(0.0, (until_dt - now).total_seconds())
            except ValueError:
                pass

        return self._default_retry_delay(fallback_attempts)

    def _default_retry_delay(self, attempts: int) -> float:
        """Compute a bounded exponential retry delay."""
        base_delay = max(1.0, float(self.runtime_config.cloud.retry_backoff_sec))
        attempts = max(0, int(attempts))
        return min(base_delay * (2 ** attempts), 300.0)
