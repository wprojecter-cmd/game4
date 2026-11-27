// @ts-check
/**
 * Explosions for a three.js scene: a flash, a rolling fireball, smoke, sparks, embers, a shockwave
 * across the ground, a scorch mark, dust, and chunks of debris that bounce and cool.
 *
 * A fixed pool of blasts is made once. `explode()` writes one blast's start into its slot, and the
 * GPU works out every particle's path from there, so the fire, smoke and sparks of every blast draw
 * in one call and a frame allocates nothing. The debris is one instanced mesh moved on the CPU.
 *
 * Made by Tyrbo (https://tyrbo.ai) and released under CC0 1.0: use it in any game, no credit needed.
 */
import {
  Color,
  CustomBlending,
  DataTexture,
  DynamicDrawUsage,
  Euler,
  Float32BufferAttribute,
  Group,
  IcosahedronGeometry,
  InstancedBufferAttribute,
  InstancedBufferGeometry,
  InstancedMesh,
  LinearFilter,
  LinearMipmapLinearFilter,
  Matrix4,
  Mesh,
  MeshStandardMaterial,
  OneFactor,
  OneMinusSrcAlphaFactor,
  PointLight,
  Quaternion,
  RepeatWrapping,
  ShaderMaterial,
  UniformsLib,
  UniformsUtils,
  Vector3,
} from 'three';

/**
 * @typedef {object} ExplosionOptions
 * @property {number} [slots] Blasts that can be alive at once; the oldest is reused.
 * @property {number} [debris] Chunks of debris a blast throws; 0 for none.
 * @property {number} [ground] Height of the ground: shockwave, scorch, dust and bouncing debris
 *   happen only for a blast near it.
 * @property {number} [light] Peak intensity of the flash's point light; 0 for none.
 * @property {boolean} [scorch] Leave a burn mark on the ground.
 * @property {number} [ambient] How lit the smoke and dust are: 1 in daylight, lower at night.
 * @property {import('three').ColorRepresentation} [hot] Colour of the hottest fire and the flash.
 * @property {import('three').ColorRepresentation} [fire] Colour of the fire.
 * @property {import('three').ColorRepresentation} [smoke] Colour of the smoke.
 * @property {import('three').ColorRepresentation} [dust] Colour of the dust thrown off the ground.
 * @property {import('three').ColorRepresentation} [rock] Colour of the debris.
 * @property {number} [seed] Makes it repeatable; a random one by default.
 */

/** The defaults. A blast of size 1 has a fireball about two units across. */
export const EXPLOSION_DEFAULTS = Object.freeze({
  slots: 6,
  debris: 10,
  ground: 0,
  light: 60,
  scorch: true,
  ambient: 1,
  hot: '#fff2c0',
  fire: '#ff7418',
  smoke: '#38332f',
  dust: '#6f6253',
  rock: '#4a433d',
});

/**
 * The settings explosions are made with: the defaults, then the options over them.
 * @param {ExplosionOptions} [options]
 */
export function explosionSettings(options = {}) {
  const own = Object.fromEntries(Object.entries(options).filter(([, v]) => v !== undefined));
  return { ...EXPLOSION_DEFAULTS, seed: Math.random() * 1e6, ...own };
}

/** The particles of one blast, by kind, in the order they draw: what is behind first. */
export const BLAST_PARTS = Object.freeze([
  ['scorch', 1],
  ['ring', 1],
  ['dust', 14],
  ['smoke', 20],
  ['fire', 26],
  ['spark', 28],
  ['ember', 10],
  ['flash', 1],
]);
const PER_BLAST = BLAST_PARTS.reduce((sum, [, count]) => sum + Number(count), 0);
/** Seconds a chunk of debris lasts before it sinks away. */
const DEBRIS_LIFE = 4;

/**
 * A small random number source, so a seed repeats exactly.
 * @param {number} seed
 */
