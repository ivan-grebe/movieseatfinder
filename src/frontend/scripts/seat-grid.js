import { setStatus } from "./ui.js";

const GRID_SIZE = 15;
const MOBILE_LAYOUT = "(max-width: 700px)";
const DESKTOP_HELP =
  "Drag to mark where you'd like to sit. Top is the screen. Shift-drag to erase. Leave blank for anywhere.";
const MOBILE_LOCKED_HELP = "Tap Edit seat area to choose where you'd like to sit.";
const MOBILE_EDITING_HELP =
  "Drag across the grid to highlight your preferred area. Top is the screen.";
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
  let isPainting = false;
  let paintMode = true;
  let dragStart = null;
  let selectionBeforeDrag = new Set();
  let dragMoved = false;
  let focus = { col: 0, row: 0 };
  let anchor = { col: 0, row: 0 };
  // Selection snapshot from before a Shift+Arrow rectangle started, so keyboard
  // Rectangles add to the existing selection the same way pointer drags do.
  let keyboardRectBase = null;
  // Pointer interactions handle their own toggling; the click event that
  // Trails them must be ignored without relying on event.detail, which not
  // Every browser zeroes for keyboard activations.
  let suppressNextClick = false;

  function mobileInteractionLocked() {
    return isMobileLayout && !isMobileEditing;
  }

  function setAnchor(row, col) {
    anchor = { col, row };
    keyboardRectBase = null;
  }

  function updateStatus() {
    const count = selected.size;
    if (count) {
      let suffix = "s";
      if (count === 1) {
        suffix = "";
      }
      setStatus(status, `${count} seat area${suffix} highlighted.`, "success");
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

  function selectBox(rowStart, rowEnd, colStart, colEnd) {
    clear();
    for (let row = rowStart; row <= rowEnd; row += 1) {
      for (let col = colStart; col <= colEnd; col += 1) {
        setCell(row, col, true);
      }
    }
    updateStatus();
  }

  function restoreSelection(snapshot) {
    for (let row = 0; row < GRID_SIZE; row += 1) {
      for (let col = 0; col < GRID_SIZE; col += 1) {
        setCell(row, col, snapshot.has(cellKey(row, col)));
      }
    }
  }

  function setRovingCell(row, col) {
    focus = { col, row };
    cells.forEach((cell) => {
      cell.tabIndex = -1;
      if (cell.dataset.cell === cellKey(row, col)) {
        cell.tabIndex = 0;
      }
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
    let helpText = DESKTOP_HELP;
    if (isMobileLayout) {
      helpText = MOBILE_LOCKED_HELP;
      if (isMobileEditing) {
        helpText = MOBILE_EDITING_HELP;
      }
    }
    help.textContent = helpText;
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

  function applyRectangle(cell) {
    const current = { col: Number(cell.dataset.col), row: Number(cell.dataset.row) };
    restoreSelection(selectionBeforeDrag);
    for (
      let row = Math.min(dragStart.row, current.row);
      row <= Math.max(dragStart.row, current.row);
      row += 1
    ) {
      for (
        let col = Math.min(dragStart.col, current.col);
        col <= Math.max(dragStart.col, current.col);
        col += 1
      ) {
        setCell(row, col, paintMode);
      }
    }
    updateStatus();
  }

  function handleCellClick(event) {
    if (mobileInteractionLocked() || suppressNextClick) {
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
        button.tabIndex = -1;
        if (row === 0 && col === 0) {
          button.tabIndex = 0;
        }
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
    isPainting = true;
    keyboardRectBase = null;
    paintMode = !(event.altKey || event.ctrlKey || event.metaKey || event.shiftKey);
    dragStart = { col: Number(cell.dataset.col), row: Number(cell.dataset.row) };
    selectionBeforeDrag = new Set(selected);
    dragMoved = false;
    grid.setPointerCapture(event.pointerId);
    applyRectangle(cell);
  });

  grid.addEventListener("pointermove", (event) => {
    if (!isPainting) {
      return;
    }
    event.preventDefault();
    const cell = cellFromEvent(event);
    if (!cell) {
      return;
    }
    if (Number(cell.dataset.row) !== dragStart.row || Number(cell.dataset.col) !== dragStart.col) {
      dragMoved = true;
    }
    applyRectangle(cell);
  });

  grid.addEventListener("pointerup", (event) => {
    if (!isPainting) {
      return;
    }
    const cell = cellFromEvent(event);
    if (!dragMoved) {
      restoreSelection(selectionBeforeDrag);
      const key = cellKey(dragStart.row, dragStart.col);
      let shouldSelect = false;
      if (paintMode) {
        shouldSelect = !selectionBeforeDrag.has(key);
      }
      setCell(dragStart.row, dragStart.col, shouldSelect);
      updateStatus();
    } else if (cell) {
      applyRectangle(cell);
    }
    isPainting = false;
    dragStart = null;
    selectionBeforeDrag = new Set();
    dragMoved = false;
    suppressNextClick = true;
    if (grid.hasPointerCapture(event.pointerId)) {
      grid.releasePointerCapture(event.pointerId);
    }
  });

  grid.addEventListener("pointercancel", () => {
    if (!isPainting) {
      return;
    }
    restoreSelection(selectionBeforeDrag);
    updateStatus();
    isPainting = false;
    dragStart = null;
    selectionBeforeDrag = new Set();
    dragMoved = false;
    suppressNextClick = false;
  });

  // Runs after the targeted cell's own click handler, so the flag only ever
  // Swallows the one click generated by the pointer interaction above.
  grid.addEventListener("click", () => {
    suppressNextClick = false;
  });

  grid.addEventListener("focusin", (event) => {
    const cell = event.target.closest(".seat-cell");
    focus = { col: Number(cell.dataset.col), row: Number(cell.dataset.row) };
    cells.forEach((other) => {
      other.tabIndex = -1;
      if (other === cell) {
        other.tabIndex = 0;
      }
    });
  });

  grid.addEventListener("keydown", (event) => {
    if (mobileInteractionLocked()) {
      return;
    }
    suppressNextClick = false;
    if (event.key in GRID_MOVES) {
      event.preventDefault();
      const [deltaRow, deltaCol] = GRID_MOVES[event.key];
      const row = Math.min(GRID_SIZE - 1, Math.max(0, focus.row + deltaRow));
      const col = Math.min(GRID_SIZE - 1, Math.max(0, focus.col + deltaCol));
      setRovingCell(row, col);
      if (event.shiftKey) {
        // Repaint the rectangle over the pre-rectangle snapshot so cells
        // Outside it keep their state, matching pointer-drag behaviour.
        if (!keyboardRectBase) {
          keyboardRectBase = new Set(selected);
        }
        restoreSelection(keyboardRectBase);
        for (
          let boxRow = Math.min(anchor.row, row);
          boxRow <= Math.max(anchor.row, row);
          boxRow += 1
        ) {
          for (
            let boxCol = Math.min(anchor.col, col);
            boxCol <= Math.max(anchor.col, col);
            boxCol += 1
          ) {
            setCell(boxRow, boxCol, true);
          }
        }
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
      let row = focus.row;
      if (event.ctrlKey) {
        row = col;
      }
      setRovingCell(row, col);
      setAnchor(row, col);
    }
  });

  centerButton.addEventListener("click", () => {
    const start = Math.floor(GRID_SIZE / 3);
    const end = GRID_SIZE - start - 1;
    selectBox(start, end, start, end);
    setAnchor(start, start);
  });
  clearButton.addEventListener("click", () => {
    keyboardRectBase = null;
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
