# My Subscribers — Model Lab

Standalone static 3D inspection space, isolated from the main game and API.

## Local development

```bash
cd model-lab
npm install
npm run dev
```

## Production build

```bash
npm run build
```

## Ostankino Tower asset

Place the downloaded file at:

`public/models/ostankino/Ostankino_Tower.gltf`

The model currently has its geometry embedded as a Base64 data URI, so no external BIN file is required. Keep the original preview JPG separately if desired.

Credit: **Ostankino Tower** by **Arslan Timkanov**, Sketchfab, **CC BY 4.0**. Source: https://sketchfab.com/3d-models/ostankino-tower-80ba20d862e24d849eabded16ef2443c

## Second public model

The viewer also includes **Cesium Man**, a local GLB from the Khronos glTF Sample Assets repository. Credit: © 2017 Cesium; provided by Cesium for Everything. **CC BY 4.0**. The Cesium logo/trademark is excluded from the model license. Model and license: https://github.com/KhronosGroup/glTF-Sample-Assets/tree/main/Models/CesiumMan

## Render Static Site

- Root Directory: `model-lab`
- Build Command: `npm install && npm run build`
- Publish Directory: `dist`
- Branch: `feature/model-lab-3d-space`

No backend, database, API key, or environment variables are required.
