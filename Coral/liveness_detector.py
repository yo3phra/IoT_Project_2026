"""
Liveness detection module - detects facial actions to prevent spoofing.
Analyzes head pose, eye state, and facial expressions using MediaPipe Tasks.
"""

import numpy as np
from typing import Optional, Tuple, List
import cv2
import time
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
    """Represents head pose estimation (yaw, pitch, roll) in degrees."""

    def __init__(self, yaw: float, pitch: float, roll: float, confidence: float = 0.8):
        """
        Args:
            yaw: Head rotation left/right (degrees). Negative = left, positive = right
            pitch: Head tilt up/down (degrees). Negative = down, positive = up
            roll: Head tilt left/right (degrees)
            confidence: Estimation confidence [0, 1]
        """
        self.yaw = yaw
        self.pitch = pitch
        self.roll = roll
        self.confidence = confidence

    def is_looking_left(self, threshold_deg: int = 15) -> bool:
        """Check if head is turned left."""
        return self.yaw < -threshold_deg

    def is_looking_right(self, threshold_deg: int = 15) -> bool:
        """Check if head is turned right."""
        return self.yaw > threshold_deg

    def is_looking_up(self, threshold_deg: int = 10) -> bool:
        """Check if head is tilted up."""
        return self.pitch > threshold_deg

    def is_looking_down(self, threshold_deg: int = 10) -> bool:
        """Check if head is tilted down."""
        return self.pitch < -threshold_deg

    def __repr__(self):
        return f"HeadPose(yaw={self.yaw:.1f}°, pitch={self.pitch:.1f}°, roll={self.roll:.1f}°, conf={self.confidence:.2f})"


