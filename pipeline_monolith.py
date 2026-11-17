#!/usr/bin/env python3
"""
local-dicom-copilot: Spec-Driven Pipeline Monolith
Stage 0: Synthetic PHI Poisoning (for deterministic test verification)
Stage 1: HIPAA Safe Harbor 18-Identifier Scrubber + SHA-256 Pixel Provenance
"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import pydicom
from pydicom.tag import Tag
from pydicom.uid import generate_uid

# ---------------------------------------------------------------------------
# SPEC: Synthetic PHI injected during Stage 0 to prove Stage 1 works
# ---------------------------------------------------------------------------
SYNTHETIC_PHI_FIXTURE = {
    "PatientName": "DOE^JOHN^WILLIAM",
    "PatientID": "MRN-99482710",
    "PatientBirthDate": "19740819",
    "PatientAddress": "742 Evergreen Terrace, Springfield",
    "PatientTelephoneNumbers": "555-019-2834",
    "InstitutionName": "St. Jude Memorial Diagnostic Center",
    "InstitutionAddress": "100 Hospital Way, Springfield",
    "ReferringPhysicianName": "HOUSE^GREGORY^MD",
    "PerformingPhysicianName": "CUDDY^LISA^MD",
    "OperatorsName": "TECH^CHASE^R",
    "DeviceSerialNumber": "SN-GE-8849201-X",
    "StudyDate": "20260314",
    "SeriesDate": "20260314",
    "AcquisitionDate": "20260314",
    "ContentDate": "20260314",
    "AccessionNumber": "ACC-2026-88412",
}

# ---------------------------------------------------------------------------
# SPEC: HIPAA Safe Harbor (45 CFR § 164.514(b)(2)) DICOM Tag Policy
# Action "REMOVE": delete tag completely
# Action "BLANK" : retain required Type 2 DICOM tag with empty value ""
# Action "YEAR_ONLY": keep YYYY0101 if date is needed, or blank
# ---------------------------------------------------------------------------
HIPAA_TAG_POLICY = {
    # 1. Names
    "PatientName": "BLANK",
    "ReferringPhysicianName": "BLANK",
    "PerformingPhysicianName": "REMOVE",
    "OperatorsName": "REMOVE",
    "OtherPatientNames": "REMOVE",
    # 2. Geographic subdivisions smaller than a state
    "PatientAddress": "REMOVE",
    "InstitutionAddress": "REMOVE",
    "RegionOfResidence": "REMOVE",
    # 3. Dates (except year)
    "PatientBirthDate": "BLANK",
    "StudyDate": "BLANK",
    "SeriesDate": "REMOVE",
    "AcquisitionDate": "REMOVE",
    "ContentDate": "REMOVE",
    "StudyTime": "BLANK",
    "SeriesTime": "REMOVE",
    "AcquisitionTime": "REMOVE",
    "ContentTime": "REMOVE",
    # 4-7. Phone, Fax, Email, SSN
    "PatientTelephoneNumbers": "REMOVE",
    "PatientTelecomInformation": "REMOVE",
    # 8-10. Medical Record Numbers, Health Plan IDs, Account Numbers
    "PatientID": "PSEUDONYM",
    "OtherPatientIDs": "REMOVE",
    "IssuerOfPatientID": "REMOVE",
    "AccessionNumber": "BLANK",
    # 13-14. Device identifiers & serial numbers
    "DeviceSerialNumber": "REMOVE",
    "StationName": "REMOVE",
    "InstitutionName": "REMOVE",
    "InstitutionalDepartmentName": "REMOVE",
}


def compute_pixel_sha256(ds: pydicom.Dataset) -> str:
    """Cryptographic fingerprint of raw uncompressed pixel bytes."""
    return hashlib.sha256(ds.PixelData).hexdigest()


# ===========================================================================
# STAGE 0: Inject Synthetic PHI into a Working Copy
# ===========================================================================
def stage_0_poison(raw_dir: Path, dirty_dir: Path) -> list[Path]:
    dirty_dir.mkdir(parents=True, exist_ok=True)
    raw_files = sorted(raw_dir.rglob("*.dcm"))
    if not raw_files:
        raise FileNotFoundError(f"No .dcm files found in {raw_dir}")

    out_paths = []
    for idx, src_path in enumerate(raw_files):
        ds = pydicom.dcmread(src_path)
        for keyword, fake_val in SYNTHETIC_PHI_FIXTURE.items():
            setattr(ds, keyword, fake_val)

        # Inject a private vendor tag (odd group number 0x0009)
        ds.add_new(Tag(0x0009, 0x1010), "LO", "INTERNAL_OPERATOR_BADGE_4412")
        ds.PatientIdentityRemoved = "NO"

        dst_path = dirty_dir / f"slice_{idx:04d}.dcm"
        ds.save_as(dst_path)
        out_paths.append(dst_path)

    print(f"[Stage 0] Poisoned {len(out_paths)} slices with synthetic PHI -> {dirty_dir}")
    return out_paths


# ===========================================================================
# STAGE 1: HIPAA Safe Harbor De-identification + SHA-256 Provenance
# ===========================================================================
def stage_1_scrub_and_hash(dirty_dir: Path, scrubbed_dir: Path, study_alias: str) -> dict:
    scrubbed_dir.mkdir(parents=True, exist_ok=True)
    dirty_files = sorted(dirty_dir.glob("*.dcm"))
    if not dirty_files:
        raise FileNotFoundError(f"No dirty .dcm files found in {dirty_dir}")

    # Deterministic UID remapping per study so OHIF/PACS still groups slices together
    clean_study_uid = generate_uid(entropy_srcs=[study_alias, "study"])
    clean_series_uid = generate_uid(entropy_srcs=[study_alias, "series"])
    pseudonym_id = f"ANON-{hashlib.sha256(study_alias.encode()).hexdigest()[:12].upper()}"

    slice_records = []
    for idx, src_path in enumerate(dirty_files):
        ds = pydicom.dcmread(src_path)

        # 1. Hash raw pixels BEFORE touching headers (proves pixel buffer stays untouched)
        pre_hash = compute_pixel_sha256(ds)

        # 2. Apply HIPAA Safe Harbor tag policy
        for keyword, action in HIPAA_TAG_POLICY.items():
            if keyword not in ds:
                continue
            if action == "REMOVE":
                del ds[keyword]
            elif action == "BLANK":
                ds[keyword].value = ""
            elif action == "PSEUDONYM":
                ds[keyword].value = pseudonym_id

        # 3. Strip ALL private tags (odd group numbers per DICOM PS3.5 Section 7.8)
        ds.remove_private_tags()

        # 4. Remap Study/Seres/SOP UIDs so original hospital UIDs cannot be cross-linked
        ds.StudyInstanceUID = clean_study_uid
        ds.SeriesInstanceUID = clean_series_uid
        ds.SOPInstanceUID = generate_uid(entropy_srcs=[study_alias, f"sop-{idx}"])
        if hasattr(ds, "file_meta") and hasattr(ds.file_meta, "MediaStorageSOPInstanceUID"):
            ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID

        # 5. Stamp official DICOM De-identification audit tags
        ds.PatientIdentityRemoved = "YES"
        ds.DeidentificationMethod = "HIPAA-Safe-Harbor-18-Identifiers-v1"

        # 6. Verify pixel integrity post-scrub
        post_hash = compute_pixel_sha256(ds)
        assert pre_hash == post_hash, f"Pixel buffer altered during scrub on {src_path}!"

        dst_path = scrubbed_dir / f"clean_{idx:04d}.dcm"
        ds.save_as(dst_path)

        slice_records.append({
            "slice_index": idx,
            "file": dst_path.name,
            "sop_instance_uid": str(ds.SOPInstanceUID),
            "pixel_sha256": post_hash,
            "z_position": float(ds.ImagePositionPatient[2]) if hasattr(ds, "ImagePositionPatient") else None,
        })

    # Compute whole-series Merkle-style root hash over all ordered slice pixel hashes
    series_pixel_sha256 = hashlib.sha256(
        "".join(r["pixel_sha256"] for r in slice_records).encode()
    ).hexdigest()

    manifest = {
        "study_alias": study_alias,
        "pseudonym_patient_id": pseudonym_id,
        "clean_study_instance_uid": str(clean_study_uid),
        "clean_series_instance_uid": str(clean_series_uid),
        "scrubbed_at_utc": datetime.now(timezone.utc).isoformat(),
        "slice_count": len(slice_records),
        "series_pixel_sha256": series_pixel_sha256,
        "slices": slice_records,
    }

    manifest_path = scrubbed_dir / "provenance_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"[Stage 1] Scrubbed {len(slice_records)} slices -> {scrubbed_dir}")
    print(f"[Stage 1] Series Pixel SHA-256 : {series_pixel_sha256}")
    return manifest


# ===========================================================================
# VERIFICATION GATE: Assert Zero Synthetic PHI Leakage
# ===========================================================================
def verify_zero_phi_leak(scrubbed_dir: Path) -> None:
    forbidden_strings = [v for v in SYNTHETIC_PHI_FIXTURE.values() if len(v) >= 4]
    forbidden_strings.append("INTERNAL_OPERATOR_BADGE_4412")

    scrubbed_files = sorted(scrubbed_dir.glob("*.dcm"))
    for path in scrubbed_files:
        ds = pydicom.dcmread(path)
        header_dump = str(ds)
        for secret in forbidden_strings:
            if secret in header_dump:
                raise AssertionError(f"PHI LEAK DETECTED in {path.name}: found '{secret}'")

        assert getattr(ds, "PatientIdentityRemoved", None) == "YES"
        assert len([elem for elem in ds if elem.tag.is_private]) == 0, "Private tag survived!"

    print(f"[Verify ] PASSED: 0 PHI leaks across all {len(scrubbed_files)} scrubbed DICOM files.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local-dicom-copilot monolith stages.")
    parser.add_argument("--study", choices=["chest_ct", "brain_mri"], default="brain_mri")
    args = parser.parse_args()

    raw_dir = Path(f"data/raw_idc/{args.study}")
    work_root = Path(f"work/{args.study}")
    dirty_dir = work_root / "00_dirty"
    scrubbed_dir = work_root / "01_scrubbed"

    stage_0_poison(raw_dir, dirty_dir)
    stage_1_scrub_and_hash(dirty_dir, scrubbed_dir, study_alias=args.study)
    verify_zero_phi_leak(scrubbed_dir)


if __name__ == "__main__":
    main()
