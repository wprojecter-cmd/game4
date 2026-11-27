import express from "express";
import fs from "node:fs/promises";
import fsSync from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import unzipper from "unzipper";

const app = express();
const ROOT = process.cwd();
const PUBLIC_DIR = path.join(ROOT, "public");
const IMPORT_DIR = path.join(PUBLIC_DIR, "models", "imported");
const DIST_DIR = path.join(ROOT, "dist");
const PORT = Number(process.env.PORT || 4174);

app.use(express.json({ limit: "16kb" }));

function sendEvent(res, payload) {
  res.write("data: " + JSON.stringify(payload) + "\n\n");
}

function extractSketchfabUid(rawUrl) {
  let parsed;
  try { parsed = new URL(rawUrl); } catch { throw new Error("Вставь корректную ссылку на страницу модели."); }
  if (!["sketchfab.com", "www.sketchfab.com"].includes(parsed.hostname)) {
    throw new Error("Пока поддерживаются только ссылки Sketchfab.");
  }
  const match = parsed.pathname.match(/-([a-f0-9]{32})\/?$/i);
  if (!match) throw new Error("Не удалось найти ID модели в ссылке Sketchfab.");
  return match[1].toLowerCase();
}

async function readModelMetadata(uid) {
  const response = await fetch("https://api.sketchfab.com/v3/models/" + uid, { signal: AbortSignal.timeout(20000) });
  if (!response.ok) return {};
  const data = await response.json();
  return {
    name: data.name || "Sketchfab model " + uid.slice(0, 8),
    author: data.user?.displayName || data.user?.username || "Unknown creator",
    license: data.license?.label || data.license?.slug || "Проверь лицензию на странице модели",
    source: "https://sketchfab.com/3d-models/" + uid
  };
}

async function extractArchive(buffer, destination) {
  const directory = await unzipper.Open.buffer(buffer);
  let totalSize = 0;
  const extracted = [];
  for (const entry of directory.files) {
    if (entry.type !== "File") continue;
    const normalized = path.posix.normalize(entry.path.replace(/\\/g, "/"));
    if (normalized.startsWith("/") || normalized === ".." || normalized.startsWith("../")) {
      throw new Error("Архив содержит недопустимый путь.");
    }
    totalSize += Number(entry.vars.uncompressedSize || 0);
    if (totalSize > 300 * 1024 * 1024) throw new Error("Распакованный архив превышает лимит тестовой лаборатории (300 МБ).");
    const target = path.resolve(destination, normalized);
    if (!target.startsWith(destination + path.sep)) throw new Error("Недопустимый путь внутри архива.");
    await fs.mkdir(path.dirname(target), { recursive: true });
    await new Promise((resolve, reject) => {
      const output = requireNotUsedCreateWriteStream(target);
      entry.stream().on("error", reject).pipe(output).on("error", reject).on("finish", resolve);
    });
    extracted.push(normalized);
  }
  const modelFile = extracted.find((name) => name.toLowerCase().endsWith(".glb"))
    || extracted.find((name) => path.posix.basename(name).toLowerCase() === "scene.gltf")
    || extracted.find((name) => name.toLowerCase().endsWith(".gltf"));
  if (!modelFile) throw new Error("В архиве не найден файл .gltf или .glb.");
  return modelFile;
}

function requireNotUsedCreateWriteStream(filePath) {
  return fsSync.createWriteStream(filePath);
}


app.get("/health", (_req, res) => res.json({ ok: true, service: "model-lab" }));

app.get("/api/models", async (_req, res) => {
  try {
    const entries = await fs.readdir(IMPORT_DIR, { withFileTypes: true });
    const models = [];
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      try {
        const metadata = JSON.parse(await fs.readFile(path.join(IMPORT_DIR, entry.name, "model.json"), "utf8"));
        models.push(metadata);
      } catch { /* Ignore incomplete imports. */ }
    }
    res.json(models);
  } catch {
    res.json([]);
  }
});

