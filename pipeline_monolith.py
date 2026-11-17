#!/usr/bin/env python3
"""
local-dicom-copilot: Spec-Driven Pipeline Monolith
Stage 0: Synthetic PHI Poisoning (for deterministic test verification)
Stage 1: HIPAA Safe Harbor 18-Identifier Scrubber + SHA-256 Pixel Provenance
Stage 2: 3D connected components -> work/<study>/02_priors.json + overlay PNGs
Stage 3: FHIR R4 DiagnosticReport, live vLLM or --offline-stub

Heavy imports (numpy, scipy, pillow, pydantic) load inside Stages 2 and 3.
"""

import argparse
import hashlib
import json
import os
import shutil
import urllib.error
import urllib.request
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


def _import_numeric():
    try:
        import numpy as np
        from PIL import Image
        from scipy import ndimage
    except ImportError as exc:
        raise SystemExit(
            "Stage 2 needs numpy, scipy, and pillow. "
            "uv run --with pydicom --with numpy --with scipy --with pillow "
            "--with pydantic python pipeline_monolith.py --study brain_mri --offline-stub"
        ) from exc
    return np, ndimage, Image


def _slice_rows(scrubbed_dir: Path) -> list[dict]:
    paths = sorted(scrubbed_dir.glob("*.dcm"))
    rows = []
    for path in paths:
        ds = pydicom.dcmread(path)
        if "PixelData" not in ds:
            continue
        z_value = None
        if hasattr(ds, "ImagePositionPatient") and len(ds.ImagePositionPatient) >= 3:
            z_value = float(ds.ImagePositionPatient[2])
        instance = int(getattr(ds, "InstanceNumber", 0) or 0)
        pixels = ds.pixel_array.astype("float32")
        slope = float(getattr(ds, "RescaleSlope", 1) or 1)
        intercept = float(getattr(ds, "RescaleIntercept", 0) or 0)
        spacing = [float(v) for v in getattr(ds, "PixelSpacing", [1.0, 1.0])]
        rows.append({
            "path": path,
            "z": z_value if z_value is not None else float(instance),
            "instance": instance,
            "pixels": pixels * slope + intercept,
            "raw_hash": compute_pixel_sha256(ds),
            "spacing": spacing,
            "thickness": float(getattr(ds, "SliceThickness", 0) or 0),
            "modality": str(getattr(ds, "Modality", "OT")),
            "description": str(getattr(ds, "SeriesDescription", "") or ""),
        })
    rows.sort(key=lambda row: (row["z"], row["instance"], row["path"].name))
    return rows


def _candidate_mask(volume, modality: str, row_mm: float, col_mm: float, slice_mm: float):
    np, ndimage, _image = _import_numeric()
    if modality == "CT":
        # Keep slices that show two large lungs, then soft-tissue voxels surrounded by that air.
        lung = np.zeros(volume.shape, dtype=bool)
        for index in range(volume.shape[0]):
            body = volume[index] > -400
            holes = ndimage.binary_fill_holes(body) & (volume[index] < -500)
            labels, count = ndimage.label(holes)
            if count == 0:
                continue
            sizes = np.bincount(labels.ravel())
            large = [label for label in range(1, count + 1) if sizes[label] >= 4000]
            if len(large) < 2:
                continue
            lung[index] = np.isin(labels, large)
        if not lung.any():
            return np.zeros(volume.shape, dtype=bool)
        air_frac = ndimage.uniform_filter(lung.astype(np.float32), size=(1, 7, 7))
        near = ndimage.binary_dilation(lung, structure=np.ones((1, 3, 3), dtype=bool), iterations=2)
        soft = (volume > -200) & (volume < 150) & near & (air_frac > 0.45)
        labels, count = ndimage.label(soft, structure=ndimage.generate_binary_structure(3, 1))
        if count == 0:
            return np.zeros(volume.shape, dtype=bool)
        sizes = np.bincount(labels.ravel())
        keep = np.zeros(count + 1, dtype=bool)
        keep[1:] = (sizes[1:] >= 30) & (sizes[1:] <= 4000)
        return keep[labels]
    positive = volume[volume > 0]
    if positive.size == 0:
        return np.zeros(volume.shape, dtype=bool)
    foreground = volume > np.percentile(positive, 20)
    brain = ndimage.binary_erosion(foreground, structure=np.ones((1, 3, 3), dtype=bool), iterations=6)
    if int(brain.sum()) < 100:
        brain = foreground
    threshold = float(np.percentile(volume[brain], 98.5))
    return brain & (volume >= threshold)


