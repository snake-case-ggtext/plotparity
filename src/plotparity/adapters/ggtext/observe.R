# ggtext adapter: R-side observation hooks (sourced by engines/r/render.R).
#   adapter_setup(family)              : geom defaults that theme base_family does NOT reach
#   adapter_observe(p, w_in, h_in, dpi): list of observation streams for one plot
# extract_r_grob_semantics() is the research Probe 2D (experiment/render_r.R), except that colours are resolved
# to hex with R's own col2rgb (R names like "red3"/"grey40" are unknown to matplotlib), sizes are in bigpts
# (1/72 in, matplotlib's pt), and gpar alpha is recorded for frames and text, multiplied down the grob tree
# (grid multiplies a parent's alpha into its children's).

adapter_setup <- function(family) {
  # theme_grey(base_family=) does NOT affect geom_richtext/geom_textbox default_aes$family.
  update_geom_defaults(ggtext::GeomRichText, list(family = family))
  update_geom_defaults(ggtext::GeomTextBox, list(family = family))
}

r_hex <- function(v, default) {
  if (is.null(v) || length(v) == 0 || is.na(v[1])) return(default)
  v <- as.character(v[1])
  if (v %in% c("none", "transparent", "NA")) return("none")
  rgba <- tryCatch(grDevices::col2rgb(v, alpha = TRUE)[, 1], error = function(e) NULL)
  if (is.null(rgba)) return(v)
  if (rgba[4] == 0) return("none")
  if (rgba[4] < 255) sprintf("#%02X%02X%02X%02X", rgba[1], rgba[2], rgba[3], rgba[4]) else sprintf("#%02X%02X%02X", rgba[1], rgba[2], rgba[3])
}

adapter_observe <- function(p, w_in, h_in, dpi) {
  list(ggtext_grobs = extract_r_grob_semantics(p, w_in, h_in, dpi))
}

