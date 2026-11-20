# ggforce adapter: R-side observation hooks (sourced by engines/r/render.R).
# Forces hull_enc / ellip_enc / circ_enc / rect_enc gTrees (triggering ggforce's draw-time
# makeContent enclosure, label placement, and connector layout) and extracts per-panel:
#   - panel anchor points (from geom_point)
#   - mark enclosure polygons (vertices, bbox, fill/edge style, roundedness)
#   - mark callout annotations (title + optional description text, styles, optional label box)
#   - connector polylines (points, color, alpha, linewidth, linetype)

adapter_json_digits <- NA
adapter_setup <- function(family) invisible(NULL)

.gf_col <- function(col) {
  if (is.null(col) || length(col) == 0 || is.na(col[1])) return(list(hex = "none", alpha = 0))
  m <- tryCatch(grDevices::col2rgb(col[1], alpha = TRUE), error = function(e) NULL)
  if (is.null(m) || m[4] == 0) return(list(hex = "none", alpha = 0))
  list(hex = tolower(sprintf("#%02X%02X%02X", m[1], m[2], m[3])), alpha = round(m[4] / 255, 4))
}

.gf_lty <- function(lty) {
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

.gf_panel_order <- function(pv_names) {
  if (length(pv_names) <= 1) return(pv_names)
  m <- regmatches(pv_names, regexec("^panel-(\\d+)-(\\d+)", pv_names))
  cols <- vapply(m, function(x) if (length(x) >= 3) as.integer(x[2]) else 1L, integer(1))
  rows <- vapply(m, function(x) if (length(x) >= 3) as.integer(x[3]) else 1L, integer(1))
  pv_names[order(rows, cols)]
}

.gf_gp_item <- function(val, k, n, default = NA) {
  if (is.null(val) || length(val) == 0) return(default)
  rep_len(val, n)[k]
}

.gf_pt_to_bbox <- function(px, py, bb) {
  dx <- max(bb[1] - px, 0, px - bb[3])
  dy <- max(bb[2] - py, 0, py - bb[4])
  sqrt(dx * dx + dy * dy)
}

adapter_observe <- function(p, w_in, h_in, dpi) {
  tmp <- tempfile(fileext = ".png")
  agg_png(tmp, width = w_in, height = h_in, units = "in", res = dpi)
  on.exit({ if (dev.cur() > 1) dev.off(); unlink(tmp) }, add = TRUE)
  grid.draw(ggplotGrob(p))
  grid.force()

  vps <- unique(grep("^panel([.-]|$)", grid.ls(grobs = FALSE, viewports = TRUE, print = FALSE)$name, value = TRUE))
  vps <- .gf_panel_order(vps)

  dloc <- function(x_u, y_u) {
    loc <- deviceLoc(x_u, y_u, valueOnly = TRUE)
    c(round(loc$x / w_in, 6), round(1 - loc$y / h_in, 6))
  }

  panels <- list()
  marks <- list()

  for (pi in seq_along(vps)) {
    upViewport(0)
    seekViewport(vps[pi])
    bl <- dloc(unit(0, "npc"), unit(0, "npc"))
    tr <- dloc(unit(1, "npc"), unit(1, "npc"))
    p_bb <- c(bl[1], tr[2], tr[1], bl[2])
    gt <- grid.get(vps[pi])

    pts <- list()
    panel_marks <- list()

    extract_mark_tree <- function(gr) {
      gkind <- if (inherits(gr, "hull_enc")) "hull" else
               if (inherits(gr, "ellip_enc")) "ellipse" else
               if (inherits(gr, "circ_enc")) "circle" else "rect"

      polys <- list()
      callouts <- list()
      conns <- list()

      for (sub in gr$children) {
        if (is.null(sub)) next
        if (inherits(sub, "polygon")) {
          loc <- deviceLoc(sub$x, sub$y, valueOnly = TRUE)
          xs_all <- loc$x / w_in
          ys_all <- 1 - loc$y / h_in
          ids <- if (is.null(sub$id)) rep(1L, length(xs_all)) else as.integer(sub$id)
          uids <- unique(ids)
          n_u <- length(uids)
          for (k in seq_len(n_u)) {
            idx <- which(ids == uids[k])
            xs <- round(xs_all[idx], 6)
            ys <- round(ys_all[idx], 6)
            ok <- is.finite(xs) & is.finite(ys)
            xs <- xs[ok]; ys <- ys[ok]
            if (length(xs) < 3) next
            verts <- lapply(seq_along(xs), function(j) c(xs[j], ys[j]))
            f1 <- .gf_col(.gf_gp_item(sub$gp$fill, k, n_u, NA))
            c1 <- .gf_col(.gf_gp_item(sub$gp$col, k, n_u, NA))
            a_gp <- .gf_gp_item(sub$gp$alpha, k, n_u, NA)
            a_mul <- if (is.na(a_gp)) 1.0 else as.numeric(a_gp)
            lwd_raw <- .gf_gp_item(sub$gp$lwd, k, n_u, NA)
            lwd_pt <- if (is.na(lwd_raw)) 1.0 else round(as.numeric(lwd_raw) * 72 / 96, 3)
            if (lwd_pt <= 0) c1 <- list(hex = "none", alpha = 0)
            lty <- .gf_lty(.gf_gp_item(sub$gp$lty, k, n_u, 1))
            if (c1$hex == "none") lty <- "blank"
            polys[[length(polys) + 1L]] <- list(
              id = uids[k],
              geom_kind = gkind,
              vertices_norm_tl = verts,
              bbox_norm_tl = round(c(min(xs), min(ys), max(xs), max(ys)), 6),
              facecolor_hex = f1$hex,
              face_alpha = round(f1$alpha * a_mul, 4),
              edgecolor_hex = c1$hex,
              edge_alpha = round(c1$alpha * a_mul, 4),
              linewidth_pt = lwd_pt,
              linetype = lty
            )
          }
        } else if (inherits(sub, "mark_label")) {
          pushed_lbl <- FALSE
          if (!is.null(sub$vp)) { pushViewport(sub$vp); pushed_lbl <- TRUE }
          lbl_bl <- dloc(unit(0, "npc"), unit(0, "npc"))
          lbl_tr <- dloc(unit(1, "npc"), unit(1, "npc"))
          lbl_bb <- round(c(min(lbl_bl[1], lbl_tr[1]), min(lbl_bl[2], lbl_tr[2]),
                            max(lbl_bl[1], lbl_tr[1]), max(lbl_bl[2], lbl_tr[2])), 6)
          box_info <- NULL
          text_items <- list()
          for (mc in sub$children) {
            if (is.null(mc)) next
            if (inherits(mc, "rect") || inherits(mc, "roundrect")) {
              f1 <- .gf_col(mc$gp$fill)
              c1 <- .gf_col(mc$gp$col)
              a1 <- if (is.null(mc$gp$alpha) || is.na(mc$gp$alpha[1])) 1.0 else as.numeric(mc$gp$alpha[1])
              lwd_raw <- if (is.null(mc$gp$lwd) || is.na(mc$gp$lwd[1])) 0.0 else as.numeric(mc$gp$lwd[1]) * 72 / 96
              if (lwd_raw <= 0) c1 <- list(hex = "none", alpha = 0)
              box_info <- list(
                bbox_norm_tl = lbl_bb,
                facecolor_hex = f1$hex,
                face_alpha = round(f1$alpha * a1, 4),
                edgecolor_hex = c1$hex,
                edge_alpha = round(c1$alpha * a1, 4),
                linewidth_pt = round(lwd_raw, 3)
              )
            } else if (inherits(mc, "text")) {
              txt_str <- paste(as.character(mc$label), collapse = "\n")
              if (!nzchar(trimws(txt_str))) next
              pushed_t <- FALSE
              if (!is.null(mc$vp)) { pushViewport(mc$vp); pushed_t <- TRUE }
              tloc <- deviceLoc(mc$x, mc$y, valueOnly = TRUE)
              tw <- convertWidth(grobWidth(mc), "inches", valueOnly = TRUE)
              th <- convertHeight(grobHeight(mc), "inches", valueOnly = TRUE)
              if (pushed_t) popViewport()
              hj <- tryCatch(grid::resolveHJust(mc$just, mc$hjust), error = function(e) 0.0)
              vj <- tryCatch(grid::resolveVJust(mc$just, mc$vjust), error = function(e) 0.5)
              tx_norm <- tloc$x / w_in
              ty_norm <- 1 - tloc$y / h_in
              x0 <- tx_norm - hj * (tw / w_in)
              y1 <- ty_norm + vj * (th / h_in)
              tbb <- round(c(x0, y1 - th / h_in, x0 + tw / w_in, y1), 6)
              c1 <- .gf_col(mc$gp$col)
              a1 <- if (is.null(mc$gp$alpha) || is.na(mc$gp$alpha[1])) 1.0 else as.numeric(mc$gp$alpha[1])
              fc <- if (is.null(mc$gp$font) || is.na(mc$gp$font[1])) 1L else as.integer(mc$gp$font[1])
              fs <- if (is.null(mc$gp$fontsize) || is.na(mc$gp$fontsize[1])) 12.0 else round(as.numeric(mc$gp$fontsize)[1], 3)
              text_items[[length(text_items) + 1L]] <- list(
                text = txt_str,
                bbox_norm_tl = tbb,
                color_hex = if (c1$hex == "none") "#000000" else c1$hex,
                alpha = round(c1$alpha * a1, 4),
                fontsize_pt = fs,
                fontweight = if (fc %in% c(2L, 4L)) "bold" else "normal",
                fontstyle = if (fc %in% c(3L, 4L)) "italic" else "normal"
              )
            }
          }
          if (pushed_lbl) popViewport()
          if (length(text_items) > 0) {
            title_item <- text_items[[1]]
            desc_item <- if (length(text_items) >= 2) text_items[[2]] else NULL
            callouts[[length(callouts) + 1L]] <- list(
              bbox_norm_tl = lbl_bb,
              title = title_item,
              description = desc_item,
              box = box_info
            )
          }
        } else if (inherits(sub, "polyline")) {
          loc <- deviceLoc(sub$x, sub$y, valueOnly = TRUE)
          xs_all <- loc$x / w_in
          ys_all <- 1 - loc$y / h_in
          ids <- if (is.null(sub$id)) rep(1L, length(xs_all)) else as.integer(sub$id)
          uids <- unique(ids)
          n_u <- length(uids)
          for (k in seq_len(n_u)) {
            idx <- which(ids == uids[k])
            xs <- round(xs_all[idx], 6)
            ys <- round(ys_all[idx], 6)
            ok <- is.finite(xs) & is.finite(ys)
            xs <- xs[ok]; ys <- ys[ok]
            if (length(xs) < 2) next
            pts_c <- lapply(seq_along(xs), function(j) c(xs[j], ys[j]))
            c1 <- .gf_col(.gf_gp_item(sub$gp$col, k, n_u, "black"))
            a_gp <- .gf_gp_item(sub$gp$alpha, k, n_u, NA)
            a_mul <- if (is.na(a_gp)) 1.0 else as.numeric(a_gp)
            lwd_raw <- .gf_gp_item(sub$gp$lwd, k, n_u, NA)
            lwd_pt <- if (is.na(lwd_raw)) 1.0 else round(as.numeric(lwd_raw) * 72 / 96, 3)
            lty <- .gf_lty(.gf_gp_item(sub$gp$lty, k, n_u, 1))
            conns[[length(conns) + 1L]] <- list(
              id = uids[k],
              points_norm_tl = pts_c,
              color_hex = if (c1$hex == "none") "#000000" else c1$hex,
              alpha = round(c1$alpha * a_mul, 4),
              linewidth_pt = lwd_pt,
              linetype = lty
            )
          }
        }
      }

      # Pair callouts and connectors to polygons
      n_p <- length(polys)
      paired_callouts <- vector("list", n_p)
      paired_conns <- vector("list", n_p)

      if (length(callouts) == n_p && n_p > 0) {
        paired_callouts <- callouts
      } else if (length(callouts) > 0 && n_p > 0) {
        used_p <- rep(FALSE, n_p)
        for (co in callouts) {
          bb <- co$bbox_norm_tl
          cx <- 0.5 * (bb[1] + bb[3]); cy <- 0.5 * (bb[2] + bb[4])
          best_j <- NA_integer_; best_d <- Inf
          for (j in seq_len(n_p)) {
            if (used_p[j]) next
            pbb <- polys[[j]]$bbox_norm_tl
            d <- .gf_pt_to_bbox(cx, cy, pbb)
            if (d < best_d) { best_d <- d; best_j <- j }
          }
          if (!is.na(best_j)) { used_p[best_j] <- TRUE; paired_callouts[[best_j]] <- co }
        }
      }

      if (length(conns) == n_p && n_p > 0) {
        # Match each connector to its callout (or by order if unambiguous)
        used_c <- rep(FALSE, length(conns))
        for (j in seq_len(n_p)) {
          co <- paired_callouts[[j]]
          if (is.null(co)) next
          bb <- co$bbox_norm_tl
          best_ci <- NA_integer_; best_d <- Inf
          for (ci in seq_along(conns)) {
            if (used_c[ci]) next
            p0 <- conns[[ci]]$points_norm_tl[[1]]
            pn <- conns[[ci]]$points_norm_tl[[length(conns[[ci]]$points_norm_tl)]]
            d <- min(.gf_pt_to_bbox(p0[1], p0[2], bb), .gf_pt_to_bbox(pn[1], pn[2], bb))
            if (d < best_d) { best_d <- d; best_ci <- ci }
          }
          if (!is.na(best_ci)) { used_c[best_ci] <- TRUE; paired_conns[[j]] <- conns[[best_ci]] }
        }
      } else if (length(conns) > 0 && n_p > 0) {
        used_c <- rep(FALSE, length(conns))
        for (j in seq_len(n_p)) {
          co <- paired_callouts[[j]]
          if (is.null(co)) next
          bb <- co$bbox_norm_tl
          best_ci <- NA_integer_; best_d <- Inf
          for (ci in seq_along(conns)) {
            if (used_c[ci]) next
            p0 <- conns[[ci]]$points_norm_tl[[1]]
            pn <- conns[[ci]]$points_norm_tl[[length(conns[[ci]]$points_norm_tl)]]
            d <- min(.gf_pt_to_bbox(p0[1], p0[2], bb), .gf_pt_to_bbox(pn[1], pn[2], bb))
            if (d < best_d) { best_d <- d; best_ci <- ci }
          }
          if (!is.na(best_ci) && best_d < 0.05) { used_c[best_ci] <- TRUE; paired_conns[[j]] <- conns[[best_ci]] }
        }
      }

      for (j in seq_len(n_p)) {
        panel_marks[[length(panel_marks) + 1L]] <<- list(
          panel = pi,
          polygon = polys[[j]],
          callout = paired_callouts[[j]],
          connector = paired_conns[[j]]
        )
      }
    }

    walk <- function(gr) {
      if (is.null(gr)) return(invisible())
      nm <- if (is.null(gr$name)) "" else gr$name
      if (inherits(gr, c("hull_enc", "ellip_enc", "circ_enc", "rect_enc"))) {
        extract_mark_tree(gr)
        return(invisible())
      }
      if (inherits(gr, "gTree")) {
        if (grepl("^grill|^panel.background|^panel.border", nm)) return(invisible())
        for (ch in gr$children) walk(ch)
        return(invisible())
      }
      if (inherits(gr, "points")) {
        xs <- convertX(gr$x, "npc", valueOnly = TRUE)
        ys <- convertY(gr$y, "npc", valueOnly = TRUE)
        n <- length(xs)
        if (n > 0) {
          fss <- if (is.null(gr$gp$fontsize)) rep(12, n) else rep_len(as.numeric(gr$gp$fontsize), n)
          sz_vals <- if (is.null(gr$size)) rep(1, n) else rep_len(as.numeric(gr$size), n)
          for (k in seq_len(n)) {
            if (!is.finite(xs[k]) || !is.finite(ys[k])) next
            xy <- dloc(unit(xs[k], "npc"), unit(ys[k], "npc"))
            pts[[length(pts) + 1L]] <<- list(
              x_norm = xy[1], y_norm = xy[2],
              size_pt = round(0.75 * fss[k] * sz_vals[k], 3)
            )
          }
        }
      }
    }

    for (ch in gt$children) walk(ch)
    for (mk in panel_marks) marks[[length(marks) + 1L]] <- mk
    panels[[pi]] <- list(panel_index = pi, bbox_norm_tl = p_bb, points = pts)
  }

  upViewport(0)
  list(ggforce = list(panels = panels, marks = marks))
}
