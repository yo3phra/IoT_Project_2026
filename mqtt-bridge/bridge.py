"""
Smart Bicycle ZOE — Bridge

Modes:
- LOCAL_TEST=True: no Azure/Cosmos/Blob/TTN connections; packets are printed locally.
- DEMO_WIFI_ONLY=True: mode commands go to PyTrack over WiFi only.
- DEMO_WIFI_ONLY=False: try LoRa/TTN downlink first, then WiFi fallback.

Main endpoints:
- GET  /health
- POST /register       PyTrack registers its WiFi mode-command port
- POST /proto_data     PyTrack binary packet over WiFi
- POST /mode           Smart mode command: demo WiFi OR LoRa-first fallback
- POST /wifi_mode      Direct WiFi mode command
- POST /data           Legacy JSON sensor endpoint
- POST /coral/auth     Coral auth result endpoint
- POST /coral/image    Coral alert image endpoint
"""

import base64
import hashlib
import json
import os
import socket
import struct
import threading
import time
from typing import Any, Dict, Optional

import jwt
import paho.mqtt.client as mqtt
import requests
from flask import Flask, jsonify, request

try:
    from azure.iot.device import IoTHubDeviceClient, Message
except Exception:
    IoTHubDeviceClient = None
    Message = None

try:
    from azure.cosmos import CosmosClient
except Exception:
    CosmosClient = None

try:
    from azure.storage.blob import BlobServiceClient
except Exception:
    BlobServiceClient = None


# =============================================================================
# TOGGLES
# =============================================================================

# Keep both True for the demo session.
LOCAL_TEST = os.environ.get("LOCAL_TEST", "true").lower() == "true"
DEMO_WIFI_ONLY = os.environ.get("DEMO_WIFI_ONLY", "true").lower() == "true"


# =============================================================================
# CONFIGURATION
# =============================================================================

# TTN / LoRaWAN
TTN_HOST = "eu1.cloud.thethings.network"
TTN_PORT = 1883
TTN_USERNAME = os.environ.get("TTN_USERNAME", "iotbicycle@ttn")
TTN_PASSWORD = os.environ.get("TTN_PASSWORD")
TTN_TOPIC = "v3/iotbicycle@ttn/devices/+/up"
TTN_APP_ID = os.environ.get("TTN_APP_ID", "iotbicycle")
TTN_DEVICE_ID = os.environ.get("TTN_DEVICE_ID", "pytrack-01")
TTN_API_KEY = os.environ.get("TTN_API_KEY")

# Azure IoT / Cosmos / Blob
AZURE_CONNECTION_STRING = os.environ.get("AZURE_IOT_CONNECTION_STRING")
COSMOS_CONNECTION_STRING = os.environ.get("COSMOS_CONNECTION_STRING")
COSMOS_DATABASE = os.environ.get("COSMOS_DATABASE", "bicycle-db")
COSMOS_CONTAINER = os.environ.get("COSMOS_CONTAINER", "sensor-data")
BLOB_CONNECTION_STRING = os.environ.get("BLOB_CONNECTION_STRING")
BLOB_CONTAINER = os.environ.get("BLOB_CONTAINER", "alerts")
BLOB_ACCOUNT_URL = os.environ.get(
    "BLOB_ACCOUNT_URL",
    "https://smartbicyclestorage.blob.core.windows.net",
)

# Bridge / WiFi
WIFI_PORT = int(os.environ.get("WIFI_PORT", "5001"))
JWT_SECRET = os.environ.get("JWT_SECRET", "zoe-secret-2026")

# Coral
CORAL_HOST = os.environ.get("CORAL_HOST", "http://localhost:8000")

# Discovery
DISCOVERY_PORT = int(os.environ.get("DISCOVERY_PORT", "37020"))
DISCOVERY_REQUEST = b"DISCOVER_SMARTBIKE_BRIDGE_V1"
DISCOVERY_RESPONSE_PREFIX = "SMARTBIKE_BRIDGE_V1"

# Binary protocol
AUTH_KEY_PROTO = os.environ.get("PYTRACK_AUTH_KEY", "change-this-shared-key").encode()
MAC_SIZE_PROTO = 8

