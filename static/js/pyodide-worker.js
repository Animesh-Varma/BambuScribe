/**
 * BambuScribe Web Worker - Client-Side Pyodide Python Engine
 * Enables serverless plotting vectorization and G-code generation
 * on static environments.
 */

importScripts("https://cdn.jsdelivr.net/pyodide/v0.26.2/full/pyodide.js");

let pyodide = null;
let isReady = false;
let initPromise = null;

async function initPyodideWorker() {
    if (initPromise) return initPromise;

    initPromise = (async () => {
        try {
            self.postMessage({ status: 'loading', message: 'Initializing WebAssembly Python engine...' });

            pyodide = await loadPyodide({
                indexURL: "https://cdn.jsdelivr.net/pyodide/v0.26.2/full/"
            });

            self.postMessage({ status: 'loading', message: 'Loading scientific packages (NumPy, OpenCV, Pillow)...' });
            await pyodide.loadPackage(['numpy', 'pillow', 'opencv-python', 'micropip']);

            self.postMessage({ status: 'loading', message: 'Installing stroke vector font libraries...' });
            const micropip = pyodide.pyimport('micropip');
            await micropip.install('Hershey-Fonts');

            self.postMessage({ status: 'loading', message: 'Fetching vector plotting algorithms...' });

            const candidateUrls = [
                new URL('../../backend/algorithms.py', self.location.href).href,
                new URL('/backend/algorithms.py', self.location.origin).href,
                new URL('../backend/algorithms.py', self.location.href).href,
                '../../backend/algorithms.py',
                '/backend/algorithms.py',
                './backend/algorithms.py'
            ];

            let algoCode = null;

            for (const url of candidateUrls) {
                try {
                    const res = await fetch(url);
                    if (res.ok) {
                        const text = await res.text();
                        if (text && !text.trim().startsWith('<!DOCTYPE') && !text.trim().startsWith('<html')) {
                            algoCode = text;
                            break;
                        }
                    }
                } catch (e) {}
            }

            if (!algoCode) {
                throw new Error('Could not locate backend/algorithms.py. Please verify repository file tree.');
            }

            // Execute algorithms.py in the Pyodide global scope
            await pyodide.runPythonAsync(algoCode);

            // Set up Python bridge functions
            await pyodide.runPythonAsync(`
import json

def py_process_paths(payload_json_str):
    data = json.loads(payload_json_str)
    paths, out_paths, msg = process_paths_request(data)
    if paths is None:
        return json.dumps({"status": "error", "message": msg})
    return json.dumps({
        "status": "success",
        "paths": paths,
        "out_paths": out_paths,
        "origin_z": data.get('bbox', {}).get('origin_z', 0.0)
    })

def py_generate_gcode(payload_json_str):
    data = json.loads(payload_json_str)
    paths, out_paths, msg = process_paths_request(data)
    if paths is None:
        return json.dumps({"status": "error", "message": msg})

    speed = float(data.get('speed', 12000))
    z_hop = float(data.get('z_hop', 4.0))
    bed_size = float(data.get('bed_size', 180.0))
    origin_z = float(data.get('bbox', {}).get('origin_z', 0.0))

    gcode = generate_full_gcode(paths, origin_z, speed, z_hop, bed_size, is_download=True)
    return json.dumps({"status": "success", "gcode": gcode})
            `);

            isReady = true;
            self.postMessage({ status: 'ready', message: 'Pyodide engine ready.' });
        } catch (err) {
            console.error('[Pyodide Worker Error]:', err);
            self.postMessage({ status: 'error', message: err.message || err.toString() });
            throw err;
        }
    })();

    return initPromise;
}

// Only initialize when an actual message/request is received
self.onmessage = async (event) => {
    const { action, id, data } = event.data;

    try {
        await initPyodideWorker();

        if (action === 'generate_preview') {
            const jsonStr = JSON.stringify(data);
            const resJson = await pyodide.runPythonAsync(`py_process_paths(${JSON.stringify(jsonStr)})`);
            const parsed = JSON.parse(resJson);
            self.postMessage({ id, ...parsed });
        } else if (action === 'generate_gcode') {
            const jsonStr = JSON.stringify(data);
            const resJson = await pyodide.runPythonAsync(`py_generate_gcode(${JSON.stringify(jsonStr)})`);
            const parsed = JSON.parse(resJson);
            self.postMessage({ id, ...parsed });
        } else if (action === 'ping') {
            self.postMessage({ id, status: 'ready' });
        } else {
            self.postMessage({ id, status: 'error', message: `Unknown action: ${action}` });
        }
    } catch (err) {
        console.error(`[Pyodide Worker] Error processing ${action}:`, err);
        self.postMessage({
            id,
            status: 'error',
            message: err.message || 'Error occurred during in-browser vector processing.'
        });
    }
};