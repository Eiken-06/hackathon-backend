import os
import random

import google.generativeai as genai
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, text
load_dotenv()

app = FastAPI()

# --- CORS設定 ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Gemini API設定 ---
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)

# =========================================================
# DB SETUP
# =========================================================

def get_db_connection_string() -> str:
    db_user = os.environ.get("DB_USER", "root")
    db_pass = os.environ.get("DB_PASSWORD", "")
    db_name = os.environ.get("DB_NAME", "hackathon")
    instance_connection_name = os.environ.get("INSTANCE_CONNECTION_NAME")

    if instance_connection_name:
        # Cloud Run用 (Unix Socket接続)
        socket_path = f"/cloudsql/{instance_connection_name}"
        return f"mysql+pymysql://{db_user}:{db_pass}@/{db_name}?unix_socket={socket_path}"

    # ローカル開発用 (TCP接続)
    db_host = os.environ.get("DB_HOST", "127.0.0.1")
    db_port = os.environ.get("DB_PORT", "3306")
    return f"mysql+pymysql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"


try:
    engine = create_engine(get_db_connection_string())
    # 接続確認
    with engine.connect() as conn:
        print("Successfully connected to the database.")
except Exception as e:
    print(f"Database connection error: {e}")


# =========================================================
# REQUEST MODELS
# =========================================================

class ItemRequest(BaseModel):
    name: str
    description: str


class InteractionRequest(BaseModel):
    user_id: int
    item_id: int
    event_type: str = "view"


# =========================================================
# HELPERS (※挙動を変えない範囲の整形用)
# =========================================================

def row_to_item_dict(r, score: float = 0.0, category_fallback: str = "Others"):
    """SQLAlchemy Row -> dict（/items の挙動を維持: category が無い場合は Others）"""
    return {
        "id": r.id,
        "name": r.name,
        "description": r.description,
        "price": r.price,
        "category": r.category if hasattr(r, "category") else category_fallback,
        "image_url": r.image_url,
        "score": score,
    }


# =========================================================
# API ENDPOINTS
# =========================================================

@app.get("/")
def read_root():
    return {"message": "Backend with Hybrid Recommendation is running!"}


@app.get("/items")
def get_items():
    """全商品取得（管理画面や一覧用）"""
    try:
        with engine.connect() as connection:
            result = connection.execute(text("SELECT * FROM items LIMIT 100"))
            items = []
            for r in result:
                items.append(row_to_item_dict(r, score=0.0, category_fallback="Others"))
            return items
    except Exception as e:
        return {"error": str(e)}


@app.post("/items")
def create_item(item: ItemRequest):
    """商品を新規出品する"""
    try:
        with engine.connect() as connection:
            new_id = random.randint(100000, 999999)

            connection.execute(
                text(
                    """
                    INSERT INTO items (id, name, description, price, category, image_url)
                    VALUES (:id, :name, :desc, :price, 'New Arrival', '')
                    """
                ),
                {
                    "id": new_id,
                    "name": item.name,
                    "desc": item.description,
                    "price": random.randint(500, 5000),
                },
            )
            connection.commit()
            return {"status": "success", "message": "出品しました！"}
    except Exception as e:
        return {"error": str(e)}


