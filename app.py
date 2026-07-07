"""
BambuScribe Backend Application

A Flask-based server that translates text and images into G-code paths.
It communicates via MQTT with a Bambu Lab 3D printer, allowing the printer
to be used as a 2D pen plotter. Features include manual movement, bounding
box tracking, live camera streaming, and various image rendering techniques.
"""

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
import traceback
import ftplib
import cv2
import numpy as np
import zipfile
import hashlib
from PIL import Image, ImageEnhance
from HersheyFonts import HersheyFonts

if not os.path.exists("config.json"):
    print("Error: config.json not found! Please run 'python setup.py' first.")
    sys.exit(1)

with open("config.json", "r") as f:
    config = json.load(f)

PRINTER_IP = config.get("PRINTER_IP", "")
ACCESS_CODE = config.get("ACCESS_CODE", "")
SERIAL_NUMBER = config.get("SERIAL_NUMBER", "")

app = Flask(__name__)

MQTT_PORT = 8883
MQTT_USER = "bblp"
TOPIC_PUBLISH = f"device/{SERIAL_NUMBER}/request"
TOPIC_REPORT = f"device/{SERIAL_NUMBER}/report"

printer_state = {
    "is_homed": False,
    "position": {"x": 90, "y": 90, "z": 90},
    "progress": 0,
    "status": "Idle"
}

sequence_id_counter = 2000
acked_sequences = set()

plot_active = False
plot_paused = False

# MQTT setup requiring TLS encryption for LAN Mode
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.username_pw_set(MQTT_USER, ACCESS_CODE)
client.tls_set(cert_reqs=ssl.CERT_NONE, tls_version=ssl.PROTOCOL_TLS)
client.tls_insecure_set(True)


def on_connect(client, userdata, flags, rc, properties=None):
    """Subscribe to the printer's report topic upon successful MQTT connection."""
    client.subscribe(TOPIC_REPORT)


def on_message(client, userdata, msg):
    """
    Handle incoming MQTT messages to track sequence_ids and SD print progress.
    """
    global printer_state, plot_active
    try:
        payload = json.loads(msg.payload.decode('utf-8'))
        if "print" in payload:
            p_data = payload["print"]
            if "sequence_id" in p_data:
                seq_id = str(p_data["sequence_id"])
                acked_sequences.add(seq_id)

            # Monitor autonomous SD card printing state
            if printer_state.get("status") == "Printing SD":
                if "mc_percent" in p_data:
                    printer_state["progress"] = p_data["mc_percent"]
                    if p_data["mc_percent"] == 100:
                        printer_state["status"] = "Idle"
                        plot_active = False

                if "gcode_state" in p_data:
                    state = p_data["gcode_state"]
                    if state in ["FINISH", "FAILED"]:
                        printer_state["status"] = "Idle"
                        plot_active = False
                        printer_state["progress"] = 100
    except Exception:
        pass


client.on_connect = on_connect
client.on_message = on_message

print(f"\nAttempting to connect to printer at {PRINTER_IP}...")
try:
    client.connect(PRINTER_IP, MQTT_PORT, 5)
    client.loop_start()
    print("Successfully connected to the printer via MQTT!\n")
except Exception as e:
    print(f"\n[WARNING] Could not connect to printer at {PRINTER_IP}.")
    print(f"Error details: {e}")
    print("The web server will still start, but plotting commands will fail until the printer is reachable.")
    print("Check if the printer is awake, the IP is correct, and LAN Only Mode is active.\n")


# Implicit FTPS Class definition for Bambu Printers (Port 990)
class ImplicitFTP_TLS(ftplib.FTP_TLS):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def connect(self, host='', port=0, timeout=-999, source_address=None):
        if host != '': self.host = host
        if port > 0: self.port = port
        if timeout != -999: self.timeout = timeout
        if source_address is not None: self.source_address = source_address

        timeout_val = getattr(self, 'timeout', 60)
        if timeout_val is not None and not timeout_val: raise ValueError('Timeout must be greater than 0')

        self.sock = socket.create_connection((self.host, self.port), timeout_val, getattr(self, 'source_address', None))
        self.af = self.sock.family
        if getattr(self, 'context', None) is None:
            self.context = ssl.create_default_context()
            self.context.check_hostname = False
            self.context.verify_mode = ssl.CERT_NONE

        self.sock = self.context.wrap_socket(self.sock, server_hostname=self.host)
        self.file = self.sock.makefile('r', encoding=getattr(self, 'encoding', 'utf-8'))
        self.welcome = self.getresp()
        return self.welcome


