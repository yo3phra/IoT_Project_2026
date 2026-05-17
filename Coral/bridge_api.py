from flask import Flask, request, jsonify, render_template_string
from auth_controller import AuthenticationController, AuthenticationResult
from enrollment_controller import EnrollmentController, EnrollmentResult
from embedding_store import EmbeddingStore
from user_manager import UserManager
from camera_interface import get_camera
from camera_display import CameraDisplay
from cloud_signaling_interface import CloudInterface
from face_detector import get_face_detector
from config import get_config
from logger import logger_admin
import cv2, base64, threading, time, os
from datetime import datetime

app = Flask(__name__)
auth_controller = AuthenticationController(mock_mode=False)
# CloudInterface uses config-driven mock mode by default; no explicit False here
cloud_interface = CloudInterface()

embedding_store = EmbeddingStore()
enrollment_controller = EnrollmentController(embedding_store=embedding_store, mock_mode=False)
user_manager = UserManager(embedding_store)

latest_result = {
    "status": "idle",
    "user": None,
    "confidence": 0,
    "timestamp": None
}

enrollment_status = {
    "state": "idle",
    "user_id": None,
    "username": None,
    "progress": "0/0",
    "embeddings_target": get_config().enrollment_controller.embeddings_per_user
}

alert_history = []
result_history = []
enrollment_history = []

@app.route("/", methods=["GET"])
def admin_dashboard():
    """Admin dashboard page"""
    return render_template_string(DASHBOARD_HTML,
                                  current_status=latest_result,
                                  alert_count=len(alert_history),
                                  result_count=len(result_history))

@app.route("/alerts", methods=["GET"])
def alerts_page():
    """Alert history page"""
    return render_template_string(ALERTS_HTML, alerts=alert_history[-50:])

@app.route("/results", methods=["GET"])
def results_page():
    """Result history page"""
    return render_template_string(RESULTS_HTML, results=result_history[-50:])

@app.route("/enroll", methods=["GET"])
def enroll_page():
    """Enrollment form page"""
    enrolled_users = user_manager.get_all_users()
    embeddings_target = get_config().enrollment_controller.embeddings_per_user
    return render_template_string(ENROLL_HTML,
                                  enrolled_users=enrolled_users,
                                  embeddings_target=embeddings_target,
                                  current_status=enrollment_status)

