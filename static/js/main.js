import { PlotterVisualizer } from './visualizer.js';

let BED_SIZE = 180;
let currentMode = 'text';
let base64Image = null;
let previewTimeout = null;
let isHomed = false;
let isPreviewing = false;
let pendingPreview = false;
let cachedPreviewData = null;

let currentPos = { x: 0, y: 0, z: 0 };
let bboxPoints = JSON.parse(localStorage.getItem('plotter_bbox_points')) || [];
let manualQueue = [];
let predictedPos = { x: 0, y: 0, z: 0 };
let isProcessingQueue = false;

let currentPenWidth = parseFloat(localStorage.getItem('plotter_pen_width') || '0.30');

const visualizer = new PlotterVisualizer('canvas-container');
visualizer.setPenWidth(currentPenWidth);

function updateCoordDisplay(x, y, z) {
    const disp = document.getElementById('coord-display');
    if (disp) {
        disp.innerText = `X: ${x.toFixed(1)} | Y: ${y.toFixed(1)} | Z: ${z.toFixed(2)}`;
    }
}

// Hybrid Client-Server State
let isWebMode = false;
let pyodideWorker = null;
let pyodideReady = false;
let pendingWorkerCallbacks = new Map();
let workerMsgId = 0;

function getPyodideWorker() {
    if (!pyodideWorker) {
        pyodideWorker = new Worker('./static/js/pyodide-worker.js');

        pyodideWorker.onmessage = (e) => {
            const { status, message, id } = e.data;
            if (status === 'loading') {
                const loader = document.getElementById('engine-loader');
                const loaderText = document.getElementById('engine-loader-text');
                if (loader && loaderText) {
                    loader.style.display = 'flex';
                    loaderText.innerText = message || 'Loading Python runtime...';
                }
            } else if (status === 'ready') {
                pyodideReady = true;
                const loader = document.getElementById('engine-loader');
                if (loader && !isPreviewing) loader.style.display = 'none';
            }
            if (id && pendingWorkerCallbacks.has(id)) {
                const { resolve, timer } = pendingWorkerCallbacks.get(id);
                clearTimeout(timer);
                pendingWorkerCallbacks.delete(id);
                resolve(e.data);
            }
        };

        const handleWorkerFailure = (err) => {
            console.error('[Pyodide Worker Failure]', err);
            const errMsg = err?.message || 'Pyodide worker failed or was terminated.';
            for (const [id, { reject, timer }] of pendingWorkerCallbacks.entries()) {
                clearTimeout(timer);
                reject(new Error(errMsg));
            }
            pendingWorkerCallbacks.clear();
            pyodideWorker = null;
            pyodideReady = false;
            const loader = document.getElementById('engine-loader');
            if (loader) loader.style.display = 'none';
        };

        pyodideWorker.onerror = handleWorkerFailure;
        pyodideWorker.onmessageerror = handleWorkerFailure;
    }
    return pyodideWorker;
}

function callWorker(action, data, timeoutMs = 60000) {
    return new Promise((resolve, reject) => {
        const id = ++workerMsgId;
        const timer = setTimeout(() => {
            if (pendingWorkerCallbacks.has(id)) {
                pendingWorkerCallbacks.delete(id);
                reject(new Error(`Worker action '${action}' timed out.`));
            }
        }, timeoutMs);

        pendingWorkerCallbacks.set(id, { resolve, reject, timer });
        try {
            getPyodideWorker().postMessage({ action, id, data });
        } catch (err) {
            clearTimeout(timer);
            pendingWorkerCallbacks.delete(id);
            reject(err);
        }
    });
}

// =============================================================================
// HYBRID DUAL-MODE ENVIRONMENT DETECTION & UI SWITCHING
// =============================================================================
function enableWebMode() {
    isWebMode = true;

    const badge = document.getElementById('mode-badge');
    if (badge) {
        badge.textContent = 'Web Mode';
        badge.style.background = 'rgba(230, 81, 0, 0.12)';
        badge.style.color = '#e65100';
    }

    // Switch Controls card view: Show static BBox setup, hide physical printer controls
    const staticBBox = document.getElementById('static-bbox-controls');
    const hwPanel = document.getElementById('hardware-panel-wrapper');
    const title = document.getElementById('card-controls-title');

    if (staticBBox) staticBBox.style.display = 'flex';
    if (hwPanel) hwPanel.style.display = 'none';
    if (title) title.innerHTML = '<md-icon>crop_free</md-icon> Bounding Box & Setup';

    const btnPlot = document.getElementById('btn-plot');
    if (btnPlot) btnPlot.style.display = 'none';

    // Offline camera notice
    const camImg = document.getElementById('camera-stream');
    const camOffline = document.getElementById('camera-offline-msg');
    if (camImg) camImg.style.display = 'none';
    if (camOffline) camOffline.style.display = 'flex';

    updateBBoxFromStaticInputs();
}

function enableLocalMode() {
    isWebMode = false;

    const badge = document.getElementById('mode-badge');
    if (badge) {
        badge.textContent = 'Local Server Connected';
        badge.style.background = 'rgba(0, 104, 116, 0.12)';
        badge.style.color = 'var(--md-sys-color-primary)';
    }

    // Revert Controls card view: Hide static inputs, restore original physical hardware panel
    const staticBBox = document.getElementById('static-bbox-controls');
    const hwPanel = document.getElementById('hardware-panel-wrapper');
    const title = document.getElementById('card-controls-title');

    if (staticBBox) staticBBox.style.display = 'none';
    if (hwPanel) hwPanel.style.display = 'flex';
    if (title) title.innerHTML = '<md-icon>gamepad</md-icon> Controls';

    const btnPlot = document.getElementById('btn-plot');
    if (btnPlot) btnPlot.style.display = 'inline-flex';

    const camImg = document.getElementById('camera-stream');
    const camOffline = document.getElementById('camera-offline-msg');
    if (camImg) camImg.style.display = 'block';
    if (camOffline) camOffline.style.display = 'none';

    // Restore original 4-point BBox list and visualization
    visualizer.updateBBoxDots(bboxPoints, BED_SIZE);
    renderBBoxList();
    const btnOrigin = document.getElementById('btn-origin');
    if (btnOrigin) {
        btnOrigin.innerHTML = bboxPoints.length === 4
            ? `<md-icon slot="icon">clear</md-icon> Clear BBox`
            : `<md-icon slot="icon">crop_free</md-icon> Set BBox (${bboxPoints.length}/4)`;
    }
}

async function detectEnvironment() {
    if (window.location.protocol === 'file:' || window.location.hostname.includes('github.io')) {
        enableWebMode();
        return;
    }

    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 1200);
        const res = await fetch('/api/state', { signal: controller.signal });
        clearTimeout(timeoutId);
        if (res.ok) enableLocalMode();
        else enableWebMode();
    } catch (e) {
        enableWebMode();
    }
}
detectEnvironment();