def upload_to_printer(file_bytes, filename="bambuscribe_plot.gcode.3mf"):
    """Securely uploads packed bytes to the printer's SD card and tracks live progress."""
    global plot_active, printer_state
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    # Set timeout to 30 seconds to prevent premature disconnection on big files
    ftp = ImplicitFTP_TLS(context=ctx, timeout=30)
    ftp.connect(host=PRINTER_IP, port=990)
    ftp.login(user="bblp", passwd=ACCESS_CODE)
    ftp.prot_p()

    bio = io.BytesIO(file_bytes)
    total_size = len(file_bytes)
    uploaded_size = [0]

    def upload_callback(data):
        if not plot_active:
            raise Exception("Upload aborted by user.")
        uploaded_size[0] += len(data)
        # Cap upload progress to 99% until the print actually starts via MQTT.
        printer_state["progress"] = min(99, int((uploaded_size[0] / total_size) * 100))

    try:
        ftp.storbinary(f"STOR /{filename}", bio, blocksize=8192, callback=upload_callback)
    except Exception as e:
        # Bambu vsftpd sometimes disconnects without sending 226 Transfer Complete.
        # If all bytes were already sent, we ignore the error.
        if uploaded_size[0] >= total_size:
            print("[SD Plotting] Ignored FTP disconnect at end of transfer.")
        else:
            raise e
    finally:
        try:
            ftp.quit()
        except:
            pass


def send_printer_command(cmd, param=""):
    """Format and send a standard command dictionary via MQTT to the printer."""
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
    """Send a fire-and-forget chunk of G-code commands."""
    formatted = "".join(f"{line.strip()} \n" for line in gcode_string.strip().split('\n') if line.strip())
    send_printer_command("gcode_line", formatted)


def send_gcode_chunk_reliable(gcode_string):
    """
    Send a chunk of G-code and verify the printer acknowledges it.
    Blocks until acknowledgement or pauses if the user stops the plot.
    Includes a retry mechanism for dropped MQTT packets.

    Returns:
        float: Time taken (in seconds) to successfully send and verify the chunk.
    """
    global sequence_id_counter, acked_sequences, plot_active, plot_paused

    formatted = "".join(f"{line.strip()} \n" for line in gcode_string.strip().split('\n') if line.strip())

    sequence_id_counter += 1
    seq_id = str(sequence_id_counter)

    payload = {
        "print": {
            "command": "gcode_line",
            "param": formatted,
            "sequence_id": seq_id
        }
    }

    payload_str = json.dumps(payload, separators=(',', ':'))

    while plot_active:
        while plot_paused and plot_active:
            time.sleep(0.1)

        if not plot_active: return 0

        send_start = time.time()
        client.publish(TOPIC_PUBLISH, payload_str, qos=0)

        acked = False
        while time.time() - send_start < 8.0:
            if not plot_active: return 0
            if seq_id in acked_sequences:
                acked = True
                acked_sequences.discard(seq_id)
                break
            time.sleep(0.01)

        if acked:
            return time.time() - send_start

        print(f"[RETRY] Chunk {seq_id} dropped by printer queue. Resending chunk...")
        time.sleep(0.5)

    return 0


def generate_bambu_camera_stream():
    """
    Connect to the Bambu Lab proprietary camera stream port (6000).
    Performs the 32-byte authentication handshake, decodes the MJPEG
    byte sequence, and yields image frames to the web interface.
    """
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


@app.route('/api/state', methods=['GET'])
def get_state():
    global printer_state, plot_paused
    response = printer_state.copy()
    response["is_paused"] = plot_paused
    return jsonify(response)


@app.route('/api/home', methods=['POST'])
def home_axes():
    """Send standard home G-code (G28) and center the toolhead."""
    bed_size = float(request.json.get('bed_size', 180.0)) if request.json else 180.0
    mid = bed_size / 2.0
    send_gcode_chunk(f"G28\nG90\nG0 Z90 F600\nM400\nG0 X{mid:.1f} Y{mid:.1f} F12000\nM400")
    printer_state["is_homed"] = True
    printer_state["position"] = {"x": mid, "y": mid, "z": 90}
    printer_state["progress"] = 0
    return jsonify({"status": "success", "duration": 15, "state": printer_state})


