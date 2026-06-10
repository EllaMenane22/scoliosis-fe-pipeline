# Scoliosis 2D FE pipeline

Automated pipeline for building and solving simplified 2D coronal-plane finite
element models of the scoliotic spine, used to predict in-brace Cobb angle
correction for single-curve adolescent idiopathic scoliosis (AIS) patients
braced with a Chêneau brace.

This repository accompanies my final year project. It contains the full
automated workflow: it takes patient Cobb angles and curve descriptions,
builds a patient-specific spine model, writes the Marc Mentat input, solves
every patient in a batch, and extracts the predicted Cobb angles.

## Important: the data here is synthetic

The CSV files in `data/` are **dummy data**, not real patient records. They
exist only to show the column format the scripts expect and to let the pipeline
run as a demonstration. See [DISCLAIMER.md](DISCLAIMER.md) for the full note.

## Pipeline

Run the scripts in this order. Steps 1 to 3 and 5 run under a normal Python
install; step 4 runs Marc, and step 5 must run under the Marc-bundled Python so
that `py_post` can be imported.

1. `coords.py` reads the two clinical CSVs and writes the per-patient geometry
   CSVs (spine nodes, pad centroids, pad node groups, pad forces, plus model
   metadata) into one folder per patient.
2. `proc_single_patient.py` turns each patient's CSVs into a Marc `.proc` file.
3. `master_proc.py` stitches every patient `.proc` into one master `.proc`.
   Open that master once in Marc Mentat (Tools, Procedures) to write all the
   `.dat` files automatically.
4. `run_all_models.py` runs every `.dat` through Marc and writes the results
   files (`.t16` etc.) back into each patient folder.
5. The results extraction step reads each `.t16`, measures the initial and
   final Cobb angle, and writes a formatted comparison spreadsheet.

## Requirements

- MSC Marc Mentat 2024.1 (beam and bushing elements)
- Python 3 for the geometry and proc-building scripts
- `openpyxl` for the results spreadsheet
- The results script must be run with the Marc-bundled Python so `py_post`
  resolves correctly

## Running it on your own machine: set your file paths

The scripts use absolute Windows file paths that point at the folders on the
machine they were written on. These are shown with a `<username>` placeholder,
for example:

```python
BASE_DIR = Path(r"C:\Users\<username>\OneDrive - Imperial College London\Year 4\FYP\Patient_1_final")
```

Before running anything you must replace these with the paths to your own
folders. Specifically, edit:

- `BASE_DIR` and `OUTPUT_ROOT` near the top of each script in `pipeline/`
- `MARC_PYTHON_PATHS` in the results script, so it points at your own Marc install

The scripts will not run until these point at real folders on your computer.

## Repository layout

```
pipeline/   the Python scripts
data/       synthetic example CSVs (no real patient data)
```
