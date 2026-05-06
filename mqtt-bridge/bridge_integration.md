# Smart Bicycle ZOE — Bridge

## What it does

The bridge is the central piece that connects everything together.
It runs in a Docker container on my machine.

```
Pytrack → LoRa → TTN ──┐
                        ├──► Bridge ──► CosmosDB ──► Web App
Pytrack → WiFi ─────────┘      │
                                └──► Coral (on alert only)
                                └──► Blob Storage (images)
```

---

## How to send data to the bridge

### Via LoRa (primary)

Send your data via LoRaWAN to TTN as usual.
The bridge is already subscribed to TTN and will receive everything automatically.

Your TTN payload formatter must decode to this JSON format:

```json
{
    "x": 0.19,
    "y": -0.006,
    "z": 9.81,
    "alert": false
}
```

When your ML detects suspicious movement → set `alert: true`.
This is the only field the bridge acts on.

---

### Via WiFi (fallback — if LoRa is unavailable)

Send a POST request directly to the bridge:

```
POST http://<BRIDGE_IP>:5001/data
Content-Type: application/json

{
    "device_id": "pytrack-01",
    "x": 0.19,
    "y": -0.006,
    "z": 9.81,
    "alert": false
}
```

---

## What happens when alert = true

```
1. Bridge receives alert: true
2. Bridge calls POST /alert on Coral → face recognition starts
3. Bridge waits for result (max 30s)
4. Result stored in CosmosDB
5. If unauthorized → image fetched from Coral → uploaded to Blob Storage
```

The Coral is called only when alert is true. Not permanently.

---

## What the Coral must expose

Three HTTP endpoints on port 8000:

| Endpoint  | Method | When called                      |
|-----------|--------|----------------------------------|
| /alert    | POST   | Bridge triggers face recognition |
| /result   | GET    | Bridge polls for ML result       |
| /image    | GET    | Bridge fetches intruder image    |

See Coral/bridge_api.py for the full implementation.

---

IoT Project 2026 — University of Antwerp