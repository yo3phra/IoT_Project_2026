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
    return {
        "bike_id": "demo-01",
        "event_type": "tracking",
        "lat": 51.2194,
        "lon": 4.4025,
        "speed": 0,
        "has_gps": True,
        "has_speed": True,
        "timestamp": time.strftime("%H:%M:%S"),
        "device_id": "demo-01",
        "simulated": True
    }

    
def get_latest_data():
    if not COSMOS_OK:
        return get_simulated_data()
    try:
        items = list(container.query_items(
            query="""SELECT TOP 1 * FROM c
                     WHERE c.source IN ('wifi', 'lora', 'wifi_json')
                     ORDER BY c.timestamp DESC""",
            enable_cross_partition_query=True
        ))
        item = items[0]
        decoded = item.get("raw", {}).get("uplink_message", {}).get("decoded_payload", {}) or {}
 
        return {
            "bike_id":     item.get("bike_id") or decoded.get("bike_id", "unknown"),
            "event_type":  item.get("event_type") or decoded.get("packet_type_name", "tracking"),
            "lat":         item.get("lat") or decoded.get("lat"),
            "lon":         item.get("lon") or decoded.get("lon"),
            "speed":       item.get("speed") or decoded.get("speed"),
            "has_gps":     item.get("has_gps", False),
            "has_speed":   item.get("has_speed", False),
            "timestamp":   item.get("timestamp", ""),
            "device_id":   item.get("device_id", "unknown"),
            "simulated":   False
        }
    except:
        return get_simulated_data()

def get_history():
    """Last 20 tracking packets for the map/chart."""
    if not COSMOS_OK:
        return [get_simulated_data() for _ in range(5)]
    try:
        items = list(container.query_items(
            query="""SELECT * FROM c
                     WHERE c.source IN ('wifi', 'lora', 'wifi_json')
                     ORDER BY c.timestamp DESC
                     OFFSET 0 LIMIT 20""",
            enable_cross_partition_query=True
        ))
        if not items:
            return [get_simulated_data() for _ in range(5)]
 
        result = []
        for item in items:
            decoded = item.get("raw", {}).get("uplink_message", {}).get("decoded_payload", {}) or {}
            result.append({
                "bike_id":    item.get("bike_id") or decoded.get("bike_id"),
                "event_type": item.get("event_type") or decoded.get("packet_type_name"),
                "lat":        item.get("lat") or decoded.get("lat"),
                "lon":        item.get("lon") or decoded.get("lon"),
                "speed":      item.get("speed") or decoded.get("speed"),
                "has_gps":    item.get("has_gps", False),
                "timestamp":  item.get("timestamp", ""),
                "simulated":  False
            })
        return result
    except:
        return [get_simulated_data() for _ in range(5)]
 
 
def get_latest_ml():
    """Latest Coral ML result."""
    if not COSMOS_OK:
        return get_simulated_ml()
    try:
        items = list(container.query_items(
            query="""SELECT TOP 1 * FROM c
                     WHERE c.source IN ('coral_ml', 'coral_image')
                     ORDER BY c.timestamp DESC""",
            enable_cross_partition_query=True
        ))
        if not items:
            return get_simulated_ml()
 
        item = items[0]
        return {
            "ml_status":  item.get("ml_status", "unknown"),
            "confidence": item.get("confidence"),
            "user":       item.get("user"),
            "image_url":  item.get("image_url"),
            "timestamp":  item.get("timestamp", ""),
            "simulated":  False
        }
    except:
        return get_simulated_ml()
 
 
def get_alerts():
    """Last 10 theft or crash alerts."""
    if not COSMOS_OK:
        return []
    try:
        items = list(container.query_items(
            query="""SELECT * FROM c
                     WHERE c.event_type IN ('theft_alert', 'crash_alert')
                     ORDER BY c.timestamp DESC
                     OFFSET 0 LIMIT 10""",
            enable_cross_partition_query=True
        ))
        result = []
        for item in items:
            result.append({
                "event_type": item.get("event_type"),
                "bike_id":    item.get("bike_id"),
                "lat":        item.get("lat"),
                "lon":        item.get("lon"),
                "timestamp":  item.get("timestamp", ""),
            })
        return result
    except:
        return []

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

@app.route("/api/ml")
def api_ml():
    return jsonify(get_latest_ml())
 
@app.route("/api/alerts")
def api_alerts():
    return jsonify(get_alerts())

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
