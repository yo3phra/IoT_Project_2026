"""
Camera interface - USB camera I/O with OpenCV.
Abstracts camera operations for face recognition pipeline.
"""

import threading
from queue import Queue
from typing import Optional, Tuple
import os
import sys

# Add Coral directory to path if running from parent
coral_dir = os.path.join(os.path.dirname(__file__))
if coral_dir not in sys.path:
    sys.path.insert(0, coral_dir)

try:
    import numpy as np
except ImportError:
    np = None

try:
    import cv2
except ImportError:
    cv2 = None

from config import get_config
from logger import logger_cam
from errors import CameraError


class CameraInterface:
    """
    USB camera interface for capture and frame processing.
    Thread-safe frame buffering.
    """

    def __init__(self):
        """Initialize camera interface."""
        self.config = get_config().camera
        self.cap: Optional[cv2.VideoCapture] = None
        self.frame_buffer: Queue = Queue(maxsize=2)
        self.is_running = False
        self.capture_thread: Optional[threading.Thread] = None
        self._frame_count = 0

    def open(self) -> bool:
        """
        Open camera connection.

        Returns:
            True if successful, False otherwise.
        """
        if cv2 is None:
            logger_cam.warning("OpenCV (cv2) not available. Camera disabled. Use --mock mode for testing.")
            raise CameraError("OpenCV not installed. Install with: pip install opencv-python")

        try:
            self.cap = cv2.VideoCapture(self.config.device_id)
            if not self.cap.isOpened():
                raise CameraError(f"Cannot open camera device {self.config.device_id}")

            # Set camera properties
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
            self.cap.set(cv2.CAP_PROP_FPS, self.config.fps)

            # Verify settings
            actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = int(self.cap.get(cv2.CAP_PROP_FPS))

            logger_cam.info(
                f"Camera opened: {actual_width}x{actual_height} @ {actual_fps} FPS"
            )

            return True

        except Exception as e:
            logger_cam.error(f"Failed to open camera: {e}")
            raise CameraError(f"Camera open failed: {e}")

    def start_capture(self):
        """Start background frame capture thread."""
        if self.is_running:
            logger_cam.warning("Capture already running")
            return

        if not self.cap or not self.cap.isOpened():
            raise CameraError("Camera not opened. Call open() first.")

        self.is_running = True
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()
        logger_cam.info("Frame capture started")

    def _capture_loop(self):
        """Background thread loop for continuous frame capture."""
        while self.is_running:
            ret, frame = self.cap.read()
            if not ret:
                logger_cam.error("Failed to read frame from camera")
                break

            # Drop frame if buffer full (keep only latest)
            if not self.frame_buffer.full():
                self.frame_buffer.put((frame, self._frame_count))
                self._frame_count += 1

    def get_frame(self, timeout_sec: float = 1.0) -> Tuple[np.ndarray, int]:
        """
        Get latest captured frame.

        Args:
            timeout_sec: Timeout for frame retrieval

        Returns:
            Tuple of (frame, frame_count)

        Raises:
            CameraError: If no frame available within timeout
        """
        try:
            frame, frame_id = self.frame_buffer.get(timeout=timeout_sec)
            return frame, frame_id
        except Exception:
            raise CameraError(f"No frame available (timeout: {timeout_sec}s)")

    def get_frame_no_wait(self) -> Optional[Tuple[np.ndarray, int]]:
        """
        Try to get latest frame without blocking.

        Returns:
            Tuple of (frame, frame_count) or None if no frame available
        """
        if not self.frame_buffer.empty():
            try:
                frame, frame_id = self.frame_buffer.get_nowait()
                return frame, frame_id
            except Exception:
                return None
        return None

    def capture_single_frame(self) -> np.ndarray:
        """
        Capture single frame synchronously (used for snapshots).

        Returns:
            Frame as numpy array

        Raises:
            CameraError: If capture fails
        """
        if not self.cap or not self.cap.isOpened():
            raise CameraError("Camera not opened")

        ret, frame = self.cap.read()
        if not ret:
            raise CameraError("Failed to capture frame")

        return frame

    def stop_capture(self):
        """Stop background frame capture."""
        if self.is_running:
            self.is_running = False
            if self.capture_thread:
                self.capture_thread.join(timeout=2.0)
            logger_cam.info("Frame capture stopped")

    def close(self):
        """Close camera connection."""
        self.stop_capture()
        if self.cap:
            self.cap.release()
            logger_cam.info("Camera closed")

    def __enter__(self):
        """Context manager entry."""
        self.open()
        self.start_capture()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()


class MockCamera:
    """
    Mock camera for testing without hardware.
    Generates synthetic frames.
    """

    def __init__(self, width: int = 720, height: int = 480):
        """Initialize mock camera."""
        self.width = width
        self.height = height
        self._frame_count = 0

    def open(self) -> bool:
        """Mock open (no-op)."""
        return True

    def start_capture(self):
        """Mock start capture (no-op)."""
        pass

    def stop_capture(self):
        """Mock stop capture (no-op)."""
        pass

    def get_frame(self, timeout_sec: float = 1.0) -> Tuple[np.ndarray, int]:
        """Generate mock frame."""
        # Black frame with some noise
        frame = np.random.randint(0, 50, (self.height, self.width, 3), dtype=np.uint8)
        frame_id = self._frame_count
        self._frame_count += 1
        return frame, frame_id

    def get_frame_no_wait(self) -> Optional[Tuple[np.ndarray, int]]:
        """Get mock frame without wait."""
        return self.get_frame(timeout_sec=0)

    def close(self):
        """Mock close (no-op)."""
        pass


def get_camera(mock: bool = False):
    """
    Factory function to get camera interface.

    Args:
        mock: Use mock camera if True

    Returns:
        CameraInterface or MockCamera instance
    """
    if mock:
        config = get_config()
        return MockCamera(config.camera.width, config.camera.height)
    return CameraInterface()
