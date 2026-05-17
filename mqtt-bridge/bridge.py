"""
bridge.py - Smart Bicycle ZOE
- Receives data from TTN (LoRaWAN) or WiFi (fallback)
- Forwards to Azure IoT Hub + CosmosDB
- Triggers Coral face recognition on alert (event-driven, not polling)
- Saves alert images to Azure Blob Storage
"""

import paho.mqtt.client as mqtt
from azure.iot.device import IoTHubDeviceClient, Message
from azure.cosmos import CosmosClient
from azure.storage.blob import BlobServiceClient
from flask import Flask, request, jsonify
import threading
import requests
import time
import json
import os
import base64
import jwt

# --------CONFIGURATION----------------

# data stored
TTN_HOST     = "eu1.cloud.thethings.network"
TTN_PORT     = 1883
TTN_USERNAME = os.environ.get("TTN_USERNAME", "iotbicycle@ttn")
TTN_PASSWORD = os.environ.get("TTN_PASSWORD")
TTN_TOPIC    = "v3/iotbicycle@ttn/devices/+/up"
TTN_APP_ID    = "iotbicycle"
TTN_DEVICE_ID = "pytrack-01"
TTN_API_KEY = os.environ.get("TTN_API_KEY")


AZURE_CONNECTION_STRING  = os.environ.get("AZURE_IOT_CONNECTION_STRING")
COSMOS_CONNECTION_STRING = os.environ.get("COSMOS_CONNECTION_STRING")
COSMOS_DATABASE  = "bicycle-db"
COSMOS_CONTAINER = "sensor-data"

BLOB_CONNECTION_STRING = os.environ.get("BLOB_CONNECTION_STRING")
BLOB_CONTAINER = "alerts"

CORAL_HOST = os.environ.get("CORAL_HOST", "http://localhost:8000")
WIFI_PORT  = 5001


# ----------INIT------------


azure_client = IoTHubDeviceClient.create_from_connection_string(AZURE_CONNECTION_STRING)
azure_client.connect()
print("Connected to Azure IoT Hub !")

cosmos_client    = CosmosClient.from_connection_string(COSMOS_CONNECTION_STRING)
database         = cosmos_client.get_database_client(COSMOS_DATABASE)
cosmos_container = database.get_container_client(COSMOS_CONTAINER)
print("Connected to CosmosDB !")

blob_service = BlobServiceClient.from_connection_string(BLOB_CONNECTION_STRING)
try:
    blob_service.create_container(BLOB_CONTAINER)
except:
    pass
print("Connected to Blob Storage !")

# ------STORE DATA-----------

def store_data(payload: dict, source: str = "lora"):
    try:
        azure_msg = Message(json.dumps(payload))
        azure_msg.content_type = "application/json"
        azure_msg.content_encoding = "utf-8"
        azure_client.send_message(azure_msg)
        print(f"[{source.upper()}] Sent to Azure IoT Hub !")

        document = {
            "id": str(time.time()),
            "source": source,
            "device_id": payload.get("end_device_ids", {}).get("device_id", "unknown"),
            "timestamp": payload.get("received_at", time.strftime("%Y-%m-%dT%H:%M:%SZ")),
            "raw": payload
        }
        cosmos_container.upsert_item(document)
        print(f"[{source.upper()}] Saved to CosmosDB !")

    except Exception as e:
        print(f"[{source.upper()}] Store error: {e}")


# -------CORAL : triggered when auth in Cloud---------


def upload_image_to_blob(image_base64: str, filename: str) -> str:
    try:
        image_bytes = base64.b64decode(image_base64)
        blob_client = blob_service.get_blob_client(container=BLOB_CONTAINER, blob=filename)
        blob_client.upload_blob(image_bytes, overwrite=True)
        url = f"https://smartbicyclestorage.blob.core.windows.net/{BLOB_CONTAINER}/{filename}"
        print(f"[BLOB] Image uploaded: {url}")
        return url
    except Exception as e:
        print(f"[BLOB] Upload error: {e}")
        return None


def check_auth_status():
    """Check last Coral ML result in CosmosDB — no direct Coral call."""
    try:
        items = list(cosmos_container.query_items(
            query="SELECT TOP 1 * FROM c WHERE c.source='coral_ml' ORDER BY c.timestamp DESC",
            enable_cross_partition_query=True
        ))
        if items:
            last = items[0]
            print(f"[CORAL] Last auth: {last.get('ml_status')} at {last.get('timestamp')}")
        else:
            print("[CORAL] No auth record found — possible theft")
    except Exception as e:
        print(f"[CORAL] Check error: {e}")


# -------LORA : TTN MQTT---------

lora_connected = False

