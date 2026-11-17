# Sample data

Two public studies are stored in the repo so you can try the pipeline without an account.

| Folder | What it is | Patient | Slices |
|---|---|---|---|
| `data/raw_idc/chest_ct` | Chest CT from LIDC-IDRI | LIDC-IDRI-0813 | 157 |
| `data/raw_idc/brain_mri` | Brain MRI, T1 after contrast, from UPENN-GBM | UPENN-GBM-00349 | 192 |

`data/raw_idc/manifest.json` lists the series IDs and folders. `data/raw_idc/SOURCES.md` lists the licenses and the papers to cite. Chest CT is CC BY 3.0. Brain MRI is CC BY 4.0.

## Download them again

```bash
python scripts/fetch_idc_samples.py
```

This needs the `idc-index` package. The script leaves a study alone when `.dcm` files are already there. Add `--force` to download again.

```bash
python scripts/fetch_idc_samples.py --dataset chest_ct
python scripts/fetch_idc_samples.py --dataset brain_mri --force
```

Each run picks the same series. The queries sort by series ID and take the first match.
