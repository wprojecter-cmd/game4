import express from "express";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import crypto from "node:crypto";
import { spawn } from "node:child_process";

const app = express();
const ROOT = process.cwd();
const PUBLIC_DIR = path.join(ROOT, "public");
const IMPORT_DIR = path.join(PUBLIC_DIR, "models", "imported");
const IMPORTER_DIR = path.join(ROOT, "importer");
const IMPORT_SCRIPT = path.join(IMPORTER_DIR, "import_model.py");
const DIST_DIR = path.join(ROOT, "dist");
const PORT = Number(process.env.PORT || 4174);
const PYTHON = process.env.PYTHON_BIN || (process.platform === "win32" ? "python" : "python3");

function describeProgress(token) {
  const [step, ...parts] = String(token || "").split("|");
  const messages = {
    "step:viewer": "Получаем данные публичного 3D-просмотра Sketchfab…",
    "step:keys": "Подготавливаем данные для обработки геометрии…",
    "step:keys_partial": "Получена часть параметров; продолжаем обработку…",
    "step:legacy": "Обрабатываем формат модели…",
    "step:mesh": "Скачиваем геометрию модели…",
    "step:decrypt": "Обрабатываем геометрию…",
    "step:textures": "Загружаем текстуры и материалы…",
    "step:anim": "Проверяем анимационные данные…",
    "step:convert": "Преобразуем модель в формат glTF…",
    "step:preview": "Получаем изображение предпросмотра…",
    "step:done": "Обработка модели завершена.",
    "step:format": "Определён формат исходных данных: " + (parts[0] || "unknown"),
    "step:model": "Модель: " + (parts[0] || "Sketchfab") + (parts[1] ? " · автор: " + parts[1] : ""),
    "step:export": "Подготовлен файл модели: " + (parts[0] || "glTF"),
    "step:anim_count": "Найдено анимационных клипов: " + (parts[0] || "0"),
    "step:textures_warn": "Предупреждение при загрузке текстур: " + parts.join("|"),
    "step:anim_warn": "Предупреждение при обработке анимации: " + parts.join("|"),
    "step:convert_fail": "Один из способов преобразования не сработал; пробуем другой…"
  };
  return messages[step] || (step.startsWith("step:") ? "Этап: " + step.slice(5) : token);
}

app.use(express.json({ limit: "16kb" }));

function sendEvent(res, payload) {
  if (!res.writableEnded) res.write("data: " + JSON.stringify(payload) + "\n\n");
}

function extractSketchfabUid(rawUrl) {
  let parsed;
  try { parsed = new URL(rawUrl); }
  catch { throw new Error("Вставь корректную ссылку на страницу модели."); }

  const hostname = parsed.hostname.toLowerCase();
  if (parsed.protocol !== "https:" || !(hostname === "sketchfab.com" || hostname.endsWith(".sketchfab.com")) || parsed.username || parsed.password) {
    throw new Error("Разрешены только HTTPS-ссылки на Sketchfab и его поддомены.");
  }
  const match = parsed.pathname.match(/-([a-f0-9]{32})\/?$/i);
  if (!match) throw new Error("Не удалось найти ID модели в ссылке Sketchfab.");
  return match[1].toLowerCase();
}

async function readModelMetadata(uid) {
  try {
    const response = await fetch("https://api.sketchfab.com/v3/models/" + uid, { signal: AbortSignal.timeout(15000) });
    if (!response.ok) return {};
    const data = await response.json();
    return {
      name: data.name || "",
      author: data.user?.displayName || data.user?.username || "",
      license: data.license?.label || data.license?.slug || ""
    };
  } catch {
    return {};
  }
}

