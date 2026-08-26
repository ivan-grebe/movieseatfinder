function byId(id) {
  const element = document.querySelector(`#${id}`);
  if (!element) {
    throw new Error(`Missing required element #${id}`);
  }
  return element;
}

export const elements = {
  adjacentSeatsInput: byId("adjacentSeatsInput"),
  cancelSeatGridButton: byId("cancelSeatGridButton"),
  clearGridButton: byId("clearGridButton"),
  doneSeatGridButton: byId("doneSeatGridButton"),
  editSeatGridButton: byId("editSeatGridButton"),
  endDateInput: byId("endDateInput"),
  endTimeInput: byId("endTimeInput"),
  excludeAccessibleInput: byId("excludeAccessibleInput"),
  formatGuide: byId("formatGuide"),
  formatGuideButton: byId("formatGuideButton"),
  formatGuideContent: byId("formatGuideContent"),
  formatMeta: byId("formatMeta"),
  formatOptions: byId("formatOptions"),
  formatStatus: byId("formatStatus"),
  gridStatus: byId("gridStatus"),
  locationStatus: byId("locationStatus"),
  movieGroup: byId("movieGroup"),
  movieInput: byId("movieInput"),
  movieMenu: byId("movieMenu"),
  movieMeta: byId("movieMeta"),
  movieStatus: byId("movieStatus"),
  pagination: byId("pagination"),
  preferencesGroup: byId("preferencesGroup"),
  radiusInput: byId("radiusInput"),
  radiusStatus: byId("radiusStatus"),
  results: byId("results"),
  resultsToolbar: byId("resultsToolbar"),
  searchButton: byId("searchButton"),
  searchForm: byId("searchForm"),
  seatPreferenceGrid: byId("seatPreferenceGrid"),
  seatPreferenceHelp: byId("seatPreferenceHelp"),
  selectCenterGridButton: byId("selectCenterGridButton"),
  sortInput: byId("sortInput"),
  sortStatus: byId("sortStatus"),
  startDateInput: byId("startDateInput"),
  startTimeInput: byId("startTimeInput"),
  summary: byId("summary"),
  theatreInput: byId("theatreInput"),
  theatreMenu: byId("theatreMenu"),
  theatreMeta: byId("theatreMeta"),
  theatreStatus: byId("theatreStatus"),
  useLocationButton: byId("useLocationButton"),
  zipInput: byId("zipInput"),
};
