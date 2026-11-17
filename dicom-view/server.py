#!/usr/bin/env python3
"""Serve the 3-pane viewer and run the pipeline when a study is loaded or uploaded.

uv run --with fastapi --with uvicorn --with python-multipart --with pydicom \
    --with numpy --with scipy --with pillow --with pydantic \
    python dicom-view/server.py
"""

import json
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline_monolith import run_pipeline  # noqa: E402

STUDIES = {
    "brain_mri": ROOT / "data" / "raw_idc" / "brain_mri",
    "chest_ct": ROOT / "data" / "raw_idc" / "chest_ct",
}
LOCK = threading.Lock()
app = FastAPI()


def _flags() -> dict:
    import os
    url = os.environ.get("VLLM_URL", "")
    offline = os.environ.get("OFFLINE_STUB", "1" if not url else "0") == "1" or not url
    return {
        "offline_stub": offline,
        "vllm_url": url,
        "vllm_model": os.environ.get("VLLM_MODEL", "local-model"),
    }


def _ready(work: Path) -> bool:
    return (work / "02_priors.json").exists() and (work / "03_report.json").exists()


def _payload(study: str, work: Path) -> dict:
    priors = json.loads((work / "02_priors.json").read_text())
    report = json.loads((work / "03_report.json").read_text())
    provenance = json.loads((work / "01_scrubbed" / "provenance_manifest.json").read_text())
    meta_path = work / "03_meta.json"
    source = json.loads(meta_path.read_text())["narrative_source"] if meta_path.exists() else "offline-stub"
    notes = {
        item["id"]: (item.get("note") or [{}])[0].get("text", "")
        for item in report.get("contained", [])
    }
    findings = []
    for raw in priors["findings"]:
        item = dict(raw)
        item["overlay_url"] = f"/work/{study}/{raw['overlay_png']}"
        item["note"] = notes.get(raw["id"], "")
        findings.append(item)
    return {
        "study_alias": study,
        "pseudonym_patient_id": provenance["pseudonym_patient_id"],
        "modality": priors["modality"],
        "label": priors.get("series_description") or study,
        "identity_removed": "YES",
        "series_pixel_sha256": provenance["series_pixel_sha256"],
        "slice_count": priors["slice_count"],
        "slices": [f"/work/{study}/01_scrubbed/{name}" for name in priors["slice_files"]],
        "findings": findings,
        "report": report,
        "narrative_source": source,
    }


def _run(raw_dir: Path, work: Path, study: str) -> dict:
    with LOCK:
        try:
            run_pipeline(raw_dir, work, study, **_flags())
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _payload(study, work)


@app.get("/")
def index():
    return FileResponse(ROOT / "dicom-view" / "index.html", headers={"Cache-Control": "no-cache"})


@app.post("/api/studies/{study}/load")
def load_study(study: str, force: bool = False):
    raw_dir = STUDIES.get(study)
    if raw_dir is None:
        raise HTTPException(status_code=404, detail="Study must be brain_mri or chest_ct")
    work = ROOT / "work" / study
    if force or not _ready(work):
        return _run(raw_dir, work, study)
    return _payload(study, work)


@app.post("/api/upload")
def upload(files: list[UploadFile] = File(...)):
    inbox = ROOT / "work" / "upload" / "inbox"
    if inbox.exists():
        for old in inbox.glob("*"):
            if old.is_file():
                old.unlink()
    inbox.mkdir(parents=True, exist_ok=True)
    saved = 0
    for item in files:
        data = item.file.read()
        if len(data) < 132 or data[128:132] != b"DICM":
            continue
        saved += 1
        (inbox / f"slice_{saved:04d}.dcm").write_bytes(data)
    if saved == 0:
        raise HTTPException(status_code=400, detail="No DICOM files were in that upload.")
    return _run(inbox, ROOT / "work" / "upload", "upload")


@app.get("/work/{path:path}")
def work_file(path: str):
    target = (ROOT / "work" / path).resolve()
    root = (ROOT / "work").resolve()
    if root not in target.parents and target != root:
        raise HTTPException(status_code=404, detail="Not found")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(target, headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)
