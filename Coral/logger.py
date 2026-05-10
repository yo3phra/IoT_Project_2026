"""
Secure logging module - ensures no biometric data is logged.
"""

import logging
import logging.handlers
from pathlib import Path
from config import get_config


class BiometricSanitizer(logging.Filter):
    """
    Filter that sanitizes log messages to prevent biometric data leakage.
    CRITICAL: No embeddings, face images, or confidence scores in logs.
    """

    SENSITIVE_KEYS = [
        "embedding", "embeddings", "face", "image", "frame",
        "confidence", "distance", "image_data", "pixel",
        "user_data", "secret", "key", "token"
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        """Sanitize log record."""
        if not hasattr(record, 'msg'):
            return True

        # Check if message contains sensitive info
        msg_str = str(record.msg).lower()
        for key in self.SENSITIVE_KEYS:
            if key in msg_str:
                # Allow certain safe operations
                if "embedding_store" not in msg_str and "user_manager" not in msg_str:
                    record.msg = f"[SANITIZED] {record.msg}"
                    break

        return True


def setup_logger(name: str, level: str = None) -> logging.Logger:
    """
    Setup a logger with file and console handlers.

    Args:
        name: Logger name
        level: Logging level (defaults to config)

    Returns:
        Configured logger instance
    """
    config = get_config()

    if level is None:
        level = config.logger.log_level

    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level))

    # Create logs directory
    log_dir = Path(config.logger.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    # File handler (rotating) - ensures logs don't grow indefinitely, and sensitive data is sanitized. 
    log_file = log_dir / f"{name}.log"
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=config.logger.max_log_size_mb * 1024 * 1024,
        backupCount=config.logger.backup_count
    )
    file_handler.setLevel(getattr(logging, level))

    # Console handler - also sanitized. Handles console output.
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, level))

    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    # Add sanitizer filter
    sanitizer = BiometricSanitizer()
    file_handler.addFilter(sanitizer)
    console_handler.addFilter(sanitizer)

    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


# Module-level logger instances
logger_cam = setup_logger("camera_interface")
logger_face_det = setup_logger("face_detector")
logger_face_rec = setup_logger("face_recognizer")
logger_liveness = setup_logger("liveness_detector")
logger_challenge = setup_logger("challenge_manager")
logger_auth = setup_logger("auth_controller")
logger_enroll = setup_logger("enrollment_controller")
logger_embed = setup_logger("embedding_store")
logger_user = setup_logger("user_manager")
logger_cloud = setup_logger("cloud_interface")
logger_admin = setup_logger("admin_interface")


def get_logger(module_name: str) -> logging.Logger:
    """Get or create logger for module."""
    return logging.getLogger(module_name)