// =============================================================================
// MATERIAL 3 TOOLTIP ENGINE
// =============================================================================
function initM3Tooltips() {
    const tooltipEl = document.getElementById('m3-tooltip');
    if (!tooltipEl) return;
    let hoverTimer = null;

    document.addEventListener('mouseover', (e) => {
        const path = e.composedPath ? e.composedPath() : [e.target];
        let target = null;
        let text = null;

        for (const el of path) {
            if (el && el.getAttribute) {
                text = el.getAttribute('data-tooltip') || el.getAttribute('title');
                if (text) {
                    target = el;
                    if (el.hasAttribute('title')) {
                        el.setAttribute('data-tooltip', text);
                        el.removeAttribute('title');
                    }
                    break;
                }
            }
        }

        if (!target || !text) return;

        clearTimeout(hoverTimer);
        hoverTimer = setTimeout(() => {
            tooltipEl.textContent = text;
            tooltipEl.classList.add('visible');

            const rect = target.getBoundingClientRect();
            const tipRect = tooltipEl.getBoundingClientRect();

            let left = rect.left + (rect.width / 2) - (tipRect.width / 2);
            left = Math.max(12, Math.min(window.innerWidth - tipRect.width - 12, left));

            let top = rect.top - tipRect.height - 8;
            if (top < 12) top = rect.bottom + 8;

            tooltipEl.style.left = `${left}px`;
            tooltipEl.style.top = `${top}px`;
        }, 80);
    }, true);

    document.addEventListener('mouseout', () => {
        clearTimeout(hoverTimer);
        tooltipEl.classList.remove('visible');
    }, true);
}
initM3Tooltips();

// =============================================================================
// ONLINE WEB MODE: STATIC BBOX & TOOLHEAD CONTACT HEIGHT
// =============================================================================
function updateBBoxFromStaticInputs() {
    if (!isWebMode) return;

    const w = Math.max(5, parseFloat(document.getElementById('bbox-width')?.value) || 100);
    const h = Math.max(5, parseFloat(document.getElementById('bbox-height')?.value) || 100);
    const cxVal = parseFloat(document.getElementById('bbox-center-x')?.value);
    const cyVal = parseFloat(document.getElementById('bbox-center-y')?.value);
    const cx = !isNaN(cxVal) ? cxVal : (BED_SIZE / 2.0);
    const cy = !isNaN(cyVal) ? cyVal : (BED_SIZE / 2.0);

    const zVal = parseFloat(document.getElementById('toolhead-z')?.value);
    const z = Math.max(0, Math.min(BED_SIZE, !isNaN(zVal) ? zVal : 30.0));

    const minX = Math.max(0, cx - (w / 2.0));
    const maxX = Math.min(BED_SIZE, cx + (w / 2.0));
    const minY = Math.max(0, cy - (h / 2.0));
    const maxY = Math.min(BED_SIZE, cy + (h / 2.0));

    bboxPoints = [
        { x: minX, y: minY, z: z },
        { x: maxX, y: minY, z: z },
        { x: maxX, y: maxY, z: z },
        { x: minX, y: maxY, z: z }
    ];

    localStorage.setItem('plotter_bbox_static', JSON.stringify({ w, h, cx, cy, z }));
    visualizer.updateToolhead(cx, cy, z, BED_SIZE);
    visualizer.updateBBoxDots(bboxPoints, BED_SIZE);
    updateCoordDisplay(cx, cy, z);
    autoPreview();
}

function applyBBoxPreset(preset) {
    const mid = BED_SIZE / 2.0;
    let w = 100, h = 100;

    if (preset === 'center-100') {
        w = Math.min(100, BED_SIZE - 10);
        h = Math.min(100, BED_SIZE - 10);
    } else if (preset === 'full') {
        w = BED_SIZE - 10;
        h = BED_SIZE - 10;
    } else if (preset === 'a6') {
        w = Math.min(105, BED_SIZE - 10);
        h = Math.min(148, BED_SIZE - 10);
    } else if (preset === 'a5') {
        w = Math.min(148, BED_SIZE - 10);
        h = Math.min(210, BED_SIZE - 10);
    }

    document.getElementById('bbox-width').value = w;
    document.getElementById('bbox-height').value = h;
    document.getElementById('bbox-center-x').value = mid;
    document.getElementById('bbox-center-y').value = mid;

    updateBBoxFromStaticInputs();
}

document.querySelectorAll('.btn-bbox-preset').forEach(btn => {
    btn.addEventListener('click', (e) => {
        applyBBoxPreset(e.currentTarget.dataset.preset);
    });
});

['bbox-width', 'bbox-height', 'bbox-center-x', 'bbox-center-y', 'toolhead-z'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', () => updateBBoxFromStaticInputs());
});

const savedStaticBBox = JSON.parse(localStorage.getItem('plotter_bbox_static')) || {};
if (savedStaticBBox.w) document.getElementById('bbox-width').value = savedStaticBBox.w;
if (savedStaticBBox.h) document.getElementById('bbox-height').value = savedStaticBBox.h;
if (savedStaticBBox.cx !== undefined) document.getElementById('bbox-center-x').value = savedStaticBBox.cx;
if (savedStaticBBox.cy !== undefined) document.getElementById('bbox-center-y').value = savedStaticBBox.cy;
if (savedStaticBBox.z !== undefined) document.getElementById('toolhead-z').value = savedStaticBBox.z;

// =============================================================================
// OFFLINE LOCAL FLASK MODE: 4-POINT MANUAL BBOX & PRINTER JOGGING
// =============================================================================
document.getElementById('btn-home').addEventListener('click', async () => {
    try {
        const res = await fetch('/api/home', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ bed_size: BED_SIZE }) });
        const data = await res.json();
        if (data.status === 'success') {
            isHomed = true;
            document.getElementById('movement-controls').style.opacity = '1';
            document.getElementById('movement-controls').style.pointerEvents = 'auto';
            currentPos = data.state.position;
            visualizer.updateToolhead(currentPos.x, currentPos.y, currentPos.z, BED_SIZE);
            updateCoordDisplay(currentPos.x, currentPos.y, currentPos.z);
            manualQueue = [];
            predictedPos = {...currentPos};
            visualizer.updateTargetDot(predictedPos, manualQueue.length, BED_SIZE);
            updateQueueUI();
        }
    } catch (err) {}
});

function updateQueueUI() {
    const qd = document.getElementById('queue-display');
    if (manualQueue.length === 0) qd.innerHTML = "Command Queue Empty";
    else qd.innerHTML = manualQueue.map((c, i) => `[${i+1}] Move ${c.axis} by ${c.amount > 0 ? '+'+c.amount : c.amount} @ F${c.speed}`).join('<br>');
}