@app.post("/interactions")
def log_interaction(interaction: InteractionRequest):
    """ユーザーの行動ログ（クリック/閲覧）を保存"""
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO interactions (user_id, item_id, event_type)
                    VALUES (:user_id, :item_id, :event_type)
                    """
                ),
                {
                    "user_id": interaction.user_id,
                    "item_id": interaction.item_id,
                    "event_type": interaction.event_type,
                },
            )
            connection.commit()
        return {"message": "Interaction logged"}
    except Exception as e:
        return {"error": str(e)}


@app.get("/recommend/{user_id}")
def get_recommendations(user_id: int):
    """
    推薦API
    1. 機械学習の予測結果(recommendationsテーブル)があればそれを優先
    2. なければ行動ログ(interactions)から好みのカテゴリを分析して推薦
    3. それもなければランダム
    """
    try:
        with engine.connect() as connection:
            recommended_items = []
            reason = "注目の商品です"
            strategy = "random"

            # 1. 【最優先】ML予測テーブル (recommendations) を確認
            ml_result = connection.execute(
                text(
                    """
                    SELECT i.*, r.score, r.reason as ml_reason
                    FROM recommendations r
                    JOIN items i ON r.item_id = i.id
                    WHERE r.user_id = :user_id
                    ORDER BY r.score DESC
                    LIMIT 10
                    """
                ),
                {"user_id": user_id},
            )

            ml_rows = ml_result.fetchall()

            if ml_rows:
                strategy = "ml_collaborative_filtering"
                reason = "あなたの行動履歴から、AIが分析しました！"

                first_row = ml_rows[0]
                ml_reason_val = getattr(first_row, "ml_reason", None)
                if ml_reason_val:
                    reason = ml_reason_val

                for r in ml_rows:
                    recommended_items.append(
                        {
                            "id": r.id,
                            "name": r.name,
                            "description": r.description,
                            "price": r.price,
                            "category": r.category,
                            "image_url": r.image_url,
                            "score": getattr(r, "score", 0.95),
                        }
                    )

            else:
                # 2. 【次点】ルールベース (行動ログからカテゴリ推薦)
                history_result = connection.execute(
                    text(
                        """
                        SELECT i.category, COUNT(*) as count
                        FROM interactions log
                        JOIN items i ON log.item_id = i.id
                        WHERE log.user_id = :user_id
                        GROUP BY i.category
                        ORDER BY count DESC
                        LIMIT 1
                        """
                    ),
                    {"user_id": user_id},
                )

                top_category_row = history_result.fetchone()

                if top_category_row:
                    strategy = "rule_based_category"
                    top_cat = top_category_row[0]
                    reason = f"よく見ている {top_cat} カテゴリのおすすめです"

                    cat_items_res = connection.execute(
                        text("SELECT * FROM items WHERE category = :cat ORDER BY RAND() LIMIT 10"),
                        {"cat": top_cat},
                    )
                    for r in cat_items_res:
                        recommended_items.append(
                            {
                                "id": r.id,
                                "name": r.name,
                                "description": r.description,
                                "price": r.price,
                                "category": r.category,
                                "image_url": r.image_url,
                                "score": 0.8,
                            }
                        )
                else:
                    # 3. 【最終手段】完全ランダム
                    strategy = "random"
                    rand_res = connection.execute(text("SELECT * FROM items ORDER BY RAND() LIMIT 10"))
                    for r in rand_res:
                        recommended_items.append(
                            {
                                "id": r.id,
                                "name": r.name,
                                "description": r.description,
                                "price": r.price,
                                "category": r.category,
                                "image_url": r.image_url,
                                "score": 0.0,
                            }
                        )

            return {
                "user_id": user_id,
                "strategy": strategy,
                "reason": reason,
                "items": recommended_items,
            }

    except Exception as e:
        return {"error": str(e)}


@app.post("/generate-description")
def generate_description(item: ItemRequest):
    """商品クリック時に呼び出されるAI解説生成API"""
    if not GOOGLE_API_KEY:
        return {"comment": "API Key missing"}

    try:
        model = genai.GenerativeModel("gemini-2.5-flash")

        prompt = f"""
        あなたはプロのバイヤーです。
        ユーザーが「{item.name}」という商品に興味を持ってクリックしました。
        商品の説明: {item.description}
        この商品について、40文字程度で推薦してください。
        """

        response = model.generate_content(prompt)
        return {"comment": response.text.strip()}
    except Exception as e:
        return {"comment": f"Error: {str(e)}"}


@app.post("/init-db")
def init_db():
    """DB初期化: ML予測結果を入れる箱(recommendations)も作成"""
    try:
        with engine.connect() as connection:
            # 外部キー制約などが面倒なので、ハッカソンではDROP & CREATEが最強
            connection.execute(text("DROP TABLE IF EXISTS recommendations"))
            connection.execute(text("DROP TABLE IF EXISTS interactions"))
            connection.execute(text("DROP TABLE IF EXISTS items"))

            # 1. Items テーブル (MerRec仕様)
            connection.execute(
                text(
                    """
                    CREATE TABLE items (
                        id BIGINT PRIMARY KEY,
                        name VARCHAR(255),
                        description TEXT,
                        price INT,
                        category VARCHAR(100),
                        brand VARCHAR(100),
                        item_condition VARCHAR(100),
                        image_url VARCHAR(255)
                    )
                    """
                )
            )

            # 2. Interactions テーブル
            connection.execute(
                text(
                    """
                    CREATE TABLE interactions (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        user_id BIGINT,
                        item_id BIGINT,
                        event_type VARCHAR(50),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )

            # 3. Recommendations テーブル (ML予測結果の格納場所)
            connection.execute(
                text(
                    """
                    CREATE TABLE recommendations (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        user_id BIGINT,
                        item_id BIGINT,
                        score FLOAT,
                        reason TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )

            connection.commit()
            return {"message": "All tables initialized! Ready for MerRec data & ML predictions."}

    except Exception as e:
        return {"error": str(e)}


@app.get("/users")
def get_valid_users():
    """recommendationsテーブルにデータがあるユーザーIDのリストを返す"""
    try:
        with engine.connect() as connection:
            result = connection.execute(text("SELECT DISTINCT user_id FROM recommendations ORDER BY user_id"))
            users = [row[0] for row in result]
            return {"users": users}
    except Exception as e:
        return {"error": str(e), "users": []}


@app.post("/purchase")
def purchase_item(interaction: InteractionRequest):
    """
    購入処理API
    実際には決済処理はせず、DBに「purchase」というイベントログを残し、
    フロントエンドに「成功」を返すだけ
    """
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO interactions (user_id, item_id, event_type)
                    VALUES (:user_id, :item_id, 'purchase')
                    """
                ),
                {
                    "user_id": interaction.user_id,
                    "item_id": interaction.item_id,
                },
            )
            connection.commit()

            return {
                "status": "success",
                "message": "ご購入ありがとうございます！",
                "receipt_id": f"REC-{random.randint(10000, 99999)}",
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/register")
def register_user():
    """新規ユーザーを作成してIDを返す"""
    try:
        with engine.connect() as connection:
            result = connection.execute(text("SELECT MAX(user_id) FROM interactions"))
            max_id = result.scalar() or 10000
            new_user_id = int(max_id) + 1

            connection.execute(
                text("INSERT INTO interactions (user_id, item_id, event_type) VALUES (:uid, 0, 'register')"),
                {"uid": new_user_id},
            )
            connection.commit()

            return {"user_id": new_user_id, "message": "登録完了！"}
    except Exception as e:
        return {"error": str(e)}
