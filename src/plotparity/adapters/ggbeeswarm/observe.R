# ggbeeswarm adapter: R-side observation hook using core engines/r/panel_geometry.R.
adapter_json_digits <- NA
adapter_setup <- function(family) invisible(NULL)
adapter_observe <- function(p, w_in, h_in, dpi) {
  list(ggbeeswarm = extract_panel_geometry(p, w_in, h_in, dpi, kinds = "points"))
}