async function processQueue() {
    if (isProcessingQueue) return;
    isProcessingQueue = true;

    while(manualQueue.length > 0) {
        const cmd = manualQueue[0];
        try {
            const res = await fetch('/api/move', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...cmd, bed_size: BED_SIZE }) });
            const data = await res.json();
            if (data.status === 'success') {
                currentPos = data.state.position;
                visualizer.updateToolhead(currentPos.x, currentPos.y, currentPos.z, BED_SIZE);
                updateCoordDisplay(currentPos.x, currentPos.y, currentPos.z);
                await new Promise(r => setTimeout(r, data.duration * 1000));
            } else {
                showWarning(data.message);
                manualQueue = [];
                predictedPos = {...currentPos};
                break;
            }
        } catch (err) {
            manualQueue = [];
            predictedPos = {...currentPos};
            break;
        }
        manualQueue.shift();
        updateQueueUI();
    }
    isProcessingQueue = false;
    visualizer.updateTargetDot(predictedPos, manualQueue.length, BED_SIZE);
}

document.querySelectorAll('.btn-move').forEach(btn => {
    btn.addEventListener('click', (e) => {
        if (!isHomed) return;
        const axis = e.target.dataset.axis;
        const dir = parseFloat(e.target.dataset.dir);
        const val = parseFloat(document.getElementById('step-input').value);
        const speed = axis === 'Z' ? document.getElementById('manual-speed-z').value : document.getElementById('manual-speed').value;
        if (isNaN(val) || val <= 0) return;

        let nextPos = {...predictedPos};
        nextPos[axis.toLowerCase()] += val * dir;
        if(nextPos.x < 0 || nextPos.x > BED_SIZE || nextPos.y < 0 || nextPos.y > BED_SIZE || nextPos.z < 0 || nextPos.z > BED_SIZE) return showWarning(`Move block: ${axis} bounds exceeded!`);

        predictedPos = nextPos;
        manualQueue.push({ axis, amount: val * dir, speed });
        visualizer.updateTargetDot(predictedPos, manualQueue.length, BED_SIZE);
        updateQueueUI();
        processQueue();
    });
});

document.getElementById('btn-origin').addEventListener('click', () => {
    if (!isHomed) return showWarning("Home first to track coordinates.");
    if (bboxPoints.length < 4) {
        bboxPoints.push({ ...currentPos });
        localStorage.setItem('plotter_bbox_points', JSON.stringify(bboxPoints));
        if (bboxPoints.length === 4) {
            document.getElementById('btn-origin').innerHTML = `<md-icon slot="icon">clear</md-icon> Clear BBox`;
            visualizer.updateBBoxDots(bboxPoints, BED_SIZE);
            renderBBoxList();
            showWarning("Bounding box complete!");
            autoPreview();
        } else {
            document.getElementById('btn-origin').innerHTML = `<md-icon slot="icon">crop_free</md-icon> Set BBox (${bboxPoints.length}/4)`;
            visualizer.updateBBoxDots(bboxPoints, BED_SIZE);
            renderBBoxList();
        }
    } else {
        bboxPoints = [];
        cachedPreviewData = null;
        localStorage.removeItem('plotter_bbox_points');
        document.getElementById('btn-origin').innerHTML = `<md-icon slot="icon">crop_free</md-icon> Set BBox (0/4)`;
        visualizer.updateBBoxDots(bboxPoints, BED_SIZE);
        renderBBoxList();
        visualizer.clearPaths();
        document.getElementById('btn-plot').disabled = true;
        document.getElementById('btn-download').disabled = true;
    }
});

function renderBBoxList() {
    const list = document.getElementById('bbox-points-list');
    const container = document.getElementById('bbox-manage-container');
    if (bboxPoints.length === 0) { container.style.display = 'none'; return; }
    container.style.display = 'flex';
    list.innerHTML = '';
    bboxPoints.forEach((pt, i) => {
        const row = document.createElement('div');
        row.style.display = 'flex';
        row.style.justifyContent = 'space-between';
        row.style.alignItems = 'center';
        row.style.fontSize = '14px';
        row.style.background = 'var(--surface-color)';
        row.style.padding = '8px 12px';
        row.style.borderRadius = '8px';
        row.style.border = '1px solid var(--border-color)';

        const label = document.createElement('span');
        const zStr = (pt.z !== undefined) ? ` Z${pt.z.toFixed(2)}` : '';
        label.innerHTML = `<strong>P${i+1}</strong> <span style="color:#888; font-family:monospace; margin-left:8px;">X${pt.x.toFixed(1)} Y${pt.y.toFixed(1)}${zStr}</span>`;

        const actions = document.createElement('div');
        actions.style.display = 'flex';
        actions.style.gap = '8px';

        const btnGo = document.createElement('md-outlined-button');
        btnGo.innerHTML = `<md-icon slot="icon" style="font-size:18px;">my_location</md-icon> Go`;
        btnGo.style.setProperty('--md-outlined-button-container-shape', '8px');
        btnGo.onclick = () => jumpToBBoxPoint(i);

        const btnUpdate = document.createElement('md-filled-tonal-button');
        btnUpdate.innerHTML = `<md-icon slot="icon" style="font-size:18px;">edit_location</md-icon> Update`;
        btnUpdate.style.setProperty('--md-filled-tonal-button-container-shape', '8px');
        btnUpdate.onclick = () => updateBBoxPoint(i);

        actions.appendChild(btnGo);
        actions.appendChild(btnUpdate);
        row.appendChild(label);
        row.appendChild(actions);
        list.appendChild(row);
    });
}

async function jumpToBBoxPoint(i) {
    if (!isHomed) return showWarning("Home first!");
    manualQueue = [];
    updateQueueUI();
    const pt = bboxPoints[i];
    const speed = document.getElementById('manual-speed').value || 12000;
    const z_hop = document.getElementById('global-zhop').value || 4.0;
    try {
        const res = await fetch('/api/goto_absolute', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ x: pt.x, y: pt.y, z: pt.z, speed: speed, bed_size: BED_SIZE, z_hop: z_hop }) });
        const data = await res.json();
        if (data.status === 'success') {
            currentPos = data.state.position;
            visualizer.updateToolhead(currentPos.x, currentPos.y, currentPos.z, BED_SIZE);
            updateCoordDisplay(currentPos.x, currentPos.y, currentPos.z);
            predictedPos = {...currentPos};
            visualizer.updateTargetDot(predictedPos, manualQueue.length, BED_SIZE);
        } else showWarning(data.message);
    } catch (e) {}
}

function updateBBoxPoint(i) {
    bboxPoints[i] = { ...currentPos };
    localStorage.setItem('plotter_bbox_points', JSON.stringify(bboxPoints));
    renderBBoxList();
    visualizer.updateBBoxDots(bboxPoints, BED_SIZE);
    if (bboxPoints.length === 4) autoPreview();
}

