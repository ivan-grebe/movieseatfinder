import { setupDisclosure } from "./ui.js";

/** A collapsed checklist of nearby theatres to leave out of every search. */
export function createTheatreExclusions(container, onChange) {
  const disclosure = setupDisclosure(container);
  const toggle = container.querySelector(".disclosure-toggle");
  const count = container.querySelector(".disclosure-count");
  const list = container.querySelector(".theatre-exclusions-list");
  const excluded = new Set();
  let theatreNames = [];

  function renderCount() {
    count.textContent = excluded.size > 0 ? `(${excluded.size})` : "";
  }

  function renderOption(name) {
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = excluded.has(name);
    input.addEventListener("change", () => {
      if (input.checked) {
        excluded.add(name);
      } else {
        excluded.delete(name);
      }
      renderCount();
      onChange();
    });
    const box = document.createElement("span");
    box.className = "check-box";
    box.setAttribute("aria-hidden", "true");
    const label = document.createElement("span");
    label.textContent = name;
    const option = document.createElement("label");
    option.className = "check";
    option.append(input, box, label);
    return option;
  }

  function render() {
    list.replaceChildren(...theatreNames.map((name) => renderOption(name)));
    renderCount();
  }

  /** List these theatres, dropping exclusions for any that are no longer nearby. */
  function setTheatres(names) {
    theatreNames = names;
    excluded.forEach((name) => {
      if (!names.includes(name)) {
        excluded.delete(name);
      }
    });
    render();
    toggle.disabled = names.length === 0;
    if (toggle.disabled) {
      disclosure.setExpanded(false);
    }
  }

  function select(names) {
    excluded.clear();
    names.forEach((name) => excluded.add(name));
    render();
  }

  return {
    has: (name) => excluded.has(name),
    select,
    setTheatres,
    values: () => [...excluded],
  };
}
