"""
main.py
-------
CLI orchestrator for the Genomics AI Interpretation Pipeline.

Usage:
    python src/main.py \\
        --vcf data/sample_nucleus_dna_download_vcf_NU-DRSQ-5692_copy.vcf \\
        --snps "data/TEST _ Genomics  - Sheet1.tsv" \\
        --output output/

Supports skipping stages when cached results exist.
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from tqdm import tqdm

# ── Local modules ────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from snp_catalog import load_snp_catalog, save_duplicate_report
from rsid_resolver import resolve_rsids
from vcf_extractor import build_position_lookup, extract_variants
from ai_interpreter import interpret_variants
from report_generator import build_output_dataframe, save_csv, save_markdown_report


# ── Logging setup ─────────────────────────────────────────────────────────────
def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _banner(msg: str) -> None:
    width = 60
    print(f"\n{'─' * width}")
    print(f"  {msg}")
    print(f"{'─' * width}")


# ── Stage functions ───────────────────────────────────────────────────────────

def stage_catalog(args) -> tuple:
    """Stage 1: Load and normalize SNP catalog."""
    _banner("Stage 1 · SNP Catalog")
    df, meta = load_snp_catalog(args.snps)

    # Save duplicate report
    dup_path = Path(args.data_dir) / "duplicate_rsids.json"
    save_duplicate_report(meta, dup_path)

    print(f"  ✓ {meta['total_rows']} total rows parsed")
    print(f"  ✓ {meta['unique_rsid_count']} unique rsIDs")
    print(f"  ✓ {len(meta['duplicate_rsids'])} rsIDs appear in multiple categories → {dup_path}")
    print(f"  ⚠ Edge cases: {list(meta['edge_cases'].keys())}")
    return df, meta


def stage_resolve(args, meta: dict) -> dict:
    """Stage 2: Resolve rsIDs to chromosomal positions."""
    _banner("Stage 2 · rsID Resolution (NCBI dbSNP)")
    cache_path = Path(args.data_dir) / "rsid_positions.json"

    unique_rsids = meta["unique_rsids"]
    print(f"  Resolving {len(unique_rsids)} unique rsIDs... (cache: {cache_path})")

    resolved = resolve_rsids(unique_rsids, cache_path=cache_path)

    n_resolved = sum(1 for v in resolved.values() if v.get("resolved"))
    print(f"  ✓ {n_resolved}/{len(unique_rsids)} rsIDs resolved to chromosomal positions")
    unresolved = [r for r, v in resolved.items() if not v.get("resolved")]
    if unresolved:
        print(f"  ⚠ Could not resolve: {unresolved}")
    return resolved


def stage_extract(args, catalog_df, resolved: dict) -> dict[str, dict]:
    """Stage 3: Extract genotypes from VCF."""
    _banner("Stage 3 · VCF Genotype Extraction")
    position_lookup = build_position_lookup(catalog_df, resolved)
    print(f"  Scanning VCF for {len(position_lookup)} unique positions...")
    print(f"  VCF: {args.vcf}  (this may take 1–3 minutes for a 1.5 GB file)")

    t0 = time.time()
    extraction_results = extract_variants(args.vcf, position_lookup)
    elapsed = time.time() - t0

    n_found = sum(1 for v in extraction_results.values() if v.get("found_in_vcf"))
    print(f"  ✓ {n_found}/{len(extraction_results)} SNPs found in VCF  [{elapsed:.1f}s]")
    return extraction_results


def stage_interpret(args, catalog_df, extraction_results: dict) -> list[dict]:
    """Stage 4: AI interpretation via Gemini."""
    _banner("Stage 4 · AI Interpretation (Gemini)")

    # Build per-row list combining catalog metadata + VCF extraction results
    rows: list[dict] = []
    seen_rsids: set[str] = set()

    for _, cat_row in catalog_df.iterrows():
        rsid = cat_row["rsid"]
        if rsid in seen_rsids:
            continue  # Only interpret once per unique rsID
        seen_rsids.add(rsid)

        vcf_data = extraction_results.get(rsid, {})
        rows.append({
            "rsid":            rsid,
            "gene":            cat_row["gene"],
            "category":        cat_row["category"],
            "description":     cat_row["description"],
            "is_edge_case":    cat_row.get("is_edge_case", False),
            "edge_case_note":  cat_row.get("edge_case_note", ""),
            **vcf_data,
        })

    print(f"  Interpreting {len(rows)} unique rsIDs via Gemini...")
    interpreted = interpret_variants(rows)
    n_done = sum(1 for r in interpreted if not r.get("ai_skipped"))
    print(f"  ✓ {n_done}/{len(rows)} SNPs interpreted")
    return interpreted


def stage_report(args, catalog_df, interpreted_rows: list[dict]) -> None:
    """Stage 5: Generate CSV and Markdown report."""
    _banner("Stage 5 · Report Generation")
    output_dir = Path(args.output)

    df = build_output_dataframe(catalog_df, interpreted_rows)
    csv_path = save_csv(df, output_dir)
    md_path = save_markdown_report(df, output_dir)

    print(f"  ✓ CSV  → {csv_path}  ({len(df)} rows)")
    print(f"  ✓ Report → {md_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Genomics AI Interpretation Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full pipeline
  python src/main.py --vcf data/sample.vcf --snps "data/TEST _ Genomics  - Sheet1.tsv"

  # Skip VCF extraction (use cached positions), re-run AI + report
  python src/main.py --vcf data/sample.vcf --snps "data/..." --skip-resolve --skip-extract

  # Verbose logging
  python src/main.py --vcf data/sample.vcf --snps "data/..." -v
""",
    )
    parser.add_argument(
        "--vcf",
        default="data/sample_nucleus_dna_download_vcf_NU-DRSQ-5692_copy.vcf",
        help="Path to the VCF file",
    )
    parser.add_argument(
        "--snps",
        default="data/TEST _ Genomics  - Sheet1.tsv",
        help="Path to the SNP TSV catalog",
    )
    parser.add_argument(
        "--output", default="output/",
        help="Output directory for CSV and Markdown report",
    )
    parser.add_argument(
        "--data-dir", default="data/",
        help="Directory for cached intermediate files",
    )
    parser.add_argument(
        "--skip-resolve", action="store_true",
        help="Skip rsID resolution (requires data/rsid_positions.json cache)",
    )
    parser.add_argument(
        "--skip-extract", action="store_true",
        help="Skip VCF extraction (requires data/extraction_results.json cache)",
    )
    parser.add_argument(
        "--skip-ai", action="store_true",
        help="Skip AI interpretation (output genotypes only, no interpretations)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    setup_logging(args.verbose)

    print("\n" + "═" * 60)
    print("  🧬  Genomics AI Interpretation Pipeline")
    print("═" * 60)

    t_total = time.time()

    # Stage 1: Catalog
    catalog_df, meta = stage_catalog(args)

    # Stage 2: Resolve rsIDs
    resolved_cache = Path(args.data_dir) / "rsid_positions.json"
    if args.skip_resolve and resolved_cache.exists():
        print(f"\n⏭  Skipping rsID resolution (using cache: {resolved_cache})")
        with open(resolved_cache) as f:
            resolved = json.load(f)
    else:
        resolved = stage_resolve(args, meta)

    # Stage 3: VCF Extraction
    extraction_cache = Path(args.data_dir) / "extraction_results.json"
    if args.skip_extract and extraction_cache.exists():
        print(f"\n⏭  Skipping VCF extraction (using cache: {extraction_cache})")
        with open(extraction_cache) as f:
            extraction_results = json.load(f)
    else:
        extraction_results = stage_extract(args, catalog_df, resolved)
        # Cache for future --skip-extract runs
        with open(extraction_cache, "w") as f:
            json.dump(extraction_results, f, indent=2)

    # Stage 4: AI Interpretation
    if args.skip_ai:
        print("\n⏭  Skipping AI interpretation")
        interpreted_rows = [
            {"rsid": k, **v,
             "interpretation": "AI skipped (--skip-ai flag)",
             "confidence_level": "N/A", "mechanism": "N/A",
             "ai_skipped": True, "ai_skip_reason": "--skip-ai flag used"}
            for k, v in extraction_results.items()
        ]
    else:
        interpreted_rows = stage_interpret(args, catalog_df, extraction_results)

    # Stage 5: Report
    stage_report(args, catalog_df, interpreted_rows)

    elapsed = time.time() - t_total
    print(f"\n{'═' * 60}")
    print(f"  ✅  Pipeline complete in {elapsed:.1f}s")
    print(f"  📁  Outputs in: {Path(args.output).resolve()}")
    print(f"{'═' * 60}\n")


if __name__ == "__main__":
    main()
