"""
First stage of the pipeline. Turns the clinical spreadsheet into the per-patient
CSVs that the rest of the pipeline (proc builder, batch runner) needs.

Only single C-curves are handled here: a patient is kept if exactly one of the
three pre-brace Cobb columns (thoracic / lumbar / thoracolumbar) is filled in.
For each of those patients it works out the curve shape from the Cobb angle,
the convexity (which way it bends) and the upper/lower Cobb end vertebrae, then:
  - builds the 2D coronal spine node coordinates (C1 down to L5),
  - places the main corrective pad over the curve and a counter-pad two levels
    above and below it,
  - puts each pad's reference node at the centroid of the spine nodes it covers,
  - writes everything out as CSVs, one folder per patient.
"""

from __future__ import annotations

import csv
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# Paths

from pathlib import Path

BASE_DIR = Path(
    r"C:\Users\<username>\OneDrive - Imperial College London\Year 4\FYP\Patient_1_final"
)

# The two raw clinical inputs this script reads.
CLINICAL_CSV = BASE_DIR / "clinical_patient_data.csv"          # Cobb angles per patient
CONVEXITY_CSV = BASE_DIR / "Curve_convexity_directions.csv"    # convexity + Cobb end vertebrae

# Everything generated lands here, one subfolder per patient.
OUTPUT_ROOT = BASE_DIR / "Automated models" / "Updated automated"

# Column headers, kept here so a spreadsheet rename only needs editing in one place.

PATIENT_ID_COL = "Patient's Number (New System)"

COBB_BEFORE_COLS = {
    "Thoracic": "Thoracic Cobb Angle (Before Brace)",
    "Lumbar": "Lumbar Cobb Angle (Before Brace)",
    "Thoracolumbar": "Thoraco Lumbar Cobb Angle (Before Brace)",
}

CONVEXITY_COLS = {
    "Thoracic": "Thoracic Convexity",
    "Lumbar": "Lumbar Convexity",
    "Thoracolumbar": "Thoraco Lumbar Convexity",
}

# Several spellings of the same column are accepted, since the spreadsheet
# headers aren't consistent about capitalisation or singular/plural.
UPPER_COBB_COL_OPTIONS = [
    "Upper Cobb vertebrae",
    "Upper Cobb Vertebrae",
    "Upper Cobb Vertebra",
    "Upper Cobb vertebra",
]

LOWER_COBB_COL_OPTIONS = [
    "Lower Cobb vertebrae",
    "Lower Cobb Vertebrae",
    "Lower Cobb Vertebra",
    "Lower Cobb vertebra",
]


# Geometry settings

# Origin for the spine: x is the midline, y is the bottom (L5) starting height.
X_CENTER_MM = 400.0
Y_BOTTOM_MM = 650.0

# Same height for every level for now. Bone step is the vertebral body height,
# disc step the gap between bodies.
BONE_VERTICAL_STEP_MM = 21.48
DISC_VERTICAL_STEP_MM = 6.00

# How far the pad reference node sits off the spine sideways. This is just a
# convenient offset for the load point, not a real spine-to-pad distance.
PAD_X_OFFSET_MM = 35.0

# Counter-pads grab this many vertebrae above and below the main curve.
COUNTER_PAD_LEVELS = 2


# Pad forces

# Main pad force and the counter-pad force. The counter force is half the main,
# so the two counter-pads together cancel the main pad and the net lateral load
# stays at zero. Magnitudes were tuned during validation.
MAIN_APICAL_FORCE_N = 123_624.9084
COUNTER_FORCE_N = 61_812.4542


# Spine levels, listed bottom to top. 24 vertebrae, 2 nodes each (bottom/top of
# the body), so 48 spine nodes in total.

SPINE_LEVELS_BOTTOM_TO_TOP = [
    "L5", "L4", "L3", "L2", "L1",
    "T12", "T11", "T10", "T9", "T8", "T7", "T6",
    "T5", "T4", "T3", "T2", "T1",
    "C7", "C6", "C5", "C4", "C3", "C2", "C1",
]

LEVEL_TO_INDEX = {
    level: i for i, level in enumerate(SPINE_LEVELS_BOTTOM_TO_TOP)
}




