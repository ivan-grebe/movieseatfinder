export function setStatus(element, text, state = "") {
  const baseClass = element.id === "gridStatus" ? "status grid-status" : "status";
  element.className = baseClass + (state ? ` is-${state}` : "");
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
  element.className = "summary" + (muted ? " is-muted" : "");
  element.textContent = "";
  if (muted === "loading") {
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    element.appendChild(spinner);
  }
  element.appendChild(document.createTextNode(text));
}

export function setButtonBusy(button, busy, busyLabel) {
  if (busy) {
    button.dataset.label = button.dataset.label || button.textContent;
    button.disabled = true;
    button.textContent = "";
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    button.appendChild(spinner);
    button.appendChild(document.createTextNode(busyLabel || button.dataset.label));
    return;
  }

  button.disabled = false;
  if (button.dataset.label) button.textContent = button.dataset.label;
}
