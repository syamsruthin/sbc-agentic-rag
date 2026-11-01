# data/

Nothing under `data/` is committed except this file. The SBC PDFs, the CMS reference file, the catalog and
the converted Markdown are fetched or generated locally (about 6.4 GB for plan year 2027).

All sources are public:

- `sbc.anthem.com/dps` and `sbc.wellpoint.com/dps`: the insurers' public SBC search portals
- CMS Exchange Plan Attributes PUF: `download.cms.gov/marketplace-puf/<year>/plan-attributes-puf.zip`

## Rebuild

From the repository root:

```bash
uv sync                                          # installs pdfplumber and requests via sbc-ingest
uv run python scripts/sbc_portal.py catalog      # ~5 min: query the portals, write the catalog
uv run python scripts/sbc_portal.py download     # resumable; skips files already on disk
uv run python scripts/sbc_portal.py manifest     # sizes and sha256 for every PDF
uv run python scripts/sbc_to_markdown.py         # PDF -> chunk-ready Markdown
uv run python scripts/verify_markdown.py         # independent check of every Markdown file (needs poppler)
```

The downloader uses 6 parallel workers by default (`--workers`). Keep it modest and don't re-run it more
often than needed; the portals are public services.

## Layout

```
data/
  catalog/sbc_catalog_<year>.csv     one row per SBC (portal index metadata, URL, local path)
  catalog/sbc_manifest_<year>.csv    catalog + downloaded flag, bytes, sha256
  raw/sbc/<year>/<portal>/<STATE>/<Market>/<Plan-Name>__<contract>__<sbcId>.pdf
  reference/cms_puf_<year>/          CMS plan attributes file
  markdown/sbc/<year>/<portal>/<STATE>/<Market>/<same name>.md
  markdown/conversion_report_<year>.csv
  markdown/verification_report_<year>.csv
```
