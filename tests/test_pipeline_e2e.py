"""
tests/test_pipeline_e2e.py
--------------------------
End-to-end smoke test for the pipeline on a 5-SNP subset.
Uses mock NCBI and Gemini API responses to run offline.
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from snp_catalog import load_snp_catalog
from vcf_extractor import build_position_lookup, extract_variants
from report_generator import build_output_dataframe, save_csv

# ── Minimal fixtures ──────────────────────────────────────────────────────────

MINI_TSV = """\
Neurotransmitter\tCOMT\trs4680\tRegulates dopamine breakdown
Neurotransmitter\tBDNF\trs6265\tKey neuroplasticity gene
Nutrient_Absorption & Metabolism\tMTHFR\trs1801133\tReduced methylation efficiency
Gut Brain & Microbiome Resiliance\tGSTM1\tNull\tDetox gene deletion
ADHD\tCOMT\trs4680\tSlower dopamine breakdown (ADHD context)
"""

MINI_VCF = """\
##fileformat=VCFv4.2
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE
chr22\t19963748\t.\tA\tG\t50\tPASS\t.\tGT\t0/1
chr11\t27679916\t.\tG\tA\t60\tPASS\t.\tGT\t0/0
chr1\t11856378\t.\tC\tT\t70\tPASS\t.\tGT\t1/1
"""

# Mock resolved positions
RESOLVED_MOCK = {
    "rs4680":    {"rsid": "rs4680",    "chrom": "chr22", "pos": 19963748, "ref": "A", "alt": "G", "resolved": True},
    "rs6265":    {"rsid": "rs6265",    "chrom": "chr11", "pos": 27679916, "ref": "G", "alt": "A", "resolved": True},
    "rs1801133": {"rsid": "rs1801133", "chrom": "chr1",  "pos": 11856378, "ref": "C", "alt": "T", "resolved": True},
    "Null":      {"rsid": "Null",      "chrom": None,    "pos": None,     "ref": None, "alt": None, "resolved": False, "note": "Gene deletion"},
}


@pytest.fixture
def mini_tsv(tmp_path):
    f = tmp_path / "snps.tsv"
    f.write_text(MINI_TSV, encoding="utf-8")
    return f


@pytest.fixture
def mini_vcf(tmp_path):
    f = tmp_path / "test.vcf"
    f.write_text(MINI_VCF, encoding="utf-8")
    return f


# ── E2E Tests ─────────────────────────────────────────────────────────────────

def test_catalog_loads(mini_tsv):
    df, meta = load_snp_catalog(mini_tsv)
    assert len(df) == 5
    assert "rs4680" in meta["duplicate_rsids"]


def test_full_extraction_pipeline(mini_tsv, mini_vcf, tmp_path):
    """Run catalog → resolution (mocked) → extraction → report."""
    df, meta = load_snp_catalog(mini_tsv)
    lookup = build_position_lookup(df, RESOLVED_MOCK)
    results = extract_variants(mini_vcf, lookup)

    # VCF has 3 positions; rs4680 and rs1801133 should be found
    assert results["rs4680"]["found_in_vcf"] is True
    assert results["rs4680"]["genotype"] == "AG"
    assert results["rs6265"]["found_in_vcf"] is True
    assert results["rs6265"]["genotype"] == "GG"
    assert results["rs1801133"]["found_in_vcf"] is True
    assert results["rs1801133"]["genotype"] == "TT"
    assert "Null" not in results  # Null has no resolved position


def test_output_csv_has_all_rows(mini_tsv, mini_vcf, tmp_path):
    """CSV must have all 5 rows (including duplicate rs4680)."""
    df, meta = load_snp_catalog(mini_tsv)
    lookup = build_position_lookup(df, RESOLVED_MOCK)
    extraction = extract_variants(mini_vcf, lookup)

    # Simulate minimal interpreted_rows (skip AI)
    interp_rows = [
        {**v, "description": "", "is_edge_case": False, "edge_case_note": "",
         "interpretation": "Test interp", "confidence_level": "High",
         "mechanism": "Test", "ai_skipped": False, "ai_skip_reason": ""}
        for v in extraction.values()
    ]

    out_df = build_output_dataframe(df, interp_rows)
    assert len(out_df) == 5, f"Expected 5 rows in output, got {len(out_df)}"

    # Both COMT rows (Neurotransmitter + ADHD) should be present
    comt_rows = out_df[out_df["rsid"] == "rs4680"]
    assert len(comt_rows) == 2
    categories = set(comt_rows["category"])
    assert "Neurotransmitter" in categories
    assert "ADHD" in categories


def test_csv_saved_correctly(mini_tsv, mini_vcf, tmp_path):
    df, meta = load_snp_catalog(mini_tsv)
    lookup = build_position_lookup(df, RESOLVED_MOCK)
    extraction = extract_variants(mini_vcf, lookup)
    interp_rows = [
        {**v, "description": "", "is_edge_case": False, "edge_case_note": "",
         "interpretation": "Test", "confidence_level": "High",
         "mechanism": "Test", "ai_skipped": False, "ai_skip_reason": ""}
        for v in extraction.values()
    ]
    out_df = build_output_dataframe(df, interp_rows)
    csv_path = save_csv(out_df, tmp_path / "out")
    assert csv_path.exists()
    loaded = pd.read_csv(csv_path)
    assert len(loaded) == 5
    assert "interpretation" in loaded.columns
    assert "confidence_level" in loaded.columns
