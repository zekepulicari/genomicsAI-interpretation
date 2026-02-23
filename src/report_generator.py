"""
report_generator.py
-------------------
Generates the final deliverables:
  1. CSV table: all 149 rows with genotypes + AI interpretations
  2. Markdown report: professional summary, method description, placeholder prompts section
"""

import logging
from datetime import date
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

OUTPUT_CSV_NAME = "genomics_report.csv"
OUTPUT_REPORT_NAME = "genomics_report.md"

CSV_COLUMNS = [
    "category",
    "gene",
    "rsid",
    "genotype",
    "zygosity",
    "ref",
    "alt",
    "interpretation",
    "confidence_level",
    "mechanism",
    "found_in_vcf",
    "is_duplicate",
    "is_edge_case",
    "edge_case_note",
    "ai_skip_reason",
]


def build_output_dataframe(catalog_df: pd.DataFrame, interpreted_rows: list[dict]) -> pd.DataFrame:
    """
    Merge the original 149-row catalog with VCF extraction + AI interpretation results.

    All 149 rows are preserved. For duplicated rsIDs (same rsID, multiple categories),
    each row gets the same genotype/interpretation but retains its own category.
    """
    # Build a lookup rsid → interpreted data (one record per unique rsid)
    interp_by_rsid: dict[str, dict] = {r["rsid"]: r for r in interpreted_rows}

    output_rows = []
    for _, cat_row in catalog_df.iterrows():
        rsid = cat_row["rsid"]
        interp = interp_by_rsid.get(rsid, {})

        row = {
            "category":        cat_row["category"],
            "gene":            cat_row["gene"],
            "rsid":            rsid,
            "description":     cat_row["description"],
            "genotype":        interp.get("genotype", "N/A"),
            "zygosity":        interp.get("zygosity", "not resolved"),
            "ref":             interp.get("ref", ""),
            "alt":             interp.get("alt", ""),
            "interpretation":  interp.get("interpretation", "Not available"),
            "confidence_level": interp.get("confidence_level", "N/A"),
            "mechanism":       interp.get("mechanism", "N/A"),
            "found_in_vcf":    interp.get("found_in_vcf", False),
            "is_duplicate":    cat_row.get("is_duplicate", False),
            "is_edge_case":    cat_row.get("is_edge_case", False),
            "edge_case_note":  cat_row.get("edge_case_note", ""),
            "ai_skip_reason":  interp.get("ai_skip_reason", ""),
        }
        output_rows.append(row)

    df = pd.DataFrame(output_rows)
    logger.info(f"Output DataFrame built: {len(df)} rows")
    return df


