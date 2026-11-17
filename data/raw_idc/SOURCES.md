# Vendored IDC sample studies

These series are unmodified copies of public objects from the NCI Imaging Data Commons. They are included so downstream builders can clone a working Chest CT and Brain MRI example without querying IDC.

| Key | Collection | Patient | License | DOI |
|---|---|---|---|---|
| `chest_ct` | LIDC-IDRI | LIDC-IDRI-0813 | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) | [10.7937/K9/TCIA.2015.LO9QL9SX](https://doi.org/10.7937/K9/TCIA.2015.LO9QL9SX) |
| `brain_mri` | UPENN-GBM | UPENN-GBM-00349 | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | [10.7937/TCIA.709X-DN49](https://doi.org/10.7937/TCIA.709X-DN49) |

Series UIDs and local paths are in `manifest.json`. Regenerate or replace the files with `scripts/fetch_idc_samples.py`.

Cite the original collections and the Imaging Data Commons (https://imaging.datacommons.cancer.gov/) when reusing these slices.
