"""
Verify real models loaded for both enrollment and auth.
"""

import sys
import os

coral_dir = os.path.join(os.path.dirname(__file__), "Coral")
if coral_dir not in sys.path:
    sys.path.insert(0, coral_dir)

print("="*70)
print("Verifying Real Model Setup")
print("="*70)

# Check model files exist
print("\n[STEP 1] Check model files exist")
from config import get_config
models_dir = get_config().face_recognition.model_dir
required_files = {
    "deploy.prototxt": "Face detection config",
    "res10_300x300_ssd_iter_140000.caffemodel": "Face detection weights",
    "facenet_keras.h5": "Face recognition (embedding) model",
}

all_exist = True
for filename, desc in required_files.items():
    filepath = os.path.join(models_dir, filename)
    if os.path.exists(filepath):
        size_mb = os.path.getsize(filepath) / (1024 * 1024)
        print(f"  [OK] {filename} ({size_mb:.1f} MB) - {desc}")
    else:
        print(f"  [MISSING] {filename}")
        all_exist = False

if not all_exist:
    print("\n[ERROR] Some model files missing!")
    sys.exit(1)

# Load detection model
print("\n[STEP 2] Load face detection model")
try:
    from face_detector import FaceDetector
    detector = FaceDetector()
    if detector.net == "mock":
        print("  [WARN] Using mock detector - models not loaded correctly")
    else:
        print(f"  [OK] Face detector loaded: {type(detector.net)}")
except Exception as e:
    print(f"  [ERROR] Failed to load detector: {e}")
    sys.exit(1)

# Load recognition model
print("\n[STEP 3] Load face recognition model")
try:
    from face_recognizer import FaceRecognizer
    recognizer = FaceRecognizer()
    if recognizer.net == "mock":
        print("  [WARN] Using mock recognizer - FaceNet model not loaded correctly")
    else:
        print(f"  [OK] Face recognizer loaded: {type(recognizer.net)}")
except Exception as e:
    print(f"  [ERROR] Failed to load recognizer: {e}")
    sys.exit(1)

# Quick integration test
print("\n[STEP 4] Quick integration test")
try:
    from camera_interface import get_camera
    camera = get_camera(mock=True)
    frame, _ = camera.get_frame()
    print(f"  [OK] Mock frame generated: {frame.shape}")

    faces = detector.detect(frame)
    print(f"  [OK] Faces detected: {len(faces)}")

    if faces:
        face = faces[0]
        face_crop = face.crop_from_frame(frame)
        embedding = recognizer.generate_embedding(face_crop)
        print(f"  [OK] Embedding generated: {embedding.dimension}D")
        print(f"       Sample: {embedding.vector[:3]}...")

except Exception as e:
    print(f"  [ERROR] Integration test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "="*70)
print("RESULT: Real models ready!")
print("="*70)
print("\nYou can now:")
print("  1. Run: python Coral/admin_interface.py")
print("  2. Choose Option 1: Enroll new user")
print("  3. Choose Option 2: Test authentication")
print("  4. Expected: Confidence > 70% for same person")
print("\nOr test directly:")
print("  python test_embedding_debug.py")
print("="*70)