# Small helpers for tidying messy spreadsheet values

def norm_header(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip()).lower()


def clean_text(value) -> Optional[str]:
    if value is None:
        return None

    text = str(value).strip()

    if text == "":
        return None

    return text


def clean_float(value) -> Optional[float]:
    text = clean_text(value)

    if text is None:
        return None

    try:
        return float(text)
    except ValueError:
        return None


def clean_patient_id(value) -> Optional[int]:
    text = clean_text(value)

    if text is None:
        return None

    match = re.search(r"\d+", text)

    if match is None:
        return None

    return int(match.group())


def patient_label_from_number(pid: int) -> str:
    if pid < 100:
        return f"P{pid:02d}"
    return f"P{pid}"


def normalise_convexity(value) -> Optional[str]:
    text = clean_text(value)

    if text is None:
        return None

    text = text.lower().strip()

    if text in {"left", "l"}:
        return "Left"

    if text in {"right", "r"}:
        return "Right"

    return None


def normalise_vertebra(value) -> Optional[str]:
    text = clean_text(value)

    if text is None:
        return None

    text = text.upper().replace(" ", "")

    # Accept T9, T09, L1, L01 and so on, then check it's a real level.
    match = re.fullmatch(r"([CTL])0?(\d+)", text)

    if match is None:
        return None

    prefix = match.group(1)
    number = int(match.group(2))

    label = f"{prefix}{number}"

    if label not in LEVEL_TO_INDEX:
        return None

    return label


def get_first_available(row: Dict[str, str], options: List[str]) -> Optional[str]:
    normalised_options = {norm_header(option) for option in options}

    for key, value in row.items():
        if norm_header(key) in normalised_options:
            return value

    return None


# Reading the clinical CSVs

def read_csv_as_dicts(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"CSV not found:\n{path}")

    rows: List[Dict[str, str]] = []

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        if reader.fieldnames is None:
            raise ValueError(f"No header row found in:\n{path}")

        for raw_row in reader:
            cleaned = {
                str(k).strip(): str(v).strip() if v is not None else ""
                for k, v in raw_row.items()
                if k is not None
            }

            if any(value != "" for value in cleaned.values()):
                rows.append(cleaned)

    return rows


def build_convexity_and_cobb_end_map(
    convexity_rows: List[Dict[str, str]],
) -> Dict[int, Dict[str, Optional[str]]]:
    out: Dict[int, Dict[str, Optional[str]]] = {}

    for row in convexity_rows:
        pid = clean_patient_id(row.get(PATIENT_ID_COL))

        if pid is None:
            continue

        out[pid] = {}

        for region, col_name in CONVEXITY_COLS.items():
            out[pid][region] = normalise_convexity(row.get(col_name))

        out[pid]["UpperCobbVertebra"] = normalise_vertebra(
            get_first_available(row, UPPER_COBB_COL_OPTIONS)
        )

        out[pid]["LowerCobbVertebra"] = normalise_vertebra(
            get_first_available(row, LOWER_COBB_COL_OPTIONS)
        )

    return out


# Patient selection.
# A patient is only modelled if exactly one Cobb region is filled in, i.e. a
# single curve. If two or three regions have angles it's an S-curve and gets
# left out, which is the scope of this project.

def identify_single_curve_patient(
    row: Dict[str, str],
) -> Optional[Tuple[str, float]]:
    present_curves: List[Tuple[str, float]] = []

    for region, col_name in COBB_BEFORE_COLS.items():
        angle = clean_float(row.get(col_name))

        if angle is not None:
            present_curves.append((region, angle))

    if len(present_curves) == 1:
        return present_curves[0]

    return None


# Node helpers.
# Each vertebra owns two nodes: the bottom of its body and the top. For level
# index i (0 = L5), those are nodes 2i+1 and 2i+2.

def vertebra_node_ids(level_index: int) -> Tuple[int, int]:
    return 2 * level_index + 1, 2 * level_index + 2


def nodes_for_level_indices(level_indices: List[int]) -> List[int]:
    node_ids: List[int] = []

    for idx in level_indices:
        n1, n2 = vertebra_node_ids(idx)
        node_ids.extend([n1, n2])

    return sorted(set(node_ids))