def save_csv(df: pd.DataFrame, output_dir: str | Path) -> Path:
    """Save the final CSV report."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / OUTPUT_CSV_NAME
    df.to_csv(out_path, index=False, encoding="utf-8")
    logger.info(f"CSV saved → {out_path}")
    return out_path


def _build_category_summary(df: pd.DataFrame) -> str:
    """Build a markdown summary broken down by category."""
    lines = []
    categories = df["category"].unique()
    for cat in categories:
        cat_df = df[df["category"] == cat]
        n_total = len(cat_df)
        n_found = cat_df["found_in_vcf"].sum()
        n_high = (cat_df["confidence_level"] == "High").sum()
        n_mod = (cat_df["confidence_level"] == "Moderate").sum()
        n_conflict = (cat_df["confidence_level"] == "Conflicting").sum()
        lines.append(f"### {cat}")
        lines.append(f"- **SNPs in category:** {n_total} | **Found in VCF:** {n_found}")
        lines.append(f"- **Confidence:** High: {n_high} | Moderate: {n_mod} | Conflicting: {n_conflict}")

        # Show a few notable findings
        high_conf = cat_df[cat_df["confidence_level"].isin(["High", "Moderate"]) & cat_df["found_in_vcf"]]
        if not high_conf.empty:
            lines.append("- **Key findings:**")
            for _, r in high_conf.head(3).iterrows():
                lines.append(f"  - **{r['gene']} ({r['rsid']})** `{r['genotype']}` — {r['interpretation']}")
        lines.append("")
    return "\n".join(lines)


def _count_stats(df: pd.DataFrame) -> dict:
    found = df["found_in_vcf"].sum()
    total = len(df)
    high = (df["confidence_level"] == "High").sum()
    mod = (df["confidence_level"] == "Moderate").sum()
    low = (df["confidence_level"] == "Low").sum()
    conflicting = (df["confidence_level"] == "Conflicting").sum()
    het = (df["zygosity"] == "heterozygous").sum()
    hom_alt = (df["zygosity"] == "homozygous alt").sum()
    edge = df["is_edge_case"].sum()
    dup = df[df["is_duplicate"]]["rsid"].nunique()
    return dict(
        found=found, total=total, high=high, mod=mod, low=low,
        conflicting=conflicting, het=het, hom_alt=hom_alt, edge=edge, dup=dup
    )


def save_markdown_report(df: pd.DataFrame, output_dir: str | Path, meta: dict | None = None) -> Path:
    """Generate and save the professional Markdown report."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / OUTPUT_REPORT_NAME

    stats = _count_stats(df)
    today = date.today().strftime("%B %d, %Y")

    report = f"""# Pediatric Genomic Interpretation Report

**Report Date:** {today}
**Sample ID:** NU-DRSQ-5692
**Sequencing Platform:** Illumina DRAGEN (Whole Genome Sequencing, hg38)
**SNP Panel Size:** {stats['total']} SNPs across {df['category'].nunique()} functional categories

---

## Executive Summary

This report presents a functional interpretation of a child's whole-genome sequencing data,
focusing on {stats['total']} curated single-nucleotide polymorphisms (SNPs) across six major
biological domains: neurotransmitter function, metabolic health, nutrient absorption,
gut–brain axis, immune resilience, and sleep regulation.

Of the {stats['total']} curated SNPs, **{stats['found']}** were successfully extracted from the
VCF ({stats['total'] - stats['found']} could not be resolved — see edge cases below).
AI-generated functional interpretations were produced for all resolved variants,
with **{stats['high']} High-confidence** and **{stats['mod']} Moderate-confidence** calls.

### Genotype Distribution

| Zygosity | Count |
|----------|-------|
| Heterozygous | {stats['het']} |
| Homozygous Alt | {stats['hom_alt']} |
| Homozygous Ref / Not found | {stats['total'] - stats['het'] - stats['hom_alt']} |

### Confidence Distribution

| Level | Count |
|-------|-------|
| High | {stats['high']} |
| Moderate | {stats['mod']} |
| Low | {stats['low']} |
| Conflicting | {stats['conflicting']} |

---

## Findings by Category

{_build_category_summary(df)}

---

## Data Quality & Edge Cases

The following SNPs could **not** be extracted from the standard VCF and are excluded from interpretation:

| Gene | rsID | Reason |
|------|------|--------|
| GSTM1 | Null | Gene deletion (copy number variant) — requires CNV analysis, not point variant calling |
| SLC6A3 | rs28363170 | VNTR (variable number tandem repeat) — insertion/deletion repeat, absent from standard SNP VCF |
| IFNL4 | rs368234815 | Dinucleotide variant (TT/ΔG) — may be absent from some VCF builds |

Additionally, **{stats['dup']} rsIDs** appear in multiple functional categories in the SNP panel;
each category row is preserved independently in the CSV with the same genotype data.

---

## Methodology

### 1. Variant Extraction

1. **SNP Catalog Normalization** (`snp_catalog.py`): The 149-SNP curated TSV was parsed
   and normalized. The first 3 rows used an abbreviated format (missing a Category column)
   and were corrected by assigning them to the "Pediatric Pharmacogenomics" category.
   Duplicate rsIDs across categories were flagged and tracked in `data/duplicate_rsids.json`.

2. **rsID → Genomic Position Resolution** (`rsid_resolver.py`): Since the VCF uses the
   DRAGEN pipeline with hg38 coordinates and does not populate the rsID column (`ID = .`),
   each unique rsID was resolved to a chromosomal position via the
   **NCBI dbSNP E-Utilities API** (with `NCBI_KEY` for accelerated rate limits).
   Results were cached to `data/rsid_positions.json`.

3. **VCF Streaming Extraction** (`vcf_extractor.py`): The ~1.5 GB whole-genome VCF was
   streamed line-by-line to extract genotypes at the resolved positions. For each match,
   the GT field was decoded into a nucleotide genotype (e.g., `AG`) and zygosity
   (heterozygous / homozygous ref / homozygous alt).

### 2. AI Interpretation

Each resolved genotype was passed to the **Google Gemini API** (`gemini-2.0-flash`) using
a structured prompt that enforces:
- No diagnostic language
- Mechanism-focused, parent-friendly prose (1 sentence)
- JSON output: `interpretation`, `confidence_level`, `mechanism`

SNPs with missing or unresolvable genotypes were flagged as not interpretable with
a documented reason.

### Tools & Libraries

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.11 | Core language |
| pandas | ≥2.2 | Data manipulation |
| requests | ≥2.31 | NCBI dbSNP API calls |
| google-generativeai | ≥0.8 | Gemini API integration |
| python-dotenv | ≥1.0 | API key management |
| streamlit | ≥1.41 | Interactive dashboard |
| Docker | — | Reproducible container runtime |
| NCBI dbSNP E-Utilities | — | rsID → position resolution |
| Illumina DRAGEN | 05.121 | Upstream sequencing pipeline (hg38) |

---

## Limitations

- **Population Reference Bias**: Functional effect sizes are derived from GWAS studies
  predominantly in European populations; effect sizes may vary across ancestries.
- **Single-Gene Interpretation**: Each SNP is interpreted in isolation. Gene–gene
  interactions (epistasis) are not modeled.
- **Pediatric Applicability**: Some SNPs (e.g., statin metabolism, SLCO1B1) are more
  relevant in adult clinical contexts.
- **Confidence Grading**: Confidence levels reflect the strength and consistency of
  published GWAS evidence, not clinical diagnostic certainty.

---

## Prompts Used

> 📝 **Paste the prompts you used below this section.**

```
<!-- SYSTEM PROMPT (from .agents/rules/bioinformatics-ai.md) -->


<!-- USER PROMPT TEMPLATE -->


<!-- Any additional prompts or iterations -->

```

---

*Report generated automatically by the Genomics AI Interpretation Pipeline.*
*All interpretations are informational and do not constitute medical advice.*
"""

    out_path.write_text(report, encoding="utf-8")
    logger.info(f"Markdown report saved → {out_path}")
    return out_path
