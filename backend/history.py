import firebase_admin
from firebase_admin import credentials, firestore, auth
from flask import Flask, request, jsonify
from flask_cors import CORS
import os

firebase_admin.initialize_app()
db = firestore.client()

app = Flask(__name__)
CORS(
    app,
    resources={r"/*": {
        "origins": [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "https://city-planner-711110564007.asia-southeast1.run.app"
        ]
    }},
    supports_credentials=False
)

def getUserId():
    try:
        header = request.headers.get("Authorization", "")

        if not header.startswith("Bearer "):
            return None

        token = header.split("Bearer ")[1].strip()
        if not token:
            return None

        decoded = auth.verify_id_token(token)
        return decoded["uid"]

    except Exception:
        return None

def addHistory(data, userId):
    if data is None or type(data) != dict:
        return

    payload = {
        **data,
        "created_at": firestore.SERVER_TIMESTAMP,
        "last_seen": firestore.SERVER_TIMESTAMP
    }
    
    db.collection("users") \
        .document(userId) \
        .collection("map_history") \
        .add(payload)

def getHistoryList(userId):
    docs = db.collection("users") \
        .document(userId) \
        .collection("map_history") \
        .order_by("last_seen", direction=firestore.Query.DESCENDING) \
        .stream()

    return [
        {"id": doc.id, **doc.to_dict()}
        for doc in docs
    ]
    
def updateTimestamp(dataId, userId):
    if dataId is None:
        return

    ref = db.collection("users") \
        .document(userId) \
        .collection("map_history") \
        .document(dataId)

    try:
        ref.update({
            "last_seen": firestore.SERVER_TIMESTAMP
        })
    except Exception:
        pass
        
def deleteHistory(dataId, userId):
    if dataId is None:
        return

    db.collection("users") \
        .document(userId) \
        .collection("map_history") \
        .document(dataId) \
        .delete()

@app.route("/get_history", methods=["GET"])
def sendHistoryList():
    userId = getUserId()
    if not userId:
        return jsonify({"error": "Unauthorized"}), 401
    
    histories = getHistoryList(userId)
    return jsonify(histories)

@app.route("/add_history", methods=["POST"])
def addNewHistory():
    try:
        data = request.get_json(silent=True)
        
        userId = getUserId()
        if not userId:
            return jsonify({"error": "Unauthorized"}), 401
        
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid JSON"}), 400

        addHistory(data, userId)

        return jsonify({
            "status": "ok",
            "message" : data
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e) 
        }), 500
    
@app.route("/delete_history", methods=["POST"])
def deleteSelectedHistory():
    try:
        data = request.get_json(silent=True) or {}
        
        userId = getUserId()
        if not userId:
            return jsonify({"error": "Unauthorized"}), 401

        dataId = data.get("id")

        deleteHistory(dataId, userId)

        return jsonify({
                "status": "ok",
                "message" : data
            })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e) 
        }), 500
    
@app.route("/select_history", methods=["POST"])
def selectHistory():
    try:
        data = request.get_json(silent=True) or {}

        dataId = data.get("id")
        
        userId = getUserId()
        if not userId:
            return jsonify({"error": "Unauthorized"}), 401
        
        if dataId is None:
            return jsonify({
                "status": "error",
                "message": "invalid id" 
            }), 400

        updateTimestamp(dataId, userId)
        
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