def level_labels_from_indices(indices: List[int]) -> List[str]:
    return [SPINE_LEVELS_BOTTOM_TO_TOP[i] for i in indices]


# Working out which vertebrae each pad covers, from the Cobb end vertebrae.
# Main pad spans the curve itself; the two counter-pads sit just above and
# just below it.

def define_pad_regions(
    upper_cobb: str,
    lower_cobb: str,
) -> Dict[str, List[int]]:
    upper_idx = LEVEL_TO_INDEX[upper_cobb]
    lower_idx = LEVEL_TO_INDEX[lower_cobb]

    # Anatomically, upper Cobb vertebra should be higher in the spine,
    # therefore it should have a larger index in the bottom-to-top list.
    if upper_idx < lower_idx:
        print(
            f"WARNING: Upper/lower Cobb vertebrae appear reversed "
            f"({upper_cobb}, {lower_cobb}). Swapping them."
        )
        upper_idx, lower_idx = lower_idx, upper_idx
        upper_cobb, lower_cobb = lower_cobb, upper_cobb

    main_indices = list(range(lower_idx, upper_idx + 1))

    distal_start = max(0, lower_idx - COUNTER_PAD_LEVELS)
    distal_end = lower_idx - 1
    distal_indices = list(range(distal_start, distal_end + 1))

    proximal_start = upper_idx + 1
    proximal_end = min(
        len(SPINE_LEVELS_BOTTOM_TO_TOP) - 1,
        upper_idx + COUNTER_PAD_LEVELS,
    )
    proximal_indices = list(range(proximal_start, proximal_end + 1))

    if len(distal_indices) == 0:
        raise ValueError(
            f"No vertebrae available below lower Cobb vertebra {lower_cobb} "
            f"for distal counter-pad."
        )

    if len(proximal_indices) == 0:
        raise ValueError(
            f"No vertebrae available above upper Cobb vertebra {upper_cobb} "
            f"for proximal counter-pad."
        )

    return {
        "main": main_indices,
        "distal_counter": distal_indices,
        "proximal_counter": proximal_indices,
    }


# Geometry generation

def compute_bone_angles_deg(
    cobb_deg: float,
    convexity: str,
    upper_cobb: str,
    lower_cobb: str,
) -> List[float]:
    # Each vertebra in the curve gets a tilt. The tilt ramps linearly from
    # +half the Cobb angle at the lower end vertebra to -half at the upper end,
    # which reproduces the measured Cobb angle across the two end plates. Sign
    # flips with convexity so the curve bends the correct way.
    if convexity not in {"Left", "Right"}:
        raise ValueError(f"Convexity must be Left or Right, got: {convexity}")

    upper_idx = LEVEL_TO_INDEX[upper_cobb]
    lower_idx = LEVEL_TO_INDEX[lower_cobb]

    if upper_idx < lower_idx:
        upper_idx, lower_idx = lower_idx, upper_idx

    sign = +1.0 if convexity == "Right" else -1.0
    half_cobb = cobb_deg / 2.0

    angles = [0.0 for _ in SPINE_LEVELS_BOTTOM_TO_TOP]

    for i in range(lower_idx, upper_idx + 1):
        t = (i - lower_idx) / (upper_idx - lower_idx)
        angle = sign * (
            (+half_cobb) * (1.0 - t)
            + (-half_cobb) * t
        )
        angles[i] = angle

    return angles


