from flask import Flask, request, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine, Column, Integer, String, DateTime, text, func as sa_func
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.sql import func
from datetime import datetime, timedelta
from collections import defaultdict
import os
import time
import secrets
import requests

app = Flask(__name__)
CORS(app)

# =======================
# CONFIG
# =======================
DATABASE_URL = "postgresql://postgres:VeHwVtiMUtrLddWDoPoGggYAyupuASZS@turntable.proxy.rlwy.net:27947/railway"

BOT_TOKEN = "8707863883:AAGePtyGNttlo3EfLT1GXGKlBqFY9TBQ5G0"
CHANNEL_USERNAME = "@earnflowtaps"
CHANNEL_REWARD = 15

# GANTI INI NANTI KALAU SUDAH MAU PUBLIC
ADMIN_KEY = "earnflow_admin_2026"

# Anti Multi Account V1
MAX_ACCOUNTS_PER_IP = 3
SUSPICIOUS_SCORE_IP = 30
SUSPICIOUS_SCORE_REF = 40

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

MIN_WITHDRAW_POINTS = 10000

# =======================
# DATABASE TABLE
# =======================
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

    # Admin Panel V1
    is_banned = Column(Integer, default=0)
    admin_note = Column(String, nullable=True)

    # Anti Multi Account V1
    ip_address = Column(String, nullable=True)
    device_id = Column(String, nullable=True)
    suspicious_score = Column(Integer, default=0)
    suspicious_reason = Column(String, nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WithdrawRequest(Base):
    __tablename__ = "withdraw_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, index=True)
    username = Column(String, nullable=True)
    method = Column(String, nullable=True)
    account = Column(String, nullable=True)
    amount = Column(Integer, default=0)
    status = Column(String, default="pending")
    admin_note = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True)

Base.metadata.create_all(bind=engine)

# =======================
# MIGRATION SAFE COLUMNS
# =======================
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

        if "is_banned" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0"))

        if "admin_note" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN admin_note VARCHAR"))

        if "ip_address" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN ip_address VARCHAR"))

        if "device_id" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN device_id VARCHAR"))

        if "suspicious_score" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN suspicious_score INTEGER DEFAULT 0"))

        if "suspicious_reason" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN suspicious_reason VARCHAR"))

        if "last_seen_at" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN last_seen_at TIMESTAMP"))

        conn.execute(text("""
            UPDATE users
            SET total_ref_count = ref_count
            WHERE total_ref_count = 0 AND ref_count > 0
        """))

        conn.commit()

ensure_columns()

# =======================
# HELPERS
# =======================
def admin_ok(req):
    key = req.args.get("key") or (req.json or {}).get("key") if req.is_json else req.args.get("key")
    return key == ADMIN_KEY

def get_client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or ""

def update_user_tracking(session, user, data=None):
    data = data or {}

    ip = get_client_ip()
    device_id = str(data.get("device_id", "")).strip()

    if ip:
        user.ip_address = ip

    if device_id:
        user.device_id = device_id

    user.last_seen_at = datetime.utcnow()

    reasons = []
    score = 0

    # Same IP used by too many accounts
    if ip:
        same_ip_count = session.query(User).filter(User.ip_address == ip).count()
        if same_ip_count >= MAX_ACCOUNTS_PER_IP:
            score += SUSPICIOUS_SCORE_IP
            reasons.append(f"same_ip_{same_ip_count}_accounts")

    # Referral from same IP
    if user.ref_by and ip:
        ref_user = session.query(User).filter(User.user_id == user.ref_by).first()
        if ref_user and ref_user.ip_address and ref_user.ip_address == ip:
            score += SUSPICIOUS_SCORE_REF
            reasons.append("referral_same_ip")

    if score > int(user.suspicious_score or 0):
        user.suspicious_score = score
        user.suspicious_reason = ", ".join(reasons)

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
        "is_banned": int(user.is_banned or 0),
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

