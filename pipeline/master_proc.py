"""
Stitches every patient's individual .proc file into one master .proc, so Marc
can write all the .dat files in a single pass instead of opening each patient
by hand.

Once it's built, open the master once in Marc Mentat:
  Tools -> Procedures -> run_all_build_dats_updated_automated.proc

Marc then loops the patients: clear the model, read that patient's .proc, write
their .dat, move on. The patient folders live under the "Updated automated"
directory below.
"""

from pathlib import Path


# Paths.

BASE_DIR = Path(
    r"C:\Users\<username>\OneDrive - Imperial College London\Year 4\FYP\Patient_1_final"
)

# Folder holding the patient subfolders (P08, P14, P18, ...).
OUTPUT_ROOT = Path(
    r"C:\Users\<username>\OneDrive - Imperial College London\Year 4\FYP\Patient_1_final\Automated models\Updated automated"
)

# The master gets written alongside the patient folders.
MASTER_PROC = OUTPUT_ROOT / "run_all_build_dats_updated_automated.proc"


# Helpers

def find_patient_folders(root: Path):
    """Every subfolder of OUTPUT_ROOT is treated as a patient folder."""
    if not root.exists():
        raise FileNotFoundError(f"Output root not found:\n{root}")

    return sorted([p for p in root.iterdir() if p.is_dir()])


def find_proc_file(patient_folder: Path, pid: str):
    """
    Find the patient's .proc. Tries the expected name first (PXX_procedure.proc),
    then PXX.proc, then falls back to whatever single .proc is in the folder. If
    there's more than one it just takes the first and says so, rather than guessing
    silently.
    """

    candidates = [
        patient_folder / f"{pid}_procedure.proc",
        patient_folder / f"{pid}.proc",
    ]

    for path in candidates:
        if path.exists():
            return path

    proc_files = sorted(patient_folder.glob("*.proc"))

    if len(proc_files) == 1:
        return proc_files[0]

    if len(proc_files) > 1:
        print(f"  [{pid}] multiple .proc files found:")
        for p in proc_files:
            print(f"      {p.name}")
        print(f"      using: {proc_files[0].name}")
        return proc_files[0]

    return None


# Build the master

def main():
    print("\n" + "=" * 72)
    print("MASTER PROC BUILDER")
    print("=" * 72)
    print(f"Patient folder root:")
    print(f"  {OUTPUT_ROOT}")
    print(f"Master proc will be written to:")
    print(f"  {MASTER_PROC}")
    print("=" * 72 + "\n")

    patient_folders = find_patient_folders(OUTPUT_ROOT)

    print(f"Folders found: {len(patient_folders)}")
    print([p.name for p in patient_folders])
    print()

    master_cmds = []
    jobs_added = 0
    missing = []

    for folder in patient_folders:
        pid = folder.name

        proc_path = find_proc_file(folder, pid)

        if proc_path is None:
            msg = f"[{pid}] MISSING .proc file in: {folder}"
            print(msg)
            missing.append(msg)
            continue

        # Marc wants forward slashes in the write path even on Windows.
        dat_full_path = str(folder / f"{pid}.dat").replace("\\", "/")

        proc_contents = proc_path.read_text(encoding="utf-8").strip()

        # For each patient: wipe the model, paste in their proc, write the .dat.
        master_cmds += [
            f"| --- {pid} ---",
            "*new_model yes",
            "",
            proc_contents,
            "",
            f'*write_marc "{dat_full_path}" yes',
            "",
        ]

        print(f"  [{pid}]")
        print(f"    proc: {proc_path.name}")
        print(f"    dat : {dat_full_path}")

        jobs_added += 1

    MASTER_PROC.write_text("\n".join(master_cmds), encoding="utf-8")

    print("\n" + "=" * 72)
    print(f"Master proc written:")
    print(f"  {MASTER_PROC}")
    print(f"Patients included: {jobs_added}")

    if missing:
        print(f"\nMissing .proc files ({len(missing)}):")
        for m in missing:
            print(f"  {m}")

    print("=" * 72)

    print(f"""
NEXT STEPS:
  1. Open Marc Mentat
  2. Tools -> Procedures...
  3. Select:
       {MASTER_PROC}
  4. Click Open.
  5. It will write {jobs_added} .dat files automatically into each patient folder.
""")


if __name__ == "__main__":
    main()
