# Business Impact Summary
*For: VP of Operations — no data-quality jargon, framed in cost and risk terms.*

## The problem
[One or two sentences on messy policyholder address data at scale — pull the
scenario framing, not the technical detail.]

## Risk 1: Mispriced premiums
[Quantify: "X% of sampled policies had address errors that would have caused
incorrect risk-zone assignment. At your book size, that's an estimated $Y in
annual premium leakage."]

## Risk 2: Misrouted claims / compliance exposure
[Quantify: claims-handling delay cost and/or compliance exposure from
unverifiable policyholder locations.]

## The fix
[One sentence: verification + geocoding close the risk-zone gap. One
sentence: enrichment gives underwriters real hazard context instead of a
raw address string.]

## What AI adds on top
[One sentence: enrichment gives you the data, the AI layer turns it into a
decision — a plain-English risk summary and an automatic flag when the data
contradicts what's on the policy, instead of a spreadsheet of coordinates.]

---
*Fill this in last, once `data/03_enriched.csv` and `data/04_ai_narratives.csv`
give you real numbers to cite — see the build plan for the two risk stats to
pull first.*
