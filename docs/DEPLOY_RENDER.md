# Render deployment

## MongoDB Atlas
1. Create a cluster at https://www.mongodb.com/atlas/.
2. Create a database user and copy the SRV connection string.
3. Configure Network Access. For a prototype, 0.0.0.0/0 is convenient but less restrictive; use a strong database password and tighten access where possible.

## Cesium ion
1. Create an account at https://ion.cesium.com/.
2. Create a public access token with World Terrain and OSM Buildings asset access.
3. Set VITE_CESIUM_ION_TOKEN on the frontend build service. This token is visible to browsers; restrict it to your deployed domain and never use a private server token.
4. OSM Buildings are approximate building geometry and coverage varies. Moscow does not automatically get MSFS-quality photogrammetry. Higher fidelity requires licensed photogrammetry/3D Tiles data.

## Render API (Web Service)
- Root Directory: server
- Runtime: Node
- Build Command: npm install
- Start Command: npm start
- Health Check Path: /health
- Environment variables:
  - MONGODB_URI = MongoDB Atlas connection string
  - JWT_SECRET = long random secret (at least 32 random bytes)
  - CLIENT_ORIGIN = deployed frontend origin
  - NODE_ENV = production

## Render frontend (Static Site)
- Root Directory: client
- Build Command: npm install && npm run build
- Publish Directory: dist
- Environment variables:
  - VITE_API_URL = full API URL, e.g. https://my-subscribers-api.onrender.com
  - VITE_CESIUM_ION_TOKEN = restricted Cesium ion token
- Add SPA rewrite: source /*, destination /index.html, action Rewrite.

## Local development
At repository root run npm install and npm run install:all. Copy server/.env.example to server/.env and fill in MongoDB URI and JWT secret. Copy client/.env.example to client/.env. Run npm run dev.

## Version I limitations
The first-person controller currently moves the Cesium camera, not a collision-aware animated humanoid. Terrain height sampling, gravity, collisions, footsteps and a full third-person rig remain future work. Roblox username resolution and avatar thumbnails are supported; the public thumbnail API returns an image, not a downloadable rig. A real 3D Roblox avatar requires an authorized/licensed rig export or conversion pipeline. Never scrape Roblox sessions or store Roblox passwords/cookies.