class LivenessDetector:
    """
    Facial liveness detection using MediaPipe Tasks.
    Detects facial actions and head pose to prevent spoofing attacks.
    Supports challenges: blink detection, head pose estimation, mouth opening.
    """

    def __init__(self):
        """Initialize liveness detector with MediaPipe Tasks."""
        self.config = get_config().liveness
        self.face_landmarker = None
        self._load_mediapipe()
        self.action_history: List[FacialAction] = []
        self._init_3d_model_points()

    def _init_3d_model_points(self):
        """Initialize 3D reference face model for PnP pose estimation."""
        # Approximate 3D coordinates of key face landmarks
        self.model_points = np.array([
            (0.0, 0.0, 0.0),           # 0: Nose tip
            (-30.0, -30.0, -30.0),     # 33: Left eye outer
            (30.0, -30.0, -30.0),      # 263: Right eye outer
            (-30.0, -30.0, -30.0),     # 133: Left eye inner
            (30.0, -30.0, -30.0),      # 362: Right eye inner
            (0.0, -50.0, -50.0),       # 10: Forehead center
            (0.0, 50.0, 50.0),         # 152: Chin
            (-20.0, 30.0, 20.0),       # 61: Left mouth corner
            (20.0, 30.0, 20.0),        # 291: Right mouth corner
        ], dtype=np.float32)

    def _load_mediapipe(self):
        """Load MediaPipe Tasks FaceLandmarker model."""
        try:
            from mediapipe.tasks.python import vision
            from mediapipe.tasks.python.core.base_options import BaseOptions
            from mediapipe.tasks.python.vision.core.vision_task_running_mode import VisionTaskRunningMode

            options = vision.FaceLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=self.config.model_dir + "/face_landmarker.task"),
                running_mode=VisionTaskRunningMode.VIDEO,
                num_faces=1,
                min_face_detection_confidence=0.5
            )

            self.face_landmarker = vision.FaceLandmarker.create_from_options(options)
            logger_liveness.info("MediaPipe Tasks FaceLandmarker loaded successfully")

        except Exception as e:
            logger_liveness.error(f"Failed to load MediaPipe Tasks: {e}")
            raise LivenessError(f"MediaPipe initialization failed: {e}")

    def _get_mediapipe_image(self, rgb_frame: np.ndarray):
        """
        Convert numpy RGB array to MediaPipe Image format.
        Handles multiple MediaPipe API versions.
        """
        try:
            # Try MediaPipe 0.10.x+ format
            from mediapipe import Image, ImageFormat
            img = Image(image_format=ImageFormat.SRGB, data=rgb_frame)
            logger_liveness.debug("Using MediaPipe Image.ImageFormat")
            return img
        except (ImportError, AttributeError) as e1:
            logger_liveness.debug(f"ImageFormat import failed: {e1}")
            try:
                # Try alternative: ImageFormat from vision module
                from mediapipe.tasks.python.vision import ImageFormat
                from mediapipe import Image
                img = Image(image_format=ImageFormat.SRGB, data=rgb_frame)
                logger_liveness.debug("Using vision.ImageFormat")
                return img
            except (ImportError, AttributeError) as e2:
                logger_liveness.debug(f"vision.ImageFormat import failed: {e2}")
                try:
                    # Fallback: numpy array directly (some versions support this)
                    logger_liveness.debug("Returning numpy array directly for detection")
                    return rgb_frame.astype(np.uint8)
                except Exception as e3:
                    logger_liveness.error(f"All Image format attempts failed: {e1}, {e2}, {e3}")
                    raise

    def _detect_landmarks(self, face_frame: np.ndarray):
        """
        Detect face landmarks using MediaPipe Tasks.

        Args:
            face_frame: BGR image of face

        Returns:
            List of landmarks or None if detection fails
        """
        try:
            rgb_frame = cv2.cvtColor(face_frame, cv2.COLOR_BGR2RGB)
            rgb_frame = rgb_frame.astype(np.uint8)  # Ensure uint8

            # Get MediaPipe image (handles multiple API versions)
            mp_image = self._get_mediapipe_image(rgb_frame)

            # If returned as numpy, wrap it
            if isinstance(mp_image, np.ndarray):
                try:
                    from mediapipe import Image, ImageFormat
                    mp_image = Image(image_format=ImageFormat.SRGB, data=mp_image)
                except Exception:
                    # Some versions of detect_for_video accept numpy directly
                    logger_liveness.debug("Passing numpy array directly to detect_for_video")

            timestamp_ms = int(time.time() * 1000)
            results = self.face_landmarker.detect_for_video(mp_image, timestamp_ms)

            if not results.face_landmarks or len(results.face_landmarks) == 0:
                return None

            return results.face_landmarks[0]

        except Exception as e:
            logger_liveness.error(f"Landmark detection failed: {e}")
            return None

    def detect_head_pose(self, face_frame: np.ndarray) -> Optional[HeadPose]:
        """
        Estimate 3D head pose using PnP algorithm.

        Args:
            face_frame: Cropped face region (BGR)

        Returns:
            HeadPose with yaw, pitch, roll (degrees) or None if detection fails
        """
        try:
            landmarks = self._detect_landmarks(face_frame)
            if landmarks is None:
                return None

            h, w = face_frame.shape[:2]
            pose_landmark_indices = [0, 33, 263, 133, 362, 10, 152, 61, 291]

            # Get 2D image points
            image_points = []
            for idx in pose_landmark_indices:
                if idx < len(landmarks):
                    lm = landmarks[idx]
                    image_points.append([lm.x * w, lm.y * h])

            image_points = np.array(image_points, dtype=np.float32)

            if len(image_points) < 6:
                return None

            # Camera matrix (assume default intrinsics)
            focal_length = w
            center = (w / 2, h / 2)
            camera_matrix = np.array([
                [focal_length, 0, center[0]],
                [0, focal_length, center[1]],
                [0, 0, 1]
            ], dtype=np.float32)

            dist_coeffs = np.zeros((4, 1))

            # Solve PnP for pose estimation
            success, rotation_vec, _ = cv2.solvePnP(
                self.model_points,
                image_points,
                camera_matrix,
                dist_coeffs
            )

            if not success:
                return None

            # Convert rotation vector to Euler angles
            rotation_mat, _ = cv2.Rodrigues(rotation_vec)
            yaw, pitch, roll = self._rotation_matrix_to_euler_angles(rotation_mat)

            return HeadPose(
                yaw=np.degrees(yaw),
                pitch=np.degrees(pitch),
                roll=np.degrees(roll),
                confidence=0.9
            )

        except Exception as e:
            logger_liveness.error(f"Head pose estimation failed: {e}")
            return None

    @staticmethod
    def _rotation_matrix_to_euler_angles(rotation_matrix):
        """Convert 3x3 rotation matrix to Euler angles (yaw, pitch, roll)."""
        pitch = np.arcsin(-rotation_matrix[2, 0])
        yaw = np.arctan2(rotation_matrix[1, 0], rotation_matrix[0, 0])
        roll = np.arctan2(rotation_matrix[2, 1], rotation_matrix[2, 2])
        return yaw, pitch, roll

    def detect_blink(self, face_frame: np.ndarray) -> Tuple[bool, float]:
        """
        Detect eye blink using Eye Aspect Ratio (EAR).

        Args:
            face_frame: Cropped face region (BGR)

        Returns:
            Tuple of (is_blink, confidence)
        """
        try:
            landmarks = self._detect_landmarks(face_frame)
            if landmarks is None:
                return False, 0.0

            h, w = face_frame.shape[:2]

            def calculate_ear(eye_indices):
                """Calculate Eye Aspect Ratio: (||P2-P6|| + ||P3-P5||) / (2 * ||P1-P4||)"""
                if len(eye_indices) < 6:
                    return 0.0

                points = []
                for idx in eye_indices:
                    if idx >= len(landmarks):
                        return 0.0
                    lm = landmarks[idx]
                    points.append(np.array([lm.x * w, lm.y * h]))

                outer_corner = points[0]
                upper_lid_1 = points[1]
                upper_lid_2 = points[2]
                inner_corner = points[3]
                lower_lid_1 = points[4]
                lower_lid_2 = points[5]

                vertical_1 = np.linalg.norm(upper_lid_1 - lower_lid_2)
                vertical_2 = np.linalg.norm(upper_lid_2 - lower_lid_1)
                horizontal = np.linalg.norm(outer_corner - inner_corner)

                return (vertical_1 + vertical_2) / (2.0 * horizontal) if horizontal > 1.0 else 0.0

            # Eye landmarks: [outer, upper1, upper2, inner, lower1, lower2]
            right_ear = calculate_ear([263, 387, 388, 362, 374, 373])
            left_ear = calculate_ear([33, 160, 161, 133, 145, 144])
            avg_ear = (right_ear + left_ear) / 2.0

            ear_threshold = 0.10
            is_blink = avg_ear < ear_threshold
            confidence = max(0.0, 1.0 - (avg_ear / ear_threshold)) if is_blink else 0.0

            logger_liveness.debug(
                f"Blink: L_EAR={left_ear:.3f}, R_EAR={right_ear:.3f}, "
                f"avg={avg_ear:.3f}, is_blink={is_blink}, conf={confidence:.2f}"
            )

            return is_blink, min(1.0, max(0.0, confidence))

        except Exception as e:
            logger_liveness.error(f"Blink detection failed: {e}")
            return False, 0.0

    def detect_mouth_opening(self, face_frame: np.ndarray) -> Tuple[bool, float]:
        """
        Detect mouth opening using Mouth Aspect Ratio (MAR).

        Args:
            face_frame: Cropped face region (BGR)

        Returns:
            Tuple of (is_open, confidence)
        """
        try:
            landmarks = self._detect_landmarks(face_frame)
            if landmarks is None:
                return False, 0.0

            h, w = face_frame.shape[:2]

            # Mouth landmarks for MAR calculation
            mouth_indices = {
                'upper_left': 78,
                'upper_center': 80,
                'upper_right': 82,
                'lower_left': 95,
                'lower_center': 87,
                'lower_right': 86,
            }

            mouth_points = {}
            for name, idx in mouth_indices.items():
                if idx >= len(landmarks):
                    return False, 0.0
                lm = landmarks[idx]
                mouth_points[name] = np.array([lm.x * w, lm.y * h])

            # Calculate Mouth Aspect Ratio: vertical_distance / horizontal_distance
            vertical_dist = (
                np.linalg.norm(mouth_points['upper_center'] - mouth_points['lower_center']) +
                np.linalg.norm(mouth_points['upper_left'] - mouth_points['lower_left']) +
                np.linalg.norm(mouth_points['upper_right'] - mouth_points['lower_right'])
            ) / 3.0

            horizontal_dist = np.linalg.norm(
                mouth_points['upper_right'] - mouth_points['upper_left']
            )

            if horizontal_dist < 1.0:
                return False, 0.0

            mar = vertical_dist / horizontal_dist
            mar_threshold = 0.5
            is_open = mar > mar_threshold
            confidence = min(1.0, mar / (mar_threshold * 2)) if is_open else 0.0

            logger_liveness.debug(f"Mouth: MAR={mar:.3f}, is_open={is_open}, conf={confidence:.2f}")

            return is_open, confidence

        except Exception as e:
            logger_liveness.error(f"Mouth detection failed: {e}")
            return False, 0.0

    def validate_pose_movement(
        self,
        pose_sequence: List[HeadPose],
        required_movement: int = 15
    ) -> bool:
        """
        Validate that head moved significantly between poses.

        Args:
            pose_sequence: Sequence of head poses
            required_movement: Minimum degree movement required

        Returns:
            True if sufficient movement detected
        """
        if len(pose_sequence) < 2:
            return False

        start_pose = pose_sequence[0]
        end_pose = pose_sequence[-1]

        yaw_movement = abs(end_pose.yaw - start_pose.yaw)
        pitch_movement = abs(end_pose.pitch - start_pose.pitch)
        total_movement = max(yaw_movement, pitch_movement)

        result = total_movement > required_movement
        logger_liveness.debug(
            f"Pose movement: yaw={yaw_movement:.1f}°, pitch={pitch_movement:.1f}°, "
            f"total={total_movement:.1f}°, required={required_movement}°, valid={result}"
        )

        return result

    def reset_history(self):
        """Clear action history."""
        self.action_history = []
