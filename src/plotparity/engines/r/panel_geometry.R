# Shared multi-panel R geometry extractor (sourced by engines/r/render.R).
# Opt-in helper for geom adapters (`extract_panel_geometry(p, w_in, h_in, dpi)`):
# Extracts per-panel ranges, discrete labels, coord_flip orientation, bbox_norm_tl,
# and normalized points / polygons / polylines / segments / rasters in paint order.
#
# Centralizes cross-engine R grid traps:
#   - single-panel ("panel.7-5-7-5") vs facetted ("panel-1-1.10-7-10-7") viewport naming & row-major order
#   - pointsGrob 1char sizing = 0.75 * gp$fontsize (not the viewport's default 12pt char!)
#   - vectorized segments gp$col / gp$lwd / gp$alpha and R lwd = 1/96 in -> * 72/96 pt
#   - roundrectGrob(r = 0) forcing to 51 degenerate vertices (unique-vertex rounding check)
#   - inherited gpar alpha down gTree hierarchies

pg_col <- function(col) {
  if (is.null(col) || length(col) == 0 || is.na(col[1])) return(list(hex = "none", alpha = 0))
  m <- tryCatch(grDevices::col2rgb(col[1], alpha = TRUE), error = function(e) NULL)
  if (is.null(m) || m[4] == 0) return(list(hex = "none", alpha = 0))
  list(hex = tolower(sprintf("#%02X%02X%02X", m[1], m[2], m[3])), alpha = round(m[4] / 255, 4))
}

pg_panel_order <- function(pv_names) {
  if (length(pv_names) <= 1) return(pv_names)
  m <- regmatches(pv_names, regexec("^panel-(\\d+)-(\\d+)", pv_names))
  cols <- vapply(m, function(x) if (length(x) >= 3) as.integer(x[2]) else 1L, integer(1))
  rows <- vapply(m, function(x) if (length(x) >= 3) as.integer(x[3]) else 1L, integer(1))
  pv_names[order(rows, cols)]
}

