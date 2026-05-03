"""
Face recognition module - generates embeddings and compares faces.
Supports both CPU and TPU inference.
"""

import numpy as np
from typing import List, Tuple, Optional
import os
from config import get_config
from logger import logger_face_rec
from errors import FaceRecognitionError, ModelError
from embedding_model import get_embedding_backend, EmbeddingModelBackend


class FaceEmbedding:
    """Represents a face embedding (feature vector)."""

    def __init__(self, vector: np.ndarray, user_id: str = None, confidence: float = 0.0):
        """
        Args:
            vector: Embedding vector (128D or 512D)
            user_id: Associated user ID (optional)
            confidence: Embedding confidence
        """
        self.vector = np.array(vector, dtype=np.float32)
        self.dimension = len(self.vector)
        self.user_id = user_id
        self.confidence = confidence

    def distance_to(self, other: "FaceEmbedding") -> float:
        """
        Euclidean distance to another embedding.

        Args:
            other: Another FaceEmbedding

        Returns:
            L2 distance
        """
        if self.dimension != other.dimension:
            raise ValueError("Embedding dimensions don't match")
        return float(np.linalg.norm(self.vector - other.vector))

    def similarity(self, other: "FaceEmbedding") -> float:
        """
        Cosine similarity to another embedding.

        Args:
            other: Another FaceEmbedding

        Returns:
            Similarity score [0, 1]
        """
        if self.dimension != other.dimension:
            raise ValueError("Embedding dimensions don't match")

        dot_product = np.dot(self.vector, other.vector)
        mag1 = np.linalg.norm(self.vector)
        mag2 = np.linalg.norm(other.vector)

        if mag1 == 0 or mag2 == 0:
            return 0.0

        return float(dot_product / (mag1 * mag2))

    def __repr__(self):
        return f"FaceEmbedding(dim={self.dimension}, user_id={self.user_id})"


class FaceRecognizer:
    """
    Face embedding generator and similarity matcher.
    Uses pluggable embedding backends (ArcFace, TensorFlow, etc.)
    """

    def __init__(self, backend_type: str = None):
        """
        Initialize face recognizer.

        Args:
            backend_type: Embedding backend type ("arcface", "tensorflow", "mock").
                         If None, uses config.face_recognition.backend_type
        """
        self.config = get_config().face_recognition
        self.runtime_config = get_config()

        # Determine backend type
        if backend_type is None:
            backend_type = self.config.backend_type

        # Load appropriate backend
        self.backend: EmbeddingModelBackend = None
        self._load_backend(backend_type)

    def _load_backend(self, backend_type: str):
        """Load embedding model backend."""
        try:
            self.backend = get_embedding_backend(
                backend_type=backend_type,
                model_dir=self.config.model_dir,
                runtime_config=self.runtime_config
            )
            self.backend.load()
            logger_face_rec.info(
                f"Face recognition initialized with {self.backend.model_name} backend "
                f"({self.backend.embedding_dimension}D embeddings)"
            )
        except Exception as e:
            logger_face_rec.error(f"Backend loading failed: {e}")
            raise ModelError(f"Face recognition model failed to load: {e}")

    def generate_embedding(self, face_frame: np.ndarray) -> FaceEmbedding:
        """
        Generate embedding from face crop.

        Args:
            face_frame: Cropped face region (from Face.crop_from_frame())

        Returns:
            FaceEmbedding object
        """
        if self.backend is None:
            raise FaceRecognitionError("Model not loaded")

        try:
            embedding_vector = self.backend.generate_embedding(face_frame)
            logger_face_rec.debug(f"Generated embedding: {embedding_vector.shape}")
            return FaceEmbedding(embedding_vector)
        except Exception as e:
            logger_face_rec.error(f"Embedding generation failed: {e}")
            raise FaceRecognitionError(f"Embedding generation failed: {e}")

    def match_embeddings(
        self,
        embedding1: FaceEmbedding,
        embedding2: FaceEmbedding
    ) -> Tuple[bool, float]:
        """
        Match two embeddings using distance threshold.

        Args:
            embedding1: First embedding
            embedding2: Second embedding

        Returns:
            Tuple of (is_match, distance)
        """
        distance = embedding1.distance_to(embedding2)
        is_match = distance < self.config.distance_threshold

        logger_face_rec.debug(
            f"Match result: match={is_match}, distance={distance:.4f}, "
            f"threshold={self.config.distance_threshold}"
        )

        return is_match, distance

    def find_best_match(
        self,
        test_embedding: FaceEmbedding,
        reference_embeddings: List[FaceEmbedding]
    ) -> Tuple[Optional[FaceEmbedding], float]:
        """
        Find best matching embedding from reference set.

        Args:
            test_embedding: Embedding to match
            reference_embeddings: List of reference embeddings

        Returns:
            Tuple of (matched_embedding, distance) or (None, inf)
        """
        if not reference_embeddings:
            return None, float('inf')

        best_match = None
        best_distance = float('inf')

        for ref_embedding in reference_embeddings:
            distance = test_embedding.distance_to(ref_embedding)
            if distance < best_distance:
                best_distance = distance
                best_match = ref_embedding

        return best_match, best_distance


def get_face_recognizer(mock: bool = False, backend_type: str = None) -> FaceRecognizer:
    """
    Factory function to get face recognizer.

    Args:
        mock: Force mock mode for testing
        backend_type: Override backend type ("arcface", "tensorflow", "mock")

    Returns:
        FaceRecognizer instance
    """
    if mock:
        logger_face_rec.info("Using mock face recognizer")
        return FaceRecognizer(backend_type="mock")

    return FaceRecognizer(backend_type=backend_type)