@app.route('/api/move', methods=['POST'])
def move_axis():
    """Move a specific axis relative to the current position, enforcing hardware limits."""
    if not printer_state["is_homed"]:
        return jsonify({"status": "error", "message": "Home first!"}), 403

    axis = request.json.get('axis').upper()
    amount = float(request.json.get('amount'))
    speed = request.json.get('speed')
    bed_size = float(request.json.get('bed_size', 180.0))

    new_pos = printer_state["position"][axis.lower()] + amount
    if new_pos < 0 or new_pos > bed_size:
        return jsonify({"status": "error", "message": f"HARD STOP: {axis} {new_pos} out of bounds."}), 400

    printer_state["position"][axis.lower()] = new_pos
    speed = float(speed) if speed else 12000

    if axis == 'Z' and speed > 1200:
        speed = 1200
    if axis in ['X', 'Y'] and speed > 18000:
        speed = 18000

    send_gcode_chunk(f"G90\nG1 {axis}{new_pos} F{speed}\nM400")
    return jsonify({"status": "success", "duration": (abs(amount) / (speed / 60.0)) + 0.2, "state": printer_state})


@app.route('/api/goto_absolute', methods=['POST'])
def goto_absolute():
    """Move the toolhead to specific absolute XYZ coordinates, enforcing a safe Z-hop pathing."""
    if not printer_state["is_homed"]:
        return jsonify({"status": "error", "message": "Home first!"}), 403

    x = request.json.get('x')
    y = request.json.get('y')
    z = request.json.get('z')
    speed = request.json.get('speed', 12000)
    bed_size = float(request.json.get('bed_size', 180.0))
    z_hop = float(request.json.get('z_hop', 4.0))

    cmds = ["G90"]
    duration = 0.2

    if x is not None and y is not None and z is not None:
        new_x, new_y, new_z = float(x), float(y), float(z)

        if new_x < 0 or new_x > bed_size or new_y < 0 or new_y > bed_size:
            return jsonify({"status": "error", "message": "HARD STOP: XY out of bounds."}), 400
        if new_z < 0 or new_z > bed_size:
            return jsonify({"status": "error", "message": f"HARD STOP: Z {new_z} out of bounds."}), 400

        safe_z = min(new_z + 2.0 * z_hop, bed_size)

        printer_state["position"]['z'] = safe_z
        cmds.append(f"G1 Z{safe_z:.2f} F1200")
        cmds.append("M400")
        duration += 1.0

        printer_state["position"]['x'] = new_x
        printer_state["position"]['y'] = new_y
        cmds.append(f"G1 X{new_x:.2f} Y{new_y:.2f} F{speed}")
        cmds.append("M400")
        duration += 2.0

        printer_state["position"]['z'] = new_z
        cmds.append(f"G1 Z{new_z:.2f} F1200")
        cmds.append("M400")
        duration += 1.0

    send_gcode_chunk("\n".join(cmds))
    return jsonify({"status": "success", "duration": duration, "state": printer_state})


@app.route('/api/pause', methods=['POST'])
def pause_plot():
    """Set flag to pause the continuous execution of path chunks."""
    global plot_paused, printer_state
    plot_paused = True
    if printer_state["status"] == "Printing SD":
        send_printer_command("pause")
    return jsonify({"status": "success"})


@app.route('/api/resume', methods=['POST'])
def resume_plot():
    """Unset flag to resume the continuous execution of path chunks."""
    global plot_paused, printer_state
    plot_paused = False
    if printer_state["status"] == "Printing SD":
        send_printer_command("resume")
    return jsonify({"status": "success"})


@app.route('/api/stop', methods=['POST'])
def stop_plot():
    """Hard cancel plot execution, kill motor movement, and clear progress."""
    global plot_active, printer_state
    plot_active = False
    printer_state["progress"] = 0
    printer_state["status"] = "Idle"
    send_gcode_chunk("M410\nM18")
    send_printer_command("stop")
    return jsonify({"status": "success"})


