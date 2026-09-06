import paho.mqtt.client as mqtt
import socket
import ssl
import json
import io
import time
import ftplib
import zipfile
import hashlib
import traceback
import math

import backend.state as state
from backend.config import PRINTER_IP, ACCESS_CODE, SERIAL_NUMBER

MQTT_PORT = 8883
MQTT_USER = "bblp"
TOPIC_PUBLISH = f"device/{SERIAL_NUMBER}/request"
TOPIC_REPORT = f"device/{SERIAL_NUMBER}/report"

client = None


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


def on_connect(c, userdata, flags, rc, properties=None):
    c.subscribe(TOPIC_REPORT)


def on_message(c, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode('utf-8'))
        if "print" in payload:
            p_data = payload["print"]
            if "sequence_id" in p_data:
                state.acked_sequences.add(str(p_data["sequence_id"]))

            if state.printer_state.get("status") in ["Printing SD", "Starting Print"]:
                if "mc_percent" in p_data:
                    with state.state_lock:
                        state.printer_state["progress"] = p_data["mc_percent"]
                        if p_data["mc_percent"] == 100:
                            state.printer_state["status"] = "Idle"
                            state.plot_active = False
                if "gcode_state" in p_data:
                    c_state = p_data["gcode_state"]
                    with state.state_lock:
                        if c_state == "FINISH" and state.printer_state.get("progress", 0) > 0:
                            state.plot_active = False
                            state.printer_state["progress"] = 100
                            state.printer_state["status"] = "Idle"
                        elif c_state == "FAILED":
                            state.plot_active = False
                            state.printer_state["progress"] = 100
                            state.printer_state["status"] = "Failed"
    except Exception:
        pass


def setup_mqtt():
    global client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, ACCESS_CODE)
    client.tls_set(cert_reqs=ssl.CERT_NONE, tls_version=ssl.PROTOCOL_TLS)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_message = on_message
    print(f"\nAttempting to connect to printer at {PRINTER_IP}...")
    try:
        client.connect(PRINTER_IP, MQTT_PORT, 5)
        client.loop_start()
        print("Successfully connected to the printer via MQTT!\n")
    except Exception as e:
        print(f"\n[WARNING] Could not connect to printer: {e}")


def send_printer_command(cmd, param=""):
    state.sequence_id_counter += 1
    payload = {"print": {"sequence_id": str(state.sequence_id_counter), "command": cmd}}
    if param: payload["print"]["param"] = param
    client.publish(TOPIC_PUBLISH, json.dumps(payload, separators=(',', ':')))


def send_gcode_chunk(gcode_string):
    formatted = "".join(f"{line.strip()} \n" for line in gcode_string.strip().split('\n') if line.strip())
    send_printer_command("gcode_line", formatted)


def send_gcode_chunk_reliable(gcode_string):
    formatted = "".join(f"{line.strip()} \n" for line in gcode_string.strip().split('\n') if line.strip())
    state.sequence_id_counter += 1
    seq_id = str(state.sequence_id_counter)
    payload_str = json.dumps({"print": {"command": "gcode_line", "param": formatted, "sequence_id": seq_id}},
                             separators=(',', ':'))

    while state.plot_active:
        while state.plot_paused and state.plot_active:
            time.sleep(0.1)
        if not state.plot_active: return 0
        send_start = time.time()
        client.publish(TOPIC_PUBLISH, payload_str, qos=0)
        acked = False
        while time.time() - send_start < 8.0:
            if not state.plot_active: return 0
            if seq_id in state.acked_sequences:
                acked = True
                state.acked_sequences.discard(seq_id)
                break
            time.sleep(0.01)
        if acked: return time.time() - send_start
        print(f"[RETRY] Chunk {seq_id} dropped by printer queue. Resending...")
        time.sleep(0.5)
    return 0


def upload_to_printer(file_bytes, filename="bambuscribe_plot.gcode.3mf"):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ftp = ImplicitFTP_TLS(context=ctx, timeout=30)
    ftp.connect(host=PRINTER_IP, port=990)
    ftp.login(user="bblp", passwd=ACCESS_CODE)
    ftp.prot_p()
    bio = io.BytesIO(file_bytes)
    total_size, uploaded_size = len(file_bytes), [0]

    def upload_callback(data):
        if not state.plot_active: raise Exception("Upload aborted by user.")
        uploaded_size[0] += len(data)
        with state.state_lock:
            state.printer_state["progress"] = min(99, int((uploaded_size[0] / total_size) * 100))

    try:
        ftp.storbinary(f"STOR /{filename}", bio, blocksize=8192, callback=upload_callback)
    except Exception as e:
        if uploaded_size[0] >= total_size:
            print("[SD Plotting] Ignored FTP disconnect at end of transfer.")
        else:
            raise e
    finally:
        try:
            ftp.quit()
        except:
            pass