extract_r_grob_semantics <- function(p, width_in, height_in, dpi) {
  t0 <- proc.time()[["elapsed"]]
  tmp <- tempfile(fileext = ".png")
  on.exit(unlink(tmp), add = TRUE)
  agg_png(tmp, width = width_in, height = height_in, units = "in", res = dpi)
  on.exit(if (dev.cur() > 1) dev.off(), add = TRUE)

  g <- ggplotGrob(p)
  grid.draw(g)
  grid.force()

  # Helper: convert current viewport's (x, y) to normalized top-left [0, 1] and pt72
  to_norm_tl <- function(x_u, y_u) {
    loc <- deviceLoc(x_u, y_u, valueOnly = TRUE) # inches, bottom-left origin
    list(
      x_norm = round(loc$x / width_in, 5),
      y_norm = round(1.0 - loc$y / height_in, 5),
      x_pt = round(loc$x * 72, 3),
      y_pt = round((height_in - loc$y) * 72, 3)
    )
  }

  ls_all <- grid.ls(grobs = TRUE, viewports = TRUE, print = FALSE)
  vp_names <- ls_all$name[ls_all$type == "vpListing"]
  panel_vps <- grep("^panel", vp_names, value = TRUE)

  # Panel bounding box
  panel_bbox <- NULL
  if (length(panel_vps) > 0) {
    seekViewport(panel_vps[1])
    bl <- to_norm_tl(unit(0, "npc"), unit(0, "npc"))
    tr <- to_norm_tl(unit(1, "npc"), unit(1, "npc"))
    panel_bbox <- c(bl$x_norm, tr$y_norm, tr$x_norm, bl$y_norm)
  }

  # Traverse forced grid tree to find every gridtext box (richtext_grob / textbox_grob)
  # We walk grobs by finding gTreeListing entries for richtext_grob or textbox_grob
  boxes <- list()
  box_idx <- 0

  # Helper to walk a gTree while pushing its viewports so deviceLoc is accurate
  gp_alpha <- function(gp) if (is.null(gp) || is.null(gp$alpha) || is.na(gp$alpha[1])) 1.0 else as.numeric(gp$alpha[1])
  walk_grob <- function(gr, inherited_angle = 0, inherited_alpha = 1.0) {
    if (is.null(gr)) return()
    pushed <- FALSE
    cur_angle <- inherited_angle
    cur_alpha <- inherited_alpha * gp_alpha(gr$gp)
    if (!is.null(gr$vp)) {
      pushViewport(gr$vp)
      pushed <- TRUE
      if (inherits(gr$vp, "viewport") && !is.null(gr$vp$angle)) {
        cur_angle <- cur_angle + as.numeric(gr$vp$angle)
      }
    }
    on.exit(if (pushed) popViewport(), add = TRUE)

    # Check if this gTree directly contains gridtext.roundrect / gridtext.text leaves
    if (inherits(gr, "gTree") && length(gr$children) > 0) {
      child_names <- names(gr$children)
      has_gt <- any(grepl("^gridtext\\.", child_names)) ||
                inherits(gr, "richtext_grob") ||
                inherits(gr, "textbox_grob")

      # Direct box container: its children include roundrect or text grobs (not sub-gTrees)
      child_classes <- vapply(gr$children, function(c) class(c)[1], character(1))
      is_box_container <- any(child_classes %in% c("roundrect", "text")) &&
                          (any(grepl("^gridtext\\.", child_names)) ||
                           (!is.null(gr$xext) && !is.null(gr$yext)))

      if (is_box_container) {
        box_idx <<- box_idx + 1
        rect_info <- NULL
        text_runs <- list()
        for (ch in gr$children) {
          if (inherits(ch, "roundrect")) {
            x0_u <- ch$x - ch$hjust * ch$width
            y0_u <- ch$y - ch$vjust * ch$height
            w_pt <- convertWidth(ch$width, "bigpts", valueOnly = TRUE)
            h_pt <- convertHeight(ch$height, "bigpts", valueOnly = TRUE)
            r_pt <- convertUnit(ch$r, "bigpts", valueOnly = TRUE)
            c_bl <- to_norm_tl(x0_u, y0_u)
            c_br <- to_norm_tl(x0_u + ch$width, y0_u)
            c_tr <- to_norm_tl(x0_u + ch$width, y0_u + ch$height)
            c_tl <- to_norm_tl(x0_u, y0_u + ch$height)
            xs <- c(c_bl$x_norm, c_br$x_norm, c_tr$x_norm, c_tl$x_norm)
            ys <- c(c_bl$y_norm, c_br$y_norm, c_tr$y_norm, c_tl$y_norm)
            rect_info <- list(
              grob_class = paste(class(ch), collapse = ","),
              width_pt = round(w_pt, 3),
              height_pt = round(h_pt, 3),
              corner_radius_pt = round(r_pt, 3),
              is_rounded = r_pt > 0.01,
              vertex_count = NA,
              col = r_hex(ch$gp$col, "none"),
              fill = r_hex(ch$gp$fill, "none"),
              alpha = cur_alpha * gp_alpha(ch$gp),
              lwd = if (is.null(ch$gp$lwd)) NA else as.numeric(ch$gp$lwd),
              bbox_norm_tl = c(min(xs), min(ys), max(xs), max(ys))
            )
          } else if (inherits(ch, "polygon") && grepl("roundrect|rect|box", ch$name)) {
            # Forced roundrect becomes a polygon grob with its own viewport (rrvp):
            # 4 vertices => sharp corners, >6 => rounded
            ch_pushed <- FALSE
            if (!is.null(ch$vp)) { pushViewport(ch$vp); ch_pushed <- TRUE }
            x_loc <- convertX(ch$x, "bigpts", valueOnly = TRUE)
            y_loc <- convertY(ch$y, "bigpts", valueOnly = TRUE)
            loc <- deviceLoc(ch$x, ch$y, valueOnly = TRUE)
            if (ch_pushed) popViewport()
            xs <- loc$x / width_in
            ys <- 1.0 - loc$y / height_in
            n_verts <- length(x_loc)
            rect_info <- list(
              grob_class = paste(class(ch), collapse = ","),
              width_pt = round(max(x_loc) - min(x_loc), 3),
              height_pt = round(max(y_loc) - min(y_loc), 3),
              local_bbox_pt = c(round(min(x_loc), 3), round(min(y_loc), 3),
                                round(max(x_loc), 3), round(max(y_loc), 3)),
              vertex_count = n_verts,
              is_rounded = n_verts > 6,
              col = r_hex(ch$gp$col, "none"),
              fill = r_hex(ch$gp$fill, "none"),
              alpha = cur_alpha * gp_alpha(ch$gp),
              lwd = if (is.null(ch$gp$lwd)) NA else as.numeric(ch$gp$lwd),
              bbox_norm_tl = c(round(min(xs), 5), round(min(ys), 5),
                               round(max(xs), 5), round(max(ys), 5))
            )
          } else if (inherits(ch, "text")) {
            lbl <- as.character(ch$label)
            if (nzchar(lbl)) {
              ch_pushed <- FALSE
              if (!is.null(ch$vp)) { pushViewport(ch$vp); ch_pushed <- TRUE }
              pos <- to_norm_tl(ch$x, ch$y)
              x_lpt <- round(convertX(ch$x, "bigpts", valueOnly = TRUE), 3)
              y_lpt <- round(convertY(ch$y, "bigpts", valueOnly = TRUE), 3)
              if (ch_pushed) popViewport()
              font_code <- if (is.null(ch$gp$font)) 1 else as.integer(ch$gp$font)
              weight <- if (font_code %in% c(2, 4)) "bold" else "normal"
              fstyle <- if (font_code %in% c(3, 4)) "italic" else "normal"
              col_val <- r_hex(ch$gp$col, "#000000")
              fs <- if (is.null(ch$gp$fontsize)) NA else as.numeric(ch$gp$fontsize)
              fam <- if (is.null(ch$gp$fontfamily)) "" else as.character(ch$gp$fontfamily)
              alpha_val <- cur_alpha * gp_alpha(ch$gp)
              text_runs[[length(text_runs) + 1]] <- list(
                text = lbl,
                x_norm = pos$x_norm,
                y_norm = pos$y_norm,
                x_local_pt = x_lpt,
                y_local_pt = y_lpt,
                color = col_val,
                alpha = alpha_val,
                fontsize_pt = round(fs, 3),
                fontweight = weight,
                fontstyle = fstyle,
                fontfamily = fam,
                angle_deg = round(cur_angle, 3)
              )
            }
          }
        }
        boxes[[box_idx]] <<- list(
          box_index = box_idx,
          grob_name = gr$name,
          grob_class = paste(class(gr), collapse = ","),
          angle_deg = round(cur_angle, 3),
          rect = rect_info,
          text_runs = text_runs
        )
        return()
      }

      for (ch in gr$children) {
        walk_grob(ch, cur_angle, cur_alpha)
      }
    }
  }

  # Walk forced top-level gtable from grid.grab()
  grabbed <- grid.grab(wrap.grobs = FALSE)
  # Reset to root viewport and walk
  upViewport(0)
  # Locate panel gTree inside grabbed or panel viewport directly
  panel_vp_name <- if (length(panel_vps) > 0) panel_vps[1] else NULL
  if (!is.null(panel_vp_name)) {
    seekViewport(panel_vp_name)
    panel_gtree <- grid.get(panel_vp_name)
    walk_grob(panel_gtree, 0)
  }

  elapsed_ms <- round((proc.time()[["elapsed"]] - t0) * 1000, 2)
  list(
    probe = "2D_r_grid_grob",
    panel_bbox_norm_tl = panel_bbox,
    box_count = length(boxes),
    boxes = boxes,
    extraction_ms = elapsed_ms
  )
}

