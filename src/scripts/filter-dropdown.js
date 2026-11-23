const SEARCHABLE_OPTION_COUNT = 8;

function createCheckbox(text, checked, onToggle) {
  const input = document.createElement("input");

  input.type = "checkbox";
  input.checked = checked;
  input.addEventListener("change", () => onToggle(input.checked));
  const box = document.createElement("span");

  box.className = "check-box";
  box.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");

  label.textContent = text;
  const option = document.createElement("label");

  option.className = "check";
  option.append(input, box, label);
  return {
    input: input,
    option: option,
  };
}

/**
 * A button that opens a checklist where checked means included and everything starts
 * checked. The selection is kept as either "all except these" or "only these", whichever
 * the user built, so it stays short in URLs and survives the option list reloading.
 */
export function createFilterDropdown(container, { allLabel, noun, onChange, placeholder }) {
  const button = container.querySelector(".filter-button");
  const buttonLabel = container.querySelector(".filter-button-label");
  const popover = container.querySelector(".filter-popover");
  const search = container.querySelector(".filter-search");
  const list = container.querySelector(".filter-list");
  let options = [];
  let included = null;
  const excluded = new Set();
  let query = "";

  function isChecked(option) {
    if (included) {
      return included.has(option);
    }

    return !excluded.has(option);
  }

  function checkedOptions() {
    return options.filter((option) => isChecked(option));
  }

  function summary() {
    if (options.length === 0) {
      return placeholder;
    }

    const picked = checkedOptions();
    const left = options.filter((option) => !isChecked(option));

    if (left.length === 0) {
      return allLabel(options.length);
    }

    if (picked.length === 0) {
      return `No ${noun} selected`;
    }

    if (picked.length <= 2) {
      return picked.join(", ");
    }

    if (left.length === 1) {
      return `All except ${left[0]}`;
    }

    if (left.length < picked.length) {
      return `All except ${left.length} ${noun}`;
    }

    return `${picked.length} ${noun}`;
  }

  function changed() {
    render();
    onChange();
  }

  function setChecked(option, checked) {
    if (included) {
      if (checked) {
        included.add(option);
      } else {
        included.delete(option);
      }

      if (included.size === options.length) {
        included = null;
      }
    } else if (checked) {
      excluded.delete(option);
    } else {
      excluded.add(option);
    }

    changed();
  }

  function renderRow(option) {
    const { option: label } = createCheckbox(option, isChecked(option), (checked) =>
      setChecked(option, checked),
    );
    const row = document.createElement("div");

    row.className = "filter-row";
    row.append(label);
    return row;
  }

  function renderSelectAll() {
    const picked = checkedOptions().length;
    const { input, option } = createCheckbox(
      `Select all (${options.length})`,
      picked === options.length,
      (checked) => {
        if (checked) {
          included = null;
        } else {
          included = new Set();
        }

        excluded.clear();
        changed();
      },
    );

    input.indeterminate = picked > 0 && picked < options.length;
    const row = document.createElement("div");

    row.className = "filter-row filter-select-all";
    row.append(option);
    return row;
  }

  function render() {
    buttonLabel.textContent = summary();
    button.classList.toggle(
      "is-filtered",
      options.length > 0 && checkedOptions().length < options.length,
    );
    const visible = options.filter((option) => option.toLowerCase().includes(query));
    const rows = [];

    if (!query) {
      rows.push(renderSelectAll());
    }

    rows.push(...visible.map((option) => renderRow(option)));
    if (visible.length === 0) {
      const empty = document.createElement("p");

      empty.className = "filter-empty";
      empty.textContent = `No ${noun} match “${query}”.`;
      rows.push(empty);
    }

    const { scrollTop } = list;

    list.replaceChildren(...rows);
    list.scrollTop = scrollTop;
  }

  function setOpen(open) {
    popover.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    if (open && search) {
      search.hidden = options.length <= SEARCHABLE_OPTION_COUNT;
      search.value = "";
      query = "";
      render();
    }
  }

  /** Replace the options, keeping the selection for any that are still listed. */
  function setOptions(values) {
    options = [...new Set(values)];
    excluded.forEach((option) => {
      if (!options.includes(option)) {
        excluded.delete(option);
      }
    });
    if (included) {
      included = new Set(options.filter((option) => included.has(option)));
      if (included.size === 0 || included.size === options.length) {
        included = null;
      }
    }

    button.disabled = options.length === 0;
    if (options.length === 0) {
      setOpen(false);
    }

    render();
  }

  /** Restore a selection (for example from the URL) before or after options load. */
  function select({ include = [], exclude = [] }) {
    if (include.length > 0) {
      included = new Set(include);
    } else {
      included = null;
    }

    excluded.clear();
    exclude.forEach((option) => excluded.add(option));
    if (options.length > 0) {
      // Drop names that aren't listed, as a fresh option list would.
      setOptions(options);
    } else {
      render();
    }
  }

  function setBusy(busy) {
    if (busy) {
      button.setAttribute("aria-busy", "true");
      button.disabled = true;
      setOpen(false);
    } else {
      button.removeAttribute("aria-busy");
      button.disabled = options.length === 0;
    }
  }

  button.addEventListener("click", () => setOpen(popover.hidden));
  search?.addEventListener("input", () => {
    query = search.value.trim().toLowerCase();
    render();
  });
  document.addEventListener("click", (event) => {
    // The path is captured at dispatch, so clicks on rows that re-rendered still count as inside.
    if (!popover.hidden && !event.composedPath().includes(container)) {
      setOpen(false);
    }
  });
  container.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !popover.hidden) {
      event.preventDefault();
      setOpen(false);
      button.focus();
    }
  });
  container.addEventListener("focusout", (event) => {
    if (!popover.hidden && event.relatedTarget && !container.contains(event.relatedTarget)) {
      setOpen(false);
    }
  });
  render();

  return {
    checked: checkedOptions,
    isAll: () => included === null && excluded.size === 0,
    isBusy: () => button.hasAttribute("aria-busy"),
    isEmpty: () => options.length > 0 && checkedOptions().length === 0,
    /** The shortest way to send the selection: an include list, an exclude list, or neither. */
    params() {
      if (included) {
        return {
          exclude: [],
          include: [...included],
        };
      }

      return {
        exclude: [...excluded],
        include: [],
      };
    },
    select: select,
    setBusy: setBusy,
    setOptions: setOptions,
  };
}