def execute_plot_sd_wrapper(gcode_data):
    try:
        # Automatically handle bytes or string depending on app.py state
        if isinstance(gcode_data, bytes):
            gcode_str = gcode_data.decode('utf-8')
        else:
            gcode_str = gcode_data

        gcode_str = gcode_str.replace('\r\n', '\n') + "\n"
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr("Metadata/plate_1.gcode", gcode_str)
            zip_file.writestr("[Content_Types].xml",
                              '<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n  <Default Extension="gcode" ContentType="application/vnd.bambulab.gcode"/>\n  <Default Extension="config" ContentType="application/xml"/>\n</Types>')
            zip_file.writestr("Metadata/slice_info.config",
                              '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <plate>\n    <metadata key="index" value="1"/>\n    <metadata key="gcode_file" value="Metadata/plate_1.gcode"/>\n    <metadata key="prediction_estimation_time" value="60"/>\n  </plate>\n</config>')

        archive_bytes = zip_buffer.getvalue()
        md5_hash = hashlib.md5(archive_bytes).hexdigest()
        filename = "bambuscribe_plot.gcode.3mf"

        upload_to_printer(archive_bytes, filename)
        if not state.plot_active: return

        with state.state_lock:
            state.printer_state["status"] = "Starting Print"
            state.printer_state["progress"] = 99
        time.sleep(3.0)

        with state.state_lock:
            if not state.plot_active: return

        state.sequence_id_counter += 1
        seq_id = str(state.sequence_id_counter)

        payload = {
            "print": {
                "sequence_id": seq_id, "command": "project_file", "param": "Metadata/plate_1.gcode",
                "project_id": "0", "profile_id": "0", "task_id": "0", "subtask_id": "0",
                "subtask_name": "BambuScribe Plot",
                "file": f"/{filename}", "url": f"ftp://{PRINTER_IP}/{filename}", "md5": md5_hash, "use_ams": False,
                "bed_leveling": False, "vibration_cali": False, "layer_inspect": False, "flow_cali": False,
                "timelapse": False
            }
        }
        client.publish(TOPIC_PUBLISH, json.dumps(payload, separators=(',', ':')))
        print("[SD Plotting] Print command sent. Waiting for acknowledgment...")

        wait_start = time.time()
        acked = False
        while time.time() - wait_start < 10.0:
            if not state.plot_active: return
            if seq_id in state.acked_sequences:
                acked = True
                state.acked_sequences.discard(seq_id)
                break
            time.sleep(0.1)

        if not acked:
            print("[WARNING] No acknowledgment from printer for SD print. It may have started anyway.")

        with state.state_lock:
            state.printer_state["status"] = "Printing SD"
            state.printer_state["progress"] = 0
            state.save_state()

    except Exception as e:
        traceback.print_exc()
        with state.state_lock:
            state.printer_state["status"] = "Failed" if state.plot_active else "Idle"
            state.plot_active = False
            state.printer_state["progress"] = 0
            state.save_state()


