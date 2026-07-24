# Thesis2Lib

Build library delivery folders from Canvas and your overview workbook.

## Setup

```bash
./setup_conda.sh && conda activate ./.conda
cp config.example.yml config.yml   # edit semester, course, paths
```

Set `canvas_url`, `course_id`, assignment IDs, `output_folder`, `programme`, and `semester` in `config.yml`.  
Overview workbook sheets: `Matching_<semester>`, `Grading_1st`, `Grading_2nd`.  
Use `--prompt-for-token` for the Canvas token (never put it in `config.yml`).

## Run

```bash
./run.sh --prompt-for-token --overwrite-output \
  --output-folder ../MSc_DSS/F2025/Output \
  --overview-file ../MSc_DSS/F2025/Input/Overview_F2025.xlsx
```

One run downloads PDFs, extracts titles, asks about uncertain cases (in a terminal), then writes the report, Archive, Open Access, and SOP.

**Rerun** with `--skip-existing-downloads` when PDFs are already in `Archive/` and you only need to refresh outputs or finish manual checks.

### Manual checks

Each flagged passed student may need one or more **separate** checks: overview **SNR**, **thesis PDF**, **revision document** (when the 2nd submission has multiple files), and **thesis title**. Auto-detected values in `manual_decisions.yml` are reference only — the terminal still asks until you confirm (or you edit the YAML and set `*_confirmed: true`).

If the **2nd submission has only one PDF**, it is the thesis and there is no revision document — no revision prompt. A revision prompt appears only when the 2nd submission has **two or more** current files.

After title extraction, every delivery-ready thesis is listed in **`Manual/manual_decisions.yml`** (sorted by name). Edit **`thesis_title`**, **`thesis_pdf`**, or **`revision_pdf`** there, then rerun with `--no-interactive-review` (and `--skip-existing-downloads` if PDFs are already cached). Edits are applied when they differ from the reference fields (`extracted_title`, `selected_pdf`, `detected_revision`) or when the matching `*_confirmed` flag is set.

Example row:

```yaml
"127052":
  student_name: Liang, Madison
  canvas_url: https://.../courses/18991/gradebook/speed_grader?assignment_id=156731&student_id=127052
  snr: "2112318"
  selected_pdf: Final_Thesis.pdf
  detected_revision: "(none)"
  extracted_title: Predicting European Scientific Excellence...
  title_confidence: Medium
  thesis_title: Predicting European Scientific Excellence...  # edit this
```

- Saved values are defaults in `[brackets]` when a terminal prompt appears
- Press **Enter** to accept the value in `[brackets]` — confirmed answers are saved immediately
- Empty Enter skips that issue

Use `--no-interactive-review` to skip prompts and apply `manual_decisions.yml` only.

## Output

| Folder / file | Contents |
|---------------|----------|
| `Archive/` | All submission PDFs + gradebook |
| `Open Access – …/` | One thesis PDF per ready passed student |
| `SOP/` | Thesis + assessments per student |
| `Processing_Report.xlsx` | Status per student |
| `Manual/manual_decisions.yml` | Title catalog + manual SNR/PDF/title choices |

## Useful flags

| Flag | Purpose |
|------|---------|
| `--skip-existing-downloads` | Do not re-download from Canvas |
| `--overwrite-output` | Replace generated folders and report |
| `--no-interactive-review` | No terminal prompts |
| `--report-only` | Report only, no delivery folders |
| `--validate-only` | Check config and Canvas access |

`./run.sh --help` for all options.