def generate_spine_nodes(
    bone_angles_deg: List[float],
) -> List[Tuple[int, float, float]]:
    # Walk up from the bottom node, dropping a node at the top of each bone step
    # and each disc step. The sideways shift at each step is tan(tilt) * height,
    # so a tilted vertebra moves the spine off the midline. Discs take the
    # average tilt of the bones either side of them.
    nodes: List[Tuple[int, float, float]] = []

    node_id = 1
    x = X_CENTER_MM
    y = Y_BOTTOM_MM

    nodes.append((node_id, x, y))

    for i, theta_deg in enumerate(bone_angles_deg):
        theta_rad = math.radians(theta_deg)

        dx_bone = math.tan(theta_rad) * BONE_VERTICAL_STEP_MM

        x += dx_bone
        y -= BONE_VERTICAL_STEP_MM

        node_id += 1
        nodes.append((node_id, x, y))

        if i < len(bone_angles_deg) - 1:
            next_theta = bone_angles_deg[i + 1]
            disc_theta = 0.5 * (theta_deg + next_theta)
            disc_theta_rad = math.radians(disc_theta)

            dx_disc = math.tan(disc_theta_rad) * DISC_VERTICAL_STEP_MM

            x += dx_disc
            y -= DISC_VERTICAL_STEP_MM

            node_id += 1
            nodes.append((node_id, x, y))

    return nodes


# Pad placement and forces

def node_coordinate_map(
    nodes: List[Tuple[int, float, float]],
) -> Dict[int, Tuple[float, float]]:
    return {
        node_id: (x, y)
        for node_id, x, y in nodes
    }


def centroid_of_node_ids(
    nodes: List[Tuple[int, float, float]],
    node_ids: List[int],
) -> Tuple[float, float]:
    coord_map = node_coordinate_map(nodes)

    xs = [coord_map[n][0] for n in node_ids]
    ys = [coord_map[n][1] for n in node_ids]

    return sum(xs) / len(xs), sum(ys) / len(ys)


def force_signs_for_convexity(convexity: str) -> Tuple[float, float]:
    # Main pad pushes against the convex side (towards the midline), counters
    # push the other way. The signs swap depending on which way the curve bends.
    if convexity == "Right":
        return -MAIN_APICAL_FORCE_N, +COUNTER_FORCE_N

    if convexity == "Left":
        return +MAIN_APICAL_FORCE_N, -COUNTER_FORCE_N

    raise ValueError(f"Unexpected convexity: {convexity}")


def centroid_side_offset(convexity: str, role: str) -> float:
    convex_side = +1.0 if convexity == "Right" else -1.0

    if role == "main":
        return convex_side * PAD_X_OFFSET_MM

    if role in {"distal_counter", "proximal_counter"}:
        return -convex_side * PAD_X_OFFSET_MM

    raise ValueError(f"Unexpected pad role: {role}")


def build_pad_definitions(
    nodes: List[Tuple[int, float, float]],
    convexity: str,
    upper_cobb: str,
    lower_cobb: str,
) -> List[Dict[str, object]]:
    pad_regions = define_pad_regions(
        upper_cobb=upper_cobb,
        lower_cobb=lower_cobb,
    )

    main_fx, counter_fx = force_signs_for_convexity(convexity)

    # Pad 1 = lower counter, Pad 2 = main, Pad 3 = upper counter.
    pad_specs = [
        (1, "distal_counter", pad_regions["distal_counter"]),
        (2, "main", pad_regions["main"]),
        (3, "proximal_counter", pad_regions["proximal_counter"]),
    ]

    first_centroid_node_id = len(nodes) + 1
    pad_defs: List[Dict[str, object]] = []

    for j, (pad_num, role, level_indices) in enumerate(pad_specs):
        dependent_nodes = nodes_for_level_indices(level_indices)

        cx, cy = centroid_of_node_ids(
            nodes=nodes,
            node_ids=dependent_nodes,
        )

        x_centroid = cx + centroid_side_offset(convexity, role)
        y_centroid = cy

        fx = main_fx if role == "main" else counter_fx

        pad_defs.append(
            {
                "Pad": pad_num,
                "Role": role,
                "Levels": "-".join(level_labels_from_indices(level_indices)),
                "CentroidNode": first_centroid_node_id + j,
                "X": x_centroid,
                "Y": y_centroid,
                "Force_X_N": fx,
                "DependentNodes": dependent_nodes,
                "DependentLevels": level_labels_from_indices(level_indices),
            }
        )

    return pad_defs


# Writing the per-patient CSVs

def fmt(value: float) -> str:
    return f"{value:.3f}"


