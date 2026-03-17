import re

from textblob import TextBlob


POSITIVE_TERMS = {
	"excellent",
	"fantastic",
	"great",
	"helpful",
	"supportive",
	"engaging",
	"perfect",
	"perfectly",
	"easy",
	"top-tier",
	"welcomes",
	"actionable",
	"amazing",
}

NEGATIVE_STRONG_TERMS = {
	"confusing",
	"poor",
	"bad",
	"disorganized",
	"inaccessible",
	"struggling",
	"fails",
	"hard",
	"rude",
	"rushes",
	"unclear",
}

NEGATIVE_MILD_TERMS = {
	"lacks",
	"rarely",
	"monotonous",
	"difficult",
	"limited",
	"basic",
}


def _token_hits(text: str, terms: set[str]) -> int:
	lower = text.lower()
	hits = 0
	for term in terms:
		if re.search(rf"\b{re.escape(term)}\b", lower):
			hits += 1
	return hits


def analyze_sentiment_with_confidence(text: str) -> tuple[str, int]:
	clean_text = (text or "").strip()
	if not clean_text:
		return "neutral", 55

	blob_polarity = TextBlob(clean_text).sentiment.polarity
	positive_hits = _token_hits(clean_text, POSITIVE_TERMS)
	negative_strong_hits = _token_hits(clean_text, NEGATIVE_STRONG_TERMS)
	negative_mild_hits = _token_hits(clean_text, NEGATIVE_MILD_TERMS)
	negative_hits = negative_strong_hits + negative_mild_hits
	keyword_delta = positive_hits - negative_hits
	blended_score = (blob_polarity * 0.7) + (keyword_delta * 0.1)
	mixed_signal = positive_hits > 0 and negative_hits > 0

	if negative_strong_hits >= 2:
		confidence = 72 + min(22, (negative_strong_hits * 5) + int(max(0.0, -blob_polarity) * 16))
		return "negative", min(confidence, 96)

	if positive_hits >= 2 and negative_strong_hits == 0 and negative_mild_hits <= 1:
		confidence = 72 + min(22, (positive_hits * 5) + int(max(0.0, blob_polarity) * 16))
		return "positive", min(confidence, 96)

	if mixed_signal:
		confidence = 64 + min(20, int((1 - min(1.0, abs(blob_polarity))) * 20))
		return "neutral", min(max(confidence, 60), 88)

	if negative_mild_hits >= 2 and negative_strong_hits == 0:
		return "neutral", 70

	if blended_score >= 0.2 or blob_polarity >= 0.2:
		confidence = 66 + min(22, int(max(0.0, blended_score) * 50) + positive_hits * 2)
		return "positive", min(max(confidence, 60), 92)

	if blended_score <= -0.25 or blob_polarity <= -0.25:
		confidence = 66 + min(22, int(max(0.0, -blended_score) * 50) + negative_hits * 2)
		return "negative", min(max(confidence, 60), 92)

	confidence = 62 + min(20, int((1 - min(1.0, abs(blob_polarity))) * 20))
	return "neutral", min(max(confidence, 60), 88)


def analyze_sentiment(text: str) -> str:
	label, _ = analyze_sentiment_with_confidence(text)
	return label
