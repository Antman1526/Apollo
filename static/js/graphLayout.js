// Pure force-directed graph layout math. No DOM, no Math.random, no browser
// globals at module top level — so Node can `import` it for unit tests.
//
//   seedPositions(nodes, w, h, seed)  → deterministically place nodes (LCG)
//   stepLayout(nodes, edges, opts)    → advance one physics tick (mutates nodes)
//
// Each node gains {x, y, vx, vy}. Layout = pairwise repulsion + edge springs +
// mild centering + velocity damping, integrated once per call and clamped to
// [0,width] × [0,height].

// Deterministic linear congruential generator (glibc constants). Returns a
// closure yielding floats in [0,1). Never touches Math.random.
function _lcg(seed) {
  let state = (seed >>> 0) || 1;
  return function next() {
    // state = (1103515245 * state + 12345) mod 2^31
    state = (Math.imul(1103515245, state) + 12345) & 0x7fffffff;
    return state / 0x7fffffff;
  };
}

// Place every node at a deterministic pseudo-random point inside the box and
// zero its velocity. Same seed → identical output (relied on by the test).
export function seedPositions(nodes, width, height, seed = 1) {
  const rnd = _lcg(seed);
  for (const n of nodes) {
    n.x = rnd() * width;
    n.y = rnd() * height;
    n.vx = 0;
    n.vy = 0;
  }
  return nodes;
}

// Advance the simulation by one tick. `edges` reference node ids via
// {source, target}; edges pointing at unknown ids are skipped safely.
export function stepLayout(nodes, edges, opts = {}) {
  const {
    width = 600,
    height = 400,
    repulsion = 800,
    spring = 0.08,
    springLen = 30,
    damping = 0.85,
    center = 0.005,
  } = opts;

  const n = nodes.length;
  if (n === 0) return nodes;

  // Ensure coords exist even if seedPositions wasn't called.
  for (const node of nodes) {
    if (!Number.isFinite(node.x)) node.x = width / 2;
    if (!Number.isFinite(node.y)) node.y = height / 2;
    if (!Number.isFinite(node.vx)) node.vx = 0;
    if (!Number.isFinite(node.vy)) node.vy = 0;
  }

  const fx = new Array(n).fill(0);
  const fy = new Array(n).fill(0);
  const index = new Map();
  nodes.forEach((node, i) => index.set(node.id, i));

  // Pairwise Coulomb-style repulsion (O(n²) — fine for ≤300 nodes).
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      let dx = nodes[i].x - nodes[j].x;
      let dy = nodes[i].y - nodes[j].y;
      let d2 = dx * dx + dy * dy;
      if (d2 < 0.01) {
        // Coincident: nudge apart deterministically to avoid divide-by-zero.
        dx = (i - j) * 0.1 + 0.1;
        dy = (i + j) * 0.1 + 0.1;
        d2 = dx * dx + dy * dy;
      }
      const dist = Math.sqrt(d2);
      const force = repulsion / d2;
      const ux = dx / dist;
      const uy = dy / dist;
      fx[i] += ux * force;
      fy[i] += uy * force;
      fx[j] -= ux * force;
      fy[j] -= uy * force;
    }
  }

  // Edge springs toward `springLen` (Hooke's law).
  for (const e of edges) {
    const a = index.get(e.source);
    const b = index.get(e.target);
    if (a === undefined || b === undefined) continue;
    const dx = nodes[b].x - nodes[a].x;
    const dy = nodes[b].y - nodes[a].y;
    const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
    const disp = dist - springLen;
    const f = spring * disp;
    const ux = dx / dist;
    const uy = dy / dist;
    fx[a] += ux * f;
    fy[a] += uy * f;
    fx[b] -= ux * f;
    fy[b] -= uy * f;
  }

  // Mild pull toward the centre so disconnected nodes don't drift off.
  const cx = width / 2;
  const cy = height / 2;
  for (let i = 0; i < n; i++) {
    fx[i] += (cx - nodes[i].x) * center;
    fy[i] += (cy - nodes[i].y) * center;
  }

  // Integrate velocity with damping, then clamp to bounds.
  for (let i = 0; i < n; i++) {
    const node = nodes[i];
    node.vx = (node.vx + fx[i]) * damping;
    node.vy = (node.vy + fy[i]) * damping;
    node.x += node.vx;
    node.y += node.vy;
    if (node.x < 0) { node.x = 0; node.vx = 0; }
    else if (node.x > width) { node.x = width; node.vx = 0; }
    if (node.y < 0) { node.y = 0; node.vy = 0; }
    else if (node.y > height) { node.y = height; node.vy = 0; }
  }

  return nodes;
}
