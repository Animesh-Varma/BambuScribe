import { PlotterVisualizer } from './visualizer.js';

let BED_SIZE = 180;
let currentMode = 'text';
let base64Image = null;
let previewTimeout = null;
let isHomed = false;
let isPreviewing = false;
let pendingPreview = false;

let currentPos = {x: 0, y: 0, z: 0};
let bboxPoints = JSON.parse(localStorage.getItem('plotter_bbox_points')) || [];
let manualQueue = [];
let predictedPos = {x: 0, y: 0, z: 0};
let isProcessingQueue = false;

const visualizer = new PlotterVisualizer('canvas-container');

window.selectModel = function(size) {
    BED_SIZE = size;
    document.getElementById('model-modal').style.display = 'none';
    visualizer.initScene(BED_SIZE);
    visualizer.updateToolhead(currentPos.x, currentPos.y, currentPos.z, BED_SIZE);

    if (bboxPoints.length === 4) {
        visualizer.updateBBoxDots(bboxPoints, BED_SIZE);
        renderBBoxList();
        autoPreview();
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
    if (bboxPoints.length === 4) triggerPreview();
});

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

    const btnOrigin = document.getElementById('btn-origin');
    if (bboxPoints.length === 4) {
        btnOrigin.innerHTML = `<md-icon slot="icon">clear</md-icon> Clear BBox`;
    } else {
        btnOrigin.innerHTML = `<md-icon slot="icon">crop_free</md-icon> Set BBox (${bboxPoints.length}/4)`;
    }
};

// UI Persistence configuration mapping via LocalStorage
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

function saveTxtSettings() {
    localStorage.setItem('plotter_text_settings', JSON.stringify({
        font: document.getElementById('doc-font').value, spacing: document.getElementById('doc-spacing').value,
        size: document.getElementById('doc-size').value, speed: document.getElementById('doc-speed').value,
        drawBBox: document.getElementById('doc-draw-bbox').checked, autoWrap: document.getElementById('doc-auto-wrap').checked,
        zhop: document.getElementById('global-zhop').value
    }));
}

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
    document.getElementById('preview-wrapper').style.display = 'none';
    document.getElementById('upload-box').style.display = 'flex';
    document.getElementById('img-input').value = '';
    visualizer.clearPaths();
    document.getElementById('btn-plot').disabled = true;
}

document.getElementById('img-gap').addEventListener('input', (e) => { document.getElementById('label-gap').innerText = parseFloat(e.target.value).toFixed(1) + ' mm'; saveImgSettings(); autoPreview(); });
document.getElementById('img-contrast').addEventListener('input', (e) => { document.getElementById('label-contrast').innerText = parseFloat(e.target.value).toFixed(1) + ' x'; saveImgSettings(); autoPreview(); });
document.getElementById('img-scale').addEventListener('input', (e) => { document.getElementById('label-scale').innerText = parseInt(e.target.value) + ' %'; saveImgSettings(); autoPreview(); });
document.getElementById('img-rotate').addEventListener('input', (e) => { document.getElementById('label-rotate').innerHTML = parseInt(e.target.value) + ' &deg;'; saveImgSettings(); autoPreview(); });
document.getElementById('img-offset-x').addEventListener('input', () => { saveImgSettings(); autoPreview(); });
document.getElementById('img-offset-y').addEventListener('input', () => { saveImgSettings(); autoPreview(); });
document.getElementById('img-method').addEventListener('change', () => { saveImgSettings(); autoPreview(); });
document.getElementById('img-speed').addEventListener('change', () => saveImgSettings());
document.getElementById('img-draw-bbox').addEventListener('change', () => { saveImgSettings(); autoPreview(); });

document.getElementById('vis-zoom').addEventListener('input', (e) => visualizer.setZoom(parseFloat(e.target.value)));

const warningBanner = document.getElementById('warning-banner');
function showWarning(msg) { warningBanner.innerText = msg; warningBanner.style.display = 'block'; setTimeout(() => warningBanner.style.display = 'none', 6000); }