function random(seed) {
  let s = Math.floor(seed) >>> 0 || 1;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** @param {number} t */
const smooth = (t) => t * t * (3 - 2 * t);

/**
 * A tileable noise picture: four independent layers of soft value noise, one in each channel.
 * @param {() => number} rand
 * @param {number} [size]
 */
function noiseTexture(rand, size = 64) {
  const data = new Uint8Array(size * size * 4);
  for (let channel = 0; channel < 4; channel += 1) {
    const layers = [4, 8, 16].map((cells) => ({
      cells,
      grid: Float32Array.from({ length: cells * cells }, rand),
    }));
    for (let y = 0; y < size; y += 1)
      for (let x = 0; x < size; x += 1) {
        let value = 0;
        let weight = 0.55;
        for (const { cells, grid } of layers) {
          const gx = (x / size) * cells;
          const gy = (y / size) * cells;
          const x0 = Math.floor(gx);
          const y0 = Math.floor(gy);
          const fx = smooth(gx - x0);
          const fy = smooth(gy - y0);
          const at = (/** @type {number} */ i, /** @type {number} */ j) =>
            grid[((j % cells) * cells + (i % cells)) % grid.length] ?? 0;
          const top = at(x0, y0) + (at(x0 + 1, y0) - at(x0, y0)) * fx;
          const bottom = at(x0, y0 + 1) + (at(x0 + 1, y0 + 1) - at(x0, y0 + 1)) * fx;
          value += (top + (bottom - top) * fy) * weight;
          weight *= 0.5;
        }
        data[(y * size + x) * 4 + channel] = Math.round(Math.min(1, value / 0.9625) * 255);
      }
  }
  const texture = new DataTexture(data, size, size);
  texture.wrapS = RepeatWrapping;
  texture.wrapT = RepeatWrapping;
  texture.magFilter = LinearFilter;
  texture.minFilter = LinearMipmapLinearFilter;
  texture.generateMipmaps = true;
  texture.needsUpdate = true;
  return texture;
}

const VERTEX = /* glsl */ `
#include <fog_pars_vertex>
attribute vec4 aSeed;
attribute float aKind;
attribute vec4 aBlast; // where the blast was, and when
attribute float aPower; // how big it was
uniform float uTime;
uniform float uGround;
uniform vec3 uHot;
uniform vec3 uFire;
uniform vec3 uSmoke;
uniform vec3 uDust;
uniform float uScorch;
varying vec2 vUv;
varying vec4 vColor;
varying float vCover;
varying float vKind;
varying vec4 vInfo;

// Where something thrown at v is after t seconds, slowed by drag k and pulled down by g.
vec3 thrown(vec3 v, float k, float g, float t) {
  float e = (1.0 - exp(-k * t)) / k;
  return v * e - vec3(0.0, g, 0.0) * (t - e) / k;
}

// 1 while 0 <= k < 1: the part is alive.
float alive(float k) { return step(0.0, k) * step(k, 0.999); }

void main() {
  float s = aPower;
  float t = uTime - aBlast.w;
  vec3 o = aBlast.xyz;
  float scale = length(modelMatrix[0].xyz);
  float low = 1.0 - step(0.8 * s, o.y - uGround);
  vec3 center = o;
  vec3 tail = o;
  vec2 size = vec2(0.0);
  float angle = 0.0;
  bool lying = false;
  bool streak = false;
  vUv = position.xy + 0.5;
  vColor = vec4(0.0);
  vCover = 0.0;
  vKind = aKind;
  vInfo = vec4(0.0);

  if (aKind < 0.5) {
    // The scorch mark left on the ground.
    lying = true;
    center = vec3(o.x, uGround + 0.012, o.z);
    size = vec2(s * 2.8);
    angle = aSeed.x * 6.2831853;
    float a = smoothstep(0.03, 0.2, t) * (1.0 - smoothstep(3.5, 7.0, t)) * step(0.0, t);
    vColor = vec4(vec3(0.02, 0.016, 0.012), a * 0.85 * low * uScorch);
    vCover = 1.0;
    vInfo = vec4(aSeed.yz, 0.0, 0.0);
  } else if (aKind < 1.5) {
    // The shockwave racing out across the ground.
    float k = t / 0.6;
    lying = true;
    center = vec3(o.x, uGround + 0.03, o.z);
    size = vec2(s * 8.0 * (1.0 - pow(1.0 - clamp(k, 0.0, 1.0), 2.2)));
    vColor = vec4(mix(uHot, uFire, k) * 0.9, (1.0 - k) * (1.0 - k) * alive(k) * low);
    vInfo = vec4(aSeed.yz, k, 0.0);
  } else if (aKind < 2.5) {
    // Dust thrown out along the ground, rising as it rolls.
    float k = t / mix(1.4, 2.4, aSeed.x);
    float around = (aSeed.y + float(gl_InstanceID) * 0.618) * 6.2831853;
    vec3 way = vec3(cos(around), 0.0, sin(around));
    center = vec3(o.x, uGround, o.z) + way * s * mix(1.8, 3.6, aSeed.z) * (1.0 - exp(-t * 2.8));
    center.y += s * (0.25 + k * 0.45);
    size = vec2(s * mix(1.4, 2.2, aSeed.w) * (0.5 + k * 0.9));
    angle = aSeed.z * 6.2831853 + k;
    vColor = vec4(uDust, smoothstep(0.0, 0.1, k) * (1.0 - smoothstep(0.25, 1.0, k)) * 0.5 * low * alive(k));
    vCover = 1.0;
    vInfo = vec4(aSeed.zw, k, 0.0);
  } else if (aKind < 3.5) {
    // Smoke: rolls out behind the fire, rises and swells.
    float tt = t - mix(0.08, 0.3, aSeed.x);
    float life = mix(3.4, 5.0, aSeed.y);
    float k = tt / life;
    vec3 way = normalize(vec3(aSeed.z - 0.5, aSeed.w * 0.8 + 0.25, fract(aSeed.z * 7.13) - 0.5));
    center = o + thrown(way * s * mix(3.0, 6.5, aSeed.w), 3.5, 0.0, max(tt, 0.0));
    center.y += s * (0.6 + 0.6 * max(tt, 0.0) * mix(0.7, 1.3, aSeed.x));
    size = vec2(s * mix(1.3, 2.0, aSeed.x) * (0.6 + sqrt(max(k, 0.0)) * 1.4));
    angle = aSeed.w * 6.2831853 + k * (aSeed.x - 0.5) * 3.0;
    vColor = vec4(uSmoke, smoothstep(0.0, 0.06, k) * (1.0 - smoothstep(0.4, 1.0, k)) * 0.95 * alive(k));
    vCover = 1.0;
    vInfo = vec4(aSeed.zw, k, exp(-max(tt, 0.0) * 4.5));
  } else if (aKind < 4.5) {
    // The fireball: puffs flung out and stopped by the air, burning from white to deep red.
    float k = t / mix(0.5, 1.0, aSeed.x);
    vec3 way = normalize(vec3(aSeed.y - 0.5, aSeed.z * 0.9 + 0.05, aSeed.w - 0.5) + 1e-4);
    center = o + thrown(way * s * mix(4.0, 8.5, aSeed.x), 6.5, 0.0, max(t, 0.0));
    center.y += s * (0.35 + 0.9 * max(t, 0.0));
    size = vec2(s * mix(1.0, 1.6, aSeed.w) * (0.35 + 0.65 * (1.0 - pow(1.0 - clamp(k, 0.0, 1.0), 3.0))));
    angle = aSeed.z * 6.2831853;
    vColor = vec4(1.0, 1.0, 1.0, (1.0 - smoothstep(0.5, 1.0, k)) * alive(k));
    vCover = 0.55;
    vInfo = vec4(aSeed.yz, k, 1.0 - k);
  } else if (aKind < 5.5) {
    // A spark: a white-hot streak that arcs down and skids along the ground.
    float k = t / mix(0.5, 1.2, aSeed.x);
    vec3 way = normalize(vec3(aSeed.y - 0.5, aSeed.z * 0.9 + 0.08, aSeed.w - 0.5) + 1e-4);
    vec3 v = way * s * mix(7.0, 15.0, aSeed.z);
    center = o + thrown(v, 1.6, 9.8 * s, max(t, 0.0));
    tail = o + thrown(v, 1.6, 9.8 * s, max(t - 0.045, 0.0));
    center.y = max(center.y, uGround + 0.02);
    tail.y = max(tail.y, uGround + 0.02);
    streak = true;
    size = vec2(s * 0.07);
    vColor = vec4(mix(uHot * 2.2, uFire * 1.6, sqrt(k)), (1.0 - k) * alive(k));
  } else if (aKind < 6.5) {
    // An ember: a glowing bit tossed up that falls slowly and winks.
    float k = t / mix(1.2, 2.2, aSeed.x);
    vec3 way = normalize(vec3(aSeed.y - 0.5, aSeed.z + 0.6, aSeed.w - 0.5));
    center = o + thrown(way * s * mix(3.0, 7.0, aSeed.w), 0.9, 4.0 * s, max(t, 0.0));
    center.y = max(center.y, uGround + 0.02);
    size = vec2(s * 0.08 * (1.0 - k * 0.5));
    float twinkle = 0.6 + 0.4 * sin(uTime * 25.0 + aSeed.y * 40.0);
    vColor = vec4(mix(uHot, uFire, k) * 2.2, (1.0 - smoothstep(0.6, 1.0, k)) * twinkle * alive(k));
  } else {
    // The flash: blinding for a moment, gone in a quarter of a second.
    float k = t / 0.3;
    center = o + vec3(0.0, s * 0.35, 0.0);
    size = vec2(s * mix(2.4, 5.5, sqrt(clamp(k, 0.0, 1.0))));
    vColor = vec4(uHot * 2.4, (1.0 - k) * (1.0 - k) * alive(k));
  }

  if (vColor.a <= 0.0) {
    gl_Position = vec4(0.0, 0.0, 2.0, 1.0);
    return;
  }
  vec4 mvPosition;
  vec2 corner = mat2(cos(angle), sin(angle), -sin(angle), cos(angle)) * (position.xy * size);
  if (lying) {
    // Flat on the ground, wound to face up.
    mvPosition = modelViewMatrix * vec4(center + vec3(corner.x, 0.0, -corner.y), 1.0);
  } else if (streak) {
    vec4 head = modelViewMatrix * vec4(center, 1.0);
    vec4 back = modelViewMatrix * vec4(tail, 1.0);
    vec2 along = head.xy - back.xy;
    float len = length(along);
    vec2 dir = len > 1e-5 ? along / len : vec2(0.0, 1.0);
    float w = size.x * scale;
    mvPosition = mix(back, head, position.y + 0.5);
    // Across is the way out turned clockwise, so the quad keeps its winding and faces the camera.
    mvPosition.xy += dir * position.y * w * 2.0 + vec2(dir.y, -dir.x) * position.x * w;
  } else {
    mvPosition = modelViewMatrix * vec4(center, 1.0);
    mvPosition.xy += corner * scale;
  }
  gl_Position = projectionMatrix * mvPosition;
  #include <fog_vertex>
}
`;

const FRAGMENT = /* glsl */ `
#include <fog_pars_fragment>
uniform sampler2D uNoise;
uniform vec3 uHot;
uniform vec3 uFire;
uniform vec3 uSmoke;
uniform float uAmbient;
varying vec2 vUv;
varying vec4 vColor;
varying float vCover;
varying float vKind;
varying vec4 vInfo;

// Fire's colour by its heat: deep red, orange, yellow, then white.
vec3 burn(float h) {
  vec3 c = mix(vec3(0.22, 0.03, 0.01), uFire, smoothstep(0.0, 0.45, h));
  c = mix(c, uHot * 1.5, smoothstep(0.45, 0.85, h));
  return mix(c, vec3(3.0), smoothstep(0.9, 1.3, h));
}

void main() {
  vec2 p = vUv - 0.5;
  float d = length(p) * 2.0;
  float a = vColor.a;
  vec3 color = vColor.rgb;
  if (vKind < 0.5) {
    float n = texture2D(uNoise, vUv * 0.7 + vInfo.xy).r;
    a *= smoothstep(1.0, 0.35, d + (n - 0.5) * 0.6);
  } else if (vKind < 1.5) {
    float n = texture2D(uNoise, vec2(atan(p.y, p.x) * 0.4, vInfo.z * 0.3) + vInfo.xy).g;
    float off = (d - 0.88) / 0.045;
    float front = exp(-off * off) * (0.3 + n * 1.1);
    a *= front + smoothstep(0.9, 0.2, d) * 0.08;
  } else if (vKind < 3.5) {
    // Dust and smoke: noise-roughened puffs, lit from above; young smoke glows from the fire.
    float n = texture2D(uNoise, vUv * 0.55 + vInfo.xy).r;
    float m = texture2D(uNoise, vUv * 1.2 - vInfo.yx).g;
    float body = (1.0 - d * d) * (0.45 + n * 0.85 + m * 0.4) - 0.47 - vInfo.z * 0.35;
    a *= smoothstep(0.0, 0.32, body);
    float lit = clamp(vUv.y * 0.9 + (n - 0.5) * 0.5, 0.0, 1.0);
    color = color * mix(0.35, 1.15, lit) * uAmbient + uFire * vInfo.w * (1.4 - vUv.y) * 1.2;
  } else if (vKind < 4.5) {
    float n = texture2D(uNoise, vUv * 0.6 + vInfo.xy).r;
    float m = texture2D(uNoise, vUv * 1.3 - vInfo.yx * 2.0).b;
    float body = (1.0 - d * d) * (0.5 + n * 0.8 + m * 0.35) - 0.38 - vInfo.z * 0.5;
    a *= smoothstep(0.0, 0.45, body);
    color = burn(vInfo.w * 1.05 + (n - 0.5) * 0.55 + (m - 0.5) * 0.3);
  } else if (vKind < 5.5) {
    float across = abs(vUv.x - 0.5) * 2.0;
    a *= (1.0 - across * across) * mix(0.25, 1.0, vUv.y);
  } else if (vKind < 6.5) {
    a *= pow(max(1.0 - d, 0.0), 2.0);
  } else {
    a *= pow(max(1.0 - d, 0.0), 2.6);
  }
  if (a < 0.002) discard;
  #ifdef TONE_MAPPING
    color = toneMapping(color);
  #endif
  vec3 c = linearToOutputTexel(vec4(color, 1.0)).rgb;
  // Light adds like a screen blend: it lifts a dark scene and cannot wash out a bright one.
  float cover = max(vCover, min(1.0, max(c.r, max(c.g, c.b))));
  gl_FragColor = vec4(c * a, a * cover);
  #ifdef USE_FOG
    #ifdef FOG_EXP2
      float fogFactor = 1.0 - exp(-fogDensity * fogDensity * vFogDepth * vFogDepth);
    #else
      float fogFactor = smoothstep(fogNear, fogFar, vFogDepth);
    #endif
    gl_FragColor *= 1.0 - fogFactor;
  #endif
}
`;

/**
 * Glowing debris: three's standard material with each chunk's heat added as light.
 * @param {import('three').ColorRepresentation} rock
 */
function debrisMaterial(rock) {
  const material = new MeshStandardMaterial({ color: rock, roughness: 0.9, flatShading: true });
  material.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace(
        '#include <common>',
        '#include <common>\nattribute float aHeat;\nvarying float vHeat;'
      )
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvHeat = aHeat;');
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', '#include <common>\nvarying float vHeat;')
      .replace(
        '#include <emissivemap_fragment>',
        '#include <emissivemap_fragment>\ntotalEmissiveRadiance += vec3(1.6, 0.32, 0.05) * vHeat * vHeat * vHeat;'
      );
  };
  return material;
}

