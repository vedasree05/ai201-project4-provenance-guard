# Provenance Guard

A backend system that classifies submitted text as likely AI-generated, likely human-written, or uncertain — with a transparency label, confidence scoring, an appeals workflow, rate limiting, and a structured audit log.

## Architecture Overview

A submission moves through the system in one straight line: text comes in, gets scored by two independent signals, those scores are combined into a single confidence value, that value is mapped to a transparency label, and the whole decision is written to the audit log before the response goes back to the caller. An appeal doesn't re-run any of this — it attaches the creator's reasoning to the existing record, flips the content's status to `under_review`, and logs the event separately for a human reviewer.

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
[Audit Log] ──► content_id, creator_id, timestamp, both signal scores,
                 combined score, attribution, status="classified"
   │
   ▼
Response ──► { content_id, attribution, confidence, label }
```

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
[Audit Log] ──► new entry: content_id, creator_reasoning, timestamp,
                 status="under_review", linked to original decision
   │
   ▼
Response ──► { content_id, status: "under_review", confirmation }
```

## Detection Signals

**Signal 1 — Groq LLM classifier (semantic signal).** Sends the submitted text to `llama-3.3-70b-versatile` with a prompt asking it to return a structured AI-probability judgment (a 0–1 float plus a one-sentence reason). This captures holistic semantic and stylistic coherence — the kind of judgment that comes from understanding meaning, not just counting things. Chosen because it can catch content-level tells (genericness, hedging, over-explanation) no statistical measure can see. Its blind spot: it's a black box — it can be fooled by lightly-edited AI text, and its own reasoning can't be independently verified beyond the sentence it returns. It's also not fully deterministic call to call (see Known Limitations).

**Signal 2 — Stylometric heuristics (structural signal).** Two pure-Python metrics, normalized and averaged:
- **Sentence length variance** — AI text trends toward uniform sentence lengths; human writing is more irregular. Low variance → higher AI-likeness score.
- **Average word length** — AI-generated text tends toward longer, more Latinate/formal vocabulary than casual human writing. Higher average word length → higher AI-likeness score.

This signal is fully independent of the LLM call — no API, no semantic understanding, purely structural — and acts as a hedge against the LLM being confidently wrong. Its blind spot: unreliable on very short text (too few sentences/words for the metrics to mean anything), and it can misread formal-but-human writing as AI-like (see Known Limitations).

**Note on the original design:** the spec (planning.md) originally called for type-token ratio (vocabulary diversity) as the second metric instead of average word length. During implementation, testing showed TTR — in its raw form and in two length-corrected variants (root TTR / Guiraud's index, Herdan's C) — clustered near-identically (0.86–0.97) across clearly-AI and clearly-human sample text at 40–55 words. The metric carried no discriminating signal at this passage length, regardless of formula. Average word length was substituted in after showing real separation on the same samples (6.23 vs 4.27 characters). This is detailed further in Spec Reflection below.

## Confidence Scoring

Both signal scores are combined as:
```
combined_score = 0.6 * llm_score + 0.4 * stylometric_score
```
The LLM signal is weighted higher because it captures content the stylometric signal can't see at all; the stylometric signal still carries real weight as a check against the LLM being confidently wrong.

**Thresholds (intentionally asymmetric):**

| Score range | Bucket |
|---|---|
| 0.00 – 0.35 | Likely human |
| 0.35 – 0.70 | Uncertain |
| 0.70 – 1.00 | Likely AI |

The "likely AI" threshold sits higher than a symmetric split would put it, and the uncertain band is wider than it needs to be, because on a creative-writing platform a false positive (accusing a human of using AI) is worse than a false negative. This is a deliberate design decision, not an artifact of the math.

**How this was validated:** tested against the four assignment sample inputs (clearly AI, clearly human, and two borderline cases) before and after the TTR→word-length signal swap, checking that scores moved in the expected direction and that the two borderline cases in particular landed closer to "uncertain" rather than being confidently miscategorized.

**Two real example submissions showing meaningfully different scores** (pulled directly from the live audit log):

- **Higher-confidence case** — text: *"This is a test submission for rate limit testing purposes only."* → `llm_score: 0.1`, `stylometric_score: 0.371` → **combined confidence: 0.208** → `likely_human`. Both signals agreed (LLM leaning human, stylometrics also below the 0.5 midpoint), producing a confident result.
- **Lower-confidence / signal-disagreement case** — text: *"The sun dipped below the horizon, painting the sky in hues of amber and rose. I sat on the porch, coffee in hand, watching the neighborhood slowly go quiet."* → `llm_score: 0.2`, `stylometric_score: 0.537` → **combined confidence: 0.335** → `likely_human`, but right at the edge of the "uncertain" boundary. Here the two signals actively disagreed: the LLM read it as fairly human, while the stylometric signal (this passage happens to have only two, similarly-sized sentences) leaned AI-like. This is a concrete illustration of why the system uses two independent signals instead of one — a single-signal system built only on stylometrics would have called this "uncertain" or worse; combining it with the LLM signal pulled the result back toward the correct answer, though not by a wide margin.

## Transparency Label

Exact text returned for each bucket:

**High-confidence AI:**
> "This content shows strong signals of AI generation. Our system is highly confident (score: {score}) that this was AI-written or AI-assisted."

**High-confidence human:**
> "This content shows strong signals of human authorship. Our system found little indication of AI generation (score: {score})."

**Uncertain:**
> "We're not confident either way about this content's origin (score: {score}). This could be human writing with an unusual style, AI-assisted writing, or AI writing that's been edited. If you believe this is misclassified, you can appeal below."

`{score}` is replaced with the actual combined confidence value at response time (e.g. `0.34`).

## Rate Limiting

`/submit` is limited to **10 requests per minute, 100 per day**, per client, via Flask-Limiter with in-memory storage.

**Reasoning:** a real creator submitting their own work rarely posts more than a handful of pieces in any given minute — 10/min comfortably covers legitimate bursts (e.g. testing a few draft revisions back to back) while still blocking a scripted flood attempting to hammer the endpoint. 100/day covers a very active user across a full day of normal use without enabling scraping-scale abuse.

**Evidence** — 12 rapid requests sent to `/submit` in a loop, first 10 succeed, next 2 are rejected:
```
200
200
200
200
200
200
200
200
200
200
429
429
```

## Audit Log

Every classification and appeal writes a structured row to a SQLite-backed log (`audit_log.py`), exposed via `GET /log`. Sample entries (real output from a live run):

```json
{
  "id": 2,
  "content_id": "f9efdb63-5265-4ef5-9f30-2f47a1eece96",
  "creator_id": "test-user-1",
  "timestamp": "2026-07-08T03:19:39.415874+00:00",
  "event_type": "classified",
  "attribution": "likely_human",
  "confidence": 0.335,
  "llm_score": 0.2,
  "stylometric_score": 0.537,
  "status": "under_review",
  "creator_reasoning": null
}
```
```json
{
  "id": 9,
  "content_id": "22c90847-ec0e-4699-a651-35f4e07bea5b",
  "creator_id": "ratelimit-test",
  "timestamp": "2026-07-08T03:22:11.659526+00:00",
  "event_type": "classified",
  "attribution": "likely_human",
  "confidence": 0.208,
  "llm_score": 0.1,
  "stylometric_score": 0.371,
  "status": "classified",
  "creator_reasoning": null
}
```
```json
{
  "id": 13,
  "content_id": "f9efdb63-5265-4ef5-9f30-2f47a1eece96",
  "timestamp": "2026-07-08T03:22:34.745721+00:00",
  "event_type": "appeal",
  "status": "under_review",
  "creator_reasoning": "I wrote this myself from personal experience."
}
```
Note entry `id: 2` and entry `id: 13` share the same `content_id` — the appeal (id 13) correctly flipped the original classification's status to `under_review`, visible in both rows.

## Known Limitations

1. **Very short passages with few sentences produce an unreliable stylometric score.** The "sun dipped below the horizon" example above has only two sentences of similar length, which reads as low variance (an "AI-like uniformity" signal) even though the text is human-written. This is a direct, observed consequence of the sentence-variance metric, not a hypothetical — it's exactly why the combined score landed near the uncertain boundary rather than confidently in "likely human."
2. **The LLM signal is not fully deterministic call to call.** Running the same short, generic filler text ("This is a test submission for rate limit testing purposes only.") through Signal 1 multiple times in the same test session produced `llm_score` values of both 0.1 and 0.8 in different calls (visible across entries in the audit log above), despite a low temperature setting (0.1). Short, content-light text seems especially prone to this, likely because there's little concrete signal for the model to anchor its judgment on either way.

## Spec Reflection

**How the spec helped:** deciding the exact label text and score thresholds in planning.md before writing any code meant the label-generation function was almost trivial to implement and verify — there was no ambiguity to resolve mid-implementation about what "uncertain" should say or where the boundaries sat.

**Where implementation diverged:** the original spec called for type-token ratio as the second stylometric signal. Testing during Milestone 4 showed TTR (and two standard length-corrected variants of it) had essentially zero discriminating power at the ~40–55 word length of the test samples — not a normalization bug, but a real property of the metric at that text length. It was replaced with average word length, which showed real separation on the same data. This divergence came from testing candidate metrics against real sample text rather than assuming the original spec'd metric would behave as expected.

## AI Usage

1. **Directed:** generate the Flask app skeleton, the Groq-based Signal 1 function, and wire them into a `/submit` endpoint, based on the detection-signals section of planning.md. **Revised:** added handling for markdown code-fence-wrapped JSON in the Groq response (the model sometimes wraps its JSON output in ```` ```json ```` fences), and added score clamping to guarantee the returned value stays within [0,1] even if the model returns something out of range.
2. **Directed:** diagnose why the stylometric signal wasn't discriminating between clearly-AI and clearly-human sample text, by testing multiple candidate metrics (raw TTR, root TTR, Herdan's C, average word length, punctuation density) against the same four samples. **Overrode:** rejected all three TTR-family formulas after the data showed they clustered together regardless of formula, and replaced the metric entirely with average word length rather than accepting a superficial fix (e.g. just reweighting the broken metric down).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Requires a `.env` file in the repo root with `GROQ_API_KEY=your_key_here`.