PACKET_THEFT_ALERT = 1
PACKET_CRASH_ALERT = 2
PACKET_TRACKING = 3

PACKET_TYPE_NAMES = {
    PACKET_THEFT_ALERT: "theft_alert",
    PACKET_CRASH_ALERT: "crash_alert",
    PACKET_TRACKING: "tracking",
}


# =============================================================================
# GLOBAL STATE
# =============================================================================

wifi_app = Flask(__name__)

azure_client = None
cosmos_client = None
cosmos_container = None
blob_service = None

lora_connected = False

pytrack_info = {
    "ip": None,
    "port": 5002,
    "device_id": None,
    "last_seen": None,
}

packet_lock = threading.Lock()
last_tracking_timestamp: Dict[int, int] = {}
last_tracking_bridge_time: Dict[int, float] = {}


# =============================================================================
# CLOUD INITIALIZATION
# =============================================================================

def init_cloud_clients() -> None:
    """Initialize Azure IoT Hub, CosmosDB, and Blob Storage when not local."""
    global azure_client, cosmos_client, cosmos_container, blob_service

    if LOCAL_TEST:
        print("[LOCAL_TEST] Azure, CosmosDB, Blob, TTN, Coral cloud checks disabled")
        return

    if IoTHubDeviceClient is None or Message is None:
        raise RuntimeError("azure-iot-device package is not available")
    if CosmosClient is None:
        raise RuntimeError("azure-cosmos package is not available")
    if BlobServiceClient is None:
        raise RuntimeError("azure-storage-blob package is not available")

    if not AZURE_CONNECTION_STRING:
        raise RuntimeError("Missing AZURE_IOT_CONNECTION_STRING")
    if not COSMOS_CONNECTION_STRING:
        raise RuntimeError("Missing COSMOS_CONNECTION_STRING")
    if not BLOB_CONNECTION_STRING:
        raise RuntimeError("Missing BLOB_CONNECTION_STRING")

    azure_client = IoTHubDeviceClient.create_from_connection_string(
        AZURE_CONNECTION_STRING
    )
    azure_client.connect()
    print("[AZURE] Connected to IoT Hub")

    cosmos_client = CosmosClient.from_connection_string(COSMOS_CONNECTION_STRING)
    database = cosmos_client.get_database_client(COSMOS_DATABASE)
    cosmos_container = database.get_container_client(COSMOS_CONTAINER)
    print("[COSMOS] Connected")

    blob_service = BlobServiceClient.from_connection_string(BLOB_CONNECTION_STRING)
    try:
        blob_service.create_container(BLOB_CONTAINER)
    except Exception:
        pass
    print("[BLOB] Connected")


# =============================================================================
# STORAGE
# =============================================================================

def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def extract_device_id(payload: Dict[str, Any]) -> str:
    return payload.get("end_device_ids", {}).get("device_id", "unknown")


def extract_decoded_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return payload.get("uplink_message", {}).get("decoded_payload", {}) or {}


def store_data(payload: Dict[str, Any], source: str = "wifi") -> None:
    """
    Store incoming data.

    In LOCAL_TEST mode, this only prints locally.
    In full mode, it sends to Azure IoT Hub and stores in CosmosDB.
    """
    if LOCAL_TEST:
        print(f"\n[LOCAL_STORE] Source: {source}")
        print(json.dumps(payload, indent=2))
        return

    try:
        if azure_client is not None and Message is not None:
            azure_msg = Message(json.dumps(payload))
            azure_msg.content_type = "application/json"
            azure_msg.content_encoding = "utf-8"
            azure_client.send_message(azure_msg)
            print(f"[{source.upper()}] Sent to Azure IoT Hub")

        decoded = extract_decoded_payload(payload)
        document = {
            "id": str(time.time_ns()),
            "source": source,
            "device_id": extract_device_id(payload),
            "timestamp": payload.get("received_at", utc_now()),
            "raw": payload,
        }

        # Convenience top-level fields for UI/Cosmos queries.
        if decoded:
            document["event_type"] = decoded.get("packet_type_name")
            document["packet_type"] = decoded.get("packet_type")
            document["bike_id"] = decoded.get("bike_id")
            document["lat"] = decoded.get("lat")
            document["lon"] = decoded.get("lon")
            document["speed"] = decoded.get("speed")
            document["has_gps"] = decoded.get("has_gps")
            document["has_speed"] = decoded.get("has_speed")

        if source == "coral_ml":
            document["ml_status"] = payload.get("ml_status") or payload.get("status")
            document["confidence"] = payload.get("confidence")
            document["user"] = payload.get("user")

        if cosmos_container is not None:
            cosmos_container.upsert_item(document)
            print(f"[{source.upper()}] Saved to CosmosDB")

    except Exception as e:
        print(f"[{source.upper()}] Store error: {e}")


