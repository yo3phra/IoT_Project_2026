"""
Enrollment controller - orchestrates new user enrollment.
Captures multiple face frames, generates embeddings, stores them.
Admin-only operation.
"""

import time
import os
import sys
from typing import Optional, Dict, List, Tuple
from enum import Enum
from dataclasses import dataclass
import uuid

# Add Coral directory to path if running from parent
coral_dir = os.path.join(os.path.dirname(__file__))
if coral_dir not in sys.path:
    sys.path.insert(0, coral_dir)

from config import get_config
from logger import logger_enroll
from errors import EnrollmentError, StatusCode

from camera_interface import CameraInterface, get_camera
from face_detector import FaceDetector, get_face_detector
from face_recognizer import FaceRecognizer, FaceEmbedding, get_face_recognizer
from embedding_store import EmbeddingStore
from user_manager import UserManager


class EnrollmentResult(Enum):
    """Enrollment result states."""
    SUCCESS = "success"
    FAILURE = "failure"
    IN_PROGRESS = "in_progress"
    TIMEOUT = "timeout"


@dataclass
class EnrollmentSession:
    """Represents an ongoing enrollment session."""
    session_id: str
    user_id: str
    username: str
    result: Optional[EnrollmentResult]
    frames_processed: int
    embeddings_captured: int
    faces_detected: int
    last_embedding_frame: int  # Track frame number of last captured embedding
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
        config = get_config().enrollment_controller
        return self.age_sec > config.total_timeout_sec


