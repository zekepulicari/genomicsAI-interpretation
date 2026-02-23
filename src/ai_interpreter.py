"""
ai_interpreter.py
-----------------
Generates 1-sentence functional summaries for each SNP using the Gemini API.

Uses the prompt template from .agents/rules/bioinformatics-ai.md:
- No diagnosis language
- Mechanism-focused, parent-friendly
- Returns JSON: {interpretation, confidence_level, mechanism}

Includes batch processing, rate limiting, and retry logic.
"""

import json
import logging
import os
import time
from pathlib import Path

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("DEFAULT_GEMINI_KEY", "")
MODEL_NAME = "gemini-2.0-flash"

# Prompt template based on .agents/rules/bioinformatics-ai.md
SYSTEM_PROMPT = """You are a Lead Bioinformatics AI Agent specializing in functional genomics and systems biology. Your goal is to provide high-precision, 1-sentence functional interpretations of specific genetic variants (SNPs) for a pediatric (childhood) genomic profile.

You are interpreting data for a "life-stage aware" engine. The audience is parents. Focus on biological mechanisms (e.g., enzyme speed, nutrient transport, receptor sensitivity) rather than disease or identity.

CONSTRAINTS (CRITICAL):
1. NO DIAGNOSIS: Never use words like "disease," "disorder," "risk of," or "patient."
2. NO IDENTITY PREDICTION: Avoid saying "The child will be..." or "This makes them..."
3. ONE SENTENCE: Provide exactly one clear, parent-friendly sentence.
4. MECHANISM OVER MAPPING: Focus on what the gene *does* (e.g., "slower dopamine clearance") rather than just a trait association.
5. EPISTEMIC DISCIPLINE: If a variant is purely speculative or has conflicting GWAS data, mark it as "Conflicting" or "Low" confidence.

Return ONLY a valid JSON object with these keys (no markdown, no extra text):
{
  "interpretation": "<1-sentence functional summary>",
  "confidence_level": "<High | Moderate | Low | Conflicting>",
  "mechanism": "<specific biological pathway, e.g. Mitochondrial ATP production>"
}"""

USER_PROMPT_TEMPLATE = """Gene: {gene}
SNP: {rsid}
Genotype: {genotype}
Zygosity: {zygosity}
Background: {description}

Provide a functional interpretation of this genotype for a child's genomic profile."""

# SNPs that are inappropriate to interpret
SKIP_INTERPRETATION_REASONS = {
    "missing": "Genotype data missing or low quality in VCF",
    "N/A": "Variant not extractable from standard VCF",
    "hom_ref": "Wild-type / reference genotype — this individual carries the common ancestral allele at this position",
}


def _build_prompt(row: dict) -> str:
    return USER_PROMPT_TEMPLATE.format(
        gene=row.get("gene", "Unknown"),
        rsid=row.get("rsid", "Unknown"),
        genotype=row.get("genotype", "Unknown"),
        zygosity=row.get("zygosity", "Unknown"),
        description=row.get("description", "No description available"),
    )


