from flask import Flask, request, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine, Column, Integer, String, DateTime, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.sql import func
import os
import time
import secrets

app = Flask(__name__)
CORS(app)

DATABASE_URL = "postgresql://postgres:VeHwVtiMUtrLddWDoPoGggYAyupuASZS@turntable.proxy.rlwy.net:27947/railway"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

DAILY_TASK_LIMIT = 30
COOLDOWN_SECONDS = 15
TASK_TOKEN_EXPIRE = 60
REF_LIMIT = 20
REF_POINT = 10

class User(Base):
    __tablename__ = "users"

    user_id = Column(String, primary_key=True, index=True)
    username = Column(String, nullable=True)

    coins = Column(Integer, default=0)
    tasks_done = Column(Integer, default=0)
    remaining_tasks = Column(Integer, default=30)
    last_task_time = Column(Integer, default=0)

    task_token = Column(String, nullable=True)
    task_token_time = Column(Integer, default=0)

    ref_count = Column(Integer, default=0)
    ref_by = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

Base.metadata.create_all(bind=engine)

def ensure_columns():
    with engine.connect() as conn:
        columns = conn.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name='users'
        """)).fetchall()

        existing = [c[0] for c in columns]

        if "username" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR"))

        if "last_task_time" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN last_task_time INTEGER DEFAULT 0"))

        if "task_token" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN task_token VARCHAR"))

        if "task_token_time" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN task_token_time INTEGER DEFAULT 0"))

        conn.commit()

ensure_columns()

@app.route("/")
def root():
    return jsonify({
        "status": "online",
        "message": "EarnFlow server is running"
    })

@app.route("/start_user", methods=["POST"])
def start_user():
    data = request.json or {}

    user_id = str(data.get("user_id"))
    username = str(data.get("username", "")).replace("@", "")
    ref = str(data.get("ref")) if data.get("ref") else None

    if not user_id or user_id == "None":
        return jsonify({"status": "error", "message": "No user_id"}), 400

    session = SessionLocal()
    user = session.query(User).filter(User.user_id == user_id).first()

    if not user:
        user = User(
            user_id=user_id,
            username=username,
            ref_by=ref if ref and ref != user_id else None
        )

        session.add(user)
        session.commit()

        if ref and ref != user_id:
            ref_user = session.query(User).filter(User.user_id == ref).first()

            if ref_user and ref_user.ref_count < REF_LIMIT:
                ref_user.coins += REF_POINT
                ref_user.ref_count += 1
                session.commit()

    else:
        if username:
            user.username = username
            session.commit()

    session.close()

    return jsonify({
        "status": "success",
        "user_id": user_id,
        "username": username
    })

@app.route("/start_task", methods=["POST"])
def start_task():
    data = request.json or {}
    user_id = str(data.get("user_id"))

    if not user_id or user_id == "None":
        return jsonify({"status": "error", "message": "No user_id"}), 400

    session = SessionLocal()
    user = session.query(User).filter(User.user_id == user_id).first()

    if not user:
        session.close()
        return jsonify({"status": "error", "message": "User not found"}), 404

    now = int(time.time())

    if user.tasks_done >= DAILY_TASK_LIMIT:
        session.close()
        return jsonify({
            "status": "blocked",
            "reason": "daily_limit",
            "remaining_tasks": 0
        })

    if user.last_task_time and now - user.last_task_time < COOLDOWN_SECONDS:
        wait = COOLDOWN_SECONDS - (now - user.last_task_time)
        session.close()
        return jsonify({
            "status": "blocked",
            "reason": "cooldown",
            "wait": wait
        })

    token = secrets.token_urlsafe(32)

    user.task_token = token
    user.task_token_time = now
    session.commit()

    session.close()

    return jsonify({
        "status": "success",
        "task_token": token,
        "expires_in": TASK_TOKEN_EXPIRE
    })

@app.route("/add_coin", methods=["POST"])
def add_coin():
    data = request.json or {}

    user_id = str(data.get("user_id"))
    amount = int(data.get("amount", 0))
    task_token = str(data.get("task_token", ""))

    session = SessionLocal()
    user = session.query(User).filter(User.user_id == user_id).first()

    if not user:
        session.close()
        return jsonify({"status": "error", "message": "User not found"}), 404

    now = int(time.time())

    if amount == 0:
        result = {
            "status": "success",
            "coins": user.coins,
            "tasks_done": user.tasks_done,
            "remaining_tasks": user.remaining_tasks,
            "ref_count": user.ref_count
        }
        session.close()
        return jsonify(result)

    if not task_token:
        session.close()
        return jsonify({
            "status": "blocked",
            "reason": "missing_task_token",
            "message": "Invalid claim"
        }), 403

    if not user.task_token or task_token != user.task_token:
        session.close()
        return jsonify({
            "status": "blocked",
            "reason": "invalid_task_token",
            "message": "Invalid claim"
        }), 403

    if now - int(user.task_token_time or 0) > TASK_TOKEN_EXPIRE:
        user.task_token = None
        user.task_token_time = 0
        session.commit()
        session.close()
        return jsonify({
            "status": "blocked",
            "reason": "expired_task_token",
            "message": "Task expired"
        }), 403

    if user.tasks_done >= DAILY_TASK_LIMIT:
        session.close()
        return jsonify({
            "status": "blocked",
            "reason": "daily_limit",
            "wait": 0,
            "coins": user.coins,
            "tasks_done": user.tasks_done,
            "remaining_tasks": 0,
            "ref_count": user.ref_count
        })

    if user.last_task_time and now - user.last_task_time < COOLDOWN_SECONDS:
        wait = COOLDOWN_SECONDS - (now - user.last_task_time)
        session.close()
        return jsonify({
            "status": "blocked",
            "reason": "cooldown",
            "wait": wait,
            "coins": user.coins,
            "tasks_done": user.tasks_done,
            "remaining_tasks": user.remaining_tasks,
            "ref_count": user.ref_count
        })

    user.coins += amount
    user.tasks_done += 1
    user.remaining_tasks = DAILY_TASK_LIMIT - user.tasks_done
    user.last_task_time = now

    user.task_token = None
    user.task_token_time = 0

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

@app.route("/leaderboard", methods=["GET"])
def leaderboard():
    session = SessionLocal()

    users = (
        session.query(User)
        .order_by(User.coins.desc())
        .limit(20)
        .all()
    )

    result = []

    for index, user in enumerate(users, start=1):
        name = user.username if user.username else f"User {user.user_id[-4:]}"

        result.append({
            "rank": index,
            "user_id": user.user_id,
            "username": name,
            "coins": user.coins
        })

    session.close()

    return jsonify({
        "status": "success",
        "leaderboard": result
    })

@app.route("/debug_users", methods=["GET"])
def debug_users():
    session = SessionLocal()
    users = session.query(User).all()

    result = []

    for user in users:
        result.append({
            "user_id": user.user_id,
            "username": user.username,
            "coins": user.coins,
            "tasks_done": user.tasks_done,
            "remaining_tasks": user.remaining_tasks,
            "ref_count": user.ref_count,
            "ref_by": user.ref_by
        })

    session.close()

    return jsonify(result)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