# =============================================================================
# CORAL / BLOB
# =============================================================================

def upload_image_to_blob(image_base64: str, filename: str) -> Optional[str]:
    if LOCAL_TEST:
        print(f"[LOCAL_TEST] Would upload image to Blob as {filename}")
        return f"local://{filename}"

    if blob_service is None:
        print("[BLOB] Blob service not initialized")
        return None

    try:
        image_bytes = base64.b64decode(image_base64)
        blob_client = blob_service.get_blob_client(container=BLOB_CONTAINER, blob=filename)
        blob_client.upload_blob(image_bytes, overwrite=True)
        url = f"{BLOB_ACCOUNT_URL}/{BLOB_CONTAINER}/{filename}"
        print(f"[BLOB] Image uploaded: {url}")
        return url
    except Exception as e:
        print(f"[BLOB] Upload error: {e}")
        return None


def check_auth_status() -> None:
    """Check last Coral ML result in CosmosDB. Does not trigger Coral auth."""
    if LOCAL_TEST:
        print("[LOCAL_TEST] Skipping Coral/Cosmos auth check")
        return

    if cosmos_container is None:
        print("[CORAL] CosmosDB not initialized")
        return

    try:
        items = list(cosmos_container.query_items(
            query="SELECT TOP 1 * FROM c WHERE c.source='coral_ml' ORDER BY c.timestamp DESC",
            enable_cross_partition_query=True,
        ))

        if items:
            last = items[0]
            print(
                f"[CORAL] Last auth: {last.get('ml_status')} "
                f"confidence={last.get('confidence')} at {last.get('timestamp')}"
            )
        else:
            print("[CORAL] No auth record found — possible theft")
    except Exception as e:
        print(f"[CORAL] Check error: {e}")


@wifi_app.route("/coral/auth", methods=["POST"])
def receive_coral_auth():
    """Receive Coral authentication result."""
    try:
        data = request.get_json(silent=True) or {}
        if not data:
            return jsonify({"error": "No JSON"}), 400

        payload = {
            "device_id": data.get("device_id", "coral-01"),
            "received_at": utc_now(),
            "ml_status": data.get("ml_status") or data.get("status", "unknown"),
            "confidence": data.get("confidence"),
            "user": data.get("user"),
            "raw": data,
        }

        image_base64 = data.get("image_base64")
        if image_base64:
            filename = data.get("filename") or f"coral_auth_{int(time.time())}.jpg"
            payload["image_url"] = upload_image_to_blob(image_base64, filename)

        store_data(payload, source="coral_ml")
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"[CORAL] Auth route error: {e}")
        return jsonify({"error": str(e)}), 500


@wifi_app.route("/coral/image", methods=["POST"])
def receive_coral_image():
    """Receive an alert image from Coral and upload it to Blob Storage."""
    try:
        data = request.get_json(silent=True) or {}
        image_base64 = data.get("image_base64")
        if not image_base64:
            return jsonify({"error": "Missing image_base64"}), 400

        filename = data.get("filename") or f"alert_{int(time.time())}.jpg"
        image_url = upload_image_to_blob(image_base64, filename)

        payload = {
            "device_id": data.get("device_id", "coral-01"),
            "received_at": utc_now(),
            "image_url": image_url,
            "filename": filename,
            "raw": {k: v for k, v in data.items() if k != "image_base64"},
        }

        store_data(payload, source="coral_image")
        return jsonify({"status": "ok", "image_url": image_url}), 200

    except Exception as e:
        print(f"[CORAL] Image route error: {e}")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# BINARY PROTOCOL
