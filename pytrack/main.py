# main.py - Smart Bicycle ZOE Pytrack combined firmware
#
# Modes:
#   PARKED_STATE:
#     - Detect repeated handling of parked bike.
#     - Send camera_trigger events to Coral.
#
#   CRUISING_STATE:
#     - Detect possible crash.
#     - Send possible_crash event to backend.
#     - Backend should notify user UI, wait 30 seconds, and escalate to emergency contact if no response.
#
# Tracking:
#   - GPS tracking is command-controlled.
#   - For now, backend command receiving is commented out.
#   - You can manually enable GPS testing with MANUAL_TRACKING_TEST = True.
#
# Current version:
#   - Serial JSON output only.
#   - LoRa / LoRaWAN communication hooks are included as commented placeholders.

import time
import math
import ujson

from pycoproc_1 import Pycoproc
from LIS2HH12 import LIS2HH12
from L76GNSS import L76GNSS


# ---------------- OPTIONAL COMMUNICATION IMPORTS ----------------
# Uncomment later when integrating LoRa / LoRaWAN.
#
# from network import LoRa
# import socket


# ---------------- SYSTEM STATE CONFIG ----------------

STATE_PARKED = "parked"
STATE_CRUISING = "cruising"

# Temporary manual state switch for testing.
# Change this before uploading.
SYSTEM_STATE = STATE_PARKED
# SYSTEM_STATE = STATE_CRUISING


# ---------------- GENERAL CONFIG ----------------

BIKE_ID = "bike01"
DEVICE_ID = "zoe-bike-01"

G_TO_MS2 = 9.81

SAMPLE_DELAY_MS = 250
CALIBRATION_SAMPLES = 25

SERIAL_OUTPUT_ENABLED = True


# ---------------- PARKED STATE CONFIG ----------------

PARKED_MOTION_STEP_THRESHOLD = 0.06
PARKED_STRONG_STEP_THRESHOLD = 0.16
PARKED_TILT_SCORE_THRESHOLD = 0.25

# 16 samples * 250 ms = about 4 seconds of motion memory.
PARKED_WINDOW_SIZE = 16

# Single bumps and one-off swish movements should not trigger the camera.
PARKED_CAMERA_TRIGGER_HITS = 5

PARKED_MINOR_BUMP_COOLDOWN_MS = 3000
PARKED_TILT_TRANSITION_COOLDOWN_MS = 3000
PARKED_TILTED_STATIC_COOLDOWN_MS = 5000
PARKED_CAMERA_EVENT_COOLDOWN_MS = 3000

# Once trigger_camera=true is emitted, do not emit trigger_camera=true again
# until this cooldown has passed.
PARKED_CAMERA_RETRIGGER_COOLDOWN_MS = 10000


# ---------------- CRUISING STATE CONFIG ----------------

# Crash detection thresholds, in g-based units.
CRUISING_IMPACT_DELTA_G_THRESHOLD = 2.0
CRUISING_IMPACT_STEP_THRESHOLD = 1.5
CRUISING_CRASH_TILT_THRESHOLD = 0.55

# After a possible impact, check whether the bike/rider becomes still.
CRUISING_POST_IMPACT_CHECK_MS = 10000
CRUISING_POST_IMPACT_STILL_STEP_THRESHOLD = 0.05
CRUISING_POST_IMPACT_STILL_REQUIRED_RATIO = 0.75

# Cooldown after sending crash event so it does not spam.
CRUISING_CRASH_ALERT_COOLDOWN_MS = 30000


# ---------------- GPS CONFIG ----------------

GPS_ENABLED = True
GPS_INITIAL_TIMEOUT_SEC = 30
GPS_FAST_TIMEOUT_SEC = 5
GPS_INTERVAL_MS = 10000

# For standalone GPS testing only.
# Set True if you want GPS tracking to start immediately after calibration.
MANUAL_TRACKING_TEST = False


# ---------------- COMMUNICATION CONFIG PLACEHOLDERS ----------------

