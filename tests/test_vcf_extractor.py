"""
tests/test_vcf_extractor.py
-----------------------------
Tests for vcf_extractor.py — GT decoding, position lookup, result structure.
"""
import tempfile
from pathlib import Path

import pytest
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vcf_extractor import _decode_genotype, _zygosity, extract_variants, build_position_lookup

# ── Unit tests for genotype decoding ─────────────────────────────────────────

@pytest.mark.parametrize("gt,ref,alts,expected_gt,expected_zyg", [
    ("0/0", "A", ["G"],  "AA", "homozygous ref"),
    ("0/1", "A", ["G"],  "AG", "heterozygous"),
    ("1/1", "A", ["G"],  "GG", "homozygous alt"),
    ("1|0", "C", ["T"],  "TC", "heterozygous"),
    ("./.", "A", ["G"],  "N/A", "missing"),
    (".",   "A", ["G"],  "N/A", "missing"),
])
def test_decode_genotype(gt, ref, alts, expected_gt, expected_zyg):
    genotype, zygosity = _decode_genotype(gt, ref, alts)
    assert genotype == expected_gt, f"GT {gt}: expected genotype '{expected_gt}', got '{genotype}'"
    assert zygosity == expected_zyg, f"GT {gt}: expected zygosity '{expected_zyg}', got '{zygosity}'"


def test_decode_multiallelic():
    """Multi-allelic ALT: GT 1/2 means first alt / second alt."""
    genotype, zygosity = _decode_genotype("1/2", "A", ["G", "T"])
    assert genotype == "GT"
    assert zygosity == "heterozygous"


# ── Minimal test VCF fixture ──────────────────────────────────────────────────
MINI_VCF = """\
##fileformat=VCFv4.2
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read Depth">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE
chr22\t19963748\t.\tA\tG\t50\tPASS\t.\tGT:DP\t0/1:30
chr1\t11856378\t.\tC\tT\t60\tPASS\t.\tGT:DP\t1/1:25
chr7\t117548628\t.\tG\tA\t40\tPASS\t.\tGT:DP\t0/0:20
"""


@pytest.fixture
def mini_vcf(tmp_path):
    vcf = tmp_path / "test.vcf"
    vcf.write_text(MINI_VCF, encoding="utf-8")
    return vcf


def test_extract_heterozygous(mini_vcf):
    """chr22:19963748 is heterozygous A/G."""
    lookup = {
        ("chr22", 19963748): [{"rsid": "rs4680", "gene": "COMT", "category": "Neurotransmitter"}]
    }
    results = extract_variants(mini_vcf, lookup)
    assert "rs4680" in results
    r = results["rs4680"]
    assert r["found_in_vcf"] is True
    assert r["genotype"] == "AG"
    assert r["zygosity"] == "heterozygous"
    assert r["ref"] == "A"
    assert r["alt"] == "G"


def test_extract_homozygous_alt(mini_vcf):
    """chr1:11856378 is homozygous alt T/T."""
    lookup = {
        ("chr1", 11856378): [{"rsid": "rs1801133", "gene": "MTHFR", "category": "Nutrient"}]
    }
    results = extract_variants(mini_vcf, lookup)
    assert results["rs1801133"]["zygosity"] == "homozygous alt"
    assert results["rs1801133"]["genotype"] == "TT"


def test_extract_homozygous_ref(mini_vcf):
    """chr7:117548628 is homozygous ref G/G."""
    lookup = {
        ("chr7", 117548628): [{"rsid": "rs762551", "gene": "CYP1A2", "category": "Detox"}]
    }
    results = extract_variants(mini_vcf, lookup)
    assert results["rs762551"]["zygosity"] == "homozygous ref"
    assert results["rs762551"]["genotype"] == "GG"


def test_not_found_in_vcf(mini_vcf):
    """A position not in the VCF should be inferred as homozygous ref."""
    lookup = {
        ("chr5", 99999999): [{"rsid": "rs999", "gene": "FAKE", "category": "Test"}]
    }
    results = extract_variants(mini_vcf, lookup)
    assert results["rs999"]["found_in_vcf"] is False
    assert results["rs999"]["genotype"] == "Ref/Ref"
    assert results["rs999"]["zygosity"] == "homozygous ref"
    assert results["rs999"]["inferred_hom_ref"] is True


def test_multiple_snps_same_position(mini_vcf):
    """Two SNPs mapped to same position both get the same genotype."""
    lookup = {
        ("chr22", 19963748): [
            {"rsid": "rs4680", "gene": "COMT", "category": "Neurotransmitter"},
            {"rsid": "rs4680_dup", "gene": "COMT", "category": "ADHD"},
        ]
    }
    results = extract_variants(mini_vcf, lookup)
    assert results["rs4680"]["genotype"] == results["rs4680_dup"]["genotype"]


def test_build_position_lookup():
    """build_position_lookup should deduplicate by rsID for the lookup."""
    import pandas as pd
    df = pd.DataFrame([
        {"rsid": "rs4680", "gene": "COMT", "category": "Neurotransmitter", "is_edge_case": False, "edge_case_note": ""},
        {"rsid": "rs4680", "gene": "COMT", "category": "ADHD", "is_edge_case": False, "edge_case_note": ""},
        {"rsid": "rs6265", "gene": "BDNF", "category": "Neurotransmitter", "is_edge_case": False, "edge_case_note": ""},
    ])
    resolved = {
        "rs4680": {"resolved": True, "chrom": "chr22", "pos": 19963748, "ref": "A", "alt": "G"},
        "rs6265": {"resolved": True, "chrom": "chr11", "pos": 27679916, "ref": "G", "alt": "A"},
    }
    lookup = build_position_lookup(df, resolved)
    assert ("chr22", 19963748) in lookup
    assert ("chr11", 27679916) in lookup
    # rs4680 should only appear once in lookup (deduplicated)
    assert len(lookup[("chr22", 19963748)]) == 1
