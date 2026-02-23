import json

with open("data/extraction_results.json") as f:
    data = json.load(f)

found = [(k, v) for k, v in data.items() if v.get("found_in_vcf") and v.get("genotype") != "N/A"]
print(f"Total actual variants found: {len(found)}")

for rsid, info in found[:15]:
    gene = info.get("gene", "Unknown")
    cat = info.get("category", "Unknown")
    genotype = info.get("genotype", "")
    zygosity = info.get("zygosity", "")
    print(f"- {gene} ({rsid}): {genotype} [{zygosity}]  -> {cat}")