# Uncomment/fill later with the team.
#
# LORA_CORAL_ENABLED = False
# LORAWAN_BACKEND_ENABLED = False
#
# LORA_REGION = "EU868"
# LORA_FREQUENCY = 868100000
#
# LORAWAN_DEV_ADDR = "00000000"
# LORAWAN_NWK_SWKEY = "00000000000000000000000000000000"
# LORAWAN_APP_SWKEY = "00000000000000000000000000000000"


# ---------------- INIT ----------------

print("=== SMART BICYCLE ZOE COMBINED PYTRACK STARTED ===")
print("Selected SYSTEM_STATE:", SYSTEM_STATE)

py = Pycoproc(Pycoproc.PYTRACK)
acc = LIS2HH12(py)

gps = None

# Optional sockets for future integration.
lora_coral_sock = None
lorawan_backend_sock = None


# ---------------- OPTIONAL COMMUNICATION SETUP ----------------
# Keep this commented until integrating with the team.
#
# def setup_local_lora_to_coral():
#     global lora_coral_sock
#
#     from network import LoRa
#     import socket
#
#     print("Starting local Raw LoRa for Pytrack -> Coral...")
#
#     lora = LoRa(
#         mode=LoRa.LORA,
#         region=LoRa.EU868,
#         frequency=LORA_FREQUENCY,
#         sf=7,
#         bandwidth=LoRa.BW_125KHZ
#     )
#
#     lora_coral_sock = socket.socket(socket.AF_LORA, socket.SOCK_RAW)
#     lora_coral_sock.setblocking(False)
#
#     print("Local LoRa to Coral ready.")
#
#
# def setup_lorawan_to_backend():
#     global lorawan_backend_sock
#
#     from network import LoRa
#     import socket
#     import binascii
#     import struct
#
#     print("Starting LoRaWAN for Pytrack -> Backend...")
#
#     lora = LoRa(mode=LoRa.LORAWAN, region=LoRa.EU868)
#
#     dev_addr = struct.unpack(">l", binascii.unhexlify(LORAWAN_DEV_ADDR))[0]
#     nwk_swkey = binascii.unhexlify(LORAWAN_NWK_SWKEY)
#     app_swkey = binascii.unhexlify(LORAWAN_APP_SWKEY)
#
#     lora.join(
#         activation=LoRa.ABP,
#         auth=(dev_addr, nwk_swkey, app_swkey)
#     )
#
#     lorawan_backend_sock = socket.socket(socket.AF_LORA, socket.SOCK_RAW)
#     lorawan_backend_sock.setsockopt(socket.SOL_LORA, socket.SO_DR, 5)
#     lorawan_backend_sock.setblocking(False)
#
#     print("LoRaWAN backend socket ready.")
#
#
# if LORA_CORAL_ENABLED:
#     setup_local_lora_to_coral()
#
# if LORAWAN_BACKEND_ENABLED:
#     setup_lorawan_to_backend()


# ---------------- HELPERS ----------------

def now_ms():
    return time.ticks_ms()


def elapsed_ms(start_ms):
    return time.ticks_diff(time.ticks_ms(), start_ms)


def can_fire_cooldown(last_ms, cooldown_ms):
    return elapsed_ms(last_ms) > cooldown_ms


def read_acc_g():
    x, y, z = acc.acceleration()
    mag = math.sqrt((x * x) + (y * y) + (z * z))
    return x, y, z, mag


def emit(packet, route="serial"):
    """
    route:
      - "coral"   = intended for Coral/camera pipeline
      - "backend" = intended for backend/GPS/crash pipeline
      - "serial"  = debug/status only

    Currently everything is printed to serial.
    Later, route can decide whether to send over local LoRa or LoRaWAN.
    """

    packet["route"] = route
    payload = ujson.dumps(packet)

    if SERIAL_OUTPUT_ENABLED:
        print(payload)

    # Future: send parked camera/motion events to Coral over local LoRa.
    #
    # if route == "coral" and LORA_CORAL_ENABLED and lora_coral_sock is not None:
    #     try:
    #         lora_coral_sock.send(payload)
    #     except Exception as e:
    #         print("Local LoRa send to Coral failed:", e)

    # Future: send GPS/crash events to backend over LoRaWAN.
    #
    # if route == "backend" and LORAWAN_BACKEND_ENABLED and lorawan_backend_sock is not None:
    #     try:
    #         lorawan_backend_sock.send(payload)
    #     except Exception as e:
    #         print("LoRaWAN send to backend failed:", e)


