"""
Authentication controller - main orchestration layer.
Coordinates camera, face detection, recognition, and liveness.
Exposes authentication state for PyTrack integration.
"""

import time
from typing import Optional, Dict, List, Tuple
from enum import Enum
from dataclasses import dataclass

from config import get_config
from logger import logger_auth
from errors import AuthenticationError, StatusCode

from camera_interface import CameraInterface, get_camera
from face_detector import FaceDetector, get_face_detector
from face_recognizer import FaceRecognizer, FaceEmbedding, get_face_recognizer
from liveness_detector import LivenessDetector
from challenge_manager import ChallengeManager, ChallengeSequence
from embedding_store import EmbeddingStore
from user_manager import UserManager


class AuthenticationResult(Enum):
    """Authentication result states."""
    SUCCESS = "success"
    FAILURE = "failure"
    IN_PROGRESS = "in_progress"
    TIMEOUT = "timeout"


@dataclass
class AuthenticationSession:
    """Represents an ongoing authentication session."""
    session_id: str
    user_detected_id: Optional[str]
    user_detected_confidence: float
    face_count: int
    frames_processed: int
    liveness_challenge: Optional[ChallengeSequence]
    liveness_passed: bool
    result: Optional[AuthenticationResult]
    error_code: Optional[str]
    created_at: float
    completed_at: Optional[float]

    @property
    def age_sec(self) -> float:
        """Age of session in seconds."""
        return time.time() - self.created_at

    @property
    def is_timed_out(self) -> bool:
        """Check if session has timed out."""
        config = get_config().auth_controller
        return self.age_sec > config.total_timeout_sec


