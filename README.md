# My Subscribers — Version I

A first-person 3D exploration game set in Moscow, built with React, CesiumJS, Express, and MongoDB.

## Development

- `npm install`
- `npm run install:all`
- Copy `server/.env.example` to `server/.env` and configure MongoDB/JWT/Cesium.
- `npm run dev`

See `docs/DEPLOY_RENDER.md` for deployment.

## Notes

Cesium World Terrain and OSM Buildings provide terrain and building geometry. Photorealistic building coverage varies by location and is not guaranteed in Moscow. Roblox username lookup uses Roblox public APIs; public thumbnail APIs provide images, not a guaranteed downloadable rig. The initial avatar renderer uses the Roblox avatar thumbnail on a simple 3D character until a licensed GLB pipeline is configured.
