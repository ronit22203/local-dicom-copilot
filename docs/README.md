# Docs

This project keeps scans on your machine. It removes patient details from DICOM headers before any later measurement or report step.

## What you can run today

1. Two public sample studies are already in `data/raw_idc/`. See [Sample data](sample-data.md).
2. `pipeline_monolith.py` copies a study, plants fake patient details, strips them, and checks that none remain. See [De-identification](scrubber.md).

## Not built yet

The root README describes a local Orthanc inbox, 3D measurement, a JSON report, and an audit log. Those steps are not in the repo yet.