document.querySelectorAll('.quick-step').forEach(btn => btn.addEventListener('click', (e) => document.getElementById('step-input').value = e.target.dataset.val));

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
            document.getElementById('coord-display').innerText = `X: ${currentPos.x.toFixed(1)} | Y: ${currentPos.y.toFixed(1)} | Z: ${currentPos.z.toFixed(1)}`;
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
                document.getElementById('coord-display').innerText = `X: ${currentPos.x.toFixed(1)} | Y: ${currentPos.y.toFixed(1)} | Z: ${currentPos.z.toFixed(1)}`;
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
        localStorage.removeItem('plotter_bbox_points');
        document.getElementById('btn-origin').innerHTML = `<md-icon slot="icon">crop_free</md-icon> Set BBox (0/4)`;
        visualizer.updateBBoxDots(bboxPoints, BED_SIZE);
        renderBBoxList();
        visualizer.clearPaths();
        document.getElementById('btn-plot').disabled = true;
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
        label.innerHTML = `<strong>P${i+1}</strong> <span style="color:#888; font-family:monospace; margin-left:8px;">X${pt.x.toFixed(1)} Y${pt.y.toFixed(1)}</span>`;

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
            document.getElementById('coord-display').innerText = `X: ${currentPos.x.toFixed(1)} | Y: ${currentPos.y.toFixed(1)} | Z: ${currentPos.z.toFixed(1)}`;
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

function getPayload() {
    if (bboxPoints.length !== 4) return null;
    let bbox = {
        min_x: Math.min(...bboxPoints.map(p => p.x)), max_x: Math.max(...bboxPoints.map(p => p.x)),
        min_y: Math.min(...bboxPoints.map(p => p.y)), max_y: Math.max(...bboxPoints.map(p => p.y)),
        origin_z: bboxPoints[0].z
    };
    const draw_bbox = currentMode === 'text' ? document.getElementById('doc-draw-bbox').checked : document.getElementById('img-draw-bbox').checked;
    const z_hop = document.getElementById('global-zhop').value;

    if (currentMode === 'text') {
        return {
            type: 'text', text: document.getElementById('doc-text').value, bbox: bbox, draw_bbox: draw_bbox,
            auto_wrap: document.getElementById('doc-auto-wrap').checked, font: document.getElementById('doc-font').value,
            line_spacing: document.getElementById('doc-spacing').value, font_size: document.getElementById('doc-size').value,
            speed: document.getElementById('doc-speed').value, z_hop: z_hop, bed_size: BED_SIZE
        };
    } else {
        if (!base64Image) return null;
        return {
            type: 'image', image: base64Image, bbox: bbox, draw_bbox: draw_bbox, method: document.getElementById('img-method').value,
            img_gap: document.getElementById('img-gap').value, img_contrast: document.getElementById('img-contrast').value,
            img_scale: document.getElementById('img-scale').value, img_rotate: document.getElementById('img-rotate').value,
            img_offset_x: document.getElementById('img-offset-x').value, img_offset_y: document.getElementById('img-offset-y').value,
            speed: document.getElementById('img-speed').value, z_hop: z_hop, bed_size: BED_SIZE
        };
    }
}