def emit_state(state):
    emit({
        "event_type": "state_changed",
        "bike_id": BIKE_ID,
        "device_id": DEVICE_ID,
        "timestamp_ms": now_ms(),
        "state": state,
        "system_state": SYSTEM_STATE
    }, route="serial")


def build_motion_packet(event_type, x_g, y_g, z_g, mag_g,
                        step_score, tilt_score, hits,
                        strong_hits, trigger_camera):
    return {
        "event_type": event_type,
        "bike_id": BIKE_ID,
        "device_id": DEVICE_ID,
        "timestamp_ms": now_ms(),
        "system_state": STATE_PARKED,

        # Outgoing acceleration values are in m/s².
        "x": round(x_g * G_TO_MS2, 3),
        "y": round(y_g * G_TO_MS2, 3),
        "z": round(z_g * G_TO_MS2, 3),
        "mag": round(mag_g * G_TO_MS2, 3),

        # Classifier features, kept in internal g-distance units.
        "step_score": round(step_score, 3),
        "tilt_score": round(tilt_score, 3),
        "hits": hits,
        "strong_hits": strong_hits,

        # One-shot command flag for Coral.
        # True means: activate camera now.
        # False means: this packet does not request a new camera activation.
        "trigger_camera": trigger_camera
    }


def build_cruising_packet(event_type, x_g, y_g, z_g, mag_g,
                          step_score, tilt_score, delta_from_1g,
                          lat=None, lon=None, gps_status="not_checked",
                          extra=None):
    packet = {
        "event_type": event_type,
        "bike_id": BIKE_ID,
        "device_id": DEVICE_ID,
        "timestamp_ms": now_ms(),
        "system_state": STATE_CRUISING,

        # Outgoing acceleration values are in m/s².
        "x": round(x_g * G_TO_MS2, 3),
        "y": round(y_g * G_TO_MS2, 3),
        "z": round(z_g * G_TO_MS2, 3),
        "mag": round(mag_g * G_TO_MS2, 3),

        # Internal classifier features.
        "step_score": round(step_score, 3),
        "tilt_score": round(tilt_score, 3),
        "delta_from_1g": round(delta_from_1g, 3),

        # GPS information.
        "gps_status": gps_status,
        "lat": lat,
        "lon": lon
    }

    if extra is not None:
        packet["crash_features"] = extra

    return packet


# ---------------- GPS HELPERS ----------------

def start_gps_tracking():
    global gps

    if not GPS_ENABLED:
        return

    print("Starting GPS tracking mode...")
    gps = L76GNSS(py, timeout=GPS_INITIAL_TIMEOUT_SEC)


def stop_gps_tracking():
    global gps
    gps = None
    print("Stopping GPS tracking mode.")


def try_gps_update(timeout_sec=None, force_timeout=False):
    """
    Returns:
      lat, lon, "fix"
      None, None, "no_fix"
      None, None, "disabled"

    force_timeout=True recreates the GPS object with the given timeout.
    This is useful for crash alerts, where we do not want an old 30s timeout
    to delay the possible_crash packet too much.
    """
    global gps

    if not GPS_ENABLED:
        return None, None, "disabled"

    if timeout_sec is None:
        timeout_sec = GPS_INITIAL_TIMEOUT_SEC

    if gps is None or force_timeout:
        gps = L76GNSS(py, timeout=timeout_sec)

    coord = gps.coordinates()

    if coord[0] is not None and coord[1] is not None:
        lat, lon = coord
        return lat, lon, "fix"

    return None, None, "no_fix"


def emit_gps_update(gps_status, lat=None, lon=None):
    emit({
        "event_type": "tracking_update",
        "bike_id": BIKE_ID,
        "device_id": DEVICE_ID,
        "timestamp_ms": now_ms(),
        "gps_status": gps_status,
        "lat": lat,
        "lon": lon
    }, route="backend")


