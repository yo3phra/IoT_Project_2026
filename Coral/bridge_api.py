from flask import Flask, request, jsonify
from auth_controller import AuthenticationController
from camera_interface import get_camera
import cv2, base64, threading, time, os

app = Flask(__name__)
auth_controller = AuthenticationController(mock_mode=False)

latest_result = {
    "status": "idle",
    "user": None,
    "confidence": 0,
    "timestamp": None
}

@app.route("/alert", methods=["POST"])
def receive_alert():
    """Bridge sends signal if on alrt"""
    data = request.json
    threading.Thread(target=run_authentication).start()
    return jsonify({"received": True, "msg": "Authentication started"})

@app.route("/result", methods=["GET"])
def get_result():
    """Bridge poll endpoint for result"""
    return jsonify(latest_result)

@app.route("/image", methods=["GET"])
def get_latest_image():
    """Return last saved image in base64"""
    img_path = "data/latest_alert.jpg"
    if not os.path.exists(img_path):
        return jsonify({"image": None})
    with open(img_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode()
    return jsonify({"image": encoded, "format": "jpeg"})

def run_authentication():
    global latest_result
    latest_result["status"] = "processing"
    try:
        result = auth_controller.authenticate()
        if result.success:
            latest_result = {
                "status": "authorized",
                "user": result.user_id,
                "confidence": result.confidence,
                "timestamp": time.time()
            }
        else:
            latest_result = {
                "status": "unauthorized",
                "user": None,
                "confidence": result.confidence,
                "timestamp": time.time()
            }
            # Save images
            save_alert_image()
    except Exception as e:
        latest_result = {"status": "error", "error": str(e)}

def save_alert_image():
    cam = get_camera()
    cam.open()
    frame, _ = cam.get_frame()
    cv2.imwrite("data/latest_alert.jpg", frame)
    cam.close()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)