from flask import Flask, render_template, request, jsonify, Response
import threading
import traceback

import backend.state as state
from backend.printer import setup_mqtt, send_gcode_chunk, send_printer_command, execute_plot_sd_wrapper, execute_plot
from backend.algorithms import process_paths_request, generate_full_gcode
from backend.camera import generate_bambu_camera_stream

app = Flask(__name__)

# Start MQTT Connection globally
setup_mqtt()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/video_feed')
def video_feed():
    return Response(generate_bambu_camera_stream(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/state', methods=['GET'])
def get_state():
    with state.state_lock:
        response = state.printer_state.copy()
        response["is_paused"] = state.plot_paused
    return jsonify(response)

@app.route('/api/home', methods=['POST'])
def home_axes():
    with state.state_lock:
        if state.is_busy():
            return jsonify({"status": "error", "message": "Printer is actively plotting! Stop it first."}), 400
        bed_size = float(request.json.get('bed_size', 180.0)) if request.json else 180.0
        mid = bed_size / 2.0
        state.printer_state.update({"is_homed": True, "position": {"x": mid, "y": mid, "z": 90}, "progress": 0})
        state.save_state()
    send_gcode_chunk(f"G28\nG90\nG0 Z90 F1200\nM400\nG0 X{mid:.1f} Y{mid:.1f} F18000\nM400")
    return jsonify({"status": "success", "duration": 15, "state": state.printer_state})

@app.route('/api/move', methods=['POST'])
def move_axis():
    with state.state_lock:
        if state.is_busy(): return jsonify({"status": "error", "message": "Printer is actively plotting!"}), 400
        if not state.printer_state["is_homed"]: return jsonify({"status": "error", "message": "Home first!"}), 403
        axis = request.json.get('axis').upper()
        try:
            amount, speed, bed_size = float(request.json.get('amount')), float(request.json.get('speed', 12000)), float(request.json.get('bed_size', 180.0))
            if speed <= 0 or bed_size <= 0: return jsonify({"status": "error", "message": "Invalid parameters"}), 400
        except (ValueError, TypeError):
            return jsonify({"status": "error", "message": "Invalid parameter types"}), 400

        new_pos = state.printer_state["position"][axis.lower()] + amount
        if not (0 <= new_pos <= bed_size):
            return jsonify({"status": "error", "message": f"HARD STOP: {axis} {new_pos} out of bounds."}), 400
        state.printer_state["position"][axis.lower()] = new_pos
        state.save_state()

    speed = min(speed, 1200) if axis == 'Z' else min(speed, 18000)
    send_gcode_chunk(f"G90\nG1 {axis}{new_pos} F{speed}\nM400")
    return jsonify({"status": "success", "duration": (abs(amount) / (speed / 60.0)) + 0.2, "state": state.printer_state})

@app.route('/api/goto_absolute', methods=['POST'])
def goto_absolute():
    with state.state_lock:
        if state.is_busy(): return jsonify({"status": "error", "message": "Printer is actively plotting!"}), 400
        if not state.printer_state["is_homed"]: return jsonify({"status": "error", "message": "Home first!"}), 403
        try:
            speed, bed_size, z_hop = min(float(request.json.get('speed', 12000)), 18000), float(request.json.get('bed_size', 180.0)), float(request.json.get('z_hop', 4.0))
            if speed <= 0 or z_hop < 0 or bed_size <= 0: return jsonify({"status": "error", "message": "Invalid parameters"}), 400
        except (ValueError, TypeError): return jsonify({"status": "error", "message": "Invalid parameter types"}), 400

        x, y, z = request.json.get('x'), request.json.get('y'), request.json.get('z')
        cmds, duration = ["G90"], 0.2
        if None not in (x, y, z):
            nx, ny, nz = float(x), float(y), float(z)
            if not (0 <= nx <= bed_size and 0 <= ny <= bed_size and 0 <= nz <= bed_size):
                return jsonify({"status": "error", "message": "HARD STOP: Out of bounds."}), 400
            safe_z = min(nz + 2.0 * z_hop, bed_size)
            state.printer_state["position"].update({'x': nx, 'y': ny, 'z': nz})
            cmds.extend([f"G1 Z{safe_z:.2f} F1200", "M400", f"G1 X{nx:.2f} Y{ny:.2f} F{speed}", "M400", f"G1 Z{nz:.2f} F1200", "M400"])
            duration += 4.0
        state.save_state()
    send_gcode_chunk("\n".join(cmds))
    return jsonify({"status": "success", "duration": duration, "state": state.printer_state})

@app.route('/api/pause', methods=['POST'])
def pause_plot():
    state.plot_paused = True
    if state.printer_state["status"] == "Printing SD": send_printer_command("pause")
    return jsonify({"status": "success"})

@app.route('/api/resume', methods=['POST'])
def resume_plot():
    state.plot_paused = False
    if state.printer_state["status"] == "Printing SD": send_printer_command("resume")
    return jsonify({"status": "success"})

@app.route('/api/stop', methods=['POST'])
def stop_plot():
    with state.state_lock:
        state.plot_active = False
        state.printer_state.update({"progress": 0, "status": "Idle", "is_homed": False})
        send_gcode_chunk("M410")
        send_printer_command("stop")
        state.save_state()
    return jsonify({"status": "success"})

@app.route('/api/preview', methods=['POST'])
def preview_paths():
    paths, out_paths, msg = process_paths_request(request.json)
    if paths is None: return jsonify({"status": "error", "message": msg}), 400
    return jsonify({"status": "success", "paths": paths, "out_paths": out_paths, "origin_z": request.json.get('bbox', {}).get('origin_z')})

@app.route('/api/plot_sd', methods=['POST'])
def plot_paths_sd():
    with state.state_lock:
        if state.is_busy(): return jsonify({"status": "error", "message": "A plot is already running!"}), 400
        data = request.json
        paths, out_paths, msg = process_paths_request(data)
        if paths is None: return jsonify({"status": "error", "message": msg}), 400
        try:
            speed, z_hop, bed_size = min(float(data.get('speed', 12000)), 18000.0), float(data.get('z_hop', 4.0)), float(data.get('bed_size', 180.0))
            origin_z = float(data.get('bbox', {}).get('origin_z', 0.0))
            if speed <= 0 or z_hop < 0 or bed_size <= 0 or not (0 <= origin_z <= bed_size): raise ValueError()
        except Exception: return jsonify({"status": "error", "message": "Invalid plotting parameters."}), 400

        gcode_str = generate_full_gcode(paths, origin_z, speed, z_hop, bed_size)
        state.plot_active, state.plot_paused = True, False
        state.printer_state.update({"progress": 0, "status": "Uploading"})
        state.save_state()
        threading.Thread(target=execute_plot_sd_wrapper, args=(gcode_str,)).start()
        return jsonify({"status": "success"})

def execute_plot_wrapper(paths, base_z, speed, z_hop, bed_size):
    try:
        execute_plot(paths, base_z, speed, z_hop, bed_size)
    except Exception as e:
        print(f"\n[FATAL ERROR] The plotting thread crashed entirely: {e}")
        traceback.print_exc()
        with state.state_lock:
            state.plot_active = False
            state.printer_state.update({"progress": 0, "status": "Idle"})
            state.save_state()

@app.route('/api/plot', methods=['POST'])
def plot_paths():
    with state.state_lock:
        if state.is_busy(): return jsonify({"status": "error", "message": "A plot is already running!"}), 400
        data = request.json
        paths, out_paths, msg = process_paths_request(data)
        if paths is None: return jsonify({"status": "error", "message": msg}), 400
        try:
            speed, z_hop, bed_size = float(data.get('speed', 12000)), float(data.get('z_hop', 4.0)), float(data.get('bed_size', 180.0))
            origin_z = float(data.get('bbox', {}).get('origin_z', 0.0))
            if speed <= 0 or z_hop < 0 or bed_size <= 0 or not (0 <= origin_z <= bed_size): raise ValueError()
        except Exception: return jsonify({"status": "error", "message": "Invalid plotting parameters."}), 400

        state.plot_active, state.plot_paused = True, False
        state.acked_sequences.clear()
        state.printer_state.update({"progress": 0, "status": "Streaming"})
        state.save_state()
        threading.Thread(target=execute_plot_wrapper, args=(paths, origin_z, speed, z_hop, bed_size)).start()
        return jsonify({"status": "success"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5050, debug=False, threaded=True)