export function closeCombo(input, menu) {
  menu.hidden = true;
  menu._items = [];
  input.setAttribute("aria-expanded", "false");
  input.removeAttribute("aria-activedescendant");
}

function renderCombo(menu, items, input, getLabel, onSelect) {
  menu.innerHTML = "";
  menu._items = items;
  if (!items.length) {
    closeCombo(input, menu);
    return;
  }

  items.forEach((item, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "combo-option";
    button.id = `${menu.id}-option-${index}`;
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", "false");
    button.textContent = getLabel(item);
    button.addEventListener("mousedown", event => {
      event.preventDefault();
      onSelect(item);
    });
    menu.appendChild(button);
  });
  menu.hidden = false;
  input.setAttribute("aria-expanded", "true");
}

export function setupCombo(input, menu, source, getLabel, onPick) {
  let activeIndex = -1;

  const options = () => Array.from(menu.querySelectorAll(".combo-option"));

  function setActive(index) {
    const choices = options();
    choices.forEach(option => {
      option.classList.remove("is-active");
      option.setAttribute("aria-selected", "false");
    });
    if (!choices.length || index < 0) {
      activeIndex = -1;
      input.removeAttribute("aria-activedescendant");
      return;
    }
    activeIndex = (index + choices.length) % choices.length;
    const active = choices[activeIndex];
    active.classList.add("is-active");
    active.setAttribute("aria-selected", "true");
    input.setAttribute("aria-activedescendant", active.id);
    active.scrollIntoView({ block: "nearest" });
  }

  function pick(item) {
    if (!item) return;
    input.value = getLabel(item);
    closeCombo(input, menu);
    activeIndex = -1;
    onPick(item);
  }

  function update() {
    const query = input.value.trim().toLowerCase();
    const items = source().filter(item => getLabel(item).toLowerCase().includes(query));
    renderCombo(menu, items, input, getLabel, pick);
    activeIndex = -1;
  }

  input.addEventListener("focus", update);
  input.addEventListener("input", update);
  input.addEventListener("keydown", event => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (menu.hidden) update();
      setActive(activeIndex + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (menu.hidden) update();
      setActive(activeIndex === -1 ? -1 : activeIndex - 1);
    } else if (event.key === "Enter") {
      if (!menu.hidden && activeIndex >= 0 && menu._items?.[activeIndex]) {
        event.preventDefault();
        pick(menu._items[activeIndex]);
      }
    } else if (event.key === "Escape" && !menu.hidden) {
      event.preventDefault();
      event.stopPropagation();
      closeCombo(input, menu);
      activeIndex = -1;
    }
  });
  input.addEventListener("blur", () => {
    setTimeout(() => closeCombo(input, menu), 120);
  });
}
