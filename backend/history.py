import firebase_admin
from firebase_admin import credentials, firestore
import uuid
from flask import Flask, render_template, request, jsonify, session

cred = credentials.Certificate(
    "hackathon2026-9c7b8-firebase-adminsdk-fbsvc-f9cd5a6012.json"
)
firebase_admin.initialize_app(cred)
db = firestore.client()

def getUserId():
    if "user_id" not in session:
        session["user_id"] = str(uuid.uuid4())
    return session["user_id"]

def addHistory(data):
    if data is None or type(data) != dict:
        return
    
    userId = getUserId()

    data = {
        **data,
        "created_at": firestore.SERVER_TIMESTAMP,
        "last_seen": firestore.SERVER_TIMESTAMP
    }

    db.collection("users") \
        .document(userId) \
        .collection("map_history") \
        .add(data)

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

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/get_history", methods=["GET"])
def sendHistoryList():
    histories = getHistoryList()
    return jsonify(histories)

@app.route("/add_history", methods=["POST"])
def addNewHistory():
    data = request.get_json()

    addHistory(data)

    return jsonify({
        "status": "done"
    })
    
@app.route("/delete_history", methods=["POST"])
def deleteSelectedHistory():
    data = request.get_json()
    dataId = data.get("id")

    if not dataId:
        return jsonify({"error": "No id provided"}), 400

    deleteHistory(dataId)

    return jsonify({
        "status": "done"
    })
    
@app.route("/select_history", methods=["POST"])
def selectHistory():
    data = request.get_json()
    dataId = data.get("id")

    if not dataId:
        return jsonify({"error": "No id provided"}), 400

    updateTimestamp(dataId)

    return jsonify({
        "status": "done"
    })
    
if __name__ == "__main__":
    app.run(debug=True)