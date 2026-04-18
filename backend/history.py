import firebase_admin
from firebase_admin import credentials, firestore
import uuid
from flask import Flask, request, jsonify, session
from flask_cors import CORS

cred = credentials.Certificate(
    "hackathon-2eedf-firebase-adminsdk-fbsvc-41700aa0be.json"
)
firebase_admin.initialize_app(cred)
db = firestore.client()

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

app = Flask(__name__)
app.secret_key = "Hackathon"
CORS(app, supports_credentials=True, origins=["http://127.0.0.1:3000", "http://localhost:3000"])
app.config.update(
    SESSION_COOKIE_SAMESITE="None",  # allow cross-site
    SESSION_COOKIE_SECURE=False      # True ONLY if HTTPS
)

@app.get("/api/get_history")
def sendHistoryList():
    histories = getHistoryList()
    return jsonify(histories)

@app.post("/api/add_history")
def addNewHistory():
    data = request.get_json()
    
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON"}), 400

    addHistory(data)

    return jsonify({
        "status": "done"
    })
    
@app.post("/api/delete_history")
def deleteSelectedHistory():
    data = request.get_json()
    
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON"}), 400

    dataId = data.get("id")

    if not dataId:
        return jsonify({"error": "No id provided"}), 400

    deleteHistory(dataId)

    return jsonify({
        "status": "done"
    })
    
@app.post("/api/select_history")
def selectHistory():
    data = request.get_json()
    
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON"}), 400

    dataId = data.get("id")

    if not dataId:
        return jsonify({"error": "No id provided"}), 400

    updateTimestamp(dataId)

    return jsonify({
        "status": "done"
    })
    
if __name__ == "__main__":
    app.run(port = 5000)