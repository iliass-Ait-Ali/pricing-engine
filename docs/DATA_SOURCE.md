# Data source

## Dataset

**Dominick's Finer Foods store-level scanner data**, category **Cereals**.

| | |
| --- | --- |
| Provider | Kilts Center for Marketing, University of Chicago Booth School of Business |
| Landing page | <https://www.chicagobooth.edu/research/kilts/research-data/dominicks> |
| Manual / codebook | <https://www.chicagobooth.edu/research/kilts/research-data/-/media/enterprise/centers/kilts/datasets/dominicks-dataset/dominicks-manual-and-codebook_kiltscenter.pdf> |
| UPC metadata file | `upc_csv-files/upccer.csv` |
| Movement file | `movement_csv-files/wcer.zip` -> `wcer.csv` |
| Coverage in this repo | 93 stores, 489 UPCs, 366 observed weeks, 1989-09-14 .. 1997-05-01 |

The exact byte sizes, SHA-256 hashes and download timestamps of the files used
are recorded in `data/raw/dominicks/cereals/SOURCE.json` and reproduced in
`reports/01_DATA_AUDIT.md`.

## Usage restriction and acknowledgment

The Kilts Center makes the Dominick's data available **for academic research
purposes**. Users are asked to **acknowledge the Kilts Center, University of
Chicago Booth School of Business** in working papers and publications.

Consequences enforced in this repository:

* Raw and processed data are **git-ignored** (`data/raw/`, `data/interim/`,
  `data/processed/`) and are never committed or redistributed.
  `tests/test_repo_and_downloader.py` asserts this.
* CI runs on small synthetic fixtures only - it never downloads the licensed
  dataset on a public build service.
* The downloader (`scripts/download_dominicks.py`) only ever contacts official
  `chicagobooth.edu` URLs; there is no Kaggle/mirror fallback, and no synthetic
  substitution. A test asserts the URL host.

## Acquisition

```bash
python scripts/download_dominicks.py          # official URLs only
python scripts/download_dominicks.py --force  # re-download
```

The script creates directories, streams downloads with timeouts, validates HTTP
status and non-zero size, writes to a temporary file and renames atomically,
guards against zip-slip / absolute paths when extracting, normalises the
extracted movement CSV to `wcer.csv` while recording the original archive
member name, and writes provenance to `SOURCE.json`.

If the network or the site blocks automated retrieval, the script prints exact
manual download instructions and marks acquisition as **BLOCKED**; it never
fabricates or substitutes data.

## Why Dominick's is suitable for a pricing project

* It is one of very few **publicly documented retail scanner panels** with
  store-level weekly prices, unit movement, promotion codes and gross margin.
* The panel is long (about 8 years) and wide (about 90 stores), so temporal
  evaluation and cross-store heterogeneity are both possible.
* Prices genuinely vary within product-store series (regular price changes,
  Bonus Buys, coupons), which is what price-response analysis needs.
* Store-level zone pricing and documented promotions make the endogeneity
  problem visible and discussable rather than hidden.

## Major limitations of this data source

1. **Historical**: 1989-1997 US grocery retail. Price levels, category
   dynamics and competition are not today's market.
2. **No customer-level data**: the grain is store x week, so no personalised
   pricing, no basket composition, no traffic normalisation.
3. **Incomplete promotion coding**: a present `sale` code indicates a
   promotion, but a missing code does **not** prove no promotion occurred.
   Two undocumented codes (`G`, `L`) also appear in the Cereals file.
4. **Margins are accounting figures**: `profit` is a gross-margin percentage
   built on **Average Acquisition Cost (AAC)**, an inventory accounting measure
   which can differ from the economically relevant replacement cost.
5. **`ok = 0` rows**: the manual marks these as suspect/trash; they are
   excluded from the modelling table and counted in the audit.
6. **Zero-price rows**: about 26.6% of raw rows carry `price = 0` (item not
   priced/stocked that week). They cannot support a unit price and are excluded
   with a documented rule.
7. **No randomised-treatment labels in this extract**: the broader Dominick's
   research programme included in-store experiments, but the Cereals movement
   file used here carries no experiment assignment or window that we can
   verify, so nothing in this project is claimed as causal identification.
