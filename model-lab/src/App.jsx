import React, { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const MODELS = [
  {
    id: "ostankino",
    name: "Останкинская телебашня",
    subtitle: "Москва · загруженная модель",
    url: "/models/ostankino/Ostankino_Tower.gltf",
    author: "Arslan Timkanov",
    license: "CC BY 4.0",
    credit: "Модель Ostankino Tower создана Arslan Timkanov. Использование с указанием авторства.",
    source: "https://sketchfab.com/3d-models/ostankino-tower-80ba20d862e24d849eabded16ef2443c",
    scale: 1,
    normalize: true
  },
  {
    id: "cesium-man",
    name: "Cesium Man",
    subtitle: "Тестовая модель Khronos glTF",
    url: "/models/cesium-man.glb",
    author: "Cesium",
    license: "CC BY 4.0",
    credit: "© 2017 Cesium. Модель предоставлена Cesium for Everything. CC BY 4.0. Логотип и товарный знак Cesium не входят в лицензию модели.",
    source: "https://github.com/KhronosGroup/glTF-Sample-Assets/tree/main/Models/CesiumMan",
    scale: 1,
    normalize: true
  }
];

export default function App() {
  const mountRef = useRef(null);
  const sceneRef = useRef(null);
  const modelRef = useRef(null);
  const loaderRef = useRef(null);
  const autoRotateRef = useRef(false);
  const [active, setActive] = useState("ostankino");
  const [status, setStatus] = useState("Подготовка 3D-сцены…");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [wireframe, setWireframe] = useState(false);
  const [autoRotate, setAutoRotate] = useState(false);
  const [modelInfo, setModelInfo] = useState(null);
  const [models, setModels] = useState(MODELS);
  const [modelUrl, setModelUrl] = useState("");
  const [importing, setImporting] = useState(false);
  const [terminalLines, setTerminalLines] = useState([
    { type: "system", text: "MODEL LAB IMPORT CONSOLE / READY" },
    { type: "system", text: "Ожидание ссылки на 3D-модель…" }
  ]);
  const terminalRef = useRef(null);

  const addTerminalLine = (text, type = "info") => {
    setTerminalLines((lines) => [...lines.slice(-79), { type, text }]);
  };

  useEffect(() => {
    fetch("/api/models")
      .then((response) => response.ok ? response.json() : [])
      .then((items) => {
        if (Array.isArray(items) && items.length) {
          setModels((current) => [...MODELS, ...items.filter((item) => !current.some((m) => m.id === item.id))]);
        }
      })
      .catch(() => addTerminalLine("Локальный импортёр пока не подключён.", "warn"));
  }, []);

  useEffect(() => {
    if (terminalRef.current) terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
  }, [terminalLines]);

  useEffect(() => {
    const host = mountRef.current;
    if (!host) return;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#090d13");
    scene.fog = new THREE.Fog("#090d13", 45, 180);
    const camera = new THREE.PerspectiveCamera(48, 1, 0.1, 2000);
    camera.position.set(12, 10, 18);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.15;
    host.appendChild(renderer.domElement);

    scene.add(new THREE.HemisphereLight(0xbfdcff, 0x20232a, 2.1));
    const key = new THREE.DirectionalLight(0xffffff, 3.2);
    key.position.set(12, 24, 16);
    scene.add(key);
    const rim = new THREE.DirectionalLight(0x72aaff, 1.4);
    rim.position.set(-14, 8, -12);
    scene.add(rim);

    const grid = new THREE.GridHelper(100, 100, 0x426078, 0x1d2b38);
    grid.position.y = -0.015;
    scene.add(grid);
    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(200, 200),
      new THREE.MeshStandardMaterial({ color: 0x111a23, roughness: 0.94, metalness: 0.05 })
    );
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -0.04;
    scene.add(ground);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.06;
    controls.minDistance = 0.4;
    controls.maxDistance = 800;
    controls.target.set(0, 4, 0);
    loaderRef.current = new GLTFLoader();
    sceneRef.current = { scene, camera, renderer, controls, grid, ground, key, rim };

    const resize = () => {
      const w = host.clientWidth || window.innerWidth;
      const h = host.clientHeight || window.innerHeight;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h, false);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    let frame;
    const animate = () => {
      frame = requestAnimationFrame(animate);
      controls.autoRotate = autoRotateRef.current;
      controls.autoRotateSpeed = 1.0;
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      renderer.dispose();
      if (modelRef.current) disposeObject(modelRef.current);
      host.removeChild(renderer.domElement);
      sceneRef.current = null;
    };
  }, []);

  useEffect(() => {
    autoRotateRef.current = autoRotate;
  }, [autoRotate]);

  useEffect(() => {
    let cancelled = false;
    const state = sceneRef.current;
    const item = models.find((m) => m.id === active);
    if (!state || !item) return;
    setLoading(true);
    setError("");
    setStatus("Загружаем геометрию и материалы…");
    if (modelRef.current) {
      state.scene.remove(modelRef.current);
      disposeObject(modelRef.current);
      modelRef.current = null;
    }

    loaderRef.current.load(
      item.url,
      (gltf) => {
        if (cancelled) {
          disposeObject(gltf.scene);
          return;
        }
        const root = gltf.scene;
        root.updateMatrixWorld(true);
        const bounds = new THREE.Box3().setFromObject(root);
        const size = new THREE.Vector3();
        const center = new THREE.Vector3();
        bounds.getSize(size);
        bounds.getCenter(center);
        root.position.sub(center);
        const maxDim = Math.max(size.x, size.y, size.z) || 1;
        const targetHeight = item.id === "ostankino" ? 22 : 5;
        const factor = item.normalize ? targetHeight / maxDim : item.scale;
        root.scale.multiplyScalar(factor);
        root.position.multiplyScalar(factor);
        root.position.y -= new THREE.Box3().setFromObject(root).min.y;
        root.traverse((obj) => {
          if (!obj.isMesh) return;
          obj.castShadow = true;
          obj.receiveShadow = true;
          if (Array.isArray(obj.material)) obj.material.forEach((m) => { m.wireframe = wireframe; });
          else if (obj.material) obj.material.wireframe = wireframe;
        });
        state.scene.add(root);
        modelRef.current = root;
        const finalBox = new THREE.Box3().setFromObject(root);
        const finalSize = finalBox.getSize(new THREE.Vector3());
        state.controls.target.set(0, finalSize.y * 0.48, 0);
        state.camera.position.set(finalSize.y * 0.9, finalSize.y * 0.65, finalSize.y * 1.5);
        state.controls.update();
        setModelInfo({
          meshes: countMeshes(root),
          materials: countMaterials(root),
          width: finalSize.x,
          height: finalSize.y,
          depth: finalSize.z
        });
        setStatus("Модель успешно загружена в сцену");
        setLoading(false);
      },
      (event) => {
        if (!cancelled && event.total) {
          setStatus("Загрузка: " + Math.round((event.loaded / event.total) * 100) + "%");
        }
      },
      (err) => {
        if (cancelled) return;
        console.error(err);
        setError("Не удалось загрузить модель. Проверь наличие файла и путь /models/.");
        setStatus("Ошибка загрузки");
        setLoading(false);
      }
    );
    return () => { cancelled = true; };
  }, [active, models]);

  useEffect(() => {
    modelRef.current?.traverse((obj) => {
      if (!obj.isMesh || !obj.material) return;
      const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
      mats.forEach((m) => { m.wireframe = wireframe; });
    });
  }, [wireframe]);

  const selected = models.find((m) => m.id === active);

  async function importModel(event) {
    event.preventDefault();
    const url = modelUrl.trim();
    if (!url || importing) return;
    setImporting(true);
    setError("");
    addTerminalLine("$ model-import --url " + url, "command");
    addTerminalLine("Подключение к локальному импортёру…");
    try {
      const response = await fetch("/api/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url })
      });
      if (!response.ok || !response.body) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || "Импортёр вернул HTTP " + response.status);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let imported = null;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";
        for (const block of events) {
          const line = block.split("\n").find((part) => part.startsWith("data:"));
          if (!line) continue;
          const payload = JSON.parse(line.slice(5).trim());
          if (payload.type === "log") addTerminalLine(payload.message, payload.level || "info");
          if (payload.type === "done") imported = payload.model;
          if (payload.type === "error") throw new Error(payload.message);
        }
      }
      if (!imported) throw new Error("Сервер завершил импорт без данных модели.");
      setModels((current) => [...current.filter((m) => m.id !== imported.id), imported]);
      setActive(imported.id);
      setModelUrl("");
      addTerminalLine("Модель добавлена в пространство: " + imported.name, "success");
      setStatus("Импорт завершён · загружаем модель в сцену");
    } catch (err) {
      addTerminalLine("ОШИБКА: " + err.message, "error");
      setError(err.message);
      setStatus("Ошибка импорта");
    } finally {
      setImporting(false);
    }
  }

  function resetCamera() {
    const state = sceneRef.current;
    if (!state || !modelInfo) return;
    state.controls.target.set(0, modelInfo.height * 0.48, 0);
    state.camera.position.set(modelInfo.height * 0.9, modelInfo.height * 0.65, modelInfo.height * 1.5);
    state.controls.update();
  }

  return (
    <main className="lab">
      <div className="viewport" ref={mountRef} />
      <header className="topbar">
        <a className="brand" href="#" aria-label="My Subscribers Model Lab"><span>MS</span><b>MODEL LAB</b></a>
        <div className="top-meta"><i /> ISOLATED 3D ENVIRONMENT <span>·</span> BUILD 001</div>
        <div className="top-right">MY SUBSCRIBERS <small>EXPERIMENTAL SPACE</small></div>
      </header>

      <aside className="panel">
        <div className="eyebrow">ASSET INSPECTOR / 001</div>
        <h1>3D пространство<span>.</span></h1>
        <p className="intro">Отдельная лаборатория для проверки загруженных 3D-моделей.</p>
        <div className="importer">
          <div className="section-label">ИМПОРТ ПО ССЫЛКЕ</div>
          <form className="import-form" onSubmit={importModel}>
            <input type="url" value={modelUrl} onChange={(event) => setModelUrl(event.target.value)}
              placeholder="https://sketchfab.com/3d-models/..." aria-label="Ссылка на страницу 3D-модели" disabled={importing} />
            <button type="submit" disabled={importing || !modelUrl.trim()}>
              {importing ? "ЗАГРУЗКА…" : "ИМПОРТИРОВАТЬ ↗"}
            </button>
          </form>
          <div className="import-hint">Публичные модели Sketchfab · импорт без API-токена.</div>
          <div className="terminal">
            <div className="terminal-head"><span><i /> IMPORT TERMINAL</span><small>{importing ? "RUNNING" : "IDLE"}</small></div>
            <div className="terminal-body" ref={terminalRef} aria-live="polite">
              {terminalLines.map((line, index) => <div className={"terminal-line " + line.type} key={index}><span>{String(index + 1).padStart(2, "0")}</span>{line.text}</div>)}
              {importing && <div className="terminal-cursor">▌</div>}
            </div>
          </div>
        </div>
        <div className="section-label models-heading">МОДЕЛИ В ПРОСТРАНСТВЕ</div>
        <div className="asset-list">
          {models.map((m, index) => (
            <button key={m.id} className={"asset " + (active === m.id ? "active" : "")} onClick={() => setActive(m.id)}>
              <span className="asset-index">{String(index + 1).padStart(2, "0")}</span>
              <span className="asset-copy"><b>{m.name}</b><small>{m.subtitle}</small></span>
              <span className="asset-arrow">↗</span>
            </button>
          ))}
        </div>
        <div className="asset-detail">
          <div className="section-label">ТЕКУЩИЙ ОБЪЕКТ</div>
          <h2>{selected?.name}</h2>
          <p>{selected?.credit}</p>
          <div className="license">{selected?.license} <span>·</span> {selected?.author}</div>
          <a href={selected?.source} target="_blank" rel="noreferrer">ИСТОЧНИК И ЛИЦЕНЗИЯ ↗</a>
        </div>
        <div className="tools">
          <div className="section-label">ИНСТРУМЕНТЫ СЦЕНЫ</div>
          <button onClick={() => setAutoRotate((v) => !v)} className={autoRotate ? "tool active" : "tool"}>Автовращение <kbd>{autoRotate ? "ON" : "OFF"}</kbd></button>
          <button onClick={() => setWireframe((v) => !v)} className={wireframe ? "tool active" : "tool"}>Каркасная сетка <kbd>{wireframe ? "ON" : "OFF"}</kbd></button>
          <button onClick={resetCamera} className="tool">Сбросить камеру <kbd>↺</kbd></button>
        </div>
        <div className="status"><span className={error ? "status-dot error" : "status-dot"} />{status}</div>
        {error && <p className="error">{error}</p>}
        {modelInfo && !loading && <div className="stats"><span>Меши <b>{modelInfo.meshes}</b></span><span>Материалы <b>{modelInfo.materials}</b></span><span>Высота сцены <b>{modelInfo.height.toFixed(2)}</b></span></div>}
        <div className="help">ЛКМ — вращение · Колесо — приближение · ПКМ — перемещение</div>
      </aside>

      <div className="viewport-label"><span>LIVE VIEWPORT</span><b>{selected?.id?.toUpperCase()}</b></div>
      <div className="axis-note">WORLD GRID / Y-UP</div>
      <footer className="footer"><span>MY SUBSCRIBERS / MODEL LAB</span><span>МОДЕЛИ ЗАГРУЖАЮТСЯ ОТДЕЛЬНО ОТ ОСНОВНОЙ ИГРЫ</span></footer>
    </main>
  );
}

function countMeshes(root) {
  let n = 0;
  root.traverse((o) => { if (o.isMesh) n += 1; });
  return n;
}
function countMaterials(root) {
  const set = new Set();
  root.traverse((o) => {
    if (!o.isMesh || !o.material) return;
    (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => set.add(m));
  });
  return set.size;
}
function disposeObject(root) {
  root.traverse((o) => {
    o.geometry?.dispose?.();
    if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => {
      for (const value of Object.values(m)) if (value?.isTexture) value.dispose();
      m.dispose?.();
    });
  });
}
