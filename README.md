# local-dicom-copilot

A local-first pipeline that takes raw hospital scans (`.dcm`), strips out patient privacy info, measures abnormalities in 3D using exact physical math, and generates a structured JSON medical report—without sending data to external APIs.

---

## The Problem This Solves
If you throw a raw medical scan straight at a Vision-Language Model (like Qwen-VL or MedGemma), two things go wrong:
1. **Privacy Leak:** Medical scan files carry patient names, birthdates, and hospital IDs hidden inside their binary headers.
2. **Hallucinated Measurements:** A VLM looking at a flat 2D image has no idea how big a pixel is in the real world—it will confidently guess that a 1.2 cm³ tumor is 4.5 cm³.

**The Fix:** Do the privacy stripping and 3D physical math *deterministically* in Python first, then hand the VLM the single best 2D image slice alongside the exact pre-calculated numbers.

---

## How Data Flows (Step by Step)

```text
[Raw .dcm Slices]
       │
       ▼
1. Orthanc (Local Storage Server / "Inbox")
       │  Triggers webhook when a full scan finishes uploading
       ▼
2. Scrubber (pydicom)
       │  Wipes 18 HIPAA personal identifiers + creates SHA-256 pixel hash
       ▼
3. 3D Math & Segmentation (MONAI)
       │  Stacks 2D slices -> 3D block -> flags lesion -> calculates exact cm³
       ▼
4. Grounded Report (vLLM + Structured JSON)
       │  Reads key slice + exact cm³ -> outputs strict FHIR R4 JSON
       ▼
5. Audit Log (SQLite / JSONL)
          Links original image hash -> 3D mask -> final JSON report
