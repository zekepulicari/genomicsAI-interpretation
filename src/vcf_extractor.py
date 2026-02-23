"""
vcf_extractor.py
----------------
Streams the 1.5GB VCF file and extracts genotypes for SNPs in our lookup table.

Strategy:
- Build a position-based lookup dict {(chrom, pos): [snp_info, ...]}
- Parse VCF line by line (memory-efficient)
- Extract GT field, derive genotype string and zygosity
- Track any SNPs not found in the VCF
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Genotype code → zygosity label
def _zygosity(gt_alleles: list[str], ref: str, alt_alleles: list[str]) -> str:
    """Determine zygosity from GT allele indices."""
    if len(gt_alleles) < 2:
        return "hemizygous"
    a, b = gt_alleles[0], gt_alleles[1]
    if a == "." or b == ".":
        return "missing"
    if a == b:
        if a == "0":
            return "homozygous ref"
        return "homozygous alt"
    return "heterozygous"


def _decode_genotype(gt_str: str, ref: str, alt_list: list[str]) -> tuple[str, str]:
    """
    Decode a GT string (e.g. '0/1', '1|1', '0/0') into:
      - genotype:  nucleotide representation (e.g. 'AG', 'GG', 'AA')
      - zygosity: 'homozygous ref' / 'heterozygous' / 'homozygous alt' / 'missing'

    Returns ('N/A', 'missing') if GT cannot be decoded.
    """
    if not gt_str or gt_str in (".", "./.", ".|."):
        return "N/A", "missing"

    separator = "|" if "|" in gt_str else "/"
    parts = gt_str.split(separator)

    allele_map = ["0"] + alt_list  # index 0 → ref, 1 → first alt, etc.
    nucleotides = []
    raw_indices = []

    for p in parts:
        if p == ".":
            nucleotides.append("N")
            raw_indices.append(".")
            continue
        idx = int(p)
        raw_indices.append(p)
        if idx == 0:
            nucleotides.append(ref)
        elif idx <= len(alt_list):
            nucleotides.append(alt_list[idx - 1])
        else:
            nucleotides.append("?")

    genotype = "/".join(nucleotides) if any(len(n) > 1 for n in nucleotides) else "".join(nucleotides)
    zygosity = _zygosity(raw_indices, ref, alt_list)
    return genotype, zygosity


def extract_variants(
    vcf_path: str | Path,
    position_lookup: dict[tuple[str, int], list[dict]],
) -> dict[str, dict]:
    """
    Stream-parse VCF and extract genotypes for all positions in position_lookup.

    Args:
        vcf_path: Path to the VCF file.
        position_lookup: dict mapping (chrom, pos) → list of snp dicts
                         Each snp dict must have at least: rsid, gene, category

    Returns:
        results: dict mapping rsid → extraction result dict with keys:
                 {rsid, chrom, pos, ref, alt, genotype, zygosity, found_in_vcf}
    """
    vcf_path = Path(vcf_path)
    if not vcf_path.exists():
        raise FileNotFoundError(f"VCF not found: {vcf_path}")

    # Build a flat set of target positions for fast lookup
    target_positions: set[tuple[str, int]] = set(position_lookup.keys())

    results: dict[str, dict] = {}
    found_positions: set[tuple[str, int]] = set()

    logger.info(f"Scanning VCF for {len(target_positions)} target positions...")

    with open(vcf_path, "r", encoding="utf-8") as f:
        sample_col_idx = None
        header_fields = None

        for line_num, line in enumerate(f, start=1):
            line = line.rstrip("\n")

            # Skip meta-info lines
            if line.startswith("##"):
                continue

            # Parse header
            if line.startswith("#CHROM"):
                header_fields = line.lstrip("#").split("\t")
                if "FORMAT" in header_fields:
                    format_idx = header_fields.index("FORMAT")
                    # Sample column is right after FORMAT
                    sample_col_idx = format_idx + 1
                logger.debug(f"VCF header found at line {line_num}, sample column: {sample_col_idx}")
                continue

            if not target_positions:
                break  # All found, no need to scan further

            # Parse data line
            cols = line.split("\t")
            if len(cols) < 9:
                continue

            chrom = cols[0]
            try:
                pos = int(cols[1])
            except ValueError:
                continue

            key = (chrom, pos)
            if key not in target_positions:
                continue

            # Match found!
            ref = cols[3]
            alt_str = cols[4]
            alt_list = [a.strip() for a in alt_str.split(",")]
            qual = cols[5]
            filter_val = cols[6]
            format_str = cols[8]
            sample_str = cols[sample_col_idx] if sample_col_idx and sample_col_idx < len(cols) else ""

            # Extract GT from FORMAT:SAMPLE
            gt = "."
            if format_str and sample_str:
                fmt_fields = format_str.split(":")
                smp_fields = sample_str.split(":")
                if "GT" in fmt_fields:
                    gt_idx = fmt_fields.index("GT")
                    gt = smp_fields[gt_idx] if gt_idx < len(smp_fields) else "."

            genotype, zygosity = _decode_genotype(gt, ref, alt_list)

            # Store result for every SNP at this position
            for snp in position_lookup[key]:
                rsid = snp["rsid"]
                results[rsid] = {
                    "rsid": rsid,
                    "gene": snp.get("gene", ""),
                    "category": snp.get("category", ""),
                    "chrom": chrom,
                    "pos": pos,
                    "ref": ref,
                    "alt": alt_str,
                    "gt_raw": gt,
                    "genotype": genotype,
                    "zygosity": zygosity,
                    "qual": qual,
                    "filter": filter_val,
                    "found_in_vcf": True,
                }

            found_positions.add(key)
            target_positions.discard(key)

            if line_num % 1_000_000 == 0:
                logger.info(f"  Scanned {line_num:,} lines... ({len(found_positions)} SNPs found so far)")

    # Mark not-found positions as homozygous reference
    # In a WGS VCF (non-gVCF), absence of a position means the individual
    # is homozygous for the reference allele at that locus.
    for key in target_positions:
        for snp in position_lookup[key]:
            rsid = snp["rsid"]
            if rsid not in results:
                # Look up the ref allele from resolved data
                ref_allele = snp.get("ref_allele", "")
                genotype = f"{ref_allele}{ref_allele}" if ref_allele else "Ref/Ref"
                results[rsid] = {
                    "rsid": rsid,
                    "gene": snp.get("gene", ""),
                    "category": snp.get("category", ""),
                    "chrom": key[0],
                    "pos": key[1],
                    "ref": ref_allele or None,
                    "alt": None,
                    "gt_raw": "0/0",
                    "genotype": genotype,
                    "zygosity": "homozygous ref",
                    "qual": None,
                    "filter": None,
                    "found_in_vcf": False,
                    "inferred_hom_ref": True,
                }

    logger.info(
        f"Extraction complete: {len(found_positions)}/{len(found_positions) + len(target_positions)} "
        f"positions found in VCF"
    )
    return results


def build_position_lookup(
    catalog_df,
    resolved_positions: dict[str, dict],
) -> dict[tuple[str, int], list[dict]]:
    """
    Build the position lookup dict from the catalog DataFrame and resolved positions.

    Returns: {(chrom, pos): [list of snp dicts]}
    """
    lookup: dict[tuple[str, int], list[dict]] = {}

    seen_rsids: set[str] = set()

    for _, row in catalog_df.iterrows():
        rsid = row["rsid"]

        # Skip if already added this rsID (same SNP, multiple categories)
        if rsid in seen_rsids:
            continue
        seen_rsids.add(rsid)

        pos_info = resolved_positions.get(rsid, {})
        if not pos_info.get("resolved"):
            continue

        chrom = pos_info["chrom"]
        pos = pos_info["pos"]
        if chrom is None or pos is None:
            continue

        key = (chrom, pos)
        if key not in lookup:
            lookup[key] = []

        lookup[key].append({
            "rsid": rsid,
            "gene": row["gene"],
            "category": row["category"],
            "ref_allele": pos_info.get("ref", ""),
        })

    logger.info(f"Built position lookup with {len(lookup)} unique chromosomal positions")
    return lookup