// =============================================================================
// PRINTER MODEL SELECTION & THEME
// =============================================================================
window.selectModel = function(size) {
    BED_SIZE = size;
    document.getElementById('model-modal').style.display = 'none';
    visualizer.initScene(BED_SIZE);

    if (isWebMode) {
        const curCx = parseFloat(document.getElementById('bbox-center-x').value);
        if (curCx === 90 && size === 256) {
            document.getElementById('bbox-center-x').value = 128;
            document.getElementById('bbox-center-y').value = 128;
        }
        updateBBoxFromStaticInputs();
    } else {
        visualizer.updateToolhead(currentPos.x, currentPos.y, currentPos.z, BED_SIZE);
        if (bboxPoints.length === 4) {
            visualizer.updateBBoxDots(bboxPoints, BED_SIZE);
            renderBBoxList();
            autoPreview();
        }
    }
};

const themeToggle = document.getElementById('theme-toggle');
const updateTheme = () => {
    themeToggle.innerHTML = document.body.classList.contains('dark-mode') ? '<md-icon>light_mode</md-icon>' : '<md-icon>dark_mode</md-icon>';
    localStorage.setItem('plotter_theme', document.body.classList.contains('dark-mode') ? 'dark' : 'light');
};
if(localStorage.getItem('plotter_theme') === 'dark') document.body.classList.add('dark-mode');
updateTheme();

themeToggle.addEventListener('click', () => {
    document.body.classList.toggle('dark-mode');
    updateTheme();
    if (cachedPreviewData) {
        renderCanvas2D(cachedPreviewData);
        visualizer.drawPreview(cachedPreviewData.paths, cachedPreviewData.out_paths, cachedPreviewData.origin_z, BED_SIZE, document.body.classList.contains('dark-mode'));
    } else if (bboxPoints.length === 4) {
        triggerPreview();
    }
});

// =============================================================================
// TOOLHEAD PEN SIZE / LINE WIDTH SLIDER
// =============================================================================
const penWidthSlider = document.getElementById('vis-pen-width');
const penWidthLabel = document.getElementById('label-pen-width');
if (penWidthSlider) {
    penWidthSlider.value = currentPenWidth;
    if (penWidthLabel) penWidthLabel.innerText = currentPenWidth.toFixed(2) + ' mm';
    penWidthSlider.addEventListener('input', (e) => {
        currentPenWidth = parseFloat(e.target.value);
        if (penWidthLabel) penWidthLabel.innerText = currentPenWidth.toFixed(2) + ' mm';
        localStorage.setItem('plotter_pen_width', currentPenWidth.toString());
        visualizer.setPenWidth(currentPenWidth);
        if (cachedPreviewData && currentMode === 'image') {
            renderCanvas2D(cachedPreviewData);
        }
        autoPreview();
    });
}

// Mode Switching (Text / Image)
window.setTab = function(mode) {
    currentMode = mode;
    document.getElementById('view-text').style.display = mode === 'text' ? 'block' : 'none';
    document.getElementById('view-image').style.display = mode === 'image' ? 'grid' : 'none';

    document.getElementById('btn-tab-text').outerHTML = mode === 'text'
        ? `<md-filled-button id="btn-tab-text" onclick="window.setTab('text')"><md-icon slot="icon">text_fields</md-icon> Write Text</md-filled-button>`
        : `<md-outlined-button id="btn-tab-text" onclick="window.setTab('text')"><md-icon slot="icon">text_fields</md-icon> Write Text</md-outlined-button>`;

    document.getElementById('btn-tab-img').outerHTML = mode === 'image'
        ? `<md-filled-button id="btn-tab-img" onclick="window.setTab('image')"><md-icon slot="icon">image</md-icon> Plot Image</md-filled-button>`
        : `<md-outlined-button id="btn-tab-img" onclick="window.setTab('image')"><md-icon slot="icon">image</md-icon> Plot Image</md-outlined-button>`;
};

// UI Persistence via LocalStorage
const imgSettings = JSON.parse(localStorage.getItem('plotter_img_settings')) || {};
if (imgSettings.gap) { document.getElementById('img-gap').value = imgSettings.gap; document.getElementById('label-gap').innerText = parseFloat(imgSettings.gap).toFixed(1) + ' mm'; }
if (imgSettings.contrast) { document.getElementById('img-contrast').value = imgSettings.contrast; document.getElementById('label-contrast').innerText = parseFloat(imgSettings.contrast).toFixed(1) + ' x'; }
if (imgSettings.scale) { document.getElementById('img-scale').value = imgSettings.scale; document.getElementById('label-scale').innerText = parseInt(imgSettings.scale) + ' %'; }
if (imgSettings.rotate !== undefined) { document.getElementById('img-rotate').value = imgSettings.rotate; document.getElementById('label-rotate').innerHTML = parseInt(imgSettings.rotate) + ' &deg;'; }
if (imgSettings.offsetX !== undefined) document.getElementById('img-offset-x').value = imgSettings.offsetX;
if (imgSettings.offsetY !== undefined) document.getElementById('img-offset-y').value = imgSettings.offsetY;
if (imgSettings.method) document.getElementById('img-method').value = imgSettings.method;
if (imgSettings.speed) document.getElementById('img-speed').value = imgSettings.speed;
if (imgSettings.drawBBox !== undefined) document.getElementById('img-draw-bbox').checked = imgSettings.drawBBox;

const optSettings = JSON.parse(localStorage.getItem('plotter_opt_settings')) || {};
if (optSettings.minStroke !== undefined && document.getElementById('opt-min-stroke')) {
    document.getElementById('opt-min-stroke').value = optSettings.minStroke;
    document.getElementById('label-min-stroke').innerText = parseFloat(optSettings.minStroke).toFixed(2) + ' mm';
}
if (optSettings.stitchGap !== undefined && document.getElementById('opt-stitch-gap')) {
    document.getElementById('opt-stitch-gap').value = optSettings.stitchGap;
    const v = parseFloat(optSettings.stitchGap);
    document.getElementById('label-stitch-gap').innerText = v === 0 ? '0.00 mm (Safe)' : v.toFixed(2) + ' mm';
    saveOptSettings(); autoPreview();
}
if (optSettings.rdpEps !== undefined && document.getElementById('opt-rdp-eps')) {
    document.getElementById('opt-rdp-eps').value = optSettings.rdpEps;
    document.getElementById('label-rdp-eps').innerText = parseFloat(optSettings.rdpEps).toFixed(3) + ' mm';
}
if (optSettings.levels !== undefined && document.getElementById('opt-contour-levels')) {
    document.getElementById('opt-contour-levels').value = optSettings.levels;
    document.getElementById('label-contour-levels').innerText = optSettings.levels;
}

const txtSettings = JSON.parse(localStorage.getItem('plotter_text_settings')) || {};
if (txtSettings.font) document.getElementById('doc-font').value = txtSettings.font;
if (txtSettings.spacing) document.getElementById('doc-spacing').value = txtSettings.spacing;
if (txtSettings.size) document.getElementById('doc-size').value = txtSettings.size;
if (txtSettings.speed) document.getElementById('doc-speed').value = txtSettings.speed;
if (txtSettings.drawBBox !== undefined) document.getElementById('doc-draw-bbox').checked = txtSettings.drawBBox;
if (txtSettings.autoWrap !== undefined) document.getElementById('doc-auto-wrap').checked = txtSettings.autoWrap;
if (txtSettings.zhop) document.getElementById('global-zhop').value = txtSettings.zhop;

