import math
import base64
import io
import cv2
import numpy as np
from PIL import Image, ImageEnhance
from HersheyFonts import HersheyFonts

# =============================================================================
# 1. MORPHOLOGICAL THINNING & SKELETON GRAPH TRACING
# =============================================================================

def zhang_suen_thinning(binary_inv):
    """
    Vectorized Zhang-Suen morphological thinning algorithm.
    Falls back to pure NumPy if cv2.ximgproc is unavailable.
    Input: binary_inv (uint8 ndarray with 0=background, 1=foreground).
    """
    if hasattr(cv2, 'ximgproc') and hasattr(cv2.ximgproc, 'thinning'):
        try:
            thinned = cv2.ximgproc.thinning((binary_inv * 255).astype(np.uint8), cv2.ximgproc.THINNING_ZHANGSUEN)
            return (thinned > 0).astype(np.uint8)
        except Exception:
            pass

    skeleton = binary_inv.copy()
    while True:
        padded = np.pad(skeleton, 1, mode='constant', constant_values=0)
        p2 = padded[0:-2, 1:-1]
        p3 = padded[0:-2, 2:]
        p4 = padded[1:-1, 2:]
        p5 = padded[2:, 2:]
        p6 = padded[2:, 1:-1]
        p7 = padded[2:, 0:-2]
        p8 = padded[1:-1, 0:-2]
        p9 = padded[0:-2, 0:-2]
        curr = padded[1:-1, 1:-1]

        b_val = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
        a_val = (
            ((p2 == 0) & (p3 == 1)).astype(int) +
            ((p3 == 0) & (p4 == 1)).astype(int) +
            ((p4 == 0) & (p5 == 1)).astype(int) +
            ((p5 == 0) & (p6 == 1)).astype(int) +
            ((p6 == 0) & (p7 == 1)).astype(int) +
            ((p7 == 0) & (p8 == 1)).astype(int) +
            ((p8 == 0) & (p9 == 1)).astype(int) +
            ((p9 == 0) & (p2 == 1)).astype(int)
        )

        del1 = (curr == 1) & (b_val >= 2) & (b_val <= 6) & (a_val == 1) & (p2 * p4 * p6 == 0) & (p4 * p6 * p8 == 0)
        skeleton[del1] = 0

        padded = np.pad(skeleton, 1, mode='constant', constant_values=0)
        p2 = padded[0:-2, 1:-1]
        p3 = padded[0:-2, 2:]
        p4 = padded[1:-1, 2:]
        p5 = padded[2:, 2:]
        p6 = padded[2:, 1:-1]
        p7 = padded[2:, 0:-2]
        p8 = padded[1:-1, 0:-2]
        p9 = padded[0:-2, 0:-2]
        curr = padded[1:-1, 1:-1]

        b_val = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
        a_val = (
            ((p2 == 0) & (p3 == 1)).astype(int) +
            ((p3 == 0) & (p4 == 1)).astype(int) +
            ((p4 == 0) & (p5 == 1)).astype(int) +
            ((p5 == 0) & (p6 == 1)).astype(int) +
            ((p6 == 0) & (p7 == 1)).astype(int) +
            ((p7 == 0) & (p8 == 1)).astype(int) +
            ((p8 == 0) & (p9 == 1)).astype(int) +
            ((p9 == 0) & (p2 == 1)).astype(int)
        )

        del2 = (curr == 1) & (b_val >= 2) & (b_val <= 6) & (a_val == 1) & (p2 * p4 * p8 == 0) & (p2 * p6 * p8 == 0)
        skeleton[del2] = 0

        if not (np.any(del1) or np.any(del2)):
            break
    return skeleton


def trace_skeleton(skeleton, ox, oy, ppm):
    """
    Transforms a 1-pixel wide skeleton bitmap into ordered polylines.
    """
    pts = set(zip(*np.where(skeleton == 1)))
    if not pts:
        return []

    neighbor_offsets = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    adj = {}
    for y, x in pts:
        nbrs = []
        for dy, dx in neighbor_offsets:
            ny, nx = y + dy, x + dx
            if (ny, nx) in pts:
                nbrs.append((ny, nx))
        adj[(y, x)] = nbrs

    visited_edges = set()
    polylines = []

    def to_coord(pt):
        return {"x": ox + (pt[1] / ppm), "y": oy - (pt[0] / ppm)}

    key_nodes = [pt for pt, nbrs in adj.items() if len(nbrs) != 2]
    if not key_nodes and adj:
        key_nodes = [next(iter(adj.keys()))]

    for start_node in key_nodes:
        for neighbor in adj[start_node]:
            edge = tuple(sorted([start_node, neighbor]))
            if edge in visited_edges:
                continue
            visited_edges.add(edge)
            chain = [to_coord(start_node), to_coord(neighbor)]
            curr, prev = neighbor, start_node
            while True:
                next_nodes = [n for n in adj[curr] if n != prev]
                if len(next_nodes) == 1:
                    nxt = next_nodes[0]
                    n_edge = tuple(sorted([curr, nxt]))
                    if n_edge in visited_edges:
                        break
                    visited_edges.add(n_edge)
                    chain.append(to_coord(nxt))
                    prev, curr = curr, nxt
                else:
                    break
            if len(chain) >= 2:
                polylines.append(chain)

    for pt in pts:
        for neighbor in adj[pt]:
            edge = tuple(sorted([pt, neighbor]))
            if edge not in visited_edges:
                visited_edges.add(edge)
                chain = [to_coord(pt), to_coord(neighbor)]
                curr, prev = neighbor, pt
                while True:
                    next_nodes = [n for n in adj[curr] if n != prev]
                    if len(next_nodes) == 1:
                        nxt = next_nodes[0]
                        n_edge = tuple(sorted([curr, nxt]))
                        if n_edge in visited_edges:
                            chain.append(to_coord(nxt))
                            break
                        visited_edges.add(n_edge)
                        chain.append(to_coord(nxt))
                        prev, curr = curr, nxt
                    else:
                        break
                if len(chain) >= 2:
                    polylines.append(chain)

    return polylines


