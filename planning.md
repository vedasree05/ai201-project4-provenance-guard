# Provenance Guard — Planning

## 1. Detection Signals

**Signal 1 — Groq LLM classifier (semantic signal)**
- What it measures: holistic semantic and stylistic coherence — asks the model to judge, from meaning and phrasing, whether text reads as AI-generated or human-written.
- Output format: a float 0–1 (probability the text is AI-generated) plus a one-line reason string returned in the same call.
- Why chosen: captures content-level patterns (genericness, hedging language, over-explanation) that no statistical measure can see.
- Blind spot: it's a black box judgment — can be fooled by lightly-edited AI text, and gives no way to independently verify its own reasoning beyond the text it returns.

**Signal 2 — Stylometric heuristics (structural signal)**
- What it measures: two pure-Python statistical properties —
  1. Sentence length variance (AI text trends toward uniform sentence lengths; human text is more irregular)
  2. Type-token ratio / vocabulary diversity (AI text trends toward lower lexical variety across a passage)
- Output format: each metric normalized to 0–1, then averaged into a single stylometric score (0–1).
- Why chosen: fully independent of the LLM signal — no API call, no semantic understanding, purely structural. Acts as a hedge against the LLM signal being confidently wrong.
- Blind spot: unreliable on short text (not enough sentences/tokens for variance to be meaningful); can be fooled by a deliberately choppy human writer or a heavily-revised AI output.

**Why these two are "distinct":** one is semantic (meaning-level), one is structural (statistics-level). They fail in different, uncorrelated ways, which is what makes combining them more informative than either alone.

## 2. Uncertainty Representation

**Combining signals:**
```
combined_score = 0.6 * llm_score + 0.4 * stylometric_score
```
LLM signal is weighted higher because it captures content the stylometric signal can't see at all. Stylometrics still carries real weight (0.4) as a check against the LLM being confidently wrong — this is the false-positive hedge.

**Thresholds (intentionally asymmetric):**

| Score range | Label bucket |
|---|---|
| 0.00 – 0.35 | Likely human |
| 0.35 – 0.70 | Uncertain |
| 0.70 – 1.00 | Likely AI |

The "likely AI" threshold is pushed up to 0.70 (rather than a symmetric 0.66) and the uncertain band is widened, because a false positive — labeling a human's work as AI-generated — is worse than a false negative on a creative-writing platform. A 0.51 and a 0.95 should read very differently to a user; a narrow, symmetric split wouldn't achieve that.

**What 0.6 means to the system:** falls inside "uncertain" — the system is explicitly saying it does not have enough signal agreement to make a confident claim either way, and routes the reader to the uncertain label rather than forcing a binary call.

## 3. Transparency Label Design

Exact text, by bucket:

**High-confidence AI:**
> "This content shows strong signals of AI generation. Our system is highly confident (score: {score}) that this was AI-written or AI-assisted."

**High-confidence human:**
> "This content shows strong signals of human authorship. Our system found little indication of AI generation (score: {score})."

**Uncertain:**
> "We're not confident either way about this content's origin (score: {score}). This could be human writing with an unusual style, AI-assisted writing, or AI writing that's been edited. If you believe this is misclassified, you can appeal below."

## 4. Appeals Workflow

- **Who:** the original creator (identified via `creator_id`), submitting against a specific `content_id`.
- **What they provide:** `content_id` + `creator_reasoning` (free text explaining why they believe the classification is wrong).
- **What the system does on receipt:**
  1. Looks up the original submission by `content_id`.
  2. Updates that content's status to `"under_review"`.
  3. Writes a new audit log entry capturing the appeal: content_id, creator_reasoning, timestamp, and a reference to the original classification (attribution, confidence, signal scores).
  4. Returns a confirmation response to the creator.
- **What a human reviewer would see in the appeal queue:** the original text, the original attribution/confidence/signal breakdown, the creator's reasoning, and the current status — everything needed to make a manual call without re-running detection.
- No automated re-classification — this is a human-in-the-loop step by design.

## 5. Anticipated Edge Cases

1. **Very short text** (a haiku, a single paragraph): sentence-length variance and type-token ratio are close to meaningless on small samples, so the combined score leans almost entirely on the LLM signal and can be unstable between near-identical short inputs.
2. **Formal academic/technical human writing** (e.g., dense economics or policy prose): naturally uniform sentence structure and lower lexical variety will push the stylometric signal toward "AI-like" even though the writer is human — this is exactly the false-positive risk the threshold design is trying to guard against.

## Architecture

**Submission flow:**
```
POST /submit
   │
   ▼
[Signal 1: Groq LLM classifier] ──► llm_score (0-1) + reason
   │
   ▼
[Signal 2: Stylometric heuristics] ──► stylometric_score (0-1)
   │
   ▼
[Confidence Scoring]
   combined_score = 0.6*llm_score + 0.4*stylometric_score
   │
   ▼
[Label Generator] ──► maps combined_score to one of 3 label variants
   │
   ▼
[Audit Log] ──► writes: content_id, creator_id, timestamp,
                 llm_score, stylometric_score, combined_score,
                 attribution, status="classified"
   │
   ▼
Response ──► { content_id, attribution, confidence, label }
```

**Appeal flow:**
```
POST /appeal {content_id, creator_reasoning}
   │
   ▼
[Lookup original submission by content_id]
   │
   ▼
[Status Update] ──► status = "under_review"
   │
   ▼
[Audit Log] ──► writes: content_id, creator_reasoning, timestamp,
                 status="under_review", linked to original decision
   │
   ▼
Response ──► { content_id, status: "under_review", confirmation }
```

**Narrative:** A submission passes through both detection signals independently, gets combined into a single confidence score with false-positive-aware thresholds, is mapped to one of three label variants, and every step is written to the audit log before the response returns to the user. An appeal doesn't re-run detection — it attaches the creator's reasoning to the existing record, flips status to under review, and logs the event for a human reviewer to act on later.

## AI Tool Plan

**M3 (submission endpoint + Signal 1):**
- Spec sections provided: Detection Signals (Signal 1 only) + Architecture diagram (submission flow).
- What I'll ask for: Flask app skeleton with `POST /submit` route stub, and the Groq-calling function for Signal 1.
- Verification: call the signal function directly with 2–3 test inputs before wiring into the endpoint; confirm it returns a 0–1 float, not a raw string.

**M4 (Signal 2 + confidence scoring):**
- Spec sections provided: Detection Signals (both) + Uncertainty Representation + Architecture diagram.
- What I'll ask for: the stylometric signal function, and the scoring function that combines both signals per the 0.6/0.4 weighting and threshold table above.
- Verification: run against the 4 sample inputs from the assignment (clearly AI, clearly human, 2 borderline); confirm scores land in the expected buckets, and that the two signals sometimes disagree in an informative way.

**M5 (production layer):**
- Spec sections provided: Transparency Label Design + Appeals Workflow + Architecture diagram (appeal flow).
- What I'll ask for: the label-generation function (score → exact text above) and the `POST /appeal` endpoint.
- Verification: manually trigger all 3 label variants by submitting inputs at different confidence levels; submit an appeal and confirm via `GET /log` that status flips to `"under_review"` and `creator_reasoning` is populated.