def generate_text_paths(text, font_style, line_gap_mm, font_pct, min_x, max_x, min_y, max_y, auto_wrap):
    """
    Generate absolute path segments representing text mapped into physical millimeters,
    utilizing the single-line vector Hershey Fonts.

    Returns:
        tuple: (list_of_paths, message_string)
    """
    hf = HersheyFonts()
    hf.load_default_font(font_style)

    line_gap = float(line_gap_mm)
    base_scale = line_gap / 25.0
    scale = base_scale * (float(font_pct) / 100.0)
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
    current_y = max_y - line_gap

    for line in wrapped_lines:
        if not line.strip():
            current_y -= line_gap
            continue

        if current_y - (15 * scale) < min_y:
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

    if not final_paths:
        return None, "Text is empty or completely exceeds bounding box bounds."

    for segment in final_paths:
        for pt in segment:
            if pt['x'] < min_x - 0.5 or pt['x'] > max_x + 0.5 or pt['y'] < min_y - 0.5 or pt['y'] > max_y + 0.5:
                return None, "Text exceeds the bounding box! Please use Auto-Wrap, reduce scale, or increase bounding box size."

    return final_paths, "Success"


def get_rotated_image_pil(base64_img, contrast, rotation):
    """Decode base64 string to a PIL Image, apply rotations, and adjust contrast."""
    image_data = base64.b64decode(base64_img.split(',')[1])
    img = Image.open(io.BytesIO(image_data)).convert('L')
    if rotation != 0:
        img = img.rotate(-rotation, expand=True, fillcolor=255)
    img = ImageEnhance.Contrast(img).enhance(contrast)
    return img


def prepare_image(img, box_w, box_h):
    """Calculate boundaries and resize the physical pixel map based on bounding box size."""
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
    """
    Generate paths using standard line cross-hatching. Darker areas
    generate denser, more frequent vector lines.
    """
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
    """
    Generate paths using Stippling via a Traveling Salesman approach.
    Points are clustered heavily in dark areas, and connected in an optimized line.
    """
    final_h = px_h / ppm
    num_dots = int(15000 / max(0.5, gap_mm))
    pts = []
    attempts = 0

    rng = np.random.default_rng(42)

    while len(pts) < num_dots and attempts < num_dots * 20:
        rx = rng.uniform(0, final_w)
        ry = rng.uniform(0, final_h)
        px, py = int(rx * ppm), int(ry * ppm)
        if px < px_w and py < px_h:
            prob = 1.0 - (img.getpixel((px, py)) / 255.0)
            if rng.random() < prob:
                pts.append((rx, ry))
        attempts += 1

    if not pts: return []

    pts_arr = np.array(pts)
    path = [pts_arr[0]]

    mask = np.ones(len(pts_arr), dtype=bool)
    mask[0] = False

    curr_pt = pts_arr[0]

    for _ in range(len(pts_arr) - 1):
        dists = (pts_arr[:, 0] - curr_pt[0]) ** 2 + (pts_arr[:, 1] - curr_pt[1]) ** 2
        dists[~mask] = np.inf

        best_idx = np.argmin(dists)
        curr_pt = pts_arr[best_idx]
        path.append(curr_pt)
        mask[best_idx] = False

    paths = []
    for i in range(len(path) - 1):
        p1 = {"x": ox + path[i][0], "y": oy - path[i][1]}
        p2 = {"x": ox + path[i + 1][0], "y": oy - path[i + 1][1]}

        if math.hypot(p1['x'] - p2['x'], p1['y'] - p2['y']) > 3.0:
            paths.append([p1, {"x": p1['x'] + 0.01, "y": p1['y']}])
        else:
            paths.append([p1, p2])

    last_p = {"x": ox + path[-1][0], "y": oy - path[-1][1]}
    paths.append([last_p, {"x": last_p['x'] + 0.01, "y": last_p['y']}])

    return paths


