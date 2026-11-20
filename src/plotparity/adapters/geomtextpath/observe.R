# geomtextpath adapter: R-side observation hooks (sourced by engines/r/render.R).
# Forces textpath / labelpath gTrees and extracts per-curve glyphs (positions, angles, style),
# curve polylines (including gaps behind text), and optional label box polygons.

adapter_json_digits <- NA

adapter_setup <- function(family) {
  for (nm in c("GeomTextpath", "GeomLabelpath", "GeomTextline", "GeomLabelline")) {
    if (exists(nm, asNamespace("geomtextpath"))) {
      update_geom_defaults(get(nm, asNamespace("geomtextpath")), list(family = family))
    }
  }
}

.gtp_col <- function(col) {
  if (is.null(col) || length(col) == 0 || is.na(col[1])) return(list(hex = "none", alpha = 0))
  m <- tryCatch(grDevices::col2rgb(col[1], alpha = TRUE), error = function(e) NULL)
  if (is.null(m) || m[4] == 0) return(list(hex = "none", alpha = 0))
  list(hex = tolower(sprintf("#%02X%02X%02X", m[1], m[2], m[3])), alpha = round(m[4] / 255, 4))
}

.gtp_lty <- function(lty) {
  if (is.null(lty) || length(lty) == 0 || is.na(lty[1])) return("solid")
  s <- tolower(trimws(as.character(lty[1])))
  map <- c(
    "0" = "blank", "blank" = "blank",
    "1" = "solid", "solid" = "solid",
    "2" = "dashed", "dashed" = "dashed", "44" = "dashed",
    "3" = "dotted", "dotted" = "dotted", "13" = "dotted",
    "4" = "dotdash", "dotdash" = "dotdash", "1343" = "dotdash",
    "5" = "longdash", "longdash" = "longdash", "73" = "longdash",
    "6" = "twodash", "twodash" = "twodash", "2262" = "twodash"
  )
  if (s %in% names(map)) return(unname(map[[s]]))
  if (grepl("^[0-9a-f]+$", s)) return("dashed")
  s
}

.gtp_norm_angle <- function(deg) {
  a <- ((as.numeric(deg) + 180) %% 360) - 180
  if (abs(a + 180) < 1e-6) a <- 180
  round(a, 3)
}

.gtp_panel_order <- function(pv_names) {
  if (length(pv_names) <= 1) return(pv_names)
  m <- regmatches(pv_names, regexec("^panel-(\\d+)-(\\d+)", pv_names))
  cols <- vapply(m, function(x) if (length(x) >= 3) as.integer(x[2]) else 1L, integer(1))
  rows <- vapply(m, function(x) if (length(x) >= 3) as.integer(x[3]) else 1L, integer(1))
  pv_names[order(rows, cols)]
}

.gtp_is_textpath_layer <- function(layer) {
  inherits(layer$geom, "GeomTextpath") || inherits(layer$geom, "GeomLabelpath")
}

.gtp_polyline_dist <- function(pts_a, pts_b) {
  if (nrow(pts_a) == 0 || nrow(pts_b) == 0) return(1e9)
  # Sample up to 20 points from pts_a for fast nearest-neighbor distance to pts_b
  idx <- unique(round(seq(1, nrow(pts_a), length.out = min(nrow(pts_a), 20L))))
  sa <- pts_a[idx, , drop = FALSE]
  dists <- vapply(seq_len(nrow(sa)), function(i) {
    min(sqrt((pts_b[, 1] - sa[i, 1])^2 + (pts_b[, 2] - sa[i, 2])^2))
  }, numeric(1))
  mean(dists)
}

