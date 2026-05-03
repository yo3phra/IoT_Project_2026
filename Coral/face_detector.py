"""
Face detection module - detects faces in frames and returns bounding boxes.
Supports both CPU and TPU inference.
"""

import cv2
import numpy as np
from typing import List, Tuple, Optional
import os
from config import get_config
from logger import logger_face_det
from errors import FaceDetectionError, ModelError


class Face:
    """Represents a detected face."""

    def __init__(
        self,
        bbox: Tuple[int, int, int, int],
        confidence: float,
        frame_id: int
    ):
        """
        Args:
            bbox: Bounding box (x1, y1, x2, y2)
            confidence: Detection confidence [0, 1]
            frame_id: ID of frame where detected
        """
        self.x1, self.y1, self.x2, self.y2 = bbox
        self.confidence = confidence
        self.frame_id = frame_id
        self.width = self.x2 - self.x1
        self.height = self.y2 - self.y1
        self.area = self.width * self.height

    @property
    def bbox(self) -> Tuple[int, int, int, int]:
        """Return bounding box."""
        return (self.x1, self.y1, self.x2, self.y2)

    @property
    def center(self) -> Tuple[int, int]:
        """Return center coordinates."""
        return (
            int((self.x1 + self.x2) / 2),
            int((self.y1 + self.y2) / 2)
        )

    def crop_from_frame(self, frame: np.ndarray) -> np.ndarray:
        """Extract face region from frame."""
        return frame[self.y1:self.y2, self.x1:self.x2]

    def __repr__(self):
        return f"Face(bbox={self.bbox}, conf={self.confidence:.2f})"


class FaceDetector:
    """
    Face detection using OpenCV DNN or TensorFlow Lite.
    Auto-selects CPU or TPU based on runtime environment.
    """

    def __init__(self):
        """Initialize face detector."""
        self.config = get_config().face_detection
        self.runtime_config = get_config()
        self.net = None
        self._load_model()

    def _load_model(self):
        """Load face detection model."""
        try:
            if self.runtime_config.is_coral:
                self._load_tpu_model()
            else:
                self._load_cpu_model()
        except Exception as e:
            logger_face_det.error(f"Model loading failed: {e}")
            raise ModelError(f"Face detection model failed to load: {e}")

    def _load_cpu_model(self):
        """Load OpenCV DNN model for CPU inference."""
        model_dir = self.config.model_dir
        prototxt = os.path.join(model_dir, "deploy.prototxt")
        caffemodel = os.path.join(model_dir, "res10_300x300_ssd_iter_140000.caffemodel")

        if not os.path.exists(prototxt) or not os.path.exists(caffemodel):
            logger_face_det.warning(
                f"Pre-trained models not found in {model_dir}. "
                "Using mock detector for testing."
            )
            self.net = "mock"  # Flag for mock operation
            return

        self.net = cv2.dnn.readNetFromCaffe(prototxt, caffemodel)
        logger_face_det.info("CPU face detection model loaded (OpenCV DNN)")

    def _load_tpu_model(self):
        """Load TensorFlow Lite TPU model for Coral."""
        try:
            from pycoral.adapters import detect
            from pycoral.utils.edgetpu import make_interpreter
        except ImportError:
            logger_face_det.warning("PyCoral not installed. Falling back to CPU.")
            self._load_cpu_model()
            return

        model_path = os.path.join(
            self.config.model_dir,
            "mobilenet_ssd_v2_face_quant_postprocess_edgetpu.tflite"
        )

        if not os.path.exists(model_path):
            logger_face_det.warning(f"TPU model not found at {model_path}. Falling back to CPU.")
            self._load_cpu_model()
            return

        self.net = make_interpreter(model_path)
        self.net.allocate_tensors()
        logger_face_det.info("TPU face detection model loaded (Edge TPU)")

    def detect(self, frame: np.ndarray) -> List[Face]:
        """
        Detect faces in frame.

        Args:
            frame: Input frame (numpy array)

        Returns:
            List of Face objects
        """
        if self.net is None:
            raise FaceDetectionError("Model not loaded")

        if self.net == "mock":
            return self._detect_mock(frame)

        if self.runtime_config.is_coral:
            return self._detect_tpu(frame)
        else:
            return self._detect_cpu(frame)

    def _detect_cpu(self, frame: np.ndarray) -> List[Face]:
        """Detect faces using OpenCV DNN."""
        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(
            frame,
            1.0,
            (300, 300),
            [104.0, 117.0, 123.0],
            False,
            crop=True   # crop=True to maintain aspect ratio and avoid distortion
        )

        self.net.setInput(blob)
        detections = self.net.forward()

        faces = []
        for i in range(detections.shape[2]):
            confidence = detections[0, 0, i, 2]

            if confidence < self.config.confidence_threshold:
                continue

            box = detections[0, 0, i, 3:7]
            x1 = int(box[0] * w)
            y1 = int(box[1] * h)
            x2 = int(box[2] * w)
            y2 = int(box[3] * h)

            # Clamp to frame bounds
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)

            if x2 > x1 and y2 > y1:
                face = Face((x1, y1, x2, y2), float(confidence), frame_id=0)
                faces.append(face)

        logger_face_det.debug(f"Detected {len(faces)} face(s) in frame")
        return faces

    def _detect_tpu(self, frame: np.ndarray) -> List[Face]:
        """Detect faces using Edge TPU."""
        try:
            from pycoral.adapters import detect
            from PIL import Image

            # Prepare image
            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            _, scale = detect.set_resized_input(self.net, img.size)

            # Run inference
            self.net.invoke()
            detections = detect.get_detections(
                self.net,
                score_threshold=self.config.confidence_threshold
            )

            h, w = frame.shape[:2]
            faces = []

            for detection in detections:
                bbox = detection.bbox.scale(scale)
                x1 = int(bbox.xmin * w)
                y1 = int(bbox.ymin * h)
                x2 = int(bbox.xmax * w)
                y2 = int(bbox.ymax * h)

                confidence = detection.score

                if x2 > x1 and y2 > y1:
                    face = Face((x1, y1, x2, y2), float(confidence), frame_id=0)
                    faces.append(face)

            logger_face_det.debug(f"Detected {len(faces)} face(s) via TPU")
            return faces

        except Exception as e:
            logger_face_det.error(f"TPU detection failed: {e}")
            raise FaceDetectionError(f"TPU inference failed: {e}")

    def _detect_mock(self, frame: np.ndarray) -> List[Face]:
        """Mock detection for testing."""
        h, w = frame.shape[:2]
        # Return dummy face in center
        face = Face(
            (w // 4, h // 4, 3 * w // 4, 3 * h // 4),
            0.95,
            frame_id=0
        )
        return [face]


def get_face_detector(mock: bool = False) -> Optional[FaceDetector]:
    """
    Factory function to get face detector.

    Args:
        mock: Force mock mode for testing

    Returns:
        FaceDetector instance
    """
    if mock:
        logger_face_det.info("Using mock face detector")
        detector = FaceDetector.__new__(FaceDetector)
        detector.config = get_config().face_detection
        detector.runtime_config = get_config()
        detector.net = "mock"
        return detector

    return FaceDetector()