def write_csv(
    path: Path,
    headers: List[str],
    rows: List[List[object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)


def write_patient_csvs(
    patient_dir: Path,
    patient_label: str,
    nodes: List[Tuple[int, float, float]],
    pad_defs: List[Dict[str, object]],
    metadata: Dict[str, object],
) -> None:
    # Writes the six CSVs the proc builder needs (spine, pad_centroids, the
    # three pad node groups, pad_forces) plus two extras the results script
    # later reads back: pad_regions and model_metadata.
    coord_map = node_coordinate_map(nodes)

    write_csv(
        patient_dir / f"{patient_label}_spine.csv",
        ["Node", "X", "Y"],
        [[nid, fmt(x), fmt(y)] for nid, x, y in nodes],
    )

    write_csv(
        patient_dir / f"{patient_label}_pad_centroids.csv",
        ["Pad", "Node", "X", "Y"],
        [
            [
                pad["Pad"],
                pad["CentroidNode"],
                fmt(float(pad["X"])),
                fmt(float(pad["Y"])),
            ]
            for pad in pad_defs
        ],
    )

    for pad in pad_defs:
        pad_num = int(pad["Pad"])
        dependent_nodes = list(pad["DependentNodes"])

        write_csv(
            patient_dir / f"{patient_label}_pad{pad_num}_nodes.csv",
            ["Node", "X", "Y"],
            [
                [
                    node_id,
                    fmt(coord_map[node_id][0]),
                    fmt(coord_map[node_id][1]),
                ]
                for node_id in dependent_nodes
            ],
        )

    write_csv(
        patient_dir / f"{patient_label}_pad_forces.csv",
        ["Pad", "Role", "Levels", "Force_X_N"],
        [
            [
                pad["Pad"],
                pad["Role"],
                pad["Levels"],
                fmt(float(pad["Force_X_N"])),
            ]
            for pad in pad_defs
        ],
    )

    write_csv(
        patient_dir / f"{patient_label}_pad_regions.csv",
        ["Pad", "Role", "CoveredLevels", "DependentNodes"],
        [
            [
                pad["Pad"],
                pad["Role"],
                ", ".join(pad["DependentLevels"]),
                ", ".join(map(str, pad["DependentNodes"])),
            ]
            for pad in pad_defs
        ],
    )

    metadata_headers = list(metadata.keys())

    write_csv(
        patient_dir / f"{patient_label}_model_metadata.csv",
        metadata_headers,
        [[metadata[key] for key in metadata_headers]],
    )


# Main: loop the clinical rows, keep the single curves, build each model

def main() -> None:
    print("\n" + "=" * 72)
    print("PATIENT-SPECIFIC COBB-END CSV GENERATOR")
    print("=" * 72)

    clinical_rows = read_csv_as_dicts(CLINICAL_CSV)
    convexity_rows = read_csv_as_dicts(CONVEXITY_CSV)

    convexity_map = build_convexity_and_cobb_end_map(convexity_rows)

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    processed_rows: List[List[object]] = []
    skipped_rows: List[List[object]] = []

    for row in clinical_rows:
        pid = clean_patient_id(row.get(PATIENT_ID_COL))

        if pid is None:
            continue

        single_curve = identify_single_curve_patient(row)

        if single_curve is None:
            continue

        region, cobb_deg = single_curve
        patient_label = patient_label_from_number(pid)

        patient_info = convexity_map.get(pid, {})

        convexity = patient_info.get(region)
        upper_cobb = patient_info.get("UpperCobbVertebra")
        lower_cobb = patient_info.get("LowerCobbVertebra")

        if convexity not in {"Left", "Right"}:
            skipped_rows.append(
                [
                    patient_label,
                    region,
                    cobb_deg,
                    convexity,
                    upper_cobb,
                    lower_cobb,
                    "Missing or invalid convexity for this curve region.",
                ]
            )
            continue

        if upper_cobb is None or lower_cobb is None:
            skipped_rows.append(
                [
                    patient_label,
                    region,
                    cobb_deg,
                    convexity,
                    upper_cobb,
                    lower_cobb,
                    "Missing or invalid upper/lower Cobb vertebrae.",
                ]
            )
            continue

        try:
            bone_angles = compute_bone_angles_deg(
                cobb_deg=cobb_deg,
                convexity=convexity,
                upper_cobb=upper_cobb,
                lower_cobb=lower_cobb,
            )

            nodes = generate_spine_nodes(bone_angles)

            pad_defs = build_pad_definitions(
                nodes=nodes,
                convexity=convexity,
                upper_cobb=upper_cobb,
                lower_cobb=lower_cobb,
            )

        except Exception as exc:
            skipped_rows.append(
                [
                    patient_label,
                    region,
                    cobb_deg,
                    convexity,
                    upper_cobb,
                    lower_cobb,
                    f"Model generation error: {exc}",
                ]
            )
            continue

        upper_idx = LEVEL_TO_INDEX[upper_cobb]
        lower_idx = LEVEL_TO_INDEX[lower_cobb]

        if upper_idx < lower_idx:
            upper_cobb, lower_cobb = lower_cobb, upper_cobb
            upper_idx, lower_idx = lower_idx, upper_idx

        main_indices = list(range(lower_idx, upper_idx + 1))
        main_levels = level_labels_from_indices(main_indices)

        # Approximate midpoint of the main loaded curve region.
        apex_idx = main_indices[len(main_indices) // 2]
        model_apex = SPINE_LEVELS_BOTTOM_TO_TOP[apex_idx]

        patient_dir = OUTPUT_ROOT / patient_label

        metadata = {
            "Patient": patient_label,
            "TargetCurveRegion": region,
            "ClinicalPreBraceCobb_deg": cobb_deg,
            "Convexity": convexity,
            "LowerModelCurveEnd": lower_cobb,
            "ModelDefinedApex": model_apex,
            "UpperModelCurveEnd": upper_cobb,
            "MainPadCoveredLevels": ", ".join(main_levels),
            "MainPadForce_N": MAIN_APICAL_FORCE_N,
            "CounterForceEach_N": COUNTER_FORCE_N,
            "PadXOffset_mm": PAD_X_OFFSET_MM,
            "CounterPadLevelsEachSide": COUNTER_PAD_LEVELS,
            "BoneVerticalStep_mm": BONE_VERTICAL_STEP_MM,
            "DiscVerticalStep_mm": DISC_VERTICAL_STEP_MM,
        }

        write_patient_csvs(
            patient_dir=patient_dir,
            patient_label=patient_label,
            nodes=nodes,
            pad_defs=pad_defs,
            metadata=metadata,
        )

        net_fx = sum(float(pad["Force_X_N"]) for pad in pad_defs)

        processed_rows.append(
            [
                patient_label,
                region,
                cobb_deg,
                convexity,
                lower_cobb,
                model_apex,
                upper_cobb,
                ", ".join(main_levels),
                fmt(net_fx),
                str(patient_dir),
            ]
        )

        print(
            f"Created {patient_label}: {region}, {convexity} convex, "
            f"{upper_cobb}-{lower_cobb}, Cobb={cobb_deg:.1f}°"
        )

    write_csv(
        OUTPUT_ROOT / "single_curve_generation_summary.csv",
        [
            "Patient",
            "TargetCurveRegion",
            "PreBraceCobb_deg",
            "Convexity",
            "LowerModelCurveEnd",
            "ModelDefinedApex",
            "UpperModelCurveEnd",
            "MainPadCoveredLevels",
            "NetPadForce_X_N",
            "OutputFolder",
        ],
        processed_rows,
    )

    write_csv(
        OUTPUT_ROOT / "single_curve_generation_skipped.csv",
        [
            "Patient",
            "TargetCurveRegion",
            "PreBraceCobb_deg",
            "Convexity",
            "UpperCobbVertebra",
            "LowerCobbVertebra",
            "Reason",
        ],
        skipped_rows,
    )

    print("\n" + "=" * 72)
    print("CSV GENERATION COMPLETE")
    print("=" * 72)
    print(f"Output folder:      {OUTPUT_ROOT}")
    print(f"Patients processed: {len(processed_rows)}")
    print(f"Patients skipped:   {len(skipped_rows)}")

    if skipped_rows:
        print("\nSkipped patients:")
        for row in skipped_rows:
            print(f"  - {row[0]}: {row[-1]}")

    print("\nDone.")


if __name__ == "__main__":
    main()