@app.route("/enroll/start", methods=["POST"])
def start_enrollment():
    """Start new user enrollment"""
    data = request.json or {}
    user_id = data.get("user_id", "").strip()
    username = data.get("username", "").strip()

    if not user_id or not username:
        return jsonify({"error": "user_id and username required"}), 400

    if user_manager.get_all_users() and any(u["user_id"] == user_id for u in user_manager.get_all_users()):
        return jsonify({"error": f"User {user_id} already exists"}), 400

    try:
        session_id = enrollment_controller.start_enrollment(user_id, username)
        enrollment_status.update({
            "state": "in_progress",
            "user_id": user_id,
            "username": username,
            "progress": "0/{}".format(get_config().enrollment_controller.embeddings_per_user),
            "session_id": session_id
        })
        threading.Thread(target=run_enrollment).start()
        return jsonify({"received": True, "session_id": session_id}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/enroll/status", methods=["GET"])
def get_enrollment_status():
    """Get current enrollment status"""
    if enrollment_controller.current_session:
        status = enrollment_controller.get_session_status()
        return jsonify(status), 200
    return jsonify(enrollment_status), 200

@app.route("/enroll/cancel", methods=["POST"])
def cancel_enrollment():
    """Cancel ongoing enrollment"""
    if enrollment_controller.current_session:
        enrollment_controller.cancel_enrollment()
        enrollment_status.update({"state": "idle", "user_id": None, "username": None})
        return jsonify({"received": True, "msg": "Enrollment cancelled"}), 200
    return jsonify({"error": "No active enrollment"}), 400

@app.route("/alert", methods=["POST"])
def receive_alert():
    """Physical button press triggers auth session"""
    data = request.json or {}
    alert_entry = {
        "timestamp": datetime.now().isoformat(),
        "source": data.get("source", "button")
    }
    alert_history.append(alert_entry)
    threading.Thread(target=run_authentication).start()
    return jsonify({"received": True, "msg": "Authentication started"})

@app.route("/trigger-alert", methods=["POST"])
def trigger_alert_manual():
    """Manual trigger for testing"""
    alert_entry = {
        "timestamp": datetime.now().isoformat(),
        "source": "manual"
    }
    alert_history.append(alert_entry)
    threading.Thread(target=run_authentication).start()
    return jsonify({"received": True, "msg": "Manual alert triggered"})

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

    camera = None
    display = None
    try:
        # Open and start camera
        camera = auth_controller.camera
        if not camera.is_running:
            camera.open()
            camera.start_capture()

        # Initialize display
        config = get_config()
        display = CameraDisplay(width=config.camera.width, height=config.camera.height)
        face_detector = get_face_detector(mock=False)

        # Start session
        session_id = auth_controller.start_authentication()
        cloud_interface.send_auth_started(session_id)

        # Process frames until completion or timeout
        timeout_sec = get_config().auth_controller.total_timeout_sec
        start_time = time.time()

        while time.time() - start_time < timeout_sec:
            try:
                frame, _ = camera.get_frame_no_wait() or camera.get_frame(timeout_sec=0.1)
            except Exception:
                continue

            status = auth_controller.process_frame((frame, 0))

            # Display with overlay
            try:
                faces = face_detector.detect(frame)
                user_id = status.get("user_id", "?")
                confidence = status.get("confidence", 0.0)
                challenge = status.get("current_challenge", "")
                error = status.get("error_code", "")

                text_lines = []
                if user_id == "?":
                    text_lines.append("Detecting face... Keep steady")
                elif not status.get("liveness_on_going"):
                    text_lines.append(f"Recognized: {user_id}")
                    text_lines.append(f"Confidence: {confidence:.1%}")
                else:
                    text_lines.append("Liveness challenge:")
                    text_lines.append(challenge if challenge else "Processing...")

                debug_info = f"Faces: {len(faces)} | Error: {error if error else 'None'}"

                display.show_frame(
                    frame,
                    title="AUTHENTICATION: Face Recognition",
                    faces=faces,
                    text_lines=text_lines,
                    progress_text=f"User: {user_id} | Conf: {confidence:.1%}",
                    debug_text=debug_info
                )
                display.wait_key(1)

                if display.closed:
                    break
            except Exception as e:
                logger_admin.debug(f"Display error: {e}")

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

                result_entry = {
                    "timestamp": datetime.fromtimestamp(latest_result["timestamp"]).isoformat(),
                    "status": latest_result["status"],
                    "user": latest_result["user"],
                    "confidence": latest_result["confidence"]
                }
                result_history.append(result_entry)

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
            result_entry = {
                "timestamp": datetime.fromtimestamp(latest_result["timestamp"]).isoformat(),
                "status": "timeout",
                "user": None,
                "confidence": 0
            }
            result_history.append(result_entry)
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
    finally:
        if display:
            display.close()
        if camera and camera.is_running:
            camera.stop_capture()
            camera.close()

def run_enrollment():
    """Full enrollment pipeline: capture face embeddings for new user"""
    global enrollment_status

    camera = None
    display = None
    try:
        # Open and start camera
        camera = enrollment_controller.camera
        if not camera.is_running:
            camera.open()
            camera.start_capture()

        # Initialize display
        config = get_config()
        display = CameraDisplay(width=config.camera.width, height=config.camera.height)
        face_detector = get_face_detector(mock=False)

        # Process frames until completion or timeout
        timeout_sec = get_config().enrollment_controller.total_timeout_sec
        start_time = time.time()
        session = enrollment_controller.current_session

        while time.time() - start_time < timeout_sec and session.result.value == "in_progress":
            try:
                frame, _ = camera.get_frame_no_wait() or camera.get_frame(timeout_sec=0.1)
            except Exception:
                continue

            status = enrollment_controller.capture_enrollment_frame((frame, 0))

            # Display with overlay
            try:
                faces = face_detector.detect(frame)
                embeddings_cap = status.get("embeddings_captured", 0)
                embeddings_target = status.get("embeddings_target", 0)
                progress = status.get("progress", "0/0")
                error = status.get("error_code", "")

                text_lines = [
                    f"Embeddings: {progress}",
                    "Keep face steady, move slightly for different angles"
                ]

                debug_info = f"Faces: {len(faces)} | Frames: {status.get('frames_processed', 0)}"
                if error:
                    debug_info += f" | Error: {error}"

                display.show_frame(
                    frame,
                    title="ENROLLMENT: Capture Face",
                    faces=faces,
                    text_lines=text_lines,
                    progress_text=f"Capturing embeddings: {progress}",
                    debug_text=debug_info
                )
                display.wait_key(1)

                if display.closed:
                    break
            except Exception as e:
                logger_admin.debug(f"Display error: {e}")

            # Update enrollment status
            enrollment_status.update({
                "progress": status.get("progress", "0/0"),
                "state": status.get("state", "in_progress")
            })

            # Check for completion
            if status.get("state") == "success":
                enrollment_status.update({"state": "success"})
                enrollment_history.append({
                    "timestamp": datetime.now().isoformat(),
                    "user_id": session.user_id,
                    "username": session.username,
                    "embeddings": status.get("embeddings_captured", 0)
                })
                break

            if status.get("state") == "failure":
                enrollment_status.update({"state": "failure", "error": status.get("error_code", "Unknown")})
                break

            time.sleep(1.0 / config.camera.fps)

        else:
            # Timeout
            enrollment_controller.end_enrollment()
            enrollment_status.update({"state": "timeout"})

    except Exception as e:
        logger_admin.error(f"Enrollment pipeline error: {e}")
        enrollment_status.update({"state": "error", "error": str(e)})
    finally:
        if display:
            display.close()
        if camera and camera.is_running:
            camera.stop_capture()
            camera.close()

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

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Coral Admin Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f5f7fa; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        header { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h1 { color: #333; margin-bottom: 10px; }
        .nav { display: flex; gap: 20px; margin-top: 15px; }
        .nav a { text-decoration: none; color: #0066cc; font-weight: 500; }
        .nav a:hover { text-decoration: underline; }

        .status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 20px; }
        .status-card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .status-card h3 { color: #666; font-size: 14px; margin-bottom: 10px; text-transform: uppercase; }
        .status-value { font-size: 28px; font-weight: bold; color: #333; }
        .status-value.authorized { color: #28a745; }
        .status-value.unauthorized { color: #dc3545; }
        .status-value.processing { color: #ffc107; }

        .timestamp { font-size: 12px; color: #999; margin-top: 10px; }

        .controls { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px; }
        .controls h2 { color: #333; margin-bottom: 15px; font-size: 18px; }
        button { background: #0066cc; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; font-size: 14px; font-weight: 500; }
        button:hover { background: #0052a3; }
        button:active { opacity: 0.8; }

        .image-preview { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); text-align: center; }
        .image-preview h2 { color: #333; margin-bottom: 15px; font-size: 18px; }
        .image-preview img { max-width: 100%; max-height: 400px; border-radius: 4px; }
        .image-preview .no-image { color: #999; font-style: italic; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Coral Authentication Admin</h1>
            <div class="nav">
                <a href="/">Dashboard</a>
                <a href="/alerts">Alerts ({{ alert_count }})</a>
                <a href="/results">Results ({{ result_count }})</a>
                <a href="/enroll">Enroll User</a>
            </div>
        </header>

        <div class="status-grid">
            <div class="status-card">
                <h3>Current Status</h3>
                <div class="status-value {{ current_status.status }}">{{ current_status.status.upper() }}</div>
                <div class="timestamp" id="timestamp"></div>
            </div>

            <div class="status-card">
                <h3>Current User</h3>
                <div class="status-value">{{ current_status.user or "-" }}</div>
            </div>

            <div class="status-card">
                <h3>Confidence</h3>
                <div class="status-value">{{ "%.2f"|format(current_status.confidence) }}</div>
            </div>
        </div>

        <div class="controls">
            <h2>Manual Testing</h2>
            <button onclick="triggerAlert()">Trigger Alert</button>
            <button onclick="autoRefresh()" id="refresh-btn">Auto-Refresh: OFF</button>
        </div>

        <div class="image-preview">
            <h2>Latest Alert Image</h2>
            <div id="image-container">
                <p class="no-image">Loading...</p>
            </div>
        </div>
    </div>

    <script>
        let autoRefreshEnabled = false;

        function updateTimestamp() {
            const ts = {{ current_status.timestamp }};
            if (ts) {
                const d = new Date(ts * 1000);
                document.getElementById('timestamp').textContent = d.toLocaleString();
            }
        }

        function loadImage() {
            fetch('/image').then(r => r.json()).then(data => {
                const container = document.getElementById('image-container');
                if (data.image) {
                    container.innerHTML = '<img src="data:image/' + data.format + ';base64,' + data.image + '">';
                } else {
                    container.innerHTML = '<p class="no-image">No alert image available</p>';
                }
            });
        }

        function triggerAlert() {
            fetch('/trigger-alert', {method: 'POST'}).then(() => {
                alert('Alert triggered');
                if (autoRefreshEnabled) {
                    setTimeout(location.reload.bind(location), 500);
                }
            });
        }

        function autoRefresh() {
            autoRefreshEnabled = !autoRefreshEnabled;
            document.getElementById('refresh-btn').textContent = 'Auto-Refresh: ' + (autoRefreshEnabled ? 'ON' : 'OFF');
            if (autoRefreshEnabled) {
                setInterval(location.reload.bind(location), 2000);
            }
        }

        updateTimestamp();
        loadImage();
    </script>
</body>
</html>
"""

ALERTS_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Alert History - Coral Admin</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f5f7fa; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        header { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h1 { color: #333; margin-bottom: 10px; }
        .nav { display: flex; gap: 20px; margin-top: 15px; }
        .nav a { text-decoration: none; color: #0066cc; font-weight: 500; }
        .nav a:hover { text-decoration: underline; }

        table { width: 100%; background: white; border-collapse: collapse; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        thead { background: #f8f9fa; }
        th, td { padding: 12px 16px; text-align: left; border-bottom: 1px solid #e0e0e0; }
        th { font-weight: 600; color: #333; }
        tr:hover { background: #f8f9fa; }

        .source-badge { display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 500; }
        .source-badge.button { background: #e3f2fd; color: #1976d2; }
        .source-badge.manual { background: #fff3e0; color: #f57c00; }

        .empty { text-align: center; padding: 40px; color: #999; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Alert History</h1>
            <div class="nav">
                <a href="/">Dashboard</a>
                <a href="/alerts">Alerts</a>
                <a href="/results">Results</a>
            </div>
        </header>

        {% if alerts %}
        <table>
            <thead>
                <tr>
                    <th>Timestamp</th>
                    <th>Source</th>
                </tr>
            </thead>
            <tbody>
                {% for alert in alerts | reverse %}
                <tr>
                    <td>{{ alert.timestamp }}</td>
                    <td><span class="source-badge {{ alert.source }}">{{ alert.source.upper() }}</span></td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}
        <div class="empty">No alerts yet</div>
        {% endif %}
    </div>
</body>
</html>
"""

RESULTS_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Result History - Coral Admin</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f5f7fa; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        header { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h1 { color: #333; margin-bottom: 10px; }
        .nav { display: flex; gap: 20px; margin-top: 15px; }
        .nav a { text-decoration: none; color: #0066cc; font-weight: 500; }
        .nav a:hover { text-decoration: underline; }

        table { width: 100%; background: white; border-collapse: collapse; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        thead { background: #f8f9fa; }
        th, td { padding: 12px 16px; text-align: left; border-bottom: 1px solid #e0e0e0; }
        th { font-weight: 600; color: #333; }
        tr:hover { background: #f8f9fa; }

        .status-badge { display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 500; }
        .status-badge.authorized { background: #e8f5e9; color: #2e7d32; }
        .status-badge.unauthorized { background: #ffebee; color: #c62828; }
        .status-badge.timeout { background: #fff3e0; color: #e65100; }
        .status-badge.processing { background: #e0f2f1; color: #00695c; }

        .empty { text-align: center; padding: 40px; color: #999; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Result History</h1>
            <div class="nav">
                <a href="/">Dashboard</a>
                <a href="/alerts">Alerts</a>
                <a href="/results">Results</a>
            </div>
        </header>

        {% if results %}
        <table>
            <thead>
                <tr>
                    <th>Timestamp</th>
                    <th>Status</th>
                    <th>User</th>
                    <th>Confidence</th>
                </tr>
            </thead>
            <tbody>
                {% for result in results | reverse %}
                <tr>
                    <td>{{ result.timestamp }}</td>
                    <td><span class="status-badge {{ result.status }}">{{ result.status.upper() }}</span></td>
                    <td>{{ result.user or "-" }}</td>
                    <td>{{ "%.2f"|format(result.confidence) }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}
        <div class="empty">No results yet</div>
        {% endif %}
    </div>
</body>
</html>
"""

ENROLL_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Enroll User - Coral Admin</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f5f7fa; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        header { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h1 { color: #333; margin-bottom: 10px; }
        .nav { display: flex; gap: 20px; margin-top: 15px; }
        .nav a { text-decoration: none; color: #0066cc; font-weight: 500; }
        .nav a:hover { text-decoration: underline; }

        .form-section { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px; }
        .form-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; color: #333; font-weight: 500; }
        input[type="text"] { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; }
        input[type="text"]:focus { outline: none; border-color: #0066cc; }
        .form-hint { font-size: 12px; color: #999; margin-top: 3px; }
        button { background: #0066cc; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; font-size: 14px; font-weight: 500; }
        button:hover { background: #0052a3; }
        button:disabled { background: #ccc; cursor: not-allowed; }

        .status-box { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px; }
        .status-title { font-weight: 600; margin-bottom: 10px; }
        .status-bar { background: #f0f0f0; height: 24px; border-radius: 4px; overflow: hidden; }
        .status-fill { background: #28a745; height: 100%; display: flex; align-items: center; justify-content: center; color: white; font-size: 12px; font-weight: 600; }
        .status-text { font-size: 14px; color: #666; margin-top: 10px; }

        .enrolled-users { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .enrolled-users h3 { color: #333; margin-bottom: 15px; }
        .users-list { display: grid; gap: 10px; }
        .user-item { padding: 10px; background: #f8f9fa; border-radius: 4px; border-left: 3px solid #0066cc; }
        .user-id { font-weight: 600; color: #333; }
        .user-embeddings { font-size: 12px; color: #999; }
        .no-users { color: #999; font-style: italic; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Enroll New User</h1>
            <div class="nav">
                <a href="/">Dashboard</a>
                <a href="/alerts">Alerts</a>
                <a href="/results">Results</a>
                <a href="/enroll">Enroll User</a>
            </div>
        </header>

        <div class="form-section" id="form-section">
            <h2 style="margin-bottom: 20px; color: #333;">User Information</h2>
            <form id="enroll-form">
                <div class="form-group">
                    <label for="user_id">User ID *</label>
                    <input type="text" id="user_id" name="user_id" placeholder="e.g., user_001" required>
                    <div class="form-hint">Unique identifier for this user</div>
                </div>

                <div class="form-group">
                    <label for="username">Username *</label>
                    <input type="text" id="username" name="username" placeholder="e.g., John Doe" required>
                    <div class="form-hint">Display name for the user</div>
                </div>

                <button type="button" onclick="startEnrollment()" id="start-btn">Start Enrollment</button>
                <button type="button" onclick="location.reload()" id="reset-btn" style="background: #6c757d; margin-left: 10px;">Reset</button>
            </form>
        </div>

        <div class="status-box" id="status-section" style="display: none;">
            <div class="status-title">Enrollment Progress</div>
            <div class="status-bar">
                <div class="status-fill" id="progress-bar" style="width: 0%">0/{{ embeddings_target }}</div>
            </div>
            <div class="status-text" id="status-text">Initializing camera...</div>
            <button type="button" onclick="cancelEnrollment()" style="margin-top: 15px; background: #dc3545;">Cancel Enrollment</button>
        </div>

        <div class="enrolled-users">
            <h3>Enrolled Users ({{ enrolled_users|length }})</h3>
            {% if enrolled_users %}
            <div class="users-list">
                {% for user in enrolled_users %}
                <div class="user-item">
                    <div class="user-id">{{ user.username }} ({{ user.user_id }})</div>
                    <div class="user-embeddings">{{ user.embeddings|default(0) }} embeddings</div>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <p class="no-users">No users enrolled yet</p>
            {% endif %}
        </div>
    </div>

    <script>
        let currentEnrollment = null;
        let statusCheckInterval = null;

        function startEnrollment() {
            const userId = document.getElementById('user_id').value.trim();
            const username = document.getElementById('username').value.trim();

            if (!userId || !username) {
                alert('User ID and Username are required');
                return;
            }

            const formData = { user_id: userId, username: username };

            fetch('/enroll/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(formData)
            }).then(r => r.json()).then(data => {
                if (data.error) {
                    alert('Error: ' + data.error);
                    return;
                }

                currentEnrollment = data.session_id;
                document.getElementById('form-section').style.display = 'none';
                document.getElementById('status-section').style.display = 'block';
                updateEnrollmentStatus();
                statusCheckInterval = setInterval(updateEnrollmentStatus, 500);
            }).catch(e => alert('Failed to start enrollment: ' + e));
        }

        function updateEnrollmentStatus() {
            fetch('/enroll/status').then(r => r.json()).then(data => {
                const progress = data.progress || '0/{{ embeddings_target }}';
                const [current, target] = progress.split('/').map(Number);
                const percentage = (current / target) * 100;

                document.getElementById('progress-bar').style.width = percentage + '%';
                document.getElementById('progress-bar').textContent = progress;

                let statusText = '';
                if (data.state === 'in_progress') {
                    statusText = `Capturing embeddings... (${progress})`;
                } else if (data.state === 'success') {
                    statusText = 'Enrollment complete! User successfully enrolled.';
                    clearInterval(statusCheckInterval);
                    setTimeout(() => location.reload(), 2000);
                } else if (data.state === 'failure') {
                    statusText = 'Enrollment failed: ' + (data.error || 'Unknown error');
                    clearInterval(statusCheckInterval);
                } else if (data.state === 'timeout') {
                    statusText = 'Enrollment timeout';
                    clearInterval(statusCheckInterval);
                }

                document.getElementById('status-text').textContent = statusText;
            });
        }

        function cancelEnrollment() {
            if (confirm('Cancel this enrollment?')) {
                fetch('/enroll/cancel', { method: 'POST' }).then(() => {
                    clearInterval(statusCheckInterval);
                    location.reload();
                });
            }
        }
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)