# =============================================================================

def make_mac(payload: bytes) -> bytes:
    h = hashlib.sha256()
    h.update(AUTH_KEY_PROTO)
    h.update(payload)
    return h.digest()[:MAC_SIZE_PROTO]


def mac_equal(a: bytes, b: bytes) -> bool:
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= x ^ y
    return diff == 0


def verify_and_strip_mac(data: bytes) -> bytes:
    if len(data) < 8 + MAC_SIZE_PROTO:
        raise ValueError("Packet too short for authenticated format")

    payload = data[:-MAC_SIZE_PROTO]
    received_mac = data[-MAC_SIZE_PROTO:]
    expected_mac = make_mac(payload)

    if not mac_equal(received_mac, expected_mac):
        raise PermissionError("Invalid packet MAC")

    return payload


def decode_packet(data: bytes) -> Dict[str, Any]:
    if len(data) < 8:
        raise ValueError(f"Packet too short: {len(data)} bytes")

    bike_id, packet_type, flags, timestamp = struct.unpack("<H B B I", data[:8])

    result = {
        "bike_id": bike_id,
        "packet_type": packet_type,
        "packet_type_name": PACKET_TYPE_NAMES.get(packet_type, f"unknown_{packet_type}"),
        "timestamp": timestamp,
        "has_gps": bool(flags & 0x01),
        "has_speed": bool(flags & 0x02),
    }

    pos = 8

    if result["has_gps"]:
        if len(data) < pos + 8:
            raise ValueError("Packet says GPS exists but GPS bytes are missing")
        lat_int, lon_int = struct.unpack("<i i", data[pos:pos + 8])
        result["lat"] = lat_int / 10000000.0
        result["lon"] = lon_int / 10000000.0
        pos += 8

    if result["has_speed"]:
        if len(data) < pos + 1:
            raise ValueError("Packet says speed exists but speed byte is missing")
        result["speed"] = struct.unpack("<B", data[pos:pos + 1])[0]
        pos += 1

    return result


def accept_tracking_packet(decoded: Dict[str, Any]) -> bool:
    """
    Accept only new tracking packets.
    Drops duplicate/out-of-order tracking packets.
    Theft/crash alerts are never filtered here.
    """
    if decoded["packet_type"] != PACKET_TRACKING:
        return True

    bike_id = decoded["bike_id"]
    pytrack_ts = decoded.get("timestamp", 0)
    now = time.time()

    last_ts = last_tracking_timestamp.get(bike_id)
    last_bridge_time = last_tracking_bridge_time.get(bike_id)

    if last_ts is not None and pytrack_ts <= last_ts:
        print(
            f"[TRACKING] Ignored old/duplicate packet from bike {bike_id}: "
            f"timestamp={pytrack_ts}, last={last_ts}"
        )
        return False

    if last_ts is not None and last_bridge_time is not None:
        print(
            f"[TRACKING_INTERVAL] bike={bike_id}, "
            f"pytrack_delta={pytrack_ts - last_ts}s, "
            f"bridge_delta={now - last_bridge_time:.1f}s"
        )

    last_tracking_timestamp[bike_id] = pytrack_ts
    last_tracking_bridge_time[bike_id] = now

    return True


def handle_alert(decoded_packet: Dict[str, Any]) -> None:
    alert_type = decoded_packet["packet_type_name"]
    print(f"\nALERT: {alert_type.upper()} from bike {decoded_packet['bike_id']}!")

    if decoded_packet.get("has_gps"):
        print(f"   Location: {decoded_packet.get('lat')}, {decoded_packet.get('lon')}")
    if decoded_packet.get("has_speed"):
        print(f"   Speed: {decoded_packet.get('speed')} km/h")

    threading.Thread(target=check_auth_status, daemon=True).start()