# =============================================================================
# 2. IMAGE PLOTTING GENERATORS
# =============================================================================

def gen_skeleton(img_pil, px_w, px_h, ppm, ox, oy):
    img_np = np.array(img_pil.resize((px_w, px_h)))
    _, binary = cv2.threshold(img_np, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    thinned = zhang_suen_thinning((binary > 0).astype(np.uint8))
    return trace_skeleton(thinned, ox, oy, ppm)


def gen_spiral(img, px_w, px_h, ppm, final_w, final_h, pitch_mm, ox, oy):
    cx, cy = final_w / 2.0, final_h / 2.0
    max_r = math.hypot(cx, cy)
    pitch = max(0.4, float(pitch_mm))
    max_theta = (max_r / pitch) * 2.0 * math.pi

    step_len = 0.2
    theta = 0.5
    wavelength = max(0.6, pitch * 0.8)
    phase = 0.0

    def sample_tone(x_mm, y_mm):
        px, py = int(x_mm * ppm), int(y_mm * ppm)
        if 0 <= px < px_w and 0 <= py < px_h:
            return 1.0 - (img.getpixel((px, py)) / 255.0)
        return 0.0

    polylines = []
    curr_poly = []

    while theta < max_theta:
        base_r = (pitch / (2.0 * math.pi)) * theta
        if base_r > max_r:
            break

        bx = cx + base_r * math.cos(theta)
        by = cy + base_r * math.sin(theta)
        tone = sample_tone(bx, by)

        amp = (pitch * 0.45) * tone
        wave = amp * math.sin(phase)
        mod_r = base_r + wave

        rx = cx + mod_r * math.cos(theta)
        ry = cy + mod_r * math.sin(theta)

        if 0 <= rx <= final_w and 0 <= ry <= final_h:
            curr_poly.append({"x": ox + rx, "y": oy - ry})
        else:
            if len(curr_poly) >= 2:
                polylines.append(curr_poly)
            curr_poly = []

        d_theta = step_len / max(0.5, base_r)
        theta += d_theta
        phase += (2.0 * math.pi / wavelength) * step_len

    if len(curr_poly) >= 2:
        polylines.append(curr_poly)
    return polylines


def gen_squiggle(img, px_w, px_h, ppm, final_w, final_h, gap_mm, ox, oy):
    gap = max(0.4, float(gap_mm))
    num_rows = int(final_h / gap)
    step_x = 0.25
    wavelength = gap * 0.9

    def sample_tone(x_mm, y_mm):
        px, py = int(x_mm * ppm), int(y_mm * ppm)
        if 0 <= px < px_w and 0 <= py < px_h:
            return 1.0 - (img.getpixel((px, py)) / 255.0)
        return 0.0

    poly = []
    phase = 0.0

    for r in range(num_rows):
        base_y = (r + 0.5) * gap
        xs = np.arange(0, final_w, step_x) if (r % 2 == 0) else np.arange(final_w, 0, -step_x)
        for x in xs:
            tone = sample_tone(x, base_y)
            amp = (gap * 0.48) * tone
            mod_y = base_y + amp * math.sin(phase)
            poly.append({"x": ox + x, "y": oy - mod_y})
            phase += (2.0 * math.pi / wavelength) * step_x

    return [poly] if len(poly) >= 2 else []


def gen_flow_field(img_pil, px_w, px_h, ppm, final_w, final_h, gap_mm, ox, oy):
    img_np = np.array(img_pil.resize((px_w, px_h)), dtype=np.float32)
    blurred = cv2.GaussianBlur(img_np, (3, 3), 0)
    gx = cv2.Sobel(blurred, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blurred, cv2.CV_32F, 0, 1, ksize=3)

    j_xx = cv2.GaussianBlur(gx * gx, (7, 7), 0)
    j_yy = cv2.GaussianBlur(gy * gy, (7, 7), 0)
    j_xy = cv2.GaussianBlur(gx * gy, (7, 7), 0)

    theta = 0.5 * np.arctan2(2.0 * j_xy, j_xx - j_yy) + (np.pi / 2.0)
    vx = np.cos(theta)
    vy = np.sin(theta)
    tone_map = 1.0 - (img_np / 255.0)

    step_mm = 0.35
    step_px = step_mm * ppm
    min_dist_mm = max(0.4, float(gap_mm))
    grid_cell = min_dist_mm * ppm

    grid_w = int(px_w / grid_cell) + 1
    grid_h = int(px_h / grid_cell) + 1
    occupied = np.zeros((grid_h, grid_w), dtype=bool)

    def is_occupied(px, py):
        gx_idx = int(px / grid_cell)
        gy_idx = int(py / grid_cell)
        if 0 <= gx_idx < grid_w and 0 <= gy_idx < grid_h:
            return occupied[gy_idx, gx_idx]
        return True

    def mark_occupied(px, py):
        gx_idx = int(px / grid_cell)
        gy_idx = int(py / grid_cell)
        if 0 <= gx_idx < grid_w and 0 <= gy_idx < grid_h:
            occupied[gy_idx, gx_idx] = True

    seeds = []
    seed_spacing = min_dist_mm * 0.75
    for sy in np.arange(0, final_h, seed_spacing):
        for sx in np.arange(0, final_w, seed_spacing):
            spx, spy = int(sx * ppm), int(sy * ppm)
            if 0 <= spx < px_w and 0 <= spy < px_h:
                t = tone_map[spy, spx]
                if t > 0.07:
                    seeds.append((t, sx, sy))

    seeds.sort(key=lambda s: s[0], reverse=True)
    polylines = []

    for tone_val, sx_mm, sy_mm in seeds:
        spx = sx_mm * ppm
        spy = sy_mm * ppm
        if is_occupied(spx, spy):
            continue

        max_steps = int(25 + 90 * tone_val)
        streamline = []

        for direction in [1.0, -1.0]:
            curr_x, curr_y = spx, spy
            last_dx, last_dy = 0.0, 0.0
            pts_dir = []

            for _ in range(max_steps):
                ix, iy = int(round(curr_x)), int(round(curr_y))
                if not (0 <= ix < px_w and 0 <= iy < px_h):
                    break

                nx = vx[iy, ix] * direction
                ny = vy[iy, ix] * direction
                if last_dx != 0.0 or last_dy != 0.0:
                    if nx * last_dx + ny * last_dy < 0:
                        nx, ny = -nx, -ny
                last_dx, last_dy = nx, ny

                curr_x += nx * step_px
                curr_y += ny * step_px

                if not (0 <= curr_x < px_w and 0 <= curr_y < px_h):
                    break
                if is_occupied(curr_x, curr_y):
                    break

                mark_occupied(curr_x, curr_y)
                pts_dir.append({"x": ox + (curr_x / ppm), "y": oy - (curr_y / ppm)})

            if direction == -1.0:
                streamline = list(reversed(pts_dir)) + streamline
            else:
                streamline = streamline + [{"x": ox + (spx / ppm), "y": oy - (spy / ppm)}] + pts_dir

        if len(streamline) >= 3:
            polylines.append(streamline)

    return polylines


def gen_stipple(img, px_w, px_h, ppm, final_w, final_h, gap_mm, ox, oy, tsp_connect=True):
    """
    Blue-Noise Stippling & High-Speed Nearest-Neighbor TSP Tour.
    - If tsp_connect=True: forms a continuous single-line path connecting dots.
    - If tsp_connect=False: outputs individual micro-dot marks for genuine stippling.
    """
    density_factor = max(0.3, float(gap_mm))
    target_dots = int(2600 / density_factor)
    pts = []
    rng = np.random.default_rng(42)

    img_arr = np.array(img, dtype=np.float32)
    tone_map = 1.0 - (img_arr / 255.0)

    attempts = 0
    max_attempts = target_dots * 20
    while len(pts) < target_dots and attempts < max_attempts:
        rx = rng.uniform(0, final_w)
        ry = rng.uniform(0, final_h)
        px, py = int(rx * ppm), int(ry * ppm)
        if 0 <= px < px_w and 0 <= py < px_h:
            prob = tone_map[py, px] ** 1.35
            if rng.random() < prob:
                pts.append([rx, ry])
        attempts += 1

    if not pts:
        return []

    pts = np.array(pts, dtype=np.float32)

    repulse_rad = math.sqrt((final_w * final_h) / max(1, len(pts))) * 0.72
    cell_size = max(0.5, repulse_rad * 1.5)

    for _ in range(2):
        grid = {}
        for idx, p in enumerate(pts):
            key = (int(p[0] / cell_size), int(p[1] / cell_size))
            grid.setdefault(key, []).append(idx)

        deltas = np.zeros_like(pts)
        for (gx, gy), indices in grid.items():
            candidates = []
            for dgx in (-1, 0, 1):
                for dgy in (-1, 0, 1):
                    candidates.extend(grid.get((gx + dgx, gy + dgy), []))
            for i in indices:
                pi = pts[i]
                for j in candidates:
                    if i >= j:
                        continue
                    pj = pts[j]
                    dx = pi[0] - pj[0]
                    dy = pi[1] - pj[1]
                    dist = math.hypot(dx, dy)
                    if 0.001 < dist < repulse_rad:
                        force = (repulse_rad - dist) / dist * 0.12
                        deltas[i, 0] += dx * force
                        deltas[i, 1] += dy * force
                        deltas[j, 0] -= dx * force
                        deltas[j, 1] -= dy * force

        pts += deltas
        pts[:, 0] = np.clip(pts[:, 0], 0, final_w)
        pts[:, 1] = np.clip(pts[:, 1], 0, final_h)

    if not tsp_connect:
        dot_paths = []
        for p in pts:
            x_val = ox + float(p[0])
            y_val = oy - float(p[1])
            dot_paths.append([
                {"x": x_val, "y": y_val},
                {"x": x_val + 0.02, "y": y_val}
            ])
        return dot_paths

    n = len(pts)
    mask = np.ones(n, dtype=bool)
    path_indices = [0]
    mask[0] = False
    curr_idx = 0

    for _ in range(n - 1):
        curr_pt = pts[curr_idx]
        dists_sq = (pts[:, 0] - curr_pt[0]) ** 2 + (pts[:, 1] - curr_pt[1]) ** 2
        dists_sq[~mask] = np.inf
        next_idx = int(np.argmin(dists_sq))
        path_indices.append(next_idx)
        mask[next_idx] = False
        curr_idx = next_idx

    ordered_pts = pts[path_indices]

    polylines = []
    jump_limit = max(3.5, gap_mm * 3.0)
    curr_poly = [{"x": ox + float(ordered_pts[0][0]), "y": oy - float(ordered_pts[0][1])}]

    for i in range(len(ordered_pts) - 1):
        p_curr = ordered_pts[i]
        p_next = ordered_pts[i + 1]
        dist = math.hypot(p_next[0] - p_curr[0], p_next[1] - p_curr[1])
        next_coord = {"x": ox + float(p_next[0]), "y": oy - float(p_next[1])}

        if dist <= jump_limit:
            curr_poly.append(next_coord)
        else:
            if len(curr_poly) >= 2:
                polylines.append(curr_poly)
            elif len(curr_poly) == 1:
                pt = curr_poly[0]
                polylines.append([pt, {"x": pt["x"] + 0.02, "y": pt["y"]}])
            curr_poly = [next_coord]

    if len(curr_poly) >= 2:
        polylines.append(curr_poly)
    elif len(curr_poly) == 1:
        pt = curr_poly[0]
        polylines.append([pt, {"x": pt["x"] + 0.02, "y": pt["y"]}])

    return polylines


def gen_contours(img_pil, box_w, box_h, min_x, max_x, min_y, max_y, levels=6):
    img_np = np.array(img_pil)
    ppm = 8
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
    blurred = cv2.GaussianBlur(img_resized, (5, 5), 0)
    ox = min_x + (box_w - final_w) / 2.0
    oy = max_y - (box_h - final_h) / 2.0

    thresholds = np.linspace(45, 215, levels)
    polylines = []
    for thresh in thresholds:
        _, bin_img = cv2.threshold(blurred, int(thresh), 255, cv2.THRESH_BINARY_INV)
        cnts, _ = cv2.findContours(bin_img, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in cnts:
            if len(cnt) < 2:
                continue
            poly = [{"x": ox + (pt[0][0] / ppm), "y": oy - (pt[0][1] / ppm)} for pt in cnt]
            poly.append(poly[0])
            polylines.append(poly)
    return polylines


def gen_hatch(img, px_w, px_h, ppm, final_w, gap_mm, ox, oy):
    final_h = px_h / ppm
    polylines = []

    def get_val(x_mm, y_mm):
        px, py = int(x_mm * ppm), int(y_mm * ppm)
        if 0 <= px < px_w and 0 <= py < px_h:
            return img.getpixel((px, py))
        return 255

    def trace(starts, dx, dy, threshold):
        for sx, sy in starts:
            cx, cy = sx, sy
            drawing = False
            curr_poly = []
            while 0 <= cx <= final_w and 0 <= cy <= final_h:
                val = get_val(cx, cy)
                phys_x, phys_y = ox + cx, oy - cy
                if val < threshold:
                    if not drawing:
                        curr_poly = [{"x": phys_x, "y": phys_y}]
                        drawing = True
                    else:
                        curr_poly.append({"x": phys_x, "y": phys_y})
                else:
                    if drawing and len(curr_poly) >= 2:
                        polylines.append(curr_poly)
                    drawing = False
                    curr_poly = []
                cx += dx
                cy += dy
            if drawing and len(curr_poly) >= 2:
                polylines.append(curr_poly)

    step = 0.5
    trace([(0, y * gap_mm) for y in range(int(final_h / gap_mm))], step, 0, 210)
    trace([(x * gap_mm, 0) for x in range(int(final_w / gap_mm))], 0, step, 160)
    starts = [(x * gap_mm, 0) for x in range(int(final_w / gap_mm))] + [(0, y * gap_mm) for y in range(int(final_h / gap_mm))]
    trace(starts, step, step, 110)
    starts = [(x * gap_mm, final_h) for x in range(int(final_w / gap_mm))] + [(0, y * gap_mm) for y in range(int(final_h / gap_mm))]
    trace(starts, step, -step, 60)
    return polylines


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
    polylines = []
    for cnt in contours:
        if len(cnt) < 2:
            continue
        poly = [{"x": ox + (pt[0][0] / ppm), "y": oy - (pt[0][1] / ppm)} for pt in cnt]
        poly.append(poly[0])
        polylines.append(poly)
    return polylines


# =============================================================================
# 3. PEN-PLOT OPTIMIZATION PIPELINE
# =============================================================================

def assemble_polylines(paths, tolerance=0.03):
    polys = [p for p in paths if len(p) >= 2]
    if not polys:
        return []

    cell_size = max(0.01, tolerance)
    tol_sq = tolerance * tolerance

    def get_cell(pt):
        return (int(round(pt['x'] / cell_size)), int(round(pt['y'] / cell_size)))

    active = {i: list(p) for i, p in enumerate(polys)}
    endpoint_grid = {}

    def add_endpoint(pid, is_end, pt):
        endpoint_grid.setdefault(get_cell(pt), []).append((pid, is_end))

    for pid, p in active.items():
        add_endpoint(pid, False, p[0])
        add_endpoint(pid, True, p[-1])

    merged_any = True
    while merged_any:
        merged_any = False
        for pid in list(active.keys()):
            if pid not in active:
                continue
            p = active[pid]
            cell = get_cell(p[-1])

            found = None
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for other_id, other_end in endpoint_grid.get((cell[0] + dx, cell[1] + dy), []):
                        if other_id == pid or other_id not in active:
                            continue
                        other_p = active[other_id]
                        target_pt = other_p[-1] if other_end else other_p[0]
                        d2 = (p[-1]['x'] - target_pt['x']) ** 2 + (p[-1]['y'] - target_pt['y']) ** 2
                        if d2 <= tol_sq:
                            found = (other_id, other_end)
                            break
                    if found:
                        break
                if found:
                    break

            if found:
                other_id, other_end = found
                other_p = active.pop(other_id)
                if other_end:
                    other_p.reverse()
                active[pid] = p + other_p[1:]
                add_endpoint(pid, True, active[pid][-1])
                merged_any = True

    return list(active.values())


def filter_noise(polylines, min_stroke_length=0.02):
    if min_stroke_length <= 0:
        return polylines
    result = []
    for poly in polylines:
        length = sum(math.hypot(p2['x'] - p1['x'], p2['y'] - p1['y']) for p1, p2 in zip(poly[:-1], poly[1:]))
        if length >= min_stroke_length:
            result.append(poly)
    return result


def rdp_simplify(points, epsilon=0.01):
    if len(points) <= 2:
        return points
    p1, p2 = points[0], points[-1]
    dx, dy = p2['x'] - p1['x'], p2['y'] - p1['y']
    line_len_sq = dx * dx + dy * dy

    dmax = 0.0
    index = 0
    if line_len_sq == 0:
        for i in range(1, len(points) - 1):
            d = math.hypot(points[i]['x'] - p1['x'], points[i]['y'] - p1['y'])
            if d > dmax:
                index, dmax = i, d
    else:
        sqrt_len = math.sqrt(line_len_sq)
        for i in range(1, len(points) - 1):
            cross = abs((points[i]['x'] - p1['x']) * dy - (points[i]['y'] - p1['y']) * dx)
            d = cross / sqrt_len
            if d > dmax:
                index, dmax = i, d

    if dmax > epsilon:
        res1 = rdp_simplify(points[:index + 1], epsilon)
        res2 = rdp_simplify(points[index:], epsilon)
        return res1[:-1] + res2
    else:
        return [points[0], points[-1]]


def reorder_polylines(polylines, start_pos=(0.0, 0.0)):
    if not polylines:
        return []
    remaining = {i: p for i, p in enumerate(polylines)}
    curr_x, curr_y = start_pos
    ordered = []

    cell_size = 5.0
    grid = {}
    for idx, p in remaining.items():
        for is_end, pt in [(False, p[0]), (True, p[-1])]:
            grid.setdefault((int(pt['x'] / cell_size), int(pt['y'] / cell_size)), []).append((idx, is_end))

    while remaining:
        curr_cell = (int(curr_x / cell_size), int(curr_y / cell_size))
        best_idx, best_dist_sq, best_rev = None, float('inf'), False

        for r in range(0, 16):
            found_candidate = False
            for dx in range(-r, r + 1):
                for dy in (-r, r) if r > 0 else (0,):
                    for idx, is_end in grid.get((curr_cell[0] + dx, curr_cell[1] + dy), []):
                        if idx not in remaining:
                            continue
                        p = remaining[idx]
                        pt = p[-1] if is_end else p[0]
                        d2 = (curr_x - pt['x']) ** 2 + (curr_y - pt['y']) ** 2
                        if d2 < best_dist_sq:
                            best_dist_sq, best_idx, best_rev = d2, idx, is_end
                            found_candidate = True
            if found_candidate:
                break

        if best_idx is None:
            for idx, p in remaining.items():
                d_start = (curr_x - p[0]['x']) ** 2 + (curr_y - p[0]['y']) ** 2
                d_end = (curr_x - p[-1]['x']) ** 2 + (curr_y - p[-1]['y']) ** 2
                if d_start < best_dist_sq:
                    best_dist_sq, best_idx, best_rev = d_start, idx, False
                if d_end < best_dist_sq:
                    best_dist_sq, best_idx, best_rev = d_end, idx, True

        poly = remaining.pop(best_idx)
        if best_rev:
            poly.reverse()
        ordered.append(poly)
        curr_x, curr_y = poly[-1]['x'], poly[-1]['y']

    return ordered


def stitch_polylines(polylines, stitch_gap=0.0):
    if stitch_gap <= 0.0 or not polylines:
        return polylines
    stitched = [list(polylines[0])]
    for next_poly in polylines[1:]:
        p_end = stitched[-1][-1]
        p_start = next_poly[0]
        if math.hypot(p_end['x'] - p_start['x'], p_end['y'] - p_start['y']) <= stitch_gap:
            stitched[-1].extend(next_poly)
        else:
            stitched.append(list(next_poly))
    return stitched


def liang_barsky_subsegments(p1, p2, xmin, xmax, ymin, ymax):
    dx = p2['x'] - p1['x']
    dy = p2['y'] - p1['y']
    p = [-dx, dx, -dy, dy]
    q = [p1['x'] - xmin, xmax - p1['x'], p1['y'] - ymin, ymax - p1['y']]
    u1, u2 = 0.0, 1.0

    for k in range(4):
        if p[k] == 0:
            if q[k] < 0:
                return None, [[p1, p2]]
        else:
            t = q[k] / p[k]
            if p[k] < 0:
                if t > u2:
                    return None, [[p1, p2]]
                if t > u1:
                    u1 = t
            else:
                if t < u1:
                    return None, [[p1, p2]]
                if t < u2:
                    u2 = t

    if u1 > u2:
        return None, [[p1, p2]]

    c1 = {"x": p1['x'] + u1 * dx, "y": p1['y'] + u1 * dy}
    c2 = {"x": p1['x'] + u2 * dx, "y": p1['y'] + u2 * dy}
    inside_seg = [c1, c2]

    outside_segs = []
    if u1 > 1e-5:
        outside_segs.append([p1, c1])
    if u2 < 1.0 - 1e-5:
        outside_segs.append([c2, p2])

    return inside_seg, outside_segs


def clip_polyline_to_box(poly, xmin, xmax, ymin, ymax):
    inside_subpolys = []
    outside_segs = []
    curr_poly = []

    for i in range(len(poly) - 1):
        inside, outside = liang_barsky_subsegments(poly[i], poly[i + 1], xmin, xmax, ymin, ymax)
        outside_segs.extend(outside)
        if inside is None:
            if len(curr_poly) >= 2:
                inside_subpolys.append(curr_poly)
            curr_poly = []
        else:
            c1, c2 = inside
            if not curr_poly:
                curr_poly = [c1, c2]
            else:
                if abs(curr_poly[-1]['x'] - c1['x']) < 1e-4 and abs(curr_poly[-1]['y'] - c1['y']) < 1e-4:
                    curr_poly.append(c2)
                else:
                    if len(curr_poly) >= 2:
                        inside_subpolys.append(curr_poly)
                    curr_poly = [c1, c2]
    if len(curr_poly) >= 2:
        inside_subpolys.append(curr_poly)

    return inside_subpolys, outside_segs


def optimize_plot_paths(raw_paths, bbox, bed_size=180.0, tolerance=0.03, min_stroke_length=0.02,
                        rdp_epsilon=0.01, stitch_gap=0.0, start_pos=(0.0, 0.0)):
    assembled = assemble_polylines(raw_paths, tolerance=tolerance)
    filtered = filter_noise(assembled, min_stroke_length=min_stroke_length)
    simplified = [rdp_simplify(poly, epsilon=rdp_epsilon) for poly in filtered]
    simplified = [p for p in simplified if len(p) >= 2]
    reordered = reorder_polylines(simplified, start_pos=start_pos)
    stitched = stitch_polylines(reordered, stitch_gap=stitch_gap)

    xmin = max(0.0, float(bbox['min_x']))
    xmax = min(float(bed_size), float(bbox['max_x']))
    ymin = max(0.0, float(bbox['min_y']))
    ymax = min(float(bed_size), float(bbox['max_y']))

    clipped_inside = []
    clipped_outside = []
    for poly in stitched:
        ins, outs = clip_polyline_to_box(poly, xmin, xmax, ymin, ymax)
        clipped_inside.extend(ins)
        clipped_outside.extend(outs)

    return clipped_inside, clipped_outside


# =============================================================================
# 4. TEXT & IMAGE PREPROCESSING
# =============================================================================

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
                if get_text_width(test_line) > target_w and current_line:
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
    if rotation != 0:
        img = img.rotate(-rotation, expand=True, fillcolor=255)
    return ImageEnhance.Contrast(img).enhance(contrast)


def prepare_image(img, box_w, box_h, ppm=4):
    img_ratio = img.width / max(1, img.height)
    box_ratio = box_w / max(0.1, box_h)
    if img_ratio > box_ratio:
        final_w = box_w
        final_h = box_w / img_ratio
    else:
        final_h = box_h
        final_w = box_h * img_ratio
    px_w, px_h = int(final_w * ppm), int(final_h * ppm)
    return img.resize((max(1, px_w), max(1, px_h))), px_w, px_h, ppm, final_w, final_h


# =============================================================================
# 5. REQUEST HANDLER & GCODE GENERATOR
# =============================================================================

def process_paths_request(data):
    bbox = data.get('bbox')
    bed_size = float(data.get('bed_size', 180.0))
    if not bbox:
        return None, None, "Set Bounding Box (4 points) first."
    min_x, max_x = float(bbox['min_x']), float(bbox['max_x'])
    min_y, max_y = float(bbox['min_y']), float(bbox['max_y'])
    box_w, box_h = max_x - min_x, max_y - min_y
    if box_w <= 0 or box_h <= 0:
        return None, None, "Invalid Bounding Box Area"

    raw_paths = []

    if data['type'] == 'text':
        paths, msg = generate_text_paths(
            data['text'], data['font'], data['line_spacing'],
            data['font_size'], min_x, max_x, min_y, max_y, data.get('auto_wrap', True)
        )
        if not paths:
            return None, None, msg
        raw_paths = paths
    else:
        method = data.get('method', 'hatch')
        try:
            img_scale = float(data.get('img_scale', 100)) / 100.0
            offset_x = float(data.get('img_offset_x', 0))
            offset_y = float(data.get('img_offset_y', 0))
            rotation = float(data.get('img_rotate', 0))
            img_pil = get_rotated_image_pil(data['image'], float(data.get('img_contrast', 1.0)), rotation)

            if method in ('canny', 'contours'):
                cx, cy = min_x + box_w / 2.0, min_y + box_h / 2.0
                if method == 'canny':
                    raw_contours = gen_canny(img_pil, box_w, box_h, min_x, max_x, min_y, max_y)
                else:
                    levels_val = int(data.get('levels', 6))
                    raw_contours = gen_contours(img_pil, box_w, box_h, min_x, max_x, min_y, max_y, levels=levels_val)

                transformed = []
                for poly in raw_contours:
                    transformed.append([
                        {
                            "x": cx + (pt['x'] - cx) * img_scale + offset_x,
                            "y": cy + (pt['y'] - cy) * img_scale - offset_y
                        }
                        for pt in poly
                    ])
                raw_paths = transformed
            else:
                img_ppm = 8 if method in ('skeleton', 'flow_field') else 4
                img, px_w, px_h, ppm, original_final_w, original_final_h = prepare_image(img_pil, box_w, box_h, ppm=img_ppm)
                scaled_w = original_final_w * img_scale
                scaled_h = original_final_h * img_scale
                ox = min_x + (box_w - scaled_w) / 2.0 + offset_x
                oy = max_y - (box_h - scaled_h) / 2.0 - offset_y
                effective_ppm = ppm / max(0.001, img_scale)
                gap_val = float(data.get('img_gap', 1.0))

                if method == 'skeleton':
                    raw_paths = gen_skeleton(img_pil, px_w, px_h, effective_ppm, ox, oy)
                elif method == 'spiral':
                    raw_paths = gen_spiral(img, px_w, px_h, effective_ppm, scaled_w, scaled_h, gap_val, ox, oy)
                elif method == 'squiggle':
                    raw_paths = gen_squiggle(img, px_w, px_h, effective_ppm, scaled_w, scaled_h, gap_val, ox, oy)
                elif method == 'flow_field':
                    raw_paths = gen_flow_field(img_pil, px_w, px_h, effective_ppm, scaled_w, scaled_h, gap_val, ox, oy)
                elif method == 'tsp':
                    raw_paths = gen_stipple(img, px_w, px_h, effective_ppm, scaled_w, scaled_h, gap_val, ox, oy, tsp_connect=True)
                elif method == 'stipple':
                    raw_paths = gen_stipple(img, px_w, px_h, effective_ppm, scaled_w, scaled_h, gap_val, ox, oy, tsp_connect=False)
                else:
                    raw_paths = gen_hatch(img, px_w, px_h, effective_ppm, scaled_w, gap_val, ox, oy)
        except Exception as e:
            return None, None, str(e)

    # -------------------------------------------------------------------------
    # Physical Pen Width & Sub-Visible Optimization Scaling
    # -------------------------------------------------------------------------
    pen_w = float(data.get('pen_width', 0.30))

    # Sub-visible RDP epsilon
    default_rdp = max(0.005, min(0.015, pen_w / 4.0))
    rdp_eps = float(data.get('rdp_epsilon', default_rdp))

    # Sub-visible assembly tolerance
    default_tol = max(0.02, min(0.06, pen_w / 3.0))
    tol = float(data.get('tolerance', default_tol))

    # Strict noise filter and stitch gap to prevent lost dots or phantom marks
    min_stroke = 0.0 if data.get('method') in ('stipple', 'tsp') else float(data.get('min_stroke_length', 0.02))
    stitch_gap = float(data.get('stitch_gap', 0.0))

    mid_pos = (float(bed_size) / 2.0, float(bed_size) / 2.0)
    optimized_polylines, out_paths = optimize_plot_paths(
        raw_paths,
        bbox=bbox,
        bed_size=bed_size,
        tolerance=tol,
        min_stroke_length=min_stroke,
        rdp_epsilon=rdp_eps,
        stitch_gap=stitch_gap,
        start_pos=mid_pos
    )

    if data.get('draw_bbox'):
        bx0, by0 = max(0.0, min_x), max(0.0, min_y)
        bx1, by1 = min(float(bed_size), max_x), min(float(bed_size), max_y)
        optimized_polylines.append([
            {"x": bx0, "y": by0},
            {"x": bx1, "y": by0},
            {"x": bx1, "y": by1},
            {"x": bx0, "y": by1},
            {"x": bx0, "y": by0}
        ])

    return optimized_polylines, out_paths, "Success"


def generate_full_gcode(paths, base_z, speed, z_hop, bed_size, is_download=False):
    """
    Generates G-code optimized for fluid motion. Eliminates buffer-stalling M400 commands
    during drawing to prevent capillary ink pooling on fine paper.
    """
    hop_z = min(base_z + z_hop, float(bed_size))
    mid = float(bed_size) / 2.0
    bed_max = float(bed_size)
    speed = int(speed)
    SAFE_Z_FEEDRATE = int(min(speed, 1200))
    SAFE_XY_FEEDRATE = int(min(speed, 18000))

    if is_download:
        gcode = [
            "; --- BAMBUSCRIBE AUTONOMOUS PLOTTER GCODE ---",
            "M73 P0 R1",
            "M104 S0 ; turn off nozzle heater",
            "M140 S0 ; turn off bed heater",
            "M106 S0 ; turn off fan",
            "M17 ; Enable steppers",
            "M84 S0 ; Keep steppers energized",
            "G90 ; Absolute positioning",
            "M83 ; Relative extrusion",
            "",
            "M400",
            "M400 U1 ; PAUSE: Ensure pen is parked. Press Resume to home",
            "G28 ; Home all axes",
            f"G1 Z50 F{SAFE_Z_FEEDRATE}",
            "M400",
            "M17",
            "M400 U1 ; PAUSE: Attach pen and calibrate paper. Press Resume to plot",
            ""
        ]
        current_pos = {"x": None, "y": None}
    else:
        gcode = [
            "; --- BAMBUSCRIBE PLOTTER GCODE ---",
            "M73 P0 R1",
            "M104 S0 ; turn off nozzle heater",
            "M140 S0 ; turn off bed heater",
            "M106 S0 ; turn off fan",
            "M17 ; Enable steppers",
            "G90 ; Absolute positioning",
            "M83 ; Relative extrusion",
            f"G1 Z90 F{SAFE_Z_FEEDRATE}",
            f"G0 X{mid:.3f} Y{mid:.3f} F{SAFE_XY_FEEDRATE}",
            ""
        ]
        current_pos = {"x": mid, "y": mid}

    def is_close(pA, pB):
        if pA['x'] is None or pA['y'] is None:
            return False
        return math.hypot(pA['x'] - pB['x'], pA['y'] - pB['y']) < 0.03

    pen_is_down = False

    for poly in paths:
        if not poly or len(poly) < 2:
            continue
        p_start = poly[0]

        if not is_close(current_pos, p_start):
            if pen_is_down:
                gcode.append(f"G1 Z{hop_z:.3f} F{SAFE_Z_FEEDRATE}")
                pen_is_down = False
            gcode.append(f"G0 X{p_start['x']:.3f} Y{p_start['y']:.3f} F{SAFE_XY_FEEDRATE}")
            gcode.append(f"G1 Z{base_z:.3f} F{SAFE_Z_FEEDRATE}")
            pen_is_down = True
        else:
            if not pen_is_down:
                gcode.append(f"G1 Z{base_z:.3f} F{SAFE_Z_FEEDRATE}")
                pen_is_down = True

        for pt in poly[1:]:
            gcode.append(f"G1 X{pt['x']:.3f} Y{pt['y']:.3f} F{speed}")
            current_pos = pt

    if pen_is_down:
        gcode.append(f"G1 Z{hop_z:.3f} F{SAFE_Z_FEEDRATE}")

    gcode.extend([
        f"G1 Z50 F{SAFE_Z_FEEDRATE} ; High lift",
        f"G0 X{mid:.3f} Y{bed_max - 10:.3f} F{SAFE_XY_FEEDRATE} ; Present bed",
        "M400 S1",
        "M73 P100 R0"
    ])

    return "\n".join(gcode)