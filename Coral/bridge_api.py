from flask import Flask, request, jsonify
from auth_controller import AuthenticationController, AuthenticationResult
from camera_interface import get_camera
from cloud_signaling_interface import CloudInterface
from config import get_config
from logger import logger_admin
import cv2, base64, threading, time, os

app = Flask(__name__)
auth_controller = AuthenticationController(mock_mode=False)
cloud_interface = CloudInterface(mock_mode=False)

latest_result = {
    "status": "idle",
    "user": None,
    "confidence": 0,
    "timestamp": None
}

@app.route("/alert", methods=["POST"])
def receive_alert():
    """Physical button press triggers auth session"""
    data = request.json
    threading.Thread(target=run_authentication).start()
    return jsonify({"received": True, "msg": "Authentication started"})

@app.route("/result", methods=["GET"])
def get_result():
    """Poll endpoint for auth result"""
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
    """Full auth pipeline: detect → recognize → liveness → result"""
    global latest_result
    latest_result["status"] = "processing"

    try:
        # Start session
        session_id = auth_controller.start_authentication()
        cloud_interface.send_auth_started(session_id)

        # Process frames until completion or timeout
        timeout_sec = get_config().auth_controller.total_timeout_sec
        start_time = time.time()

        while time.time() - start_time < timeout_sec:
            status = auth_controller.process_frame()

            # Send progress telemetry
            if status.get("state") in ["in_progress", "success", "failure"]:
                liveness_status = {
                    "ongoing": status.get("liveness_on_going", False),
                    "passed": status.get("liveness_passed", False),
                    "challenge": status.get("current_challenge")
                }
                confidence_bool = status.get("confidence", 0) >= get_config().face_recognition.distance_threshold
                cloud_interface.send_auth_progress(
                    session_id,
                    status.get("state", "in_progress"),
                    confidence_bool,
                    liveness_status,
                    time.time()
                )

            # Check result
            if status.get("state") != "in_progress":
                final_result = auth_controller.end_session()

                # Finalize and send result
                if final_result == AuthenticationResult.SUCCESS:
                    latest_result = {
                        "status": "authorized",
                        "user": status.get("user_id"),
                        "confidence": status.get("confidence", 0),
                        "timestamp": time.time()
                    }
                else:
                    latest_result = {
                        "status": "unauthorized",
                        "user": None,
                        "confidence": 0,
                        "timestamp": time.time()
                    }
                    save_alert_image()

                # Send result telemetry
                cloud_interface.send_auth_result(
                    session_id,
                    final_result.value,
                    status.get("user_id"),
                    status.get("confidence", 0),
                    time.time()
                )
                break

            time.sleep(1.0 / get_config().camera.fps)

        else:
            # Timeout
            auth_controller.end_session()
            latest_result = {
                "status": "timeout",
                "user": None,
                "confidence": 0,
                "timestamp": time.time()
            }
            cloud_interface.send_auth_result(
                session_id,
                AuthenticationResult.TIMEOUT.value,
                None,
                0,
                time.time()
            )

    except Exception as e:
        logger_admin.error(f"Auth pipeline error: {e}")
        latest_result = {"status": "error", "error": str(e)}

def save_alert_image():
    """Capture and save frame on failed auth"""
    try:
        cam = get_camera()
        cam.open()
        frame, _ = cam.get_frame()
        os.makedirs("data", exist_ok=True)
        cv2.imwrite("data/latest_alert.jpg", frame)
        cam.close()
    except Exception as e:
        logger_admin.error(f"Failed to save alert image: {e}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)