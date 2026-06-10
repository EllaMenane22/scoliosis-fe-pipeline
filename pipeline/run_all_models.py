"""
Baseline batch runner. Walks every patient folder, pushes that patient's .dat
through Marc, and lets Marc drop the .t16 / .out / .sts back into the same
folder.

This is the single baseline model only (no M2-M5 parameter sweep). Anything
that's already finished (its .t16 exists) is skipped, so re-running is cheap.
To force one patient to run again, delete its .t16 first.

Has to run on the Windows machine with Marc installed.
"""

import subprocess
import logging
from pathlib import Path
import time


# Config. Only this block should ever need touching if paths or the PC change.

MARC_PATH = r"C:\Program Files\MSC.Software\Marc\2024.1.0\marc2024.1\tools\run_marc.bat"

# Folder of patient subfolders, each holding its own .dat (P08\P08.dat, ...).
OUTPUT_ROOT = Path(
    r"C:\Users\<username>\OneDrive - Imperial College London\Year 4\FYP\Patient_1_final\Automated models\Updated automated"
)

# Solver threads. This PC has 16 cores; 14 leaves a couple for Windows.
NTS = 14
NTE = 14

# Pause between jobs so file handles release cleanly.
JOB_DELAY = 1


# Runner

def process_all_jobs(marc_path: str, output_root: Path) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("BASELINE BATCH RUN")
    logger.info("=" * 60)
    logger.info(f"Marc path:   {marc_path}")
    logger.info(f"Output root: {output_root}")

    if not Path(marc_path).exists():
        logger.error(f"Marc run_marc.bat not found: {marc_path}")
        return

    if not output_root.exists():
        logger.error(f"Output root not found: {output_root}")
        return

    patient_dirs = sorted(d for d in output_root.iterdir() if d.is_dir())

    if not patient_dirs:
        logger.warning(f"No patient subfolders found in {output_root}")
        return

    logger.info(f"Patient folders found: {len(patient_dirs)}")
    logger.info([d.name for d in patient_dirs])
    logger.info("")

    # Running tallies for the summary at the end.
    total_attempted = 0
    total_ok = 0
    total_failed = 0
    total_skipped = 0
    failed_jobs = []

    for patient_dir in patient_dirs:
        pid = patient_dir.name

        # Normally PXX.dat, but glob lets it cope with any .dat name in there.
        dat_files = sorted(patient_dir.glob("*.dat"))

        if not dat_files:
            logger.warning(f"  [{pid}] No .dat file found - skipping")
            total_skipped += 1
            continue

        if len(dat_files) > 1:
            logger.warning(
                f"  [{pid}] Multiple .dat files found: "
                f"{[f.name for f in dat_files]} - using {dat_files[0].name}"
            )

        dat_file = dat_files[0]
        job_id = dat_file.stem
        t16_path = patient_dir / f"{job_id}.t16"

        # A .t16 means this one already solved, so leave it be.
        if t16_path.exists():
            logger.info(
                f"  [{pid}] Already complete (.t16 exists) - skipping. "
                f"Delete {t16_path.name} to re-run."
            )
            total_skipped += 1
            continue

        logger.info(f"  [{pid}] Submitting: {job_id}")
        total_attempted += 1

        # -back yes runs in the background; -nts/-nte set the thread counts.
        cmd = f'"{marc_path}" -jid "{job_id}" -back yes -nts {NTS} -nte {NTE}'

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(patient_dir),   # run from the patient folder so outputs land there
            )

            # Marc doesn't always return a useful exit code, so the real test of
            # success is whether the .t16 actually showed up.
            if t16_path.exists():
                logger.info(f"  [{pid}] COMPLETE")
                total_ok += 1
            else:
                logger.error(
                    f"  [{pid}] FAILED - .t16 not created\n"
                    f"    Expected: {t16_path}\n"
                    f"    Check:    {patient_dir / (job_id + '.out')}\n"
                    f"    stdout:   {result.stdout[:500]}\n"
                    f"    stderr:   {result.stderr[:500]}"
                )
                total_failed += 1
                failed_jobs.append(pid)

        except Exception as e:
            logger.error(f"  [{pid}] Unexpected error: {e}")
            total_failed += 1
            failed_jobs.append(pid)

        time.sleep(JOB_DELAY)

    logger.info("")
    logger.info("=" * 60)
    logger.info("BATCH COMPLETE")
    logger.info(f"  Attempted : {total_attempted}")
    logger.info(f"  Succeeded : {total_ok}")
    logger.info(f"  Failed    : {total_failed}")
    logger.info(f"  Skipped   : {total_skipped}  (already done or no .dat)")
    if failed_jobs:
        logger.info("  Failed jobs:")
        for job in failed_jobs:
            logger.info(f"    {job}")
    logger.info("=" * 60)


if __name__ == "__main__":
    process_all_jobs(MARC_PATH, OUTPUT_ROOT)
