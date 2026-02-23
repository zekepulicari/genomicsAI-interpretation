"""
tests/test_snp_catalog.py
--------------------------
Tests for snp_catalog.py — TSV parsing, normalization, duplicate tracking.
"""
import json
import tempfile
from pathlib import Path

import pytest
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from snp_catalog import load_snp_catalog, save_duplicate_report

# ── Minimal sample TSV that mirrors the real file structure ──────────────────
SAMPLE_TSV_CONTENT = """\
CYP2D6\trs3892097\tImpacts drug metabolism — especially psych meds
CYP2C19\trs4244285\tImportant for SSRIs and clopidogrel
SLCO1B1\trs4149056\tStatin sensitivity
Neurotransmitter\tCOMT\trs4680\tRegulates dopamine breakdown
Neurotransmitter\tBDNF\trs6265\tKey neuroplasticity gene
ADHD\tCOMT\trs4680\tSlower dopamine breakdown (duplicate category)
"""


@pytest.fixture
def sample_tsv(tmp_path):
    tsv_file = tmp_path / "test_snps.tsv"
    tsv_file.write_text(SAMPLE_TSV_CONTENT, encoding="utf-8")
    return tsv_file


def test_total_rows_parsed(sample_tsv):
    df, meta = load_snp_catalog(sample_tsv)
    assert len(df) == 6, f"Expected 6 rows, got {len(df)}"


def test_first_three_rows_fixed(sample_tsv):
    """Misaligned rows (3 cols) must get the Pediatric Pharmacogenomics category."""
    df, meta = load_snp_catalog(sample_tsv)
    first_three = df.head(3)
    assert (first_three["category"] == "Pediatric Pharmacogenomics").all(), (
        "First 3 rows should be assigned to 'Pediatric Pharmacogenomics'"
    )


def test_normalized_columns(sample_tsv):
    df, _ = load_snp_catalog(sample_tsv)
    for col in ["row_id", "category", "gene", "rsid", "description"]:
        assert col in df.columns, f"Missing column: {col}"


def test_duplicate_tracking(sample_tsv):
    """rs4680 appears in Neurotransmitter AND ADHD — must be flagged."""
    df, meta = load_snp_catalog(sample_tsv)
    dup_rsids = meta["duplicate_rsids"]
    assert "rs4680" in dup_rsids, "rs4680 should be flagged as a duplicate"
    assert "Neurotransmitter" in dup_rsids["rs4680"]
    assert "ADHD" in dup_rsids["rs4680"]


def test_nonduplicate_not_flagged(sample_tsv):
    """rs3892097 appears only once — must NOT be flagged."""
    _, meta = load_snp_catalog(sample_tsv)
    assert "rs3892097" not in meta["duplicate_rsids"]


def test_edge_case_flagging(sample_tsv):
    """GSTM1 Null rsID should be flagged as edge case if present."""
    content = SAMPLE_TSV_CONTENT + "Gut Brain\tGSTM1\tNull\tDetox gene deletion\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".tsv", delete=False, encoding="utf-8") as f:
        f.write(content)
        tsv = Path(f.name)
    df, meta = load_snp_catalog(tsv)
    null_rows = df[df["rsid"] == "Null"]
    assert not null_rows.empty, "Null rsID row should be present"
    assert null_rows.iloc[0]["is_edge_case"], "Null rsID should be flagged as edge case"
    tsv.unlink()


def test_unique_rsids_exclude_null(sample_tsv):
    """'Null' should be excluded from the unique rsIDs list sent to the resolver."""
    content = SAMPLE_TSV_CONTENT + "Gut Brain\tGSTM1\tNull\tDetox gene deletion\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".tsv", delete=False, encoding="utf-8") as f:
        f.write(content)
        tsv = Path(f.name)
    _, meta = load_snp_catalog(tsv)
    assert "Null" not in meta["unique_rsids"], "'Null' should not be in unique_rsids"
    tsv.unlink()


def test_save_duplicate_report(sample_tsv, tmp_path):
    _, meta = load_snp_catalog(sample_tsv)
    out = tmp_path / "dups.json"
    save_duplicate_report(meta, out)
    assert out.exists()
    data = json.loads(out.read_text())
    assert "duplicates" in data
    assert "rs4680" in data["duplicates"]
