import { setStatus } from "./ui.js";

const GRID_SIZE = 15;
const MOBILE_LAYOUT = "(max-width: 700px)";
const DESKTOP_HELP =
  "Drag to mark where you'd like to sit. Top is the screen. Shift-drag to erase. Leave blank for anywhere.";
const MOBILE_LOCKED_HELP = "Tap Edit seat area to choose where you'd like to sit.";
const MOBILE_EDITING_HELP =
  "Drag across the grid to highlight your preferred area. Top is the screen.";
const IDLE = Object.freeze({
  kind: "idle",
});
const GRID_MOVES = {
  ArrowDown: [1, 0],
  ArrowLeft: [0, -1],
  ArrowRight: [0, 1],
  ArrowUp: [-1, 0],
};

function cellKey(row, col) {
  return `${row}:${col}`;
}

function cellFromEvent(event) {
  const element = document.elementFromPoint(event.clientX, event.clientY);

  return element?.closest?.(".seat-cell") || null;
}

export function createSeatGrid(
  grid,
  status,
  centerButton,
  clearButton,
  { help, editButton, cancelButton, doneButton },
) {
  const selected = new Set();
  const cells = new Map();
  const mobileLayout = globalThis.matchMedia(MOBILE_LAYOUT);
  let isMobileLayout = mobileLayout.matches;
  let isMobileEditing = false;
  let selectionBeforeMobileEdit = null;
  let gesture = IDLE;
  let focus = {
    col: 0,
    row: 0,
  };
  let anchor = {
    col: 0,
    row: 0,
  };

  function mobileInteractionLocked() {
    return isMobileLayout && !isMobileEditing;
  }

  function endKeyboardRect() {
    if (gesture.kind === "keyboardRect") {
      gesture = IDLE;
    }
  }

  function setAnchor(row, col) {
    anchor = {
      col: col,
      row: row,
    };
    endKeyboardRect();
  }

  function updateStatus() {
    const count = selected.size;

    if (count) {
      const areas = count === 1 ? "seat area" : "seat areas";

      setStatus(status, `${count} ${areas} highlighted.`, "success");
    } else {
      setStatus(status, "No area highlighted - matching seats anywhere.");
    }
  }

  function setCell(row, col, isSelected) {
    const key = cellKey(row, col);
    const cell = cells.get(key);

    if (isSelected) {
      selected.add(key);
    } else {
      selected.delete(key);
    }

    cell.classList.toggle("selected", isSelected);
    cell.setAttribute("aria-pressed", String(isSelected));
  }

  function clear() {
    [...selected].forEach((key) => {
      const [row, col] = key.split(":").map(Number);

      setCell(row, col, false);
    });
    updateStatus();
  }

  /** Set every cell in the rectangle between two corners, in any order. */
  function fillRectangle(corner, otherCorner, isSelected) {
    const rowEnd = Math.max(corner.row, otherCorner.row);
    const colEnd = Math.max(corner.col, otherCorner.col);

    for (let row = Math.min(corner.row, otherCorner.row); row <= rowEnd; row += 1) {
      for (let col = Math.min(corner.col, otherCorner.col); col <= colEnd; col += 1) {
        setCell(row, col, isSelected);
      }
    }
  }

  function restoreSelection(snapshot) {
    for (let row = 0; row < GRID_SIZE; row += 1) {
      for (let col = 0; col < GRID_SIZE; col += 1) {
        setCell(row, col, snapshot.has(cellKey(row, col)));
      }
    }
  }

  function setRovingCell(row, col) {
    focus = {
      col: col,
      row: row,
    };
    const focusedKey = cellKey(row, col);

    cells.forEach((cell) => {
      cell.tabIndex = cell.dataset.cell === focusedKey ? 0 : -1;
    });
    cells.get(cellKey(row, col)).focus();
  }

  function syncMobileInteraction() {
    const locked = mobileInteractionLocked();

    grid.classList.toggle("is-mobile-locked", locked);
    grid.classList.toggle("is-mobile-editing", isMobileLayout && isMobileEditing);
    grid.toggleAttribute("aria-disabled", locked);

    cells.forEach((cell) => {
      cell.toggleAttribute("aria-disabled", locked);
      cell.tabIndex = -1;
      if (!locked && cell.dataset.cell === cellKey(focus.row, focus.col)) {
        cell.tabIndex = 0;
      }
    });

    editButton.hidden = !locked;
    centerButton.hidden = locked;
    clearButton.hidden = locked;
    cancelButton.hidden = !isMobileLayout || !isMobileEditing;
    doneButton.hidden = !isMobileLayout || !isMobileEditing;
    centerButton.disabled = locked;
    clearButton.disabled = locked;
    if (!isMobileLayout) {
      help.textContent = DESKTOP_HELP;
    } else if (isMobileEditing) {
      help.textContent = MOBILE_EDITING_HELP;
    } else {
      help.textContent = MOBILE_LOCKED_HELP;
    }
  }

  function beginMobileEditing() {
    if (!isMobileLayout || isMobileEditing) {
      return;
    }

    selectionBeforeMobileEdit = new Set(selected);
    isMobileEditing = true;
    syncMobileInteraction();
    doneButton.focus();
  }

  function finishMobileEditing(restorePreviousSelection) {
    if (!isMobileEditing) {
      return;
    }

    if (restorePreviousSelection && selectionBeforeMobileEdit) {
      restoreSelection(selectionBeforeMobileEdit);
      updateStatus();
    }

    selectionBeforeMobileEdit = null;
    isMobileEditing = false;
    syncMobileInteraction();
    editButton.focus();
  }

  function applyRectangle(drag, cell) {
    const current = {
      col: Number(cell.dataset.col),
      row: Number(cell.dataset.row),
    };

    restoreSelection(drag.before);
    fillRectangle(drag.start, current, drag.paint);
    updateStatus();
  }

  function handleCellClick(event) {
    if (mobileInteractionLocked() || gesture.kind === "released") {
      return;
    }

    const button = event.currentTarget;
    const buttonRow = Number(button.dataset.row);
    const buttonCol = Number(button.dataset.col);

    setCell(buttonRow, buttonCol, !selected.has(cellKey(buttonRow, buttonCol)));
    setAnchor(buttonRow, buttonCol);
    updateStatus();
  }

  function build() {
    grid.replaceChildren();
    cells.clear();
    for (let row = 0; row < GRID_SIZE; row += 1) {
      for (let col = 0; col < GRID_SIZE; col += 1) {
        const button = document.createElement("button");

        button.type = "button";
        button.className = "seat-cell";
        button.dataset.cell = cellKey(row, col);
        button.dataset.row = row;
        button.dataset.col = col;
        button.title = `Row ${row + 1} of ${GRID_SIZE} from the screen, column ${col + 1} of ${GRID_SIZE}`;
        button.setAttribute("aria-label", button.title);
        button.setAttribute("aria-pressed", "false");
        button.tabIndex = row === 0 && col === 0 ? 0 : -1;
        button.addEventListener("click", handleCellClick);
        cells.set(button.dataset.cell, button);
        grid.append(button);
      }
    }

    updateStatus();
    syncMobileInteraction();
  }

  function selectValues(values) {
    values.forEach((value) => {
      const [row, col] = value.split(":").map(Number);

      if (
        Number.isInteger(row) &&
        Number.isInteger(col) &&
        row >= 0 &&
        row < GRID_SIZE &&
        col >= 0 &&
        col < GRID_SIZE
      ) {
        setCell(row, col, true);
      }
    });
    updateStatus();
  }

  grid.addEventListener("pointerdown", (event) => {
    const cell = event.target.closest(".seat-cell");

    if (!cell || mobileInteractionLocked()) {
      return;
    }

    event.preventDefault();
    gesture = {
      before: new Set(selected),
      kind: "dragging",
      moved: false,
      paint: !(event.altKey || event.ctrlKey || event.metaKey || event.shiftKey),
      start: {
        col: Number(cell.dataset.col),
        row: Number(cell.dataset.row),
      },
    };
    grid.setPointerCapture(event.pointerId);
    applyRectangle(gesture, cell);
  });

  grid.addEventListener("pointermove", (event) => {
    if (gesture.kind !== "dragging") {
      return;
    }

    event.preventDefault();
    const cell = cellFromEvent(event);

    if (!cell) {
      return;
    }

    if (
      Number(cell.dataset.row) !== gesture.start.row ||
      Number(cell.dataset.col) !== gesture.start.col
    ) {
      gesture.moved = true;
    }

    applyRectangle(gesture, cell);
  });

  grid.addEventListener("pointerup", (event) => {
    if (gesture.kind !== "dragging") {
      return;
    }

    const drag = gesture;
    const cell = cellFromEvent(event);

    if (!drag.moved) {
      restoreSelection(drag.before);
      const key = cellKey(drag.start.row, drag.start.col);

      setCell(drag.start.row, drag.start.col, drag.paint && !drag.before.has(key));
      updateStatus();
    } else if (cell) {
      applyRectangle(drag, cell);
    }

    gesture = {
      kind: "released",
    };
    if (grid.hasPointerCapture(event.pointerId)) {
      grid.releasePointerCapture(event.pointerId);
    }
  });

  grid.addEventListener("pointercancel", () => {
    if (gesture.kind !== "dragging") {
      return;
    }

    restoreSelection(gesture.before);
    updateStatus();
    gesture = IDLE;
  });

  grid.addEventListener("click", () => {
    if (gesture.kind === "released") {
      gesture = IDLE;
    }
  });

  grid.addEventListener("focusin", (event) => {
    const cell = event.target.closest(".seat-cell");

    focus = {
      col: Number(cell.dataset.col),
      row: Number(cell.dataset.row),
    };
    cells.forEach((other) => {
      other.tabIndex = other === cell ? 0 : -1;
    });
  });

  grid.addEventListener("keydown", (event) => {
    if (mobileInteractionLocked()) {
      return;
    }

    if (gesture.kind === "released") {
      gesture = IDLE;
    }

    if (event.key in GRID_MOVES) {
      event.preventDefault();
      const [deltaRow, deltaCol] = GRID_MOVES[event.key];
      const row = Math.min(GRID_SIZE - 1, Math.max(0, focus.row + deltaRow));
      const col = Math.min(GRID_SIZE - 1, Math.max(0, focus.col + deltaCol));

      setRovingCell(row, col);
      if (event.shiftKey) {
        if (gesture.kind !== "keyboardRect") {
          gesture = {
            before: new Set(selected),
            kind: "keyboardRect",
          };
        }

        restoreSelection(gesture.before);
        fillRectangle(
          anchor,
          {
            col: col,
            row: row,
          },
          true,
        );
        updateStatus();
      } else {
        setAnchor(row, col);
      }
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      let col = GRID_SIZE - 1;

      if (event.key === "Home") {
        col = 0;
      }

      // Ctrl+Home/End jump to the first/last cell of the grid, not just the row.
      const row = event.ctrlKey ? col : focus.row;

      setRovingCell(row, col);
      setAnchor(row, col);
    }
  });

  centerButton.addEventListener("click", () => {
    const start = Math.floor(GRID_SIZE / 3);
    const end = GRID_SIZE - start - 1;

    clear();
    fillRectangle(
      {
        col: start,
        row: start,
      },
      {
        col: end,
        row: end,
      },
      true,
    );
    updateStatus();
    setAnchor(start, start);
  });
  clearButton.addEventListener("click", () => {
    endKeyboardRect();
    clear();
  });
  editButton.addEventListener("click", beginMobileEditing);
  cancelButton.addEventListener("click", () => finishMobileEditing(true));
  doneButton.addEventListener("click", () => finishMobileEditing(false));
  mobileLayout.addEventListener("change", (event) => {
    isMobileLayout = event.matches;
    isMobileEditing = false;
    selectionBeforeMobileEdit = null;
    syncMobileInteraction();
  });

  build();

  return {
    select: selectValues,
    values: () => [...selected],
  };
}