def _window_gray(slice_values, modality: str):
    np, _ndimage, _image = _import_numeric()
    if modality == "CT":
        low, high = -1350.0, 150.0
    else:
        low = float(np.percentile(slice_values, 1))
        high = float(np.percentile(slice_values, 99.5))
    if high <= low:
        high = low + 1
    scaled = (slice_values - low) / (high - low) * 255.0
    return np.clip(scaled, 0, 255).astype(np.uint8)


def _write_overlay(slice_values, component_slice, out_path: Path, modality: str) -> None:
    np, ndimage, image_cls = _import_numeric()
    gray = _window_gray(slice_values, modality)
    rgb = np.stack([gray, gray, gray], axis=-1)
    edge = component_slice & ~ndimage.binary_erosion(component_slice)
    if not edge.any():
        edge = component_slice
    rgb[edge] = (208, 138, 128)
    image_cls.fromarray(rgb, mode="RGB").save(out_path)


def stage_2_components(scrubbed_dir: Path, work_root: Path, study_alias: str) -> dict:
    """Label 3D components and write priors plus one overlay PNG per component."""
    np, ndimage, _image = _import_numeric()
    rows = _slice_rows(scrubbed_dir)
    if len(rows) < 2:
        raise RuntimeError(f"Need at least 2 scrubbed slices in {scrubbed_dir}")

    for stale in work_root.glob("finding_*_overlay.png"):
        stale.unlink()

    modality = rows[0]["modality"]
    volume = np.stack([row["pixels"] for row in rows])
    z_values = [row["z"] for row in rows]
    gaps = [abs(z_values[i + 1] - z_values[i]) for i in range(len(z_values) - 1)]
    gaps = [gap for gap in gaps if gap > 1e-4]
    slice_mm = float(np.median(gaps)) if gaps else (rows[0]["thickness"] or 1.0)
    row_mm, col_mm = rows[0]["spacing"]
    voxel_cm3 = row_mm * col_mm * slice_mm / 1000.0
    mask = _candidate_mask(volume, modality, row_mm, col_mm, slice_mm)
    structure = ndimage.generate_binary_structure(3, 1)
    labels, count = ndimage.label(mask, structure=structure)
    sizes = np.bincount(labels.ravel())
    min_voxels = 40 if modality == "MR" else 25
    order = [int(label) for label in np.argsort(sizes[1:])[::-1] + 1 if int(sizes[label]) >= min_voxels]

    findings = []
    for label in order:
        component = labels == label
        per_slice = component.sum(axis=(1, 2))
        peak = int(np.argmax(per_slice))
        voxels = int(sizes[label])
        finding_id = f"finding_{len(findings) + 1:02d}"
        overlay_name = f"{finding_id}_overlay.png"
        _write_overlay(volume[peak], component[peak], work_root / overlay_name, modality)
        findings.append({
            "id": finding_id,
            "overlay_png": overlay_name,
            "volume_cm3": round(voxels * voxel_cm3, 4),
            "slice_area_mm2": round(float(per_slice[peak]) * row_mm * col_mm, 2),
            "voxel_count": voxels,
            "peak_slice_index": peak + 1,
            "slice_sha256": rows[peak]["raw_hash"],
        })

    priors = {
        "study_alias": study_alias,
        "modality": modality,
        "series_description": rows[0]["description"],
        "slice_count": len(rows),
        "slice_index_base": 1,
        "pixel_spacing_mm": [row_mm, col_mm],
        "slice_spacing_mm": round(slice_mm, 4),
        "min_voxels": min_voxels,
        "components_detected": len(order),
        "slice_files": [row["path"].name for row in rows],
        "findings": findings,
    }
    out_path = work_root / "02_priors.json"
    out_path.write_text(json.dumps(priors, indent=2))
    print(f"[Stage 2] {len(findings)} components ({int(mask.sum())} candidate voxels) -> {out_path}")
    return priors


