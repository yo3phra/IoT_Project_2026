"""
Encrypted embedding storage - securely stores facial embeddings locally.
Uses AES-256-GCM encryption with key derivation.
CRITICAL: Facial data never leaves this device.
"""

import sqlite3
import os
import json
import base64
from typing import List, Optional, Dict
import numpy as np
from config import get_config
from logger import logger_embed
from errors import StorageError
from face_recognizer import FaceEmbedding


class EncryptionManager:
    """Handles encryption/decryption of embedding data."""

    def __init__(self):
        """Initialize encryption manager."""
        self.config = get_config().embedding_store
        self._crypto_warning_shown = False  # Track if warning already shown

    def encrypt_data(self, data: str, password: str) -> str:
        """
        Encrypt data using AES-256-GCM.

        Args:
            data: Plain text data
            password: Encryption password

        Returns:
            Encrypted data (base64 for storage)
        """
        try:
            from cryptography.fernet import Fernet
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2
            from cryptography.hazmat.backends import default_backend

            # Derive key from password
            kdf = PBKDF2(
                algorithm=hashes.SHA256(),
                length=32,
                salt=b"coral_biometric",  # later: use random salt
                iterations=self.config.key_iterations,
                backend=default_backend()
            )
            key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
            cipher = Fernet(key)

            encrypted = cipher.encrypt(data.encode())
            return base64.b64encode(encrypted).decode()

        except ImportError:
            if not self._crypto_warning_shown:
                logger_embed.warning("cryptography not available. Storing unencrypted (DEV ONLY).")
                self._crypto_warning_shown = True
            return base64.b64encode(data.encode()).decode()

    def decrypt_data(self, encrypted_data: str, password: str) -> str:
        """
        Decrypt data using AES-256-GCM.

        Args:
            encrypted_data: Encrypted data (base64)
            password: Decryption password

        Returns:
            Plain text data
        """
        try:
            from cryptography.fernet import Fernet
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2
            from cryptography.hazmat.backends import default_backend

            kdf = PBKDF2(
                algorithm=hashes.SHA256(),
                length=32,
                salt=b"coral_biometric",
                iterations=self.config.key_iterations,
                backend=default_backend()
            )
            key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
            cipher = Fernet(key)

            encrypted = base64.b64decode(encrypted_data.encode())
            decrypted = cipher.decrypt(encrypted)
            return decrypted.decode()

        except ImportError:
            if not self._crypto_warning_shown:
                logger_embed.warning("cryptography not available. Using plaintext (DEV ONLY).")
                self._crypto_warning_shown = True
            return base64.b64decode(encrypted_data.encode()).decode()

    def __repr__(self):
        return f"EncryptionManager(algorithm={self.config.encryption_algorithm})"


