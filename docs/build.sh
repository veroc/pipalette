#!/usr/bin/env bash
# Build piPalette user manual as PDF.
#
# Requires: pandoc, xelatex (texlive-xetex), Inter + DejaVu Sans Mono fonts.
# Output:   docs/build/piPalette-Manual.pdf

set -euo pipefail

cd "$(dirname "$0")"

mkdir -p build

CHAPTERS=(
  chapters/01-rolls.md
  chapters/02-film-tables.md
  chapters/03-device.md
)

pandoc \
  --from=markdown+definition_lists+raw_tex \
  --to=pdf \
  --pdf-engine=xelatex \
  --template=template.tex \
  --highlight-style=breezedark \
  --metadata-file=metadata.yaml \
  --output=build/piPalette-Manual.pdf \
  "${CHAPTERS[@]}"

echo "wrote build/piPalette-Manual.pdf"
