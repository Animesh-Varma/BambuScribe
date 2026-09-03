import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

export class PlotterVisualizer {
    constructor(containerId) {
        const container = document.getElementById(containerId);
        this.scene = new THREE.Scene();
        this.camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.1, 1000);
        this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        this.renderer.setSize(container.clientWidth, container.clientHeight);
        container.appendChild(this.renderer.domElement);

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
            const z = bboxPoints[0].z;

            const corners = [ {x: minX, y: minY}, {x: maxX, y: minY}, {x: maxX, y: maxY}, {x: minX, y: maxY} ];
            corners.forEach((c, i) => {
                this.bboxDots[i].position.set(c.x - bedSize/2, z, bedSize/2 - c.y);
                this.bboxDots[i].visible = true;
            });
        } else {
            bboxPoints.forEach((p, i) => {
                this.bboxDots[i].position.set(p.x - bedSize/2, p.z, bedSize/2 - p.y);
                this.bboxDots[i].visible = true;
            });
        }
    }

    drawPreview(paths, outPaths, originZ, bedSize, isDarkMode) {
        this.textPathsGroup.clear();
        const inkColor = isDarkMode ? 0x4fd8eb : 0x006874;
        const outColor = 0xff0000;

        const addPathGroup = (pathList, color) => {
            if (!pathList || pathList.length === 0) return;
            const points = [];
            pathList.forEach(segment => {
                points.push(new THREE.Vector3(segment[0].x - bedSize/2, originZ, bedSize/2 - segment[0].y));
                points.push(new THREE.Vector3(segment[1].x - bedSize/2, originZ, bedSize/2 - segment[1].y));
            });
            const geo = new THREE.BufferGeometry().setFromPoints(points);
            const mat = new THREE.LineBasicMaterial({ color: color });
            this.textPathsGroup.add(new THREE.LineSegments(geo, mat));
        };

        addPathGroup(paths, inkColor);
        addPathGroup(outPaths, outColor);
    }

    clearPaths() {
        this.textPathsGroup.clear();
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