"""
Camera display utilities - show live preview during enrollment/auth.
Draws overlays: face boxes, instructions, progress.
Handles headless/SSH environments gracefully.
"""

import cv2
import numpy as np
import os
from typing import Optional, Tuple, List


class CameraDisplay:
    """Display live camera feed with overlays."""

    def __init__(self, width: int = 720, height: int = 480):
        """Init display."""
        self.width = width
        self.height = height
        self.window_name = "Coral Face Recognition - Admin"
        self.closed = False
        self.has_display = self._check_display()

    def _check_display(self) -> bool:
        """Check if display server available."""
        if cv2 is None:
            return False

        # Check for DISPLAY env var (Linux)
        if os.environ.get('DISPLAY'):
            return True

        # Try creating test window
        try:
            cv2.namedWindow('_test_', cv2.WINDOW_NORMAL)
            cv2.destroyWindow('_test_')
            return True
        except:
            return False

    def show_frame(
        self,
        frame: np.ndarray,
        title: str = "",
        text_lines: List[str] = None,
        faces: List = None,
        progress_text: str = None,
        debug_text: str = None
    ):
        """Display frame with overlays. Silent fallback if no display.

        Args:
            debug_text: Additional debug info (model output, distances, etc)
        """
        if cv2 is None or not self.has_display:
            self._show_text_status(title, text_lines, progress_text)
            return

        display_frame = frame.copy()

        # Title bar
        if title:
            cv2.rectangle(display_frame, (0, 0), (self.width, 50), (40, 40, 40), -1)
            cv2.putText(
                display_frame, title, (20, 35),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 200, 0), 2
            )

        # Face bboxes
        if faces:
            for face in faces:
                x1, y1, x2, y2 = face.bbox
                cv2.rectangle(display_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                conf_text = f"{face.confidence:.1%}"
                cv2.putText(
                    display_frame, conf_text, (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2
                )

        # Instructions text
        if text_lines:
            y_offset = self.height - len(text_lines) * 35 - 20
            cv2.rectangle(
                display_frame, (0, y_offset - 10),
                (self.width, self.height), (40, 40, 40), -1
            )
            for i, line in enumerate(text_lines):
                y = y_offset + i * 35
                cv2.putText(
                    display_frame, line, (20, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 1
                )

        # Progress bar
        if progress_text:
            bar_y = 60
            cv2.rectangle(display_frame, (20, bar_y), (700, bar_y + 30), (100, 100, 100), -1)
            cv2.putText(
                display_frame, progress_text, (30, bar_y + 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2
            )

        # Debug text (bottom right)
        if debug_text:
            debug_lines = debug_text.split('\n')
            y_start = self.height - len(debug_lines) * 25 - 10
            for i, line in enumerate(debug_lines):
                y = y_start + i * 25
                cv2.putText(
                    display_frame, line, (self.width - 300, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 200, 100), 1
                )

        # Display
        try:
            cv2.imshow(self.window_name, display_frame)
            if cv2.getWindowProperty(self.window_name, cv2.WND_PROP_VISIBLE) < 1:
                self.closed = True
        except:
            pass

    def _show_text_status(self, title: str, text_lines: List[str], progress: str):
        """Fallback text display for headless."""
        # Only print periodically to avoid spam
        if not hasattr(self, '_last_print_time'):
            self._last_print_time = 0

        import time
        now = time.time()
        if now - self._last_print_time < 1.0:  # Print every 1s max
            return

        self._last_print_time = now

        # Print status line
        parts = []
        if title:
            parts.append(title)
        if progress:
            parts.append(progress)
        if text_lines:
            parts.append(" | ".join(text_lines[:2]))

        if parts:
            print(f"\r[Status] {' -> '.join(parts):<80}", end="", flush=True)

    def wait_key(self, ms: int = 1) -> Optional[int]:
        """Wait for key press (or timeout)."""
        if cv2 is None or not self.has_display:
            return None

        try:
            key = cv2.waitKey(ms)
            if key == 27:  # ESC
                self.closed = True
            return key if key > 0 else None
        except:
            return None

    def close(self):
        """Close display window."""
        if cv2 is None or not self.has_display:
            print()  # Newline after status
            return

        try:
            cv2.destroyWindow(self.window_name)
        except:
            pass

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, *args):
        """Context manager exit."""
        self.close()

