const BAYER = [
  [0, 8, 2, 10],
  [12, 4, 14, 6],
  [3, 11, 1, 9],
  [15, 7, 13, 5],
];
const canvas = document.querySelector(".dither-canvas");
const hero = document.querySelector(".hero-copy");
const context = canvas.getContext("2d");
const theme = globalThis.matchMedia("(prefers-color-scheme: dark)");
const reducedMotion = globalThis.matchMedia("(prefers-reduced-motion: reduce)");
let pixels = null;
let safeZone = null;
let animationFrame = 0;
let lastFrame = 0;
let elapsed = 0;

function drawDither() {
  if (!pixels) {
    return;
  }
  const { data, height, width } = pixels;
  const phase = elapsed * 0.36;
  let opacity = 0.38;
  if (theme.matches) {
    opacity = 0.5;
  }

  for (let column = 0; column < width; column += 2) {
    const x = column / width;
    const crest =
      0.2 +
      0.17 * Math.sin(x * Math.PI * 2 - phase - 0.6) +
      0.065 * Math.sin(x * Math.PI * 4 + phase * 0.7);
    const secondCrest = 0.4 + 0.16 * Math.sin(x * Math.PI * 2 + phase * 0.6 + 0.4);
    const thickness = 0.15 + 0.055 * Math.sin(x * Math.PI * 2 + phase * 0.45);
    for (let row = 0; row < height; row += 2) {
      const y = row / height;
      const wave = Math.exp(-(((y - crest) / thickness) ** 2));
      const secondWave = Math.exp(-(((y - secondCrest) / 0.09) ** 2));
      const fade = Math.max(0, 1 - y / 0.9);
      const density = (wave * 0.62 + secondWave * 0.2) * fade * safeZone[row * width + column];
      const threshold = (BAYER[(row / 2) % 4][(column / 2) % 4] + 0.5) / 16;
      // Ease pixels across the threshold so slow motion doesn't sparkle.
      const coverage = Math.max(0, Math.min(1, (density - threshold) / 0.045 + 0.5));
      const smoothCoverage = coverage * coverage * (3 - 2 * coverage);
      data[(row * width + column) * 4 + 3] = Math.round(255 * opacity * smoothCoverage);
    }
  }
  context.putImageData(pixels, 0, 0);
}

function resizeDither() {
  // One bitmap pixel becomes a 2px square; empty pixels keep the 4px grid crisp.
  canvas.width = Math.ceil(canvas.clientWidth / 2);
  canvas.height = Math.ceil(canvas.clientHeight / 2);
  pixels = context.createImageData(canvas.width, canvas.height);
  safeZone = new Float32Array(pixels.width * pixels.height);
  const canvasBounds = canvas.getBoundingClientRect();
  const heroBounds = hero?.getBoundingClientRect();
  for (let column = 0; column < pixels.width; column += 2) {
    const x = column / pixels.width;
    for (let row = 0; row < pixels.height; row += 2) {
      let density = 1;
      if (heroBounds?.width && heroBounds.height) {
        const screenX = canvasBounds.left + x * canvasBounds.width;
        const screenY = canvasBounds.top + (row / pixels.height) * canvasBounds.height;
        const dx = (screenX - heroBounds.left - heroBounds.width / 2) / (heroBounds.width / 2 + 32);
        const dy =
          (screenY - heroBounds.top - heroBounds.height / 2) / (heroBounds.height / 2 + 24);
        const edge = Math.max(0, Math.min(1, (Math.hypot(dx, dy) - 0.65) / 0.8));
        density = edge * edge * (3 - 2 * edge);
      }
      // Reduce density before Bayer thresholding: dots disappear, not darken.
      safeZone[row * pixels.width + column] = density;
      const offset = (row * pixels.width + column) * 4;
      pixels.data[offset] = Math.round(193 + 28 * x);
      pixels.data[offset + 1] = Math.round(65 + 84 * x);
      pixels.data[offset + 2] = Math.round(75 - 7 * x);
    }
  }
  drawDither();
}

function animate(timestamp) {
  if (!lastFrame) {
    lastFrame = timestamp;
  }
  if (timestamp - lastFrame >= 1000 / 24) {
    elapsed += (timestamp - lastFrame) / 1000;
    lastFrame = timestamp;
    drawDither();
  }
  animationFrame = globalThis.requestAnimationFrame(animate);
}

function syncMotion() {
  globalThis.cancelAnimationFrame(animationFrame);
  lastFrame = 0;
  drawDither();
  if (!reducedMotion.matches && !document.hidden) {
    animationFrame = globalThis.requestAnimationFrame(animate);
  }
}

const resizeObserver = new ResizeObserver(resizeDither);
resizeObserver.observe(canvas);
if (hero) {
  resizeObserver.observe(hero);
}
theme.addEventListener("change", drawDither);
reducedMotion.addEventListener("change", syncMotion);
document.addEventListener("visibilitychange", syncMotion);
syncMotion();