def gen_canny(img_pil, box_w, box_h, min_x, max_x, min_y, max_y):
    """
    Generate paths by utilizing OpenCV's Canny Edge Detection to
    physically trace the stark borders in an image.
    """
    img_np = np.array(img_pil)
    ppm = 10

    img_ratio = img_np.shape[1] / max(1, img_np.shape[0])
    box_ratio = box_w / max(0.1, box_h)

    if img_ratio > box_ratio:
        final_w = box_w
        final_h = box_w / img_ratio
    else:
        final_h = box_h
        final_w = box_h * img_ratio

    px_w = int(final_w * ppm)
    px_h = int(final_h * ppm)
    img_resized = cv2.resize(img_np, (px_w, px_h))
    edges = cv2.Canny(img_resized, 100, 200)

    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    ox = min_x + (box_w - final_w) / 2.0
    oy = max_y - (box_h - final_h) / 2.0

    paths = []
    for cnt in contours:
        if len(cnt) == 1:
            p1 = {"x": ox + (cnt[0][0][0] / ppm), "y": oy - (cnt[0][0][1] / ppm)}
            paths.append([p1, {"x": p1['x'] + 0.01, "y": p1['y']}])
        else:
            for i in range(len(cnt) - 1):
                p1 = {"x": ox + (cnt[i][0][0] / ppm), "y": oy - (cnt[i][0][1] / ppm)}
                p2 = {"x": ox + (cnt[i + 1][0][0] / ppm), "y": oy - (cnt[i + 1][0][1] / ppm)}
                paths.append([p1, p2])

            p_end = {"x": ox + (cnt[-1][0][0] / ppm), "y": oy - (cnt[-1][0][1] / ppm)}
            p_start = {"x": ox + (cnt[0][0][0] / ppm), "y": oy - (cnt[0][0][1] / ppm)}

            if abs(p_end['x'] - p_start['x']) > 0.001 or abs(p_end['y'] - p_start['y']) > 0.001:
                paths.append([p_end, p_start])

    return paths


def process_paths_request(data):
    """Central router that generates line paths."""
    bbox = data.get('bbox')
    bed_size = float(data.get('bed_size', 180.0))
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
            img_scale = float(data.get('img_scale', 100)) / 100.0
            offset_x = float(data.get('img_offset_x', 0))
            offset_y = float(data.get('img_offset_y', 0))
            rotation = float(data.get('img_rotate', 0))

            img_pil = get_rotated_image_pil(data['image'], float(data['img_contrast']), rotation)

            if method == 'canny':
                raw_paths = gen_canny(img_pil, box_w, box_h, min_x, max_x, min_y, max_y)
                cx = min_x + box_w / 2.0
                cy = min_y + box_h / 2.0
                for seg in raw_paths:
                    p1x = cx + (seg[0]['x'] - cx) * img_scale + offset_x
                    p1y = cy + (seg[0]['y'] - cy) * img_scale - offset_y
                    p2x = cx + (seg[1]['x'] - cx) * img_scale + offset_x
                    p2y = cy + (seg[1]['y'] - cy) * img_scale - offset_y
                    paths.append([{"x": p1x, "y": p1y}, {"x": p2x, "y": p2y}])
            else:
                img, px_w, px_h, ppm, final_w, final_h = prepare_image(img_pil, box_w, box_h)
                final_w *= img_scale
                final_h *= img_scale
                ox = min_x + (box_w - final_w) / 2.0 + offset_x
                oy = max_y - (box_h - final_h) / 2.0 - offset_y

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
        x1 = max(0.0, min(bed_size, seg[0]['x']))
        y1 = max(0.0, min(bed_size, seg[0]['y']))
        x2 = max(0.0, min(bed_size, seg[1]['x']))
        y2 = max(0.0, min(bed_size, seg[1]['y']))

        if abs(x1 - x2) < 0.001 and abs(y1 - y2) < 0.001:
            continue
        safe_paths.append([{"x": x1, "y": y1}, {"x": x2, "y": y2}])

    return safe_paths, "Success"


@app.route('/api/preview', methods=['POST'])
def preview_paths():
    """Endpoint for UI to fetch calculated paths for 3D visualizer representation."""
    paths, msg = process_paths_request(request.json)
    if not paths: return jsonify({"status": "error", "message": msg}), 400
    return jsonify({"status": "success", "paths": paths, "origin_z": request.json.get('bbox', {}).get('origin_z')})


