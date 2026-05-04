"""
Configuration module - centralized settings for face recognition system.
Supports both Windows prototype and Coral TPU deployment.
"""

import os
from enum import Enum
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class ExecutionMode(Enum):
    """Runtime environment mode."""
    WINDOWS_PROTOTYPE = "windows_prototype"
    CORAL_TPU = "coral_tpu"


# Auto-detect execution mode
def get_execution_mode() -> ExecutionMode:
    """Auto-detect if running on Coral or Windows."""
    try:
        # Check for Coral Edge TPU kernel module
        if os.path.exists("/sys/module/apex"):
            return ExecutionMode.CORAL_TPU
    except Exception:
        pass
    return ExecutionMode.WINDOWS_PROTOTYPE


@dataclass
class CameraConfig:
    """Camera settings."""
    width: int = 720
    height: int = 480
    fps: int = 15
    device_id: int = 0  # Default USB camera


@dataclass
class FaceDetectionConfig:
    """Face detection model settings."""
    confidence_threshold: float = 0.5
    nms_threshold: float = 0.5
    # Model path (CPU vs TPU selected at runtime)
    model_dir: str = "models"


@dataclass
class FaceRecognitionConfig:
    """Face recognition & embedding settings."""
    embedding_dim: int = 512
    confidence_threshold: float = 0.60  # Must exceed this for auth
    distance_threshold: float = 0.6  # L2 distance for matching
    # Model backend: "tensorflow", "onnx"(default), or "mock"
    backend_type: str = "onnx"
    # Model will be auto-selected (CPU vs TPU)
    model_dir: str = "models"


@dataclass
class LivenessDetectionConfig:
    """Liveness challenge settings."""
    model_dir: str = "models"
    timeout_per_challenge_sec: int = 10
    max_attempts_per_session: int = 3
    required_head_turn_degrees: int = 15
    required_blink_count: int = 2
    head_pose_confidence_threshold: float = 0.50


@dataclass
class AuthControllerConfig:
    """Authentication orchestration settings."""
    total_timeout_sec: int = 30
    min_frames_for_detection: int = 5
    cache_embeddings_locally: bool = True


@dataclass
class EnrollmentControllerConfig:
    """Enrollment orchestration settings."""
    total_timeout_sec: int = 120  # 2 minutes to capture all embeddings
    embeddings_per_user: int = 2  # Require 8 embeddings for robust recognition (more angles)
    min_frames_between_captures: int = 10  # Skip 10 frames (~667ms @ 15fps) to get varied angles


@dataclass
class EmbeddingStoreConfig:
    """Encrypted storage settings."""
    db_path: str = "data/embeddings.db"
    encryption_algorithm: str = "AES-256-GCM"
    key_derivation: str = "PBKDF2"
    key_iterations: int = 100_000


@dataclass
class UserManagerConfig:
    """User management settings."""
    max_users: int = 50
    min_username_length: int = 3
    max_username_length: int = 32


@dataclass
class PyTrackInterfaceConfig:
    """PyTrack integration settings."""
    timeout_for_auth_sec: int = 300  # 5 minutes
    enable_mock_mode: bool = True  # For Windows testing
    mock_data_dir: str = "data/mock_pytrack"


@dataclass
class CloudInterfaceConfig:
    """Cloud communication settings (placeholder)."""
    protocol: str = "mqtt"  # mqtt, rest, or websocket
    mqtt_broker_url: str = ""  # Will be set via env var
    mqtt_port: int = 1883
    mqtt_topic_events: str = "bikes/events"
    rest_api_url: str = ""  # Will be set via env var
    offline_queue_dir: str = "data/event_queue"
    retry_max_attempts: int = 5
    retry_backoff_sec: int = 2


@dataclass
class LoggerConfig:
    """Logging settings."""
    log_dir: str = "logs"
    log_level: str = "INFO"
    max_log_size_mb: int = 10
    backup_count: int = 5
    # SECURITY: Never log biometric data
    sanitize_logs: bool = True


class Config:
    """Master configuration object."""

    def __init__(self, mode: ExecutionMode = None):
        if mode is None:
            mode = get_execution_mode()

        self.mode = mode
        self.execution_mode_name = mode.value

        # Component configs
        self.camera = CameraConfig()
        self.face_detection = FaceDetectionConfig()
        self.face_recognition = FaceRecognitionConfig()
        self.liveness = LivenessDetectionConfig()
        self.auth_controller = AuthControllerConfig()
        self.enrollment_controller = EnrollmentControllerConfig()
        self.embedding_store = EmbeddingStoreConfig()
        self.user_manager = UserManagerConfig()
        self.pytrack = PyTrackInterfaceConfig()
        self.cloud = CloudInterfaceConfig()
        self.logger = LoggerConfig()

        # Create data directories if not exist
        self._setup_directories()

        # Load environment overrides
        self._load_from_env()

    def _setup_directories(self):
        """Create necessary directories."""
        dirs = [
            self.embedding_store.db_path.split('/')[0],  # data/
            self.logger.log_dir,
            self.cloud.offline_queue_dir,
            self.pytrack.mock_data_dir,
        ]
        for dir_path in dirs:
            Path(dir_path).mkdir(parents=True, exist_ok=True)

    def _load_from_env(self):
        """Load settings from environment variables."""
        # Cloud configuration
        if mqtt_broker := os.getenv("MQTT_BROKER_URL"):
            self.cloud.mqtt_broker_url = mqtt_broker
        if rest_api := os.getenv("CLOUD_API_URL"):
            self.cloud.rest_api_url = rest_api

        # Protocol selection
        if protocol := os.getenv("CLOUD_PROTOCOL"):
            self.cloud.protocol = protocol

        # Azure IoT Hub
        self._azure_connection_string = os.getenv("AZURE_IOT_CONNECTION_STRING")

        # Logging
        if log_level := os.getenv("LOG_LEVEL"):
            self.logger.log_level = log_level

        # Model directories
        if model_dir := os.getenv("MODEL_DIR"):
            self.face_detection.model_dir = model_dir
            self.face_recognition.model_dir = model_dir

    @property
    def azure_connection_string(self) -> Optional[str]:
        """Get Azure IoT Hub connection string from environment."""
        return getattr(self, '_azure_connection_string', None)

    @property
    def is_coral(self) -> bool:
        """Check if running on Coral TPU."""
        return self.mode == ExecutionMode.CORAL_TPU

    @property
    def is_windows(self) -> bool:
        """Check if running on Windows prototype."""
        return self.mode == ExecutionMode.WINDOWS_PROTOTYPE


# Global config instance
_config = None


def get_config() -> Config:
    """Get global config instance (singleton)."""
    global _config
    if _config is None:
        _config = Config()
    return _config


def reset_config():
    """Reset config (for testing)."""
    global _config
    _config = None
