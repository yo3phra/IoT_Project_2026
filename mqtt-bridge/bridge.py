import paho.mqtt.client as mqtt
from azure.iot.device import IoTHubDeviceClient, Message
from azure.cosmos import CosmosClient
import time
import json
import os

TTN_HOST     = "eu1.cloud.thethings.network"
TTN_PORT     = 1883
TTN_USERNAME = os.environ.get("TTN_USERNAME", "iotbicycle@ttn")
TTN_PASSWORD = os.environ.get("TTN_PASSWORD")
AZURE_CONNECTION_STRING = os.environ.get("AZURE_IOT_CONNECTION_STRING")
COSMOS_CONNECTION_STRING = os.environ.get("COSMOS_CONNECTION_STRING")
TTN_TOPIC    = "v3/iotbicycle@ttn/devices/+/up"

#IoT hub
AZURE_CONNECTION_STRING = os.environ.get("AZURE_IOT_CONNECTION_STRING")

# CosmosDB
COSMOS_CONNECTION_STRING = os.environ.get("COSMOS_CONNECTION_STRING")
COSMOS_DATABASE  = "bicycle-db"
COSMOS_CONTAINER = "sensor-data"

# Init Azure client
azure_client = IoTHubDeviceClient.create_from_connection_string(AZURE_CONNECTION_STRING)
azure_client.connect()
print("Connected to Azure IoT Hub !")

# Init CosmosDB
cosmos_client    = CosmosClient.from_connection_string(COSMOS_CONNECTION_STRING)
database         = cosmos_client.get_database_client(COSMOS_DATABASE)
cosmos_container = database.get_container_client(COSMOS_CONTAINER)
print("Connected to CosmosDB !")

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected toTTN !")
        client.subscribe(TTN_TOPIC)
        print(f"Listening on : {TTN_TOPIC}")
    else:
        print(f"Error code : {rc}")

def on_message(client, userdata, msg):
    print(f"\nMessage received!")
    try:
        payload = json.loads(msg.payload.decode())
        print(json.dumps(payload, indent=2))

        # Envoyer vers Azure
        azure_msg = Message(json.dumps(payload))
        azure_msg.content_type = "application/json"
        azure_msg.content_encoding = "utf-8"
        azure_client.send_message(azure_msg)
        print("Sent to Azure !")
        # Saving in CosmosDB
        document = {
            "id": str(time.time()),
            "device_id": payload.get("end_device_ids", {}).get("device_id", "unknown"),
            "timestamp": payload.get("received_at", ""),
            "raw": payload
        }
        cosmos_container.upsert_item(document)
        print("Saved to CosmosDB !")

    except Exception as e:
        print(f"Error : {e}")

# Init MQTT TTN
client = mqtt.Client()
client.username_pw_set(TTN_USERNAME, TTN_PASSWORD)
client.on_connect = on_connect
client.on_message = on_message

print("Connecting...")
client.connect(TTN_HOST, TTN_PORT, keepalive=60)
client.loop_forever()