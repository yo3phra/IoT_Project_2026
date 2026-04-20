"""
Debug test - find enrollment issue
"""

import sys
import os
import numpy as np

# Add Coral directory to path
coral_dir = os.path.join(os.path.dirname(__file__), "Coral")
if coral_dir not in sys.path:
    sys.path.insert(0, coral_dir)

from camera_interface import get_camera
from face_detector import get_face_detector
from face_recognizer import get_face_recognizer

print("Testing mock components...")

# Test camera
print("\n[1] Testing MockCamera")
camera = get_camera(mock=True)
frame, frame_id = camera.get_frame()
print(f"    Frame shape: {frame.shape}, dtype: {frame.dtype}")

# Test face detector
print("\n[2] Testing MockFaceDetector")
detector = get_face_detector(mock=True)
faces = detector.detect(frame)
print(f"    Faces detected: {len(faces)}")
if faces:
    face = faces[0]
    print(f"    Face bbox: {face.bbox}")
    face_crop = face.crop_from_frame(frame)
    print(f"    Crop shape: {face_crop.shape}")

# Test face recognizer
print("\n[3] Testing MockFaceRecognizer")
try:
    recognizer = get_face_recognizer(mock=True)
    if faces:
        embedding = recognizer.generate_embedding(face_crop)
        print(f"    Embedding: {embedding}")
        if hasattr(embedding, 'vector'):
            print(f"    Embedding vector shape: {embedding.vector.shape}")
            print(f"    Embedding vector: {embedding.vector[:5]}...")
        else:
            print(f"    Embedding is type: {type(embedding)}")
    else:
        print("    [ERROR] No faces to embed")
except Exception as e:
    print(f"    [ERROR] Embedding failed: {e}")
    import traceback
    traceback.print_exc()