extract_panel_geometry <- function(p, w_in, h_in, dpi, kinds = c("points", "shapes", "rasters")) {
  b <- suppressMessages(ggplot_build(p))
  n_panels <- length(b$layout$panel_params)
  lay <- b$layout$layout
  is_flip <- inherits(b$plot$coordinates, "CoordFlip")

  tmp <- tempfile(fileext = ".png")
  agg_png(tmp, width = w_in, height = h_in, units = "in", res = dpi)
  on.exit({ if (dev.cur() > 1) dev.off(); unlink(tmp) }, add = TRUE)
  grid.draw(ggplotGrob(p))
  grid.force()

  vps <- unique(grep("^panel([.-]|$)", grid.ls(grobs = FALSE, viewports = TRUE, print = FALSE)$name, value = TRUE))
  vps <- pg_panel_order(vps)
  gp_alpha <- function(gp) if (is.null(gp) || is.null(gp$alpha) || is.na(gp$alpha[1])) 1.0 else as.numeric(gp$alpha[1])

  panels <- list()
  for (pi in seq_len(min(length(vps), n_panels))) {
    pp <- b$layout$panel_params[[pi]]
    xr <- as.numeric(pp$x.range)
    yr <- as.numeric(pp$y.range)
    sc_x <- b$layout$panel_scales_x[[lay$SCALE_X[pi]]]
    sc_y <- b$layout$panel_scales_y[[lay$SCALE_Y[pi]]]
    x_disc <- isTRUE(sc_x$is_discrete())
    y_disc <- isTRUE(sc_y$is_discrete())
    x_labs <- if (x_disc) tryCatch(as.character(sc_x$get_limits()), error = function(e) character()) else character()
    y_labs <- if (y_disc) tryCatch(as.character(sc_y$get_limits()), error = function(e) character()) else character()
    if (is_flip) {
      td <- x_disc; x_disc <- y_disc; y_disc <- td
      tl <- x_labs; x_labs <- y_labs; y_labs <- tl
    }

    upViewport(0)
    seekViewport(vps[pi])
    bl <- deviceLoc(unit(0, "npc"), unit(0, "npc"), valueOnly = TRUE)
    tr <- deviceLoc(unit(1, "npc"), unit(1, "npc"), valueOnly = TRUE)
    bbox_norm <- round(c(bl$x / w_in, 1 - tr$y / h_in, tr$x / w_in, 1 - bl$y / h_in), 6)
    panel_gtree <- grid.get(vps[pi])

    pts <- list()
    shapes <- list()
    rasters <- list()
    paint <- 0L

    conv_pts <- function(xs, ys) {
      loc <- deviceLoc(unit(xs, "npc"), unit(ys, "npc"), valueOnly = TRUE)
      list(
        norm = cbind(round(loc$x / w_in, 6), round(1 - loc$y / h_in, 6)),
        data = cbind(round(xr[1] + xs * diff(xr), 6), round(yr[1] + ys * diff(yr), 6))
      )
    }

    walk <- function(gr, inh_alpha = 1.0) {
      if (is.null(gr)) return(invisible())
      cur_a <- inh_alpha * gp_alpha(gr$gp)
      if (inherits(gr, "gTree")) {
        if (grepl("^grill|^panel.background|^panel.border", gr$name)) return(invisible())
        for (ch in gr$children) walk(ch, cur_a)
        return(invisible())
      }
      if ("points" %in% kinds && inherits(gr, "points")) {
        paint <<- paint + 1L
        xs <- convertX(gr$x, "npc", valueOnly = TRUE)
        ys <- convertY(gr$y, "npc", valueOnly = TRUE)
        n <- length(xs)
        if (!n) return(invisible())
        cc <- conv_pts(xs, ys)
        cols <- if (is.null(gr$gp$col)) rep("#000000", n) else rep_len(gr$gp$col, n)
        fills <- if (is.null(gr$gp$fill)) rep(NA, n) else rep_len(gr$gp$fill, n)
        alphas <- if (is.null(gr$gp$alpha)) rep(1, n) else rep_len(as.numeric(gr$gp$alpha), n)
        fss <- if (is.null(gr$gp$fontsize)) rep(12, n) else rep_len(as.numeric(gr$gp$fontsize), n)
        sz_vals <- rep_len(as.numeric(gr$size), n)
        sz_units <- rep_len(tryCatch(as.character(grid::unitType(gr$size)), error = function(e) "char"), n)
        for (k in seq_len(n)) {
          if (!is.finite(xs[k]) || !is.finite(ys[k])) next
          c1 <- pg_col(cols[k])
          f1 <- pg_col(fills[k])
          a1 <- inh_alpha * (if (is.na(alphas[k])) 1 else alphas[k])
          diam <- if (sz_units[k] == "char") 0.75 * fss[k] * sz_vals[k]
                  else convertWidth(gr$size[min(k, length(gr$size))], "bigpts", valueOnly = TRUE)
          eff_hex <- if (f1$hex != "none") f1$hex else c1$hex
          eff_alpha <- round((if (f1$hex != "none") f1$alpha else c1$alpha) * a1, 4)
          pts[[length(pts) + 1L]] <<- list(
            x = cc$data[k, 1], y = cc$data[k, 2],
            x_norm = cc$norm[k, 1], y_norm = cc$norm[k, 2],
            color_hex = eff_hex, alpha = eff_alpha,
            size_pt = round(diam, 3), paint_index = paint
          )
        }
      } else if ("shapes" %in% kinds && (inherits(gr, "polygon") || inherits(gr, "polyline") ||
                                         inherits(gr, "pathgrob") || inherits(gr, "segments"))) {
        paint <<- paint + 1L
        filled <- inherits(gr, "polygon") || inherits(gr, "pathgrob")
        if (inherits(gr, "segments")) {
          x0 <- convertX(gr$x0, "npc", valueOnly = TRUE); x1 <- convertX(gr$x1, "npc", valueOnly = TRUE)
          y0 <- convertY(gr$y0, "npc", valueOnly = TRUE); y1 <- convertY(gr$y1, "npc", valueOnly = TRUE)
          n <- max(length(x0), length(x1), length(y0), length(y1))
          x0 <- rep_len(x0, n); x1 <- rep_len(x1, n); y0 <- rep_len(y0, n); y1 <- rep_len(y1, n)
          cols <- if (is.null(gr$gp$col)) rep("#000000", n) else rep_len(gr$gp$col, n)
          lwds <- if (is.null(gr$gp$lwd)) rep(1, n) else rep_len(as.numeric(gr$gp$lwd), n)
          ltys <- if (is.null(gr$gp$lty)) rep("solid", n) else rep_len(as.character(gr$gp$lty), n)
          alphas <- if (is.null(gr$gp$alpha)) rep(1, n) else rep_len(as.numeric(gr$gp$alpha), n)
          for (k in seq_len(n)) {
            if (!all(is.finite(c(x0[k], x1[k], y0[k], y1[k])))) next
            blank <- ltys[k] %in% c("0", "blank") || is.na(lwds[k]) || lwds[k] <= 0
            c1 <- if (blank) list(hex = "none", alpha = 0) else pg_col(cols[k])
            a1 <- inh_alpha * (if (is.na(alphas[k])) 1 else alphas[k])
            cc <- conv_pts(c(x0[k], x1[k]), c(y0[k], y1[k]))
            shapes[[length(shapes) + 1L]] <<- list(
              kind = "segment", paint_index = paint, filled = FALSE, is_rounded = FALSE,
              facecolor_hex = "none", face_alpha = 0,
              edgecolor_hex = c1$hex, edge_alpha = round(c1$alpha * a1, 4),
              linewidth_pt = if (c1$hex == "none") 0 else round(lwds[k] * 72 / 96, 4),
              pieces = list(list(data = cc$data, norm = cc$norm))
            )
          }
        } else {
          xs <- convertX(gr$x, "npc", valueOnly = TRUE); ys <- convertY(gr$y, "npc", valueOnly = TRUE)
          id <- if (!is.null(gr$id)) gr$id else if (!is.null(gr$pathId)) gr$pathId else rep(1L, length(xs))
          lty <- gr$gp$lty
          lwd <- if (is.null(gr$gp$lwd)) 1 else if (is.na(gr$gp$lwd[1])) NA_real_ else as.numeric(gr$gp$lwd[1])
          blank <- (!is.null(lty) && as.character(lty[1]) %in% c("0", "blank")) || (!is.na(lwd) && lwd <= 0)
          c1 <- if (blank || is.null(gr$gp$col)) list(hex = "none", alpha = 0) else pg_col(gr$gp$col)
          f1 <- if (filled) pg_col(gr$gp$fill) else list(hex = "none", alpha = 0)
          pieces <- list()
          is_r <- FALSE
          for (pid in sort(unique(id[!is.na(id)]))) {
            sel <- which(!is.na(id) & id == pid & is.finite(xs) & is.finite(ys))
            if (length(sel) < 2 || (length(unique(xs[sel])) < 2 && length(unique(ys[sel])) < 2)) next
            cc <- conv_pts(xs[sel], ys[sel])
            if (filled && nrow(unique(round(cc$norm, 5))) > 6L) is_r <- TRUE
            pieces[[length(pieces) + 1L]] <- list(data = cc$data, norm = cc$norm)
          }
          if (length(pieces)) {
            shapes[[length(shapes) + 1L]] <<- list(
              kind = if (filled) "polygon" else "polyline",
              paint_index = paint, filled = filled, is_rounded = is_r,
              facecolor_hex = f1$hex, face_alpha = round(f1$alpha * cur_a, 4),
              edgecolor_hex = c1$hex, edge_alpha = round(c1$alpha * cur_a, 4),
              linewidth_pt = if (c1$hex == "none" || is.na(lwd)) 0 else round(lwd * 72 / 96, 4),
              pieces = pieces
            )
          }
        }
      } else if ("rasters" %in% kinds && inherits(gr, "rastergrob")) {
        paint <<- paint + 1L
        hj <- grid::resolveHJust(gr$just, gr$hjust); vj <- grid::resolveVJust(gr$just, gr$vjust)
        x0 <- convertX(gr$x - hj * gr$width, "npc", valueOnly = TRUE)
        y0 <- convertY(gr$y - vj * gr$height, "npc", valueOnly = TRUE)
        x1 <- convertX(gr$x + (1 - hj) * gr$width, "npc", valueOnly = TRUE)
        y1 <- convertY(gr$y + (1 - vj) * gr$height, "npc", valueOnly = TRUE)
        cc <- conv_pts(c(x0, x1), c(y0, y1))
        rasters[[length(rasters) + 1L]] <<- list(
          paint_index = paint,
          bbox_norm_tl = c(min(cc$norm[, 1]), min(cc$norm[, 2]), max(cc$norm[, 1]), max(cc$norm[, 2])),
          bbox_data = c(min(cc$data[, 1]), min(cc$data[, 2]), max(cc$data[, 1]), max(cc$data[, 2]))
        )
      }
    }
    for (ch in panel_gtree$children) walk(ch, 1.0)
    panels[[pi]] <- list(
      panel_index = pi,
      x_range = xr, y_range = yr,
      x_labels = x_labs, y_labels = y_labs,
      x_is_discrete = x_disc, y_is_discrete = y_disc,
      bbox_norm_tl = bbox_norm,
      points = pts,
      shapes = shapes,
      rasters = rasters
    )
  }
  upViewport(0)
  list(panels = panels)
}