app.post("/api/import", async (req, res) => {
  let uid;
  try { uid = extractSketchfabUid(String(req.body?.url || "")); }
  catch (error) { return res.status(400).json({ error: error.message }); }
  if (!process.env.SKETCHFAB_API_TOKEN) {
    return res.status(503).json({ error: "Не задан SKETCHFAB_API_TOKEN. Добавь токен Sketchfab в секреты Codespaces и перезапусти импортёр." });
  }

  res.status(200);
  res.setHeader("Content-Type", "text/event-stream; charset=utf-8");
  res.setHeader("Cache-Control", "no-cache, no-transform");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders?.();

  try {
    sendEvent(res, { type: "log", message: "Ссылка распознана · Sketchfab UID " + uid });
    const metadata = await readModelMetadata(uid);
    sendEvent(res, { type: "log", message: "Карточка модели получена: " + (metadata.name || uid) });
    sendEvent(res, { type: "log", message: "Запрашиваем временную ссылку Download API…" });

    const downloadInfoResponse = await fetch("https://api.sketchfab.com/v3/models/" + uid + "/download", {
      headers: { Authorization: "Token " + process.env.SKETCHFAB_API_TOKEN },
      signal: AbortSignal.timeout(30000)
    });
    if (!downloadInfoResponse.ok) {
      const detail = downloadInfoResponse.status === 401 ? "Sketchfab отклонил токен. Проверь SKETCHFAB_API_TOKEN."
        : downloadInfoResponse.status === 403 ? "У аккаунта нет доступа к скачиванию этой модели."
        : downloadInfoResponse.status === 400 ? "Эта модель недоступна для скачивания."
        : "Sketchfab Download API ответил HTTP " + downloadInfoResponse.status + ".";
      throw new Error(detail);
    }
    const downloadInfo = await downloadInfoResponse.json();
    const archiveUrl = downloadInfo.gltf?.url;
    if (!archiveUrl) throw new Error("Sketchfab не предоставил архив glTF для этой модели.");
    sendEvent(res, { type: "log", message: "Скачиваем архив модели…" });

    const archiveResponse = await fetch(archiveUrl, { signal: AbortSignal.timeout(120000) });
    if (!archiveResponse.ok) throw new Error("Не удалось скачать архив модели: HTTP " + archiveResponse.status + ".");
    const declaredSize = Number(archiveResponse.headers.get("content-length") || 0);
    if (declaredSize > 150 * 1024 * 1024) throw new Error("Архив превышает тестовый лимит 150 МБ.");
    const archiveBuffer = Buffer.from(await archiveResponse.arrayBuffer());
    if (archiveBuffer.length > 150 * 1024 * 1024) throw new Error("Архив превышает тестовый лимит 150 МБ.");

    const folder = uid + "-" + crypto.randomBytes(3).toString("hex");
    const destination = path.join(IMPORT_DIR, folder);
    await fs.mkdir(destination, { recursive: true });
    sendEvent(res, { type: "log", message: "Архив получен (" + (archiveBuffer.length / 1024 / 1024).toFixed(2) + " МБ) · распаковываем…" });
    const modelFile = await extractArchive(archiveBuffer, destination);
    const model = {
      id: "import-" + folder,
      name: metadata.name || "Sketchfab " + uid.slice(0, 8),
      subtitle: "Импортировано из Sketchfab",
      url: "/models/imported/" + folder + "/" + modelFile.split("/").map(encodeURIComponent).join("/"),
      author: metadata.author || "Unknown creator",
      license: metadata.license || "Проверь лицензию на странице модели",
      credit: "Источник: Sketchfab. Автор: " + (metadata.author || "не указан") + ". Лицензия: " + (metadata.license || "не указана") + ".",
      source: "https://sketchfab.com/3d-models/" + uid,
      normalize: true
    };
    await fs.writeFile(path.join(destination, "model.json"), JSON.stringify(model, null, 2));
    sendEvent(res, { type: "log", message: "Найден файл сцены: " + modelFile });
    sendEvent(res, { type: "log", message: "Модель сохранена в public/models/imported/" + folder });
    sendEvent(res, { type: "done", model });
    res.end();
  } catch (error) {
    sendEvent(res, { type: "error", message: error.message || "Неизвестная ошибка импорта." });
    res.end();
  }
});

app.use("/models", express.static(path.join(PUBLIC_DIR, "models")));
app.use(express.static(DIST_DIR));
app.get("*", async (_req, res, next) => {
  try { await fs.access(path.join(DIST_DIR, "index.html")); res.sendFile(path.join(DIST_DIR, "index.html")); }
  catch { next(); }
});

app.listen(PORT, "0.0.0.0", () => {
  console.log("[Model Lab] API listening on port " + PORT);
  if (!process.env.SKETCHFAB_API_TOKEN) console.warn("[Model Lab] SKETCHFAB_API_TOKEN is not configured.");
});