function saveImgSettings() {
    localStorage.setItem('plotter_img_settings', JSON.stringify({
        gap: document.getElementById('img-gap').value, contrast: document.getElementById('img-contrast').value,
        scale: document.getElementById('img-scale').value, rotate: document.getElementById('img-rotate').value,
        offsetX: document.getElementById('img-offset-x').value, offsetY: document.getElementById('img-offset-y').value,
        method: document.getElementById('img-method').value, speed: document.getElementById('img-speed').value,
        drawBBox: document.getElementById('img-draw-bbox').checked
    }));
}

function saveOptSettings() {
    localStorage.setItem('plotter_opt_settings', JSON.stringify({
        minStroke: document.getElementById('opt-min-stroke')?.value,
        stitchGap: document.getElementById('opt-stitch-gap')?.value,
        rdpEps: document.getElementById('opt-rdp-eps')?.value,
        levels: document.getElementById('opt-contour-levels')?.value
    }));
}

function saveTxtSettings() {
    localStorage.setItem('plotter_text_settings', JSON.stringify({
        font: document.getElementById('doc-font').value, spacing: document.getElementById('doc-spacing').value,
        size: document.getElementById('doc-size').value, speed: document.getElementById('doc-speed').value,
        drawBBox: document.getElementById('doc-draw-bbox').checked, autoWrap: document.getElementById('doc-auto-wrap').checked,
        zhop: document.getElementById('global-zhop').value
    }));
}

// =============================================================================
// CONTEXT-SENSITIVE ALGORITHM UI
// =============================================================================
function updateMethodOptionsUI() {
    const methodSelect = document.getElementById('img-method');
    if (!methodSelect) return;
    const method = methodSelect.value;

    const itemGap = document.getElementById('item-img-gap');
    const titleGap = document.getElementById('title-img-gap');
    const itemContrast = document.getElementById('item-img-contrast');
    const titleContrast = document.getElementById('title-img-contrast');
    const itemLevels = document.getElementById('item-img-levels');
    const descText = document.getElementById('method-description-text');

    if (itemGap) itemGap.style.display = 'flex';
    if (itemContrast) itemContrast.style.display = 'flex';
    if (itemLevels) itemLevels.style.display = 'none';

    switch (method) {
        case 'hatch':
            if (descText) descText.innerText = "Classic multi-directional crosshatching. Maps grayscale tone darkness to shading line density.";
            if (titleGap) {
                titleGap.innerText = 'Hatch Line Gap';
                titleGap.setAttribute('data-tooltip', 'Spacing between parallel hatch lines.');
            }
            if (titleContrast) {
                titleContrast.innerText = 'Contrast / Tone Threshold';
                titleContrast.setAttribute('data-tooltip', 'Adjusts tone separation curve across multi-angle hatching passes.');
            }
            break;
        case 'skeleton':
            if (descText) descText.innerText = "Morphological thinning to 1-pixel spines. Eliminates double-outlining on signatures, line art, and handwriting.";
            if (itemGap) itemGap.style.display = 'none';
            if (titleContrast) {
                titleContrast.innerText = 'Binarization Threshold';
                titleContrast.setAttribute('data-tooltip', 'Otsu threshold cutoff distinguishing pen ink strokes from background paper.');
            }
            break;
        case 'spiral':
            if (descText) descText.innerText = "Continuous Archimedean spiral with tone-modulated sine waves. Plots portraits with zero pen lifts.";
            if (titleGap) {
                titleGap.innerText = 'Spiral Pitch / Revolution Spacing';
                titleGap.setAttribute('data-tooltip', 'Radial distance between consecutive spiral revolutions.');
            }
            if (titleContrast) {
                titleContrast.innerText = 'Tone Modulation Sensitivity';
                titleContrast.setAttribute('data-tooltip', 'Controls how aggressively dark pixels bend the spiral line into sine waves.');
            }
            break;
        case 'squiggle':
            if (descText) descText.innerText = "Continuous boustrophedon serpentine raster scanlines with tone-modulated wave amplitude.";
            if (titleGap) {
                titleGap.innerText = 'Wave Gap / Row Height';
                titleGap.setAttribute('data-tooltip', 'Vertical spacing between back-and-forth serpentine scanlines.');
            }
            if (titleContrast) {
                titleContrast.innerText = 'Wave Amplitude Contrast';
                titleContrast.setAttribute('data-tooltip', 'Scales sine-wave oscillation amplitude across darker image areas.');
            }
            break;
        case 'flow_field':
            if (descText) descText.innerText = "Structure Tensor Edge Tangent Flow streamlines. Generates classic copperplate and woodcut engraving aesthetics.";
            if (titleGap) {
                titleGap.innerText = 'Streamline Separation Distance';
                titleGap.setAttribute('data-tooltip', 'Minimum clearance between streamlines.');
            }
            if (titleContrast) {
                titleContrast.innerText = 'Gradient Sensitivity';
                titleContrast.setAttribute('data-tooltip', 'Enhances edge tangent gradients for calculating streamline trajectories.');
            }
            break;
        case 'stipple':
            if (descText) descText.innerText = "Blue-noise tone-weighted dot sampling relaxed via spatial repulsion. Draws distinct micro-dots.";
            if (titleGap) {
                titleGap.innerText = 'Stipple Point Density';
                titleGap.setAttribute('data-tooltip', 'Dot spacing factor. Lower values produce denser stipple shading.');
            }
            if (titleContrast) {
                titleContrast.innerText = 'Tone Weighting Contrast';
                titleContrast.setAttribute('data-tooltip', 'Non-linear tone curve for sampling dot frequency in shadows vs highlights.');
            }
            break;
        case 'tsp':
            if (descText) descText.innerText = "Travelling Salesperson Tour connecting blue-noise stipple nodes into a continuous single-line path.";
            if (titleGap) {
                titleGap.innerText = 'Tour Dot Density';
                titleGap.setAttribute('data-tooltip', 'Density of coordinate nodes visited by the continuous single-line TSP path.');
            }
            if (titleContrast) {
                titleContrast.innerText = 'Tone Weighting Contrast';
                titleContrast.setAttribute('data-tooltip', 'Non-linear tone curve for sampling tour nodes in shadows vs highlights.');
            }
            break;
        case 'contours':
            if (descText) descText.innerText = "Multi-level iso-luminance topographic contour elevation slices across tone depths.";
            if (itemGap) itemGap.style.display = 'none';
            if (titleContrast) {
                titleContrast.innerText = 'Image Pre-Filter Contrast';
                titleContrast.setAttribute('data-tooltip', 'Adjusts tone contrast before contour extraction.');
            }
            if (itemLevels) itemLevels.style.display = 'flex';
            break;
        case 'canny':
            if (descText) descText.innerText = "Canny hysteresis gradient filter for tracing high-contrast vector outlines.";
            if (itemGap) itemGap.style.display = 'none';
            if (titleContrast) {
                titleContrast.innerText = 'Edge Detection Sensitivity';
                titleContrast.setAttribute('data-tooltip', 'Adjusts Canny hysteresis threshold sensitivity.');
            }
            break;
    }
}
updateMethodOptionsUI();

