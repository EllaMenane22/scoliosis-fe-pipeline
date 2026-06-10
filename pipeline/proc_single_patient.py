"""
Builds the Marc Mentat .proc file for each patient from the CSVs that coords.py
generates (spine nodes, pad centroids, the three pad node groups and pad forces).

Run this after coords.py. It scans every patient folder under OUTPUT_ROOT,
checks the six CSVs are there, and drops a PXX_procedure.proc into each folder.
That .proc rebuilds the 2D coronal spine inside Marc: vertebrae as planar beams,
discs as bushings, and the three brace pads applied as RBE3 point loads. Pad
force directions are patient-specific and are already baked into the CSVs by
coords.py, so nothing about loading direction is decided here.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Dict, List, Tuple


# Model constants

VERTEBRAE_ELEM_TYPE = 5      # Marc element type 5: 2-node planar (Bernoulli) beam
DISC_ELEM_TYPE = 194         # type 194: planar bushing / connector element

# Cortical bone modulus
E_BEAM = 1.0e4
NU_BEAM = 0.30

# Disc bushing stiffnesses (baseline values, tuned during validation).
KX_N_PER_MM = 5e5            # lateral / shear stiffness
KY_N_PER_MM = 25e5           # axial stiffness
KRZ_NMM_PER_RAD = 400e6      # bending stiffness about the out-of-plane axis

# Vertebral cross-section.
# The model is planar, so each beam only bends in the coronal plane (side to
# side). What resists that bending is the vertebra's mediolateral width, so
# Marc's in-plane "height" gets set to the width and not the body height. The
# superior-inferior body height is the beam *length* instead, and that comes
# from the node spacing in coords.py. Area is width x depth; there's no AP
# measurement in the dataset yet so depth is taken equal to width.
AVG_WIDTH = 33.08
AVG_DEPTH = AVG_WIDTH
AVG_AREA = AVG_WIDTH * AVG_DEPTH


# Paths. Points at the folder that holds the patient subfolders (P08, P14, ...).
# Every subfolder is processed automatically, so there's nothing to type in.

OUTPUT_ROOT = Path(
    r"C:\Users\<username>\OneDrive - Imperial College London\Year 4\FYP\Patient_1_final\Automated models\Updated automated"
)

# A folder only counts as a valid patient if all six of these CSVs exist.
REQUIRED_SUFFIXES = [
    "_spine.csv",
    "_pad_centroids.csv",
    "_pad1_nodes.csv",
    "_pad2_nodes.csv",
    "_pad3_nodes.csv",
    "_pad_forces.csv",
]


def find_patient_folders(root: Path):
    """Return [(patient_id, folder_path), ...] for every folder that has all six CSVs."""
    found = []

    if not root.exists():
        raise FileNotFoundError(f"Output root not found: {root}")

    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        pid = folder.name

        ok = all((folder / f"{pid}{suf}").exists() for suf in REQUIRED_SUFFIXES)

        if ok:
            found.append((pid, folder))
        else:
            # Tell me which files are missing rather than silently skipping.
            missing = [
                suf for suf in REQUIRED_SUFFIXES
                if not (folder / f"{pid}{suf}").exists()
            ]
            print(f"  [{pid}] skipped - missing: {missing}")

    return found


# CSV readers.
# utf-8-sig handles the BOM Excel sometimes writes, and headers are stripped
# because trailing spaces in column names are easy to introduce by accident.

def read_spine_nodes(filepath: str) -> List[Tuple[float, float, float]]:
    pts: List[Tuple[float, float, float]] = []

    with open(filepath, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        if reader.fieldnames is None:
            raise ValueError(f"No header row found in {filepath}")

        reader.fieldnames = [h.strip() for h in reader.fieldnames]

        for row in reader:
            clean = {k.strip(): str(v).strip() for k, v in row.items()}
            # Y is negated: image coordinates run downwards, so flipping the sign
            # puts the spine the right way up in Marc. Z stays 0 (planar model).
            pts.append((float(clean["X"]), -float(clean["Y"]), 0.0))

    return pts


def read_centroids(
    filepath: str,
) -> Tuple[Dict[int, int], Dict[int, Tuple[float, float, float]]]:
    centroid_map: Dict[int, int] = {}
    centroid_coords: Dict[int, Tuple[float, float, float]] = {}

    with open(filepath, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        if reader.fieldnames is None:
            raise ValueError(f"No header row found in {filepath}")

        reader.fieldnames = [h.strip() for h in reader.fieldnames]

        for row in reader:
            clean = {k.strip(): str(v).strip() for k, v in row.items()}

            pad = int(clean["Pad"])
            node = int(clean["Node"])
            x = float(clean["X"])
            y = -float(clean["Y"])     # same Y flip as the spine nodes

            centroid_map[pad] = node
            centroid_coords[pad] = (x, y, 0.0)

    return centroid_map, centroid_coords


def read_pad_nodes(filepath: str) -> List[int]:
    # The spine nodes that each pad pushes on. Sorted so the RBE3 connectivity
    # comes out in a predictable order.
    indices: List[int] = []

    with open(filepath, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        if reader.fieldnames is None:
            raise ValueError(f"No header row found in {filepath}")

        reader.fieldnames = [h.strip() for h in reader.fieldnames]

        for row in reader:
            clean = {k.strip(): str(v).strip() for k, v in row.items()}
            indices.append(int(clean["Node"]))

    return sorted(indices)


def read_pad_forces(filepath: str) -> Dict[int, float]:
    # Lateral (x) force per pad. Sign carries the direction, set by coords.py.
    forces: Dict[int, float] = {}

    with open(filepath, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        if reader.fieldnames is None:
            raise ValueError(f"No header row found in {filepath}")

        reader.fieldnames = [h.strip() for h in reader.fieldnames]

        for row in reader:
            clean = {k.strip(): str(v).strip() for k, v in row.items()}

            pad = int(clean["Pad"])
            force_x = float(clean["Force_X_N"])

            forces[pad] = force_x

    return forces


# Main builder: one patient in, one .proc out.

def create_proc(patient_id: str, folder: Path) -> None:
    folder_str = str(folder)

    CSV_SPINE = os.path.join(folder_str, f"{patient_id}_spine.csv")
    CSV_CENTROIDS = os.path.join(folder_str, f"{patient_id}_pad_centroids.csv")
    CSV_PAD1 = os.path.join(folder_str, f"{patient_id}_pad1_nodes.csv")
    CSV_PAD2 = os.path.join(folder_str, f"{patient_id}_pad2_nodes.csv")
    CSV_PAD3 = os.path.join(folder_str, f"{patient_id}_pad3_nodes.csv")
    CSV_FORCES = os.path.join(folder_str, f"{patient_id}_pad_forces.csv")
    PROC_NAME = os.path.join(folder_str, f"{patient_id}_procedure.proc")

    spine_pts = read_spine_nodes(CSV_SPINE)
    centroid_map, centroid_coords = read_centroids(CSV_CENTROIDS)
    pad_forces = read_pad_forces(CSV_FORCES)

    pad_node_files = {
        1: CSV_PAD1,
        2: CSV_PAD2,
        3: CSV_PAD3,
    }

    dep_nodes: Dict[int, List[int]] = {}

    for pad_num in sorted(centroid_map.keys()):
        dep_nodes[pad_num] = read_pad_nodes(pad_node_files[pad_num])

    n_spine_nodes = len(spine_pts)
    n_elements = n_spine_nodes - 1

    sorted_pads = sorted(centroid_map.keys())

    # The pad centroid nodes are numbered straight after the spine nodes, so a
    # spine of N nodes makes the first pad centroid node N+1, the next N+2, etc.
    marc_centroid_ids: Dict[int, int] = {}

    for i, pad_num in enumerate(sorted_pads):
        marc_centroid_ids[pad_num] = n_spine_nodes + 1 + i

    net_force = sum(pad_forces.values())

    print("\n" + "=" * 72)
    print("AUTOMATED PROC BUILD")
    print("=" * 72)
    print(f"Patient:                 {patient_id}")
    print(f"Spine nodes:             {n_spine_nodes}")
    print(f"Spine elements:          {n_elements}")
    print(f"Pad centroid Marc IDs:   {marc_centroid_ids}")
    print(f"Pad forces X [N]:        {pad_forces}")
    print(f"Net lateral force [N]:   {net_force:.3f}")

    # Sanity check: the three pad forces should balance to ~0 N laterally. If
    # they don't, the model would drift sideways instead of just deforming.
    if abs(net_force) > 1e-6:
        print("WARNING: Net lateral pad force is not zero.")
    else:
        print("Force equilibrium check PASSED: net lateral pad force = 0 N.")

    # Elements alternate vertebra, disc, vertebra, disc... down the chain, so
    # the odd-numbered ones are bone and the even-numbered ones are discs.
    bone_eids = [
        i for i in range(1, n_elements + 1)
        if i % 2 != 0
    ]

    disc_eids = [
        i for i in range(1, n_elements + 1)
        if i % 2 == 0
    ]

    bone_str = " ".join(map(str, bone_eids))
    disc_str = " ".join(map(str, disc_eids))

    cmds: List[str] = []

    # Fresh planar model, units forced to mm.
    cmds += [
        "*new_model",
        "*prog_option units:target_unit_length:millimeter",
        "*prog_option units:target_has_units:no",
        "*prog_option units:target_has_units:yes",
        "*define_model_unit_system",
        "*prog_option units:target_has_units:no",
        "*set_model_analysis_dimension planar",
    ]

    # Nodes: all the spine nodes first, then the three pad centroid nodes.
    cmds.append("*add_nodes")

    for p in spine_pts:
        cmds.append(f"{p[0]} {p[1]} {p[2]}")

    for pad_num in sorted_pads:
        c = centroid_coords[pad_num]
        cmds.append(f"{c[0]} {c[1]} {c[2]}")

    cmds.append("#")

    # Join consecutive spine nodes into 2-node line elements.
    cmds += [
        "*set_element_class line2",
        "*add_elements",
    ]

    for i in range(1, n_spine_nodes):
        cmds.append(f"{i} {i + 1}")

    cmds.append("#")

    # Reassign element types: bone elements to beam, disc elements to bushing.
    cmds += [
        "*select_clear",
        f"*select_elements {bone_str} #",
        f"*change_element_type {VERTEBRAE_ELEM_TYPE}",
        "selected",
        "#",
        "*select_clear",
        f"*select_elements {disc_str} #",
        f"*change_element_type {DISC_ELEM_TYPE}",
        "selected",
        "#",
    ]

    # Cortical bone material on the vertebra beams.
    cmds += [
        "*new_mater standard",
        "*mater_name cortical_bone",
        f"*mater_param structural:youngs_modulus {E_BEAM}",
        f"*mater_param structural:poissons_ratio {NU_BEAM}",
        f"*add_mater_elements {bone_str} #",
    ]

    # Beam section. In-plane "height" is the mediolateral width (see top note),
    # out-of-plane "width" is the AP depth, here equal to width.
    cmds += [
        "*new_geometry",
        "*geometry_name geom_vertebrae",
        "*geometry_type mech_planar_beam_str",
        f"*geometry_param height {AVG_WIDTH}",
        f"*geometry_param width {AVG_DEPTH}",
        f"*geometry_param area {AVG_AREA}",
        f"*add_geometry_elements {bone_str} #",
    ]

    # Disc bushings, using the stiffnesses defined at the top.
    cmds += [
        "*new_geometry",
        "*geometry_name geom_discs",
        "*geometry_type mech_planar_bushing",
        "*edit_geometry geom_discs",
        "*geometry_option stiffness:value",
        f"*geometry_param stiffness_x {KX_N_PER_MM}",
        f"*geometry_param stiffness_y {KY_N_PER_MM}",
        f"*geometry_param stiffness_rz {KRZ_NMM_PER_RAD}",
        f"*add_geometry_elements {disc_str} #",
    ]

    # RBE3 per pad: the centroid node is the reference, and the load it carries
    # is spread across the spine nodes sitting under that pad. rz is switched
    # off on every connection so the pad pushes laterally without forcing a
    # rotation through the tie.
    rbe3_count = 1

    for pad_num in sorted_pads:
        ref_node = marc_centroid_ids[pad_num]
        deps = [
            n for n in dep_nodes.get(pad_num, [])
            if n <= n_spine_nodes
        ]

        if not deps:
            print(f"WARNING: Pad {pad_num} has no valid dependent nodes.")
            continue

        cmds += [
            "*new_rbe3",
            f"*rbe3_ref_node {ref_node}",
            "*set_rbe3_ref_dof x on",
            "*set_rbe3_ref_dof y on",
            "*set_rbe3_ref_dof rz on",
            "*set_rbe3_ret_dof_def x",
            "*set_rbe3_ret_dof_def y",
            "*add_rbe3_conn_nodes_ac structural",
        ]

        cmds.append(" ".join(map(str, deps)) + " #")
        cmds.append(f"*edit_rbe3 rbe3_{rbe3_count}")

        # Equal weight on every spine node under the pad, no rotational tie.
        for idx in range(len(deps)):
            cmds.append(f"*set_rbe3_conn_coef {idx + 1} 1")
            cmds.append(f"*set_rbe3_conn_dof {idx + 1} rz off")

        rbe3_count += 1

    # Boundary conditions. Node 1 (base) is pinned fully. The top node is held
    # in x and rotation but left free to slide vertically, so the spine can
    # straighten without being artificially stretched or compressed.
    cmds += [
        "*new_apply",
        "*apply_name base_fix",
        "*apply_type fixed_displacement",
        "*apply_dof x",
        "*apply_value x 0.0",
        "*apply_dof y",
        "*apply_value y 0.0",
        "*apply_dof rz",
        "*apply_value rz 0.0",
        "*add_apply_nodes 1 #",
        "*apply_apply",

        "*new_apply",
        "*apply_name top_fix",
        "*apply_type fixed_displacement",
        "*apply_dof x",
        "*apply_value x 0.0",
        "*apply_dof rz",
        "*apply_value rz 0.0",
        f"*add_apply_nodes {n_spine_nodes} #",
        "*apply_apply",
    ]

    # Linear ramp from 0 to 1 used to scale the load over the step.
    cmds += [
        "*new_md_table 1 1",
        "*set_md_table_type 1 normlzd_time",
        "*table_add 0,0 1,1 #",
    ]

    # Apply each pad's lateral force at its centroid node, scaled by the ramp.
    for pad_num in sorted_pads:
        ref_node = marc_centroid_ids[pad_num]
        force = pad_forces.get(pad_num, 0.0)

        cmds += [
            "*new_apply",
            f"*apply_name pad{pad_num}_force",
            "*apply_type point_load",
            "*apply_dof x",
            "*apply_dof_table x table1",
            f"*add_apply_nodes {ref_node} #",
            "*apply_apply",
            f"*edit_apply pad{pad_num}_force",
            f"*apply_dof_value x {force}",
        ]

    # Static loadcase and job, then fit the view ready to write the .dat.
    cmds += [
        "*new_loadcase",
        "*loadcase_type struc:static",
        "*loadcase_value nsteps 100",
        "*edit_loadcase lcase1",
        "*new_job structural",
        "*edit_job job1",
        "*job_option elastic:on",
        "*job_option nod_dof:on",
        "*add_job_loadcases lcase1 #",
        "*edit_job job1",
        "*select_clear",
        "*fill_view",
        "*redraw",
    ]

    with open(PROC_NAME, "w", encoding="utf-8") as f:
        f.write("\n".join(cmds))

    print("\n✓ Proc file written:")
    print(f"  {PROC_NAME}")
    print("=" * 72)


if __name__ == "__main__":
    patients = find_patient_folders(OUTPUT_ROOT)

    print("\nFound {} patient folder(s).\n".format(len(patients)))

    ok = 0

    for pid, folder in patients:
        try:
            create_proc(pid, folder)
            ok += 1
        except Exception as exc:
            print("  [{}] ERROR: {}".format(pid, exc))

    print("\nProc files written: {}/{}".format(ok, len(patients)))
