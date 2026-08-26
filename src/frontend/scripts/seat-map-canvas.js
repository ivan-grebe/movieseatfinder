const CANVAS_MAX_DIMENSION = 960;
const ACCESSIBLE_SEAT_TYPES = new Set(["wheelchair", "companion"]);

function canvasColor(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function seatLabel(seat, accessibleSeatsExcluded) {
  const isAvailable = seat.status === "A";
  let accessibilityType = "";
  if (ACCESSIBLE_SEAT_TYPES.has(seat.type)) {
    accessibilityType = seat.type;
  }
  let availabilityLabel = "unavailable";
  if (isAvailable) {
    availabilityLabel = "available";
  }
  let exclusionLabel = "";
  if (isAvailable && accessibilityType && accessibleSeatsExcluded) {
    exclusionLabel = "excluded by filter";
  }
  return [seat.id || "Seat", availabilityLabel, accessibilityType, exclusionLabel]
    .filter(Boolean)
    .join(" - ");
}

function roundedRect(context, x, y, width, height, radius) {
  context.beginPath();
  context.roundRect(x, y, width, height, Math.min(radius, width / 2, height / 2));
}

function seatAppearance(seat, accessibleSeatsExcluded, colors) {
  const isAvailable = seat.status === "A";
  const isAccessible = ACCESSIBLE_SEAT_TYPES.has(seat.type);
  const isExcluded = isAvailable && isAccessible && accessibleSeatsExcluded;

  if (seat.matched) {
    let fill = colors.matched;
    if (isAccessible) {
      fill = "accessible-match";
    }
    return {
      border: colors.matchedBorder,
      fill,
      glow: colors.matchedGlow,
    };
  }
  if (isAvailable && isAccessible && !isExcluded) {
    return {
      border: colors.accessibleBorder,
      fill: colors.accessible,
      glow: colors.accessibleGlow,
    };
  }
  if (isAvailable && !isExcluded) {
    return { border: colors.availableBorder, fill: colors.available, glow: "" };
  }
  return { border: colors.unavailableBorder, fill: colors.unavailable, glow: "" };
}

function drawSeats(canvas, seatMap, accessibleSeatsExcluded, background) {
  const context = canvas.getContext("2d");
  const { height, seats, width } = seatMap.layout;
  const scaleX = canvas.width / width;
  const scaleY = canvas.height / height;
  const visualScale = Math.max(canvas.width / 620, 1);
  const colors = {
    accessible: canvasColor("--seat-accessible"),
    accessibleBorder: canvasColor("--seat-accessible-border"),
    accessibleGlow: "rgba(37, 99, 199, 0.62)",
    available: canvasColor("--seat-available"),
    availableBorder: canvasColor("--seat-available-border"),
    matched: canvasColor("--accent"),
    matchedBorder: canvasColor("--accent-deep"),
    matchedGlow: "rgba(201, 58, 58, 0.62)",
    unavailable: canvasColor("--seat-taken"),
    unavailableBorder: canvasColor("--seat-unavailable-border"),
  };

  context.clearRect(0, 0, canvas.width, canvas.height);
  if (background) {
    context.drawImage(background, 0, 0, canvas.width, canvas.height);
  }

  seats.forEach((seat) => {
    const x = (Number(seat.x) || 0) * scaleX;
    const y = (Number(seat.y) || 0) * scaleY;
    const seatWidth = Math.max(Number(seat.width) || 1, 1) * scaleX;
    const seatHeight = Math.max(Number(seat.height) || 1, 1) * scaleY;
    const appearance = seatAppearance(seat, accessibleSeatsExcluded, colors);
    const radius = 4 * visualScale;

    if (appearance.glow) {
      context.save();
      context.fillStyle = appearance.glow;
      context.shadowBlur = 4 * visualScale;
      context.shadowColor = appearance.glow;
      roundedRect(context, x, y, seatWidth, seatHeight, radius);
      context.fill();
      context.restore();
    }

    if (appearance.fill === "accessible-match") {
      const gradient = context.createLinearGradient(x, y, x + seatWidth, y + seatHeight);
      gradient.addColorStop(0, colors.matched);
      gradient.addColorStop(0.5, colors.matched);
      gradient.addColorStop(0.5, colors.accessible);
      gradient.addColorStop(1, colors.accessible);
      context.fillStyle = gradient;
    } else {
      context.fillStyle = appearance.fill;
    }
    context.strokeStyle = appearance.border;
    context.lineWidth = 1.5 * visualScale;
    roundedRect(context, x, y, seatWidth, seatHeight, radius);
    context.fill();
    context.stroke();
  });
}

function attachSeatTitles(canvas, seats, layoutWidth, layoutHeight, accessibleSeatsExcluded) {
  const defaultTitle = "Seat map";
  canvas.title = defaultTitle;
  canvas.addEventListener("pointermove", (event) => {
    const bounds = canvas.getBoundingClientRect();
    const x = ((event.clientX - bounds.left) / bounds.width) * layoutWidth;
    const y = ((event.clientY - bounds.top) / bounds.height) * layoutHeight;
    const seat = seats.findLast(
      (candidate) =>
        x >= (Number(candidate.x) || 0) &&
        x <= (Number(candidate.x) || 0) + Math.max(Number(candidate.width) || 1, 1) &&
        y >= (Number(candidate.y) || 0) &&
        y <= (Number(candidate.y) || 0) + Math.max(Number(candidate.height) || 1, 1),
    );
    let title = defaultTitle;
    if (seat) {
      title = seatLabel(seat, accessibleSeatsExcluded);
    }
    canvas.title = title;
  });
  canvas.addEventListener("pointerleave", () => {
    canvas.title = defaultTitle;
  });
}

export function createSeatMapCanvas(seatMap, accessibleSeatsExcluded) {
  const { height, seats, width } = seatMap.layout;
  const scale = CANVAS_MAX_DIMENSION / Math.max(width, height);
  const canvas = document.createElement("canvas");
  canvas.className = "real-seat-map-canvas";
  canvas.width = Math.max(Math.round(width * scale), 1);
  canvas.height = Math.max(Math.round(height * scale), 1);
  canvas.setAttribute("role", "img");
  let exclusionNote = "";
  if (accessibleSeatsExcluded) {
    exclusionNote = " Accessible seats are excluded from matches.";
  }
  canvas.setAttribute(
    "aria-label",
    `Seat map with ${seatMap.availableSeatCount} available of ${seatMap.totalSeatCount} total seats.${exclusionNote}`,
  );
  attachSeatTitles(canvas, seats, width, height, accessibleSeatsExcluded);
  drawSeats(canvas, seatMap, accessibleSeatsExcluded);

  if (seatMap.layout.backgroundSvg) {
    const background = new Image();
    background.alt = "";
    background.addEventListener("load", () => {
      drawSeats(canvas, seatMap, accessibleSeatsExcluded, background);
    });
    background.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(seatMap.layout.backgroundSvg)}`;
  }

  return canvas;
}
