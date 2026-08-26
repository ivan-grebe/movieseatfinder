export function closeCombo(input, menu) {
  menu.hidden = true;
  input.setAttribute("aria-expanded", "false");
  input.removeAttribute("aria-activedescendant");
}

export function setupCombo(input, menu, source, getLabel, onPick) {
  let items = [];
  let activeIndex = -1;

  function options() {
    return [...menu.querySelectorAll(".combo-option")];
  }

  function setActive(index) {
    const choices = options();
    choices.forEach((option) => {
      option.classList.remove("is-active");
      option.setAttribute("aria-selected", "false");
    });
    if (choices.length === 0) {
      activeIndex = -1;
      input.removeAttribute("aria-activedescendant");
      return;
    }
    // Negative indexes wrap, so ArrowUp from the top lands on the last option
    // Just as ArrowDown from the bottom lands on the first.
    activeIndex = ((index % choices.length) + choices.length) % choices.length;
    const active = choices[activeIndex];
    active.classList.add("is-active");
    active.setAttribute("aria-selected", "true");
    input.setAttribute("aria-activedescendant", active.id);
    active.scrollIntoView({ block: "nearest" });
  }

  function pick(item) {
    input.value = getLabel(item);
    closeCombo(input, menu);
    activeIndex = -1;
    onPick(item);
  }

  function render() {
    menu.replaceChildren();
    if (items.length === 0) {
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
      button.addEventListener("mousedown", (event) => {
        event.preventDefault();
        pick(item);
      });
      menu.append(button);
    });
    menu.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  function update() {
    const query = input.value.trim().toLowerCase();
    items = source().filter((item) => getLabel(item).toLowerCase().includes(query));
    render();
    activeIndex = -1;
  }

  input.addEventListener("focus", update);
  input.addEventListener("input", update);
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (menu.hidden) {
        update();
      }
      setActive(activeIndex + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (menu.hidden) {
        update();
      }
      if (activeIndex === -1) {
        setActive(-1);
      } else {
        setActive(activeIndex - 1);
      }
    } else if (event.key === "Enter") {
      if (!menu.hidden && activeIndex >= 0) {
        event.preventDefault();
        pick(items[activeIndex]);
      }
    } else if (event.key === "Escape" && !menu.hidden) {
      event.preventDefault();
      event.stopPropagation();
      closeCombo(input, menu);
      activeIndex = -1;
    }
  });
  input.addEventListener("blur", () => {
    setTimeout(() => {
      if (document.activeElement !== input) {
        closeCombo(input, menu);
      }
    }, 120);
  });

  return { refresh: update };
}
