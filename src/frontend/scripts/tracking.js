function logEvent(endpoint) {
  try {
    if (navigator.sendBeacon?.(endpoint)) {
      return;
    }
    void fetch(endpoint, { keepalive: true, method: "POST" }).catch(() => {
      // Tracking failures are intentionally ignored.
    });
  } catch {
    // Tracking must never interrupt the user flow.
  }
}

export function logTicketClick() {
  logEvent("/api/events/ticket-click");
}

export function logSharedLinkVisit() {
  logEvent("/api/events/shared-link-visit");
}
