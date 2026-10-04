// ─────────────────────────────────────────────────────────────────────────────
// vault.ts — The Keeper, drawn with three.js: a living orb whose eye follows
// you, inside a vault dial you can grab and spin. It ripples while you type,
// spins up while thinking, flashes red on errors and bursts open on a breach.
// Decorative only (the canvas is aria-hidden): the chat works without WebGL.
// ─────────────────────────────────────────────────────────────────────────────

import { animate } from "animejs";
import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";

export type Layout = "gate" | "console";

export interface Vault {
  frame(layout: Layout): void;
  thinking(on: boolean): void;
  speak(ms: number): void;
  keystroke(): void;
  alarm(): void;
  breach(instant?: boolean): void;
  seal(): void;
}

const color = (hex: string) => new THREE.Color(hex);
const MOODS = {
  calm: [color("#22d3ee"), color("#7c3aed")],
  think: [color("#f0abfc"), color("#6d28d9")],
  alarm: [color("#f87171"), color("#991b1b")],
  open: [color("#fbbf24"), color("#b45309")],
};

// 3D simplex noise, Ashima Arts / Stefan Gustavson (MIT).
const NOISE = /* glsl */ `
vec3 mod289(vec3 x){return x-floor(x*(1./289.))*289.;}
vec4 mod289(vec4 x){return x-floor(x*(1./289.))*289.;}
vec4 permute(vec4 x){return mod289(((x*34.)+1.)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1./6.,1./3.);const vec4 D=vec4(0.,.5,1.,2.);
  vec3 i=floor(v+dot(v,C.yyy));vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz);vec3 l=1.-g;vec3 i1=min(g.xyz,l.zxy);vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx;vec3 x2=x0-i2+C.yyy;vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.,i1.z,i2.z,1.))+i.y+vec4(0.,i1.y,i2.y,1.))+i.x+vec4(0.,i1.x,i2.x,1.));
  vec3 ns=.142857142857*D.wyz-D.xzx;
  vec4 j=p-49.*floor(p*ns.z*ns.z);vec4 x_=floor(j*ns.z);vec4 y_=floor(j-7.*x_);
  vec4 x=x_*ns.x+ns.yyyy;vec4 y=y_*ns.x+ns.yyyy;vec4 h=1.-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy);vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.+1.;vec4 s1=floor(b1)*2.+1.;vec4 sh=-step(h,vec4(0.));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x);vec3 p1=vec3(a0.zw,h.y);vec3 p2=vec3(a1.xy,h.z);vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
  vec4 m=max(.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.);m=m*m;
  return 42.*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}`;