function autoPreview() {
    if (currentMode === 'image' && !base64Image) return;
    if (bboxPoints.length !== 4) return;
    clearTimeout(previewTimeout);
    previewTimeout = setTimeout(() => triggerPreview(), 300);
}

document.getElementById('doc-font').addEventListener('change', () => { saveTxtSettings(); autoPreview(); });
document.getElementById('doc-spacing').addEventListener('input', () => { saveTxtSettings(); autoPreview(); });
document.getElementById('doc-text').addEventListener('input', () => { autoPreview(); });
document.getElementById('doc-size').addEventListener('input', () => { saveTxtSettings(); autoPreview(); });
document.getElementById('doc-speed').addEventListener('change', saveTxtSettings);
document.getElementById('doc-draw-bbox').addEventListener('change', () => { saveTxtSettings(); autoPreview(); });
document.getElementById('doc-auto-wrap').addEventListener('change', () => { saveTxtSettings(); autoPreview(); });
document.getElementById('global-zhop').addEventListener('input', saveTxtSettings);

document.getElementById('img-input').addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (event) => {
        base64Image = event.target.result;
        document.getElementById('upload-box').style.display = 'none';
        document.getElementById('preview-wrapper').style.display = 'flex';
        document.getElementById('preview-2d').style.display = 'block';
        autoPreview();
    };
    reader.readAsDataURL(file);
});

window.clearImage = function() {
    base64Image = null;
    cachedPreviewData = null;
    document.getElementById('preview-wrapper').style.display = 'none';
    document.getElementById('upload-box').style.display = 'flex';
    document.getElementById('img-input').value = '';
    visualizer.clearPaths();
    document.getElementById('btn-plot').disabled = true;
    document.getElementById('btn-download').disabled = true;
};

document.getElementById('img-gap').addEventListener('input', (e) => { document.getElementById('label-gap').innerText = parseFloat(e.target.value).toFixed(1) + ' mm'; saveImgSettings(); autoPreview(); });
document.getElementById('img-contrast').addEventListener('input', (e) => { document.getElementById('label-contrast').innerText = parseFloat(e.target.value).toFixed(1) + ' x'; saveImgSettings(); autoPreview(); });
document.getElementById('img-scale').addEventListener('input', (e) => { document.getElementById('label-scale').innerText = parseInt(e.target.value) + ' %'; saveImgSettings(); autoPreview(); });
document.getElementById('img-rotate').addEventListener('input', (e) => { document.getElementById('label-rotate').innerHTML = parseInt(e.target.value) + ' &deg;'; saveImgSettings(); autoPreview(); });
document.getElementById('img-offset-x').addEventListener('input', () => { saveImgSettings(); autoPreview(); });
document.getElementById('img-offset-y').addEventListener('input', () => { saveImgSettings(); autoPreview(); });
document.getElementById('img-method').addEventListener('change', () => { updateMethodOptionsUI(); saveImgSettings(); autoPreview(); });
document.getElementById('img-speed').addEventListener('change', () => saveImgSettings());
document.getElementById('img-draw-bbox').addEventListener('change', () => { saveImgSettings(); autoPreview(); });

// Optimizer listeners
const optMinStroke = document.getElementById('opt-min-stroke');
if (optMinStroke) optMinStroke.addEventListener('input', (e) => { document.getElementById('label-min-stroke').innerText = parseFloat(e.target.value).toFixed(2) + ' mm'; saveOptSettings(); autoPreview(); });
const optStitchGap = document.getElementById('opt-stitch-gap');
if (optStitchGap) optStitchGap.addEventListener('input', (e) => {
    const v = parseFloat(e.target.value);
    document.getElementById('label-stitch-gap').innerText = v === 0 ? '0.00 mm (Safe)' : v.toFixed(2) + ' mm';
    saveOptSettings(); autoPreview();
});
const optRdpEps = document.getElementById('opt-rdp-eps');
if (optRdpEps) optRdpEps.addEventListener('input', (e) => { document.getElementById('label-rdp-eps').innerText = parseFloat(e.target.value).toFixed(3) + ' mm'; saveOptSettings(); autoPreview(); });
const optContourLevels = document.getElementById('opt-contour-levels');
if (optContourLevels) optContourLevels.addEventListener('input', (e) => { document.getElementById('label-contour-levels').innerText = e.target.value; saveOptSettings(); autoPreview(); });

document.getElementById('vis-zoom').addEventListener('input', (e) => visualizer.setZoom(parseFloat(e.target.value)));

const warningBanner = document.getElementById('warning-banner');
function showWarning(msg) { warningBanner.innerText = msg; warningBanner.style.display = 'block'; setTimeout(() => warningBanner.style.display = 'none', 6000); }

document.querySelectorAll('.quick-step').forEach(btn => btn.addEventListener('click', (e) => document.getElementById('step-input').value = e.target.dataset.val));

// =============================================================================
// PATH GENERATION PAYLOAD BUILDER
// =============================================================================
function getPayload() {
    if (isWebMode) {
        if (bboxPoints.length !== 4) updateBBoxFromStaticInputs();
        const rawZ = parseFloat(document.getElementById('toolhead-z')?.value);
        const toolheadZ = Math.max(0, Math.min(BED_SIZE, !isNaN(rawZ) ? rawZ : 30.0));
        var bbox = {
            min_x: Math.min(...bboxPoints.map(p => p.x)),
            max_x: Math.max(...bboxPoints.map(p => p.x)),
            min_y: Math.min(...bboxPoints.map(p => p.y)),
            max_y: Math.max(...bboxPoints.map(p => p.y)),
            origin_z: toolheadZ
        };
    } else {
        if (bboxPoints.length !== 4) return null;
        const rawOriginZ = bboxPoints[0]?.z;
        const validOriginZ = Math.max(0, Math.min(BED_SIZE, !isNaN(rawOriginZ) ? rawOriginZ : 0.0));
        var bbox = {
            min_x: Math.min(...bboxPoints.map(p => p.x)),
            max_x: Math.max(...bboxPoints.map(p => p.x)),
            min_y: Math.min(...bboxPoints.map(p => p.y)),
            max_y: Math.max(...bboxPoints.map(p => p.y)),
            origin_z: validOriginZ
        };
    }

    const draw_bbox = currentMode === 'text' ? document.getElementById('doc-draw-bbox').checked : document.getElementById('img-draw-bbox').checked;
    const z_hop = document.getElementById('global-zhop').value;

    const minStroke = parseFloat(document.getElementById('opt-min-stroke')?.value || '0.02');
    const stitchGap = parseFloat(document.getElementById('opt-stitch-gap')?.value || '0.0');
    const rdpEps = parseFloat(document.getElementById('opt-rdp-eps')?.value || '0.008');
    const levels = parseInt(document.getElementById('opt-contour-levels')?.value || '6');

    if (currentMode === 'text') {
        return {
            type: 'text', text: document.getElementById('doc-text').value, bbox: bbox, draw_bbox: draw_bbox,
            auto_wrap: document.getElementById('doc-auto-wrap').checked, font: document.getElementById('doc-font').value,
            line_spacing: document.getElementById('doc-spacing').value, font_size: document.getElementById('doc-size').value,
            speed: document.getElementById('doc-speed').value, z_hop: z_hop, bed_size: BED_SIZE,
            pen_width: currentPenWidth, min_stroke_length: minStroke, stitch_gap: stitchGap, rdp_epsilon: rdpEps
        };
    } else {
        if (!base64Image) return null;
        return {
            type: 'image', image: base64Image, bbox: bbox, draw_bbox: draw_bbox, method: document.getElementById('img-method').value,
            img_gap: document.getElementById('img-gap').value, img_contrast: document.getElementById('img-contrast').value,
            img_scale: document.getElementById('img-scale').value, img_rotate: document.getElementById('img-rotate').value,
            img_offset_x: document.getElementById('img-offset-x').value, img_offset_y: document.getElementById('img-offset-y').value,
            speed: document.getElementById('img-speed').value, z_hop: z_hop, bed_size: BED_SIZE,
            pen_width: currentPenWidth, min_stroke_length: minStroke, stitch_gap: stitchGap, rdp_epsilon: rdpEps, levels: levels
        };
    }
}

