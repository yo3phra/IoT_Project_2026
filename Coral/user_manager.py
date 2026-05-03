"""
User manager - manages user lifecycle and enrollment.
Admin-controlled enrollment only (security).
"""

from typing import List, Optional
from config import get_config
from logger import logger_user
from errors import StorageError
from embedding_store import EmbeddingStore


class UserManager:
    """Manages user enrollment and removal."""

    def __init__(self, embedding_store: EmbeddingStore):
        """
        Initialize user manager.

        Args:
            embedding_store: EmbeddingStore instance for persistence
        """
        self.config = get_config().user_manager
        self.store = embedding_store

    def enroll_user(
        self,
        user_id: str,
        username: str,
        metadata: dict = None
    ) -> bool:
        """
        Enroll new user (admin-only operation).

        Args:
            user_id: Unique user identifier
            username: Display name
            metadata: Optional metadata (e.g., {"role": "owner", "email": "..."})

        Returns:
            True if enrollment successful

        Raises:
            StorageError: If enrollment fails
        """
        # Validate
        if not self._is_valid_username(username):
            raise ValueError(f"Invalid username: {username}")

        if len(self.get_all_users()) >= self.config.max_users:
            raise StorageError(f"Max users ({self.config.max_users}) reached")

        try:
            self.store.add_user(user_id, username, metadata or {})
            logger_user.info(f"User enrolled: {username} ({user_id})")
            return True

        except Exception as e:
            logger_user.error(f"Enrollment failed: {e}")
            raise StorageError(f"Enrollment failed: {e}")

    def remove_user(self, user_id: str) -> bool:
        """
        Remove user and all data (secure wipe).

        Args:
            user_id: User to remove

        Returns:
            True if successful
        """
        try:
            user_info = self.store.get_user_info(user_id)
            if not user_info:
                raise StorageError(f"User not found: {user_id}")

            self.store.remove_user(user_id)
            logger_user.warning(f"User removed: {user_info['username']} ({user_id})")
            return True

        except Exception as e:
            logger_user.error(f"User removal failed: {e}")
            raise StorageError(f"Removal failed: {e}")

    def get_user(self, user_id: str) -> Optional[dict]:
        """Get user information."""
        return self.store.get_user_info(user_id)

    def get_all_users(self) -> List[dict]:
        """Get all enrolled users."""
        user_ids = self.store.get_all_users()
        users = []

        for uid in user_ids:
            if info := self.store.get_user_info(uid):
                embedding_count = self.store.get_embedding_count(uid)
                info["embedding_count"] = embedding_count
                users.append(info)

        return users

    def _is_valid_username(self, username: str) -> bool:
        """Validate username format."""
        if not isinstance(username, str):
            return False

        length = len(username)
        if length < self.config.min_username_length or length > self.config.max_username_length:
            return False

        # Alphanumeric + underscore + dash
        import re
        if not re.match(r'^[a-zA-Z0-9_-]+$', username):
            return False

        return True

    def __repr__(self):
        return f"UserManager(users={len(self.get_all_users())})"