/** Returns null when WebGL is unavailable; the page then keeps its CSS backdrop. */
export function createVault(canvas: HTMLCanvasElement): Vault | null {
  let renderer: THREE.WebGLRenderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
  } catch {
    return null;
  }
  const motion = matchMedia("(prefers-reduced-motion: reduce)").matches ? 0.2 : 1;
  const narrow = () => innerWidth < 820;

  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  const scene = new THREE.Scene();
  scene.background = color("#05060d");
  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
  const composer = new EffectComposer(renderer);
  const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.8, 0.45, 0.18);
  composer.addPass(new RenderPass(scene, camera));
  composer.addPass(bloom);
  composer.addPass(new OutputPass());

  const s = { time: 0, phase: 0, energy: 0, typing: 0, thinking: false, speakUntil: 0, alarmUntil: 0, open: 0, burst: 0, shake: 0, spin: 0 };
  const rig = new THREE.Group();
  scene.add(rig);

  // The orb: noise-displaced sphere with a fresnel rim, brighter and wilder as energy rises.
  const orbUniforms = { uTime: { value: 0 }, uEnergy: { value: 0 }, uA: { value: color("#fff") }, uB: { value: color("#fff") } };
  const orb = new THREE.Mesh(
    new THREE.IcosahedronGeometry(1, 40),
    new THREE.ShaderMaterial({
      uniforms: orbUniforms,
      vertexShader: NOISE + /* glsl */ `
        uniform float uTime;
        uniform float uEnergy;
        varying vec3 vNormal;
        varying vec3 vView;
        varying float vNoise;
        void main() {
          float n = snoise(normal * 1.4 + uTime * 0.3) + 0.45 * snoise(normal * 3.6 - uTime * 0.8);
          vec4 mv = modelViewMatrix * vec4(position + normal * n * (0.06 + uEnergy * 0.24), 1.0);
          vNoise = n;
          vNormal = normalMatrix * normal;
          vView = -mv.xyz;
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: /* glsl */ `
        uniform float uEnergy;
        uniform vec3 uA;
        uniform vec3 uB;
        varying vec3 vNormal;
        varying vec3 vView;
        varying float vNoise;
        void main() {
          float rim = pow(1.0 - abs(dot(normalize(vNormal), normalize(vView))), 2.2);
          vec3 tint = mix(uB, uA, smoothstep(-0.6, 0.9, vNoise));
          gl_FragColor = vec4(tint * (0.08 + rim * (1.25 + uEnergy * 1.1) + uEnergy * 0.18), 1.0);
        }`,
    }),
  );
  rig.add(orb);

  // The eye: pupil + iris riding the orb's surface toward the pointer.
  const glow = new THREE.MeshBasicMaterial({ color: "#ffffff" });
  const eye = new THREE.Group();
  eye.add(new THREE.Mesh(new THREE.CircleGeometry(0.1, 40), glow), new THREE.Mesh(new THREE.RingGeometry(0.15, 0.19, 48), glow));
  rig.add(eye);

  // Gyroscope rings and the outer dial, all lit by one material that follows the mood.
  const lines = new THREE.MeshBasicMaterial();
  const ring = (radius: number, ticks: number) => {
    const group = new THREE.Group();
    group.add(new THREE.Mesh(new THREE.TorusGeometry(radius, 0.012, 6, 220), lines));
    if (ticks) {
      const marks = new THREE.InstancedMesh(new THREE.BoxGeometry(0.016, 0.12, 0.016), lines, ticks);
      const m = new THREE.Matrix4();
      for (let i = 0; i < ticks; i++) {
        const a = (i / ticks) * Math.PI * 2;
        m.makeRotationZ(a - Math.PI / 2).scale(new THREE.Vector3(1, i % 6 ? 1 : 2.2, 1));
        marks.setMatrixAt(i, m.setPosition(Math.cos(a) * (radius + 0.14), Math.sin(a) * (radius + 0.14), 0));
      }
      group.add(marks);
    }
    return group;
  };
  const ringA = ring(1.7, 0);
  const ringB = ring(2.25, 48);
  const dial = ring(2.95, 120);
  const bolts = new THREE.InstancedMesh(new THREE.BoxGeometry(0.34, 0.07, 0.07), lines, 12);
  dial.add(bolts);
  ringA.rotation.set(1.1, 0.3, 0);
  ringB.rotation.set(-0.5, 0.9, 0);
  rig.add(ringA, ringB, dial);

  // Dust orbiting the vault; the swirl and the burst happen in the vertex shader.
  const count = narrow() ? 700 : 1500;
  const positions = new Float32Array(count * 3);
  const seeds = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    const r = 3.3 + Math.random() * 6.5;
    const a = Math.random() * Math.PI * 2;
    positions.set([Math.cos(a) * r, Math.sin(a) * r, (Math.random() - 0.5) * 6], i * 3);
    seeds[i] = Math.random();
  }
  const dustGeometry = new THREE.BufferGeometry();
  dustGeometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  dustGeometry.setAttribute("aSeed", new THREE.BufferAttribute(seeds, 1));
  const dustUniforms = { uPhase: { value: 0 }, uBurst: { value: 0 }, uSize: { value: 60 }, uColor: { value: color("#fff") } };
  scene.add(
    new THREE.Points(
      dustGeometry,
      new THREE.ShaderMaterial({
        uniforms: dustUniforms,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        vertexShader: /* glsl */ `
          attribute float aSeed;
          uniform float uPhase;
          uniform float uBurst;
          uniform float uSize;
          varying float vSeed;
          void main() {
            float a = uPhase * (0.25 + aSeed);
            vec3 p = position;
            p.xy = mat2(cos(a), sin(a), -sin(a), cos(a)) * p.xy;
            p *= 1.0 + uBurst * (0.4 + aSeed);
            vec4 mv = modelViewMatrix * vec4(p, 1.0);
            gl_PointSize = uSize * (0.35 + aSeed) / -mv.z;
            gl_Position = projectionMatrix * mv;
            vSeed = aSeed;
          }`,
        fragmentShader: /* glsl */ `
          uniform vec3 uColor;
          varying float vSeed;
          void main() {
            float d = length(gl_PointCoord - 0.5);
            if (d > 0.5) discard;
            float glow = pow(1.0 - d * 2.0, 2.0);
            gl_FragColor = vec4(uColor * glow * (0.5 + vSeed), glow);
          }`,
      }),
    ),
  );

  // Framing: on wide screens the gate card and the console sit in a 464px right column
  // (see .gate/.console in styles.css), so the orb centres in what's left. Narrow screens put
  // the orb on top and back the camera off until the whole dial fits the width.
  let layout: Layout = "gate";
  const target = (l: Layout) => {
    if (!narrow()) return { x: 232, y: 0, z: l === "gate" ? 10.6 : 9.8 };
    const fitWidth = 3.4 / (Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * (innerWidth / innerHeight));
    return { x: 0, y: innerHeight * (l === "gate" ? 0.27 : 0.31), z: Math.max(11, fitWidth) };
  };
  const view = target(layout);

  const resize = () => {
    const pixelRatio = Math.min(devicePixelRatio, narrow() ? 1.5 : 1.75);
    renderer.setPixelRatio(pixelRatio);
    renderer.setSize(innerWidth, innerHeight);
    composer.setPixelRatio(pixelRatio);
    composer.setSize(innerWidth, innerHeight);
    camera.aspect = innerWidth / innerHeight;
    camera.updateProjectionMatrix();
    dustUniforms.uSize.value = 60 * pixelRatio;
    Object.assign(view, target(layout));
  };
  addEventListener("resize", resize);
  resize();

  // Pointer: the Keeper watches it everywhere; on the canvas you can turn the dial or poke the orb.
  const pointer = new THREE.Vector2();
  addEventListener("pointermove", (e) => pointer.set((e.clientX / innerWidth) * 2 - 1, 1 - (e.clientY / innerHeight) * 2));
  const angleAt = (e: PointerEvent) => Math.atan2(innerHeight / 2 - view.y - e.clientY, e.clientX - (innerWidth / 2 - view.x));
  let grabbed: number | null = null;
  let down = { x: 0, y: 0 };
  canvas.addEventListener("pointerdown", (e) => {
    grabbed = angleAt(e);
    down = { x: e.clientX, y: e.clientY };
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (grabbed === null) return;
    const angle = angleAt(e);
    const turn = Math.atan2(Math.sin(angle - grabbed), Math.cos(angle - grabbed));
    dial.rotation.z += turn;
    s.spin = turn * 30;
    grabbed = angle;
  });
  const raycaster = new THREE.Raycaster();
  canvas.addEventListener("pointerup", (e) => {
    grabbed = null;
    if (Math.hypot(e.clientX - down.x, e.clientY - down.y) > 5) return;
    raycaster.setFromCamera(new THREE.Vector2((e.clientX / innerWidth) * 2 - 1, 1 - (e.clientY / innerHeight) * 2), camera);
    if (raycaster.intersectObject(orb).length) {
      s.typing = 0.9;
      s.shake = 0.3 * motion;
    }
  });

  const colorA = MOODS.calm[0].clone();
  const colorB = MOODS.calm[1].clone();
  const direction = new THREE.Vector3();
  const forward = new THREE.Vector3(0, 0, 1);
  const bolt = new THREE.Matrix4();
  let last = performance.now();

  renderer.setAnimationLoop((now: number) => {
    const dt = Math.min((now - last) / 1000, 0.05);
    last = now;
    const ease = (rate: number) => 1 - Math.exp(-dt * rate);
    const speaking = now < s.speakUntil;

    s.energy += ((s.thinking ? 0.55 : speaking ? 0.85 : 0.08) + s.typing - s.energy) * ease(5);
    s.typing *= Math.exp(-dt * 2.5);
    s.time += dt * motion;
    s.phase += dt * motion * (0.05 + (s.thinking || speaking ? 0.45 : 0) + s.burst * 0.8);

    const [a, b] = now < s.alarmUntil ? MOODS.alarm : s.thinking ? MOODS.think : s.open > 0.5 ? MOODS.open : MOODS.calm;
    colorA.lerp(a, ease(3));
    colorB.lerp(b, ease(3));
    orbUniforms.uTime.value = s.time;
    orbUniforms.uEnergy.value = s.energy;
    orbUniforms.uA.value.copy(colorA);
    orbUniforms.uB.value.copy(colorB);
    lines.color.copy(colorA).multiplyScalar(1.05 + s.energy * 0.5);
    glow.color.copy(colorA).lerp(MOODS.open[0], 0.5).multiplyScalar(1.6);
    dustUniforms.uColor.value.copy(colorB).lerp(colorA, 0.4);
    dustUniforms.uPhase.value = s.phase;
    dustUniforms.uBurst.value = s.burst;

    // Rings spin harder with energy; the dial idles, races while thinking, and coasts after a drag.
    const spin = dt * motion * (1 + s.energy * 5);
    ringA.rotation.x += spin * 0.31;
    ringA.rotation.y += spin * 0.17;
    ringB.rotation.y -= spin * 0.23;
    ringB.rotation.z += spin * 0.11;
    if (grabbed === null) {
      dial.rotation.z += s.spin * dt + dt * motion * (s.thinking ? 1.2 : 0.04);
      s.spin *= Math.exp(-dt * 1.8);
    }
    ringA.scale.setScalar(1 + s.open * 0.35);
    ringB.scale.setScalar(1 + s.open * 0.5);
    dial.scale.setScalar(1 + s.open * 0.12);
    for (let i = 0; i < 12; i++) {
      const angle = (i / 12) * Math.PI * 2;
      const r = 2.62 + s.open * 0.75; // bolts retract as the vault opens
      bolts.setMatrixAt(i, bolt.makeRotationZ(angle).setPosition(Math.cos(angle) * r, Math.sin(angle) * r, 0));
    }
    bolts.instanceMatrix.needsUpdate = true;

    // Lean toward the pointer; the eye rides the surface to face it, and blinks now and then.
    rig.rotation.y += (pointer.x * 0.3 - rig.rotation.y) * ease(3);
    rig.rotation.x += (-pointer.y * 0.2 - rig.rotation.x) * ease(3);
    direction.set(pointer.x * 0.6, pointer.y * 0.5, 1).normalize();
    eye.position.copy(direction).multiplyScalar(1.1 + s.energy * 0.18);
    eye.quaternion.setFromUnitVectors(forward, direction);
    eye.scale.set(1 + s.open, (s.time % 5.7 < 0.13 ? 0.12 : 1) * (1 + s.open), 1);

    s.shake *= Math.exp(-dt * 4);
    camera.position.set((Math.random() - 0.5) * s.shake * 0.3, (Math.random() - 0.5) * s.shake * 0.3, view.z);
    camera.setViewOffset(innerWidth, innerHeight, view.x, view.y, innerWidth, innerHeight);
    bloom.strength = 0.75 + s.energy * 0.35 + s.open * 0.2;
    composer.render();
  });

  return {
    frame(next) {
      layout = next;
      animate(view, { ...target(next), duration: 1800 * motion, ease: "inOutExpo" });
    },
    thinking(on) {
      s.thinking = on;
    },
    speak(ms) {
      s.speakUntil = performance.now() + ms;
    },
    keystroke() {
      s.typing = Math.min(s.typing + 0.12, 0.7);
    },
    alarm() {
      s.alarmUntil = performance.now() + 900;
      s.shake = 0.5 * motion;
    },
    breach(instant = false) {
      if (instant) {
        s.open = 1;
        return;
      }
      animate(s, { open: 1, duration: 2800, ease: "inOutExpo" });
      animate(s, { burst: [0, 1], duration: 900, ease: "outExpo" }).then(() =>
        animate(s, { burst: 0.15, duration: 2600, ease: "inOutSine" }),
      );
      s.shake = 1.2 * motion;
    },
    seal() {
      s.open = 0;
      s.burst = 0;
    },
  };
}
