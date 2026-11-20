# ggstream adapter: R-side observation hook (sourced by engines/r/render.R).
# Extracts per-panel stream ribbon polygons (handling vectorized gpar per group id)
# and stream text/label annotations (geom_stream_label) in panel data units.

adapter_json_digits <- 5L

adapter_setup <- function(family) {
  update_geom_defaults(ggplot2::GeomText, list(family = family))
  update_geom_defaults(ggplot2::GeomLabel, list(family = family))
}

.gs_col <- function(col) {
  if (is.null(col) || length(col) == 0 || is.na(col[1])) return(list(hex = "none", alpha = 0))
  m <- tryCatch(grDevices::col2rgb(col[1], alpha = TRUE), error = function(e) NULL)
  if (is.null(m) || m[4] == 0) return(list(hex = "none", alpha = 0))
  list(hex = tolower(sprintf("#%02X%02X%02X", m[1], m[2], m[3])), alpha = round(m[4] / 255, 4))
}

.gs_panel_order <- function(pv_names) {
  if (length(pv_names) <= 1) return(pv_names)
  m <- regmatches(pv_names, regexec("^panel-(\\d+)-(\\d+)", pv_names))
  cols <- vapply(m, function(x) if (length(x) >= 3) as.integer(x[2]) else 1L, integer(1))
  rows <- vapply(m, function(x) if (length(x) >= 3) as.integer(x[3]) else 1L, integer(1))
  pv_names[order(rows, cols)]
}

