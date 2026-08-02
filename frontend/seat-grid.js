import { setStatus } from "./ui.js";

const GRID_SIZE = 15;
const GRID_MOVES = {
  ArrowUp: [-1, 0],
  ArrowDown: [1, 0],
  ArrowLeft: [0, -1],
  ArrowRight: [0, 1],
};

const cellKey = (row, col) => `${row}:${col}`;

export function createSeatGrid(grid, status, centerButton, clearButton) {
  const selected = new Set();
  const cells = new Map();
  let isPainting = false;
  let paintMode = true;
  let dragStart = null;
  let selectionBeforeDrag = new Set();
  let dragMoved = false;
  let focus = { row: 0, col: 0 };
  let anchor = { row: 0, col: 0 };
  // Selection snapshot from before a Shift+Arrow rectangle started, so keyboard
  // rectangles add to the existing selection the same way pointer drags do.
  let keyboardRectBase = null;
  // Pointer interactions handle their own toggling; the click event that
  // trails them must be ignored without relying on event.detail, which not
  // every browser zeroes for keyboard activations.
  let suppressNextClick = false;

  function setAnchor(row, col) {
    anchor = { row, col };
    keyboardRectBase = null;
  }

  function updateStatus() {
    const count = selected.size;
    if (count) {
      setStatus(status, `${count} seat area${count === 1 ? "" : "s"} highlighted.`, "success");
    } else {
      setStatus(status, "No area highlighted — matching seats anywhere.");
    }
  }

  function setCell(row, col, isSelected) {
    const key = cellKey(row, col);
    const cell = cells.get(key);
    if (isSelected) selected.add(key);
    else selected.delete(key);
    cell?.classList.toggle("selected", isSelected);
    cell?.setAttribute("aria-pressed", String(isSelected));
  }

  function clear() {
    [...selected].forEach(key => {
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

  function setRovingCell(row, col, shouldFocus = true) {
    focus = { row, col };
    cells.forEach(cell => {
      cell.tabIndex = cell.dataset.cell === cellKey(row, col) ? 0 : -1;
    });
    if (shouldFocus) cells.get(cellKey(row, col))?.focus();
  }

  function cellFromEvent(event) {
    const element = document.elementFromPoint(event.clientX, event.clientY);
    return element?.closest?.(".seat-cell") || null;
  }

  function applyRectangle(cell) {
    if (!cell || !grid.contains(cell)) return;
    const current = { row: Number(cell.dataset.row), col: Number(cell.dataset.col) };
    restoreSelection(selectionBeforeDrag);
    for (let row = Math.min(dragStart.row, current.row); row <= Math.max(dragStart.row, current.row); row += 1) {
      for (let col = Math.min(dragStart.col, current.col); col <= Math.max(dragStart.col, current.col); col += 1) {
        setCell(row, col, paintMode);
      }
    }
    updateStatus();
  }

  function build() {
    grid.innerHTML = "";
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
        button.addEventListener("click", () => {
          if (suppressNextClick || isPainting) return;
          setCell(row, col, !selected.has(cellKey(row, col)));
          setAnchor(row, col);
          updateStatus();
        });
        cells.set(button.dataset.cell, button);
        grid.appendChild(button);
      }
    }
    updateStatus();
  }

  function selectValues(values) {
    values.forEach(value => {
      const [row, col] = value.split(":").map(Number);
      if (Number.isInteger(row) && Number.isInteger(col) && row >= 0 && row < GRID_SIZE && col >= 0 && col < GRID_SIZE) {
        setCell(row, col, true);
      }
    });
    updateStatus();
  }

  grid.addEventListener("pointerdown", event => {
    const cell = event.target.closest(".seat-cell");
    if (!cell) return;
    event.preventDefault();
    isPainting = true;
    keyboardRectBase = null;
    paintMode = !(event.altKey || event.ctrlKey || event.metaKey || event.shiftKey);
    dragStart = { row: Number(cell.dataset.row), col: Number(cell.dataset.col) };
    selectionBeforeDrag = new Set(selected);
    dragMoved = false;
    grid.setPointerCapture(event.pointerId);
    applyRectangle(cell);
  });

  grid.addEventListener("pointermove", event => {
    if (!isPainting) return;
    event.preventDefault();
    const cell = cellFromEvent(event);
    if (!cell) return;
    if (Number(cell.dataset.row) !== dragStart.row || Number(cell.dataset.col) !== dragStart.col) dragMoved = true;
    applyRectangle(cell);
  });

  grid.addEventListener("pointerup", event => {
    if (!isPainting) return;
    const cell = cellFromEvent(event);
    if (!dragMoved && dragStart) {
      restoreSelection(selectionBeforeDrag);
      const key = cellKey(dragStart.row, dragStart.col);
      setCell(dragStart.row, dragStart.col, paintMode ? !selectionBeforeDrag.has(key) : false);
      updateStatus();
    } else if (cell) {
      applyRectangle(cell);
    }
    isPainting = false;
    dragStart = null;
    selectionBeforeDrag = new Set();
    dragMoved = false;
    suppressNextClick = true;
    if (grid.hasPointerCapture(event.pointerId)) grid.releasePointerCapture(event.pointerId);
  });

  grid.addEventListener("pointercancel", () => {
    restoreSelection(selectionBeforeDrag);
    updateStatus();
    isPainting = false;
    dragStart = null;
    selectionBeforeDrag = new Set();
    dragMoved = false;
    suppressNextClick = false;
  });

  // Runs after the targeted cell's own click handler, so the flag only ever
  // swallows the one click generated by the pointer interaction above.
  grid.addEventListener("click", () => {
    suppressNextClick = false;
  });

  grid.addEventListener("focusin", event => {
    const cell = event.target.closest(".seat-cell");
    if (!cell) return;
    focus = { row: Number(cell.dataset.row), col: Number(cell.dataset.col) };
    cells.forEach(other => {
      other.tabIndex = other === cell ? 0 : -1;
    });
  });

  grid.addEventListener("keydown", event => {
    suppressNextClick = false;
    if (event.key in GRID_MOVES) {
      event.preventDefault();
      const [deltaRow, deltaCol] = GRID_MOVES[event.key];
      const row = Math.min(GRID_SIZE - 1, Math.max(0, focus.row + deltaRow));
      const col = Math.min(GRID_SIZE - 1, Math.max(0, focus.col + deltaCol));
      setRovingCell(row, col);
      if (event.shiftKey) {
        // Repaint the rectangle over the pre-rectangle snapshot so cells
        // outside it keep their state, matching pointer-drag behaviour.
        if (!keyboardRectBase) keyboardRectBase = new Set(selected);
        restoreSelection(keyboardRectBase);
        for (let boxRow = Math.min(anchor.row, row); boxRow <= Math.max(anchor.row, row); boxRow += 1) {
          for (let boxCol = Math.min(anchor.col, col); boxCol <= Math.max(anchor.col, col); boxCol += 1) {
            setCell(boxRow, boxCol, true);
          }
        }
        updateStatus();
      } else {
        setAnchor(row, col);
      }
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      const col = event.key === "Home" ? 0 : GRID_SIZE - 1;
      const row = event.ctrlKey ? col : focus.row;
      setRovingCell(row, col);
      setAnchor(row, col);
    }
  });

  centerButton.addEventListener("click", () => {
    // The middle third of the grid in both directions.
    const start = Math.floor(GRID_SIZE / 3);
    const end = GRID_SIZE - start - 1;
    selectBox(start, end, start, end);
    setAnchor(start, start);
  });
  clearButton.addEventListener("click", () => {
    keyboardRectBase = null;
    clear();
  });

  build();

  return {
    select: selectValues,
    values: () => [...selected],
  };
}
