const TICKET_CLICK_ENDPOINT = "/api/events/ticket-click";

export function logTicketClick() {
  try {
    if (navigator.sendBeacon?.(TICKET_CLICK_ENDPOINT)) return;
    void fetch(TICKET_CLICK_ENDPOINT, { method: "POST", keepalive: true }).catch(() => {});
  } catch {
    // Tracking must never interrupt the ticket purchase flow.
  }
}