async function triggerPreview() {
    if (isPreviewing) { pendingPreview = true; return; }
    const payload = getPayload();
    if(!payload) { if (bboxPoints.length !== 4) showWarning("Set 4-point Bounding Box first!"); return; }

    isPreviewing = true;
    const btnPreview = document.getElementById('btn-preview');
    const originalText = btnPreview.innerHTML;
    btnPreview.innerHTML = `<md-icon slot="icon">sync</md-icon> Calculating...`;

    try {
        const res = await fetch('/api/preview', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
        const data = await res.json();
        if (data.status === 'success') {
            const isDarkMode = document.body.classList.contains('dark-mode');
            visualizer.drawPreview(data.paths, data.out_paths, data.origin_z, BED_SIZE, isDarkMode);

            if (currentMode === 'image') {
                const canvas = document.getElementById('preview-2d');
                canvas.width = canvas.parentElement.clientWidth;
                canvas.height = canvas.width;
                const ctx = canvas.getContext('2d');
                ctx.clearRect(0, 0, canvas.width, canvas.height);

                let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
                const allPaths = (data.paths || []).concat(data.out_paths || []);
                allPaths.forEach(segment => {
                    minX = Math.min(minX, segment[0].x, segment[1].x);
                    maxX = Math.max(maxX, segment[0].x, segment[1].x);
                    minY = Math.min(minY, segment[0].y, segment[1].y);
                    maxY = Math.max(maxY, segment[0].y, segment[1].y);
                });

                const w = maxX - minX, h = maxY - minY;

                if(w > 0 && h > 0) {
                    const scale = Math.min(canvas.width / w, canvas.height / h) * 0.9;
                    ctx.save();
                    ctx.translate(canvas.width/2 - (w/2)*scale, canvas.height/2 - (h/2)*scale);
                    ctx.lineWidth = 0.5;

                    const drawLines = (pathList, colorStyle) => {
                        if (!pathList) return;
                        ctx.beginPath();
                        pathList.forEach(s => {
                            ctx.moveTo((s[0].x - minX) * scale, (maxY - s[0].y) * scale);
                            ctx.lineTo((s[1].x - minX) * scale, (maxY - s[1].y) * scale);
                        });
                        ctx.strokeStyle = colorStyle;
                        ctx.stroke();
                    };
                    drawLines(data.paths, isDarkMode ? '#4fd8eb' : '#006874');
                    drawLines(data.out_paths, '#ff0000');
                    ctx.restore();
                }
            }
            document.getElementById('btn-plot').disabled = false;
        } else showWarning(data.message);
    } catch (err) {}

    btnPreview.innerHTML = originalText;
    isPreviewing = false;
    if (pendingPreview) { pendingPreview = false; triggerPreview(); }
}

document.getElementById('btn-preview').addEventListener('click', triggerPreview);

document.getElementById('btn-plot').addEventListener('click', () => {
    if(!confirm("Ready to draw? Ensure pen is lowered and paper is secure!")) return;
    const modal = document.getElementById('plot-method-modal');
    modal.style.display = 'flex';
    setTimeout(() => document.getElementById('btn-sd-plot').focus(), 50);
});

window.closePlotModal = function() {
    document.getElementById('plot-method-modal').style.display = 'none';
    document.getElementById('btn-plot').focus();
}

function trapFocus(modal, e) {
    const focusable = modal.querySelectorAll('md-filled-button, md-filled-tonal-button, md-outlined-button');
    if (focusable.length === 0) return;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (e.key === 'Tab') {
        if (e.shiftKey && document.activeElement === first) {
            last.focus();
            e.preventDefault();
        } else if (!e.shiftKey && document.activeElement === last) {
            first.focus();
            e.preventDefault();
        }
    }
}

document.getElementById('plot-method-modal').addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        window.closePlotModal();
    } else {
        trapFocus(document.getElementById('plot-method-modal'), e);
    }
});

document.getElementById('model-modal').addEventListener('keydown', (e) => {
    trapFocus(document.getElementById('model-modal'), e);
});

window.startPlot = async function(method) {
    document.getElementById('plot-method-modal').style.display = 'none';
    document.getElementById('btn-plot').style.display = 'none';
    document.getElementById('btn-preview').style.display = 'none';
    document.getElementById('btn-estop').style.display = 'inline-flex';

    try {
        const res = await fetch(method === 'stream' ? '/api/plot' : '/api/plot_sd', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(getPayload()) });
        const data = await res.json();
        if (data.status !== 'success') { showWarning(data.message); resetPlottingUI(); }
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
    document.getElementById('btn-preview').style.display = 'inline-flex';
    document.getElementById('btn-plot').style.display = 'inline-flex';
    document.getElementById('progress-container').style.display = 'none';
    document.getElementById('progress-bar').style.background = 'var(--md-sys-color-primary)';
}

setInterval(async () => {
    try {
        const res = await fetch('/api/state');
        const data = await res.json();
        if (data.is_homed && !isHomed) {
            isHomed = true;
            document.getElementById('movement-controls').style.opacity = '1';
            document.getElementById('movement-controls').style.pointerEvents = 'auto';
            currentPos = data.position;
            visualizer.updateToolhead(currentPos.x, currentPos.y, currentPos.z, BED_SIZE);
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

if (document.getElementById('model-modal').style.display !== 'none') setTimeout(() => document.querySelector('#model-modal md-filled-button').focus(), 50);
visualizer.updateBBoxDots(bboxPoints, BED_SIZE);
renderBBoxList();
window.setTab(currentMode);
if (bboxPoints.length === 4) setTimeout(autoPreview, 200);