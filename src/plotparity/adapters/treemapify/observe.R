# treemapify adapter: R-side observation hook (sourced by engines/r/render.R).
# Extracts per-panel treemap tiles (filled roundrects/polygons/rects), subgroup borders
# (unfilled stroked rects), and fitted tile/subgroup text labels in panel NPC [0, 1] x [0, 1]
# and canvas normalized top-left coordinates.

adapter_json_digits <- NA

adapter_setup <- function(family) {
  tryCatch(update_geom_defaults(ggplot2::GeomText, list(family = family)), error = function(e) NULL)
  tryCatch({
    update_geom_defaults(treemapify::geom_treemap_text()$geom, list(family = family))
    update_geom_defaults(treemapify::geom_treemap_subgroup_text()$geom, list(family = family))
  }, error = function(e) NULL)
}

.tm_col <- function(col) {
  if (is.null(col) || length(col) == 0 || is.na(col[1])) return(list(hex = "none", alpha = 0))
  m <- tryCatch(grDevices::col2rgb(col[1], alpha = TRUE), error = function(e) NULL)
  if (is.null(m) || m[4] == 0) return(list(hex = "none", alpha = 0))
  list(hex = tolower(sprintf("#%02X%02X%02X", m[1], m[2], m[3])), alpha = round(m[4] / 255, 4))
}

.tm_lty <- function(lty) {
  if (is.null(lty) || length(lty) == 0 || is.na(lty[1])) return("solid")
  s <- tolower(trimws(as.character(lty[1])))
  map <- c("0" = "blank", "1" = "solid", "2" = "dashed", "3" = "dotted",
           "4" = "dotdash", "5" = "longdash", "6" = "twodash")
  if (s %in% names(map)) return(unname(map[[s]]))
  if (s %in% unname(map)) return(s)
  if (grepl("^[0-9a-f]{2,}$", s)) {
    if (nchar(s) >= 4) return("dotdash")
    return("dashed")
  }
  "solid"
}

.tm_fontweight <- function(gp) {
  ff <- if (!is.null(gp$fontface) && length(gp$fontface) > 0 && !is.na(gp$fontface[1])) tolower(as.character(gp$fontface[1])) else ""
  if (ff %in% c("bold", "bold.italic")) return("bold")
  f <- if (!is.null(gp$font) && length(gp$font) > 0 && !is.na(gp$font[1])) as.integer(gp$font[1]) else 1L
  if (f %in% c(2L, 4L)) "bold" else "normal"
}

.tm_fontstyle <- function(gp) {
  ff <- if (!is.null(gp$fontface) && length(gp$fontface) > 0 && !is.na(gp$fontface[1])) tolower(as.character(gp$fontface[1])) else ""
  if (ff %in% c("italic", "bold.italic", "oblique")) return("italic")
  f <- if (!is.null(gp$font) && length(gp$font) > 0 && !is.na(gp$font[1])) as.integer(gp$font[1]) else 1L
  if (f %in% c(3L, 4L)) "italic" else "normal"
}

