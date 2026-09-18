import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

let LineSegments2, LineSegmentsGeometry, LineMaterial;
try {
    const linesMod = await import('three/addons/lines/LineSegments2.js');
    const geomMod = await import('three/addons/lines/LineSegmentsGeometry.js');
    const matMod = await import('three/addons/lines/LineMaterial.js');
    LineSegments2 = linesMod.LineSegments2;
    LineSegmentsGeometry = geomMod.LineSegmentsGeometry;
    LineMaterial = matMod.LineMaterial;
} catch (e) {
    LineSegments2 = null;
}

export class PlotterVisualizer {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.scene = new THREE.Scene();
        this.camera = new THREE.PerspectiveCamera(45, this.container.clientWidth / this.container.clientHeight, 0.1, 1000);
        this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        this.renderer.setSize(this.container.clientWidth, this.container.clientHeight);
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        this.container.appendChild(this.renderer.domElement);

        this.controls = new OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enableDamping = true;

        this.toolhead = new THREE.Mesh(new THREE.SphereGeometry(5, 32, 32), new THREE.MeshBasicMaterial({ color: 0xff0000 }));
        this.scene.add(this.toolhead);

        this.targetHead = new THREE.Mesh(new THREE.SphereGeometry(5, 32, 32), new THREE.MeshBasicMaterial({ color: 0x00ffff, transparent: true, opacity: 0.5 }));
        this.targetHead.visible = false;
        this.scene.add(this.targetHead);

        this.bboxDots = [];
        for (let i = 0; i < 4; i++) {
            const d = new THREE.Mesh(new THREE.SphereGeometry(3, 16, 16), new THREE.MeshBasicMaterial({ color: 0x2196f3 }));
            d.visible = false;
            this.scene.add(d);
            this.bboxDots.push(d);
        }

        this.textPathsGroup = new THREE.Group();
        this.scene.add(this.textPathsGroup);

        this.penWidth = 0.3;
        this.materials = [];
        this.lastPreviewArgs = null;

        window.addEventListener('resize', () => {
            if (!this.container) return;
            const w = this.container.clientWidth;
            const h = this.container.clientHeight;
            this.camera.aspect = w / h;
            this.camera.updateProjectionMatrix();
            this.renderer.setSize(w, h);
            this.materials.forEach(m => {
                if (m.resolution) m.resolution.set(w, h);
            });
        });

        this.animate = this.animate.bind(this);
        this.animate();
    }

    initScene(bedSize) {
        if(this.gridHelper) this.scene.remove(this.gridHelper);
        if(this.wireframeBox) this.scene.remove(this.wireframeBox);

        this.gridHelper = new THREE.GridHelper(bedSize, bedSize/10, 0x888888, 0x555555);
        this.wireframeBox = new THREE.LineSegments(
            new THREE.EdgesGeometry(new THREE.BoxGeometry(bedSize, bedSize, bedSize)),
            new THREE.LineBasicMaterial({ color: 0x006874, transparent: true, opacity: 0.3 })
        );
        this.wireframeBox.position.set(0, bedSize/2, 0);

        this.scene.add(this.gridHelper);
        this.scene.add(this.wireframeBox);

        this.controls.target.set(0, bedSize/2, 0);
        this.camera.position.set(bedSize * 1.4, bedSize * 1.1, bedSize * 1.4);
    }

    updateToolhead(x, y, z, bedSize) {
        this.toolhead.position.set(x - bedSize/2, z, bedSize/2 - y);
    }

    updateTargetDot(predictedPos, manualQueueLength, bedSize) {
        if (manualQueueLength > 0) {
            this.targetHead.position.set(predictedPos.x - bedSize/2, predictedPos.z, bedSize/2 - predictedPos.y);
            this.targetHead.visible = true;
        } else {
            this.targetHead.visible = false;
        }
    }

    updateBBoxDots(bboxPoints, bedSize) {
        this.bboxDots.forEach(d => d.visible = false);
        if (bboxPoints.length === 4) {
            const minX = Math.min(...bboxPoints.map(p => p.x));
            const maxX = Math.max(...bboxPoints.map(p => p.x));
            const minY = Math.min(...bboxPoints.map(p => p.y));
            const maxY = Math.max(...bboxPoints.map(p => p.y));
            const z = bboxPoints[0].z || 0;

            const corners = [ {x: minX, y: minY}, {x: maxX, y: minY}, {x: maxX, y: maxY}, {x: minX, y: maxY} ];
            corners.forEach((c, i) => {
                this.bboxDots[i].position.set(c.x - bedSize/2, z, bedSize/2 - c.y);
                this.bboxDots[i].visible = true;
            });
        } else {
            bboxPoints.forEach((p, i) => {
                this.bboxDots[i].position.set(p.x - bedSize/2, p.z || 0, bedSize/2 - p.y);
                this.bboxDots[i].visible = true;
            });
        }
    }

    drawPreview(paths, outPaths, originZ, bedSize, isDarkMode) {
        this.lastPreviewArgs = { paths, outPaths, originZ, bedSize, isDarkMode };
        this.textPathsGroup.clear();
        this.materials = [];

        const inkColor = isDarkMode ? 0x4fd8eb : 0x006874;
        const outColor = 0xff0000;
        const zPos = originZ !== undefined ? originZ : 0.2;

        const addPathGroup = (pathList, color) => {
            if (!pathList || pathList.length === 0) return;

            const segments = [];
            pathList.forEach(poly => {
                if (!poly || poly.length < 2) return;
                for (let i = 0; i < poly.length - 1; i++) {
                    segments.push(poly[i], poly[i + 1]);
                }
            });

            if (segments.length === 0) return;

            if (LineSegments2 && LineSegmentsGeometry && LineMaterial) {
                const positions = [];
                segments.forEach(pt => {
                    positions.push(pt.x - bedSize / 2, zPos, bedSize / 2 - pt.y);
                });

                const geo = new LineSegmentsGeometry();
                geo.setPositions(positions);

                const mat = new LineMaterial({
                    color: color,
                    linewidth: Math.max(0.02, this.penWidth),
                    worldUnits: true,
                    alphaToCoverage: true
                });
                mat.resolution.set(this.container.clientWidth, this.container.clientHeight);
                this.materials.push(mat);

                const line = new LineSegments2(geo, mat);
                this.textPathsGroup.add(line);
            } else {
                const points = segments.map(pt => new THREE.Vector3(pt.x - bedSize / 2, zPos, bedSize / 2 - pt.y));
                const geo = new THREE.BufferGeometry().setFromPoints(points);
                const mat = new THREE.LineBasicMaterial({ color: color });
                this.textPathsGroup.add(new THREE.LineSegments(geo, mat));
            }
        };

        addPathGroup(paths, inkColor);
        addPathGroup(outPaths, outColor);
    }

    setPenWidth(widthMm) {
        this.penWidth = Math.max(0.02, parseFloat(widthMm));
        this.materials.forEach(mat => {
            if (mat.linewidth !== undefined) {
                mat.linewidth = this.penWidth;
                mat.needsUpdate = true;
            }
        });
    }

    clearPaths() {
        this.textPathsGroup.clear();
        this.materials = [];
        this.lastPreviewArgs = null;
    }

    setZoom(val) {
        this.camera.zoom = val;
        this.camera.updateProjectionMatrix();
    }

    animate() {
        requestAnimationFrame(this.animate);
        this.controls.update();
        this.renderer.render(this.scene, this.camera);
    }
}