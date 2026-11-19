#!/usr/bin/env Rscript
# Reference render worker:  Rscript render.R job.json
# job: {out_dir, packages, ttf_dir, observe_r, width_px, height_px, dpi, cases: [{id, code, data}]}
# Per case writes <out_dir>/<id>/{reference.png, observations.json} or error.json, plus r_env.json.
# Data arrives fully expanded from Python (single parser), so R never parses YAML.

invisible(Sys.setlocale("LC_COLLATE", "C"))   # match Python's code-point string ordering (factor levels)
suppressPackageStartupMessages({
  library(jsonlite)
  library(grid)
  library(ragg)
  library(systemfonts)
})

args <- commandArgs(trailingOnly = TRUE)
job <- fromJSON(args[1], simplifyVector = TRUE, simplifyDataFrame = FALSE)
for (pkg in job$packages) suppressPackageStartupMessages(library(pkg, character.only = TRUE))

# Pinned fonts: the SAME TTF files the candidate's matplotlib uses.
ttf <- job$ttf_dir
needed <- file.path(ttf, c("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf"))
if (!all(file.exists(needed))) stop(paste("pinned font files missing in", ttf))
register_font(name = "DejaVu Sans", plain = needed[1], bold = needed[2], italic = needed[3], bolditalic = needed[4])
font_family <- "DejaVu Sans"
theme_set(theme_grey(base_family = font_family))

PT_PER_MM <- 72.27 / 25.4
source(job$observe_r)                       # defines adapter_setup(family) and adapter_observe(p, w_in, h_in, dpi)
adapter_setup(font_family)

w_in <- job$width_px / job$dpi
h_in <- job$height_px / job$dpi

pkg_versions <- lapply(setNames(nm = unique(c(job$packages, "ragg", "systemfonts", "jsonlite"))),
                       function(p) as.character(packageVersion(p)))
write_json(list(r_version = R.version.string, platform = R.version$platform, packages = pkg_versions),
           file.path(job$out_dir, "r_env.json"), auto_unbox = TRUE, pretty = TRUE)

for (case in job$cases) {
  cdir <- file.path(job$out_dir, case$id)
  dir.create(cdir, recursive = TRUE, showWarnings = FALSE)
  res <- tryCatch({
    env <- new.env(parent = globalenv())
    env$df <- if (length(case$data)) as.data.frame(lapply(case$data, function(v) unlist(lapply(v, function(x) if (is.null(x)) NA else x))),
                                                   stringsAsFactors = FALSE, check.names = FALSE) else NULL
    env$PT_PER_MM <- PT_PER_MM
    p <- eval(parse(text = case$code), envir = env)
    ggsave(file.path(cdir, "reference.png"), p, width = w_in, height = h_in, dpi = job$dpi, device = ragg::agg_png)
    obs <- adapter_observe(p, w_in, h_in, job$dpi)
    # jsonlite's default digits (4 decimals) unless the adapter asks for more (adapter_json_digits; NA = full)
    digits <- if (exists("adapter_json_digits")) adapter_json_digits else 4
    write_json(obs, file.path(cdir, "observations.json"), auto_unbox = TRUE, pretty = TRUE, digits = digits)
    NULL
  }, error = function(e) e)
  if (!is.null(res)) {
    write_json(list(kind = "reference_error", message = conditionMessage(res)),
               file.path(cdir, "error.json"), auto_unbox = TRUE, pretty = TRUE)
    cat(sprintf("[R] %s ERROR %s\n", case$id, conditionMessage(res)))
  } else {
    cat(sprintf("[R] %s ok\n", case$id))
  }
}
