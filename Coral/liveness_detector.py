"""
Liveness detection module - detects facial actions to prevent spoofing.
Analyzes head pose, eye state, and facial expressions.
"""

import numpy as np
from typing import Dict, Optional, Tuple, List
import cv2
from config import get_config
from logger import logger_liveness
from errors import LivenessError


class FacialAction:
    """Represents a detected facial action."""

    BLINK = "blink"
    HEAD_LEFT = "head_left"
    HEAD_RIGHT = "head_right"
    HEAD_UP = "head_up"
    HEAD_DOWN = "head_down"
    MOUTH_OPEN = "mouth_open"

    def __init__(self, action_type: str, confidence: float, frame_id: int):
        """
        Args:
            action_type: Type of facial action
            confidence: Detection confidence [0, 1]
            frame_id: Frame where detected
        """
        self.type = action_type
        self.confidence = confidence
        self.frame_id = frame_id


class HeadPose:
    """Represents head pose estimation (yaw, pitch, roll)."""

    def __init__(self, yaw: float, pitch: float, roll: float, confidence: float = 0.5):
        """
        Args:
            yaw: Head rotation left/right (degrees)
            pitch: Head tilt up/down (degrees)
            roll: Head tilt l/r (degrees)
            confidence: Estimation confidence
        """
        self.yaw = yaw  # Negative = left, positive = right
        self.pitch = pitch  # Negative = down, positive = up
        self.roll = roll
        self.confidence = confidence

    def is_looking_left(self, threshold_deg: int = 15) -> bool:
        """Check if head is turned left."""
        return self.yaw < -threshold_deg

    def is_looking_right(self, threshold_deg: int = 15) -> bool:
        """Check if head is turned right."""
        return self.yaw > threshold_deg

    def __repr__(self):
        return f"HeadPose(yaw={self.yaw:.1f}°, pitch={self.pitch:.1f}°, roll={self.roll:.1f}°)"


