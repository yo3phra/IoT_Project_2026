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

# --------CONFIGURATION----------------

TTN_HOST     = "eu1.cloud.thethings.network"
TTN_PORT     = 1883
TTN_USERNAME = os.environ.get("TTN_USERNAME", "iotbicycle@ttn")
TTN_PASSWORD = os.environ.get("TTN_PASSWORD")
TTN_TOPIC    = "v3/iotbicycle@ttn/devices/+/up"

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


# -------CORAL : triggered only on alert---------


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

def trigger_coral_and_wait():
    """Called only when Pytrack sends alert=true. Not running permanently."""
    try:
        requests.post(f"{CORAL_HOST}/alert", json={"source": "pytrack"}, timeout=5)
        print("[CORAL] Face recognition triggered")

        # Poll until result (max 30s)
        for _ in range(15):
            time.sleep(2)
            res = requests.get(f"{CORAL_HOST}/result", timeout=3)
            result = res.json()
            status = result.get("status")

            if status not in ("idle", "processing", None):
                image_url = None

                if status == "unauthorized" and result.get("has_image"):
                    try:
                        img_res = requests.get(f"{CORAL_HOST}/image", timeout=5)
                        img_data = img_res.json()
                        if img_data.get("image"):
                            filename = f"alert_{int(time.time())}.jpg"
                            image_url = upload_image_to_blob(img_data["image"], filename)
                    except Exception as e:
                        print(f"[CORAL] Image error: {e}")

                document = {
                    "id": f"ml_{time.time()}",
                    "source": "coral_ml",
                    "device_id": "coral-dev-board",
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "ml_status": status,
                    "ml_user": result.get("user"),
                    "ml_confidence": result.get("confidence"),
                    "image_url": image_url,
                    "raw": result
                }
                cosmos_container.upsert_item(document)
                print(f"[CORAL] Result stored: {status} — user: {result.get('user')}")
                return

        print("[CORAL] Timeout — no result in 30s")

    except requests.exceptions.ConnectionError:
        print("[CORAL] Offline — skipping face recognition")
    except Exception as e:
        print(f"[CORAL] Error: {e}")


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
            threading.Thread(target=trigger_coral_and_wait, daemon=True).start()

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

@wifi_app.route("/data", methods=["POST"])
def receive_wifi_data():
    try:
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
            print("[WIFI] Alert — triggering Coral")
            threading.Thread(target=trigger_coral_and_wait, daemon=True).start()

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@wifi_app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "lora_connected": lora_connected}), 200

def start_wifi_server():
    print(f"WiFi fallback server on port {WIFI_PORT}")
    wifi_app.run(host="0.0.0.0", port=WIFI_PORT, debug=False, use_reloader=False)


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