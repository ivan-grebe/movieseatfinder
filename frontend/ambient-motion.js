const AMBIENT_GLOWS = [
  {
    selector: ".ambient-glow-warm",
    start: { x: -50, y: 0 },
    bounds: { x: [-64, -36], y: [0, 20], minDistance: 12 },
    duration: [6_500, 9_000],
  },
  {
    selector: ".ambient-glow-cool",
    start: { x: 0, y: 0 },
    bounds: { x: [-22, 12], y: [0, 20], minDistance: 12 },
    duration: [7_500, 10_500],
  },
];

function randomBetween([minimum, maximum], random) {
  return minimum + ((maximum - minimum) * random());
}

function distanceBetween(first, second) {
  return Math.hypot(second.x - first.x, second.y - first.y);
}

export function pickAmbientTarget(current, bounds, random = Math.random) {
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const target = {
      x: randomBetween(bounds.x, random),
      y: randomBetween(bounds.y, random),
    };
    if (distanceBetween(current, target) >= bounds.minDistance) return target;
  }

  const corners = bounds.x.flatMap(x => bounds.y.map(y => ({ x, y })));
  return corners.reduce((farthest, candidate) => (
    distanceBetween(current, candidate) > distanceBetween(current, farthest) ? candidate : farthest
  ));
}

function transformFor({ x, y }) {
  const roundedX = Math.round(x * 100) / 100;
  const roundedY = Math.round(y * 100) / 100;
  return `translate3d(${roundedX}%, ${roundedY}%, 0)`;
}

export function initializeAmbientMotion({
  root = document,
  motionPreference = window.matchMedia("(prefers-reduced-motion: reduce)"),
  random = Math.random,
} = {}) {
  const states = AMBIENT_GLOWS.flatMap(config => {
    const element = root.querySelector(config.selector);
    if (!element || typeof element.animate !== "function") return [];
    return [{ element, config, current: { ...config.start }, animation: null }];
  });

  function startSegment(state) {
    const target = pickAmbientTarget(state.current, state.config.bounds, random);
    const destination = transformFor(target);
    const animation = state.element.animate(
      [
        { transform: transformFor(state.current) },
        { transform: destination },
      ],
      {
        duration: Math.round(randomBetween(state.config.duration, random)),
        easing: "cubic-bezier(.45, 0, .55, 1)",
        fill: "forwards",
      },
    );

    state.animation = animation;
    state.element.dataset.ambientMotion = "wandering";
    animation.onfinish = () => {
      if (state.animation !== animation || motionPreference.matches) return;
      state.element.style.transform = destination;
      state.current = target;
      state.animation = null;
      animation.cancel();
      startSegment(state);
    };
  }

  function syncMotionPreference() {
    states.forEach(state => {
      if (state.animation) {
        state.animation.onfinish = null;
        state.animation.cancel();
        state.animation = null;
      }
      state.current = { ...state.config.start };
      state.element.style.transform = transformFor(state.current);
      state.element.dataset.ambientMotion = motionPreference.matches ? "paused" : "wandering";
      if (!motionPreference.matches) startSegment(state);
    });
  }

  syncMotionPreference();
  motionPreference.addEventListener("change", syncMotionPreference);

  return () => {
    motionPreference.removeEventListener("change", syncMotionPreference);
    states.forEach(state => {
      if (state.animation) state.animation.cancel();
      state.element.style.removeProperty("transform");
      delete state.element.dataset.ambientMotion;
    });
  };
}
