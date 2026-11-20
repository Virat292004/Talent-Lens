# Matching method

## Weighted score

```text
Final = 0.40 × Skills + 0.25 × Semantic + 0.20 × Experience
      + 0.10 × Education + 0.05 × Project Relevance
```

Each available component is in the range 0–100. When one or more components are unavailable, their weights are omitted and the remaining available weights are normalized to sum to one. The result exposes `coverage`, the sum of originally available weights as a percentage, and `missing_components`; unavailable data is not silently scored as a zero.

### Skills

Mandatory exact matches each earn full credit. Preferred exact matches have half the weight of mandatory skills. The denominator is mandatory count plus half the preferred count. Aliases normalize equivalent names (for example `React.js` → `React`, `Postgres` → `PostgreSQL`). SQL and PostgreSQL remain separate. Related technology pairs are shown as context, not counted as exact matches. Duplicate normalized skills count once.

### Semantic similarity

The preferred model is Sentence Transformers `all-MiniLM-L6-v2`, with normalized embedding cosine similarity. Since cosine spans -1 to 1, the score maps it to 0–100 with `(cosine + 1) × 50`. If the model cannot be loaded in a restricted local environment, a deterministic TF-IDF cosine fallback maps `[0,1]` directly to `[0,100]`; result evidence identifies the fallback in the method description. Job summary is compared with extracted role/project evidence.

### Experience, education, projects

Experience uses unioned year-precision employment ranges to avoid double-counting overlaps. When a job specifies a minimum, score is `min(1, candidate_years / required_years) × 100`. The extractor reports total and role-relevant range union separately; missing dates remain unavailable. Education is a keyword comparison against extracted qualifications. Project relevance uses overlap between job skills and extracted project text; missing project evidence is unavailable.

## Worked example

For component scores Skills 80, Semantic 70, Experience 50, Education 100, Projects unavailable:

```text
Weighted available points = .40×80 + .25×70 + .20×50 + .10×100 = 69.5
Available weight = .95
Final = 69.5 / .95 = 73.2
Coverage = 95%
```

Every score is deterministic for the same inputs and model/runtime. It is a comparison aid, not a prediction of hiring success.