def generate_full_gcode(paths, base_z, speed, z_hop, bed_size):
    """Calculates the complete static G-Code payload optimized for autonomous SD card printing."""
    hop_z = min(base_z + z_hop, bed_size)
    mid = float(bed_size) / 2.0
    SAFE_Z_FEEDRATE = 400
    SAFE_XY_FEEDRATE = 18000

    gcode = [
        "M104 S0 ; turn off nozzle heater",
        "M140 S0 ; turn off bed heater",
        "M106 S0 ; turn off fan",
        "M17",
        "G90",
        f"G1 Z90 F{SAFE_Z_FEEDRATE}",
        f"G0 X{mid:.1f} Y{mid:.1f} F{SAFE_XY_FEEDRATE}",
        "M400"
    ]

    current_pos = {"x": mid, "y": mid}

    def is_close(pA, pB):
        return abs(pA['x'] - pB['x']) < 0.03 and abs(pA['y'] - pB['y']) < 0.03

    for segment in paths:
        p1, p2 = segment[0], segment[1]

        if not is_close(current_pos, p1):
            gcode.extend([
                "M400",
                f"G1 Z{hop_z:.2f} F{SAFE_Z_FEEDRATE}",
                f"G0 X{p1['x']:.2f} Y{p1['y']:.2f} F{SAFE_XY_FEEDRATE}",
                "M400",
                f"G1 Z{base_z:.2f} F{SAFE_Z_FEEDRATE}",
                "M400"
            ])
        else:
            if abs(current_pos['x'] - p1['x']) > 0.005 or abs(current_pos['y'] - p1['y']) > 0.005:
                gcode.append(f"G1 X{p1['x']:.2f} Y{p1['y']:.2f} F{speed}")

        gcode.append(f"G1 X{p2['x']:.2f} Y{p2['y']:.2f} F{speed}")
        current_pos = p2

    gcode.extend([
        "M400",
        f"G1 Z{hop_z:.2f} F{SAFE_Z_FEEDRATE}",
        f"G1 Z90 F{SAFE_Z_FEEDRATE}",
        f"G0 X{mid:.1f} Y{mid:.1f} F{SAFE_XY_FEEDRATE}",
        "M400 S1"
    ])

    return "\n".join(gcode)


def execute_plot_sd_wrapper(gcode_str):
    """Background wrapper for generating a 3MF, uploading it, and initiating a full SD print."""
    global plot_active, printer_state, sequence_id_counter
    try:
        print("[SD Plotting] Packaging G-code into Bambu 3MF archive...")
        # Force standard UNIX line endings which Bambu expects natively
        gcode_str = gcode_str.replace('\r\n', '\n') + "\n"

        # Package the raw g-code into a completely standard .3MF (ZIP) metadata wrapper
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr("Metadata/plate_1.gcode", gcode_str)

            content_types = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
                '  <Default Extension="gcode" ContentType="application/vnd.bambulab.gcode"/>\n'
                '  <Default Extension="config" ContentType="application/xml"/>\n'
                '</Types>'
            )
            zip_file.writestr("[Content_Types].xml", content_types)

            slice_info = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<config>\n'
                '  <plate>\n'
                '    <metadata key="index" value="1"/>\n'
                '    <metadata key="gcode_file" value="Metadata/plate_1.gcode"/>\n'
                '    <metadata key="prediction_estimation_time" value="60"/>\n'
                '  </plate>\n'
                '</config>'
            )
            zip_file.writestr("Metadata/slice_info.config", slice_info)

        archive_bytes = zip_buffer.getvalue()
        md5_hash = hashlib.md5(archive_bytes).hexdigest()
        filename = "bambuscribe_plot.gcode.3mf"

        print(f"[SD Plotting] Starting upload to SD card ({len(archive_bytes)} bytes)...")
        upload_to_printer(archive_bytes, filename)

        if not plot_active:
            print("[SD Plotting] Upload aborted.")
            return

        print(f"[SD Plotting] Upload complete. Initiating {filename} on printer...")
        printer_state["status"] = "Printing SD"
        printer_state["progress"] = 0

        time.sleep(3.0)

        sequence_id_counter += 1
        payload = {
            "print": {
                "sequence_id": str(sequence_id_counter),
                "command": "project_file",
                "param": "Metadata/plate_1.gcode",
                "project_id": "0",
                "profile_id": "0",
                "task_id": "0",
                "subtask_id": "0",
                "subtask_name": "BambuScribe Plot",
                "file": f"/{filename}",
                "url": f"ftp://{PRINTER_IP}/{filename}",
                "md5": md5_hash,
                "use_ams": False,
                "bed_leveling": False,
                "vibration_cali": False,
                "layer_inspect": False,
                "flow_cali": False,
                "timelapse": False
            }
        }
        client.publish(TOPIC_PUBLISH, json.dumps(payload, separators=(',', ':')))
        print("[SD Plotting] Print command successfully sent to printer!")

    except Exception as e:
        print(f"\n[SD Plotting Error]: {e}")
        traceback.print_exc()
        plot_active = False
        printer_state["status"] = "Idle"
        printer_state["progress"] = 0