class AuthenticationController:
    """
    Main authentication orchestrator.
    Manages full auth pipeline: detect → recognize → liveness → success/fail.
    """

    def __init__(
        self,
        camera: CameraInterface = None,
        embedding_store: EmbeddingStore = None,
        mock_mode: bool = False
    ):
        """
        Initialize authentication controller.

        Args:
            camera: Camera interface (None to auto-create)
            embedding_store: Embedding storage (None to auto-create)
            mock_mode: Use mock models for testing
        """
        self.config = get_config().auth_controller
        self.runtime_config = get_config()

        # Components
        self.camera = camera or get_camera(mock=mock_mode)
        self.face_detector = get_face_detector(mock=mock_mode)
        self.face_recognizer = get_face_recognizer(mock=mock_mode)
        self.liveness_detector = LivenessDetector()
        self.challenge_manager = ChallengeManager()

        # Storage
        if embedding_store is None:
            from embedding_store import EmbeddingStore
            embedding_store = EmbeddingStore()
        self.embedding_store = embedding_store
        self.user_manager = UserManager(embedding_store)

        # State
        self.current_session: Optional[AuthenticationSession] = None
        self.last_successful_auth = None
        self.auth_history: List[Dict] = []
        self.mock_mode = mock_mode

        logger_auth.info(f"Authentication controller initialized (mock={mock_mode})")

    # ========== Public API for PyTrack Integration ==========

    @property
    def last_auth_timestamp(self) -> Optional[float]:
        """Get timestamp of last successful authentication."""
        if self.last_successful_auth:
            return self.last_successful_auth.get("timestamp")
        return None

    @property
    def last_auth_user_id(self) -> Optional[str]:
        """Get user ID of last successful authentication."""
        if self.last_successful_auth:
            return self.last_successful_auth.get("user_id")
        return None

    @property
    def last_auth_confidence(self) -> float:
        """Get confidence score of last successful authentication."""
        if self.last_successful_auth:
            return self.last_successful_auth.get("confidence", 0.0)
        return 0.0

    @property
    def seconds_since_last_auth(self) -> Optional[float]:
        """Get seconds since last successful auth."""
        if timestamp := self.last_auth_timestamp:
            return time.time() - timestamp
        return None

    def is_recently_authenticated(self, timeout_sec: int = None) -> bool:
        """
        Check if user was recently authenticated.
        Used by PyTrack to determine theft alert threshold.

        Args:
            timeout_sec: Timeout threshold (None uses config default)

        Returns:
            True if authenticated within timeout
        """
        if timeout_sec is None:
            timeout_sec = get_config().pytrack.timeout_for_auth_sec

        if seconds := self.seconds_since_last_auth:
            result = seconds < timeout_sec
            logger_auth.debug(f"Recent auth check: {seconds:.1f}s < {timeout_sec}s = {result}")
            return result

        return False

    # ========== Authentication Flow ==========

    def start_authentication(self) -> str:
        """
        Start authentication session.

        Returns:
            Session ID
        """
        import uuid

        session_id = str(uuid.uuid4())
        self.current_session = AuthenticationSession(
            session_id=session_id,
            user_detected_id=None,
            user_detected_confidence=0.0,
            face_count=0,
            frames_processed=0,
            liveness_challenge=None,
            liveness_passed=False,
            result=AuthenticationResult.IN_PROGRESS,
            error_code=None,
            created_at=time.time(),
            completed_at=None
        )

        logger_auth.info(f"Authentication session started: {session_id}")
        return session_id

    def process_frame(self, frame_data=None) -> Dict:
        """
        Process one frame in authentication pipeline.

        Args:
            frame_data: Optional pre-captured frame (for testing)

        Returns:
            Status dict with current progress
        """
        if not self.current_session:
            raise AuthenticationError("No active session. Call start_authentication() first.")

        session = self.current_session

        # Check timeout
        if session.is_timed_out:
            session.result = AuthenticationResult.TIMEOUT
            session.error_code = StatusCode.AUTH_TIMEOUT
            self._finalize_session()
            return self._session_status()

        try:
            # Get frame from camera
            if frame_data is None:
                try:
                    frame, frame_id = self.camera.get_frame(timeout_sec=0.5)
                except Exception:
                    return self._session_status()
            else:
                frame, frame_id = frame_data

            session.frames_processed += 1

            # 1. DETECT
            if session.user_detected_id is None:
                faces = self.face_detector.detect(frame)

                if len(faces) == 0:
                    session.error_code = StatusCode.AUTH_FACE_NOT_DETECTED
                    return self._session_status()

                if len(faces) > 1:
                    logger_auth.warning(f"Multiple faces detected: {len(faces)}")
                    session.error_code = "AUTH_MULTIPLE_FACES"
                    session.result = AuthenticationResult.FAILURE
                    self._finalize_session()
                    return self._session_status()

                face = faces[0]
                session.face_count += 1

                # 2. RECOGNIZE
                face_crop = face.crop_from_frame(frame)
                detected_embedding = self.face_recognizer.generate_embedding(face_crop)

                # Find best match in database
                all_users = self.embedding_store.get_all_users()

                best_match_embedding = None
                best_match_user = None
                best_distance = float('inf')

                for user_id in all_users:
                    user_embeddings = self.embedding_store.get_user_embeddings(user_id)
                    for ref_embedding in user_embeddings:
                        distance = detected_embedding.distance_to(ref_embedding)
                        if distance < best_distance:
                            best_distance = distance
                            best_match_embedding = ref_embedding
                            best_match_user = user_id

                # Check confidence threshold
                if best_distance > self.runtime_config.face_recognition.distance_threshold:
                    session.error_code = StatusCode.AUTH_CONFIDENCE_TOO_LOW
                    logger_auth.debug(f"Low confidence: {1.0 - (best_distance / 2.0):.2%} < {1.0 - (self.runtime_config.face_recognition.distance_threshold / 2.0):.2%}")
                    # Don't fail immediately - keep trying frames within timeout
                    return self._session_status()

                # User recognized!
                session.user_detected_id = best_match_user
                session.user_detected_confidence = 1.0 - (best_distance / 2.0)
                logger_auth.info(f"User recognized: {best_match_user} (conf={session.user_detected_confidence:.2f})")

                # 3. START LIVENESS CHALLENGE
                session.liveness_challenge = self.challenge_manager.create_sequence(
                    best_match_user,
                    num_challenges=2
                )
                return self._session_status()

            # 4. LIVENESS CHECK
            if session.liveness_challenge and not session.liveness_passed:
                return self._process_liveness_frame(frame)

            return self._session_status()

        except Exception as e:
            logger_auth.error(f"Frame processing error: {e}")
            session.error_code = StatusCode.SYSTEM_INTERNAL_ERROR
            session.result = AuthenticationResult.FAILURE
            self._finalize_session()
            return self._session_status()

    def _process_liveness_frame(self, frame) -> Dict:
        """Process frame during liveness challenge."""
        session = self.current_session
        challenge_seq = session.liveness_challenge

        if not challenge_seq or challenge_seq.is_complete:
            return self._session_status()

        current_challenge = challenge_seq.current_challenge

        if current_challenge.is_timed_out:
            logger_auth.warning(f"Challenge timed out: {current_challenge.type.value}")
            session.error_code = StatusCode.AUTH_LIVENESS_TIMEOUT
            session.result = AuthenticationResult.FAILURE
            self._finalize_session()
            return self._session_status()

        # Get face and analyze
        faces = self.face_detector.detect(frame)
        if len(faces) == 0:
            return self._session_status()

        face = faces[0]
        face_crop = face.crop_from_frame(frame)

        # Detect liveness cues
        head_pose = self.liveness_detector.detect_head_pose(face_crop)
        is_blink, blink_conf = self.liveness_detector.detect_blink(face_crop)
        is_mouth_open, mouth_conf = self.liveness_detector.detect_mouth_opening(face_crop)

        # Validate current challenge
        challenge_type = current_challenge.type.value
        passed = False

        if challenge_type == "turn_head_left":
            if head_pose:
                passed = self.challenge_manager.validate_head_turn_left(
                    challenge_seq.sequence_id,
                    head_pose.yaw
                )

        elif challenge_type == "turn_head_right":
            if head_pose:
                passed = self.challenge_manager.validate_head_turn_right(
                    challenge_seq.sequence_id,
                    head_pose.yaw
                )

        elif challenge_type == "blink_twice":
            # Simple blink counting (would need frame sequence in production)
            if is_blink and blink_conf > 0.5:
                passed = self.challenge_manager.validate_blink(challenge_seq.sequence_id, 1)

        elif challenge_type == "open_mouth":
            passed = self.challenge_manager.validate_mouth_open(challenge_seq.sequence_id, is_mouth_open)

        if passed:
            challenge_seq.advance()

        # Check if all challenges passed
        if challenge_seq.is_complete and challenge_seq.passed:
            session.liveness_passed = True
            session.result = AuthenticationResult.SUCCESS
            self._finalize_session()
            logger_auth.info(f"Authentication SUCCESS: {session.user_detected_id}")

        elif challenge_seq.is_timed_out:
            session.error_code = StatusCode.AUTH_LIVENESS_TIMEOUT
            session.result = AuthenticationResult.FAILURE
            self._finalize_session()
            logger_auth.warning("Liveness challenge timed out")

        return self._session_status()

    def _finalize_session(self):
        """Finalize current session and update state."""
        session = self.current_session
        session.completed_at = time.time()

        # Record in history
        auth_event = {
            "session_id": session.session_id,
            "timestamp": session.completed_at,
            "result": session.result.value,
            "user_id": session.user_detected_id,
            "confidence": session.user_detected_confidence,
            "liveness_passed": session.liveness_passed,
            "error_code": session.error_code,
            "duration_sec": session.age_sec
        }

        self.auth_history.append(auth_event)

        # Update last successful auth if applicable
        if session.result == AuthenticationResult.SUCCESS:
            self.last_successful_auth = auth_event
            logger_auth.info("Last successful auth updated")

    def _session_status(self) -> Dict:
        """Get current session status."""
        if not self.current_session:
            return {"error": "No active session"}

        session = self.current_session
        challenge = session.liveness_challenge

        return {
            "session_id": session.session_id,
            "state": session.result.value,
            "user_id": session.user_detected_id,
            "confidence": session.user_detected_confidence,
            "frames_processed": session.frames_processed,
            "faces_detected": session.face_count,
            "liveness_on_going": challenge is not None and not challenge.is_complete,
            "liveness_passed": session.liveness_passed,
            "challenge_progress": challenge.progress if challenge else (0, 0),
            "current_challenge": challenge.current_challenge.human_description if challenge and challenge.current_challenge else None,
            "error_code": session.error_code,
            "age_sec": session.age_sec,
            "timed_out": session.is_timed_out
        }

    def get_session_status(self) -> Dict:
        """Get current session status (public API)."""
        return self._session_status()

    def end_session(self) -> AuthenticationResult:
        """End session and return final result."""
        if not self.current_session:
            raise AuthenticationError("No active session")

        if self.current_session.result == AuthenticationResult.IN_PROGRESS:
            self.current_session.result = AuthenticationResult.TIMEOUT
            self._finalize_session()

        return self.current_session.result

    # ========== Management ==========

    def get_auth_history(self, limit: int = 10) -> List[Dict]:
        """Get recent authentication history."""
        return self.auth_history[-limit:]

    def __repr__(self):
        session_state = self.current_session.result.value if self.current_session else "idle"
        return f"AuthController(session={session_state}, users={len(self.user_manager.get_all_users())})"