def maybe_send_gps_update(current_ms):
    global gps
    global gps_has_fix
    global last_gps_ms

    if not tracking_active or not GPS_ENABLED:
        return

    if elapsed_ms(last_gps_ms) > GPS_INTERVAL_MS:
        lat, lon, gps_status = try_gps_update()

        if gps_status == "fix":
            emit_gps_update("fix", lat, lon)

            if not gps_has_fix:
                gps_has_fix = True

                # After first successful fix, use shorter GPS timeout.
                gps = L76GNSS(py, timeout=GPS_FAST_TIMEOUT_SEC)

                emit({
                    "event_type": "gps_mode",
                    "bike_id": BIKE_ID,
                    "device_id": DEVICE_ID,
                    "timestamp_ms": now_ms(),
                    "mode": "fast_updates",
                    "timeout_s": GPS_FAST_TIMEOUT_SEC
                }, route="backend")

        else:
            emit_gps_update(gps_status, None, None)

        last_gps_ms = current_ms


# ---------------- FUTURE BACKEND COMMAND HANDLING ----------------
# Later, backend/user can send:
#   {"command":"set_state","state":"parked"}
#   {"command":"set_state","state":"cruising"}
#   {"command":"tracking_on"}
#   {"command":"tracking_off"}
#
# def check_backend_commands():
#     global SYSTEM_STATE, tracking_active, gps_has_fix, state
#     global park_base_x, park_base_y, park_base_z, park_base_mag
#     global cruise_base_x, cruise_base_y, cruise_base_z, cruise_base_mag
#     global prev_x, prev_y, prev_z, prev_mag
#
#     if not LORAWAN_BACKEND_ENABLED or lorawan_backend_sock is None:
#         return
#
#     try:
#         data = lorawan_backend_sock.recv(128)
#         if data:
#             msg = data.decode("utf-8")
#             print("Backend command received:", msg)
#
#             if "set_state" in msg and "parked" in msg:
#                 SYSTEM_STATE = STATE_PARKED
#                 park_base_x, park_base_y, park_base_z, park_base_mag = calibrate_baseline("parked")
#                 prev_x, prev_y, prev_z, prev_mag = read_acc_g()
#                 state = "parked_monitoring"
#                 emit_state(state)
#
#             elif "set_state" in msg and "cruising" in msg:
#                 SYSTEM_STATE = STATE_CRUISING
#                 cruise_base_x, cruise_base_y, cruise_base_z, cruise_base_mag = calibrate_baseline("cruising")
#                 prev_x, prev_y, prev_z, prev_mag = read_acc_g()
#                 state = "cruising"
#                 emit_state(state)
#
#             elif "tracking_on" in msg:
#                 if not tracking_active:
#                     tracking_active = True
#                     gps_has_fix = False
#                     start_gps_tracking()
#                     state = "tracking"
#                     emit_state(state)
#
#             elif "tracking_off" in msg:
#                 if tracking_active:
#                     tracking_active = False
#                     stop_gps_tracking()
#                     state = "monitoring"
#                     emit_state(state)
#
#     except Exception:
#         pass


# ---------------- CALIBRATION ----------------

def calibrate_baseline(label):
    print("Calibrating", label, "baseline. Keep the bike still/upright...")

    sum_x = 0.0
    sum_y = 0.0
    sum_z = 0.0
    sum_mag = 0.0
    valid = 0

    for i in range(CALIBRATION_SAMPLES):
        x, y, z, mag = read_acc_g()

        # First sample can sometimes be unstable.
        if i > 0:
            sum_x += x
            sum_y += y
            sum_z += z
            sum_mag += mag
            valid += 1

        time.sleep_ms(SAMPLE_DELAY_MS)

    base_x = sum_x / valid
    base_y = sum_y / valid
    base_z = sum_z / valid
    base_mag = sum_mag / valid

    print(label, "baseline done.")
    print(label, "baseline_x:", base_x)
    print(label, "baseline_y:", base_y)
    print(label, "baseline_z:", base_z)
    print(label, "baseline_mag:", base_mag)

    return base_x, base_y, base_z, base_mag