/**
 * Makes a pool of explosions and adds it to `scene`. Set one off with `explode()`.
 * @param {import('three').Object3D} scene
 * @param {ExplosionOptions} [options]
 */
export function createExplosions(scene, options = {}) {
  const settings = explosionSettings(options);
  const rand = random(settings.seed);
  const slots = Math.max(1, Math.floor(settings.slots));
  const total = slots * PER_BLAST;

  const seeds = new Float32Array(total * 4).map(() => rand());
  const kinds = new Float32Array(total);
  for (let slot = 0, i = 0; slot < slots; slot += 1)
    BLAST_PARTS.forEach(([, count], kind) => {
      for (let n = 0; n < Number(count); n += 1, i += 1) kinds[i] = kind;
    });
  const blasts = new Float32Array(total * 4);
  for (let i = 0; i < total; i += 1) blasts[i * 4 + 3] = -1e6;
  const powers = new Float32Array(total).fill(1);

  const geometry = new InstancedBufferGeometry();
  geometry.setAttribute(
    'position',
    new Float32BufferAttribute([-0.5, -0.5, 0, 0.5, -0.5, 0, 0.5, 0.5, 0, -0.5, 0.5, 0], 3)
  );
  geometry.setIndex([0, 1, 2, 0, 2, 3]);
  geometry.setAttribute('aSeed', new InstancedBufferAttribute(seeds, 4));
  geometry.setAttribute('aKind', new InstancedBufferAttribute(kinds, 1));
  const blastAttribute = new InstancedBufferAttribute(blasts, 4).setUsage(DynamicDrawUsage);
  const powerAttribute = new InstancedBufferAttribute(powers, 1).setUsage(DynamicDrawUsage);
  geometry.setAttribute('aBlast', blastAttribute);
  geometry.setAttribute('aPower', powerAttribute);
  geometry.instanceCount = total;

  const noise = noiseTexture(rand);
  const uniforms = {
    ...UniformsUtils.clone(UniformsLib.fog),
    uTime: { value: 0 },
    uGround: { value: settings.ground },
    uScorch: { value: settings.scorch ? 1 : 0 },
    uAmbient: { value: settings.ambient },
    uHot: { value: new Color(settings.hot) },
    uFire: { value: new Color(settings.fire) },
    uSmoke: { value: new Color(settings.smoke) },
    uDust: { value: new Color(settings.dust) },
    uNoise: { value: noise },
  };
  const material = new ShaderMaterial({
    uniforms,
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    transparent: true,
    depthWrite: false,
    fog: true,
    blending: CustomBlending,
    blendSrc: OneFactor,
    blendDst: OneMinusSrcAlphaFactor,
  });
  const object = new Group();
  object.name = 'explosions';
  const particles = new Mesh(geometry, material);
  // Blasts go off anywhere, so the camera never culls the pool by a box.
  particles.frustumCulled = false;
  object.add(particles);

  // Debris: chunks in a ring of slots, moved here each frame while they fly.
  const chunks = slots * Math.max(0, Math.floor(settings.debris));
  const rockGeometry = new IcosahedronGeometry(1, 0);
  const heat = new Float32Array(Math.max(1, chunks));
  const heatAttribute = new InstancedBufferAttribute(heat, 1).setUsage(DynamicDrawUsage);
  rockGeometry.setAttribute('aHeat', heatAttribute);
  const rockMaterial = debrisMaterial(settings.rock);
  const rocks = new InstancedMesh(rockGeometry, rockMaterial, Math.max(1, chunks));
  rocks.instanceMatrix.setUsage(DynamicDrawUsage);
  rocks.frustumCulled = false;
  rocks.visible = chunks > 0;
  const hidden = new Matrix4().makeScale(0, 0, 0);
  for (let i = 0; i < rocks.count; i += 1) rocks.setMatrixAt(i, hidden);
  object.add(rocks);
  // Each chunk's place, speed, spin and turn (three numbers each), then its size and its pull.
  const [place, speed, spin, turn] = [0, 1, 2, 3].map(() => new Float32Array(chunks * 3));
  const [sizes, pull] = [0, 1].map(() => new Float32Array(chunks));
  const age = new Float32Array(chunks).fill(DEBRIS_LIFE);
  const matrix = new Matrix4();
  const rotation = new Quaternion();
  const euler = new Euler();
  const at = new Vector3();
  const scaleBy = new Vector3();

  const light = settings.light > 0 ? new PointLight(settings.fire, 0, 0, 2) : null;
  if (light) object.add(light);
  scene.add(object);

  let time = 0;
  let next = 0;
  let nextChunk = 0;
  let flashAt = -1e6;
  let flashPower = 1;
  return {
    /** The group holding every blast; leave it at the origin unless the blasts should move with something. */
    object,
    /** The flash's point light, or null. */
    light,
    /**
     * Sets off a blast at `position`, a point in the world.
     * @param {import('three').Vector3Like} position
     * @param {number} [size] How big: 1 is a fireball about two units across.
     */
    explode(position, size = 1) {
      object.updateWorldMatrix(true, false);
      object.worldToLocal(at.set(position.x, position.y, position.z));
      const first = next * PER_BLAST;
      next = (next + 1) % slots;
      for (let i = first; i < first + PER_BLAST; i += 1) {
        blasts[i * 4] = at.x;
        blasts[i * 4 + 1] = at.y;
        blasts[i * 4 + 2] = at.z;
        blasts[i * 4 + 3] = time;
        powers[i] = size;
      }
      blastAttribute.needsUpdate = true;
      powerAttribute.needsUpdate = true;
      flashAt = time;
      flashPower = size;
      if (light) light.position.set(at.x, at.y + size * 0.6, at.z);
      for (let n = 0; n < Math.floor(settings.debris) && chunks > 0; n += 1) {
        const i = nextChunk;
        nextChunk = (nextChunk + 1) % chunks;
        const around = rand() * Math.PI * 2;
        const out = (2 + rand() * 4) * size;
        const k = i * 3;
        place[k] = at.x;
        place[k + 1] = at.y + 0.2 * size;
        place[k + 2] = at.z;
        speed[k] = Math.cos(around) * out;
        speed[k + 1] = (4 + rand() * 6) * size;
        speed[k + 2] = Math.sin(around) * out;
        for (let j = 0; j < 3; j += 1) {
          spin[k + j] = rand() * 12 - 6;
          turn[k + j] = rand() * 6;
        }
        sizes[i] = (0.04 + rand() * 0.07) * size;
        pull[i] = 9.8 * size;
        age[i] = 0;
      }
    },
    /**
     * Moves every blast on by `dt` seconds. Call it every frame.
     * @param {number} dt
     */
    update(dt) {
      time += dt;
      uniforms.uTime.value = time;
      if (light) {
        const since = time - flashAt;
        light.intensity =
          settings.light * flashPower * (Math.exp(-since * 9) + 0.25 * Math.exp(-since * 2.2));
      }
      if (chunks === 0) return;
      const ground = settings.ground;
      let moved = false;
      for (let i = 0; i < chunks; i += 1) {
        if ((age[i] ?? DEBRIS_LIFE) >= DEBRIS_LIFE) continue;
        moved = true;
        const life = (age[i] ?? 0) + dt;
        age[i] = life;
        const r = sizes[i] ?? 0;
        const k = i * 3;
        let vy = (speed[k + 1] ?? 0) - (pull[i] ?? 0) * dt;
        let x = (place[k] ?? 0) + (speed[k] ?? 0) * dt;
        let y = (place[k + 1] ?? 0) + vy * dt;
        let z = (place[k + 2] ?? 0) + (speed[k + 2] ?? 0) * dt;
        if (y < ground + r) {
          // A bounce: most of the speed is lost to the ground, and the spin with it.
          y = ground + r;
          vy = Math.abs(vy) > 0.6 ? -vy * 0.35 : 0;
          speed[k] = (speed[k] ?? 0) * 0.55;
          speed[k + 2] = (speed[k + 2] ?? 0) * 0.55;
          for (let j = 0; j < 3; j += 1) spin[k + j] = (spin[k + j] ?? 0) * 0.5;
        }
        speed[k + 1] = vy;
        place[k] = x;
        place[k + 1] = y;
        place[k + 2] = z;
        for (let j = 0; j < 3; j += 1) turn[k + j] = (turn[k + j] ?? 0) + (spin[k + j] ?? 0) * dt;
        // It sinks away at the end of its life.
        const shown = r * Math.min(1, (DEBRIS_LIFE - life) / 0.6);
        euler.set(turn[k] ?? 0, turn[k + 1] ?? 0, turn[k + 2] ?? 0);
        rotation.setFromEuler(euler);
        matrix.compose(at.set(x, y, z), rotation, scaleBy.set(shown, shown * 0.75, shown));
        rocks.setMatrixAt(i, life >= DEBRIS_LIFE ? hidden : matrix);
        heat[i] = Math.max(0, 1 - life / 1.4);
      }
      if (moved) {
        rocks.instanceMatrix.needsUpdate = true;
        heatAttribute.needsUpdate = true;
      }
    },
    /** Takes every blast out of the scene and frees their GPU memory. */
    dispose() {
      object.removeFromParent();
      geometry.dispose();
      material.dispose();
      noise.dispose();
      rockGeometry.dispose();
      rockMaterial.dispose();
      rocks.dispose();
      light?.dispose();
    },
  };
}
