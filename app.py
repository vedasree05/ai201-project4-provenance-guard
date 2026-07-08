import os
import uuid
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from signals import get_llm_signal, get_stylometric_signal, get_combined_confidence, score_to_attribution
from labels import generate_label
from audit_log import init_db, log_classification, log_appeal, get_recent_entries, get_original_entry

load_dotenv()

app = Flask(__name__)
init_db()

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    data = request.get_json(force=True, silent=True) or {}
    text = data.get("text")
    creator_id = data.get("creator_id")

    if not text or not creator_id:
        return jsonify({"error": "Both 'text' and 'creator_id' are required."}), 400

    content_id = str(uuid.uuid4())

    # --- Signal 1: Groq LLM classifier ---
    try:
        llm_result = get_llm_signal(text)
    except RuntimeError as e:
        return jsonify({"error": f"Detection signal failed: {e}"}), 502

    llm_score = llm_result["score"]

    # --- Signal 2: stylometric heuristics (sentence variance + avg word length) ---
    style_result = get_stylometric_signal(text)
    stylometric_score = style_result["score"]

    # --- Combined confidence + attribution bucket, per planning.md ---
    confidence = get_combined_confidence(llm_score, stylometric_score)
    attribution = score_to_attribution(confidence)

    # --- Real transparency label text (not a placeholder) ---
    label = generate_label(confidence, attribution)

    log_classification(
        content_id=content_id,
        creator_id=creator_id,
        attribution=attribution,
        confidence=confidence,
        llm_score=llm_score,
        stylometric_score=stylometric_score,
        text=text,
    )

    return jsonify({
        "content_id": content_id,
        "attribution": attribution,
        "confidence": round(confidence, 3),
        "label": label,
        "signal_details": {
            "llm_score": round(llm_score, 3),
            "llm_reason": llm_result["reason"],
            "stylometric_score": round(stylometric_score, 3),
            "sentence_variance": style_result["sentence_variance"],
            "avg_word_length": style_result["avg_word_length"],
        },
    })


@app.route("/log", methods=["GET"])
def get_log():
    entries = get_recent_entries(limit=20)
    return jsonify({"entries": entries})


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(force=True, silent=True) or {}
    content_id = data.get("content_id")
    creator_reasoning = data.get("creator_reasoning")

    if not content_id or not creator_reasoning:
        return jsonify({"error": "Both 'content_id' and 'creator_reasoning' are required."}), 400

    original = get_original_entry(content_id)
    if not original:
        return jsonify({"error": f"No submission found with content_id {content_id}"}), 404

    log_appeal(content_id, creator_reasoning)

    return jsonify({
        "content_id": content_id,
        "status": "under_review",
        "message": "Appeal received and logged for human review.",
    })


if __name__ == "__main__":
    app.run(debug=True, port=5001)