"""
Embedding model abstraction - pluggable backends for face embeddings.
Supports multiple sources: ArcFace library, TensorFlow models, etc.
"""

from abc import ABC, abstractmethod
import numpy as np
from typing import Optional
import os
from logger import logger_face_rec


class EmbeddingModelBackend(ABC):
    """Abstract interface for embedding model backends."""

    @abstractmethod
    def load(self):
        """Load/initialize the model."""
        pass

    @abstractmethod
    def generate_embedding(self, face_frame: np.ndarray) -> np.ndarray:
        """
        Generate embedding from face crop.

        Args:
            face_frame: Cropped face region (numpy array)

        Returns:
            Embedding vector (numpy array)
        """
        pass

    @property
    @abstractmethod
    def embedding_dimension(self) -> int:
        """Get embedding vector dimension."""
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Get model backend name."""
        pass


class ArcFaceBackend(EmbeddingModelBackend):
    """ArcFace embedding backend - recommended default."""

    def __init__(self):
        """Initialize ArcFace backend."""
        self.model = None
        self._embedding_dim = 512

    def load(self):
        """Load ArcFace model."""
        try:
            from arcface import ArcFace
            self.model = ArcFace.ArcFace()
            logger_face_rec.info("ArcFace model loaded successfully")
        except ImportError:
            logger_face_rec.error("ArcFace library not installed. Run: pip install arcface")
            raise
        except Exception as e:
            logger_face_rec.error(f"Failed to load ArcFace model: {e}")
            raise

    def generate_embedding(self, face_frame: np.ndarray) -> np.ndarray:
        """
        Generate embedding using ArcFace.

        Args:
            face_frame: Cropped face region (cv2 image, already aligned)

        Returns:
            512D embedding vector
        """
        if self.model is None:
            raise RuntimeError("Model not loaded. Call load() first.")

        try:
            # ArcFace.calc_emb accepts cv2 images directly
            embedding = self.model.calc_emb(face_frame)
            # Ensure float32
            embedding = np.array(embedding, dtype=np.float32)
            logger_face_rec.debug(f"Generated ArcFace embedding: {embedding.shape}")
            return embedding

        except Exception as e:
            logger_face_rec.error(f"ArcFace embedding generation failed: {e}")
            raise

    @property
    def embedding_dimension(self) -> int:
        """ArcFace produces 512D embeddings."""
        return self._embedding_dim

    @property
    def model_name(self) -> str:
        """Return backend name."""
        return "arcface"


class TensorFlowBackend(EmbeddingModelBackend):
    """TensorFlow embedding backend - supports CPU and Coral TPU models."""

    def __init__(self, model_dir: str = "models", runtime_config=None):
        """
        Initialize TensorFlow backend.

        Args:
            model_dir: Directory containing TF models
            runtime_config: Runtime config for Coral/CPU detection
        """
        self.model_dir = model_dir
        self.runtime_config = runtime_config
        self.model = None
        self._embedding_dim = 128
        self.is_tpu = runtime_config.is_coral if runtime_config else False

    def load(self):
        """Load TensorFlow model (CPU or TPU)."""
        try:
            if self.is_tpu:
                self._load_tpu_model()
            else:
                self._load_cpu_model()
        except Exception as e:
            logger_face_rec.error(f"TensorFlow model loading failed: {e}")
            raise

    def _load_cpu_model(self):
        """Load TensorFlow model for CPU."""
        try:
            import tensorflow as tf

            model_path = os.path.join(self.model_dir, "facenet_keras.h5")
            if not os.path.exists(model_path):
                logger_face_rec.warning(f"Model not found at {model_path}")
                raise FileNotFoundError(f"Model not found: {model_path}")

            self.model = tf.keras.models.load_model(model_path)
            self._embedding_dim = 128
            logger_face_rec.info("TensorFlow CPU model loaded")

        except ImportError:
            logger_face_rec.error("TensorFlow not installed")
            raise
        except Exception as e:
            logger_face_rec.error(f"CPU model loading failed: {e}")
            raise

    def _load_tpu_model(self):
        """Load TensorFlow Lite model for Coral TPU."""
        try:
            from pycoral.utils.edgetpu import make_interpreter

            model_path = os.path.join(
                self.model_dir,
                "facenet_mobilenet_v2_quant_edgetpu.tflite"
            )
            if not os.path.exists(model_path):
                logger_face_rec.warning(f"TPU model not found at {model_path}")
                raise FileNotFoundError(f"TPU model not found: {model_path}")

            self.model = make_interpreter(model_path)
            self.model.allocate_tensors()
            self._embedding_dim = 128
            logger_face_rec.info("TensorFlow Lite TPU model loaded")

        except ImportError:
            logger_face_rec.error("PyCoral not installed")
            raise
        except Exception as e:
            logger_face_rec.error(f"TPU model loading failed: {e}")
            raise

    def generate_embedding(self, face_frame: np.ndarray) -> np.ndarray:
        """Generate embedding using TensorFlow."""
        if self.model is None:
            raise RuntimeError("Model not loaded. Call load() first.")

        try:
            import cv2

            # Preprocess
            processed = cv2.resize(face_frame, (160, 160))
            processed = processed.astype(np.float32) / 255.0
            processed = np.expand_dims(processed, axis=0)

            if self.is_tpu:
                # TPU inference
                from PIL import Image
                self.model.resample_quantized_input(
                    Image.fromarray(cv2.cvtColor(face_frame, cv2.COLOR_BGR2RGB)),
                    (160, 160)
                )
                self.model.invoke()
                output_details = self.model.get_output_details()
                embedding = np.array(
                    self.model.get_tensor(output_details[0]['index'])
                ).flatten()
            else:
                # CPU inference
                embedding = self.model.predict(processed, verbose=0).flatten()

            embedding = np.array(embedding, dtype=np.float32)
            logger_face_rec.debug(f"Generated TensorFlow embedding: {embedding.shape}")
            return embedding

        except Exception as e:
            logger_face_rec.error(f"TensorFlow embedding generation failed: {e}")
            raise

    @property
    def embedding_dimension(self) -> int:
        """TensorFlow typically produces 128D embeddings."""
        return self._embedding_dim

    @property
    def model_name(self) -> str:
        """Return backend name."""
        return "tensorflow"


class ONNXBackend(EmbeddingModelBackend):
    """ONNX Runtime backend - supports WebFace R50 and other ONNX models."""

    def __init__(self, model_dir: str = "models", model_name: str = "webface_r50.onnx"):
        """
        Initialize ONNX backend.

        Args:
            model_dir: Directory containing ONNX model
            model_name: ONNX model filename (default: webface_r50.onnx)
        """
        self.model_dir = model_dir
        self.model_name_file = model_name
        self.session = None
        self._embedding_dim = 512
        self.input_name = None
        self.output_name = None

    def load(self):
        """Load ONNX model using ONNX Runtime."""
        try:
            import onnxruntime as ort

            model_path = os.path.join(self.model_dir, self.model_name_file)
            if not os.path.exists(model_path):
                raise FileNotFoundError(f"ONNX model not found: {model_path}")

            # Create ONNX Runtime session
            self.session = ort.InferenceSession(
                model_path,
                providers=['CPUExecutionProvider']
            )

            # Get input and output names
            self.input_name = self.session.get_inputs()[0].name
            self.output_name = self.session.get_outputs()[0].name

            logger_face_rec.info(
                f"ONNX model loaded: {self.model_name_file} "
                f"(input: {self.input_name}, output: {self.output_name})"
            )

        except ImportError:
            logger_face_rec.error("onnxruntime not installed. Install with: pip install onnxruntime")
            raise
        except FileNotFoundError as e:
            logger_face_rec.error(f"ONNX model file not found: {e}")
            raise
        except Exception as e:
            logger_face_rec.error(f"Failed to load ONNX model: {e}")
            raise

    def generate_embedding(self, face_frame: np.ndarray) -> np.ndarray:
        """
        Generate embedding using ONNX model.

        Args:
            face_frame: Cropped face region (BGR image, aligned)

        Returns:
            Embedding vector (typically 512D)
        """
        if self.session is None:
            raise RuntimeError("Model not loaded. Call load() first.")

        try:
            import cv2

            # Preprocess: WebFace R50 expects 112x112 RGB input
            # Normalize to [-1, 1] or similar depending on model training
            face_rgb = cv2.cvtColor(face_frame, cv2.COLOR_BGR2RGB)
            face_resized = cv2.resize(face_rgb, (112, 112))

            # Normalize: subtract mean and divide by std (common for face models)
            # Values: mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5] -> normalize to [-1, 1]
            face_normalized = (face_resized.astype(np.float32) / 255.0 - 0.5) / 0.5

            # Add batch dimension and transpose to CHW format (C, H, W)
            input_data = np.transpose(face_normalized, (2, 0, 1))
            input_data = np.expand_dims(input_data, axis=0).astype(np.float32)

            # Run inference
            outputs = self.session.run(
                [self.output_name],
                {self.input_name: input_data}
            )

            # Extract embedding
            embedding = outputs[0][0].astype(np.float32)

            logger_face_rec.debug(f"Generated ONNX embedding: {embedding.shape}")
            return embedding

        except Exception as e:
            logger_face_rec.error(f"ONNX embedding generation failed: {e}")
            raise

    @property
    def embedding_dimension(self) -> int:
        """WebFace R50 produces 512D embeddings."""
        return self._embedding_dim

    @property
    def model_name(self) -> str:
        """Return backend name."""
        return "onnx"


class MockBackend(EmbeddingModelBackend):
    """Mock backend for testing without real models."""

    def __init__(self, dimension: int = 512):
        """Initialize mock backend."""
        self._embedding_dim = dimension

    def load(self):
        """Mock load - no-op."""
        logger_face_rec.info("Mock embedding backend loaded")

    def generate_embedding(self, face_frame: np.ndarray) -> np.ndarray:
        """Generate random embedding for testing."""
        return np.random.randn(self._embedding_dim).astype(np.float32)

    @property
    def embedding_dimension(self) -> int:
        """Return mock dimension."""
        return self._embedding_dim

    @property
    def model_name(self) -> str:
        """Return backend name."""
        return "mock"


def get_embedding_backend(
    backend_type: str = "arcface",
    model_dir: str = "models",
    runtime_config=None
) -> EmbeddingModelBackend:
    """
    Factory function to get embedding backend.

    Args:
        backend_type: "arcface" (default), "tensorflow", "onnx", or "mock"
        model_dir: Model directory for TensorFlow/ONNX backends
        runtime_config: Runtime config for CPU/TPU detection

    Returns:
        EmbeddingModelBackend instance
    """
    if backend_type == "arcface":
        return ArcFaceBackend()
    elif backend_type == "tensorflow":
        return TensorFlowBackend(model_dir, runtime_config)
    elif backend_type == "onnx":
        return ONNXBackend(model_dir, model_name="webface_r50.onnx")
    elif backend_type == "mock":
        return MockBackend()
    else:
        raise ValueError(f"Unknown backend: {backend_type}")
