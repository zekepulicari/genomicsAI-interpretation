# 🧬 Genomics AI Interpretation Pipeline

Extracts curated SNPs from a whole-genome VCF and generates AI-powered functional summaries for a pediatric genomic profile.

## Pipeline Architecture

```
TSV (149 SNPs) → snp_catalog → rsid_resolver (NCBI API) → vcf_extractor (1.5GB VCF)
                                                                ↓
                              report_generator ← ai_interpreter (Gemini API)
                                   ↓
                        CSV (149 rows) + Markdown Report + Streamlit Dashboard
```

## Module Reference

| Module | Purpose |
|--------|---------|
| `src/snp_catalog.py` | Parses the 149-row curated SNP spreadsheet, fixes malformed rows, flags 25 duplicate rsIDs across categories, tracks 3 edge cases (GSTM1 Null, SLC6A3 VNTR, IFNL4 dinucleotide) |
| `src/rsid_resolver.py` | Resolves each rsID → `chr:position` via NCBI dbSNP E-Utilities API with `NCBI_KEY`; caches results to `data/rsid_positions.json` for instant re-runs |
| `src/vcf_extractor.py` | Streams the 1.5 GB VCF line-by-line (memory-efficient), matches target positions, extracts GT → genotype + zygosity. Short-circuits when all targets found |
| `src/ai_interpreter.py` | Sends each variant to Gemini (`gemini-2.0-flash`) using a structured prompt: mechanism-focused, parent-friendly, no diagnostic language. Skips variants with no VCF data |
| `src/report_generator.py` | Generates `genomics_report.csv` (all 149 rows with genotypes + interpretations) and a professional Markdown report with methodology, findings by category, and a placeholder for prompts |
| `src/main.py` | CLI orchestrator — runs all 5 stages sequentially with `--skip-resolve`, `--skip-extract`, `--skip-ai` flags for fast re-runs |
| `src/app.py` | Streamlit dashboard — interactive table with category/confidence/zygosity filters, metric cards, pipeline runner, and download buttons |
| `PROMPTS_USED.md` | Dedicated template for system and user prompts used in AI interpretation |
| `tests/` | 25 pytest tests covering TSV parsing, GT decoding, VCF extraction, and end-to-end data flow |
| `Dockerfile` + `docker-compose.yml` | Containerized deployment with `cli` and `streamlit` profiles |

## Setup

### Option A: Docker (recommended)

```bash
# Copy and fill in API keys
cp .env.example .env

# Run the full CLI pipeline
docker-compose --profile cli up --build

# Or run the Streamlit dashboard
docker-compose --profile streamlit up --build
# → Open http://localhost:8501
```

### Option B: Local Python

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Running the Pipeline

```bash
# Full pipeline (all stages)
python src/main.py \
  --vcf "data/sample_nucleus_dna_download_vcf_NU-DRSQ-5692_copy.vcf" \
  --snps "data/TEST _ Genomics  - Sheet1.tsv" \
  --output output/

# Resume from cached genotypes (skip slow VCF scan)
python src/main.py --skip-resolve --skip-extract

# Skip AI (genotypes only)
python src/main.py --skip-ai

# Streamlit dashboard
streamlit run src/app.py
```

### Skip Flags

| Flag | Skips | Requires |
|------|-------|---------|
| `--skip-resolve` | NCBI API calls | `data/rsid_positions.json` |
| `--skip-extract` | VCF scan | `data/extraction_results.json` |
| `--skip-ai` | Gemini API | — |

## Running Tests

```bash
python -m pytest tests/ -v
```

## Environment Variables (.env)

| Variable | Description |
|----------|-------------|
| `NCBI_KEY` | NCBI E-Utilities API key ([get free key](https://www.ncbi.nlm.nih.gov/account/)) |
| `DEFAULT_GEMINI_KEY` | Google Gemini API key ([get free key](https://aistudio.google.com/apikey)) |

## Outputs

| File | Description |
|------|-------------|
| `output/genomics_report.csv` | 149-row table: genotypes + AI interpretations |
| `output/genomics_report.md` | Professional summary report |
| `data/duplicate_rsids.json` | rsIDs appearing in multiple categories |
| `data/rsid_positions.json` | Cached rsID → position lookups |
| `data/extraction_results.json` | Cached VCF extraction results |
