"""
app.py
------
Streamlit interactive dashboard for the Genomics AI Interpretation Pipeline.

Run with:
    streamlit run src/app.py

Or inside Docker:
    docker-compose up app
"""

import json
import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from snp_catalog import load_snp_catalog, save_duplicate_report
from rsid_resolver import resolve_rsids
from vcf_extractor import build_position_lookup, extract_variants
from ai_interpreter import interpret_variants
from report_generator import build_output_dataframe, save_csv, save_markdown_report

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Genomics AI Interpreter",
    page_icon="🧬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

    .main { background: #0f1117; }

    .metric-card {
        background: linear-gradient(135deg, #1e2130, #252940);
        border: 1px solid #2d3144;
        border-radius: 12px;
        padding: 20px;
        text-align: center;
    }
    .metric-value { font-size: 2.2rem; font-weight: 700; color: #4fd1c5; margin: 0; }
    .metric-label { font-size: 0.85rem; color: #8892a4; margin-top: 4px; }

    .confidence-High    { color: #48bb78; font-weight: 600; }
    .confidence-Moderate { color: #ed8936; font-weight: 600; }
    .confidence-Low     { color: #fc8181; font-weight: 600; }
    .confidence-Conflicting { color: #a78bfa; font-weight: 600; }

    .badge {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 999px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .badge-duplicate { background: #2d3748; color: #90cdf4; }
    .badge-edge { background: #3d2a0f; color: #f6ad55; }

    div[data-testid="stProgress"] > div { background-color: #4fd1c5 !important; }

    .stButton > button {
        background: linear-gradient(135deg, #4fd1c5, #63b3ed);
        color: #0f1117;
        font-weight: 600;
        border: none;
        border-radius: 8px;
        padding: 0.5rem 1.5rem;
    }
    .stButton > button:hover { opacity: 0.9; transform: translateY(-1px); }
</style>
""", unsafe_allow_html=True)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🧬 Genomics AI")
    st.caption("Pediatric Genomic Interpretation Pipeline")
    st.divider()

    st.subheader("⚙️ Configuration")
    default_vcf = "data/sample_nucleus_dna_download_vcf_NU-DRSQ-5692_copy.vcf"
    default_snps = "data/TEST _ Genomics  - Sheet1.tsv"
    default_output = "output/"

    vcf_path = st.text_input("VCF File Path", value=default_vcf)
    snp_path = st.text_input("SNP Catalog (TSV)", value=default_snps)
    output_dir = st.text_input("Output Directory", value=default_output)

    st.divider()
    st.subheader("🚀 Pipeline Steps")
    run_resolve = st.checkbox("Resolve rsIDs (NCBI)", value=True)
    run_extract = st.checkbox("Extract VCF Genotypes", value=True)
    run_ai = st.checkbox("AI Interpretation (Gemini)", value=True)

    run_btn = st.button("▶ Run Pipeline", use_container_width=True)

    st.divider()
    st.caption("Uses cached intermediates when available.")


# ── Header ────────────────────────────────────────────────────────────────────
st.title("🧬 Pediatric Genomic Interpretation")
st.markdown(
    "Extracts SNP genotypes from whole-genome VCF and generates "
    "AI-powered functional summaries for a child's genomic profile."
)
st.divider()


# ── State management ──────────────────────────────────────────────────────────
if "results_df" not in st.session_state:
    st.session_state.results_df = None
if "pipeline_log" not in st.session_state:
    st.session_state.pipeline_log = []


# ── Pipeline runner ───────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_catalog(tsv_path: str):
    return load_snp_catalog(tsv_path)


def run_pipeline():
    log = []
    progress = st.progress(0, text="Starting pipeline...")
    status = st.empty()

    try:
        # Stage 1: Catalog
        status.info("📋 Stage 1/4 — Loading SNP Catalog...")
        catalog_df, meta = load_catalog(snp_path)
        save_duplicate_report(meta, Path("data/duplicate_rsids.json"))
        log.append(f"✓ Catalog: {meta['total_rows']} rows, {meta['unique_rsid_count']} unique rsIDs")
        progress.progress(15, text="Catalog loaded...")

        # Stage 2: Resolve
        cache_path = Path("data/rsid_positions.json")
        if run_resolve:
            status.info("🔍 Stage 2/4 — Resolving rsIDs via NCBI dbSNP...")
            resolved = resolve_rsids(meta["unique_rsids"], cache_path=cache_path)
            n_res = sum(1 for v in resolved.values() if v.get("resolved"))
            log.append(f"✓ Resolved {n_res}/{len(meta['unique_rsids'])} rsIDs")
        elif cache_path.exists():
            with open(cache_path) as f:
                resolved = json.load(f)
            log.append(f"⏭  rsID resolution skipped — loaded {len(resolved)} from cache")
        else:
            st.error("rsID cache not found. Please run with 'Resolve rsIDs' enabled first.")
            return
        progress.progress(40, text="rsIDs resolved...")

        # Stage 3: Extract
        extraction_cache = Path("data/extraction_results.json")
        if run_extract:
            status.info("🧬 Stage 3/4 — Extracting genotypes from VCF (this takes 1–3 min)...")
            position_lookup = build_position_lookup(catalog_df, resolved)
            extraction_results = extract_variants(vcf_path, position_lookup)
            with open(extraction_cache, "w") as f:
                json.dump(extraction_results, f, indent=2)
            n_found = sum(1 for v in extraction_results.values() if v.get("found_in_vcf"))
            log.append(f"✓ {n_found}/{len(extraction_results)} SNPs found in VCF")
        elif extraction_cache.exists():
            with open(extraction_cache) as f:
                extraction_results = json.load(f)
            log.append(f"⏭  VCF extraction skipped — loaded {len(extraction_results)} from cache")
        else:
            st.error("Extraction cache not found. Run with 'Extract VCF Genotypes' enabled first.")
            return
        progress.progress(65, text="VCF extraction complete...")

        # Stage 4: AI
        seen = set()
        rows = []
        for _, r in catalog_df.iterrows():
            rid = r["rsid"]
            if rid in seen:
                continue
            seen.add(rid)
            vcf_d = extraction_results.get(rid, {})
            rows.append({
                "rsid": rid, "gene": r["gene"], "category": r["category"],
                "description": r["description"],
                "is_edge_case": r.get("is_edge_case", False),
                "edge_case_note": r.get("edge_case_note", ""),
                **vcf_d,
            })

        if run_ai:
            status.info("🤖 Stage 4/4 — AI Interpretation via Gemini...")
            interpreted = interpret_variants(rows)
        else:
            interpreted = [
                {**r, "interpretation": "AI skipped", "confidence_level": "N/A",
                 "mechanism": "N/A", "ai_skipped": True, "ai_skip_reason": "Disabled in UI"}
                for r in rows
            ]
        log.append(f"✓ {sum(1 for r in interpreted if not r.get('ai_skipped'))} SNPs interpreted")
        progress.progress(90, text="Interpretation complete...")

        # Stage 5: Report
        status.info("📄 Generating reports...")
        out_df = build_output_dataframe(catalog_df, interpreted)
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        save_csv(out_df, output_dir)
        save_markdown_report(out_df, output_dir)
        log.append(f"✓ Reports saved to {output_dir}")

        progress.progress(100, text="Pipeline complete!")
        status.success("✅ Pipeline complete!")
        st.session_state.results_df = out_df
        st.session_state.pipeline_log = log

    except Exception as e:
        progress.empty()
        status.error(f"❌ Pipeline error: {e}")
        raise


if run_btn:
    run_pipeline()

# Also try to load existing results
if st.session_state.results_df is None:
    csv_path = Path(output_dir) / "genomics_report.csv"
    if csv_path.exists():
        st.session_state.results_df = pd.read_csv(csv_path)


# ── Results Dashboard ─────────────────────────────────────────────────────────
df: pd.DataFrame | None = st.session_state.results_df

if df is not None:
    # ── Metric cards ──────────────────────────────────────────────────────────
    st.subheader("📊 Overview")
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.markdown(f"""<div class="metric-card">
            <p class="metric-value">{len(df)}</p>
            <p class="metric-label">Total SNPs</p></div>""", unsafe_allow_html=True)
    with c2:
        n_found = df["found_in_vcf"].sum() if "found_in_vcf" in df.columns else 0
        st.markdown(f"""<div class="metric-card">
            <p class="metric-value">{n_found}</p>
            <p class="metric-label">Found in VCF</p></div>""", unsafe_allow_html=True)
    with c3:
        n_high = (df["confidence_level"] == "High").sum()
        st.markdown(f"""<div class="metric-card">
            <p class="metric-value" style="color:#48bb78">{n_high}</p>
            <p class="metric-label">High Confidence</p></div>""", unsafe_allow_html=True)
    with c4:
        n_het = (df["zygosity"] == "heterozygous").sum()
        st.markdown(f"""<div class="metric-card">
            <p class="metric-value" style="color:#63b3ed">{n_het}</p>
            <p class="metric-label">Heterozygous</p></div>""", unsafe_allow_html=True)
    with c5:
        n_dup = df["is_duplicate"].sum() if "is_duplicate" in df.columns else 0
        st.markdown(f"""<div class="metric-card">
            <p class="metric-value" style="color:#90cdf4">{n_dup}</p>
            <p class="metric-label">Duplicate Rows</p></div>""", unsafe_allow_html=True)

    st.divider()

    # ── Filters ───────────────────────────────────────────────────────────────
    st.subheader("🔍 Explore Variants")
    col1, col2, col3 = st.columns([2, 2, 2])

    with col1:
        categories = ["All"] + sorted(df["category"].unique().tolist())
        sel_cat = st.selectbox("Category", categories)

    with col2:
        confidences = ["All"] + sorted(df["confidence_level"].dropna().unique().tolist())
        sel_conf = st.selectbox("Confidence Level", confidences)

    with col3:
        zygosities = ["All"] + sorted(df["zygosity"].dropna().unique().tolist())
        sel_zyg = st.selectbox("Zygosity", zygosities)

    filtered = df.copy()
    if sel_cat != "All":
        filtered = filtered[filtered["category"] == sel_cat]
    if sel_conf != "All":
        filtered = filtered[filtered["confidence_level"] == sel_conf]
    if sel_zyg != "All":
        filtered = filtered[filtered["zygosity"] == sel_zyg]

    show_cols = ["category", "gene", "rsid", "genotype", "zygosity",
                 "interpretation", "confidence_level", "mechanism"]
    available = [c for c in show_cols if c in filtered.columns]

    st.dataframe(
        filtered[available].reset_index(drop=True),
        use_container_width=True,
        height=420,
        column_config={
            "confidence_level": st.column_config.TextColumn("Confidence"),
            "interpretation": st.column_config.TextColumn("Interpretation", width="large"),
        },
    )
    st.caption(f"Showing {len(filtered)} of {len(df)} rows")

    # ── Downloads ─────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("📥 Download Outputs")
    dl1, dl2 = st.columns(2)

    csv_path = Path(output_dir) / "genomics_report.csv"
    md_path = Path(output_dir) / "genomics_report.md"

    with dl1:
        if csv_path.exists():
            st.download_button(
                label="⬇️ Download CSV Report",
                data=csv_path.read_bytes(),
                file_name="genomics_report.csv",
                mime="text/csv",
                use_container_width=True,
            )
    with dl2:
        if md_path.exists():
            st.download_button(
                label="⬇️ Download Markdown Report",
                data=md_path.read_bytes(),
                file_name="genomics_report.md",
                mime="text/markdown",
                use_container_width=True,
            )

    # ── Pipeline log ──────────────────────────────────────────────────────────
    if st.session_state.pipeline_log:
        with st.expander("📋 Pipeline Log"):
            for entry in st.session_state.pipeline_log:
                st.text(entry)
else:
    st.info(
        "👈 Configure the paths in the sidebar and click **▶ Run Pipeline** to begin.\n\n"
        "If you've already run the CLI pipeline, the results will also load automatically "
        f"from `{output_dir}genomics_report.csv`."
    )