// =============================================================================
// 2D CANVAS PREVIEW
// =============================================================================
function renderCanvas2D(data) {
    const canvas = document.getElementById('preview-2d');
    if (!canvas || !canvas.parentElement) return;
    canvas.width = canvas.parentElement.clientWidth;
    canvas.height = canvas.width;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    const allPaths = (data.paths || []).concat(data.out_paths || []);
    allPaths.forEach(poly => {
        if (!poly) return;
        poly.forEach(pt => {
            if (pt.x < minX) minX = pt.x;
            if (pt.x > maxX) maxX = pt.x;
            if (pt.y < minY) minY = pt.y;
            if (pt.y > maxY) maxY = pt.y;
        });
    });

    const w = maxX - minX, h = maxY - minY;
    if (w > 0 && h > 0) {
        const scale = Math.min(canvas.width / w, canvas.height / h) * 0.9;
        ctx.save();
        ctx.translate(canvas.width / 2 - (w / 2) * scale, canvas.height / 2 - (h / 2) * scale);

        const strokePx = Math.max(0.4, currentPenWidth * scale);
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';

        const drawLines = (pathList, colorStyle) => {
            if (!pathList) return;
            ctx.beginPath();
            pathList.forEach(poly => {
                if (!poly || poly.length < 2) return;
                ctx.moveTo((poly[0].x - minX) * scale, (maxY - poly[0].y) * scale);
                for (let k = 1; k < poly.length; k++) {
                    ctx.lineTo((poly[k].x - minX) * scale, (maxY - poly[k].y) * scale);
                }
            });
            ctx.strokeStyle = colorStyle;
            ctx.lineWidth = strokePx;
            ctx.stroke();
        };

        const isDarkMode = document.body.classList.contains('dark-mode');
        drawLines(data.paths, isDarkMode ? '#4fd8eb' : '#006874');
        drawLines(data.out_paths, '#ff0000');
        ctx.restore();
    }
}