def admin_user_json(user):
    total_refs = user.total_ref_count if user.total_ref_count else user.ref_count
    return {
        "user_id": user.user_id,
        "username": user.username or "",
        "coins": int(user.coins or 0),
        "tasks_done": int(user.tasks_done or 0),
        "remaining_tasks": int(user.remaining_tasks or 0),
        "daily_streak": int(user.daily_streak or 0),
        "joined_channel_claimed": int(user.joined_channel_claimed or 0),
        "total_ref_count": int(total_refs or 0),
        "today_ref_count": int(user.today_ref_count or 0),
        "ref_by": user.ref_by or "",
        "is_banned": int(user.is_banned or 0),
        "admin_note": user.admin_note or "",
        "ip_address": user.ip_address or "",
        "device_id": user.device_id or "",
        "suspicious_score": int(user.suspicious_score or 0),
        "suspicious_reason": user.suspicious_reason or "",
        "last_seen_at": str(user.last_seen_at) if user.last_seen_at else "",
        "created_at": str(user.created_at) if user.created_at else ""
    }

# =======================
# PUBLIC ROUTES
# =======================
@app.route("/")
def root():
    return jsonify({
        "status": "online",
        "message": "EarnFlow server is running",
        "reset_time": "03:00 WIB",
        "channel": CHANNEL_USERNAME,
        "admin": "Admin Panel V1 endpoints active"
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

        update_user_tracking(session, user, data)
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

        update_user_tracking(session, user, data)
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

    if int(user.is_banned or 0) == 1:
        session.close()
        return jsonify({"status": "blocked", "reason": "banned", "message": "User banned"}), 403

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

    if int(user.is_banned or 0) == 1:
        session.close()
        return jsonify({"status": "blocked", "reason": "banned", "message": "User banned"}), 403

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

    if int(user.is_banned or 0) == 1:
        session.close()
        return jsonify({"status": "blocked", "reason": "banned", "message": "User banned"}), 403

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

    if int(user.is_banned or 0) == 1:
        session.close()
        return jsonify({"status": "blocked", "reason": "banned", "message": "User banned"}), 403

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


# =======================
# WITHDRAW REQUEST V1
# =======================
@app.route("/request_withdraw", methods=["POST"])
def request_withdraw():
    data = request.json or {}

    user_id = str(data.get("user_id", "")).strip()
    method = str(data.get("method", "")).strip()
    account = str(data.get("account", "")).strip()
    amount = int(data.get("amount", 0))

    if not user_id or user_id == "None":
        return jsonify({"status": "error", "message": "No user_id"}), 400

    if not method:
        return jsonify({"status": "error", "message": "Select withdraw method"}), 400

    if not account:
        return jsonify({"status": "error", "message": "Enter account or wallet"}), 400

    if amount < MIN_WITHDRAW_POINTS:
        return jsonify({
            "status": "blocked",
            "reason": "minimum",
            "message": f"Minimum withdraw is {MIN_WITHDRAW_POINTS} points"
        }), 400

    session = SessionLocal()
    user = session.query(User).filter(User.user_id == user_id).first()

    if not user:
        session.close()
        return jsonify({"status": "error", "message": "User not found"}), 404

    if int(user.is_banned or 0) == 1:
        session.close()
        return jsonify({"status": "blocked", "reason": "banned", "message": "User banned"}), 403

    if int(user.coins or 0) < amount:
        session.close()
        return jsonify({
            "status": "blocked",
            "reason": "insufficient_points",
            "message": "Not enough points"
        }), 400

    # Prevent many pending withdraws
    pending = (
        session.query(WithdrawRequest)
        .filter(WithdrawRequest.user_id == user_id)
        .filter(WithdrawRequest.status == "pending")
        .first()
    )

    if pending:
        session.close()
        return jsonify({
            "status": "blocked",
            "reason": "pending_exists",
            "message": "You already have a pending withdraw request"
        }), 400

    user.coins -= amount

    wd = WithdrawRequest(
        user_id=user.user_id,
        username=user.username,
        method=method,
        account=account,
        amount=amount,
        status="pending"
    )

    session.add(wd)
    session.commit()

    result = {
        "status": "success",
        "message": "Withdraw request submitted",
        "withdraw_id": wd.id,
        "coins": user.coins,
        **user_response(user)
    }

    session.close()
    return jsonify(result)

@app.route("/my_withdraws", methods=["POST"])
def my_withdraws():
    data = request.json or {}
    user_id = str(data.get("user_id", "")).strip()

    if not user_id or user_id == "None":
        return jsonify({"status": "error", "message": "No user_id"}), 400

    session = SessionLocal()

    rows = (
        session.query(WithdrawRequest)
        .filter(WithdrawRequest.user_id == user_id)
        .order_by(WithdrawRequest.created_at.desc())
        .limit(20)
        .all()
    )

    result = []
    for w in rows:
        result.append({
            "id": w.id,
            "method": w.method,
            "account": w.account,
            "amount": int(w.amount or 0),
            "status": w.status,
            "admin_note": w.admin_note or "",
            "created_at": str(w.created_at) if w.created_at else "",
            "updated_at": str(w.updated_at) if w.updated_at else ""
        })

    session.close()

    return jsonify({
        "status": "success",
        "withdraws": result
    })


# =======================
# ADMIN PANEL API V1
# =======================
@app.route("/admin_stats", methods=["GET"])
def admin_stats():
    if not admin_ok(request):
        return jsonify({"status": "error", "message": "Invalid admin key"}), 403

    session = SessionLocal()
    total_users = session.query(User).count()
    total_points = session.query(sa_func.coalesce(sa_func.sum(User.coins), 0)).scalar()
    total_tasks_done = session.query(sa_func.coalesce(sa_func.sum(User.tasks_done), 0)).scalar()
    total_referrals = session.query(sa_func.coalesce(sa_func.sum(User.total_ref_count), 0)).scalar()
    total_today_referrals = session.query(sa_func.coalesce(sa_func.sum(User.today_ref_count), 0)).scalar()
    total_banned = session.query(User).filter(User.is_banned == 1).count()
    channel_claimed = session.query(User).filter(User.joined_channel_claimed == 1).count()
    suspicious_users = session.query(User).filter(User.suspicious_score >= 30).count()
    pending_withdraws = session.query(WithdrawRequest).filter(WithdrawRequest.status == "pending").count()
    approved_withdraws = session.query(WithdrawRequest).filter(WithdrawRequest.status == "approved").count()
    rejected_withdraws = session.query(WithdrawRequest).filter(WithdrawRequest.status == "rejected").count()

    session.close()

    return jsonify({
        "status": "success",
        "stats": {
            "total_users": int(total_users or 0),
            "total_points": int(total_points or 0),
            "total_tasks_done_today": int(total_tasks_done or 0),
            "total_referrals": int(total_referrals or 0),
            "today_referrals": int(total_today_referrals or 0),
            "banned_users": int(total_banned or 0),
            "channel_claimed": int(channel_claimed or 0),
            "suspicious_users": int(suspicious_users or 0),
            "pending_withdraws": int(pending_withdraws or 0),
            "approved_withdraws": int(approved_withdraws or 0),
            "rejected_withdraws": int(rejected_withdraws or 0),
            "reset_day": reset_day_wib()
        }
    })

@app.route("/admin_users", methods=["GET"])
def admin_users():
    if not admin_ok(request):
        return jsonify({"status": "error", "message": "Invalid admin key"}), 403

    q = (request.args.get("q") or "").strip()
    limit = int(request.args.get("limit", 50))

    if limit > 200:
        limit = 200

    session = SessionLocal()
    query = session.query(User)

    if q:
        like = f"%{q}%"
        query = query.filter(
            (User.user_id.ilike(like)) |
            (User.username.ilike(like)) |
            (User.ref_by.ilike(like))
        )

    users = query.order_by(User.coins.desc()).limit(limit).all()
    result = [admin_user_json(user) for user in users]

    session.close()

    return jsonify({
        "status": "success",
        "users": result
    })

@app.route("/admin_update_user", methods=["POST"])
def admin_update_user():
    if not admin_ok(request):
        return jsonify({"status": "error", "message": "Invalid admin key"}), 403

    data = request.json or {}
    user_id = str(data.get("user_id", "")).strip()

    if not user_id:
        return jsonify({"status": "error", "message": "Missing user_id"}), 400

    session = SessionLocal()
    user = session.query(User).filter(User.user_id == user_id).first()

    if not user:
        session.close()
        return jsonify({"status": "error", "message": "User not found"}), 404

    action = data.get("action")
    amount = int(data.get("amount", 0))
    note = str(data.get("note", "")).strip()

    if action == "add_points":
        user.coins += amount

    elif action == "remove_points":
        user.coins = max(0, int(user.coins or 0) - amount)

    elif action == "set_points":
        user.coins = max(0, amount)

    elif action == "reset_tasks":
        user.tasks_done = 0
        user.remaining_tasks = DAILY_TASK_LIMIT
        user.last_task_time = 0
        user.task_token = None
        user.task_token_time = 0

    elif action == "ban":
        user.is_banned = 1

    elif action == "unban":
        user.is_banned = 0

    elif action == "note":
        user.admin_note = note

    else:
        session.close()
        return jsonify({"status": "error", "message": "Invalid action"}), 400

    if note and action != "note":
        user.admin_note = note

    session.commit()
    result = admin_user_json(user)
    session.close()

    return jsonify({
        "status": "success",
        "user": result
    })

@app.route("/admin_suspicious", methods=["GET"])
def admin_suspicious():
    if not admin_ok(request):
        return jsonify({"status": "error", "message": "Invalid admin key"}), 403

    session = SessionLocal()

    users = (
        session.query(User)
        .filter(User.suspicious_score >= 30)
        .order_by(User.suspicious_score.desc(), User.coins.desc())
        .limit(200)
        .all()
    )

    result = [admin_user_json(user) for user in users]

    session.close()

    return jsonify({
        "status": "success",
        "users": result
    })


@app.route("/admin_withdraws", methods=["GET"])
def admin_withdraws():
    if not admin_ok(request):
        return jsonify({"status": "error", "message": "Invalid admin key"}), 403

    status = (request.args.get("status") or "pending").strip()
    limit = int(request.args.get("limit", 100))

    if limit > 300:
        limit = 300

    session = SessionLocal()
    query = session.query(WithdrawRequest)

    if status != "all":
        query = query.filter(WithdrawRequest.status == status)

    rows = query.order_by(WithdrawRequest.created_at.desc()).limit(limit).all()

    result = []
    for w in rows:
        result.append({
            "id": w.id,
            "user_id": w.user_id,
            "username": w.username or "",
            "method": w.method or "",
            "account": w.account or "",
            "amount": int(w.amount or 0),
            "status": w.status,
            "admin_note": w.admin_note or "",
            "created_at": str(w.created_at) if w.created_at else "",
            "updated_at": str(w.updated_at) if w.updated_at else ""
        })

    session.close()

    return jsonify({
        "status": "success",
        "withdraws": result
    })

@app.route("/admin_update_withdraw", methods=["POST"])
def admin_update_withdraw():
    if not admin_ok(request):
        return jsonify({"status": "error", "message": "Invalid admin key"}), 403

    data = request.json or {}
    withdraw_id = int(data.get("withdraw_id", 0))
    action = str(data.get("action", "")).strip()
    note = str(data.get("note", "")).strip()

    if not withdraw_id:
        return jsonify({"status": "error", "message": "Missing withdraw_id"}), 400

    session = SessionLocal()
    wd = session.query(WithdrawRequest).filter(WithdrawRequest.id == withdraw_id).first()

    if not wd:
        session.close()
        return jsonify({"status": "error", "message": "Withdraw not found"}), 404

    if wd.status != "pending":
        session.close()
        return jsonify({"status": "blocked", "message": "Withdraw already processed"}), 400

    user = session.query(User).filter(User.user_id == wd.user_id).first()

    if action == "approve":
        wd.status = "approved"
        wd.admin_note = note

    elif action == "reject":
        wd.status = "rejected"
        wd.admin_note = note

        # Refund points on reject
        if user:
            user.coins += int(wd.amount or 0)

    else:
        session.close()
        return jsonify({"status": "error", "message": "Invalid action"}), 400

    wd.updated_at = datetime.utcnow()
    session.commit()

    result = {
        "id": wd.id,
        "status": wd.status,
        "user_id": wd.user_id,
        "amount": int(wd.amount or 0)
    }

    session.close()

    return jsonify({
        "status": "success",
        "withdraw": result
    })


@app.route("/admin_referrals", methods=["GET"])
def admin_referrals():
    if not admin_ok(request):
        return jsonify({"status": "error", "message": "Invalid admin key"}), 403

    session = SessionLocal()
    users = (
        session.query(User)
        .filter(User.ref_by != None)
        .order_by(User.created_at.desc())
        .limit(200)
        .all()
    )

    result = []
    for user in users:
        result.append({
            "new_user_id": user.user_id,
            "new_username": user.username or "",
            "ref_by": user.ref_by or "",
            "created_at": str(user.created_at) if user.created_at else ""
        })

    session.close()

    return jsonify({
        "status": "success",
        "referrals": result
    })

# =======================
# DEBUG ROUTES
# =======================
@app.route("/debug_users", methods=["GET"])
def debug_users():
    session = SessionLocal()
    users = session.query(User).all()

    result = []

    for user in users:
        result.append(admin_user_json(user))

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