def execute_plot(paths, base_z, speed, z_hop, bed_size):
    speed = int(speed)
    hop_z = min(base_z + z_hop, bed_size)
    mid = float(bed_size) / 2.0
    bed_max = float(bed_size)
    SAFE_Z_FEEDRATE, SAFE_XY_FEEDRATE = min(speed, 1200), min(speed, 18000)
    z_time_hop = (abs(hop_z - base_z) / (SAFE_Z_FEEDRATE / 60.0)) + 0.05

    timed_commands = [
        {"cmd": "M73 P0 R1", "time": 0.1},
        {"cmd": "M104 S0", "time": 0.1},
        {"cmd": "M140 S0", "time": 0.1},
        {"cmd": "M106 S0", "time": 0.1},
        {"cmd": "M17", "time": 0.1},
        {"cmd": "G90", "time": 0.1},
        {"cmd": "M83", "time": 0.1},
        {"cmd": f"G1 Z90 F{SAFE_Z_FEEDRATE}", "time": 1.5},
        {"cmd": f"G0 X{mid:.1f} Y{mid:.1f} F{SAFE_XY_FEEDRATE}", "time": 1.5},
        {"cmd": "M400", "time": 0.1}
    ]

    current_pos = {"x": mid, "y": mid}

    def is_close(pA, pB):
        return abs(pA['x'] - pB['x']) < 0.03 and abs(pA['y'] - pB['y']) < 0.03

    for segment in paths:
        p1, p2 = segment[0], segment[1]
        if not is_close(current_pos, p1):
            dist = math.hypot(p1['x'] - current_pos['x'], p1['y'] - current_pos['y'])
            timed_commands.extend([
                {"cmd": "M400", "time": 0.05},
                {"cmd": f"G1 Z{hop_z:.2f} F{SAFE_Z_FEEDRATE}", "time": z_time_hop},
                {"cmd": "M400", "time": 0.05},
                {"cmd": f"G0 X{p1['x']:.2f} Y{p1['y']:.2f} F{SAFE_XY_FEEDRATE}", "time": (dist / (SAFE_XY_FEEDRATE / 60.0)) + 0.05},
                {"cmd": "M400", "time": 0.05},
                {"cmd": f"G1 Z{base_z:.2f} F{SAFE_Z_FEEDRATE}", "time": z_time_hop},
                {"cmd": "M400", "time": 0.05}
            ])
        else:
            if abs(current_pos['x'] - p1['x']) > 0.005 or abs(current_pos['y'] - p1['y']) > 0.005:
                dist = math.hypot(p1['x'] - current_pos['x'], p1['y'] - current_pos['y'])
                timed_commands.append({"cmd": f"G1 X{p1['x']:.2f} Y{p1['y']:.2f} F{speed}", "time": (dist / (speed / 60.0)) + 0.05})

        dist = math.hypot(p2['x'] - p1['x'], p2['y'] - p1['y'])
        timed_commands.append({"cmd": f"G1 X{p2['x']:.2f} Y{p2['y']:.2f} F{speed}", "time": (dist / (speed / 60.0)) + 0.05})
        current_pos = p2

    timed_commands.extend([
        {"cmd": "M400", "time": 0.1},
        {"cmd": f"G1 Z{hop_z:.2f} F{SAFE_Z_FEEDRATE}", "time": z_time_hop},
        {"cmd": f"G1 Z50 F{SAFE_Z_FEEDRATE}", "time": 2.0},
        {"cmd": f"G0 X{mid:.1f} Y{bed_max - 10:.1f} F{SAFE_XY_FEEDRATE}", "time": 2.0},
        {"cmd": "M400 S1", "time": 1.0},
        {"cmd": "M73 P100 R0", "time": 0.1}
    ])

    chunks, current_chunk_cmds, current_chunk_time = [], [], 0.0
    for c in timed_commands:
        current_chunk_cmds.append(c["cmd"])
        current_chunk_time += c["time"]
        if len("\n".join(current_chunk_cmds)) > 800:
            chunks.append({"str": "\n".join(current_chunk_cmds), "time": current_chunk_time})
            current_chunk_cmds, current_chunk_time = [], 0.0
    if current_chunk_cmds: chunks.append({"str": "\n".join(current_chunk_cmds), "time": current_chunk_time})

    virtual_buffer_time, MAX_BUFFER_TIME = 0.0, 2.0
    for i, chunk in enumerate(chunks):
        if not state.plot_active: break
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
                    with state.state_lock: state.printer_state["position"].update({"x": x_val, "y": y_val})
                    break

        with state.state_lock:
            state.printer_state["progress"] = int((i / max(1, len(chunks))) * 100)

        ack_time = send_gcode_chunk_reliable(chunk["str"])
        virtual_buffer_time += chunk["time"] - ack_time
        if virtual_buffer_time > MAX_BUFFER_TIME:
            sleep_for = virtual_buffer_time - (MAX_BUFFER_TIME * 0.5)
            if sleep_for > 0:
                time.sleep(sleep_for)
                virtual_buffer_time -= sleep_for
        if virtual_buffer_time < 0: virtual_buffer_time = 0.0

    with state.state_lock:
        state.plot_active = False
        state.printer_state["position"].update({"x": mid, "y": mid, "z": 90})
        state.printer_state["progress"] = 100
        state.printer_state["status"] = "Idle"
        state.save_state()