def handle_tracking(decoded_packet: Dict[str, Any]) -> None:
    bike_id = decoded_packet["bike_id"]

    if decoded_packet.get("has_gps"):
        lat = decoded_packet.get("lat")
        lon = decoded_packet.get("lon")
        speed = decoded_packet.get("speed")

        if speed is not None:
            print(f"[TRACKING] Bike {bike_id}: {lat}, {lon} @ {speed} km/h")
        else:
            print(f"[TRACKING] Bike {bike_id}: {lat}, {lon}")
    else:
        print(f"[TRACKING] Bike {bike_id}: no GPS fix")


def handle_decoded_packet(decoded: Dict[str, Any], source: str) -> bool:
    """Run common decoded packet behavior for both WiFi and LoRa."""
    print(f"[{source.upper()} DATA] {json.dumps(decoded)}")

    if not accept_tracking_packet(decoded):
        return False

    if decoded["packet_type"] == PACKET_THEFT_ALERT:
        handle_alert(decoded)
    elif decoded["packet_type"] == PACKET_CRASH_ALERT:
        handle_alert(decoded)
    elif decoded["packet_type"] == PACKET_TRACKING:
        handle_tracking(decoded)
    else:
        print(f"[{source.upper()}] Unknown packet type: {decoded['packet_type']}")

    store_data({
        "end_device_ids": {"device_id": f"bike-{decoded['bike_id']}"},
        "received_at": utc_now(),
        "uplink_message": {"decoded_payload": decoded},
    }, source=source)

    return True


# =============================================================================
# LORA / TTN
# =============================================================================

def on_connect(client, userdata, flags, rc):
    global lora_connected

    if rc == 0:
        lora_connected = True
        print("[TTN] Connected")
        client.subscribe(TTN_TOPIC)
        print(f"[TTN] Listening on: {TTN_TOPIC}")
    else:
        lora_connected = False
        print(f"[TTN] Connection error, code: {rc}")


def on_disconnect(client, userdata, rc):
    global lora_connected
    lora_connected = False
    print("[TTN] Disconnected — WiFi fallback available")


def on_message(client, userdata, msg):
    print("\n[LORA] Message received")

    try:
        ttn_payload = json.loads(msg.payload.decode())

        # Preferred path: raw binary frm_payload from TTN.
        frm_payload = ttn_payload.get("uplink_message", {}).get("frm_payload")
        if frm_payload:
            binary_data = base64.b64decode(frm_payload)
            payload = verify_and_strip_mac(binary_data)
            decoded = decode_packet(payload)
            handle_decoded_packet(decoded, source="lora")
            return

        # Compatibility path: TTN payload formatter already decoded.
        decoded = ttn_payload.get("uplink_message", {}).get("decoded_payload", {})
        store_data(ttn_payload, source="lora")

        if decoded.get("alert") is True:
            print("[LORA] Alert flag detected — checking Coral auth status")
            threading.Thread(target=check_auth_status, daemon=True).start()

    except PermissionError:
        print("[LORA AUTH] Invalid packet MAC")
    except ValueError as e:
        print(f"[LORA ERROR] Decode failed: {e}")
    except Exception as e:
        print(f"[LORA ERROR] {e}")


def start_lora_client() -> None:
    if not TTN_PASSWORD:
        print("[TTN] Missing TTN_PASSWORD — LoRa MQTT not started")
        return

    client = mqtt.Client()
    client.username_pw_set(TTN_USERNAME, TTN_PASSWORD)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    while True:
        try:
            print("[TTN] Connecting...")
            client.connect(TTN_HOST, TTN_PORT, keepalive=60)
            client.loop_forever()
        except Exception as e:
            print(f"[TTN] Failed: {e} — retrying in 10s")
            time.sleep(10)


