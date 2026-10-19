export function initializeHalReveal() {
  const revealWindow = document.querySelector(".hal-easter-egg");
  const locationFields = document.querySelector(".location-group > .grid");
  const movieGroup = document.querySelector(".movie-group");
  const movieFields = movieGroup.querySelector(".grid");
  const guideBody = document.querySelector(".format-guide-body");

  function sizeImage() {
    // The unclipped guide body retains its full height even while collapsed.
    // Size HAL for that final space so the growing window reveals a still image.
    const bottom = guideBody.getBoundingClientRect().bottom;
    const padding = Number.parseFloat(getComputedStyle(movieGroup).paddingBottom);
    const sectionPadding = Number.parseFloat(
      getComputedStyle(locationFields.parentElement).paddingBottom,
    );
    const offset = Number.parseFloat(
      getComputedStyle(revealWindow).getPropertyValue("--hal-offset"),
    );
    const height = Math.max(
      0,
      bottom + padding - sectionPadding - revealWindow.getBoundingClientRect().top - offset,
    );
    revealWindow.style.setProperty("--hal-height", `${height}px`);
  }

  const observer = new ResizeObserver(sizeImage);
  observer.observe(locationFields);
  observer.observe(movieFields);
  observer.observe(guideBody);
}
