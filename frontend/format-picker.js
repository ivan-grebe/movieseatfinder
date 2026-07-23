export function createFormatPicker(container) {
  const selected = new Set(["any"]);

  function syncOption(option) {
    const isSelected = selected.has(option.dataset.format);
    option.classList.toggle("is-selected", isSelected);
    option.setAttribute("aria-pressed", String(isSelected));
  }

  function toggle(value) {
    if (value === "any") {
      selected.clear();
      selected.add("any");
    } else {
      selected.delete("any");
      if (selected.has(value)) selected.delete(value);
      else selected.add(value);
      if (!selected.size) selected.add("any");
    }
    container.querySelectorAll(".format-option").forEach(syncOption);
  }

  function renderOption(label, value) {
    const option = document.createElement("button");
    option.type = "button";
    option.className = "format-option";
    option.dataset.format = value;
    option.textContent = label;
    option.addEventListener("click", () => toggle(value));
    container.appendChild(option);
    syncOption(option);
  }

  function setOptions(formats) {
    const available = [...new Set(formats.filter(Boolean))];
    const retained = [...selected].filter(format => available.includes(format));
    selected.clear();
    (retained.length ? retained : ["any"]).forEach(format => selected.add(format));

    container.innerHTML = "";
    renderOption("Any available format", "any");
    available.forEach(format => renderOption(format, format));
  }

  function select(values) {
    selected.clear();
    values.filter(Boolean).forEach(format => selected.add(format));
    if (!selected.size) selected.add("any");
  }

  return {
    setOptions,
    select,
    value: () => selected.has("any") ? "any" : [...selected].join(","),
    values: () => [...selected],
  };
}