def send_downlink(mode: str) -> bool:
    """Send mode command over TTN downlink."""
    mode = mode.lower()

    if mode not in ["cruising", "parked"]:
        print(f"[DOWNLINK] Invalid mode: {mode}")
        return False

    if not TTN_API_KEY:
        print("[DOWNLINK] Missing TTN_API_KEY")
        return False

    byte_value = 0x01 if mode == "cruising" else 0x02
    payload_b64 = base64.b64encode(bytes([byte_value])).decode()

    url = (
        f"https://eu1.cloud.thethings.network/api/v3/as/applications/"
        f"{TTN_APP_ID}/devices/{TTN_DEVICE_ID}/down/push"
    )

    headers = {
        "Authorization": f"Bearer {TTN_API_KEY}",
        "Content-Type": "application/json",
    }

    body = {
        "downlinks": [{
            "frm_payload": payload_b64,
            "f_port": 1,
            "priority": "NORMAL",
        }]
    }

    try:
        res = requests.post(url, json=body, headers=headers, timeout=5)
        print(f"[DOWNLINK] Mode '{mode}' sent to TTN — status: {res.status_code}")
        return 200 <= res.status_code < 300
    except Exception as e:
        print(f"[DOWNLINK] Error: {e}")
        return False


# =============================================================================
# WIFI DISCOVERY AND MODE COMMANDS
# =============================================================================

def start_discovery_server() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except Exception:
        pass

    s.bind(("0.0.0.0", DISCOVERY_PORT))
    print(f"[DISCOVERY] Listening on UDP {DISCOVERY_PORT}")

    while True:
        data, addr = s.recvfrom(128)

        if data == DISCOVERY_REQUEST:
            response = f"{DISCOVERY_RESPONSE_PREFIX}:{WIFI_PORT}".encode()
            s.sendto(response, addr)
            print(f"[DISCOVERY] Replied to {addr[0]}")


def send_wifi_mode(mode: str) -> bool:
    """Send mode byte directly to registered PyTrack over TCP/WiFi."""
    mode = mode.lower()

    if mode not in ["cruising", "parked"]:
        print(f"[WIFI_MODE] Invalid mode: {mode}")
        return False

    if not pytrack_info.get("ip"):
        print("[WIFI_MODE] No registered PyTrack")
        return False

    packet = bytes([0x01 if mode == "cruising" else 0x02])

    try:
        s = socket.socket()
        s.settimeout(5)
        s.connect((pytrack_info["ip"], int(pytrack_info["port"])))
        s.sendall(packet)
        s.close()

        print(f"[WIFI_MODE] Sent mode '{mode}' to PyTrack")
        return True

    except Exception as e:
        print(f"[WIFI_MODE] Error sending mode: {e}")
        return False


# =============================================================================
# HTTP ROUTES
# =============================================================================

def verify_jwt(req) -> bool:
    token = req.headers.get("Authorization", "").replace("Bearer ", "")

    try:
        jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return True
    except Exception:
        return False


@wifi_app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "local_test": LOCAL_TEST,
        "demo_wifi_only": DEMO_WIFI_ONLY,
        "lora_connected": lora_connected,
        "pytrack_registered": bool(pytrack_info.get("ip")),
        "pytrack": pytrack_info,
        "cloud": {
            "azure_iot": azure_client is not None,
            "cosmos": cosmos_container is not None,
            "blob": blob_service is not None,
        },
    }), 200


@wifi_app.route("/register", methods=["POST"])
def register_pytrack():
    data = request.get_json(silent=True) or {}

    pytrack_info["ip"] = request.remote_addr
    pytrack_info["port"] = int(data.get("mode_port", 5002))
    pytrack_info["device_id"] = data.get("device_id", "unknown")
    pytrack_info["last_seen"] = utc_now()

    print(f"[REGISTER] PyTrack registered: {pytrack_info}")

    return jsonify({"status": "ok", "registered": pytrack_info}), 200


@wifi_app.route("/proto_data", methods=["POST"])
def receive_proto_data():
    try:
        with packet_lock:
            binary_data = request.data

            if not binary_data:
                return jsonify({"error": "Empty data"}), 400

            payload = verify_and_strip_mac(binary_data)
            decoded = decode_packet(payload)

            print(f"\n[RECEIVED] {len(payload)} bytes + {MAC_SIZE_PROTO} MAC bytes")

            accepted = handle_decoded_packet(decoded, source="wifi")

            if not accepted:
                return jsonify({"status": "ignored_old_tracking_packet"}), 200

            return jsonify({"status": "ok"}), 200

    except PermissionError:
        print("[AUTH] Invalid packet MAC")
        return jsonify({"error": "Invalid authentication"}), 401
    except ValueError as e:
        print(f"[ERROR] Decode failed: {e}")
        return jsonify({"error": f"Decode failed: {str(e)}"}), 400
    except Exception as e:
        print(f"[ERROR] {e}")
        return jsonify({"error": str(e)}), 500


