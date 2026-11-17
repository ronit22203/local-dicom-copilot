# De-identification

A DICOM file is an image plus a header. The header can hold a name, a birth date, a phone number, or a hospital ID. This script clears those fields and checks that the image bytes did not change.

It is a test of the pipeline. It is not a full hospital de-identification product. It does not search the picture for text burned into the pixels, and it only edits the header fields listed in `pipeline_monolith.py`.

## Run it

```bash
uv run --with pydicom python pipeline_monolith.py --study brain_mri
uv run --with pydicom python pipeline_monolith.py --study chest_ct
```

The public files in `data/raw_idc/` stay as they are. New files go under `work/`, and git ignores that folder.

## Step by step

**Stage 0 writes a dirty copy.** Each slice is copied to `work/<study>/00_dirty/`. The copy gets fake names, dates, a phone number, an accession number, and a private vendor tag. Those fakes give Stage 1 something obvious to remove.

**Stage 1 writes a clean copy** to `work/<study>/01_scrubbed/`. For every slice it:

1. Hashes the pixel bytes.
2. Blanks or deletes the identity fields in the tag list.
3. Deletes every private tag.
4. Replaces the study, series, and image IDs so the original IDs are gone.
5. Sets the patient ID to `ANON-` plus a short hash of the study name (`brain_mri` or `chest_ct`).
6. Marks the file with `PatientIdentityRemoved = YES`.
7. Hashes the pixels again and stops if the image bytes changed.

The same study name always gets the same new study and series IDs, so a viewer can still open the slices as one scan.

Stage 1 also writes `work/<study>/01_scrubbed/provenance_manifest.json`. That file holds the stand-in IDs, one pixel hash per slice, and one hash for the whole series.

**The check reads the clean headers.** It fails if a planted fake value is still present, if a private tag remains, or if a file is not marked as identity-removed.
