# My Subscribers — Model Lab

Standalone 3D inspection space with a local Sketchfab import service.

## Local development

\`\`\`bash
cd model-lab
npm install
npm run dev
\`\`\`

The dev command starts the Vite UI and the local Node API together. The API listens on port 4174; Vite proxies \`/api\` and imported model assets to it.

## Sketchfab import

1. Create a Sketchfab API token in your Sketchfab account.
2. In Codespaces, add it as a secret named \`SKETCHFAB_API_TOKEN\`.
3. Restart the Codespace or reload its environment, then run \`npm run dev\` from \`model-lab\`.
4. Paste a Sketchfab model page URL in the importer.

The server uses the Sketchfab v3 model metadata and Download API. The token is only read by the server and is never sent to the browser. The model must be downloadable by the authenticated Sketchfab account.

Imported archives are extracted to \`public/models/imported\`. The importer currently accepts Sketchfab model-page URLs and GLTF/GLB archives, with basic archive path and expanded-size checks. It is an experimental prototype, not a hardened public upload service.

## Render

The Model Lab is now a Node web service (not a static site), because imports need a server-side process and writable storage.

- Root Directory: \`model-lab\`
- Build Command: \`npm install && npm run build\`
- Start Command: \`npm start\`
- Environment variable: \`SKETCHFAB_API_TOKEN\` (secret)

Note: Render's normal service filesystem is ephemeral. Imported models may disappear on redeploy/restart unless persistent storage is configured. For a later durable setup, use a persistent disk or object storage.

## Included assets

- **Ostankino Tower** by **Arslan Timkanov**, Sketchfab, **CC BY 4.0**: https://sketchfab.com/3d-models/ostankino-tower-80ba20d862e24d849eabded16ef2443c
- **Cesium Man**, © 2017 Cesium, **CC BY 4.0**: https://github.com/KhronosGroup/glTF-Sample-Assets/tree/main/Models/CesiumMan