adapter_observe <- function(p, w_in, h_in, dpi) {
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
  vps <- .gs_panel_order(vps)
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

    dev_to_coords <- function(x_u, y_u) {
      loc <- deviceLoc(x_u, y_u, valueOnly = TRUE)
      fx <- (loc$x - bl$x) / (tr$x - bl$x)
      fy <- (loc$y - bl$y) / (tr$y - bl$y)
      list(
        norm = cbind(round(loc$x / w_in, 5), round(1 - loc$y / h_in, 5)),
        data = cbind(round(xr[1] + fx * diff(xr), 5), round(yr[1] + fy * diff(yr), 5))
      )
    }

    shapes <- list()
    labels <- list()
    paint <- 0L

    extract_label_tree <- function(gr, inh_alpha = 1.0) {
      cur_a <- inh_alpha * gp_alpha(gr$gp)
      box_info <- NULL
      txt_info <- NULL

      find_parts <- function(node, a_acc) {
        if (is.null(node)) return(invisible())
        pushed <- FALSE
        if (!is.null(node$vp)) { pushViewport(node$vp); pushed <- TRUE }
        on.exit(if (pushed) popViewport(), add = TRUE)
        a_now <- a_acc * gp_alpha(node$gp)
        if (inherits(node, "gTree")) {
          for (ch in node$children) find_parts(ch, a_now)
        } else if (inherits(node, "polygon") || inherits(node, "roundrect") || inherits(node, "rect")) {
          f1 <- .gs_col(node$gp$fill)
          c1 <- .gs_col(node$gp$col)
          box_info <<- list(
            box_fill_hex = f1$hex,
            box_face_alpha = round(f1$alpha * a_now, 4),
            box_edge_hex = c1$hex,
            box_edge_alpha = round(c1$alpha * a_now, 4)
          )
        } else if (inherits(node, "text")) {
          cc <- dev_to_coords(node$x[1], node$y[1])
          c1 <- .gs_col(node$gp$col)
          fs <- if (is.null(node$gp$fontsize)) 11 else round(as.numeric(node$gp$fontsize)[1], 3)
          txt_info <<- list(
            text = trimws(as.character(node$label)[1]),
            x = cc$data[1, 1], y = cc$data[1, 2],
            x_norm = cc$norm[1, 1], y_norm = cc$norm[1, 2],
            color_hex = if (c1$hex == "none") "#000000" else c1$hex,
            alpha = round(c1$alpha * a_now, 4),
            fontsize_pt = fs
          )
        }
      }

      find_parts(gr, cur_a)
      if (!is.null(txt_info) && nzchar(txt_info$text)) {
        paint <<- paint + 1L
        txt_info$paint_index <- paint
        txt_info$has_box <- !is.null(box_info)
        if (!is.null(box_info)) {
          txt_info$box_fill_hex <- box_info$box_fill_hex
          txt_info$box_face_alpha <- box_info$box_face_alpha
          txt_info$box_edge_hex <- box_info$box_edge_hex
          txt_info$box_edge_alpha <- box_info$box_edge_alpha
        }
        labels[[length(labels) + 1L]] <<- txt_info
      }
    }

    walk <- function(gr, inh_alpha = 1.0) {
      if (is.null(gr)) return(invisible())
      pushed <- FALSE
      if (!is.null(gr$vp)) { pushViewport(gr$vp); pushed <- TRUE }
      on.exit(if (pushed) popViewport(), add = TRUE)
      cur_a <- inh_alpha * gp_alpha(gr$gp)
      nm <- if (is.null(gr$name)) "" else gr$name

      if (inherits(gr, "gTree")) {
        if (grepl("^grill|^panel.background|^panel.border", nm)) return(invisible())
        if (grepl("^geom_label", nm)) {
          for (ch in gr$children) extract_label_tree(ch, cur_a)
          return(invisible())
        }
        for (ch in gr$children) walk(ch, cur_a)
        return(invisible())
      }

      if (inherits(gr, "polygon") || inherits(gr, "pathgrob")) {
        cc_all <- dev_to_coords(gr$x, gr$y)
        n_v <- nrow(cc_all$data)
        id <- if (!is.null(gr$id)) {
          gr$id
        } else if (!is.null(gr$pathId)) {
          gr$pathId
        } else if (!is.null(gr$id.lengths)) {
          rep(seq_along(gr$id.lengths), gr$id.lengths)
        } else {
          rep(1L, n_v)
        }
        pids <- unique(id[!is.na(id)])
        np <- length(pids)
        if (!np) return(invisible())

        fills <- if (is.null(gr$gp$fill)) rep(NA_character_, np) else rep_len(gr$gp$fill, np)
        cols <- if (is.null(gr$gp$col)) rep(NA_character_, np) else rep_len(gr$gp$col, np)
        lwds <- if (is.null(gr$gp$lwd)) rep(1, np) else rep_len(as.numeric(gr$gp$lwd), np)
        ltys <- if (is.null(gr$gp$lty)) rep("solid", np) else rep_len(as.character(gr$gp$lty), np)
        alphas <- if (is.null(gr$gp$alpha)) rep(1, np) else rep_len(as.numeric(gr$gp$alpha), np)

        for (k in seq_len(np)) {
          sel <- which(!is.na(id) & id == pids[k] & is.finite(cc_all$data[, 1]) & is.finite(cc_all$data[, 2]))
          if (length(sel) < 3) next
          paint <<- paint + 1L
          lt <- tolower(trimws(ltys[k]))
          blank <- lt %in% c("0", "blank") || is.na(lwds[k]) || lwds[k] <= 0
          c1 <- if (blank) list(hex = "none", alpha = 0) else .gs_col(cols[k])
          f1 <- .gs_col(fills[k])
          a1 <- inh_alpha * (if (is.na(alphas[k])) 1 else alphas[k])
          shapes[[length(shapes) + 1L]] <<- list(
            kind = "polygon",
            paint_index = paint,
            filled = f1$hex != "none",
            facecolor_hex = f1$hex,
            face_alpha = round(f1$alpha * a1, 4),
            edgecolor_hex = c1$hex,
            edge_alpha = round(c1$alpha * a1, 4),
            linewidth_pt = if (c1$hex == "none" || is.na(lwds[k])) 0 else round(lwds[k] * 72 / 96, 4),
            pieces = list(list(
              data = cc_all$data[sel, , drop = FALSE],
              norm = cc_all$norm[sel, , drop = FALSE]
            ))
          )
        }
      } else if (inherits(gr, "text")) {
        lbls <- as.character(gr$label)
        n <- length(lbls)
        if (!n) return(invisible())
        cc <- dev_to_coords(gr$x, gr$y)
        cols <- if (is.null(gr$gp$col)) rep("#000000", n) else rep_len(gr$gp$col, n)
        alphas <- if (is.null(gr$gp$alpha)) rep(1, n) else rep_len(as.numeric(gr$gp$alpha), n)
        fss <- if (is.null(gr$gp$fontsize)) rep(11, n) else rep_len(as.numeric(gr$gp$fontsize), n)
        for (k in seq_len(n)) {
          txt <- trimws(lbls[k])
          if (!nzchar(txt) || is.na(txt)) next
          if (!is.finite(cc$data[k, 1]) || !is.finite(cc$data[k, 2])) next
          paint <<- paint + 1L
          c1 <- .gs_col(cols[k])
          a1 <- inh_alpha * (if (is.na(alphas[k])) 1 else alphas[k])
          labels[[length(labels) + 1L]] <<- list(
            text = txt,
            x = cc$data[k, 1], y = cc$data[k, 2],
            x_norm = cc$norm[k, 1], y_norm = cc$norm[k, 2],
            color_hex = if (c1$hex == "none") "#000000" else c1$hex,
            alpha = round(c1$alpha * a1, 4),
            fontsize_pt = round(fss[k], 3),
            paint_index = paint,
            has_box = FALSE
          )
        }
      }
    }

    for (ch in panel_gtree$children) walk(ch, 1.0)

    panels[[pi]] <- list(
      panel_index = pi,
      is_flip = is_flip,
      x_range = xr,
      y_range = yr,
      x_labels = x_labs,
      y_labels = y_labs,
      x_is_discrete = x_disc,
      y_is_discrete = y_disc,
      bbox_norm_tl = bbox_norm,
      shapes = shapes,
      labels = labels
    )
  }
  upViewport(0)
  list(ggstream = list(panels = panels))
}
