from flask import Flask, jsonify, render_template
import json
import os

app = Flask(__name__)

CACHE_FILE = "data_cache.json"

@app.route("/")
def home():
    return render_template("ios_template.html")

@app.route("/data")
def get_data():
    if not os.path.exists(CACHE_FILE):
        return jsonify({"facilities": {}, "availability": {}})

    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        cache = json.load(f)

    return jsonify(cache)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