@app.route('/api/plot_sd', methods=['POST'])
def plot_paths_sd():
    """Endpoint starting an autonomous plot by uploading full G-code to SD and triggering it."""
    global plot_active, plot_paused, printer_state
    data = request.json
    paths, msg = process_paths_request(data)
    if not paths: return jsonify({"status": "error", "message": msg}), 400

    speed = int(data['speed'])
    origin_z = data.get('bbox', {}).get('origin_z')
    z_hop = float(data.get('z_hop', 4.0))
    bed_size = float(data.get('bed_size', 180.0))

    try:
        gcode_str = generate_full_gcode(paths, origin_z, speed, z_hop, bed_size)
        plot_active = True
        plot_paused = False
        printer_state["progress"] = 0
        printer_state["status"] = "Uploading"

        threading.Thread(target=execute_plot_sd_wrapper, args=(gcode_str,)).start()
        return jsonify({"status": "success"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


def execute_plot_wrapper(paths, base_z, speed, z_hop, bed_size):
    """Execution wrapper to isolate and catch errors occurring inside the background plotting thread."""
    global plot_active, printer_state
    try:
        execute_plot(paths, base_z, speed, z_hop, bed_size)
    except Exception as e:
        print(f"\n[FATAL ERROR] The plotting thread crashed entirely: {e}")
        traceback.print_exc()
        plot_active = False
        printer_state["progress"] = 0
        printer_state["status"] = "Idle"


@app.route('/api/plot', methods=['POST'])
def plot_paths():
    """Endpoint starting the streaming plot. Kicks off the background execution thread."""
    global plot_active, plot_paused, acked_sequences, printer_state

    data = request.json
    paths, msg = process_paths_request(data)
    if not paths: return jsonify({"status": "error", "message": msg}), 400

    speed = int(data['speed'])
    origin_z = data.get('bbox', {}).get('origin_z')
    z_hop = float(data.get('z_hop', 4.0))
    bed_size = float(data.get('bed_size', 180.0))

    plot_active = True
    plot_paused = False
    acked_sequences.clear()
    printer_state["progress"] = 0
    printer_state["status"] = "Streaming"

    threading.Thread(target=execute_plot_wrapper, args=(paths, origin_z, speed, z_hop, bed_size)).start()
    return jsonify({"status": "success"})


def execute_plot(paths, base_z, speed, z_hop, bed_size):
    """
    Core function that calculates actual hardware kinematics, packs G-code into
    payloads strictly formatted by network string length, and streams them while
    maintaining exact timings so the hardware doesn't overrun.
    """
    global plot_active, printer_state
    hop_z = min(base_z + z_hop, bed_size)
    mid = float(bed_size) / 2.0

    SAFE_Z_FEEDRATE = 400
    SAFE_XY_FEEDRATE = 18000

    z_time_hop = (abs(hop_z - base_z) / (SAFE_Z_FEEDRATE / 60.0)) + 0.05

    timed_commands = [
        {"cmd": "M17", "time": 0.1},
        {"cmd": "G90", "time": 0.1},
        {"cmd": f"G1 Z90 F{SAFE_Z_FEEDRATE}", "time": 1.5},
        {"cmd": f"G0 X{mid:.1f} Y{mid:.1f} F{SAFE_XY_FEEDRATE}", "time": 1.5},
        {"cmd": "M400", "time": 0.1},
        {"cmd": "G4 P500", "time": 0.5}
    ]

    current_pos = {"x": mid, "y": mid}

    def is_close(pA, pB):
        return abs(pA['x'] - pB['x']) < 0.03 and abs(pA['y'] - pB['y']) < 0.03

    for segment in paths:
        p1, p2 = segment[0], segment[1]

        if not is_close(current_pos, p1):
            dist = math.hypot(p1['x'] - current_pos['x'], p1['y'] - current_pos['y'])

            timed_commands.append({"cmd": "M400", "time": 0.1})
            timed_commands.append({"cmd": f"G1 Z{hop_z:.2f} F{SAFE_Z_FEEDRATE}", "time": z_time_hop})
            timed_commands.append({"cmd": "M400", "time": 0.1})
            timed_commands.append({"cmd": "G4 P200", "time": 0.2})

            travel_time = (dist / (SAFE_XY_FEEDRATE / 60.0)) + 0.05
            timed_commands.append({"cmd": f"G0 X{p1['x']:.2f} Y{p1['y']:.2f} F{SAFE_XY_FEEDRATE}", "time": travel_time})

            timed_commands.append({"cmd": "M400", "time": 0.1})
            timed_commands.append({"cmd": f"G1 Z{base_z:.2f} F{SAFE_Z_FEEDRATE}", "time": z_time_hop})
            timed_commands.append({"cmd": "M400", "time": 0.1})
            timed_commands.append({"cmd": "G4 P300", "time": 0.3})
        else:
            if abs(current_pos['x'] - p1['x']) > 0.005 or abs(current_pos['y'] - p1['y']) > 0.005:
                dist = math.hypot(p1['x'] - current_pos['x'], p1['y'] - current_pos['y'])
                t = (dist / (speed / 60.0)) + 0.05
                timed_commands.append({"cmd": f"G1 X{p1['x']:.2f} Y{p1['y']:.2f} F{speed}", "time": t})

        dist = math.hypot(p2['x'] - p1['x'], p2['y'] - p1['y'])
        t = (dist / (speed / 60.0)) + 0.05
        timed_commands.append({"cmd": f"G1 X{p2['x']:.2f} Y{p2['y']:.2f} F{speed}", "time": t})

        current_pos = p2

    timed_commands.extend([
        {"cmd": "M400", "time": 0.1},
        {"cmd": f"G1 Z{hop_z:.2f} F{SAFE_Z_FEEDRATE}", "time": z_time_hop},
        {"cmd": "M400", "time": 0.1},
        {"cmd": "G4 P250", "time": 0.25},
        {"cmd": f"G1 Z90 F{SAFE_Z_FEEDRATE}", "time": 1.5},
        {"cmd": f"G0 X{mid:.1f} Y{mid:.1f} F{SAFE_XY_FEEDRATE}", "time": 1.5},
        {"cmd": "M400", "time": 0.1},
        {"cmd": "M400 S1", "time": 1.0}
    ])

    chunks = []
    current_chunk_cmds = []
    current_chunk_time = 0.0

    for c in timed_commands:
        current_chunk_cmds.append(c["cmd"])
        current_chunk_time += c["time"]

        if len("\n".join(current_chunk_cmds)) > 800:
            chunks.append({"str": "\n".join(current_chunk_cmds), "time": current_chunk_time})
            current_chunk_cmds = []
            current_chunk_time = 0.0

    if current_chunk_cmds:
        chunks.append({"str": "\n".join(current_chunk_cmds), "time": current_chunk_time})

    virtual_buffer_time = 0.0
    MAX_BUFFER_TIME = 2.0

    for i, chunk in enumerate(chunks):
        if not plot_active:
            break

        for cmd_str in reversed(chunk["str"].split('\n')):
            if "X" in cmd_str and "Y" in cmd_str:
                parts = cmd_str.split()
                x_val, y_val = None, None
                for p in parts:
                    if p.startswith("X"):
                        x_val = float(p[1:])
                    elif p.startswith("Y"):
                        y_val = float(p[1:])
                if x_val is not None and y_val is not None:
                    printer_state["position"].update({"x": x_val, "y": y_val})
                    break

        printer_state["progress"] = int((i / max(1, len(chunks))) * 100)

        ack_time = send_gcode_chunk_reliable(chunk["str"])

        virtual_buffer_time += chunk["time"]
        virtual_buffer_time -= ack_time

        if virtual_buffer_time > MAX_BUFFER_TIME:
            sleep_for = virtual_buffer_time - (MAX_BUFFER_TIME * 0.5)
            if sleep_for > 0:
                time.sleep(sleep_for)
                virtual_buffer_time -= sleep_for

        if virtual_buffer_time < 0:
            virtual_buffer_time = 0.0

    plot_active = False
    printer_state["position"].update({"x": mid, "y": mid, "z": 90})
    printer_state["progress"] = 100
    printer_state["status"] = "Idle"


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5050, debug=False, threaded=True)