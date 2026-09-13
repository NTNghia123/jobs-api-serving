# Jobs Serving API pipeline diagram

- `jobs-serving-api-pipeline.drawio`: editable source for draw.io / diagrams.net.
- `jobs-serving-api-pipeline-preview.svg`: quick visual preview of the same layout.
- `jobs-serving-api-pipeline-preview.png`: rendered preview for viewers that do not display SVG.
- The diagram follows the repository's current MongoDB → BigQuery migration architecture.
- Production path: TopDev/VietnamWorks → scraper → MongoDB → Dagster → Python ELT → BigQuery → FastAPI on Cloud Run.
- Supporting services: Secret Manager, optional Memorystore/Redis, GitHub Actions, Workload Identity Federation, and Artifact Registry.
- Runtime alternatives are shown in the footer: BigQuery (production), DuckDB (local development), fake repositories (tests).

Open the `.drawio` file in draw.io and use **File → Export as** to generate PNG, SVG, or PDF.
