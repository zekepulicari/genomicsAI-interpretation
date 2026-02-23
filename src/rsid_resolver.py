"""
rsid_resolver.py
----------------
Resolves rsIDs → chromosomal coordinates (chr, pos, ref, alt)
using the NCBI dbSNP Variation Services API.

Endpoint: https://api.ncbi.nlm.nih.gov/variation/v0/refsnp/{id}
This returns full placement data including SPDI coordinates on GRCh38.

Results are cached to data/rsid_positions.json to avoid repeated API calls.
"""

import json
import logging
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

NCBI_KEY = os.getenv("NCBI_KEY", "")
RATE_LIMIT_DELAY = 0.15 if NCBI_KEY else 0.35  # Variation API is separate from E-Utils

# NC_ accession → chromosome name mapping for GRCh38
NC_TO_CHROM = {
    f"NC_0000{str(i).zfill(2)}.11": str(i) for i in range(1, 23)
}
NC_TO_CHROM["NC_000023.11"] = "X"
NC_TO_CHROM["NC_000024.10"] = "Y"
# GRCh37 (fallback)
NC_TO_CHROM_37 = {
    f"NC_0000{str(i).zfill(2)}.10": str(i) for i in range(1, 23)
}
NC_TO_CHROM_37["NC_000023.10"] = "X"
NC_TO_CHROM_37["NC_000024.9"] = "Y"

# Known edge cases that will never resolve to a standard VCF position
UNRESOLVABLE_RSIDS = {
    "Null": "GSTM1 gene deletion — no rsID exists; requires CNV/deletion genotyping",
    "rs28363170": "DAT1 VNTR — insertion/deletion repeat, not a point variant; absent from standard VCF",
}


def _fetch_refsnp(rsid_num: str) -> dict | None:
    """Query NCBI Variation Services API for a single rsID number (without 'rs' prefix)."""
    url = f"https://api.ncbi.nlm.nih.gov/variation/v0/refsnp/{rsid_num}"
    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code == 404:
            logger.warning(f"rs{rsid_num}: Not found in dbSNP (404)")
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.warning(f"NCBI request failed for rs{rsid_num}: {e}")
        return None


def _parse_refsnp(data: dict, rsid: str) -> dict | None:
    """
    Extract chr, pos, ref, alt from NCBI Variation Services refSNP JSON.

    Looks for the GRCh38 primary top-level placement (is_ptlp=True, NC_ accession).
    Falls back to any GRCh38 NC_ placement if PTLP not found.
    """
    try:
        placements = data.get("primary_snapshot_data", {}).get("placements_with_allele", [])
        if not placements:
            logger.warning(f"{rsid}: No placements in response")
            return None

        # Find GRCh38 primary placement
        target_placement = None
        for p in placements:
            seq_id = p.get("seq_id", "")
            if seq_id in NC_TO_CHROM:
                if p.get("is_ptlp", False):
                    target_placement = p
                    break
                elif target_placement is None:
                    target_placement = p  # Fallback to first NC_ match

        if target_placement is None:
            # Try GRCh37
            for p in placements:
                seq_id = p.get("seq_id", "")
                if seq_id in NC_TO_CHROM_37:
                    target_placement = p
                    break

        if target_placement is None:
            logger.warning(f"{rsid}: No chromosomal (NC_) placement found")
            return None

        seq_id = target_placement["seq_id"]
        chrom = NC_TO_CHROM.get(seq_id, NC_TO_CHROM_37.get(seq_id, None))
        if chrom is None:
            logger.warning(f"{rsid}: Unknown accession {seq_id}")
            return None

        # Get alleles — first allele is REF, subsequent are ALT
        alleles = target_placement.get("alleles", [])
        if len(alleles) < 2:
            logger.warning(f"{rsid}: Less than 2 alleles in placement")
            return None

        # SPDI from the first allele (REF) gives position
        ref_spdi = alleles[0].get("allele", {}).get("spdi", {})
        position_0based = ref_spdi.get("position")
        ref_seq = ref_spdi.get("deleted_sequence", "")

        if position_0based is None:
            logger.warning(f"{rsid}: No position in SPDI")
            return None

        # ALT alleles — collect inserted sequences from non-ref alleles
        alt_seqs = []
        for a in alleles[1:]:
            a_spdi = a.get("allele", {}).get("spdi", {})
            ins = a_spdi.get("inserted_sequence", "")
            if ins and ins != ref_seq:
                alt_seqs.append(ins)

        alt_str = ",".join(alt_seqs) if alt_seqs else ref_seq  # Fallback

        # VCF uses 1-based positions
        pos_1based = int(position_0based) + 1

        return {
            "rsid": rsid,
            "chrom": f"chr{chrom}",
            "pos": pos_1based,
            "ref": ref_seq,
            "alt": alt_str,
            "seq_accession": seq_id,
            "resolved": True,
        }

    except (KeyError, TypeError, ValueError, IndexError) as e:
        logger.warning(f"{rsid}: Failed to parse response — {e}")
        return None


