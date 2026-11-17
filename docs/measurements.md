# Measurements and report

After the scrubber finishes, two more steps run on the clean slices.

## Stage 2

The script stacks the slices into a 3D volume and labels connected bright regions. Brain MRI uses the brightest tissue inside the head. Chest CT looks for soft-tissue spots surrounded by lung air.

Each region that passes the size filter is one object in `work/<study>/02_priors.json`:

- `volume_cm3`
- `slice_area_mm2` on the slice where the region is largest
- `voxel_count`
- `peak_slice_index` (slice 1 is the first slice)
- `slice_sha256` of that slice's pixels
- `finding_XX_overlay.png` next to the JSON

## Stage 3

`03_report.json` is a FHIR DiagnosticReport. The volumes and hashes in it are copied from Stage 2.

```bash
uv run --with pydicom --with numpy --with scipy --with pillow --with pydantic python pipeline_monolith.py --study brain_mri --offline-stub
```

`--offline-stub` fills the report on this machine and does not call a model. Leave the flag off and pass `--vllm-url http://127.0.0.1:8000/v1` to ask a local OpenAI-compatible server for the wording. The server is called with `guided_json` set to the report schema. Returned numbers are replaced with the Stage 2 values.

## Viewer

```bash
uv run --with fastapi --with uvicorn --with python-multipart --with pydicom --with numpy --with scipy --with pillow --with pydantic python dicom-view/server.py
```

Open http://127.0.0.1:8765/. **Load Study (brain_mri)** or **Load Study (chest_ct)** reads `work/<study>/` and fills all three panes. If those files are missing, the server runs the pipeline first.

**Upload DICOM** or dropping a folder sends the files to the server, which runs the same stages and then fills the panes.
