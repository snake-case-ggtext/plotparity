# ggridges adapter: R-side observation hooks (sourced by engines/r/render.R).
#   adapter_observe(p, w_in, h_in, dpi): one stream, `ggridges`:
#     panel_x_range / panel_y_range / y_labels   trained panel scales (discrete y: level i sits at y = i)
#     events   every polygon / polyline in the first panel, in paint order, vertices in data units
#     build    the first layer's built data per ridge (x and density, when the stat computes one)
# Colours via col2rgb; line widths in pt (R lwd is 1/96 in).

adapter_json_digits <- NA          # densities need more than jsonlite's default 4 decimals

adapter_setup <- function(family) invisible(NULL)

.rg_col <- function(col) {
  if (is.null(col) || length(col) == 0 || is.na(col[1])) return(list(hex = "none", alpha = 0))
  m <- grDevices::col2rgb(col[1], alpha = TRUE)
  if (m[4] == 0) return(list(hex = "none", alpha = 0))
  list(hex = tolower(sprintf("#%02X%02X%02X", m[1], m[2], m[3])), alpha = round(m[4] / 255, 4))
}

adapter_observe <- function(p, w_in, h_in, dpi) {
  b <- suppressMessages(ggplot_build(p))
  pp <- b$layout$panel_params[[1]]
  xr <- pp$x.range; yr <- pp$y.range
  y_labels <- tryCatch(as.character(b$layout$panel_scales_y[[1]]$get_limits()), error = function(e) character())

  tmp <- tempfile(fileext = ".png")
  agg_png(tmp, width = w_in, height = h_in, units = "in", res = dpi)
  on.exit({ if (dev.cur() > 1) dev.off(); unlink(tmp) }, add = TRUE)
  grid.draw(ggplotGrob(p))
  grid.force()
  vps <- grid.ls(grobs = FALSE, viewports = TRUE, print = FALSE)$name
  pv <- grep("^panel", vps, value = TRUE)[1]
  seekViewport(pv)
  panel_gtree <- grid.get(pv)

  events <- list(); paint <- 0
  conv <- function(xs, ys) {
    loc <- deviceLoc(unit(xs, "native"), unit(ys, "native"), valueOnly = TRUE)
    list(norm = cbind(round(loc$x / w_in, 6), round(1 - loc$y / h_in, 6)),
         data = cbind(xr[1] + xs * diff(xr), yr[1] + ys * diff(yr)))
  }
  walk <- function(gr) {
    if (is.null(gr)) return(invisible())
    if (inherits(gr, "gTree")) {
      if (grepl("^grill|^panel.background|^panel.border", gr$name)) return(invisible())
      for (ch in gr$children) walk(ch)
      return(invisible())
    }
    if (!(inherits(gr, "polygon") || inherits(gr, "polyline") || inherits(gr, "pathgrob") ||
          inherits(gr, "segments"))) return(invisible())
    paint <<- paint + 1
    if (inherits(gr, "segments")) {           # e.g. quantile lines: one piece per segment
      x0 <- convertX(gr$x0, "npc", valueOnly = TRUE); x1 <- convertX(gr$x1, "npc", valueOnly = TRUE)
      y0 <- convertY(gr$y0, "npc", valueOnly = TRUE); y1 <- convertY(gr$y1, "npc", valueOnly = TRUE)
      n <- max(length(x0), length(x1), length(y0), length(y1))
      xs <- as.vector(rbind(rep_len(x0, n), rep_len(x1, n))); ys <- as.vector(rbind(rep_len(y0, n), rep_len(y1, n)))
      id <- rep(seq_len(n), each = 2)
    } else {
      xs <- convertX(gr$x, "npc", valueOnly = TRUE); ys <- convertY(gr$y, "npc", valueOnly = TRUE)
      id <- if (!is.null(gr$id)) gr$id else if (!is.null(gr$pathId)) gr$pathId else rep(1L, length(xs))
    }
    lty <- gr$gp$lty
    blank <- !is.null(lty) && as.character(lty[1]) %in% c("0", "blank")
    col <- if (blank || is.null(gr$gp$col)) list(hex = "none", alpha = 0) else .rg_col(gr$gp$col)
    filled <- inherits(gr, "polygon") || inherits(gr, "pathgrob")
    fil <- if (filled) .rg_col(gr$gp$fill) else list(hex = "none", alpha = 0)
    a <- if (is.null(gr$gp$alpha) || is.na(gr$gp$alpha[1])) 1 else as.numeric(gr$gp$alpha[1])
    lwd <- if (is.null(gr$gp$lwd)) 1 else as.numeric(gr$gp$lwd[1])
    pieces <- list()
    for (pid in sort(unique(id[!is.na(id)]))) {
      sel <- which(!is.na(id) & id == pid & is.finite(xs) & is.finite(ys))
      if (length(sel) < 2 || (length(unique(xs[sel])) < 2 && length(unique(ys[sel])) < 2)) next
      cc <- conv(xs[sel], ys[sel])
      pieces[[length(pieces) + 1]] <- list(data = cc$data, norm = cc$norm)
    }
    events[[length(events) + 1]] <<- list(
      paint_index = paint, filled = filled,
      facecolor_hex = fil$hex, face_alpha = round(fil$alpha * a, 4),
      edgecolor_hex = col$hex, edge_alpha = round(col$alpha * a, 4),
      linewidth_pt = if (col$hex == "none") 0 else round(lwd * 72 / 96, 4),
      pieces = pieces)
  }
  for (ch in panel_gtree$children) walk(ch)
  upViewport(0)

  d <- b$data[[1]]
  build <- list()
  if (all(c("x", "y") %in% names(d))) {
    if ("datatype" %in% names(d)) d <- d[d$datatype == "ridgeline", , drop = FALSE]
    for (pos in sort(unique(round(d$y)))) {
      s <- d[round(d$y) == pos, , drop = FALSE]
      s <- s[order(s$x), , drop = FALSE]
      build[[length(build) + 1]] <- list(pos = pos, x = s$x,
                                         density = if ("density" %in% names(s)) s$density else NULL)
    }
  }
  list(ggridges = list(panel_x_range = xr, panel_y_range = yr, y_labels = y_labels,
                       events = events, build = build))
}