function runImporter(url, outputRoot, onProgress, onDiagnostic) {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, ["-u", IMPORT_SCRIPT, url, outputRoot], {
      cwd: IMPORTER_DIR,
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
      stdio: ["ignore", "pipe", "pipe"]
    });

    let stdoutBuffer = "";
    let stderrBuffer = "";
    let result = null;
    let settled = false;

    const fail = (error) => {
      if (settled) return;
      settled = true;
      reject(error instanceof Error ? error : new Error(String(error)));
    };

    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdoutBuffer += chunk;
      const lines = stdoutBuffer.split("\n");
      stdoutBuffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const event = JSON.parse(line);
          if (event.type === "progress") onProgress(event.message || "");
          else if (event.type === "result") result = event;
          else if (event.type === "error") fail(new Error(event.message || "Ошибка Python-импортёра."));
        } catch (error) {
          if (error instanceof SyntaxError) onDiagnostic("Импортёр: " + line.slice(0, 500));
          else fail(error);
        }
      }
    });

    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => {
      stderrBuffer += chunk;
      const lines = stderrBuffer.split("\n");
      stderrBuffer = lines.pop() || "";
      for (const line of lines) if (line.trim()) onDiagnostic(line.slice(0, 700));
    });

    child.on("error", (error) => fail(new Error("Не удалось запустить Python (" + PYTHON + "): " + error.message)));
    child.on("close", (code, signal) => {
      if (settled) return;
      if (stderrBuffer.trim()) onDiagnostic(stderrBuffer.slice(0, 700));
      if (stdoutBuffer.trim()) {
        try {
          const event = JSON.parse(stdoutBuffer);
          if (event.type === "result") result = event;
          else if (event.type === "error") return fail(new Error(event.message || "Ошибка Python-импортёра."));
          else if (event.type === "progress") onProgress(event.message || "");
        } catch {
          onDiagnostic("Импортёр завершил вывод без JSON: " + stdoutBuffer.slice(0, 500));
        }
      }
      if (code !== 0) return fail(new Error("Загрузчик завершился с кодом " + code + (signal ? " (" + signal + ")" : "") + "."));
      if (!result) return fail(new Error("Загрузчик завершился без результата импорта."));
      settled = true;
      resolve(result);
    });
  });
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
  const sourceUrl = String(req.body?.url || "").trim();
  try { uid = extractSketchfabUid(sourceUrl); }
  catch (error) { return res.status(400).json({ error: error.message }); }

  res.status(200);
  res.setHeader("Content-Type", "text/event-stream; charset=utf-8");
  res.setHeader("Cache-Control", "no-cache, no-transform");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders?.();

  let disconnected = false;
  req.on("close", () => { disconnected = true; });

  const log = (message, level = "info") => {
    if (!disconnected) sendEvent(res, { type: "log", message, level });
  };

  let workDir = null;
  let finalDir = null;
  try {
    log("Ссылка проверена · Sketchfab UID " + uid);
    log("Проверяем публичные сведения и лицензию модели…");
    const metadata = await readModelMetadata(uid);
    log("Подготовка локального загрузчика без API-токена…");

    await fs.mkdir(IMPORT_DIR, { recursive: true });
    workDir = await fs.mkdtemp(path.join(os.tmpdir(), "model-lab-import-"));
    const outputRoot = path.join(workDir, "output");
    await fs.mkdir(outputRoot, { recursive: true });

    const imported = await runImporter(
      sourceUrl,
      outputRoot,
      (message) => log(describeProgress(message)),
      (message) => log(message, "system")
    );

    if (disconnected) return;
    const sourceDir = path.resolve(imported.directory);
    const modelRelative = path.posix.normalize(String(imported.model_file || "").replace(/\\/g, "/"));
    if (!modelRelative || modelRelative.startsWith("../") || path.isAbsolute(modelRelative)) {
      throw new Error("Загрузчик вернул недопустимый путь к файлу модели.");
    }
    const sourceModel = path.resolve(sourceDir, modelRelative);
    if (!sourceModel.startsWith(sourceDir + path.sep)) throw new Error("Путь к модели выходит за пределы каталога импорта.");
    await fs.access(sourceModel);

    const folder = uid + "-" + crypto.randomBytes(4).toString("hex");
    finalDir = path.join(IMPORT_DIR, folder);
    await fs.cp(sourceDir, finalDir, { recursive: true, errorOnExist: true, force: false });

    const modelName = metadata.name || imported.name || path.parse(modelRelative).name;
    const author = metadata.author || imported.author || "Неизвестный автор";
    const license = metadata.license || "Проверь лицензию на странице модели";
    const model = {
      id: "import-" + folder,
      name: modelName,
      subtitle: "Импортировано из Sketchfab",
      url: "/models/imported/" + folder + "/" + modelRelative.split("/").map(encodeURIComponent).join("/"),
      author,
      license,
      credit: "Источник: Sketchfab. Автор: " + author + ". Лицензия: " + license + ".",
      source: "https://sketchfab.com/3d-models/" + uid,
      normalize: true
    };
    await fs.writeFile(path.join(finalDir, "model.json"), JSON.stringify(model, null, 2));
    log("Файл модели подготовлен: " + modelRelative, "success");
    log("Модель сохранена в public/models/imported/" + folder, "success");
    sendEvent(res, { type: "done", model });
    res.end();
  } catch (error) {
    if (finalDir) await fs.rm(finalDir, { recursive: true, force: true }).catch(() => {});
    sendEvent(res, { type: "error", message: error.message || "Неизвестная ошибка импорта." });
    res.end();
  } finally {
    if (workDir) await fs.rm(workDir, { recursive: true, force: true }).catch(() => {});
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
  console.log("[Model Lab] Sketchfab public-page importer enabled; Python: " + PYTHON);
});
