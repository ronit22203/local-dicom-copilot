#!/usr/bin/env python3
"""
Deterministic sample data fetcher from NCI Imaging Data Commons (IDC).
Pulls one Chest CT series (LIDC-IDRI) and one Brain MRI series (UPENN-GBM)
in native DICOM (.dcm) format without requiring credentials.
"""

import argparse
import json
from pathlib import Path
from idc_index import IDCClient


DATASETS = {
    "chest_ct": {
        "collection_id": "lidc_idri",
        "description": "Thoracic CT with lung nodule ground-truth annotations",
        "sql": """
            SELECT
                PatientID,
                StudyInstanceUID,
                SeriesInstanceUID,
                Modality,
                SeriesDescription,
                instanceCount,
                ROUND(series_size_MB, 2) AS size_mb
            FROM index
            WHERE collection_id = 'lidc_idri'
              AND Modality = 'CT'
              AND instanceCount BETWEEN 110 AND 180
            ORDER BY SeriesInstanceUID ASC
            LIMIT 1
        """,
    },
    "brain_mri": {
        "collection_id": "upenn_gbm",
        "description": "Multi-parametric Brain MRI (T1-Gd) for BraTS-style segmentation",
        "sql": """
            SELECT
                PatientID,
                StudyInstanceUID,
                SeriesInstanceUID,
                Modality,
                SeriesDescription,
                instanceCount,
                ROUND(series_size_MB, 2) AS size_mb
            FROM index
            WHERE collection_id = 'upenn_gbm'
              AND Modality = 'MR'
              AND LOWER(SeriesDescription) LIKE '%t1%'
              AND (
                    LOWER(SeriesDescription) LIKE '%post%'
                    OR LOWER(SeriesDescription) LIKE '%gad%'
                    OR LOWER(SeriesDescription) LIKE '%gd%'
                    OR SeriesDescription LIKE '%C+%'
                  )
              AND LOWER(SeriesDescription) NOT LIKE '%pre%'
              AND LOWER(SeriesDescription) NOT LIKE '%tera%'
              AND LOWER(SeriesDescription) NOT LIKE '%repeat%'
              AND LOWER(SeriesDescription) NOT LIKE '%flair%'
              AND LOWER(SeriesDescription) NOT LIKE '%t2%'
              AND instanceCount BETWEEN 120 AND 220
            ORDER BY SeriesInstanceUID ASC
            LIMIT 1
        """,
    },
}


def fetch_dataset(client: IDCClient, key: str, base_dir: Path, force: bool = False) -> dict:
    spec = DATASETS[key]
    target_dir = base_dir / key
    target_dir.mkdir(parents=True, exist_ok=True)

    df = client.sql_query(spec["sql"])
    if df.empty:
        raise RuntimeError(f"No matching IDC series found for '{key}'")

    row = df.iloc[0].to_dict()
    series_uid = row["SeriesInstanceUID"]

    existing_dcms = list(target_dir.rglob("*.dcm"))
    if existing_dcms and not force:
        print(f"[{key}] Found {len(existing_dcms)} existing .dcm files in {target_dir}. Skipping download.")
    else:
        print(f"[{key}] Downloading {row['instanceCount']} slices ({row['size_mb']} MB) -> {target_dir}")
        client.download_dicom_series(
            seriesInstanceUID=series_uid,
            downloadDir=str(target_dir),
        )
        existing_dcms = list(target_dir.rglob("*.dcm"))

    return {
        "dataset_key": key,
        "collection_id": spec["collection_id"],
        "description": spec["description"],
        "patient_id": row["PatientID"],
        "study_instance_uid": row["StudyInstanceUID"],
        "series_instance_uid": series_uid,
        "modality": row["Modality"],
        "series_description": row["SeriesDescription"],
        "downloaded_slices": len(existing_dcms),
        "local_dir": str(target_dir),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch gold-standard DICOM sample studies from NCI IDC.")
    parser.add_argument(
        "--dataset",
        choices=["chest_ct", "brain_mri", "all"],
        default="all",
        help="Which reference study to download (default: all)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/raw_idc"),
        help="Destination directory for downloaded DICOM series",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if .dcm files already exist locally",
    )
    args = parser.parse_args()

    client = IDCClient.client()
    targets = list(DATASETS.keys()) if args.dataset == "all" else [args.dataset]

    manifest = []
    for key in targets:
        meta = fetch_dataset(client, key, args.out_dir, force=args.force)
        manifest.append(meta)

    manifest_path = args.out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\nSaved dataset manifest -> {manifest_path}")


if __name__ == "__main__":
    main()