class LivenessDetector:
    """
    Facial liveness detection - detects facial actions and head pose.
    Based on dlib and mediapipe (fallback to handcrafted features).
    """

    def __init__(self):
        """Initialize liveness detector."""
        self.config = get_config().liveness
        self._load_models()
        self.action_history: List[FacialAction] = []

    def _load_models(self):
        """Load liveness detection models (dlib/mediapipe)."""
        self._try_load_mediapipe()

    def _try_load_mediapipe(self):
        """Try to load MediaPipe for robust facial actions."""
        try:
            import mediapipe as mp
            self.mp_face_mesh = mp.solutions.face_mesh
            self.mp_drawing = mp.solutions.drawing_utils
            self.face_mesh = self.mp_face_mesh.FaceMesh(
                static_image_mode=False,
                max_num_faces=1,
                min_detection_confidence=0.5
            )
            logger_liveness.info("MediaPipe face mesh loaded")
            self.use_mediapipe = True
        except ImportError:
            logger_liveness.warning("MediaPipe not available. Using handcrafted features.")
            self.use_mediapipe = False

    def detect_head_pose(self, face_frame: np.ndarray) -> Optional[HeadPose]:
        """
        Estimate head pose from face image.

        Args:
            face_frame: Cropped face region

        Returns:
            HeadPose object or None if detection fails
        """
        if self.use_mediapipe:
            return self._detect_head_pose_mediapipe(face_frame)
        else:
            return self._detect_head_pose_handcrafted(face_frame)

    def _detect_head_pose_mediapipe(self, face_frame: np.ndarray) -> Optional[HeadPose]:
        """Detect head pose using MediaPipe."""
        try:
            # Convert BGR to RGB
            rgb_frame = cv2.cvtColor(face_frame, cv2.COLOR_BGR2RGB)
            results = self.face_mesh.process(rgb_frame)

            if not results.multi_face_landmarks:
                return None

            landmarks = results.multi_face_landmarks[0]

            # Extract key landmark indices for pose estimation
            # Nose tip (1), forehead (10), chin (152)
            pose_landmarks = [1, 33, 263, 10, 152]
            points = []

            for idx in pose_landmarks:
                if idx < len(landmarks.landmark):
                    lm = landmarks.landmark[idx]
                    h, w = face_frame.shape[:2]
                    x = int(lm.x * w)
                    y = int(lm.y * h)
                    points.append((x, y))

            if len(points) < 5:
                return None

            # Simplified pose estimation: compare x positions
            nose_x, forehead_x = points[0][0], points[3][0]
            left_cheek_x, right_cheek_x = points[1][0], points[2][0]

            # Estimate yaw (left/right head turn)
            yaw = (nose_x - forehead_x) * 2
            pitch = (points[4][1] - forehead_x) * 0.5  # Simplified
            roll = np.arctan2(points[1][1] - points[2][1], points[2][0] - points[1][0])
            roll = np.degrees(roll)

            return HeadPose(float(yaw), float(pitch), float(roll), confidence=0.8)

        except Exception as e:
            logger_liveness.error(f"MediaPipe head pose detection failed: {e}")
            return None

    def _detect_head_pose_handcrafted(self, face_frame: np.ndarray) -> Optional[HeadPose]:
        """Fallback: handcrafted head pose estimation."""
        try:
            # Use eye aspect ratio and face width change to estimate pose
            h, w = face_frame.shape[:2]

            # Find edges
            gray = cv2.cvtColor(face_frame, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, 50, 150)

            # Estimate yaw from face contour asymmetry
            left_edge_density = np.sum(edges[:, :w//2]) / (h * w//2)
            right_edge_density = np.sum(edges[:, w//2:]) / (h * w//2)

            yaw = (right_edge_density - left_edge_density) * 45.0

            # Pitch: estimate from face height change
            top_half_density = np.sum(edges[:h//2, :]) / (h//2 * w)
            bottom_half_density = np.sum(edges[h//2:, :]) / (h//2 * w)
            pitch = (bottom_half_density - top_half_density) * 30.0

            return HeadPose(yaw, pitch, 0.0, confidence=0.5)

        except Exception as e:
            logger_liveness.error(f"Handcrafted pose estimation failed: {e}")
            return None

    def detect_blink(self, face_frame: np.ndarray) -> Tuple[bool, float]:
        """
        Detect if eyes are closed (blink).

        Args:
            face_frame: Cropped face region

        Returns:
            Tuple of (is_blink, confidence)
        """
        if self.use_mediapipe:
            return self._detect_blink_mediapipe(face_frame)
        else:
            return self._detect_blink_handcrafted(face_frame)

    def _detect_blink_mediapipe(self, face_frame: np.ndarray) -> Tuple[bool, float]:
        """Detect blink using MediaPipe eye landmarks."""
        try:
            rgb_frame = cv2.cvtColor(face_frame, cv2.COLOR_BGR2RGB)
            results = self.face_mesh.process(rgb_frame)

            if not results.multi_face_landmarks:
                return False, 0.0

            landmarks = results.multi_face_landmarks[0]

            # Eye landmarks
            left_eye = [landmarks.landmark[i] for i in [33, 133]]
            right_eye = [landmarks.landmark[i] for i in [362, 263]]

            h, w = face_frame.shape[:2]

            # Calculate eye aspect ratio
            def eye_aspect_ratio(eye_points):
                p1 = np.array([eye_points[0].x * w, eye_points[0].y * h])
                p2 = np.array([eye_points[1].x * w, eye_points[1].y * h])
                dist = np.linalg.norm(p2 - p1)
                return dist

            left_ear = eye_aspect_ratio(left_eye)
            right_ear = eye_aspect_ratio(right_eye)
            avg_ear = (left_ear + right_ear) / 2

            # Threshold for blink
            is_blink = avg_ear < 2.0
            confidence = 1.0 - (avg_ear / 10.0) if is_blink else avg_ear / 10.0

            return is_blink, min(1.0, max(0.0, confidence))

        except Exception as e:
            logger_liveness.error(f"MediaPipe blink detection failed: {e}")
            return False, 0.0

    def _detect_blink_handcrafted(self, face_frame: np.ndarray) -> Tuple[bool, float]:
        """Fallback: handcrafted blink detection via brightness."""
        try:
            gray = cv2.cvtColor(face_frame, cv2.COLOR_BGR2GRAY)

            # Eyes are typically darker, so brightness indicates closed eyes
            upper_half = gray[: gray.shape[0] // 3, :]
            brightness = np.mean(upper_half)

            # Threshold: low brightness suggests closed eyes
            is_blink = brightness < 50
            confidence = max(0.3, 1.0 - brightness / 100.0) if is_blink else 0.3

            return is_blink, confidence

        except Exception as e:
            logger_liveness.error(f"Handcrafted blink detection failed: {e}")
            return False, 0.0

    def detect_mouth_movement(self, face_frame: np.ndarray) -> Tuple[bool, float]:
        """
        Detect mouth opening/movement.

        Args:
            face_frame: Cropped face region

        Returns:
            Tuple of (is_open, confidence)
        """
        if not self.use_mediapipe:
            return False, 0.0

        try:
            rgb_frame = cv2.cvtColor(face_frame, cv2.COLOR_BGR2RGB)
            results = self.face_mesh.process(rgb_frame)

            if not results.multi_face_landmarks:
                return False, 0.0

            landmarks = results.multi_face_landmarks[0]

            # Mouth landmarks
            mouth_open = [landmarks.landmark[i] for i in [13, 14]]  # Top and bottom

            h = face_frame.shape[0]
            p1_y = mouth_open[0].y * h
            p2_y = mouth_open[1].y * h

            mouth_dist = abs(p2_y - p1_y)
            is_open = mouth_dist > 5  # Threshold
            confidence = min(1.0, mouth_dist / 15.0)

            return is_open, confidence

        except Exception as e:
            logger_liveness.error(f"Mouth movement detection failed: {e}")
            return False, 0.0

    def validate_pose_movement(
        self,
        pose_sequence: List[HeadPose],
        required_movement: int = 15
    ) -> bool:
        """
        Validate that head moved significantly between poses.

        Args:
            pose_sequence: Sequence of head poses from frames
            required_movement: Minimum degree movement required

        Returns:
            True if sufficient movement detected
        """
        if len(pose_sequence) < 2:
            return False

        start_pose = pose_sequence[0]
        end_pose = pose_sequence[-1]

        yaw_movement = abs(end_pose.yaw - start_pose.yaw)

        result = yaw_movement > required_movement
        logger_liveness.debug(
            f"Pose movement: yaw={yaw_movement:.1f}°, required={required_movement}°, valid={result}"
        )

        return result

    def reset_history(self):
        """Clear action history."""
        self.action_history = []
