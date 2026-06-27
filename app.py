from flask import Flask, render_template, request, jsonify, Response
import paho.mqtt.client as mqtt
import socket
import ssl
import json
import threading
import time
import math
import base64
import io
import os
import sys
import random
import cv2
import numpy as np
from PIL import Image, ImageEnhance
from HersheyFonts import HersheyFonts

# --- LOAD CONFIGURATION ---
if not os.path.exists("config.json"):
    print("Error: config.json not found! Please run 'python setup.py' first.")
    sys.exit(1)

with open("config.json", "r") as f:
    config = json.load(f)

PRINTER_IP = config.get("PRINTER_IP", "")
ACCESS_CODE = config.get("ACCESS_CODE", "")
SERIAL_NUMBER = config.get("SERIAL_NUMBER", "")
# --------------------------

app = Flask(__name__)

MQTT_PORT = 8883
MQTT_USER = "bblp"
TOPIC_PUBLISH = f"device/{SERIAL_NUMBER}/request"
TOPIC_REPORT = f"device/{SERIAL_NUMBER}/report"

printer_state = {
    "is_homed": False,
    "position": {"x": 90, "y": 90, "z": 90}
}

sequence_id_counter = 2000
acked_sequences = set()

plot_active = False
plot_paused = False

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.username_pw_set(MQTT_USER, ACCESS_CODE)
client.tls_set(cert_reqs=ssl.CERT_NONE, tls_version=ssl.PROTOCOL_TLS)
client.tls_insecure_set(True)

def on_connect(client, userdata, flags, rc, properties=None):
    client.subscribe(TOPIC_REPORT)

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode('utf-8'))
        if "print" in payload and "sequence_id" in payload["print"]:
            seq_id = str(payload["print"]["sequence_id"])
            acked_sequences.add(seq_id)
    except Exception:
        pass

client.on_connect = on_connect
client.on_message = on_message

print(f"\nAttempting to connect to printer at {PRINTER_IP}...")
try:
    # Timeout reduced to 5 seconds. If it takes longer on a local network, something is blocking it.
    client.connect(PRINTER_IP, MQTT_PORT, 5)
    client.loop_start()
    print("Successfully connected to the printer via MQTT!\n")
except Exception as e:
    print(f"\n[WARNING] Could not connect to printer at {PRINTER_IP}.")
    print(f"Error details: {e}")
    print("The web server will still start, but plotting commands will fail until the printer is reachable.")
    print("Check if the printer is awake, the IP is correct, and LAN Only Mode is active.\n")

def send_printer_command(cmd, param=""):
    global sequence_id_counter
    sequence_id_counter += 1
    payload = {
        "print": {
            "sequence_id": str(sequence_id_counter),
            "command": cmd
        }
    }
    if param:
        payload["print"]["param"] = param
    client.publish(TOPIC_PUBLISH, json.dumps(payload, separators=(',', ':')))

def send_gcode_chunk(gcode_string):
    formatted = "".join(f"{line.strip()} \n" for line in gcode_string.strip().split('\n') if line.strip())
    send_printer_command("gcode_line", formatted)

def send_gcode_chunk_reliable(gcode_string):
    global sequence_id_counter, acked_sequences, plot_active, plot_paused

    formatted = "".join(f"{line.strip()} \n" for line in gcode_string.strip().split('\n') if line.strip())

    while plot_active:
        while plot_paused and plot_active:
            time.sleep(0.1)

        if not plot_active: return 0

        sequence_id_counter += 1
        seq_id = str(sequence_id_counter)

        payload = {
            "print": {
                "command": "gcode_line",
                "param": formatted,
                "sequence_id": seq_id
            }
        }

        send_start = time.time()
        client.publish(TOPIC_PUBLISH, json.dumps(payload, separators=(',', ':')))

        acked = False
        while time.time() - send_start < 2.0:
            if seq_id in acked_sequences:
                acked = True
                break
            time.sleep(0.01)

        if acked:
            return time.time() - send_start

    return 0