@wifi_app.route("/mode", methods=["POST"])
def receive_mode():
    """
    Main mode endpoint.

    DEMO_WIFI_ONLY=True:
      sends mode over WiFi only.

    DEMO_WIFI_ONLY=False:
      tries LoRa/TTN downlink first, then WiFi fallback.
    """
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "").lower()

    if mode not in ["cruising", "parked"]:
        return jsonify({"error": "Invalid mode. Use 'cruising' or 'parked'."}), 400

    if DEMO_WIFI_ONLY:
        ok = send_wifi_mode(mode)
        return jsonify({
            "status": "ok" if ok else "failed",
            "mode": mode,
            "transport": "wifi_demo",
        }), 200 if ok else 500

    lora_ok = send_downlink(mode)
    if lora_ok:
        return jsonify({"status": "ok", "mode": mode, "transport": "lora"}), 200

    wifi_ok = send_wifi_mode(mode)
    return jsonify({
        "status": "ok" if wifi_ok else "failed",
        "mode": mode,
        "transport": "wifi_fallback",
        "lora_failed": True,
    }), 200 if wifi_ok else 500


@wifi_app.route("/wifi_mode", methods=["POST"])
def receive_wifi_mode():
    """Direct WiFi mode endpoint kept for compatibility with today's app changes."""
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "").lower()

    if mode not in ["cruising", "parked"]:
        return jsonify({"error": "Invalid mode. Use 'cruising' or 'parked'."}), 400

    ok = send_wifi_mode(mode)
    return jsonify({
        "status": "ok" if ok else "failed",
        "mode": mode,
        "transport": "wifi_direct",
    }), 200 if ok else 500


@wifi_app.route("/data", methods=["POST"])
def receive_wifi_data():
    """
    Legacy JSON endpoint for simple x/y/z tests.
    Your current PyTrack binary firmware uses /proto_data instead.
    """
    try:
        if not verify_jwt(request):
            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(silent=True) or {}
        if not data:
            return jsonify({"error": "No JSON"}), 400

        print(f"\n[WIFI JSON] Data received: {data}")

        payload = {
            "end_device_ids": {"device_id": data.get("device_id", "pycom-wifi")},
            "received_at": utc_now(),
            "uplink_message": {
                "decoded_payload": {
                    "x": data.get("x", 0),
                    "y": data.get("y", 0),
                    "z": data.get("z", 9.81),
                    "alert": data.get("alert", False),
                }
            },
        }

        store_data(payload, source="wifi_json")

        if data.get("alert") is True:
            print("[WIFI JSON] Alert — checking Coral auth status")
            threading.Thread(target=check_auth_status, daemon=True).start()

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =============================================================================
# MAIN
# =============================================================================

def run_wifi_app() -> None:
    wifi_app.run(
        host="0.0.0.0",
        port=WIFI_PORT,
        debug=False,
        use_reloader=False,
        threaded=False,
    )


def main() -> None:
    print("=== Smart Bicycle ZOE — Bridge ===")
    print(f"LOCAL_TEST={LOCAL_TEST}")
    print(f"DEMO_WIFI_ONLY={DEMO_WIFI_ONLY}")

    init_cloud_clients()

    if not LOCAL_TEST:
        threading.Thread(target=start_lora_client, daemon=True).start()
    else:
        print("[LOCAL_TEST] LoRa/TTN client not started")

    threading.Thread(target=start_discovery_server, daemon=True).start()
    threading.Thread(target=run_wifi_app, daemon=True).start()

    print(f"  LoRa/TTN : {TTN_HOST}:{TTN_PORT}")
    print(f"  WiFi     : http://0.0.0.0:{WIFI_PORT}")
    print(f"  Discovery: UDP {DISCOVERY_PORT}")
    print(f"  Coral    : {CORAL_HOST}")
    print(f"  Blob     : {BLOB_CONTAINER}")

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
