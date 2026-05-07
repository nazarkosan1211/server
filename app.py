from flask import Flask, request, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.sql import func
import os

app = Flask(__name__)
CORS(app)

# =======================
# DATABASE
# =======================
# Ganti DATABASE_URL sesuai PostgreSQL Railway
DATABASE_URL = "postgresql://postgres:VeHwVtiMUtrLddWDoPoGggYAyupuASZS@turntable.proxy.rlwy.net:27947/railway"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

# =======================
# TABLES
# =======================
class User(Base):
    __tablename__ = "users"
    user_id = Column(String, primary_key=True, index=True)
    coins = Column(Integer, default=0)
    tasks_done = Column(Integer, default=0)
    remaining_tasks = Column(Integer, default=30)
    ref_count = Column(Integer, default=0)
    ref_by = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

Base.metadata.create_all(bind=engine)

# =======================
# CONSTANTS
# =======================
DAILY_TASK_LIMIT = 30
REF_LIMIT = 20
REF_POINT = 10

# =======================
# ROOT TEST
# =======================
@app.route("/")
def root():
    return "Server is online!"

# =======================
# ENDPOINTS
# =======================
@app.route("/start_user", methods=["POST"])
def start_user():
    data = request.json
    user_id = str(data.get("user_id"))
    ref = str(data.get("ref")) if data.get("ref") else None

    session = SessionLocal()
    user = session.query(User).filter(User.user_id == user_id).first()
    if not user:
        user = User(user_id=user_id, ref_by=ref if ref != user_id else None)
        session.add(user)
        session.commit()
        # update referrer
        if ref:
            ref_user = session.query(User).filter(User.user_id == ref).first()
            if ref_user and ref_user.ref_count < REF_LIMIT:
                ref_user.coins += REF_POINT
                ref_user.ref_count += 1
                session.commit()
    session.close()
    return jsonify({"status": "success", "user_id": user_id})

@app.route("/add_coin", methods=["POST"])
def add_coin():
    data = request.json
    user_id = str(data.get("user_id"))
    amount = int(data.get("amount", 0))

    session = SessionLocal()
    user = session.query(User).filter(User.user_id == user_id).first()
    if not user:
        session.close()
        return jsonify({"status": "error", "message": "User not found"}), 404

    # daily task limit
    if amount > 0:
        if user.tasks_done >= DAILY_TASK_LIMIT:
            session.close()
            return jsonify({"status": "blocked", "reason": "limit", "wait": 0})
        user.coins += amount
        user.tasks_done += 1
        user.remaining_tasks = DAILY_TASK_LIMIT - user.tasks_done
        session.commit()

    result = {
        "status": "success",
        "coins": user.coins,
        "tasks_done": user.tasks_done,
        "remaining_tasks": user.remaining_tasks,
        "ref_count": user.ref_count
    }
    session.close()
    return jsonify(result)

# =======================
# MAIN
# =======================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
