"""
Transparency label generation for Provenance Guard.
Maps a combined confidence score + attribution bucket to the exact label
text a reader would see, per planning.md's Transparency Label Design section.
"""


def generate_label(confidence: float, attribution: str) -> str:
    """
    Returns the exact label text for a given attribution bucket.
    confidence is included inline so the reader sees the actual score.
    """
    if attribution == "likely_ai":
        return (
            f"This content shows strong signals of AI generation. Our system is "
            f"highly confident (score: {confidence:.2f}) that this was AI-written "
            f"or AI-assisted."
        )
    elif attribution == "likely_human":
        return (
            f"This content shows strong signals of human authorship. Our system "
            f"found little indication of AI generation (score: {confidence:.2f})."
        )
    else:  # uncertain
        return (
            f"We're not confident either way about this content's origin "
            f"(score: {confidence:.2f}). This could be human writing with an "
            f"unusual style, AI-assisted writing, or AI writing that's been "
            f"edited. If you believe this is misclassified, you can appeal below."
        )