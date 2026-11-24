function logEvent(endpoint) {
  if (navigator.sendBeacon?.(endpoint)) {
    return;
  }

  void fetch(endpoint, {
    keepalive: true,
    method: "POST",
  }).catch(() => undefined);
}

export function logTicketClick() {
  logEvent("/api/events/ticket-click");
}

export function logSharedLinkVisit() {
  logEvent("/api/events/shared-link-visit");
}