def _report_models():
    try:
        from pydantic import BaseModel, ConfigDict, Field
    except ImportError as exc:
        raise SystemExit("Stage 3 needs pydantic. uv run --with pydantic python pipeline_monolith.py --offline-stub") from exc

    class TextCode(BaseModel):
        model_config = ConfigDict(extra="ignore")
        text: str

    class Quantity(BaseModel):
        model_config = ConfigDict(extra="ignore")
        value: float
        unit: str

    class Component(BaseModel):
        model_config = ConfigDict(extra="ignore")
        code: TextCode
        valueQuantity: Quantity | None = None
        valueInteger: int | None = None
        valueString: str | None = None

    class Note(BaseModel):
        model_config = ConfigDict(extra="ignore")
        text: str

    class Observation(BaseModel):
        model_config = ConfigDict(extra="ignore")
        resourceType: str = "Observation"
        id: str
        status: str = "preliminary"
        code: TextCode
        note: list[Note] = Field(default_factory=list)
        component: list[Component] = Field(default_factory=list)

    class Identifier(BaseModel):
        model_config = ConfigDict(extra="ignore")
        system: str
        value: str

    class ResultRef(BaseModel):
        model_config = ConfigDict(extra="ignore")
        reference: str

    class DiagnosticReport(BaseModel):
        model_config = ConfigDict(extra="ignore")
        resourceType: str = "DiagnosticReport"
        status: str = "preliminary"
        identifier: list[Identifier]
        code: TextCode
        subject: dict
        conclusion: str
        contained: list[Observation] = Field(default_factory=list)
        result: list[ResultRef] = Field(default_factory=list)

    return DiagnosticReport


def _measurement_sentence(finding: dict, slice_count: int) -> str:
    return (
        f"{finding['id']} measures {finding['volume_cm3']:.2f} cm3 "
        f"({finding['voxel_count']} voxels). "
        f"Peak slice {finding['peak_slice_index']} of {slice_count} "
        f"covers {finding['slice_area_mm2']:.1f} mm2."
    )


def _assemble_report(priors: dict, manifest: dict, notes: dict[str, str], conclusion: str) -> dict:
    diagnostic_report = _report_models()
    contained = []
    result = []
    for finding in priors["findings"]:
        sentence = notes.get(finding["id"]) or _measurement_sentence(finding, priors["slice_count"])
        contained.append({
            "resourceType": "Observation",
            "id": finding["id"],
            "status": "preliminary",
            "code": {"text": finding["id"]},
            "note": [{"text": sentence}],
            "component": [
                {"code": {"text": "Volume"}, "valueQuantity": {"value": finding["volume_cm3"], "unit": "cm3"}},
                {"code": {"text": "Slice area"}, "valueQuantity": {"value": finding["slice_area_mm2"], "unit": "mm2"}},
                {"code": {"text": "Voxel count"}, "valueInteger": finding["voxel_count"]},
                {"code": {"text": "Peak slice"}, "valueInteger": finding["peak_slice_index"]},
                {"code": {"text": "Pixel hash"}, "valueString": finding["slice_sha256"]},
            ],
        })
        result.append({"reference": "#" + finding["id"]})
    if not conclusion:
        if priors["findings"]:
            largest = priors["findings"][0]
            conclusion = (
                f"{len(priors['findings'])} components detected. "
                f"Largest is {largest['id']} at {largest['volume_cm3']:.2f} cm3 "
                f"on slice {largest['peak_slice_index']}."
            )
        else:
            conclusion = "No 3D components were above the size filter."
    report = diagnostic_report.model_validate({
        "resourceType": "DiagnosticReport",
        "status": "preliminary",
        "identifier": [{
            "system": "urn:local-dicom-copilot:series-pixel-sha256",
            "value": manifest["series_pixel_sha256"],
        }],
        "code": {"text": f"{priors['modality']} grounded synthesis"},
        "subject": {"display": manifest["pseudonym_patient_id"]},
        "conclusion": conclusion,
        "contained": contained,
        "result": result,
    })
    return report.model_dump()


def _chat_completions_url(base: str) -> str:
    trimmed = base.rstrip("/")
    if trimmed.endswith("/chat/completions"):
        return trimmed
    if trimmed.endswith("/v1"):
        return trimmed + "/chat/completions"
    return trimmed + "/v1/chat/completions"


