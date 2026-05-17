from flask import Flask, render_template, jsonify, request
from azure.cosmos import CosmosClient
import random, time, os, requests

app = Flask(__name__)

COSMOS_CONNECTION_STRING = os.environ.get("COSMOS_CONNECTION_STRING")
COSMOS_DATABASE  = "bicycle-db"
COSMOS_CONTAINER = "sensor-data"

BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://localhost:5001")

try:
    cosmos_client    = CosmosClient.from_connection_string(COSMOS_CONNECTION_STRING)
    database         = cosmos_client.get_database_client(COSMOS_DATABASE)
    container        = database.get_container_client(COSMOS_CONTAINER)
    COSMOS_OK = True
except:
    COSMOS_OK = False

def get_simulated_data():
    x = round(random.uniform(-0.3, 0.3), 3)
    y = round(random.uniform(-0.3, 0.3), 3)
    z = round(9.81 + random.uniform(-0.1, 0.1), 3)
    return {"x": x, "y": y, "z": z, "timestamp": time.strftime("%H:%M:%S"), "simulated": True}


    
def get_latest_data():
    if not COSMOS_OK:
        return get_simulated_data()
    try:
        items = list(container.query_items(
            query="SELECT TOP 1 * FROM c ORDER BY c.timestamp DESC",
            enable_cross_partition_query=True
        ))
        if not items:
            return get_simulated_data()
        raw = items[0].get("raw", {})
        decoded = raw.get("uplink_message", {}).get("decoded_payload", {})
        return {
            "x": decoded.get("x", 0),
            "y": decoded.get("y", 0),
            "z": decoded.get("z", 9.81),
            "timestamp": items[0].get("timestamp", ""),
            "device_id": items[0].get("device_id", "unknown"),
            "simulated": False
        }
    except:
        return get_simulated_data()

def get_history():
    if not COSMOS_OK:
        return [get_simulated_data() for _ in range(12)]
    try:
        items = list(container.query_items(
            query="SELECT * FROM c ORDER BY c.timestamp DESC OFFSET 0 LIMIT 20",
            enable_cross_partition_query=True
        ))
        if not items:
            return [get_simulated_data() for _ in range(12)]
        result = []
        for item in items:
            raw = item.get("raw", {})
            decoded = raw.get("uplink_message", {}).get("decoded_payload", {})
            result.append({
                "x": decoded.get("x", 0),
                "y": decoded.get("y", 0),
                "z": decoded.get("z", 9.81),
                "timestamp": item.get("timestamp", ""),
                "simulated": False
            })
        return result
    except:
        return [get_simulated_data() for _ in range(12)]

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/navigation")
def navigation():
    return render_template("navigation.html")

@app.route("/parked")
def parked():
    return render_template("parked.html")

@app.route("/api/latest")
def api_latest():
    return jsonify(get_latest_data())

@app.route("/api/history")
def api_history():
    return jsonify(get_history())

@app.route("/api/mode", methods=["POST"])
def api_set_mode():
    data = request.json or {}
    mode = data.get("mode", "").lower()

    if mode not in ["parked", "cruising"]:
        return jsonify({"error": "Invalid mode. Use 'parked' or 'cruising'."}), 400

    try:
        response = requests.post(
            f"{BRIDGE_URL}/wifi_mode",
            json={"mode": mode},
            timeout=5
        )

        return jsonify({
            "status": "sent_to_bridge",
            "mode": mode,
            "bridge_status": response.status_code,
            "bridge_response": response.json()
        }), response.status_code

    except Exception as e:
        return jsonify({
            "error": "Could not reach bridge",
            "details": str(e)
        }), 500
        
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