def _call_gemini(client, prompt: str, max_retries: int = 5) -> dict | None:
    """Call Gemini API with retry logic. Returns parsed JSON dict or None."""
    for attempt in range(max_retries):
        try:
            response = client.generate_content(
                [SYSTEM_PROMPT, prompt],
                generation_config=genai.GenerationConfig(
                    temperature=0.3,
                    max_output_tokens=512,
                ),
            )
            raw_text = response.text.strip()

            # Strip markdown code fences if present
            if raw_text.startswith("```"):
                lines = raw_text.splitlines()
                raw_text = "\n".join(lines[1:-1]) if lines[-1] == "```" else "\n".join(lines[1:])

            return json.loads(raw_text)

        except json.JSONDecodeError as e:
            logger.warning(f"Attempt {attempt+1}: JSON decode error — {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
        except Exception as e:
            err_str = str(e)
            is_quota = "429" in err_str or "quota" in err_str.lower()
            if is_quota:
                wait = min(60 * (attempt + 1), 300)  # 60s, 120s, 180s, 240s, 300s
                logger.warning(f"Attempt {attempt+1}: Quota exceeded — waiting {wait}s before retry")
                if attempt < max_retries - 1:
                    time.sleep(wait)
                else:
                    logger.error(f"Quota still exhausted after {max_retries} attempts (waited up to 5 min)")
            else:
                logger.warning(f"Attempt {attempt+1}: Gemini API error — {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"All retries exhausted for this SNP")

    return None


def interpret_variants(
    rows: list[dict],
    rate_limit_delay: float = 0.5,
) -> list[dict]:
    """
    Generate AI interpretations for a list of SNP result rows.

    Args:
        rows: List of dicts with keys: rsid, gene, genotype, zygosity, description,
              found_in_vcf, is_edge_case, edge_case_note
        rate_limit_delay: Seconds between API calls

    Returns:
        List of rows with added keys: interpretation, confidence_level, mechanism,
        ai_skipped, ai_skip_reason
    """
    if not GEMINI_API_KEY:
        raise ValueError(
            "GEMINI API key not found. Set DEFAULT_GEMINI_KEY in your .env file.\n"
            "Get a free key at: https://aistudio.google.com/apikey"
        )

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(MODEL_NAME)
    logger.info(f"Gemini model initialized: {MODEL_NAME}")

    results = []
    total = len(rows)

    for i, row in enumerate(rows):
        rsid = row.get("rsid", "unknown")
        genotype = row.get("genotype", "N/A")
        zygosity = row.get("zygosity", "")
        is_edge = row.get("is_edge_case", False)
        edge_note = row.get("edge_case_note", "")

        # Decide whether to skip interpretation
        skip_reason = None

        # Inferred hom-ref: absent from VCF = wild-type at this locus
        if row.get("inferred_hom_ref") or (not row.get("found_in_vcf") and genotype not in ("N/A", "") and zygosity == "homozygous ref"):
            skip_reason = SKIP_INTERPRETATION_REASONS["hom_ref"]
        # True edge cases with no genotype at all
        elif is_edge and genotype in ("N/A", ""):
            skip_reason = edge_note or "Edge-case variant not extractable from VCF"
        # Missing/unresolvable genotype
        elif genotype in ("N/A", "") or zygosity == "missing":
            skip_reason = SKIP_INTERPRETATION_REASONS.get(
                genotype, f"Genotype unavailable ({genotype})"
            )

        if skip_reason:
            logger.info(f"  [{i+1}/{total}] SKIP {rsid}: {skip_reason}")
            result = {**row,
                "interpretation": "Not available — variant could not be extracted from VCF",
                "confidence_level": "N/A",
                "mechanism": "N/A",
                "ai_skipped": True,
                "ai_skip_reason": skip_reason,
            }
            results.append(result)
            continue

        # Call Gemini
        logger.info(f"  [{i+1}/{total}] Interpreting {rsid} ({row.get('gene')}) genotype={genotype}")
        prompt = _build_prompt(row)
        parsed = _call_gemini(model, prompt)

        if parsed:
            result = {**row,
                "interpretation": parsed.get("interpretation", "Could not generate interpretation"),
                "confidence_level": parsed.get("confidence_level", "Low"),
                "mechanism": parsed.get("mechanism", "Unknown"),
                "ai_skipped": False,
                "ai_skip_reason": "",
            }
        else:
            result = {**row,
                "interpretation": "API call failed after retries",
                "confidence_level": "N/A",
                "mechanism": "N/A",
                "ai_skipped": True,
                "ai_skip_reason": "Gemini API call failed",
            }

        results.append(result)
        time.sleep(rate_limit_delay)

    logger.info(f"AI interpretation complete: {sum(1 for r in results if not r.get('ai_skipped'))}/{total} interpreted")
    return results