def generate_bambu_camera_stream():
    auth_packet = bytearray(
        [0x40, 0x00, 0x00, 0x00, 0x00, 0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    auth_packet += "bblp".encode('utf-8').ljust(32, b'\x00') + ACCESS_CODE.encode('utf-8').ljust(32, b'\x00')
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        sock = socket.create_connection((PRINTER_IP, 6000), timeout=5)
        secure_sock = ctx.wrap_socket(sock, server_hostname=PRINTER_IP)
        secure_sock.sendall(auth_packet)
        buffer = b''
        while True:
            chunk = secure_sock.recv(4096)
            if not chunk: break
            buffer += chunk
            if len(buffer) > 5000000: buffer = b''
            while True:
                start_idx, end_idx = buffer.find(b'\xff\xd8'), buffer.find(b'\xff\xd9')
                if start_idx != -1 and end_idx != -1:
                    if start_idx < end_idx:
                        jpg = buffer[start_idx:end_idx + 2]
                        buffer = buffer[end_idx + 2:]
                        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpg + b'\r\n')
                    else:
                        buffer = buffer[end_idx + 2:]
                else:
                    break
    except Exception:
        pass

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/video_feed')
def video_feed():
    return Response(generate_bambu_camera_stream(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/home', methods=['POST'])
def home_axes():
    send_gcode_chunk("G28\nG90\nG0 Z90 F600\nG0 X90 Y90 F12000")
    printer_state["is_homed"] = True
    printer_state["position"] = {"x": 90, "y": 90, "z": 90}
    return jsonify({"status": "success", "duration": 15, "state": printer_state})

@app.route('/api/move', methods=['POST'])
def move_axis():
    if not printer_state["is_homed"]: return jsonify({"status": "error", "message": "Home first!"}), 403

    axis = request.json.get('axis').upper()
    amount = float(request.json.get('amount'))
    speed = request.json.get('speed')

    new_pos = printer_state["position"][axis.lower()] + amount
    if new_pos < 0 or new_pos > 180:
        return jsonify({"status": "error", "message": f"HARD STOP: {axis} {new_pos} out of bounds."}), 400

    printer_state["position"][axis.lower()] = new_pos
    speed = float(speed) if speed else 12000

    if axis == 'Z' and speed > 1200:
        speed = 1200
    if axis in ['X', 'Y'] and speed > 18000:
        speed = 18000

    send_gcode_chunk(f"G90\nG1 {axis}{new_pos} F{speed}")
    return jsonify({"status": "success", "duration": (abs(amount) / (speed / 60.0)) + 0.2, "state": printer_state})

@app.route('/api/pause', methods=['POST'])
def pause_plot():
    global plot_paused
    plot_paused = True
    return jsonify({"status": "success"})

@app.route('/api/resume', methods=['POST'])
def resume_plot():
    global plot_paused
    plot_paused = False
    return jsonify({"status": "success"})

@app.route('/api/stop', methods=['POST'])
def stop_plot():
    global plot_active
    plot_active = False
    send_gcode_chunk("M410\nM18")
    return jsonify({"status": "success"})

def generate_text_paths(text, font_style, line_gap_mm, font_pct, min_x, max_x, min_y, max_y, auto_wrap):
    hf = HersheyFonts()
    hf.load_default_font(font_style)

    scale = (float(font_pct) / 100.0) * 0.5
    line_gap = float(line_gap_mm)
    target_w = max_x - min_x

    def get_text_width(t):
        if not t: return 0
        segs = list(hf.lines_for_text(t))
        if not segs: return 0
        return (max(max(p[0][0], p[1][0]) for p in segs) - min(min(p[0][0], p[1][0]) for p in segs)) * scale

    clean_text = text.replace('\r', '')
    paragraphs = clean_text.split('\n')
    wrapped_lines = []

    for p in paragraphs:
        if not p:
            wrapped_lines.append("")
            continue

        if auto_wrap:
            words = p.split(' ')
            current_line = words[0]
            for word in words[1:]:
                test_line = current_line + " " + word
                w = get_text_width(test_line)
                if w > target_w and current_line:
                    wrapped_lines.append(current_line)
                    current_line = word
                else:
                    current_line = test_line
            if current_line:
                wrapped_lines.append(current_line)
        else:
            wrapped_lines.append(p)

    final_paths = []
    ox = min_x
    current_y = max_y - (15 * scale)

    for line in wrapped_lines:
        if not line.strip():
            current_y -= line_gap
            continue

        segs = list(hf.lines_for_text(line))
        if not segs:
            current_y -= line_gap
            continue

        min_x_seg = min(min(p[0][0], p[1][0]) for p in segs)

        for (p1, p2) in segs:
            x1 = ox + (p1[0] - min_x_seg) * scale
            y1 = current_y - (p1[1] - 9) * scale
            x2 = ox + (p2[0] - min_x_seg) * scale
            y2 = current_y - (p2[1] - 9) * scale
            final_paths.append([{"x": x1, "y": y1}, {"x": x2, "y": y2}])

        current_y -= line_gap

    if not final_paths: return None, "Text is empty."
    return final_paths, "Success"

def prepare_image(base64_img, box_w, box_h, contrast):
    image_data = base64.b64decode(base64_img.split(',')[1])
    img = Image.open(io.BytesIO(image_data)).convert('L')
    img = ImageEnhance.Contrast(img).enhance(contrast)
    ppm = 4

    img_ratio = img.width / max(1, img.height)
    box_ratio = box_w / max(0.1, box_h)

    if img_ratio > box_ratio:
        final_w = box_w
        final_h = box_w / img_ratio
    else:
        final_h = box_h
        final_w = box_h * img_ratio

    px_w = int(final_w * ppm)
    px_h = int(final_h * ppm)
    img = img.resize((px_w, px_h))
    return img, px_w, px_h, ppm, final_w, final_h

def gen_hatch(img, px_w, px_h, ppm, final_w, gap_mm, ox, oy):
    final_h = px_h / ppm
    paths = []

    def get_val(x_mm, y_mm):
        px, py = int(x_mm * ppm), int(y_mm * ppm)
        if 0 <= px < px_w and 0 <= py < px_h: return img.getpixel((px, py))
        return 255

    def trace(starts, dx, dy, threshold):
        for sx, sy in starts:
            cx, cy = sx, sy
            drawing = False
            seg_start, last_valid = None, None
            while 0 <= cx <= final_w and 0 <= cy <= final_h:
                val = get_val(cx, cy)
                phys_x, phys_y = ox + cx, oy - cy
                if val < threshold:
                    if not drawing:
                        seg_start = {"x": phys_x, "y": phys_y}
                        drawing = True
                    last_valid = {"x": phys_x, "y": phys_y}
                else:
                    if drawing and math.hypot(last_valid['x'] - seg_start['x'], last_valid['y'] - seg_start['y']) > 0.5:
                        paths.append([seg_start, last_valid])
                    drawing = False
                cx += dx
                cy += dy
            if drawing and math.hypot(last_valid['x'] - seg_start['x'], last_valid['y'] - seg_start['y']) > 0.5:
                paths.append([seg_start, last_valid])

    step = 0.5
    trace([(0, y * gap_mm) for y in range(int(final_h / gap_mm))], step, 0, 210)
    trace([(x * gap_mm, 0) for x in range(int(final_w / gap_mm))], 0, step, 160)
    starts = [(x * gap_mm, 0) for x in range(int(final_w / gap_mm))] + [(0, y * gap_mm) for y in
                                                                        range(int(final_h / gap_mm))]
    trace(starts, step, step, 110)
    starts = [(x * gap_mm, final_h) for x in range(int(final_w / gap_mm))] + [(0, y * gap_mm) for y in
                                                                              range(int(final_h / gap_mm))]
    trace(starts, step, -step, 60)
    return paths

def gen_tsp(img, px_w, px_h, ppm, final_w, gap_mm, ox, oy):
    final_h = px_h / ppm
    num_dots = int(4000 / max(0.5, gap_mm))
    pts = []
    attempts = 0
    while len(pts) < num_dots and attempts < num_dots * 10:
        rx, ry = random.uniform(0, final_w), random.uniform(0, final_h)
        px, py = int(rx * ppm), int(ry * ppm)
        if px < px_w and py < px_h:
            prob = 1.0 - (img.getpixel((px, py)) / 255.0)
            if random.random() < prob: pts.append((rx, ry))
        attempts += 1
    if not pts: return []
    path = [pts.pop(0)]
    while pts:
        last = path[-1]
        best_idx, best_d = 0, float('inf')
        for i, p in enumerate(pts):
            d = (p[0] - last[0]) ** 2 + (p[1] - last[1]) ** 2
            if d < best_d: best_d, best_idx = d, i
        path.append(pts.pop(best_idx))
    paths = []
    for i in range(len(path) - 1):
        p1, p2 = {"x": ox + path[i][0], "y": oy - path[i][1]}, {"x": ox + path[i + 1][0], "y": oy - path[i + 1][1]}
        paths.append([p1, p2])
    return paths

def gen_canny(base64_img, box_w, box_h, contrast, min_x, max_x, min_y, max_y):
    nparr = np.frombuffer(base64.b64decode(base64_img.split(',')[1]), np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
    img = cv2.convertScaleAbs(img, alpha=contrast, beta=0)
    ppm = 4

    img_ratio = img.shape[1] / max(1, img.shape[0])
    box_ratio = box_w / max(0.1, box_h)

    if img_ratio > box_ratio:
        final_w = box_w
        final_h = box_w / img_ratio
    else:
        final_h = box_h
        final_w = box_h * img_ratio

    px_w = int(final_w * ppm)
    px_h = int(final_h * ppm)
    img = cv2.resize(img, (px_w, px_h))
    edges = cv2.Canny(img, 100, 200)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    ox = min_x + (box_w - final_w) / 2.0
    oy = max_y - (box_h - final_h) / 2.0

    paths = []
    for cnt in contours:
        for i in range(len(cnt) - 1):
            p1 = {"x": ox + (cnt[i][0][0] / ppm), "y": oy - (cnt[i][0][1] / ppm)}
            p2 = {"x": ox + (cnt[i + 1][0][0] / ppm), "y": oy - (cnt[i + 1][0][1] / ppm)}
            paths.append([p1, p2])
    return paths

def process_paths_request(data):
    bbox = data.get('bbox')
    if not bbox: return None, "Set Bounding Box (4 points) first."

    min_x, max_x = float(bbox['min_x']), float(bbox['max_x'])
    min_y, max_y = float(bbox['min_y']), float(bbox['max_y'])
    box_w, box_h = max_x - min_x, max_y - min_y

    if box_w <= 0 or box_h <= 0: return None, "Invalid Bounding Box Area"

    paths = []
    if data['type'] == 'text':
        paths, msg = generate_text_paths(
            data['text'], data['font'], data['line_spacing'],
            data['font_size'], min_x, max_x, min_y, max_y, data.get('auto_wrap', True)
        )
        if not paths:
            return None, msg
    else:
        method = data.get('method', 'hatch')
        try:
            if method == 'canny':
                paths = gen_canny(data['image'], box_w, box_h, float(data['img_contrast']), min_x, max_x, min_y, max_y)
            else:
                img, px_w, px_h, ppm, final_w, final_h = prepare_image(data['image'], box_w, box_h,
                                                                       float(data['img_contrast']))
                ox = min_x + (box_w - final_w) / 2.0
                oy = max_y - (box_h - final_h) / 2.0

                if method == 'tsp':
                    paths = gen_tsp(img, px_w, px_h, ppm, final_w, float(data['img_gap']), ox, oy)
                else:
                    paths = gen_hatch(img, px_w, px_h, ppm, final_w, float(data['img_gap']), ox, oy)

        except Exception as e:
            return None, str(e)

    if data.get('draw_bbox'):
        paths.append([{"x": min_x, "y": min_y}, {"x": max_x, "y": min_y}])
        paths.append([{"x": max_x, "y": min_y}, {"x": max_x, "y": max_y}])
        paths.append([{"x": max_x, "y": max_y}, {"x": min_x, "y": max_y}])
        paths.append([{"x": min_x, "y": max_y}, {"x": min_x, "y": min_y}])

    safe_paths = []
    for seg in paths:
        x1 = max(0.0, min(180.0, seg[0]['x']))
        y1 = max(0.0, min(180.0, seg[0]['y']))
        x2 = max(0.0, min(180.0, seg[1]['x']))
        y2 = max(0.0, min(180.0, seg[1]['y']))

        if abs(x1 - x2) < 0.001 and abs(y1 - y2) < 0.001:
            continue

        safe_paths.append([{"x": x1, "y": y1}, {"x": x2, "y": y2}])

    return safe_paths, "Success"

@app.route('/api/preview', methods=['POST'])
def preview_paths():
    paths, msg = process_paths_request(request.json)
    if not paths: return jsonify({"status": "error", "message": msg}), 400
    return jsonify({"status": "success", "paths": paths, "origin_z": request.json.get('bbox', {}).get('origin_z')})

@app.route('/api/plot', methods=['POST'])
def plot_paths():
    global plot_active, plot_paused, acked_sequences

    data = request.json
    paths, msg = process_paths_request(data)
    if not paths: return jsonify({"status": "error", "message": msg}), 400

    speed = int(data['speed'])
    origin_z = data.get('bbox', {}).get('origin_z')

    plot_active = True
    plot_paused = False
    acked_sequences.clear()

    threading.Thread(target=execute_plot, args=(paths, origin_z, speed)).start()
    return jsonify({"status": "success"})

def execute_plot(paths, base_z, speed):
    global plot_active
    hop_z = base_z + 4.0

    SAFE_Z_FEEDRATE = 1200
    SAFE_XY_FEEDRATE = 18000

    timed_commands = [
        {"cmd": "M17", "time": 0.1},
        {"cmd": "G90", "time": 0.1},
        {"cmd": f"G0 Z90 F{SAFE_Z_FEEDRATE}", "time": 1.5},
        {"cmd": f"G0 X90 Y90 F{SAFE_XY_FEEDRATE}", "time": 1.5},
        {"cmd": "M400", "time": 0.1}
    ]

    current_pos = {"x": 90.0, "y": 90.0}

    def is_close(pA, pB):
        return abs(pA['x'] - pB['x']) < 0.1 and abs(pA['y'] - pB['y']) < 0.1

    for segment in paths:
        p1, p2 = segment[0], segment[1]

        if not is_close(current_pos, p1):
            timed_commands.append({"cmd": f"G0 Z{hop_z:.2f} F{SAFE_Z_FEEDRATE}", "time": 0.3})
            dist = math.hypot(p1['x'] - current_pos['x'], p1['y'] - current_pos['y'])
            timed_commands.append({"cmd": f"G0 X{p1['x']:.2f} Y{p1['y']:.2f} F{SAFE_XY_FEEDRATE}",
                                   "time": (dist / (SAFE_XY_FEEDRATE / 60.0)) + 0.05})
            timed_commands.append({"cmd": f"G1 Z{base_z:.2f} F{SAFE_Z_FEEDRATE}", "time": 0.3})
        else:
            if abs(current_pos['x'] - p1['x']) > 0.01 or abs(current_pos['y'] - p1['y']) > 0.01:
                dist = math.hypot(p1['x'] - current_pos['x'], p1['y'] - current_pos['y'])
                timed_commands.append({"cmd": f"G1 X{p1['x']:.2f} Y{p1['y']:.2f} F{speed}",
                                       "time": (dist / (speed / 60.0)) + 0.02})

        dist = math.hypot(p2['x'] - p1['x'], p2['y'] - p1['y'])
        timed_commands.append(
            {"cmd": f"G1 X{p2['x']:.2f} Y{p2['y']:.2f} F{speed}", "time": (dist / (speed / 60.0)) + 0.02})
        current_pos = p2

    timed_commands.extend([
        {"cmd": f"G0 Z{hop_z:.2f} F{SAFE_Z_FEEDRATE}", "time": 0.3},
        {"cmd": f"G0 Z90 F{SAFE_Z_FEEDRATE}", "time": 1.5},
        {"cmd": f"G0 X90 Y90 F{SAFE_XY_FEEDRATE}", "time": 1.5},
        {"cmd": "M400", "time": 0.1},
        {"cmd": "M400 S1", "time": 1.0}
    ])

    chunk_size = 4
    virtual_buffer_time = 0.0
    MAX_BUFFER_TIME = 2.0

    for i in range(0, len(timed_commands), chunk_size):
        if not plot_active:
            break

        chunk = timed_commands[i:i + chunk_size]
        chunk_str = "\n".join([c["cmd"] for c in chunk])

        ack_time = send_gcode_chunk_reliable(chunk_str)
        chunk_duration = sum([c["time"] for c in chunk])

        virtual_buffer_time += chunk_duration
        virtual_buffer_time -= ack_time

        if virtual_buffer_time > MAX_BUFFER_TIME:
            sleep_for = virtual_buffer_time - (MAX_BUFFER_TIME * 0.5)
            if sleep_for > 0:
                time.sleep(sleep_for)
                virtual_buffer_time -= sleep_for

        if virtual_buffer_time < 0:
            virtual_buffer_time = 0.0

    plot_active = False
    printer_state["position"].update({"x": 90, "y": 90, "z": 90})

if __name__ == '__main__':
    # Debug mode is explicitly FALSE to prevent Remote Code Execution vulnerabilities
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)