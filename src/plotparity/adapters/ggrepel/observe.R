# ggrepel adapter: R-side observation hooks (sourced by engines/r/render.R).
# Forces textrepeltree / labelrepeltree (triggering ggrepel's draw-time makeContent layout)
# and extracts per-panel points, repelled text labels, optional label boxes, and leader segments.

adapter_json_digits <- NA
adapter_setup <- function(family) {
  update_geom_defaults(ggrepel::GeomTextRepel, list(family = family))
  update_geom_defaults(ggrepel::GeomLabelRepel, list(family = family))
}

.rp_col <- function(col) {
  if (is.null(col) || length(col) == 0 || is.na(col[1])) return(list(hex = "none", alpha = 0))
  m <- tryCatch(grDevices::col2rgb(col[1], alpha = TRUE), error = function(e) NULL)
  if (is.null(m) || m[4] == 0) return(list(hex = "none", alpha = 0))
  list(hex = tolower(sprintf("#%02X%02X%02X", m[1], m[2], m[3])), alpha = round(m[4] / 255, 4))
}

.rp_panel_order <- function(pv_names) {
  if (length(pv_names) <= 1) return(pv_names)
  m <- regmatches(pv_names, regexec("^panel-(\\d+)-(\\d+)", pv_names))
  cols <- vapply(m, function(x) if (length(x) >= 3) as.integer(x[2]) else 1L, integer(1))
  rows <- vapply(m, function(x) if (length(x) >= 3) as.integer(x[3]) else 1L, integer(1))
  pv_names[order(rows, cols)]
}