def _live_narrative(priors: dict, manifest: dict, vllm_url: str, vllm_model: str) -> tuple[str, dict[str, str]]:
    diagnostic_report = _report_models()
    schema = diagnostic_report.model_json_schema()
    measured = {
        "pseudonym": manifest["pseudonym_patient_id"],
        "series_pixel_sha256": manifest["series_pixel_sha256"],
        "findings": priors["findings"],
    }
    body = {
        "model": vllm_model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Return one FHIR R4 DiagnosticReport. Repeat the supplied measurements exactly. "
                    "Do not invent anatomy, diagnoses, or numbers."
                ),
            },
            {"role": "user", "content": json.dumps(measured)},
        ],
        "guided_json": schema,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "DiagnosticReport", "schema": schema},
        },
    }
    request = urllib.request.Request(
        _chat_completions_url(vllm_url),
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.URLError as exc:
        raise RuntimeError(f"vLLM request failed: {exc}") from exc
    content = payload["choices"][0]["message"]["content"]
    parsed = diagnostic_report.model_validate_json(content) if content.strip().startswith("{") else diagnostic_report.model_validate(json.loads(content))
    notes = {item.id: item.note[0].text for item in parsed.contained if item.note}
    return parsed.conclusion, notes


def stage_3_fhir_synthesis(
    priors: dict,
    manifest: dict,
    work_root: Path,
    *,
    offline_stub: bool,
    vllm_url: str,
    vllm_model: str,
) -> dict:
    """Write a DiagnosticReport. Numbers always come from Stage 2, not the model."""
    source = "offline-stub"
    notes: dict[str, str] = {}
    conclusion = ""
    if not offline_stub:
        if not vllm_url:
            raise RuntimeError("Pass --vllm-url or use --offline-stub")
        print(f"[Stage 3] Requesting structured report from {vllm_url}")
        conclusion, notes = _live_narrative(priors, manifest, vllm_url, vllm_model)
        source = "vllm"
    else:
        print("[Stage 3] Offline stub. Measurements are copied into the FHIR report without a model.")
        if priors["findings"]:
            largest = priors["findings"][0]
            conclusion = (
                f"{len(priors['findings'])} components detected. "
                f"Largest is {largest['id']} at {largest['volume_cm3']:.2f} cm3 "
                f"on slice {largest['peak_slice_index']}."
            )
        else:
            conclusion = "No 3D components were above the size filter."
    report = _assemble_report(priors, manifest, notes, conclusion)
    report_path = work_root / "03_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    (work_root / "03_meta.json").write_text(json.dumps({"narrative_source": source}, indent=2))
    print(f"[Stage 3] {source} -> {report_path}")
    return report


def _clear_generated(work_root: Path) -> None:
    for name in ("00_dirty", "01_scrubbed"):
        path = work_root / name
        if path.exists():
            shutil.rmtree(path)
    for pattern in ("finding_*_overlay.png", "02_priors.json", "03_report.json", "03_meta.json"):
        for path in work_root.glob(pattern):
            path.unlink()


def run_pipeline(
    raw_dir: Path,
    work_root: Path,
    study_alias: str,
    *,
    offline_stub: bool = True,
    vllm_url: str = "",
    vllm_model: str = "local-model",
) -> dict:
    _clear_generated(work_root)
    dirty_dir = work_root / "00_dirty"
    scrubbed_dir = work_root / "01_scrubbed"
    stage_0_poison(raw_dir, dirty_dir)
    manifest = stage_1_scrub_and_hash(dirty_dir, scrubbed_dir, study_alias=study_alias)
    verify_zero_phi_leak(scrubbed_dir)
    priors = stage_2_components(scrubbed_dir, work_root, study_alias)
    report = stage_3_fhir_synthesis(
        priors,
        manifest,
        work_root,
        offline_stub=offline_stub,
        vllm_url=vllm_url,
        vllm_model=vllm_model,
    )
    return {"provenance": manifest, "priors": priors, "report": report}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local-dicom-copilot monolith stages.")
    parser.add_argument("--study", choices=["chest_ct", "brain_mri"], default="brain_mri")
    parser.add_argument("--offline-stub", action="store_true", help="Build the FHIR report from measurements. No model call.")
    parser.add_argument("--vllm-url", default=os.environ.get("VLLM_URL", ""), help="OpenAI-compatible base URL, usually http://host:8000/v1")
    parser.add_argument("--vllm-model", default=os.environ.get("VLLM_MODEL", "local-model"))
    args = parser.parse_args()

    offline_stub = args.offline_stub or not args.vllm_url
    run_pipeline(
        Path(f"data/raw_idc/{args.study}"),
        Path(f"work/{args.study}"),
        args.study,
        offline_stub=offline_stub,
        vllm_url=args.vllm_url,
        vllm_model=args.vllm_model,
    )


if __name__ == "__main__":
    main()
