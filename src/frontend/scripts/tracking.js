const TICKET_CLICK_ENDPOINT = "/api/events/ticket-click";

export function logTicketClick() {
  try {
    if (navigator.sendBeacon?.(TICKET_CLICK_ENDPOINT)) {
      return;
    }
    void fetch(TICKET_CLICK_ENDPOINT, { keepalive: true, method: "POST" }).catch(() => {
      // Tracking failures are intentionally ignored.
    });
  } catch {
    // Tracking must never interrupt the ticket purchase flow.
  }
}