adapter_observe <- function(p, w_in, h_in, dpi) {
  tmp <- tempfile(fileext = ".png")
  agg_png(tmp, width = w_in, height = h_in, units = "in", res = dpi)
  on.exit({ if (dev.cur() > 1) dev.off(); unlink(tmp) }, add = TRUE)
  grid.draw(ggplotGrob(p))
  grid.force()

  vps <- unique(grep("^panel([.-]|$)", grid.ls(grobs = FALSE, viewports = TRUE, print = FALSE)$name, value = TRUE))
  vps <- .rp_panel_order(vps)

  dloc <- function(x_u, y_u) {
    loc <- deviceLoc(x_u, y_u, valueOnly = TRUE)
    c(round(loc$x / w_in, 6), round(1 - loc$y / h_in, 6))
  }

  panels <- list()
  labels <- list()

  for (pi in seq_along(vps)) {
    upViewport(0)
    seekViewport(vps[pi])
    bl <- dloc(unit(0, "npc"), unit(0, "npc"))
    tr <- dloc(unit(1, "npc"), unit(1, "npc"))
    p_bb <- c(bl[1], tr[2], tr[1], bl[2])
    gt <- grid.get(vps[pi])

    pts <- list()
    texts <- list()
    rects <- list()
    segs <- list()

    walk <- function(gr, parent_name = "") {
      if (is.null(gr)) return(invisible())
      nm <- if (is.null(gr$name)) "" else gr$name
      if (inherits(gr, "gTree")) {
        if (grepl("^grill|^panel.background|^panel.border", nm)) return(invisible())
        for (ch in gr$children) walk(ch, nm)
        return(invisible())
      }
      if (inherits(gr, "points")) {
        xs <- convertX(gr$x, "npc", valueOnly = TRUE)
        ys <- convertY(gr$y, "npc", valueOnly = TRUE)
        n <- length(xs)
        fss <- if (is.null(gr$gp$fontsize)) rep(12, n) else rep_len(as.numeric(gr$gp$fontsize), n)
        for (k in seq_len(n)) {
          if (!is.finite(xs[k]) || !is.finite(ys[k])) next
          xy <- dloc(unit(xs[k], "npc"), unit(ys[k], "npc"))
          pts[[length(pts) + 1L]] <<- list(x_norm = xy[1], y_norm = xy[2], size_pt = round(0.75 * fss[k], 3))
        }
      } else if (inherits(gr, "text") && grepl("^textrepelgrob", nm)) {
        idx <- sub("^textrepelgrob", "", nm)
        lbl <- as.character(gr$label)[1]
        xy <- dloc(gr$x, gr$y)
        w_in_t <- convertWidth(grobWidth(gr), "inches", valueOnly = TRUE)
        h_in_t <- convertHeight(grobHeight(gr), "inches", valueOnly = TRUE)
        hj <- if (is.null(gr$hjust)) 0.5 else as.numeric(gr$hjust)[1]
        vj <- if (is.null(gr$vjust)) 0.5 else as.numeric(gr$vjust)[1]
        x0 <- xy[1] - hj * (w_in_t / w_in)
        y1 <- xy[2] + vj * (h_in_t / h_in)
        bb <- round(c(x0, y1 - h_in_t / h_in, x0 + w_in_t / w_in, y1), 6)
        c1 <- .rp_col(gr$gp$col)
        a1 <- if (is.null(gr$gp$alpha) || is.na(gr$gp$alpha[1])) 1 else as.numeric(gr$gp$alpha[1])
        fc <- if (is.null(gr$gp$font)) 1L else as.integer(gr$gp$font)[1]
        texts[[idx]] <<- list(
          panel = pi, idx = idx, text = lbl,
          x_norm = xy[1], y_norm = xy[2], bbox_norm_tl = bb,
          color_hex = if (c1$hex == "none") "#000000" else c1$hex,
          alpha = round(c1$alpha * a1, 4),
          fontsize_pt = if (is.null(gr$gp$fontsize)) 11 else round(as.numeric(gr$gp$fontsize)[1], 3),
          fontweight = if (fc %in% c(2L, 4L)) "bold" else "normal",
          fontstyle = if (fc %in% c(3L, 4L)) "italic" else "normal",
          angle_deg = if (is.null(gr$rot)) 0 else round(as.numeric(gr$rot)[1], 3)
        )
      } else if ((inherits(gr, "polygon") || inherits(gr, "roundrect") || inherits(gr, "rect")) && grepl("^rectrepelgrob", nm)) {
        idx <- sub("^rectrepelgrob", "", nm)
        ch_pushed <- FALSE
        if (!is.null(gr$vp)) { pushViewport(gr$vp); ch_pushed <- TRUE }
        if (inherits(gr, "polygon")) {
          loc <- deviceLoc(gr$x, gr$y, valueOnly = TRUE)
          xs <- loc$x / w_in; ys <- 1 - loc$y / h_in
          # roundrectGrob(r = 0) forces to 51 vertices collapsed onto the 4 corners; count unique vertices
          nv <- nrow(unique(round(cbind(xs, ys), 5)))
          is_r <- nv > 6L
        } else {
          hj <- grid::resolveHJust(gr$just, gr$hjust); vj <- grid::resolveVJust(gr$just, gr$vjust)
          x0 <- gr$x - hj * gr$width; y0 <- gr$y - vj * gr$height
          cs <- rbind(dloc(x0, y0), dloc(x0 + gr$width, y0), dloc(x0 + gr$width, y0 + gr$height), dloc(x0, y0 + gr$height))
          xs <- cs[, 1]; ys <- cs[, 2]
          is_r <- !is.null(gr$r) && convertUnit(gr$r, "bigpts", valueOnly = TRUE) > 0.01
        }
        if (ch_pushed) popViewport()
        f1 <- .rp_col(gr$gp$fill)
        c1 <- .rp_col(gr$gp$col)
        a1 <- if (is.null(gr$gp$alpha) || is.na(gr$gp$alpha[1])) 1 else as.numeric(gr$gp$alpha[1])
        lwd <- if (is.null(gr$gp$lwd) || is.na(gr$gp$lwd[1])) 0.5 else as.numeric(gr$gp$lwd[1]) * 72 / 96
        if (lwd <= 0) c1 <- list(hex = "none", alpha = 0)
        rects[[idx]] <<- list(
          bbox_norm_tl = round(c(min(xs), min(ys), max(xs), max(ys)), 6),
          is_rounded = is_r,
          facecolor_hex = f1$hex, face_alpha = round(f1$alpha * a1, 4),
          edgecolor_hex = c1$hex, edge_alpha = round(c1$alpha * a1, 4),
          linewidth_pt = round(lwd, 3)
        )
      } else if (inherits(gr, "segments") && grepl("^segmentrepelgrob", parent_name)) {
        idx <- sub("^segmentrepelgrob", "", parent_name)
        p0 <- dloc(gr$x0[1], gr$y0[1])
        p1 <- dloc(gr$x1[1], gr$y1[1])
        c1 <- .rp_col(gr$gp$col)
        a1 <- if (is.null(gr$gp$alpha) || is.na(gr$gp$alpha[1])) 1 else as.numeric(gr$gp$alpha[1])
        segs[[idx]] <<- list(
          x0_norm = p0[1], y0_norm = p0[2], x1_norm = p1[1], y1_norm = p1[2],
          color_hex = if (c1$hex == "none") "#000000" else c1$hex,
          alpha = round(c1$alpha * a1, 4)
        )
      }
    }
    for (ch in gt$children) walk(ch)

    for (idx in names(texts)) {
      t <- texts[[idx]]
      t$rect <- rects[[idx]]
      t$segment <- segs[[idx]]
      labels[[length(labels) + 1L]] <- t
    }
    panels[[pi]] <- list(panel_index = pi, bbox_norm_tl = p_bb, points = pts)
  }
  upViewport(0)
  list(ggrepel = list(panels = panels, labels = labels))
}
