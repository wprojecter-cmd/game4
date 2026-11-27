#!/usr/bin/env bash
set -euo pipefail

# Run from the GAME3 repository root in Codespaces.
ZIP="${1:-tyrbo-explosion-effect.zip}"
test -f "$ZIP" || { echo "Не найден архив: $ZIP"; exit 1; }
test -f package.json && test -f client/package.json || {
  echo "Запусти скрипт из корня репозитория GAME3."; exit 1;
}

mkdir -p client/src/effects
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
unzip -oq "$ZIP" -d "$TMP"
test -f "$TMP/explosion.js" || {
  echo "В архиве не найден explosion.js"; exit 1;
}
cp "$TMP/explosion.js" client/src/effects/explosion.js

cat > client/src/effects/ExplosionVfx.js <<'EOF'
import * as THREE from "three";
import { createExplosions } from "./explosion.js";
import { Matrix4, Transforms, Cartesian3 } from "cesium";

const INVERSE_ENU = new Matrix4();
const SCRATCH_POSITION = new Cartesian3();
const SCRATCH_DIRECTION = new Cartesian3();
const SCRATCH_UP = new Cartesian3();

function playImpactAudio() {
  try {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return;
    const context = new AudioContextClass();
    const master = context.createGain();
    master.gain.setValueAtTime(0.0001, context.currentTime);
    master.gain.exponentialRampToValueAtTime(0.55, context.currentTime + 0.035);
    master.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 3.2);
    master.connect(context.destination);

    const low = context.createOscillator();
    const lowGain = context.createGain();
    low.type = "sawtooth";
    low.frequency.setValueAtTime(78, context.currentTime);
    low.frequency.exponentialRampToValueAtTime(25, context.currentTime + 2);
    lowGain.gain.value = 0.32;
    low.connect(lowGain);
    lowGain.connect(master);
    low.start();
    low.stop(context.currentTime + 3.25);

    const length = Math.floor(context.sampleRate * 2.2);
    const buffer = context.createBuffer(1, length, context.sampleRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < length; i += 1) {
      channel[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / length, 2.5);
    }
    const noise = context.createBufferSource();
    const filter = context.createBiquadFilter();
    const noiseGain = context.createGain();
    noise.buffer = buffer;
    filter.type = "lowpass";
    filter.frequency.setValueAtTime(2100, context.currentTime);
    filter.frequency.exponentialRampToValueAtTime(130, context.currentTime + 1.9);
    noiseGain.gain.value = 0.62;
    noise.connect(filter);
    filter.connect(noiseGain);
    noiseGain.connect(master);
    noise.start();
    noise.onended = () => context.close();
  } catch (error) {
    console.warn("Explosion audio unavailable:", error);
  }
}