def on_connect(client, userdata, flags, rc):
    global lora_connected
    if rc == 0:
        lora_connected = True
        print("Connected to TTN !")
        client.subscribe(TTN_TOPIC)
        print(f"Listening on: {TTN_TOPIC}")
    else:
        lora_connected = False
        print(f"TTN error, code: {rc}")

def on_disconnect(client, userdata, rc):
    global lora_connected
    lora_connected = False
    print("Disconnected from TTN — WiFi fallback active")

def on_message(client, userdata, msg):
    print(f"\n[LORA] Message received!")
    try:
        payload = json.loads(msg.payload.decode())
        store_data(payload, source="lora")

        decoded = payload.get("uplink_message", {}).get("decoded_payload", {})
        if decoded.get("alert") == True:
            print("[LORA] Alert flag detected — triggering Coral")
            threading.Thread(target=check_auth_status, daemon=True).start()

    except Exception as e:
        print(f"[LORA] Error: {e}")

def start_lora_client():
    client = mqtt.Client()
    client.username_pw_set(TTN_USERNAME, TTN_PASSWORD)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    while True:
        try:
            print("Connecting to TTN...")
            client.connect(TTN_HOST, TTN_PORT, keepalive=60)
            client.loop_forever()
        except Exception as e:
            print(f"TTN failed: {e} — retrying in 10s")
            time.sleep(10)


# ---------WIFI FALLBACK-----------

wifi_app = Flask(__name__)
JWT_SECRET = os.environ.get("JWT_SECRET", "zoe-secret-2026")
def verify_jwt(req):
    token = req.headers.get("Authorization", "").replace("Bearer ", "")
    try:
        jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return True
    except:
        return False
    
@wifi_app.route("/data", methods=["POST"])
def receive_wifi_data():
    try:
        if not verify_jwt(request):  
            return jsonify({"error": "Unauthorized"}), 401
        data = request.json
        if not data:
            return jsonify({"error": "No JSON"}), 400

        print(f"\n[WIFI] Data received: {data}")

        payload = {
            "end_device_ids": {"device_id": data.get("device_id", "pycom-wifi")},
            "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "uplink_message": {
                "decoded_payload": {
                    "x": data.get("x", 0),
                    "y": data.get("y", 0),
                    "z": data.get("z", 9.81),
                    "alert": data.get("alert", False)
                }
            }
        }

        store_data(payload, source="wifi")

        if data.get("alert") == True:
            print("[WIFI] Alert — Check Auth Status")
            threading.Thread(target=check_auth_status, daemon=True).start()

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@wifi_app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "lora_connected": lora_connected}), 200

@wifi_app.route("/mode", methods=["POST"])
def receive_mode():
    data = request.json
    mode = data.get("mode")
    send_downlink(mode)
    return jsonify({"status": "ok"})

def start_wifi_server():
    wifi_app.run(host="0.0.0.0", port=WIFI_PORT,
                ssl_context=("certs/cert.pem", "certs/key.pem"),
                 debug=False, use_reloader=False)


#----------DOWNLINK UPDATE-------


def send_downlink(mode: str):
    """Send downlink message to FiPy via TTN."""
    # Encode mode as 1 byte : 0x01 = navigation, 0x02 = parked
    byte_value = 0x01 if mode == "navigation" else 0x02
    payload_b64 = base64.b64encode(bytes([byte_value])).decode()

    url = f"https://eu1.cloud.thethings.network/api/v3/as/applications/{TTN_APP_ID}/devices/{TTN_DEVICE_ID}/down/push"
    
    headers = {
        "Authorization": f"Bearer {TTN_API_KEY}",
        "Content-Type": "application/json"
    }
    
    body = {
        "downlinks": [{
            "frm_payload": payload_b64,
            "f_port": 1,
            "priority": "NORMAL"
        }]
    }
    
    try:
        res = requests.post(url, json=body, headers=headers)
        print(f"[DOWNLINK] Mode '{mode}' sent to FiPy — status: {res.status_code}")
    except Exception as e:
        print(f"[DOWNLINK] Error: {e}")

# ----------MAIN------------

if __name__ == "__main__":
    print("=== Smart Bicycle ZOE — Bridge ===")
    threading.Thread(target=start_lora_client, daemon=True).start()
    threading.Thread(target=start_wifi_server, daemon=True).start()
    print(f"  LoRa/TTN : {TTN_HOST}:{TTN_PORT}")
    print(f"  WiFi     : http://0.0.0.0:{WIFI_PORT}/data")
    print(f"  Coral    : {CORAL_HOST} (on-demand)")
    print(f"  Blob     : smartbicyclestorage/{BLOB_CONTAINER}")
    while True:
        time.sleep(60)