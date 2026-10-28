/* 3D flight view (SIMULASI). A drone flies an approved plan on autopilot, driven by the same telemetry as the pilot
   console: nothing here is steered by hand. The ground is the incident's Sentinel-2 image (or OpenStreetMap tiles)
   laid flat: no elevation model is used, and the drone is drawn larger than life so it can be seen.
   Loaded on demand; three.js comes from the import map in index.html. */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

export const DRONE_SCALE = 30;  // a 1 m drone is invisible from a few hundred metres away
const M_LAT = 110574;

export function createFlight3D(container, { plan, ground, hotspots = [], getState, loadImage, t }) {
  const [w, s, e, n] = plan.bbox;
  const lat0 = (s + n) / 2, lon0 = (w + e) / 2;
  const mLon = 111320 * Math.cos((lat0 * Math.PI) / 180);
  const local = (lon, lat, y = 0) => new THREE.Vector3((lon - lon0) * mLon, y, -(lat - lat0) * M_LAT);
  const alt = plan.altitude_agl_m;
  const [fw, fh] = plan.footprint_m;

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  container.append(renderer.domElement);
  const inset = document.createElement("div");
  inset.className = "f3d-inset";
  inset.textContent = t("Drone camera");
  container.append(inset);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xcfdbe6);
  scene.fog = new THREE.Fog(0xcfdbe6, 2500, 7000);
  scene.add(new THREE.HemisphereLight(0xeef4ff, 0x4a5a3a, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 1.4);
  sun.position.set(-800, 1500, 600);
  scene.add(sun);

  // Ground: a neutral apron, then the image quad on its four corners (top left, top right, bottom right, bottom left).
  const apron = new THREE.Mesh(new THREE.PlaneGeometry(20000, 20000), new THREE.MeshLambertMaterial({ color: 0x8a9a78 }));
  apron.rotation.x = -Math.PI / 2;
  apron.position.y = -0.5;
  scene.add(apron);
  const quad = (corners, y) => {
    const g = new THREE.BufferGeometry();
    const p = corners.map(([lo, la]) => local(lo, la, y));
    g.setAttribute("position", new THREE.Float32BufferAttribute([p[0], p[3], p[1], p[1], p[3], p[2]].flatMap((v) => [v.x, v.y, v.z]), 3));
    g.setAttribute("uv", new THREE.Float32BufferAttribute([0, 1, 0, 0, 1, 1, 1, 1, 0, 0, 1, 0], 2));
    g.computeVertexNormals();
    return g;
  };
  const texture = async (url) => {
    const tex = new THREE.Texture(await loadImage(url));
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
    tex.needsUpdate = true;
    return tex;
  };
  const groundMesh = new THREE.Mesh(quad(ground.corners, 0), new THREE.MeshLambertMaterial({ color: 0xffffff, side: THREE.DoubleSide }));
  scene.add(groundMesh);
  texture(ground.image).then((tex) => { groundMesh.material.map = tex; groundMesh.material.needsUpdate = true; }).catch(() => {});
  let maskMesh = null;
  if (ground.mask) {
    maskMesh = new THREE.Mesh(quad(ground.corners, 0.6), new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.85, side: THREE.DoubleSide, depthWrite: false }));
    scene.add(maskMesh);
    texture(ground.mask).then((tex) => { maskMesh.material.map = tex; maskMesh.material.needsUpdate = true; }).catch(() => {});
  }

  // Hotspots: glowing posts where the satellite saw heat.
  const hotMat = new THREE.MeshBasicMaterial({ color: 0xff5a1f });
  for (const h of hotspots) {
    const post = new THREE.Mesh(new THREE.CylinderGeometry(6, 6, 40, 10), hotMat);
    post.position.copy(local(h.lon, h.lat, 20));
    scene.add(post);
  }

  // Route at flight altitude, and the frame positions on the ground.
  const wps = plan.waypoints;
  const route = new THREE.Line(new THREE.BufferGeometry().setFromPoints(wps.map((p) => local(p.lon, p.lat, alt))),
    new THREE.LineDashedMaterial({ color: 0x7a5af8, dashSize: 14, gapSize: 9 }));
  route.computeLineDistances();
  scene.add(route);
  const shotGeo = new THREE.CircleGeometry(7, 16);
  const shotMat = new THREE.MeshBasicMaterial({ color: 0x7a5af8, transparent: true, opacity: 0.9 });
  const shots = wps.map((p) => {
    const m = new THREE.Mesh(shotGeo, shotMat);
    m.rotation.x = -Math.PI / 2;
    m.position.copy(local(p.lon, p.lat, 1));
    m.visible = false;
    scene.add(m);
    return m;
  });

  // Drone: body, four arms, spinning rotors, a light underneath.
  const drone = new THREE.Group();
  const dark = new THREE.MeshLambertMaterial({ color: 0x2b2f36 });
  drone.add(new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.18, 0.5), new THREE.MeshLambertMaterial({ color: 0xf2f4f7 })));
  const rotors = [];
  for (const [x, z] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) {
    const arm = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.05, 0.62), dark);
    arm.position.set(x * 0.21, 0, z * 0.21);
    arm.rotation.y = Math.atan2(x, z);
    drone.add(arm);
    const rotor = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.2, 0.01, 18), new THREE.MeshBasicMaterial({ color: 0x9aa4b2, transparent: true, opacity: 0.55 }));
    rotor.position.set(x * 0.42, 0.08, z * 0.42);
    drone.add(rotor);
    rotors.push(rotor);
  }
  const led = new THREE.Mesh(new THREE.SphereGeometry(0.06, 10, 8), new THREE.MeshBasicMaterial({ color: 0x5b3fd6 }));
  led.position.y = -0.12;
  drone.add(led);
  drone.scale.setScalar(DRONE_SCALE);
  scene.add(drone);

  // Camera footprint on the ground and the frustum lines from the drone to it.
  const footMesh = new THREE.Mesh(new THREE.PlaneGeometry(fw, fh), new THREE.MeshBasicMaterial({ color: 0x7a5af8, transparent: true, opacity: 0.22, depthWrite: false }));
  footMesh.rotation.x = -Math.PI / 2;
  scene.add(footMesh);
  const frustum = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0xc4b5fd }));
  frustum.frustumCulled = false;  // its geometry moves every frame; a bounding sphere from the first frame would hide it
  scene.add(frustum);

  // Cameras: a chase view the reader can orbit (the drone keeps flying by itself), and the drone's nadir camera.
  const cam = new THREE.PerspectiveCamera(50, 1, 1, 20000);
  const controls = new OrbitControls(cam, renderer.domElement);
  controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.48;
  controls.minDistance = 60;
  controls.maxDistance = 4000;
  const nadir = new THREE.PerspectiveCamera((2 * Math.atan(fh / 2 / alt) * 180) / Math.PI, fw / fh, 1, alt * 2);
  let follow = true;
  let last = null;
  controls.addEventListener("start", () => { follow = true; });

  const resize = () => {
    const r = container.getBoundingClientRect();
    renderer.setSize(r.width, r.height, false);
    cam.aspect = r.width / Math.max(1, r.height);
    cam.updateProjectionMatrix();
  };
  const ro = new ResizeObserver(resize);
  ro.observe(container);
  resize();

  let raf = 0;
  const clock = new THREE.Clock();
  const frame = () => {
    raf = requestAnimationFrame(frame);
    const st = getState();
    const dt = clock.getDelta();
    if (st) {
      const y = st.swapping ? 2 : st.alt_m;
      const pos = local(st.lon, st.lat, y);
      const heading = Math.atan2(st.b.lon - st.a.lon, st.b.lat - st.a.lat) || 0;
      drone.position.copy(pos);
      drone.rotation.set(0, -heading, 0);
      rotors.forEach((r) => { r.rotation.y += (st.swapping ? 0 : 40) * dt; });
      shots.forEach((m, i) => { m.visible = i < st.shots; });
      footMesh.visible = frustum.visible = !st.swapping;
      footMesh.position.set(pos.x, 1.5, pos.z);
      footMesh.rotation.z = heading;
      const c = [[1, 1], [1, -1], [-1, -1], [-1, 1]].map(([a, b]) => {
        const ax = Math.sin(heading), az = -Math.cos(heading);  // along track, east and three.js z
        return new THREE.Vector3(pos.x + ax * a * fh / 2 + az * b * fw / 2, 1.5, pos.z + az * a * fh / 2 - ax * b * fw / 2);
      });
      frustum.geometry.setFromPoints(c.flatMap((v) => [pos, v]));
      // The chase camera keeps its offset from the drone while the reader orbits.
      if (!last) {
        cam.position.set(pos.x - 230, pos.y + 140, pos.z + 230);
        controls.target.copy(pos);
      } else if (follow) {
        const d = pos.clone().sub(last);
        cam.position.add(d);
        controls.target.add(d);
      }
      last = pos.clone();
      nadir.position.set(pos.x, y, pos.z);
      nadir.up.set(Math.sin(heading), 0, -Math.cos(heading));
      nadir.lookAt(pos.x, 0, pos.z);
    }
    controls.update();
    const r = renderer.domElement.getBoundingClientRect();
    renderer.setScissorTest(false);
    renderer.setViewport(0, 0, r.width, r.height);
    renderer.render(scene, cam);
    if (!st || st.swapping) inset.style.display = "none";
    if (st && !st.swapping) {
      // Inset: what the nadir camera sees, bottom right.
      const iw = Math.round(Math.min(300, r.width * 0.34)), ih = Math.round(iw * fh / fw), pad = 12;
      Object.assign(inset.style, { width: `${iw}px`, height: `${ih}px`, right: `${pad}px`, bottom: `${pad}px`, display: "block" });
      renderer.setScissorTest(true);
      renderer.setScissor(r.width - iw - pad, pad, iw, ih);
      renderer.setViewport(r.width - iw - pad, pad, iw, ih);
      footMesh.visible = frustum.visible = drone.visible = false;
      renderer.render(scene, nadir);
      footMesh.visible = frustum.visible = drone.visible = true;
      renderer.setScissorTest(false);
    }
  };
  frame();

  return {
    setMask(on) { if (maskMesh) maskMesh.visible = on; },
    hasMask: !!maskMesh,
    recentre() { last = null; follow = true; },
    destroy() {
      cancelAnimationFrame(raf);
      ro.disconnect();
      controls.dispose();
      renderer.dispose();
      scene.traverse((o) => { o.geometry?.dispose?.(); if (o.material) [].concat(o.material).forEach((m) => { m.map?.dispose?.(); m.dispose?.(); }); });
      renderer.domElement.remove();
      inset.remove();
    },
  };
}