.tm_panel_order <- function(pv_names) {
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
  vps <- .tm_panel_order(vps)
  gp_alpha <- function(gp) if (is.null(gp) || is.null(gp$alpha) || is.na(gp$alpha[1])) 1.0 else as.numeric(gp$alpha[1])

  panels <- list()
  for (pi in seq_along(vps)) {
    upViewport(0)
    seekViewport(vps[pi])
    bl <- deviceLoc(unit(0, "npc"), unit(0, "npc"), valueOnly = TRUE)
    tr <- deviceLoc(unit(1, "npc"), unit(1, "npc"), valueOnly = TRUE)
    pw_in <- max(tr$x - bl$x, 1e-6)
    ph_in <- max(tr$y - bl$y, 1e-6)
    bbox_norm <- round(c(bl$x / w_in, 1 - tr$y / h_in, tr$x / w_in, 1 - bl$y / h_in), 6)
    panel_gtree <- grid.get(vps[pi])

    in_to_npc_and_norm <- function(x0_in, y0_in, x1_in, y1_in) {
      nx0 <- round((x0_in - bl$x) / pw_in, 6)
      ny0 <- round((y0_in - bl$y) / ph_in, 6)
      nx1 <- round((x1_in - bl$x) / pw_in, 6)
      ny1 <- round((y1_in - bl$y) / ph_in, 6)
      bb <- round(c(x0_in / w_in, 1 - y1_in / h_in, x1_in / w_in, 1 - y0_in / h_in), 6)
      list(npc = c(min(nx0, nx1), min(ny0, ny1), max(nx0, nx1), max(ny0, ny1)),
           bbox_norm_tl = c(min(bb[1], bb[3]), min(bb[2], bb[4]), max(bb[1], bb[3]), max(bb[2], bb[4])))
    }

    tiles <- list()
    borders <- list()
    labels <- list()
    paint <- 0L

    walk <- function(gr, inh_alpha = 1.0, role = "tile") {
      if (is.null(gr)) return(invisible())
      pushed <- FALSE
      if (!is.null(gr$vp)) { pushViewport(gr$vp); pushed <- TRUE }
      on.exit(if (pushed) popViewport(), add = TRUE)
      cur_a <- inh_alpha * gp_alpha(gr$gp)
      nm <- if (is.null(gr$name)) "" else gr$name

      if (inherits(gr, "gTree")) {
        if (grepl("^grill|^panel[.]background|^panel[.]border", nm)) return(invisible())
        next_role <- role
        if (grepl("^geom_treemap_subgroup", nm)) {
          next_role <- "subgroup"
        } else if (grepl("^geom_treemap_text", nm)) {
          next_role <- "tile"
        } else if (grepl("^geom_treemap[.]", nm)) {
          next_role <- "tile"
        }
        for (ch in gr$children) walk(ch, cur_a, next_role)
        return(invisible())
      }

      if (inherits(gr, "polygon") || inherits(gr, "pathgrob")) {
        loc <- deviceLoc(gr$x, gr$y, valueOnly = TRUE)
        sel <- which(is.finite(loc$x) & is.finite(loc$y))
        if (length(sel) < 3) return(invisible())
        paint <<- paint + 1L
        coords <- in_to_npc_and_norm(min(loc$x[sel]), min(loc$y[sel]), max(loc$x[sel]), max(loc$y[sel]))
        lty <- .tm_lty(gr$gp$lty)
        lwd <- if (is.null(gr$gp$lwd)) 1 else if (is.na(gr$gp$lwd[1])) NA_real_ else as.numeric(gr$gp$lwd[1])
        blank <- lty == "blank" || (!is.na(lwd) && lwd <= 0)
        c1 <- if (blank || is.null(gr$gp$col)) list(hex = "none", alpha = 0) else .tm_col(gr$gp$col[1])
        f1 <- .tm_col(gr$gp$fill[1])
        rec <- list(
          paint_index = paint,
          x0 = coords$npc[1], y0 = coords$npc[2], x1 = coords$npc[3], y1 = coords$npc[4],
          bbox_norm_tl = coords$bbox_norm_tl,
          facecolor_hex = f1$hex,
          face_alpha = round(f1$alpha * cur_a, 4),
          edgecolor_hex = c1$hex,
          edge_alpha = round(c1$alpha * cur_a, 4),
          linewidth_pt = if (c1$hex == "none" || is.na(lwd)) 0 else round(lwd * 72 / 96, 4),
          linetype = if (c1$hex == "none") "blank" else lty
        )
        if (f1$hex != "none" && rec$face_alpha > 0) {
          tiles[[length(tiles) + 1L]] <<- rec
        } else if (c1$hex != "none" && rec$edge_alpha > 0) {
          borders[[length(borders) + 1L]] <<- rec
        }
      } else if (inherits(gr, "rect")) {
        hj <- grid::resolveHJust(gr$just, gr$hjust)
        vj <- grid::resolveVJust(gr$just, gr$vjust)
        cx <- convertX(gr$x, "npc", valueOnly = TRUE)
        cy <- convertY(gr$y, "npc", valueOnly = TRUE)
        ws <- convertWidth(gr$width, "npc", valueOnly = TRUE)
        hs <- convertHeight(gr$height, "npc", valueOnly = TRUE)
        n <- max(length(cx), length(cy), length(ws), length(hs))
        if (!n) return(invisible())
        cx <- rep_len(cx, n); cy <- rep_len(cy, n); ws <- rep_len(ws, n); hs <- rep_len(hs, n)
        fills <- if (is.null(gr$gp$fill)) rep(NA_character_, n) else rep_len(gr$gp$fill, n)
        cols <- if (is.null(gr$gp$col)) rep(NA_character_, n) else rep_len(gr$gp$col, n)
        lwds <- if (is.null(gr$gp$lwd)) rep(1, n) else rep_len(as.numeric(gr$gp$lwd), n)
        ltys <- if (is.null(gr$gp$lty)) rep("solid", n) else rep_len(as.character(gr$gp$lty), n)
        alphas <- if (is.null(gr$gp$alpha)) rep(1, n) else rep_len(as.numeric(gr$gp$alpha), n)
        for (k in seq_len(n)) {
          if (!all(is.finite(c(cx[k], cy[k], ws[k], hs[k])))) next
          if (abs(ws[k]) < 1e-6 || abs(hs[k]) < 1e-6) next
          paint <<- paint + 1L
          p0 <- deviceLoc(unit(cx[k] - hj * ws[k], "npc"), unit(cy[k] - vj * hs[k], "npc"), valueOnly = TRUE)
          p1 <- deviceLoc(unit(cx[k] + (1 - hj) * ws[k], "npc"), unit(cy[k] + (1 - vj) * hs[k], "npc"), valueOnly = TRUE)
          coords <- in_to_npc_and_norm(min(p0$x, p1$x), min(p0$y, p1$y), max(p0$x, p1$x), max(p0$y, p1$y))
          lty <- .tm_lty(ltys[k])
          blank <- lty == "blank" || is.na(lwds[k]) || lwds[k] <= 0
          c1 <- if (blank) list(hex = "none", alpha = 0) else .tm_col(cols[k])
          f1 <- .tm_col(fills[k])
          a1 <- inh_alpha * (if (is.na(alphas[k])) 1 else alphas[k])
          rec <- list(
            paint_index = paint,
            x0 = coords$npc[1], y0 = coords$npc[2], x1 = coords$npc[3], y1 = coords$npc[4],
            bbox_norm_tl = coords$bbox_norm_tl,
            facecolor_hex = f1$hex,
            face_alpha = round(f1$alpha * a1, 4),
            edgecolor_hex = c1$hex,
            edge_alpha = round(c1$alpha * a1, 4),
            linewidth_pt = if (c1$hex == "none" || is.na(lwds[k])) 0 else round(lwds[k] * 72 / 96, 4),
            linetype = if (c1$hex == "none") "blank" else lty
          )
          if (f1$hex != "none" && rec$face_alpha > 0) {
            tiles[[length(tiles) + 1L]] <<- rec
          } else if (c1$hex != "none" && rec$edge_alpha > 0) {
            borders[[length(borders) + 1L]] <<- rec
          }
        }
      } else if (inherits(gr, "text")) {
        lbls <- as.character(gr$label)
        if (!length(lbls)) return(invisible())
        txt <- lbls[1]
        if (is.na(txt) || !nzchar(trimws(txt))) return(invisible())
        pos <- deviceLoc(gr$x[1], gr$y[1], valueOnly = TRUE)
        if (!is.finite(pos$x) || !is.finite(pos$y)) return(invisible())
        w_t <- convertWidth(grobWidth(gr), "inches", valueOnly = TRUE)
        h_t <- convertHeight(grobHeight(gr), "inches", valueOnly = TRUE)
        hj <- grid::resolveHJust(gr$just, gr$hjust)
        vj <- grid::resolveVJust(gr$just, gr$vjust)
        coords <- in_to_npc_and_norm(
          pos$x - hj * w_t, pos$y - vj * h_t,
          pos$x + (1 - hj) * w_t, pos$y + (1 - vj) * h_t
        )
        c1 <- .tm_col(gr$gp$col[1])
        fs <- if (is.null(gr$gp$fontsize) || is.na(gr$gp$fontsize[1])) 11 else round(as.numeric(gr$gp$fontsize[1]), 3)
        paint <<- paint + 1L
        labels[[length(labels) + 1L]] <<- list(
          paint_index = paint,
          text = txt,
          label_role = role,
          x0 = coords$npc[1], y0 = coords$npc[2], x1 = coords$npc[3], y1 = coords$npc[4],
          bbox_norm_tl = coords$bbox_norm_tl,
          fontsize_pt = fs,
          color_hex = if (c1$hex == "none") "#000000" else c1$hex,
          alpha = round(c1$alpha * cur_a, 4),
          fontweight = .tm_fontweight(gr$gp),
          fontstyle = .tm_fontstyle(gr$gp),
          line_count = length(strsplit(txt, "\n", fixed = TRUE)[[1]])
        )
      }
    }

    for (ch in panel_gtree$children) walk(ch, 1.0, "tile")

    panels[[pi]] <- list(
      panel_index = pi,
      bbox_norm_tl = bbox_norm,
      tiles = tiles,
      borders = borders,
      labels = labels
    )
  }
  upViewport(0)
  list(treemapify = list(panels = panels))
}