export function spawnExplosionVfx(viewer, worldPosition, size) {
  if (!viewer || viewer.isDestroyed() || !worldPosition) return () => {};

  const host = viewer.container;
  if (getComputedStyle(host).position === "static") host.style.position = "relative";

  const canvas = document.createElement("canvas");
  canvas.className = "game3-explosion-canvas";
  Object.assign(canvas.style, {
    position: "absolute",
    inset: "0",
    width: "100%",
    height: "100%",
    pointerEvents: "none",
    zIndex: "2"
  });
  host.appendChild(canvas);

  const mobile = window.matchMedia("(max-width: 760px)").matches ||
    (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 4);

  let renderer;
  let explosions;
  let resizeObserver;
  let disposed = false;
  let startedAt = performance.now();
  let previousFrame = startedAt;

  try {
    renderer = new THREE.WebGLRenderer({
      canvas,
      alpha: true,
      antialias: false,
      powerPreference: "high-performance"
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, mobile ? 1 : 1.5));
    renderer.setSize(Math.max(1, host.clientWidth), Math.max(1, host.clientHeight), false);
    renderer.setClearColor(0x000000, 0);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;

    const scene = new THREE.Scene();
    scene.add(new THREE.AmbientLight(0xffffff, 1.15));

    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 200000);
    explosions = createExplosions(scene, {
      slots: 3,
      debris: mobile ? 7 : 12,
      ground: 0,
      light: mobile ? 35 : 60,
      scorch: true,
      ambient: 0.85
    });

    // Tyrbo's simulation is Y-up; Cesium's local ENU frame is Z-up.
    // Rotate the effect group so gravity, debris and the ground plane align.
    explosions.object.rotation.x = -Math.PI / 2;
    explosions.explode(new THREE.Vector3(0, 0, 0), size ?? (mobile ? 12 : 18));

    const enu = Transforms.eastNorthUpToFixedFrame(worldPosition);
    Matrix4.inverse(enu, INVERSE_ENU);

    const resize = () => {
      if (disposed) return;
      const width = Math.max(1, host.clientWidth);
      const height = Math.max(1, host.clientHeight);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };

    if (typeof ResizeObserver !== "undefined") {
      resizeObserver = new ResizeObserver(resize);
      resizeObserver.observe(host);
    } else {
      window.addEventListener("resize", resize);
    }
    resize();

    const cleanup = () => {
      if (disposed) return;
      disposed = true;
      viewer.scene.postRender.removeEventListener(render);
      if (resizeObserver) resizeObserver.disconnect();
      else window.removeEventListener("resize", resize);
      explosions?.dispose();
      renderer?.dispose();
      renderer?.forceContextLoss();
      canvas.remove();
    };

    function render() {
      if (disposed || viewer.isDestroyed()) {
        cleanup();
        return;
      }

      const now = performance.now();
      if ((now - startedAt) / 1000 >= 7.5) {
        cleanup();
        return;
      }

      const dt = Math.min(0.05, Math.max(0, (now - previousFrame) / 1000));
      previousFrame = now;

      const cesiumCamera = viewer.camera;
      Matrix4.multiplyByPoint(INVERSE_ENU, cesiumCamera.positionWC, SCRATCH_POSITION);
      Matrix4.multiplyByPointAsVector(INVERSE_ENU, cesiumCamera.directionWC, SCRATCH_DIRECTION);
      Matrix4.multiplyByPointAsVector(INVERSE_ENU, cesiumCamera.upWC, SCRATCH_UP);

      camera.position.set(SCRATCH_POSITION.x, SCRATCH_POSITION.y, SCRATCH_POSITION.z);
      camera.up.set(SCRATCH_UP.x, SCRATCH_UP.y, SCRATCH_UP.z).normalize();
      camera.lookAt(
        SCRATCH_POSITION.x + SCRATCH_DIRECTION.x,
        SCRATCH_POSITION.y + SCRATCH_DIRECTION.y,
        SCRATCH_POSITION.z + SCRATCH_DIRECTION.z
      );

      const projection = cesiumCamera.frustum.projectionMatrix;
      camera.projectionMatrix.fromArray(Array.from({ length: 16 }, (_, i) => projection[i]));
      camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();

      explosions.update(dt);
      renderer.render(scene, camera);
      // Keep Cesium's request-render loop alive for the duration of the effect.
      viewer.scene.requestRender();
    }

    viewer.scene.postRender.addEventListener(render);
    playImpactAudio();
    viewer.scene.requestRender();
    return cleanup;
  } catch (error) {
    console.error("Tyrbo explosion failed to initialize:", error);
    explosions?.dispose();
    renderer?.dispose();
    canvas.remove();
    return () => {};
  }
}
EOF

# Remove the old postinstall hook that built three-realistic-vfx.
node --input-type=module <<'NODE'
import fs from "node:fs";
const root = JSON.parse(fs.readFileSync("package.json", "utf8"));
delete root.scripts?.postinstall;
fs.writeFileSync("package.json", JSON.stringify(root, null, 2) + "\n");

const client = JSON.parse(fs.readFileSync("client/package.json", "utf8"));
client.dependencies = client.dependencies || {};
delete client.dependencies["three-realistic-vfx"];
client.dependencies.three = "^0.186.0";
fs.writeFileSync("client/package.json", JSON.stringify(client, null, 2) + "\n");
NODE

npm install
npm run build -w client

echo
echo "Готово: оригинальный Tyrbo Explosion подключён, сборка клиента прошла."
echo "Проверь git diff, затем выполни:"
echo "git add package.json package-lock.json client/package.json client/src/effects/explosion.js client/src/effects/ExplosionVfx.js"
echo "git commit -m \"Integrate original Tyrbo explosion effect\""
echo "git push"