# For now, calibrate only the active state at boot.
# Later, when backend changes state live, recalibrate during the state transition.
if SYSTEM_STATE == STATE_PARKED:
    park_base_x, park_base_y, park_base_z, park_base_mag = calibrate_baseline("parked")

    # Dummy cruising baseline values, not used unless state changes later.
    cruise_base_x = park_base_x
    cruise_base_y = park_base_y
    cruise_base_z = park_base_z
    cruise_base_mag = park_base_mag

elif SYSTEM_STATE == STATE_CRUISING:
    cruise_base_x, cruise_base_y, cruise_base_z, cruise_base_mag = calibrate_baseline("cruising")

    # Dummy parked baseline values, not used unless state changes later.
    park_base_x = cruise_base_x
    park_base_y = cruise_base_y
    park_base_z = cruise_base_z
    park_base_mag = cruise_base_mag

else:
    print("Unknown SYSTEM_STATE. Defaulting to parked.")
    SYSTEM_STATE = STATE_PARKED
    park_base_x, park_base_y, park_base_z, park_base_mag = calibrate_baseline("parked")
    cruise_base_x = park_base_x
    cruise_base_y = park_base_y
    cruise_base_z = park_base_z
    cruise_base_mag = park_base_mag

prev_x, prev_y, prev_z, prev_mag = read_acc_g()

if SYSTEM_STATE == STATE_PARKED:
    state = "parked_monitoring"
else:
    state = "cruising"

emit_state(state)


# ---------------- PARKED STATE VARIABLES ----------------

parked_hits_window = []
parked_strong_hits_window = []

parked_last_minor_bump_ms = 0
parked_last_tilt_transition_ms = 0
parked_last_tilted_static_ms = 0
parked_last_camera_event_ms = 0
parked_last_camera_trigger_command_ms = -PARKED_CAMERA_RETRIGGER_COOLDOWN_MS


# ---------------- CRUISING STATE VARIABLES ----------------

cruising_last_crash_alert_ms = -CRUISING_CRASH_ALERT_COOLDOWN_MS


# ---------------- GPS STATE VARIABLES ----------------

tracking_active = False
gps_has_fix = False
last_gps_ms = 0

if MANUAL_TRACKING_TEST:
    tracking_active = True
    gps_has_fix = False
    start_gps_tracking()
    state = "tracking"
    emit_state(state)


# ---------------- PARKED STATE LOGIC ----------------

