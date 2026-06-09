# -- Sphinx configuration for easy-tdx ---------------------------------------
project = "easy-tdx"
copyright = "2025, Justin Gu"
author = "Justin Gu"

# The full version, including alpha/beta/rc tags
release = "1.8.0"

# -- Extensions ---------------------------------------------------------------
extensions = [
    "myst_parser",
]

# -- MyST parser config -------------------------------------------------------
myst_enable_extensions = [
    "colon_fence",
    "deflist",
]

# Suppress cross-reference warnings for relative links (e.g. docs/*.md, LICENSE)
suppress_warnings = ["myst.xref_missing"]

# -- HTML output --------------------------------------------------------------
html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "navigation_depth": 3,
}

# -- Source suffix -------------------------------------------------------------
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}