def resolve_rsids(
    rsids: list[str],
    cache_path: str | Path = "data/rsid_positions.json",
) -> dict[str, dict]:
    """
    Resolve a list of rsIDs to chromosomal positions.

    Returns a dict: rsid → {chrom, pos, ref, alt, resolved, note}
    Uses cache to avoid re-querying already resolved rsIDs.
    """
    cache_path = Path(cache_path)
    cache: dict[str, dict] = {}

    # Load existing cache
    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)
        logger.info(f"Loaded {len(cache)} cached rsID resolutions from {cache_path}")

    results: dict[str, dict] = {}
    to_fetch: list[str] = []

    for rsid in rsids:
        rsid = rsid.strip()

        # Handle known unresolvables immediately
        if rsid in UNRESOLVABLE_RSIDS:
            results[rsid] = {
                "rsid": rsid,
                "chrom": None,
                "pos": None,
                "ref": None,
                "alt": None,
                "resolved": False,
                "note": UNRESOLVABLE_RSIDS[rsid],
            }
            continue

        # Check cache
        if rsid in cache:
            results[rsid] = cache[rsid]
            continue

        to_fetch.append(rsid)

    if to_fetch:
        logger.info(f"Fetching {len(to_fetch)} rsIDs from NCBI Variation Services...")
        for i, rsid in enumerate(to_fetch):
            if not rsid.startswith("rs"):
                logger.warning(f"Skipping non-standard rsID format: {rsid}")
                results[rsid] = {
                    "rsid": rsid, "chrom": None, "pos": None, "ref": None,
                    "alt": None, "resolved": False,
                    "note": f"Non-standard rsID format: {rsid}",
                }
                cache[rsid] = results[rsid]
                continue

            rsid_num = rsid[2:]  # strip 'rs' prefix
            data = _fetch_refsnp(rsid_num)

            if data:
                parsed = _parse_refsnp(data, rsid)
                if parsed:
                    results[rsid] = parsed
                    cache[rsid] = parsed
                    logger.info(f"  [{i+1}/{len(to_fetch)}] {rsid} → {parsed['chrom']}:{parsed['pos']} ({parsed['ref']}>{parsed['alt']})")
                else:
                    entry = {
                        "rsid": rsid, "chrom": None, "pos": None, "ref": None,
                        "alt": None, "resolved": False,
                        "note": "NCBI returned data but position could not be parsed",
                    }
                    results[rsid] = entry
                    cache[rsid] = entry
            else:
                entry = {
                    "rsid": rsid, "chrom": None, "pos": None, "ref": None,
                    "alt": None, "resolved": False,
                    "note": "NCBI API request failed or rsID not found",
                }
                results[rsid] = entry
                cache[rsid] = entry

            # Rate limiting
            time.sleep(RATE_LIMIT_DELAY)

            # Progress checkpoint every 20
            if (i + 1) % 20 == 0:
                logger.info(f"  ... {i+1}/{len(to_fetch)} done")

        # Save updated cache
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
        logger.info(f"Cache updated → {cache_path}")

    resolved_count = sum(1 for v in results.values() if v.get("resolved"))
    logger.info(f"Resolved {resolved_count}/{len(results)} rsIDs to chromosomal positions")
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    test_rsids = ["rs4680", "rs1801133", "rs6265", "rs762551", "Null", "rs28363170"]
    results = resolve_rsids(test_rsids, cache_path="data/test_positions.json")
    for rsid, data in results.items():
        if data["resolved"]:
            print(f"✓ {rsid}: {data['chrom']}:{data['pos']} {data['ref']}>{data['alt']}")
        else:
            print(f"✗ {rsid}: {data.get('note', 'unresolved')}")