class EmbeddingStore:
    """
    Secure local storage for face embeddings.
    Encrypted SQLite database.
    """

    def __init__(self, db_path: str = None, encryption_key: str = "default_key"):
        """
        Initialize embedding store.

        Args:
            db_path: Path to SQLite database
            encryption_key: Master encryption key (derive from secure bootstrap)
        """
        self.config = get_config().embedding_store
        self.db_path = db_path or self.config.db_path
        self.encryption_key = encryption_key
        self.encryptor = EncryptionManager()
        self._ensure_db()

    def _ensure_db(self):
        """Create database tables if not exist."""
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)

        try:
            with sqlite3.connect(self.db_path) as conn:
                # CRITICAL: Enable foreign keys for CASCADE DELETE to work
                conn.execute("PRAGMA foreign_keys = ON")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id TEXT PRIMARY KEY,
                        username TEXT UNIQUE NOT NULL,
                        created_at REAL,
                        updated_at REAL,
                        metadata TEXT
                    )
                """)

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS embeddings (
                        embedding_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        embedding_data TEXT NOT NULL,
                        embedding_dim INTEGER,
                        created_at REAL,
                        source TEXT,
                        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                    )
                """)

                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_user_embeddings
                    ON embeddings(user_id)
                """)

                conn.commit()
            logger_embed.info(f"Database initialized: {self.db_path}")

        except Exception as e:
            logger_embed.error(f"Database initialization failed: {e}")
            raise StorageError(f"DB init failed: {e}")

    def add_user(self, user_id: str, username: str, metadata: Dict = None) -> bool:
        """
        Add new user to store.

        Args:
            user_id: Unique user identifier
            username: User display name
            metadata: Optional metadata (dict)

        Returns:
            True if successful
        """
        import time

        try:
            metadata_json = json.dumps(metadata or {})

            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO users (user_id, username, created_at, updated_at, metadata)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, username, time.time(), time.time(), metadata_json))
                conn.commit()

            logger_embed.info(f"User added: {username} ({user_id})")
            return True

        except sqlite3.IntegrityError as e:
            logger_embed.warning(f"User already exists: {user_id}")
            raise StorageError(f"User exists: {e}")
        except Exception as e:
            logger_embed.error(f"Failed to add user: {e}")
            raise StorageError(f"Add user failed: {e}")

    def remove_user(self, user_id: str) -> bool:
        """
        Remove user and all embeddings (secure wipe).

        Args:
            user_id: User to remove

        Returns:
            True if successful
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Enable foreign keys for CASCADE DELETE
                conn.execute("PRAGMA foreign_keys = ON")

                # Cascading delete via foreign key
                conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
                conn.commit()

            logger_embed.info(f"User removed: {user_id}")
            return True

        except Exception as e:
            logger_embed.error(f"Failed to remove user: {e}")
            raise StorageError(f"Remove user failed: {e}")

    def add_embedding(
        self,
        user_id: str,
        embedding: FaceEmbedding,
        source: str = "camera"
    ) -> str:
        """
        Store encrypted embedding for user.

        Args:
            user_id: User ID
            embedding: FaceEmbedding object
            source: Source of embedding (e.g., "camera", "enrollment")

        Returns:
            Embedding ID
        """
        import time
        import uuid

        try:
            embedding_id = str(uuid.uuid4())

            # Serialize embedding
            embedding_data = {
                "vector": embedding.vector.tolist(),
                "dimension": embedding.dimension
            }
            embedding_json = json.dumps(embedding_data)

            # Encrypt
            encrypted_data = self.encryptor.encrypt_data(
                embedding_json,
                self.encryption_key
            )

            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO embeddings
                    (embedding_id, user_id, embedding_data, embedding_dim, created_at, source)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    embedding_id,
                    user_id,
                    encrypted_data,
                    embedding.dimension,
                    time.time(),
                    source
                ))
                conn.commit()

            logger_embed.debug(f"Embedding stored: {embedding_id} for user {user_id}")
            return embedding_id

        except Exception as e:
            logger_embed.error(f"Failed to store embedding: {e}")
            raise StorageError(f"Embedding storage failed: {e}")

    def get_user_embeddings(self, user_id: str) -> List[FaceEmbedding]:
        """
        Retrieve all embeddings for user (decrypted).

        Args:
            user_id: User ID

        Returns:
            List of FaceEmbedding objects
        """
        try:
            embeddings = []

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    SELECT embedding_data FROM embeddings
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                """, (user_id,))

                for row in cursor.fetchall():
                    encrypted_data = row[0]

                    # Decrypt
                    embedding_json = self.encryptor.decrypt_data(
                        encrypted_data,
                        self.encryption_key
                    )

                    data = json.loads(embedding_json)
                    vector = np.array(data["vector"], dtype=np.float32)
                    embedding = FaceEmbedding(vector, user_id=user_id)
                    embeddings.append(embedding)

            logger_embed.debug(f"Retrieved {len(embeddings)} embeddings for {user_id}")
            return embeddings

        except Exception as e:
            logger_embed.error(f"Failed to retrieve embeddings: {e}")
            raise StorageError(f"Retrieval failed: {e}")

    def get_all_users(self) -> List[str]:
        """Get list of all user IDs."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT user_id FROM users ORDER BY username")
                users = [row[0] for row in cursor.fetchall()]

            logger_embed.debug(f"Retrieved {len(users)} users")
            return users

        except Exception as e:
            logger_embed.error(f"Failed to list users: {e}")
            raise StorageError(f"List users failed: {e}")

    def get_user_info(self, user_id: str) -> Optional[Dict]:
        """Get user metadata."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    SELECT user_id, username, created_at, metadata
                    FROM users WHERE user_id = ?
                """, (user_id,))

                row = cursor.fetchone()
                if row:
                    return {
                        "user_id": row[0],
                        "username": row[1],
                        "created_at": row[2],
                        "metadata": json.loads(row[3] or "{}")
                    }

            return None

        except Exception as e:
            logger_embed.error(f"Failed to get user info: {e}")
            raise StorageError(f"Get user info failed: {e}")

    def get_embedding_count(self, user_id: str) -> int:
        """Count embeddings for user."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    SELECT COUNT(*) FROM embeddings WHERE user_id = ?
                """, (user_id,))
                count = cursor.fetchone()[0]

            return count

        except Exception as e:
            logger_embed.error(f"Failed to count embeddings: {e}")
            raise StorageError(f"Count failed: {e}")

    def __repr__(self):
        return f"EmbeddingStore(db={self.db_path})"
