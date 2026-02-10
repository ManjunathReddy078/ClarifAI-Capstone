from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/")
def home():
    return "ClarifAI backend is running successfully!"

@app.route("/health")
def health():
    return jsonify({
        "status": "OK",
        "message": "ClarifAI service is healthy"
    })

if __name__ == "__main__":
    app.run(debug=True)
