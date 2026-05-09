from flask import Flask, request, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine, Column, Integer, String, DateTime, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.sql import func
from datetime import datetime, timedelta
import os
import time
import secrets
import requests

app = Flask(__name__)
CORS(app)

DATABASE_URL = "postgresql://postgres:VeHwVtiMUtrLddWDoPoGggYAyupuASZS@turntable.proxy.rlwy.net:27947/railway"

BOT_TOKEN = "8707863883:AAGePtyGNttlo3EfLT1GXGKlBqFY9TBQ5G0"
CHANNEL_USERNAME = "@earnflowtaps"
CHANNEL_REWARD = 15

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

DAILY_TASK_LIMIT = 30
COOLDOWN_SECONDS = 15
TASK_TOKEN_EXPIRE = 60

REF_LIMIT = 20
REF_POINT = 10
RESET_HOUR_WIB = 3

CHECKIN_REWARDS = [2, 6, 8, 10, 14, 18, 25]

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

    last_reset_day = Column(String, default="")

    daily_streak = Column(Integer, default=0)
    last_checkin_day = Column(String, default="")

    joined_channel_claimed = Column(Integer, default=0)

    ref_count = Column(Integer, default=0)
    total_ref_count = Column(Integer, default=0)
    today_ref_count = Column(Integer, default=0)
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

        if "last_reset_day" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN last_reset_day VARCHAR DEFAULT ''"))

        if "total_ref_count" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN total_ref_count INTEGER DEFAULT 0"))

        if "today_ref_count" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN today_ref_count INTEGER DEFAULT 0"))

        if "daily_streak" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN daily_streak INTEGER DEFAULT 0"))

        if "last_checkin_day" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN last_checkin_day VARCHAR DEFAULT ''"))

        if "joined_channel_claimed" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN joined_channel_claimed INTEGER DEFAULT 0"))

        conn.execute(text("""
            UPDATE users
            SET total_ref_count = ref_count
            WHERE total_ref_count = 0 AND ref_count > 0
        """))

        conn.commit()

ensure_columns()

def reset_day_wib():
    now_wib = datetime.utcnow() + timedelta(hours=7)

    if now_wib.hour < RESET_HOUR_WIB:
        reset_day = now_wib.date() - timedelta(days=1)
    else:
        reset_day = now_wib.date()

    return reset_day.isoformat()

def yesterday_reset_day_wib():
    today = datetime.fromisoformat(reset_day_wib()).date()
    return (today - timedelta(days=1)).isoformat()

def reset_daily_if_needed(user):
    today_key = reset_day_wib()

    if not user.last_reset_day:
        user.last_reset_day = today_key
        return

    if user.last_reset_day != today_key:
        user.tasks_done = 0
        user.remaining_tasks = DAILY_TASK_LIMIT
        user.last_task_time = 0
        user.task_token = None
        user.task_token_time = 0
        user.today_ref_count = 0
        user.last_reset_day = today_key

def checkin_info(user):
    today_key = reset_day_wib()
    yesterday_key = yesterday_reset_day_wib()

    already_claimed = user.last_checkin_day == today_key
    streak = int(user.daily_streak or 0)

    if user.last_checkin_day and user.last_checkin_day not in [today_key, yesterday_key]:
        streak = 0

    next_day = streak + 1

    if next_day > 7:
        next_day = 1

    reward = CHECKIN_REWARDS[next_day - 1]

    return {
        "daily_streak": streak,
        "next_checkin_day": next_day,
        "next_checkin_reward": reward,
        "last_checkin_day": user.last_checkin_day or "",
        "can_checkin": not already_claimed,
        "already_claimed": already_claimed
    }

def user_response(user):
    total_refs = user.total_ref_count if user.total_ref_count else user.ref_count

    return {
        "coins": user.coins,
        "tasks_done": user.tasks_done,
        "remaining_tasks": user.remaining_tasks,
        "ref_count": total_refs,
        "total_ref_count": total_refs,
        "today_ref_count": user.today_ref_count,
        "joined_channel_claimed": int(user.joined_channel_claimed or 0),
        "channel_username": CHANNEL_USERNAME,
        "channel_reward": CHANNEL_REWARD,
        **checkin_info(user)
    }

def is_user_in_channel(user_id):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember"

        response = requests.get(
            url,
            params={
                "chat_id": CHANNEL_USERNAME,
                "user_id": user_id
            },
            timeout=15
        )

        data = response.json()

        if not data.get("ok"):
            return False, data

        status = data.get("result", {}).get("status", "")

        if status in ["member", "administrator", "creator"]:
            return True, data

        return False, data

    except Exception as e:
        return False, {"error": str(e)}