// =============================================================================
// PREVIEW EXECUTION
// =============================================================================
async function triggerPreview() {
    if (isPreviewing) { pendingPreview = true; return; }
    const payload = getPayload();
    if (!payload) {
        if (!isWebMode && bboxPoints.length !== 4) showWarning("Set 4-point Bounding Box first!");
        return;
    }

    isPreviewing = true;
    const loader = document.getElementById('engine-loader');
    const loaderText = document.getElementById('engine-loader-text');
    if (loader) {
        loader.style.display = 'flex';
        if (loaderText) loaderText.innerText = "Generating vector toolpaths...";
    }

    try {
        let data = null;

        if (!isWebMode) {
            try {
                const res = await fetch('/api/preview', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
                if (res.ok) {
                    data = await res.json();
                } else {
                    const errData = await res.json().catch(() => ({}));
                    showWarning(errData.message || `Preview generation failed (HTTP ${res.status})`);
                    return;
                }
            } catch (e) {
                // Local server offline -> gracefully fall back to in-browser worker
            }
        }

        if (!data) {
            data = await callWorker('generate_preview', payload);
        }

        if (data && data.status === 'success') {
            cachedPreviewData = data;
            const isDarkMode = document.body.classList.contains('dark-mode');
            visualizer.drawPreview(data.paths, data.out_paths, data.origin_z, BED_SIZE, isDarkMode);

            if (currentMode === 'image') {
                renderCanvas2D(data);
            }
            document.getElementById('btn-download').disabled = false;
            if (!isWebMode) document.getElementById('btn-plot').disabled = false;
        } else if (data && data.message) {
            showWarning(data.message);
        }
    } catch (err) {
        showWarning(err.message || "Vector preview failed.");
    } finally {
        if (loader && !pendingPreview) loader.style.display = 'none';
        isPreviewing = false;
        if (pendingPreview) {
            pendingPreview = false;
            triggerPreview();
        }
    }
}

function showHomeErrorPopup(msg) {
    const modal = document.getElementById('home-error-modal');
    if (modal) modal.style.display = 'flex';
    else alert(msg || "Home the printer first before starting a direct plot!");
}

document.getElementById('btn-plot').addEventListener('click', () => {
    if (!isHomed) {
        showHomeErrorPopup("Home the printer first before starting a direct plot!");
        return;
    }
    if(!confirm("Ready to draw? Ensure pen is lowered and paper is secure!")) return;
    const modal = document.getElementById('plot-method-modal');
    modal.style.display = 'flex';
    setTimeout(() => document.getElementById('btn-sd-plot').focus(), 50);
});

window.closePlotModal = function() {
    document.getElementById('plot-method-modal').style.display = 'none';
    document.getElementById('btn-plot').focus();
};

function trapFocus(modal, e) {
    const focusable = modal.querySelectorAll('md-filled-button, md-filled-tonal-button, md-outlined-button');
    if (focusable.length === 0) return;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (e.key === 'Tab') {
        if (e.shiftKey && document.activeElement === first) {
            last.focus(); e.preventDefault();
        } else if (!e.shiftKey && document.activeElement === last) {
            first.focus(); e.preventDefault();
        }
    }
}

document.getElementById('plot-method-modal').addEventListener('keydown', (e) => {
    if (e.key === 'Escape') window.closePlotModal();
    else trapFocus(document.getElementById('plot-method-modal'), e);
});

document.getElementById('download-modal').addEventListener('keydown', (e) => {
    if (e.key === 'Escape') window.closeDownloadModal();
    else trapFocus(document.getElementById('download-modal'), e);
});

document.getElementById('model-modal').addEventListener('keydown', (e) => {
    trapFocus(document.getElementById('model-modal'), e);
});

window.downloadGCode = function() {
    const payload = getPayload();
    if (!payload) return;
    document.getElementById('download-modal').style.display = 'flex';
    setTimeout(() => document.querySelector('#download-modal md-filled-button').focus(), 50);
};

window.closeDownloadModal = function() {
    document.getElementById('download-modal').style.display = 'none';
    document.getElementById('btn-download').focus();
};

window.confirmDownloadGCode = async function() {
    const payload = getPayload();
    if (!payload) return;

    document.getElementById('download-modal').style.display = 'none';

    const btnDownload = document.getElementById('btn-download');
    const originalText = btnDownload.innerHTML;
    btnDownload.innerHTML = `<md-icon slot="icon">hourglass_empty</md-icon> Compiling G-Code...`;
    btnDownload.disabled = true;

    try {
        let blob = null;

        if (!isWebMode) {
            try {
                const response = await fetch('/api/download_gcode', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                if (response.ok) {
                    blob = await response.blob();
                } else {
                    const errData = await response.json().catch(() => ({}));
                    showWarning(errData.message || `Download failed (HTTP ${response.status})`);
                    btnDownload.innerHTML = originalText;
                    btnDownload.disabled = false;
                    return;
                }
            } catch (e) {
                // Local server offline -> fallback to worker
            }
        }

        if (!blob) {
            const res = await callWorker('generate_gcode', payload);
            if (res && res.status === 'success') {
                blob = new Blob([res.gcode], { type: 'text/plain;charset=utf-8' });
            } else {
                showWarning((res && res.message) || "G-code compile failed.");
                btnDownload.innerHTML = originalText;
                btnDownload.disabled = false;
                return;
            }
        }

        if (blob) {
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            a.download = 'bambuscribe_plot.gcode';
            document.body.appendChild(a);
            a.click();
            setTimeout(() => {
                a.remove();
                window.URL.revokeObjectURL(url);
            }, 1000);
        }
    } catch (e) {
        showWarning("Download Error: " + e.message);
    }

    btnDownload.innerHTML = originalText;
    btnDownload.disabled = false;
};

window.startPlot = async function(method) {
    document.getElementById('plot-method-modal').style.display = 'none';
    document.getElementById('btn-plot').style.display = 'none';
    document.getElementById('btn-download').style.display = 'none';
    document.getElementById('btn-estop').style.display = 'inline-flex';

    try {
        const res = await fetch(method === 'stream' ? '/api/plot' : '/api/plot_sd', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(getPayload()) });
        const data = await res.json();
        if (data.status !== 'success') {
            if (data.message && data.message.includes("Home the printer first")) {
                showHomeErrorPopup(data.message);
            } else {
                showWarning(data.message);
            }
            resetPlottingUI();
        }
    } catch(e) { showWarning("Error: " + e.message); resetPlottingUI(); }
};

document.getElementById('btn-pause').addEventListener('click', async () => await fetch('/api/pause', { method: 'POST' }));
document.getElementById('btn-resume').addEventListener('click', async () => await fetch('/api/resume', { method: 'POST' }));
document.getElementById('btn-estop').addEventListener('click', async () => {
    if(!confirm("Are you sure you want to completely STOP the current plot?")) return;
    await fetch('/api/stop', { method: 'POST' });
    resetPlottingUI();
});

function resetPlottingUI() {
    document.getElementById('btn-pause').style.display = 'none';
    document.getElementById('btn-resume').style.display = 'none';
    document.getElementById('btn-estop').style.display = 'none';
    document.getElementById('btn-download').style.display = 'inline-flex';
    document.getElementById('btn-plot').style.display = 'inline-flex';
    document.getElementById('progress-container').style.display = 'none';
    document.getElementById('progress-bar').style.background = 'var(--md-sys-color-primary)';
}

// Printer Status Polling Interval
setInterval(async () => {
    if (isWebMode) return;
    try {
        const res = await fetch('/api/state');
        if (!res.ok) return;
        const data = await res.json();
        if (data.is_homed && !isHomed) {
            isHomed = true;
            document.getElementById('movement-controls').style.opacity = '1';
            document.getElementById('movement-controls').style.pointerEvents = 'auto';
            currentPos = data.position;
            visualizer.updateToolhead(currentPos.x, currentPos.y, currentPos.z, BED_SIZE);
            updateCoordDisplay(currentPos.x, currentPos.y, currentPos.z);
            predictedPos = {...data.position};
        } else if (!data.is_homed && isHomed) {
            isHomed = false;
            document.getElementById('movement-controls').style.opacity = '0.5';
            document.getElementById('movement-controls').style.pointerEvents = 'none';
        }
        if (data.is_homed) {
            if (!isProcessingQueue && manualQueue.length === 0) {
                currentPos = data.position;
                visualizer.updateToolhead(currentPos.x, currentPos.y, currentPos.z, BED_SIZE);
                updateCoordDisplay(currentPos.x, currentPos.y, currentPos.z);
            }
            if (data.status !== "Idle") {
                document.getElementById('progress-container').style.display = 'flex';
                document.getElementById('progress-bar').style.width = data.progress + '%';
                document.getElementById('btn-estop').style.display = 'inline-flex';
                if (data.status === "Failed") {
                    document.getElementById('progress-text').innerText = "Print Failed! Check printer.";
                    document.getElementById('progress-bar').style.background = '#ba1a1a';
                    document.getElementById('btn-pause').style.display = 'none';
                    document.getElementById('btn-resume').style.display = 'none';
                } else if (data.status === "Uploading" || data.status === "Starting Print") {
                    document.getElementById('progress-text').innerText = data.status === "Uploading" ? `Uploading to SD... ${data.progress}%` : `Starting print on hardware...`;
                    document.getElementById('btn-pause').style.display = 'none';
                    document.getElementById('btn-resume').style.display = 'none';
                } else {
                    document.getElementById('progress-text').innerText = data.status === "Printing SD" ? `Autonomous SD Print... ${data.progress}%` : `Streaming Plot Over WiFi... ${data.progress}%`;
                    document.getElementById('btn-pause').style.display = data.is_paused ? 'none' : 'inline-flex';
                    document.getElementById('btn-resume').style.display = data.is_paused ? 'inline-flex' : 'none';
                }
            } else {
                document.getElementById('progress-container').style.display = 'none';
                if (document.getElementById('btn-plot').style.display === 'none') resetPlottingUI();
            }
        }
    } catch(e) {}
}, 1000);

if (document.getElementById('model-modal').style.display !== 'none') {
    setTimeout(() => document.querySelector('#model-modal md-filled-button').focus(), 50);
}
window.setTab(currentMode);