import * as Cesium from "cesium";
import { spawnExplosionVfx } from "./ExplosionVfx.js";

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const MODEL_URL = "https://raw.githubusercontent.com/wprojecter-cmd/game4/main/quadcopter-original.glb";
const MODEL_SCALE = 3.2;

export function startDroneFlight(viewer, startPosition, onCollision = () => {}, onCameraModeChange = () => {}) {
  if (!viewer || viewer.isDestroyed() || !startPosition) return () => {};

  const scene = viewer.scene;
  const camera = viewer.camera;
  const ellipsoid = scene.globe.ellipsoid;
  const start = Cesium.Cartographic.fromCartesian(startPosition);
  const position = new Cesium.Cartographic(
    start.longitude,
    start.latitude,
    Math.max(35, start.height + 25)
  );

  let heading = 0;
  let speed = 0;
  let verticalSpeed = 0;
  let disposed = false;
  let crashed = false;
  let model = null;
  let modelLoadError = null;
  let lastTime = performance.now();
  let lastCollisionCheck = 0;
  let frame = 0;
  let cameraMode = "orbit";
  let orbitYaw = 0;
  let orbitPitch = Cesium.Math.toRadians(22);
  let orbitDistance = 18;
  let dragging = false;
  let lastPointer = null;

  const keys = Object.create(null);
  const world = () => Cesium.Cartesian3.fromRadians(
    position.longitude,
    position.latitude,
    position.height,
    ellipsoid
  );
  const enu = () => Cesium.Transforms.eastNorthUpToFixedFrame(world());
  const local = (east, north, up) => Cesium.Matrix4.multiplyByPoint(
    enu(),
    new Cesium.Cartesian3(east, north, up),
    new Cesium.Cartesian3()
  );

  const savedController = {
    enableRotate: scene.screenSpaceCameraController.enableRotate,
    enableTranslate: scene.screenSpaceCameraController.enableTranslate,
    enableZoom: scene.screenSpaceCameraController.enableZoom,
    enableTilt: scene.screenSpaceCameraController.enableTilt,
    enableLook: scene.screenSpaceCameraController.enableLook
  };

  function updateModelMatrix() {
    if (!model) return;
    const localRotation = Cesium.Matrix4.fromRotationTranslation(
      Cesium.Matrix3.fromRotationZ(-heading),
      Cesium.Cartesian3.ZERO,
      new Cesium.Matrix4()
    );
    model.modelMatrix = Cesium.Matrix4.multiply(
      enu(),
      localRotation,
      new Cesium.Matrix4()
    );
  }

  async function loadDroneModel() {
    try {
      const loaded = await Cesium.Model.fromGltfAsync({
        url: MODEL_URL,
        modelMatrix: enu(),
        scale: MODEL_SCALE,
        minimumPixelSize: 32,
        maximumScale: 12,
        allowPicking: false,
        shadows: Cesium.ShadowMode.ENABLED
      });

      if (disposed || viewer.isDestroyed()) {
        loaded.destroy();
        return;
      }

      model = scene.primitives.add(loaded);
      updateModelMatrix();
      scene.requestRender();
    } catch (error) {
      modelLoadError = error;
      console.error("Could not load the quadcopter GLB model:", error);
    }
  }

  function cameraOrientation(destination, target) {
    const direction = Cesium.Cartesian3.normalize(
      Cesium.Cartesian3.subtract(target, destination, new Cesium.Cartesian3()),
      new Cesium.Cartesian3()
    );
    const up = Cesium.Cartesian3.normalize(
      Cesium.Matrix4.multiplyByPointAsVector(
        enu(),
        Cesium.Cartesian3.UNIT_Z,
        new Cesium.Cartesian3()
      ),
      new Cesium.Cartesian3()
    );
    camera.setView({ destination, orientation: { direction, up } });
  }

  function updateCamera() {
    let destination;
    let target;

    if (cameraMode === "fpv") {
      destination = local(0, 1.25, 0.48);
      target = local(
        Math.sin(heading) * 100,
        Math.cos(heading) * 100,
        0.25
      );
    } else {
      const angle = heading + orbitYaw;
      const horizontal = orbitDistance * Math.cos(orbitPitch);
      destination = local(
        -Math.sin(angle) * horizontal,
        -Math.cos(angle) * horizontal,
        orbitDistance * Math.sin(orbitPitch) + 1.8
      );
      target = local(0, 0, 0.25);
    }

    cameraOrientation(destination, target);
  }

  function setCameraMode(mode) {
    if (mode !== "fpv" && mode !== "orbit") return;
    cameraMode = mode;
    dragging = false;
    lastPointer = null;
    onCameraModeChange(mode);
    updateCamera();
    scene.requestRender();
  }

  function removeModel() {
    if (!model || viewer.isDestroyed()) return;
    scene.primitives.remove(model);
    model = null;
  }

  function cleanup() {
    if (disposed) return;
    disposed = true;
    cancelAnimationFrame(frame);

    window.removeEventListener("keydown", keyDown);
    window.removeEventListener("keyup", keyUp);
    window.removeEventListener("blur", clearKeys);
    scene.canvas.removeEventListener("pointerdown", pointerDown);
    window.removeEventListener("pointermove", pointerMove);
    window.removeEventListener("pointerup", pointerUp);
    scene.canvas.removeEventListener("wheel", wheel);

    if (!viewer.isDestroyed()) {
      removeModel();
      Object.assign(scene.screenSpaceCameraController, savedController);
      scene.requestRender();
    }
  }

  cleanup.setCameraMode = setCameraMode;

  function keyDown(event) {
    keys[event.code] = true;

    if (event.code === "KeyV" && !event.repeat) {
      setCameraMode(cameraMode === "orbit" ? "fpv" : "orbit");
    }

    if ([
      "KeyW", "KeyA", "KeyS", "KeyD",
      "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
      "Space", "ControlLeft", "ControlRight", "ShiftLeft", "ShiftRight"
    ].includes(event.code)) {
      event.preventDefault();
    }
  }

  function keyUp(event) {
    keys[event.code] = false;
  }

  function clearKeys() {
    for (const key of Object.keys(keys)) keys[key] = false;
  }

  function pointerDown(event) {
    if (cameraMode !== "orbit" || event.button !== 0) return;
    dragging = true;
    lastPointer = { x: event.clientX, y: event.clientY };
  }

  function pointerMove(event) {
    if (!dragging || !lastPointer || cameraMode !== "orbit") return;

    const dx = event.clientX - lastPointer.x;
    const dy = event.clientY - lastPointer.y;
    lastPointer = { x: event.clientX, y: event.clientY };

    orbitYaw -= dx * 0.006;
    orbitPitch = clamp(
      orbitPitch + dy * 0.004,
      Cesium.Math.toRadians(-5),
      Cesium.Math.toRadians(78)
    );
    updateCamera();
  }

  function pointerUp() {
    dragging = false;
    lastPointer = null;
  }

  function wheel(event) {
    if (cameraMode !== "orbit") return;
    event.preventDefault();
    orbitDistance = clamp(
      orbitDistance + Math.sign(event.deltaY) * Math.max(1, orbitDistance * 0.12),
      5,
      90
    );
    updateCamera();
  }

  function collide() {
    if (crashed || disposed) return;
    crashed = true;
    const impact = world();
    cleanup();

    try {
      spawnExplosionVfx(viewer, impact, 22);
    } catch (error) {
      console.error("Drone explosion failed:", error);
    }

    onCollision();
  }

  function checkCollision(now) {
    if (now - lastCollisionCheck < 120) return;
    lastCollisionCheck = now;

    let surface = scene.globe.getHeight(position);
    try {
      const exclusions = model ? [model] : [];
      const sampled = scene.sampleHeight(position, exclusions, 2);
      if (Number.isFinite(sampled)) {
        surface = Math.max(surface ?? -Infinity, sampled);
      }
    } catch {
      // Terrain sampling may be unavailable while tiles are loading.
    }

    if (Number.isFinite(surface) && position.height <= surface + 1.7) {
      collide();
    }
  }

  function update(now) {
    if (disposed || viewer.isDestroyed()) {
      cleanup();
      return;
    }

    const dt = Math.min(0.05, Math.max(0, (now - lastTime) / 1000));
    lastTime = now;

    const forward = keys.KeyW || keys.ArrowUp;
    const backward = keys.KeyS || keys.ArrowDown;
    const left = keys.KeyA || keys.ArrowLeft;
    const right = keys.KeyD || keys.ArrowRight;

    if (left) heading -= 1.8 * dt;
    if (right) heading += 1.8 * dt;

    const boost = keys.ShiftLeft || keys.ShiftRight;
    const maxSpeed = boost ? 65 : 28;

    if (forward) speed += 24 * dt;
    else if (backward) speed -= 24 * dt;
    else speed *= Math.max(0, 1 - 2.2 * dt);

    speed = clamp(speed, -maxSpeed * 0.45, maxSpeed);

    if (keys.Space) verticalSpeed += 16 * dt;
    else if (keys.ControlLeft || keys.ControlRight) verticalSpeed -= 16 * dt;
    else verticalSpeed *= Math.max(0, 1 - 2.5 * dt);

    verticalSpeed = clamp(verticalSpeed, -18, 18);

    const distance = speed * dt;
    const east = Math.sin(heading) * distance;
    const north = Math.cos(heading) * distance;
    const radius = ellipsoid.maximumRadius;
    const cosLatitude = Math.max(0.05, Math.cos(position.latitude));

    position.longitude += east / (radius * cosLatitude);
    position.latitude = clamp(
      position.latitude + north / radius,
      -Math.PI / 2 + 0.001,
      Math.PI / 2 - 0.001
    );
    position.height = Math.max(2, position.height + verticalSpeed * dt);

    updateModelMatrix();
    updateCamera();
    checkCollision(now);

    if (!disposed) {
      scene.requestRender();
      frame = requestAnimationFrame(update);
    }
  }

  const controller = scene.screenSpaceCameraController;
  controller.enableRotate = false;
  controller.enableTranslate = false;
  controller.enableZoom = false;
  controller.enableTilt = false;
  controller.enableLook = false;

  window.addEventListener("keydown", keyDown, { passive: false });
  window.addEventListener("keyup", keyUp);
  window.addEventListener("blur", clearKeys);
  scene.canvas.addEventListener("pointerdown", pointerDown);
  window.addEventListener("pointermove", pointerMove);
  window.addEventListener("pointerup", pointerUp);
  scene.canvas.addEventListener("wheel", wheel, { passive: false });

  updateCamera();
  loadDroneModel();
  frame = requestAnimationFrame(update);

  return cleanup;
}