adapter_observe <- function(p, w_in, h_in, dpi) {
  tmp <- tempfile(fileext = ".png")
  agg_png(tmp, width = w_in, height = h_in, units = "in", res = dpi)
  on.exit({ if (dev.cur() > 1) dev.off(); unlink(tmp) }, add = TRUE)

  b <- ggplot_build(p)
  tp_layer_idx <- which(vapply(p$layers, .gtp_is_textpath_layer, logical(1)))
  is_flipped <- inherits(b$plot$coordinates, "CoordFlip")

  grid.draw(ggplotGrob(p))
  grid.force()

  all_vps <- unique(grep("^panel([.-]|$)", grid.ls(grobs = FALSE, viewports = TRUE, print = FALSE)$name, value = TRUE))
  # Keep only top-level panel viewports (panel.R-C-... or panel-C-R.R-C-...), exclude panel.background / panel.grid / panel-1.gTree
  vps <- grep("^panel(-[0-9]+-[0-9]+)?\\.[0-9]+-[0-9]+", all_vps, value = TRUE)
  if (length(vps) == 0) {
    vps <- grep("^panel([.-]|$)", all_vps, value = TRUE)
    vps <- vps[!grepl("background|grid|border|gTree", vps)]
  }
  vps <- .gtp_panel_order(vps)

  dloc_vec <- function(x_u, y_u) {
    loc <- deviceLoc(x_u, y_u, valueOnly = TRUE)
    cbind(round(loc$x / w_in, 6), round(1 - loc$y / h_in, 6))
  }

  panels <- list()
  curves <- list()

  for (pi in seq_along(vps)) {
    upViewport(0)
    seekViewport(vps[pi])
    bl <- dloc_vec(unit(0, "npc"), unit(0, "npc"))[1, ]
    tr <- dloc_vec(unit(1, "npc"), unit(1, "npc"))[1, ]
    p_bb <- c(bl[1], tr[2], tr[1], bl[2])
    gt <- grid.get(vps[pi])

    # Collect forced textpath / labelpath gTrees in panel pi in paint order
    tp_trees <- list()
    find_tp <- function(gr) {
      if (is.null(gr)) return(invisible())
      nm <- if (is.null(gr$name)) "" else gr$name
      if (grepl("^grill|^panel.background|^panel.border", nm)) return(invisible())
      if (inherits(gr, "textpath") || inherits(gr, "labelpath")) {
        tp_trees[[length(tp_trees) + 1L]] <<- gr
        return(invisible())
      }
      if (inherits(gr, "gTree")) {
        for (ch in gr$children) find_tp(ch)
      }
    }
    for (ch in gt$children) find_tp(ch)

    # Panel ranges for projecting built data points into normalized canvas coords
    pp <- b$layout$panel_params[[pi]]
    xr <- if (!is.null(pp$x.range)) pp$x.range else c(0, 1)
    yr <- if (!is.null(pp$y.range)) pp$y.range else c(0, 1)

    for (ti in seq_along(tp_trees)) {
      tree <- tp_trees[[ti]]
      # Built data for this layer & panel
      layer_groups <- list()
      if (ti <= length(tp_layer_idx)) {
        ld <- b$data[[tp_layer_idx[ti]]]
        if (!is.null(ld) && nrow(ld) > 0) {
          ld_p <- ld[as.integer(ld$PANEL) == pi, , drop = FALSE]
          if (nrow(ld_p) > 0) {
            ug <- unique(ld_p$group)
            for (g_id in ug) {
              sub <- ld_p[ld_p$group == g_id, , drop = FALSE]
              lbl <- as.character(sub$label[1])
              if (is.na(lbl)) lbl <- ""
              px <- as.numeric(sub$x)
              py <- as.numeric(sub$y)
              if (is_flipped) {
                tmp_v <- px; px <- py; py <- tmp_v
              }
              # Map data coords to panel norm -> canvas norm_tl
              nx <- (px - xr[1]) / max(xr[2] - xr[1], 1e-12)
              ny <- (py - yr[1]) / max(yr[2] - yr[1], 1e-12)
              cx <- p_bb[1] + nx * (p_bb[3] - p_bb[1])
              cy <- p_bb[4] - ny * (p_bb[4] - p_bb[2])
              ok <- is.finite(cx) & is.finite(cy)
              layer_groups[[length(layer_groups) + 1L]] <- list(
                group = g_id, label = lbl,
                pts = cbind(cx[ok], cy[ok])
              )
            }
          }
        }
      }

      # Extract raw polylines, polygons, and text grobs from tree$children
      raw_polylines <- list()
      raw_polygons <- list()
      raw_texts <- list()

      walk_tp <- function(gr) {
        if (is.null(gr)) return(invisible())
        if (inherits(gr, "gTree")) {
          for (ch in gr$children) walk_tp(ch)
          return(invisible())
        }
        if (inherits(gr, "polyline")) {
          xy <- dloc_vec(gr$x, gr$y)
          n_pts <- nrow(xy)
          if (n_pts >= 2L) {
            ids <- if (!is.null(gr$id)) gr$id else rep(1L, n_pts)
            uids <- unique(ids)
            n_seg <- length(uids)
            cols <- if (is.null(gr$gp$col)) rep("#000000", n_seg) else rep_len(gr$gp$col, n_seg)
            alphas <- if (is.null(gr$gp$alpha)) rep(1, n_seg) else rep_len(gr$gp$alpha, n_seg)
            lwds <- if (is.null(gr$gp$lwd)) rep(1.4226, n_seg) else rep_len(as.numeric(gr$gp$lwd), n_seg)
            ltys <- if (is.null(gr$gp$lty)) rep("solid", n_seg) else rep_len(as.character(gr$gp$lty), n_seg)
            for (si in seq_along(uids)) {
              idx <- which(ids == uids[si])
              if (length(idx) < 2L) next
              c1 <- .gtp_col(cols[si])
              a1 <- if (is.na(alphas[si])) 1 else as.numeric(alphas[si])
              eff_a <- round(c1$alpha * a1, 4)
              lw_pt <- round(lwds[si] * 72 / 96, 3)
              lt <- .gtp_lty(ltys[si])
              if (c1$hex == "none" || eff_a == 0 || lw_pt <= 0 || lt == "blank") next
              pts_m <- xy[idx, , drop = FALSE]
              raw_polylines[[length(raw_polylines) + 1L]] <<- list(
                pts_mat = pts_m,
                points_norm_tl = unname(lapply(seq_len(nrow(pts_m)), function(k) as.numeric(pts_m[k, ]))),
                color_hex = c1$hex,
                alpha = eff_a,
                linewidth_pt = lw_pt,
                linetype = lt
              )
            }
          }
        } else if (inherits(gr, "polygon")) {
          xy <- dloc_vec(gr$x, gr$y)
          if (nrow(xy) >= 3L) {
            f1 <- .gtp_col(gr$gp$fill)
            c1 <- .gtp_col(gr$gp$col)
            a1 <- if (is.null(gr$gp$alpha) || is.na(gr$gp$alpha[1])) 1 else as.numeric(gr$gp$alpha[1])
            lwd <- if (is.null(gr$gp$lwd) || is.na(gr$gp$lwd[1])) 0.5 else as.numeric(gr$gp$lwd[1]) * 72 / 96
            if (lwd <= 0) c1 <- list(hex = "none", alpha = 0)
            nv <- nrow(unique(round(xy, 5)))
            raw_polygons[[length(raw_polygons) + 1L]] <<- list(
              pts_mat = xy,
              points_norm_tl = unname(lapply(seq_len(nrow(xy)), function(k) as.numeric(xy[k, ]))),
              bbox_norm_tl = round(c(min(xy[, 1]), min(xy[, 2]), max(xy[, 1]), max(xy[, 2])), 6),
              is_rounded = nv > 6L,
              facecolor_hex = f1$hex,
              face_alpha = round(f1$alpha * a1, 4),
              edgecolor_hex = c1$hex,
              edge_alpha = round(c1$alpha * a1, 4),
              linewidth_pt = round(lwd, 3)
            )
          }
        } else if (inherits(gr, "text")) {
          labs <- as.character(gr$label)
          n <- length(labs)
          if (n > 0L) {
            xy <- dloc_vec(gr$x, gr$y)
            rots <- if (is.null(gr$rot)) rep(0, n) else rep_len(as.numeric(gr$rot), n)
            cols <- if (is.null(gr$gp$col)) rep("#000000", n) else rep_len(gr$gp$col, n)
            alphas <- if (is.null(gr$gp$alpha)) rep(1, n) else rep_len(gr$gp$alpha, n)
            fss <- if (is.null(gr$gp$fontsize)) rep(11, n) else rep_len(as.numeric(gr$gp$fontsize), n)
            fonts <- if (is.null(gr$gp$font)) rep(1L, n) else rep_len(as.integer(gr$gp$font), n)
            glyphs <- vector("list", n)
            for (k in seq_len(n)) {
              c1 <- .gtp_col(cols[k])
              a1 <- if (is.na(alphas[k])) 1 else as.numeric(alphas[k])
              fc <- if (is.na(fonts[k])) 1L else fonts[k]
              fs_pt <- round(fss[k], 3)
              # Approximate glyph box around visual center (cx, cy) using fontsize
              hw <- (0.35 * fs_pt) / (w_in * 72)
              hh <- (0.5 * fs_pt) / (h_in * 72)
              glyphs[[k]] <- list(
                char = labs[k],
                cx_norm = xy[k, 1],
                cy_norm = xy[k, 2],
                angle_deg = .gtp_norm_angle(rots[k]),
                color_hex = if (c1$hex == "none") "#000000" else c1$hex,
                alpha = round(c1$alpha * a1, 4),
                fontsize_pt = fs_pt,
                fontweight = if (fc %in% c(2L, 4L)) "bold" else "normal",
                fontstyle = if (fc %in% c(3L, 4L)) "italic" else "normal",
                bbox_norm_tl = round(c(xy[k, 1] - hw, xy[k, 2] - hh, xy[k, 1] + hw, xy[k, 2] + hh), 6)
              )
            }
            raw_texts[[length(raw_texts) + 1L]] <<- glyphs
          }
        }
      }
      walk_tp(tree)

      # Partition raw_texts into per-curve glyph lists
      grouped_glyphs <- list()
      if (length(raw_texts) == 1L && length(layer_groups) > 1L) {
        # Single concatenated text grob (standard GeomTextpath with multiple groups)
        all_g <- raw_texts[[1]]
        all_str <- paste(vapply(all_g, function(g) g$char, character(1)), collapse = "")
        pos <- 1L
        matched_ok <- TRUE
        slices <- list()
        for (lg in layer_groups) {
          nc <- nchar(lg$label)
          if (nc == 0L) next
          if (pos + nc - 1L <= length(all_g)) {
            sub_str <- substr(all_str, pos, pos + nc - 1L)
            if (identical(sub_str, lg$label)) {
              slices[[length(slices) + 1L]] <- list(
                text = lg$label, glyphs = all_g[pos:(pos + nc - 1L)], ref_pts = lg$pts
              )
              pos <- pos + nc
              next
            }
          }
          matched_ok <- FALSE
          break
        }
        if (matched_ok && pos == length(all_g) + 1L) {
          grouped_glyphs <- slices
        } else {
          # Fallback: match substrings in order (handles remove_long dropping a curve)
          pos <- 1L
          for (lg in layer_groups) {
            nc <- nchar(lg$label)
            if (nc == 0L || pos > length(all_g)) next
            rem <- substr(all_str, pos, nchar(all_str))
            if (startsWith(rem, lg$label)) {
              grouped_glyphs[[length(grouped_glyphs) + 1L]] <- list(
                text = lg$label, glyphs = all_g[pos:(pos + nc - 1L)], ref_pts = lg$pts
              )
              pos <- pos + nc
            }
          }
          if (length(grouped_glyphs) == 0L) {
            grouped_glyphs[[1]] <- list(text = all_str, glyphs = all_g, ref_pts = matrix(numeric(0), ncol = 2))
          }
        }
      } else {
        # Already one text grob per curve (e.g. GeomLabelpath or single group)
        for (gi in seq_along(raw_texts)) {
          gs <- raw_texts[[gi]]
          txt <- paste(vapply(gs, function(g) g$char, character(1)), collapse = "")
          # Find best matching built group by label and proximity
          g_pts <- do.call(rbind, lapply(gs, function(g) c(g$cx_norm, g$cy_norm)))
          best_pts <- matrix(numeric(0), ncol = 2)
          best_d <- Inf
          for (lg in layer_groups) {
            if (identical(lg$label, txt) && nrow(lg$pts) > 0) {
              d <- .gtp_polyline_dist(g_pts, lg$pts)
              if (d < best_d) { best_d <- d; best_pts <- lg$pts }
            }
          }
          grouped_glyphs[[length(grouped_glyphs) + 1L]] <- list(
            text = txt, glyphs = gs, ref_pts = best_pts
          )
        }
      }

      n_c <- length(grouped_glyphs)
      if (n_c == 0L) next
      curve_polys <- vector("list", n_c)
      for (k in seq_len(n_c)) curve_polys[[k]] <- list()
      curve_boxes <- vector("list", n_c)

      # Assign each polyline to the closest curve (using built ref_pts, or glyph centers)
      for (pl in raw_polylines) {
        if (n_c == 1L) {
          best_k <- 1L
        } else {
          best_k <- 1L; best_d <- Inf
          for (k in seq_len(n_c)) {
            target <- grouped_glyphs[[k]]$ref_pts
            if (nrow(target) == 0L) {
              target <- do.call(rbind, lapply(grouped_glyphs[[k]]$glyphs, function(g) c(g$cx_norm, g$cy_norm)))
            }
            d <- .gtp_polyline_dist(pl$pts_mat, target)
            if (d < best_d) { best_d <- d; best_k <- k }
          }
        }
        pl_clean <- pl[c("points_norm_tl", "color_hex", "alpha", "linewidth_pt", "linetype")]
        curve_polys[[best_k]][[length(curve_polys[[best_k]]) + 1L]] <- pl_clean
      }

      # Assign each polygon box to the curve whose glyph centroid is closest / inside
      for (bx in raw_polygons) {
        bb <- bx$bbox_norm_tl
        bc <- c(0.5 * (bb[1] + bb[3]), 0.5 * (bb[2] + bb[4]))
        best_k <- 1L; best_d <- Inf
        for (k in seq_len(n_c)) {
          gs <- grouped_glyphs[[k]]$glyphs
          gc <- c(mean(vapply(gs, function(g) g$cx_norm, numeric(1))),
                  mean(vapply(gs, function(g) g$cy_norm, numeric(1))))
          d <- sqrt(sum((gc - bc)^2))
          if (d < best_d) { best_d <- d; best_k <- k }
        }
        curve_boxes[[best_k]] <- bx[c("points_norm_tl", "bbox_norm_tl", "is_rounded",
                                      "facecolor_hex", "face_alpha", "edgecolor_hex",
                                      "edge_alpha", "linewidth_pt")]
      }

      for (k in seq_len(n_c)) {
        curves[[length(curves) + 1L]] <- list(
          panel = pi,
          text = grouped_glyphs[[k]]$text,
          glyphs = grouped_glyphs[[k]]$glyphs,
          polylines = curve_polys[[k]],
          box = curve_boxes[[k]]
        )
      }
    }

    panels[[pi]] <- list(panel_index = pi, bbox_norm_tl = p_bb)
  }
  upViewport(0)
  list(geomtextpath = list(panels = panels, curves = curves))
}
