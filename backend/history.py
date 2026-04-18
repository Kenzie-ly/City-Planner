import firebase_admin
from firebase_admin import credentials, firestore
import uuid
from flask import Flask, request, jsonify, session
from flask_cors import CORS
import os

cred = credentials.Certificate(
    "hackathon-2eedf-firebase-adminsdk-fbsvc-8f245aa67c.json"
)
firebase_admin.initialize_app(cred)
db = firestore.client()

app = Flask(__name__)
CORS(
    app,
    resources={r"/*": {
        "origins": [
            "http://localhost:3000",
            "http://127.0.0.1:3000"
        ]
    }},
    supports_credentials=False
)
"""
app.secret_key = "Hackathon"
CORS(app, supports_credentials=True, origins=["http://127.0.0.1:3000", "http://localhost:3000"])
app.config.update(
    SESSION_COOKIE_SAMESITE="None",  # allow cross-site
    SESSION_COOKIE_SECURE=True      # True ONLY if HTTPS
)"""

def getUserId():
    return "userTest01"
    if "user_id" not in session:
        session["user_id"] = str(uuid.uuid4())
    return session["user_id"]

def addHistory(data):
    if data is None or type(data) != dict:
        return
    
    userId = getUserId()

    payload = {
        **data,
        "created_at": firestore.SERVER_TIMESTAMP,
        "last_seen": firestore.SERVER_TIMESTAMP
    }
    
    update_time, doc_ref = db.collection("users") \
        .document(userId) \
        .collection("map_history") \
        .add(payload)

def getHistoryList():
    userId = getUserId()

    docs = db.collection("users") \
        .document(userId) \
        .collection("map_history") \
        .order_by("last_seen", direction=firestore.Query.DESCENDING) \
        .stream()

    return [
        {"id": doc.id, **doc.to_dict()}
        for doc in docs
    ]
    
def updateTimestamp(dataId):
    if dataId is None:
        return
    
    userId = getUserId()

    ref = db.collection("users") \
        .document(userId) \
        .collection("map_history") \
        .document(dataId)

    if ref.get().exists:
        ref.update({
            "last_seen": firestore.SERVER_TIMESTAMP
        })
        
def deleteHistory(dataId):
    if dataId is None:
        return
    
    userId = getUserId()

    db.collection("users") \
        .document(userId) \
        .collection("map_history") \
        .document(dataId) \
        .delete()

@app.route("/get_history", methods=["GET"])
def sendHistoryList():
    histories = getHistoryList()
    return jsonify(histories)

@app.route("/add_history", methods=["POST", "OPTIONS"])
def addNewHistory():
    if request.method == "OPTIONS":
        return "", 204
    try:
        data = request.get_json()
        
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid JSON"}), 400

        addHistory(data)

        return jsonify({
            "status": "ok",
            "message" : data
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e) 
        }), 500
    
@app.route("/delete_history", methods=["POST", "OPTIONS"])
def deleteSelectedHistory():
    if request.method == "OPTIONS":
        return "", 204
    try:
        data = request.get_json()

        dataId = data.get("id")

        deleteHistory(dataId)

        return jsonify({
                "status": "ok",
                "message" : data
            })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e) 
        }), 500
    
@app.route("/select_history", methods=["POST", "OPTIONS"])
def selectHistory():
    if request.method == "OPTIONS":
        return "", 204
    try:
        data = request.get_json()

        dataId = data.get("id")

        updateTimestamp(dataId)
        
        return jsonify({
            "status": "ok",
            "message" : data
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e) 
        }), 500

    
    
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)