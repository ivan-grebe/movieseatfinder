const STATE_CLASSES = ["is-loading", "is-error", "is-success"];

function createSpinner() {
  const spinner = document.createElement("span");
  spinner.className = "spinner";
  spinner.setAttribute("aria-hidden", "true");
  return spinner;
}

/** A spinner plus text that only screen readers announce. */
export function createLoadingIndicator(message) {
  const announcement = document.createElement("span");
  announcement.className = "visually-hidden";
  announcement.textContent = message;
  return [createSpinner(), announcement];
}

export function setStatus(element, text, state = "") {
  element.title = text;
  element.classList.remove(...STATE_CLASSES);
  if (state) {
    element.classList.add(`is-${state}`);
  }
  element.textContent = "";
  if (!text) {
    return;
  }

  if (state === "loading") {
    element.append(createSpinner());
  }
  element.append(document.createTextNode(text));
}

export function setSummary(element, text, muted) {
  element.classList.toggle("is-muted", muted);
  element.textContent = text;
}

const SEARCH_LOADING_STAGES = [
  { label: "Loading theatres" },
  { delay: 650, label: "Checking showtimes" },
  { delay: 1500, label: "Checking seat maps" },
];

function appendAnimatedLabel(element, text) {
  const label = document.createElement("span");
  label.className = "loading-label";
  label.append(document.createTextNode(text));
  const dots = document.createElement("span");
  dots.className = "loading-dots";
  dots.setAttribute("aria-hidden", "true");
  for (let index = 1; index <= 3; index += 1) {
    const dot = document.createElement("span");
    dot.className = `loading-dot loading-dot-${index}`;
    dot.textContent = ".";
    dots.append(dot);
  }
  label.append(dots);
  element.append(label);
}

export function startLoadingStages(onStage) {
  onStage(SEARCH_LOADING_STAGES[0].label);
  const timers = SEARCH_LOADING_STAGES.slice(1).map((stage) =>
    globalThis.setTimeout(() => onStage(stage.label), stage.delay),
  );
  return () => timers.forEach((timer) => globalThis.clearTimeout(timer));
}

export function setButtonBusy(button, busy, busyLabel) {
  if (busy) {
    button.dataset.label ||= button.textContent;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.textContent = "";
    button.append(createSpinner());
    appendAnimatedLabel(button, busyLabel);
    return;
  }

  button.disabled = false;
  button.removeAttribute("aria-busy");
  button.textContent = button.dataset.label;
}
