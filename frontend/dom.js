function byId(id) {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing required element #${id}`);
  return element;
}

export const elements = {
  searchForm: byId("searchForm"),
  zipInput: byId("zipInput"),
  useLocationButton: byId("useLocationButton"),
  locationStatus: byId("locationStatus"),
  radiusInput: byId("radiusInput"),
  radiusStatus: byId("radiusStatus"),
  startDateInput: byId("startDateInput"),
  endDateInput: byId("endDateInput"),
  theatreStatus: byId("theatreStatus"),
  theatreInput: byId("theatreInput"),
  theatreMenu: byId("theatreMenu"),
  movieStatus: byId("movieStatus"),
  movieInput: byId("movieInput"),
  movieMenu: byId("movieMenu"),
  formatOptions: byId("formatOptions"),
  formatStatus: byId("formatStatus"),
  startTimeInput: byId("startTimeInput"),
  endTimeInput: byId("endTimeInput"),
  adjacentSeatsInput: byId("adjacentSeatsInput"),
  excludeAccessibleInput: byId("excludeAccessibleInput"),
  seatPreferenceGrid: byId("seatPreferenceGrid"),
  selectCenterGridButton: byId("selectCenterGridButton"),
  clearGridButton: byId("clearGridButton"),
  gridStatus: byId("gridStatus"),
  searchButton: byId("searchButton"),
  summary: byId("summary"),
  results: byId("results"),
  pagination: byId("pagination"),
};
