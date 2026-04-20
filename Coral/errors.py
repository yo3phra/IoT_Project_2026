"""
Custom exceptions for face recognition system.
"""


class BiometricSystemError(Exception):
    """Base exception for biometric system."""
    pass


class CameraError(BiometricSystemError):
    """Camera I/O errors."""
    pass


class FaceDetectionError(BiometricSystemError):
    """Face detection failures."""
    pass


class FaceRecognitionError(BiometricSystemError):
    """Face recognition / embedding errors."""
    pass


class LivenessError(BiometricSystemError):
    """Liveness detection failures."""
    pass


class AuthenticationError(BiometricSystemError):
    """Authentication / authorization failures."""
    pass


class StorageError(BiometricSystemError):
    """Data storage / encryption errors."""
    pass


class PyTrackError(BiometricSystemError):
    """PyTrack communication errors."""
    pass


class CloudError(BiometricSystemError):
    """Cloud backend communication errors."""
    pass


class ConfigError(BiometricSystemError):
    """Configuration errors."""
    pass


class ModelError(BiometricSystemError):
    """Model loading / inference errors."""
    pass


class EnrollmentError(BiometricSystemError):
    """User enrollment errors."""
    pass


class StatusCode:
    """Standard status codes for authentication and events."""

    # Success
    AUTH_SUCCESS = "AUTH_SUCCESS"

    # Auth failures
    AUTH_FACE_NOT_DETECTED = "AUTH_FACE_NOT_DETECTED"
    AUTH_FACE_NOT_RECOGNIZED = "AUTH_FACE_NOT_RECOGNIZED"
    AUTH_CONFIDENCE_TOO_LOW = "AUTH_CONFIDENCE_TOO_LOW"
    AUTH_LIVENESS_TIMEOUT = "AUTH_LIVENESS_TIMEOUT"
    AUTH_LIVENESS_FAILED = "AUTH_LIVENESS_FAILED"
    AUTH_TIMEOUT = "AUTH_TIMEOUT"

    # Enrollment failures
    ENROLLMENT_FACE_NOT_DETECTED = "ENROLLMENT_FACE_NOT_DETECTED"
    ENROLLMENT_TIMEOUT = "ENROLLMENT_TIMEOUT"
    ENROLLMENT_FAILED = "ENROLLMENT_FAILED"

    # System errors
    SYSTEM_CAMERA_ERROR = "SYSTEM_CAMERA_ERROR"
    SYSTEM_MODEL_ERROR = "SYSTEM_MODEL_ERROR"
    SYSTEM_STORAGE_ERROR = "SYSTEM_STORAGE_ERROR"
    SYSTEM_INTERNAL_ERROR = "SYSTEM_INTERNAL_ERROR"

    # Tracking/theft
    TRACKING_TRIGGER = "TRACKING_TRIGGER"
    SUSPICIOUS_ACTIVITY = "SUSPICIOUS_ACTIVITY"
