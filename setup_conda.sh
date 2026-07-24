#!/usr/bin/env bash
# Create the project-local Conda environment in .conda/
# Works with older Conda versions that break on "conda env create -f environment.yml".

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_PATH="$ROOT/.conda"

echo "Environment path: $ENV_PATH"

if [[ -d "$ENV_PATH" ]]; then
  echo "Removing existing environment..."
  conda env remove -p "$ENV_PATH" -y || rm -rf "$ENV_PATH"
fi

conda create -y -p "$ENV_PATH" -c conda-forge \
  "python>=3.13" \
  "pandas>=2.0" \
  "openpyxl>=3.1" \
  "requests>=2.31" \
  "python-dateutil>=2.8" \
  "pyyaml>=6.0" \
  "pypdf>=5.0" \
  "wordninja>=2.0" \
  "pypdfium2>=4.0" \
  "pillow>=10.0" \
  "pytesseract>=0.3" \
  "tesseract>=5.0"

# Avoid pip/conda MuPDF mismatches: OCR rendering uses pypdfium2, not pymupdf.
"$ENV_PATH/bin/pip" uninstall -y pymupdf PyMuPDF 2>/dev/null || true

echo
echo "Setup complete."
echo "Activate:  conda activate $ENV_PATH"
echo "Run app:   ./run.sh --help"
echo
"$ENV_PATH/bin/python" --version
