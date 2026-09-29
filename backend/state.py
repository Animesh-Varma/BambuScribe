import threading
import json
import os

STATE_FILE = "printer_state.json"

state_lock = threading.Lock()
plot_active = False
plot_paused = False
sequence_id_counter = 2000
acked_sequences = set()

printer_state = {
    "is_homed": False,
    "position": {"x": 90, "y": 90, "z": 90},
    "progress": 0,
    "status": "Idle"
}

if os.path.exists(STATE_FILE):
    try:
        os.remove(STATE_FILE)
        print("[INFO] Cleared stale printer_state.json from previous session.")
    except Exception as e:
        print(f"[WARNING] Could not delete stale printer_state.json: {e}")

def save_state():
    """Save state to local disk for persistence during the active server runtime."""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(printer_state, f)
    except Exception:
        pass

def is_busy():
    """Check if the printer is actively plotting."""
    return plot_active or printer_state.get("status") != "Idle"