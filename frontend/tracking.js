const TICKET_CLICK_ENDPOINT = "/api/events/ticket-click";

export function logTicketClick() {
  try {
    if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
      if (navigator.sendBeacon(TICKET_CLICK_ENDPOINT)) return;
    }

    if (typeof fetch !== "undefined") {
      void fetch(TICKET_CLICK_ENDPOINT, {
        method: "POST",
        keepalive: true,
      }).catch(() => {});
    }
  } catch {
    // Tracking must never interrupt the ticket purchase flow.
  }
}
