"""
snp_catalog.py
--------------
Parses and normalizes the curated SNP spreadsheet (TSV).

Key behaviors:
- Fixes first 3 rows that lack a Category column
- Normalizes all 149 rows to: category, gene, rsid, description
- Preserves ALL 149 rows (including duplicates) for final output
- Produces a separate list of unique rsIDs for VCF lookup
- Saves data/duplicate_rsids.json documenting which rsIDs appear in multiple categories
"""

import json
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# The first 3 rows in the TSV have only 3 columns (gene, rsid, description)
# They belong to the Pediatric Pharmacogenomics category
FIRST_ROWS_CATEGORY = "Pediatric Pharmacogenomics"

# Known edge-case rsIDs that cannot be looked up in a standard VCF
EDGE_CASE_RSIDS = {
    "Null": "GSTM1 gene deletion — not a point variant, requires CNV analysis",
    "rs28363170": "VNTR (variable number tandem repeat) — not typically in standard VCF",
    "rs368234815": "Dinucleotide variant (IFNL4 TT>ΔG) — may not appear in standard SNP VCF",
}


def load_snp_catalog(tsv_path: str | Path) -> tuple[pd.DataFrame, dict]:
    """
    Load, parse, and normalize the SNP TSV spreadsheet.

    Args:
        tsv_path: Path to the TSV file.

    Returns:
        A tuple of:
          - full_df: DataFrame with all 149 rows, columns:
                     [row_id, category, gene, rsid, description, is_duplicate, is_edge_case, edge_case_note]
          - meta: dict with unique rsIDs, edge cases, duplicates summary
    """
    tsv_path = Path(tsv_path)
    if not tsv_path.exists():
        raise FileNotFoundError(f"SNP catalog not found: {tsv_path}")

    rows = []
    with open(tsv_path, "r", encoding="utf-8-sig") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.rstrip("\r\n")
            if not line:
                continue
            parts = line.split("\t")

            if len(parts) == 4:
                # Normal row: Category | Gene | rsID | Description
                category, gene, rsid, description = (p.strip() for p in parts)
            elif len(parts) == 3:
                # Misaligned row (first 3 rows): Gene | rsID | Description
                gene, rsid, description = (p.strip() for p in parts)
                category = FIRST_ROWS_CATEGORY
                logger.debug(
                    f"Line {line_num}: Fixed misaligned row — assigned category '{category}' to {gene} {rsid}"
                )
            else:
                logger.warning(f"Line {line_num}: Unexpected column count ({len(parts)}), skipping: {line[:80]}")
                continue

            rows.append(
                {
                    "row_id": line_num,
                    "category": category,
                    "gene": gene,
                    "rsid": rsid,
                    "description": description,
                }
            )

    if not rows:
        raise ValueError("No rows parsed from SNP catalog. Check file format.")

    df = pd.DataFrame(rows)
    logger.info(f"Parsed {len(df)} total rows from SNP catalog")

    # --- Track duplicates ---
    # An rsID is a duplicate if it appears in more than one category
    rsid_to_categories: dict[str, list[str]] = {}
    for _, row in df.iterrows():
        rsid_to_categories.setdefault(row["rsid"], [])
        if row["category"] not in rsid_to_categories[row["rsid"]]:
            rsid_to_categories[row["rsid"]].append(row["category"])

    duplicate_rsids = {
        rsid: cats for rsid, cats in rsid_to_categories.items() if len(cats) > 1
    }
    logger.info(f"Found {len(duplicate_rsids)} rsIDs appearing in multiple categories")

    df["is_duplicate"] = df["rsid"].isin(duplicate_rsids)

    # --- Flag edge cases ---
    df["is_edge_case"] = df["rsid"].isin(EDGE_CASE_RSIDS)
    df["edge_case_note"] = df["rsid"].map(lambda r: EDGE_CASE_RSIDS.get(r, ""))

    # --- Unique rsIDs for resolver (exclude pure edge cases that can't be resolved) ---
    unresolvable = {"Null"}  # GSTM1 Null has no rsID at all
    unique_rsids = [
        rsid
        for rsid in rsid_to_categories
        if rsid not in unresolvable
    ]

    meta = {
        "total_rows": len(df),
        "unique_rsids": unique_rsids,
        "unique_rsid_count": len(unique_rsids),
        "duplicate_rsids": duplicate_rsids,
        "edge_cases": {
            rsid: EDGE_CASE_RSIDS[rsid]
            for rsid in df[df["is_edge_case"]]["rsid"].unique()
            if rsid in EDGE_CASE_RSIDS
        },
    }

    return df, meta


def save_duplicate_report(meta: dict, output_path: str | Path) -> None:
    """Save a JSON file documenting which rsIDs appear in multiple categories."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "summary": f"{len(meta['duplicate_rsids'])} rsIDs appear in multiple categories",
        "duplicates": meta["duplicate_rsids"],
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    logger.info(f"Duplicate rsID report saved → {output_path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    import sys

    tsv = sys.argv[1] if len(sys.argv) > 1 else "data/TEST _ Genomics  - Sheet1.tsv"
    df, meta = load_snp_catalog(tsv)
    print(f"\n✓ Parsed {meta['total_rows']} rows, {meta['unique_rsid_count']} unique rsIDs")
    print(f"✓ Duplicates: {len(meta['duplicate_rsids'])} rsIDs across multiple categories")
    print(f"✓ Edge cases: {list(meta['edge_cases'].keys())}")
    print(df.head(10).to_string(index=False))
    save_duplicate_report(meta, "data/duplicate_rsids.json")
