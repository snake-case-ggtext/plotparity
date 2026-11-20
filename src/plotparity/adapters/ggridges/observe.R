# ggridges adapter: R-side observation hooks (uses core engines/r/panel_geometry.R + built layer density).
adapter_json_digits <- NA          # densities need more than jsonlite's default 4 decimals
adapter_setup <- function(family) invisible(NULL)

adapter_observe <- function(p, w_in, h_in, dpi) {
  pg <- extract_panel_geometry(p, w_in, h_in, dpi, kinds = "shapes")
  p1 <- pg$panels[[1]]

  b <- suppressMessages(ggplot_build(p))
  ridge_idx <- which(vapply(b$data, function(df) any(c("density", "height") %in% names(df)), logical(1)))
  d <- if (length(ridge_idx)) b$data[[ridge_idx[1]]] else b$data[[1]]
  build <- list()
  if (all(c("x", "y") %in% names(d))) {
    if ("datatype" %in% names(d)) d <- d[d$datatype == "ridgeline", , drop = FALSE]
    for (pos in sort(unique(round(d$y)))) {
      s <- d[round(d$y) == pos, , drop = FALSE]
      s <- s[order(s$x), , drop = FALSE]
      build[[length(build) + 1L]] <- list(pos = pos, x = s$x,
                                          density = if ("density" %in% names(s)) s$density else NULL)
    }
  }
  list(ggridges = list(
    panel_x_range = p1$x_range, panel_y_range = p1$y_range, y_labels = p1$y_labels,
    events = p1$shapes, build = build
  ))
}