def process_parked_sample(current_ms, x, y, z, mag):
    global state
    global parked_hits_window
    global parked_strong_hits_window
    global parked_last_minor_bump_ms
    global parked_last_tilt_transition_ms
    global parked_last_tilted_static_ms
    global parked_last_camera_event_ms
    global parked_last_camera_trigger_command_ms

    # Dynamic movement: current sample vs previous sample.
    step_dx = x - prev_x
    step_dy = y - prev_y
    step_dz = z - prev_z

    step_score = math.sqrt(
        (step_dx * step_dx) +
        (step_dy * step_dy) +
        (step_dz * step_dz)
    )

    # Static tilt: current orientation vs original parked baseline.
    tilt_dx = x - park_base_x
    tilt_dy = y - park_base_y
    tilt_dz = z - park_base_z

    tilt_score = math.sqrt(
        (tilt_dx * tilt_dx) +
        (tilt_dy * tilt_dy) +
        (tilt_dz * tilt_dz)
    )

    is_motion = step_score > PARKED_MOTION_STEP_THRESHOLD
    is_strong_motion = step_score > PARKED_STRONG_STEP_THRESHOLD
    is_tilted = tilt_score > PARKED_TILT_SCORE_THRESHOLD

    # Simple tilt/rotation is treated separately.
    # It should not count toward camera triggering by itself.
    # But strong movement while tilted still counts as handling/shaking.
    is_tilt_transition = is_tilted and is_motion and not is_strong_motion

    # Only count suspicious handling if movement is not just a simple tilt transition.
    counts_as_handling = is_motion and not is_tilt_transition

    parked_hits_window.append(1 if counts_as_handling else 0)
    if len(parked_hits_window) > PARKED_WINDOW_SIZE:
        parked_hits_window.pop(0)

    parked_strong_hits_window.append(1 if is_strong_motion else 0)
    if len(parked_strong_hits_window) > PARKED_WINDOW_SIZE:
        parked_strong_hits_window.pop(0)

    hits = sum(parked_hits_window)
    strong_hits = sum(parked_strong_hits_window)

    # Classification.
    event_type = "idle"

    if hits >= PARKED_CAMERA_TRIGGER_HITS:
        event_type = "camera_trigger"

    elif is_tilt_transition:
        event_type = "tilt_transition"

    elif is_motion or is_strong_motion:
        event_type = "minor_bump"

    elif is_tilted:
        event_type = "tilted_static"

    else:
        event_type = "idle"

    should_emit = False
    wants_camera = False

    if event_type == "minor_bump":
        if can_fire_cooldown(parked_last_minor_bump_ms, PARKED_MINOR_BUMP_COOLDOWN_MS):
            should_emit = True
            parked_last_minor_bump_ms = current_ms

    elif event_type == "tilt_transition":
        if can_fire_cooldown(parked_last_tilt_transition_ms, PARKED_TILT_TRANSITION_COOLDOWN_MS):
            should_emit = True
            parked_last_tilt_transition_ms = current_ms

    elif event_type == "tilted_static":
        if can_fire_cooldown(parked_last_tilted_static_ms, PARKED_TILTED_STATIC_COOLDOWN_MS):
            should_emit = True
            parked_last_tilted_static_ms = current_ms

    elif event_type == "camera_trigger":
        wants_camera = True

        if can_fire_cooldown(parked_last_camera_event_ms, PARKED_CAMERA_EVENT_COOLDOWN_MS):
            should_emit = True
            parked_last_camera_event_ms = current_ms

            if not tracking_active and state != "camera_triggered":
                state = "camera_triggered"
                emit_state(state)

    # One-shot camera trigger command.
    # If the event wants camera, trigger_camera=true only once every 10 seconds.
    # Important: only update the camera trigger cooldown if a packet is actually emitted.
    trigger_camera = False
    if should_emit and wants_camera:
        if can_fire_cooldown(
            parked_last_camera_trigger_command_ms,
            PARKED_CAMERA_RETRIGGER_COOLDOWN_MS
        ):
            trigger_camera = True
            parked_last_camera_trigger_command_ms = current_ms

    if should_emit:
        emit(build_motion_packet(
            event_type,
            x, y, z, mag,
            step_score,
            tilt_score,
            hits,
            strong_hits,
            trigger_camera
        ), route="coral")


# ---------------- CRUISING STATE LOGIC ----------------

def check_post_impact_stillness():
    """
    After a possible impact, observe for CRUISING_POST_IMPACT_CHECK_MS.
    If the bike is mostly still and tilted from the cruising baseline,
    treat it as a possible crash.

    Returns:
    {
        "still_ratio": ...,
        "max_step_score": ...,
        "final_tilt_score": ...,
        "still_samples": ...,
        "total_samples": ...
    }
    """

    start_ms = now_ms()

    still_samples = 0
    total_samples = 0
    max_step_score = 0.0

    last_x, last_y, last_z, _ = read_acc_g()
    final_x = last_x
    final_y = last_y
    final_z = last_z

    while elapsed_ms(start_ms) < CRUISING_POST_IMPACT_CHECK_MS:
        x, y, z, mag = read_acc_g()

        dx = x - last_x
        dy = y - last_y
        dz = z - last_z

        step_score = math.sqrt((dx * dx) + (dy * dy) + (dz * dz))

        if step_score > max_step_score:
            max_step_score = step_score

        if step_score < CRUISING_POST_IMPACT_STILL_STEP_THRESHOLD:
            still_samples += 1

        total_samples += 1

        final_x = x
        final_y = y
        final_z = z

        last_x = x
        last_y = y
        last_z = z

        time.sleep_ms(SAMPLE_DELAY_MS)

    if total_samples == 0:
        still_ratio = 0.0
    else:
        still_ratio = still_samples / total_samples

    tilt_dx = final_x - cruise_base_x
    tilt_dy = final_y - cruise_base_y
    tilt_dz = final_z - cruise_base_z

    final_tilt_score = math.sqrt(
        (tilt_dx * tilt_dx) +
        (tilt_dy * tilt_dy) +
        (tilt_dz * tilt_dz)
    )

    return {
        "still_ratio": still_ratio,
        "max_step_score": max_step_score,
        "final_tilt_score": final_tilt_score,
        "still_samples": still_samples,
        "total_samples": total_samples
    }


