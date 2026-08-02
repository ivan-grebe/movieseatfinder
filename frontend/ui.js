const STATE_CLASSES = ["is-loading", "is-error", "is-success"];

export function setStatus(element, text, state = "") {
  element.classList.remove(...STATE_CLASSES);
  if (state) element.classList.add(`is-${state}`);
  element.textContent = "";
  if (!text) return;

  if (state === "loading") {
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    element.appendChild(spinner);
  }
  element.appendChild(document.createTextNode(text));
}

export function setSummary(element, text, muted) {
  element.classList.toggle("is-muted", Boolean(muted));
  element.textContent = text;
}

const SEARCH_LOADING_STAGES = [
  { label: "Loading theatres" },
  { label: "Checking showtimes", delay: 650 },
  { label: "Checking seat maps", delay: 1500 },
];

function appendAnimatedLabel(element, text) {
  const label = document.createElement("span");
  label.className = "loading-label";
  label.appendChild(document.createTextNode(text));
  const dots = document.createElement("span");
  dots.className = "loading-dots";
  dots.setAttribute("aria-hidden", "true");
  for (let index = 1; index <= 3; index += 1) {
    const dot = document.createElement("span");
    dot.className = `loading-dot loading-dot-${index}`;
    dot.textContent = ".";
    dots.appendChild(dot);
  }
  label.appendChild(dots);
  element.appendChild(label);
}

export function setAnimatedStatus(element, text) {
  element.textContent = "";
  if (text) appendAnimatedLabel(element, text);
}

export function startLoadingStages(onStage, stages = SEARCH_LOADING_STAGES) {
  if (!stages.length) return () => {};
  onStage(stages[0].label);
  const timers = stages.slice(1).map(stage => window.setTimeout(
    () => onStage(stage.label),
    stage.delay,
  ));
  return () => timers.forEach(timer => window.clearTimeout(timer));
}

export function setButtonBusy(button, busy, busyLabel) {
  if (busy) {
    button.dataset.label = button.dataset.label || button.textContent;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.textContent = "";
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    button.appendChild(spinner);
    appendAnimatedLabel(button, busyLabel || button.dataset.label);
    return;
  }

  button.disabled = false;
  button.removeAttribute("aria-busy");
  if (button.dataset.label) button.textContent = button.dataset.label;
}
