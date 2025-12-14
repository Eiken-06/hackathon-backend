import os
import random
import google.generativeai as genai
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import create_engine, text
from pydantic import BaseModel

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

# --- DB接続設定 ---
def get_db_connection_string():
    db_user = os.environ.get("DB_USER", "root")
    db_pass = os.environ.get("DB_PASSWORD", "")
    db_name = os.environ.get("DB_NAME", "hackathon")
    instance_connection_name = os.environ.get("INSTANCE_CONNECTION_NAME")

    if instance_connection_name:
        # Cloud Run用
        socket_path = f"/cloudsql/{instance_connection_name}"
        return f"mysql+pymysql://{db_user}:{db_pass}@/{db_name}?unix_socket={socket_path}"
    else:
        # ローカル開発用
        db_host = os.environ.get("DB_HOST", "127.0.0.1")
        db_port = os.environ.get("DB_PORT", "3306")
        return f"mysql+pymysql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"

engine = create_engine(get_db_connection_string())

# --- リクエスト型定義 ---
class ItemRequest(BaseModel):
    name: str
    description: str

class InteractionRequest(BaseModel):
    user_id: int
    item_id: int
    event_type: str = "view"

# --- APIエンドポイント ---

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
                items.append({
                    "id": r.id,
                    "name": r.name,
                    "description": r.description,
                    "price": r.price,
                    "category": r.category if hasattr(r, 'category') else "Others",
                    "image_url": r.image_url
                })
            return items
    except Exception as e:
        return {"error": str(e)}

@app.post("/interactions")
def log_interaction(interaction: InteractionRequest):
    """ユーザーの行動ログ（クリック/閲覧）を保存"""
    try:
        with engine.connect() as connection:
            connection.execute(
                text("""
                    INSERT INTO interactions (user_id, item_id, event_type)
                    VALUES (:user_id, :item_id, :event_type)
                """),
                {
                    "user_id": interaction.user_id,
                    "item_id": interaction.item_id,
                    "event_type": interaction.event_type
                }
            )
            connection.commit()
        return {"message": "Interaction logged"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/recommend/{user_id}")
def get_recommendations(user_id: int):
    """
    【最重要】ハイブリッド推薦API
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
            # 別途Pythonスクリプト(Colab等)で計算してINSERTされたデータを読み込む
            ml_result = connection.execute(
                text("""
                    SELECT i.*, r.score, r.reason as ml_reason
                    FROM recommendations r
                    JOIN items i ON r.item_id = i.id
                    WHERE r.user_id = :user_id
                    ORDER BY r.score DESC
                    LIMIT 10
                """),
                {"user_id": user_id}
            )
            
            ml_rows = ml_result.fetchall()
            
            if ml_rows:
                # MLの予測データがある場合
                strategy = "ml_collaborative_filtering"
                reason = "あなたの行動履歴から、AIが分析しました！"
                if hasattr(ml_rows[0], 'ml_reason') and ml_rows[0].ml_reason:
                     reason = ml_rows[0].ml_reason

                for r in ml_rows:
                    recommended_items.append({
                        "id": r.id,
                        "name": r.name,
                        "description": r.description,
                        "price": r.price,
                        "category": r.category,
                        "image_url": r.image_url
                    })
            
            else:
                # 2. 【次点】ルールベース (行動ログからカテゴリ推薦)
                history_result = connection.execute(
                    text("""
                        SELECT i.category, COUNT(*) as count
                        FROM interactions log
                        JOIN items i ON log.item_id = i.id
                        WHERE log.user_id = :user_id
                        GROUP BY i.category
                        ORDER BY count DESC
                        LIMIT 1
                    """),
                    {"user_id": user_id}
                )
                
                top_category_row = history_result.fetchone()
                
                if top_category_row:
                    strategy = "rule_based_category"
                    top_cat = top_category_row[0]
                    reason = f"よく見ている {top_cat} カテゴリのおすすめです"
                    
                    # そのカテゴリの商品を取得
                    cat_items_res = connection.execute(
                        text("SELECT * FROM items WHERE category = :cat ORDER BY RAND() LIMIT 10"),
                        {"cat": top_cat}
                    )
                    for r in cat_items_res:
                         recommended_items.append({
                            "id": r.id, "name": r.name, "description": r.description,
                            "price": r.price, "category": r.category, "image_url": r.image_url
                        })
                else:
                    # 3. 【最終手段】完全ランダム
                    strategy = "random"
                    rand_res = connection.execute(text("SELECT * FROM items ORDER BY RAND() LIMIT 10"))
                    for r in rand_res:
                         recommended_items.append({
                            "id": r.id, "name": r.name, "description": r.description,
                            "price": r.price, "category": r.category, "image_url": r.image_url
                        })

            return {
                "user_id": user_id,
                "strategy": strategy,
                "reason": reason,
                "items": recommended_items
            }

    except Exception as e:
        return {"error": str(e)}

@app.post("/generate-description")
def generate_description(item: ItemRequest):
    """
    商品クリック時に呼び出されるAI解説生成API
    """
    if not GOOGLE_API_KEY:
        return {"comment": "API Key missing"}
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        
        # プロンプトエンジニアリング: 
        # ただの説明ではなく「なぜこのユーザーにおすすめなのか」を捏造気味に熱弁させる
        prompt = f"""
        あなたはプロのバイヤーAIです。
        ユーザーが「{item.name}」という商品に興味を持ってクリックしました。
        商品の説明: {item.description}
        
        この商品がなぜ素晴らしいのか、ユーザーに語りかけるような口調で、
        30文字〜50文字程度の「ひとこと推薦コメント」を作成してください。
        最後に絵文字を1つ添えてください。
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
            connection.execute(text("""
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
            """))
            
            # 2. Interactions テーブル
            connection.execute(text("""
                CREATE TABLE interactions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id BIGINT,
                    item_id BIGINT,
                    event_type VARCHAR(50),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            # 3. Recommendations テーブル (ML予測結果の格納場所)
            connection.execute(text("""
                CREATE TABLE recommendations (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id BIGINT,
                    item_id BIGINT,
                    score FLOAT,
                    reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            
            connection.commit()
            return {"message": "All tables initialized! Ready for MerRec data & ML predictions."}
            
    except Exception as e:
        return {"error": str(e)}