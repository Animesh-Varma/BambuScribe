import math
import base64
import io
import cv2
import numpy as np
from PIL import Image, ImageEnhance
from HersheyFonts import HersheyFonts

def generate_text_paths(text, font_style, line_gap_mm, font_pct, min_x, max_x, min_y, max_y, auto_wrap):
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
    return final_paths, "Success"


def get_rotated_image_pil(base64_img, contrast, rotation):
    image_data = base64.b64decode(base64_img.split(',')[1])
    img = Image.open(io.BytesIO(image_data)).convert('L')
    if rotation != 0: img = img.rotate(-rotation, expand=True, fillcolor=255)
    return ImageEnhance.Contrast(img).enhance(contrast)


def prepare_image(img, box_w, box_h):
    ppm = 4
    img_ratio = img.width / max(1, img.height)
    box_ratio = box_w / max(0.1, box_h)
    if img_ratio > box_ratio:
        final_w = box_w
        final_h = box_w / img_ratio
    else:
        final_h = box_h
        final_w = box_h * img_ratio
    px_w, px_h = int(final_w * ppm), int(final_h * ppm)
    return img.resize((px_w, px_h)), px_w, px_h, ppm, final_w, final_h


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
    starts = [(x * gap_mm, 0) for x in range(int(final_w / gap_mm))] + [(0, y * gap_mm) for y in range(int(final_h / gap_mm))]
    trace(starts, step, step, 110)
    starts = [(x * gap_mm, final_h) for x in range(int(final_w / gap_mm))] + [(0, y * gap_mm) for y in range(int(final_h / gap_mm))]
    trace(starts, step, -step, 60)
    return paths


def gen_tsp(img, px_w, px_h, ppm, final_w, gap_mm, ox, oy):
    final_h = px_h / ppm
    num_dots = int(15000 / max(0.5, gap_mm))
    pts = []
    rng = np.random.default_rng(42)
    attempts = 0
    while len(pts) < num_dots and attempts < num_dots * 20:
        rx, ry = rng.uniform(0, final_w), rng.uniform(0, final_h)
        px, py = int(rx * ppm), int(ry * ppm)
        if px < px_w and py < px_h:
            prob = 1.0 - (img.getpixel((px, py)) / 255.0)
            if rng.random() < prob: pts.append((rx, ry))
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
    px_w, px_h = int(final_w * ppm), int(final_h * ppm)
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


def split_segment_by_bbox(p1, p2, xmin, xmax, ymin, ymax):
    dx, dy = p2['x'] - p1['x'], p2['y'] - p1['y']
    t_values = [0.0, 1.0]
    if dx != 0:
        tx1 = (xmin - p1['x']) / dx
        if 0 < tx1 < 1: t_values.append(tx1)
        tx2 = (xmax - p1['x']) / dx
        if 0 < tx2 < 1: t_values.append(tx2)
    if dy != 0:
        ty1 = (ymin - p1['y']) / dy
        if 0 < ty1 < 1: t_values.append(ty1)
        ty2 = (ymax - p1['y']) / dy
        if 0 < ty2 < 1: t_values.append(ty2)
    t_values.sort()
    in_segs, out_segs = [], []
    eps = 1e-6
    for i in range(len(t_values) - 1):
        tA, tB = t_values[i], t_values[i + 1]
        if tB - tA < 1e-7: continue
        t_mid = (tA + tB) / 2.0
        mid_x, mid_y = p1['x'] + t_mid * dx, p1['y'] + t_mid * dy
        seg = [{"x": p1['x'] + tA * dx, "y": p1['y'] + tA * dy}, {"x": p1['x'] + tB * dx, "y": p1['y'] + tB * dy}]
        if (xmin - eps <= mid_x <= xmax + eps) and (ymin - eps <= mid_y <= ymax + eps):
            in_segs.append(seg)
        else:
            out_segs.append(seg)
    return in_segs, out_segs