class EnrollmentController:
    """
    Enrollment orchestrator.
    Captures faces + generates embeddings for new user.
    """

    def __init__(
        self,
        camera: CameraInterface = None,
        embedding_store: EmbeddingStore = None,
        mock_mode: bool = False
    ):
        """
        Initialize enrollment controller.

        Args:
            camera: Camera interface (None to auto-create)
            embedding_store: Embedding storage (None to auto-create)
            mock_mode: Use mock models for testing
        """
        self.config = get_config().enrollment_controller
        self.runtime_config = get_config()

        # Components
        self.camera = camera or get_camera(mock=mock_mode)
        self.face_detector = get_face_detector(mock=mock_mode)
        self.face_recognizer = get_face_recognizer(mock=mock_mode)

        # Storage
        if embedding_store is None:
            from embedding_store import EmbeddingStore
            embedding_store = EmbeddingStore()
        self.embedding_store = embedding_store
        self.user_manager = UserManager(embedding_store)

        # State
        self.current_session: Optional[EnrollmentSession] = None
        self.mock_mode = mock_mode

        logger_enroll.info(f"Enrollment controller initialized (mock={mock_mode})")

    def start_enrollment(
        self,
        user_id: str,
        username: str,
        metadata: dict = None
    ) -> str:
        """
        Start new user enrollment.

        Args:
            user_id: Unique user identifier
            username: User display name
            metadata: Optional metadata

        Returns:
            Session ID

        Raises:
            EnrollmentError: If user already exists or validation fails
        """
        # Validate user doesn't exist
        if existing := self.embedding_store.get_user_info(user_id):
            raise EnrollmentError(f"User already exists: {user_id}")

        # Enroll user in database first
        try:
            self.user_manager.enroll_user(user_id, username, metadata or {})
        except Exception as e:
            logger_enroll.error(f"Failed to create user record: {e}")
            raise EnrollmentError(f"User creation failed: {e}")

        # Start enrollment session
        session_id = str(uuid.uuid4())
        self.current_session = EnrollmentSession(
            session_id=session_id,
            user_id=user_id,
            username=username,
            result=EnrollmentResult.IN_PROGRESS,
            frames_processed=0,
            embeddings_captured=0,
            faces_detected=0,
            last_embedding_frame=-999,  # Initialize to old frame so first one captured
            error_code=None,
            created_at=time.time(),
            completed_at=None
        )

        logger_enroll.info(f"Enrollment started: {username} ({user_id}), session={session_id}")
        return session_id

    def capture_enrollment_frame(self, frame_data=None) -> Dict:
        """
        Process one frame during enrollment to capture face embedding.

        Args:
            frame_data: Optional pre-captured frame (for testing)

        Returns:
            Status dict with capture progress
        """
        if not self.current_session:
            raise EnrollmentError("No active enrollment. Call start_enrollment() first.")

        session = self.current_session

        # Check timeout
        if session.is_timed_out:
            session.result = EnrollmentResult.TIMEOUT
            session.error_code = StatusCode.ENROLLMENT_TIMEOUT
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

            # Detect face
            faces = self.face_detector.detect(frame)

            if len(faces) == 0:
                session.error_code = StatusCode.ENROLLMENT_FACE_NOT_DETECTED
                logger_enroll.debug(f"Frame {session.frames_processed}: No face detected")
                return self._session_status()

            if len(faces) > 1:
                session.error_code = "ENROLLMENT_MULTIPLE_FACES"
                logger_enroll.debug(f"Frame {session.frames_processed}: Multiple faces detected")
                return self._session_status()

            # Check if enough frames have passed since last embedding
            frames_since_last = session.frames_processed - session.last_embedding_frame
            min_frames = self.config.min_frames_between_captures

            if frames_since_last < min_frames:
                logger_enroll.debug(
                    f"Frame {session.frames_processed}: Skipping capture "
                    f"({frames_since_last}/{min_frames} frames since last)"
                )
                return self._session_status()

            # Extract and generate embedding
            face = faces[0]
            face_crop = face.crop_from_frame(frame)
            embedding = self.face_recognizer.generate_embedding(face_crop)

            # Store embedding
            self.embedding_store.add_embedding(
                user_id=session.user_id,
                embedding=embedding,
                source="enrollment"
            )

            session.faces_detected += 1
            session.embeddings_captured += 1
            session.last_embedding_frame = session.frames_processed
            logger_enroll.info(
                f"Frame {session.frames_processed}: Embedding captured for {session.username} "
                f"({session.embeddings_captured}/{self.config.embeddings_per_user})"
            )

            # Check if we have enough embeddings
            if session.embeddings_captured >= self.config.embeddings_per_user:
                session.result = EnrollmentResult.SUCCESS
                self._finalize_session()
                logger_enroll.info(
                    f"Enrollment SUCCESS: {session.username} "
                    f"({session.embeddings_captured} embeddings)"
                )

            return self._session_status()

        except Exception as e:
            logger_enroll.error(f"Frame processing error: {e}")
            session.error_code = StatusCode.SYSTEM_INTERNAL_ERROR
            session.result = EnrollmentResult.FAILURE
            self._finalize_session()
            return self._session_status()

    def _finalize_session(self):
        """Finalize current enrollment session."""
        session = self.current_session
        session.completed_at = time.time()

        if session.result != EnrollmentResult.SUCCESS:
            # Rollback user if enrollment failed
            try:
                self.user_manager.remove_user(session.user_id)
                logger_enroll.warning(f"Enrollment rolled back: {session.user_id}")
            except Exception as e:
                logger_enroll.error(f"Rollback failed: {e}")

    def _session_status(self) -> Dict:
        """Get current enrollment session status."""
        if not self.current_session:
            return {"error": "No active enrollment"}

        session = self.current_session
        target_embeddings = self.config.embeddings_per_user

        return {
            "session_id": session.session_id,
            "state": session.result.value,
            "user_id": session.user_id,
            "username": session.username,
            "frames_processed": session.frames_processed,
            "embeddings_captured": session.embeddings_captured,
            "embeddings_target": target_embeddings,
            "progress": f"{session.embeddings_captured}/{target_embeddings}",
            "faces_detected": session.faces_detected,
            "error_code": session.error_code,
            "age_sec": session.age_sec,
            "timed_out": session.is_timed_out
        }

    def get_session_status(self) -> Dict:
        """Get current session status (public API)."""
        return self._session_status()

    def end_enrollment(self) -> EnrollmentResult:
        """End enrollment session and return final result."""
        if not self.current_session:
            raise EnrollmentError("No active enrollment")

        if self.current_session.result == EnrollmentResult.IN_PROGRESS:
            self.current_session.result = EnrollmentResult.TIMEOUT
            self._finalize_session()

        return self.current_session.result

    def cancel_enrollment(self) -> bool:
        """Cancel ongoing enrollment and rollback user."""
        if not self.current_session:
            return False

        try:
            self.user_manager.remove_user(self.current_session.user_id)
            logger_enroll.info(f"Enrollment cancelled: {self.current_session.user_id}")
            self.current_session = None
            return True
        except Exception as e:
            logger_enroll.error(f"Cancellation failed: {e}")
            return False

    def __repr__(self):
        session_state = self.current_session.result.value if self.current_session else "idle"
        return f"EnrollmentController(session={session_state}, users={len(self.user_manager.get_all_users())})"