@app.route("/")
def root():
    return jsonify({
        "status": "online",
        "message": "EarnFlow server is running",
        "reset_time": "03:00 WIB",
        "channel": CHANNEL_USERNAME
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
            ref_by=ref if ref and ref != user_id else None,
            last_reset_day=reset_day_wib()
        )

        session.add(user)
        session.commit()

        if ref and ref != user_id:
            ref_user = session.query(User).filter(User.user_id == ref).first()

            if ref_user:
                reset_daily_if_needed(ref_user)

                if ref_user.today_ref_count < REF_LIMIT:
                    ref_user.coins += REF_POINT
                    ref_user.total_ref_count += 1
                    ref_user.today_ref_count += 1
                    ref_user.ref_count = ref_user.total_ref_count
                    session.commit()

    else:
        reset_daily_if_needed(user)

        if username:
            user.username = username

        session.commit()

    result = {
        "status": "success",
        "user_id": user_id,
        "username": username,
        **user_response(user)
    }

    session.close()
    return jsonify(result)

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

    reset_daily_if_needed(user)
    session.commit()

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

    reset_daily_if_needed(user)
    session.commit()

    now = int(time.time())

    if amount == 0:
        result = {
            "status": "success",
            **user_response(user)
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
        result = {
            "status": "blocked",
            "reason": "daily_limit",
            "wait": 0,
            **user_response(user)
        }
        result["remaining_tasks"] = 0
        session.close()
        return jsonify(result)

    if user.last_task_time and now - user.last_task_time < COOLDOWN_SECONDS:
        wait = COOLDOWN_SECONDS - (now - user.last_task_time)
        result = {
            "status": "blocked",
            "reason": "cooldown",
            "wait": wait,
            **user_response(user)
        }
        session.close()
        return jsonify(result)

    user.coins += amount
    user.tasks_done += 1
    user.remaining_tasks = DAILY_TASK_LIMIT - user.tasks_done
    user.last_task_time = now

    user.task_token = None
    user.task_token_time = 0

    session.commit()

    result = {
        "status": "success",
        **user_response(user)
    }

    session.close()
    return jsonify(result)

@app.route("/claim_checkin", methods=["POST"])
def claim_checkin():
    data = request.json or {}
    user_id = str(data.get("user_id"))

    if not user_id or user_id == "None":
        return jsonify({"status": "error", "message": "No user_id"}), 400

    session = SessionLocal()
    user = session.query(User).filter(User.user_id == user_id).first()

    if not user:
        session.close()
        return jsonify({"status": "error", "message": "User not found"}), 404

    reset_daily_if_needed(user)

    today_key = reset_day_wib()
    yesterday_key = yesterday_reset_day_wib()

    if user.last_checkin_day == today_key:
        result = {
            "status": "blocked",
            "reason": "already_claimed",
            "message": "Daily check-in already claimed",
            **user_response(user)
        }
        session.commit()
        session.close()
        return jsonify(result)

    if user.last_checkin_day == yesterday_key:
        streak = int(user.daily_streak or 0) + 1
    else:
        streak = 1

    if streak > 7:
        streak = 1

    reward = CHECKIN_REWARDS[streak - 1]

    user.coins += reward
    user.daily_streak = streak
    user.last_checkin_day = today_key

    session.commit()

    result = {
        "status": "success",
        "reward": reward,
        **user_response(user)
    }

    session.close()
    return jsonify(result)

@app.route("/verify_channel", methods=["POST"])
def verify_channel():
    data = request.json or {}
    user_id = str(data.get("user_id"))

    if not user_id or user_id == "None":
        return jsonify({"status": "error", "message": "No user_id"}), 400

    session = SessionLocal()
    user = session.query(User).filter(User.user_id == user_id).first()

    if not user:
        session.close()
        return jsonify({"status": "error", "message": "User not found"}), 404

    reset_daily_if_needed(user)

    if int(user.joined_channel_claimed or 0) == 1:
        result = {
            "status": "blocked",
            "reason": "already_claimed",
            "message": "Channel reward already claimed",
            **user_response(user)
        }
        session.commit()
        session.close()
        return jsonify(result)

    is_member, tg_response = is_user_in_channel(user_id)

    if not is_member:
        result = {
            "status": "blocked",
            "reason": "not_joined",
            "message": "Please join the channel first",
            "telegram_response": tg_response,
            **user_response(user)
        }
        session.commit()
        session.close()
        return jsonify(result)

    user.coins += CHANNEL_REWARD
    user.joined_channel_claimed = 1

    session.commit()

    result = {
        "status": "success",
        "reward": CHANNEL_REWARD,
        "message": "Channel task completed",
        **user_response(user)
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
        total_refs = user.total_ref_count if user.total_ref_count else user.ref_count

        result.append({
            "user_id": user.user_id,
            "username": user.username,
            "coins": user.coins,
            "tasks_done": user.tasks_done,
            "remaining_tasks": user.remaining_tasks,
            "last_reset_day": user.last_reset_day,
            "daily_streak": user.daily_streak,
            "last_checkin_day": user.last_checkin_day,
            "joined_channel_claimed": user.joined_channel_claimed,
            "total_ref_count": total_refs,
            "today_ref_count": user.today_ref_count,
            "ref_by": user.ref_by
        })

    session.close()
    return jsonify(result)

@app.route("/debug_reset_time", methods=["GET"])
def debug_reset_time():
    now_wib = datetime.utcnow() + timedelta(hours=7)

    return jsonify({
        "status": "success",
        "now_wib": now_wib.strftime("%Y-%m-%d %H:%M:%S"),
        "reset_hour_wib": RESET_HOUR_WIB,
        "current_reset_day": reset_day_wib()
    })

@app.route("/debug_channel", methods=["GET"])
def debug_channel():
    return jsonify({
        "status": "success",
        "channel_username": CHANNEL_USERNAME,
        "channel_reward": CHANNEL_REWARD
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