def process_paths_request(data):
    bbox = data.get('bbox')
    bed_size = float(data.get('bed_size', 180.0))
    if not bbox: return None, None, "Set Bounding Box (4 points) first."
    min_x, max_x = float(bbox['min_x']), float(bbox['max_x'])
    min_y, max_y = float(bbox['min_y']), float(bbox['max_y'])
    box_w, box_h = max_x - min_x, max_y - min_y
    if box_w <= 0 or box_h <= 0: return None, None, "Invalid Bounding Box Area"

    paths = []
    if data['type'] == 'text':
        paths, msg = generate_text_paths(
            data['text'], data['font'], data['line_spacing'],
            data['font_size'], min_x, max_x, min_y, max_y, data.get('auto_wrap', True)
        )
        if not paths: return None, None, msg
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
                cx, cy = min_x + box_w / 2.0, min_y + box_h / 2.0
                for seg in raw_paths:
                    p1x = cx + (seg[0]['x'] - cx) * img_scale + offset_x
                    p1y = cy + (seg[0]['y'] - cy) * img_scale - offset_y
                    p2x = cx + (seg[1]['x'] - cx) * img_scale + offset_x
                    p2y = cy + (seg[1]['y'] - cy) * img_scale - offset_y
                    paths.append([{"x": p1x, "y": p1y}, {"x": p2x, "y": p2y}])
            else:
                img, px_w, px_h, ppm, original_final_w, original_final_h = prepare_image(img_pil, box_w, box_h)
                scaled_w = original_final_w * img_scale
                scaled_h = original_final_h * img_scale
                ox = min_x + (box_w - scaled_w) / 2.0 + offset_x
                oy = max_y - (box_h - scaled_h) / 2.0 - offset_y
                effective_ppm = ppm / max(0.001, img_scale)
                if method == 'tsp':
                    paths = gen_tsp(img, px_w, px_h, effective_ppm, scaled_w, float(data['img_gap']), ox, oy)
                else:
                    paths = gen_hatch(img, px_w, px_h, effective_ppm, scaled_w, float(data['img_gap']), ox, oy)
        except Exception as e:
            return None, None, str(e)

    in_paths, out_paths = [], []
    for seg in paths:
        i_segs, o_segs = split_segment_by_bbox(seg[0], seg[1], min_x, max_x, min_y, max_y)
        in_paths.extend(i_segs)
        out_paths.extend(o_segs)

    if data.get('draw_bbox'):
        in_paths.extend([
            [{"x": min_x, "y": min_y}, {"x": max_x, "y": min_y}],
            [{"x": max_x, "y": min_y}, {"x": max_x, "y": max_y}],
            [{"x": max_x, "y": max_y}, {"x": min_x, "y": max_y}],
            [{"x": min_x, "y": max_y}, {"x": min_x, "y": min_y}]
        ])

    safe_paths = []
    for seg in in_paths:
        x1, y1 = max(0.0, min(bed_size, seg[0]['x'])), max(0.0, min(bed_size, seg[0]['y']))
        x2, y2 = max(0.0, min(bed_size, seg[1]['x'])), max(0.0, min(bed_size, seg[1]['y']))
        if abs(x1 - x2) < 0.001 and abs(y1 - y2) < 0.001: continue
        safe_paths.append([{"x": x1, "y": y1}, {"x": x2, "y": y2}])

    return safe_paths, out_paths, "Success"

def generate_full_gcode(paths, base_z, speed, z_hop, bed_size, is_download=False):
    hop_z = min(base_z + z_hop, float(bed_size))
    mid = float(bed_size) / 2.0
    bed_max = float(bed_size)
    speed = int(speed)
    SAFE_Z_FEEDRATE = int(min(speed, 1200))
    SAFE_XY_FEEDRATE = int(min(speed, 18000))

    if is_download:
        # Standalone SD file: Alert -> Home -> Energized Pause -> Plot
        gcode = [
            "; --- BAMBUSCRIBE AUTONOMOUS PLOTTER GCODE ---",
            "M73 P0 R1 ; Set progress to 0",
            "M104 S0 ; turn off nozzle heater",
            "M140 S0 ; turn off bed heater",
            "M106 S0 ; turn off fan",
            "M17 ; Enable steppers",
            "M84 S0 ; Disable idle timeout (keep motors locked)",
            "G90 ; Absolute positioning",
            "M83 ; Relative extrusion",
            "",
            "; --- STEP 1: SIGNAL BEFORE HOMING ---",
            "M400",
            "M400 U1 ; PAUSE 1: REMOVE PEN/ATTACHMENTS! Press Resume to home",
            "",
            "; --- STEP 2: HOMING ---",
            "G28 ; Home all axes",
            f"G1 Z50 F{SAFE_Z_FEEDRATE} ; Raise Z for attachment at side parking position",
            "M400",
            "",
            "; --- STEP 3: ENERGIZED PAUSE FOR PEN & PAPER ---",
            "M17 ; Re-verify motors are locked",
            "M400 U1 ; PAUSE 2: ATTACH PEN & PAPER! (Motors locked). Press Resume to plot",
            ""
        ]
        current_pos = {"x": None, "y": None}
    else:
        # Direct send: already homed & calibrated in app session, start immediately
        gcode = [
            "; --- BAMBUSCRIBE PLOTTER GCODE ---",
            "M73 P0 R1 ; Set progress to 0",
            "M104 S0 ; turn off nozzle heater",
            "M140 S0 ; turn off bed heater",
            "M106 S0 ; turn off fan",
            "M17 ; Enable steppers",
            "G90 ; Absolute positioning",
            "M83 ; Relative extrusion",
            "",
            "; --- SETUP & CALIBRATION (DIRECT SEND) ---",
            f"G1 Z90 F{SAFE_Z_FEEDRATE} ; Raise Z safely before moving",
            f"G0 X{mid:.1f} Y{mid:.1f} F{SAFE_XY_FEEDRATE} ; Move head to center",
            "M400",
            ""
        ]
        current_pos = {"x": mid, "y": mid}

    def is_close(pA, pB):
        return abs(pA['x'] - pB['x']) < 0.03 and abs(pA['y'] - pB['y']) < 0.03

    for i, segment in enumerate(paths):
        p1, p2 = segment[0], segment[1]
        if i == 0:
            gcode.extend([
                f"G0 X{p1['x']:.2f} Y{p1['y']:.2f} F{SAFE_XY_FEEDRATE}",
                "M400",
                f"G1 Z{base_z:.2f} F{SAFE_Z_FEEDRATE}",
                "M400"
            ])
        elif not is_close(current_pos, p1):
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
        f"G1 Z50 F{SAFE_Z_FEEDRATE} ; Lift pen high",
        f"G0 X{mid:.1f} Y{bed_max - 10:.1f} F{SAFE_XY_FEEDRATE} ; Move bed forward for removal",
        "M400 S1",
        "M73 P100 R0"
    ])
    return "\n".join(gcode)