def process_cruising_sample(current_ms, x, y, z, mag):
    global cruising_last_crash_alert_ms

    step_dx = x - prev_x
    step_dy = y - prev_y
    step_dz = z - prev_z

    step_score = math.sqrt(
        (step_dx * step_dx) +
        (step_dy * step_dy) +
        (step_dz * step_dz)
    )

    tilt_dx = x - cruise_base_x
    tilt_dy = y - cruise_base_y
    tilt_dz = z - cruise_base_z

    tilt_score = math.sqrt(
        (tilt_dx * tilt_dx) +
        (tilt_dy * tilt_dy) +
        (tilt_dz * tilt_dz)
    )

    delta_from_1g = abs(mag - 1.0)

    impact_detected = (
        delta_from_1g > CRUISING_IMPACT_DELTA_G_THRESHOLD or
        step_score > CRUISING_IMPACT_STEP_THRESHOLD
    )

    if impact_detected and elapsed_ms(cruising_last_crash_alert_ms) > CRUISING_CRASH_ALERT_COOLDOWN_MS:
        print("Possible impact detected. Checking post-impact stillness...")

        post = check_post_impact_stillness()

        still_ratio = post["still_ratio"]
        final_tilt_score = post["final_tilt_score"]

        stillness_confirmed = still_ratio >= CRUISING_POST_IMPACT_STILL_REQUIRED_RATIO
        tilt_confirmed = final_tilt_score >= CRUISING_CRASH_TILT_THRESHOLD

        if stillness_confirmed and tilt_confirmed:
            lat, lon, gps_status = try_gps_update(
                timeout_sec=GPS_FAST_TIMEOUT_SEC,
                force_timeout=True
            )

            extra = {
                "impact_step_score": round(step_score, 3),
                "impact_delta_from_1g": round(delta_from_1g, 3),
                "impact_tilt_score": round(tilt_score, 3),

                "post_impact_still_ratio": round(still_ratio, 3),
                "post_impact_max_step_score": round(post["max_step_score"], 3),
                "post_impact_final_tilt_score": round(final_tilt_score, 3),
                "post_impact_still_samples": post["still_samples"],
                "post_impact_total_samples": post["total_samples"],

                "backend_action": "notify_user_and_start_30s_confirmation_timer"
            }

            emit(build_cruising_packet(
                "possible_crash",
                x, y, z, mag,
                step_score,
                tilt_score,
                delta_from_1g,
                lat,
                lon,
                gps_status,
                extra
            ), route="backend")

            cruising_last_crash_alert_ms = now_ms()

        else:
            # Not a confirmed crash pattern.
            # Do not send this to backend.
            # Keep this local print only for debugging/tuning.
            print("Impact ignored: no crash pattern confirmed.")


# ---------------- MAIN LOOP ----------------

while True:
    current_ms = now_ms()

    # Later, uncomment once backend command channel is implemented.
    #
    # check_backend_commands()

    x, y, z, mag = read_acc_g()

    if SYSTEM_STATE == STATE_PARKED:
        process_parked_sample(current_ms, x, y, z, mag)

    elif SYSTEM_STATE == STATE_CRUISING:
        process_cruising_sample(current_ms, x, y, z, mag)

    else:
        print("Unknown SYSTEM_STATE:", SYSTEM_STATE)

    # GPS tracking is independent of parked/cruising state.
    # It only runs when tracking_active is True.
    maybe_send_gps_update(current_ms)

    prev_x = x
    prev_y = y
    prev_z = z
    prev_mag = mag

    time.sleep_ms(SAMPLE_DELAY_MS)