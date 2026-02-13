from textblob import TextBlob


def analyze_sentiment(text: str) -> str:
	polarity = TextBlob(text).sentiment.polarity
	if polarity > 0.1:
		return "positive"
	if polarity < -0.1:
		return "negative"
	return "neutral"
