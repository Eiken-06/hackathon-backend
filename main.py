import os
import random
import google.generativeai as genai
from fastapi import FastAPI, HTTPException
from dotenv import load_dotenv
load_dotenv()
from fastapi.middleware.cors import CORSMiddleware
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

# --- DB接続設定 (前回と同じ) ---
def get_db_connection_string():
    db_user = os.environ.get("DB_USER", "root")
    db_pass = os.environ.get("DB_PASSWORD", "")
    db_name = os.environ.get("DB_NAME", "hackathon")
    instance_connection_name = os.environ.get("INSTANCE_CONNECTION_NAME")

    if instance_connection_name:
        socket_path = f"/cloudsql/{instance_connection_name}"
        return f"mysql+pymysql://{db_user}:{db_pass}@/{db_name}?unix_socket={socket_path}"
    else:
        db_host = os.environ.get("DB_HOST", "127.0.0.1")
        db_port = os.environ.get("DB_PORT", "3306")
        return f"mysql+pymysql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"

engine = create_engine(get_db_connection_string())

# --- リクエスト型定義 ---
class ItemRequest(BaseModel):
    name: str
    description: str

# ★追加: 行動ログ記録用
class InteractionRequest(BaseModel):
    user_id: int
    item_id: int
    event_type: str = "view"  # view, like, purchase など

# --- APIエンドポイント ---

@app.get("/")
def read_root():
    return {"message": "Backend with Recommendation Engine is running!"}

# ★変更: カテゴリ情報も含めて商品を返す
@app.get("/items")
def get_items():
    try:
        with engine.connect() as connection:
            # categoryカラムがあれば取得、なければ無視するように書くのが安全だが今回は作成前提
            result = connection.execute(text("SELECT * FROM items"))
            items = []
            for row in result:
                items.append({
                    "id": row.id,
                    "name": row.name,
                    "description": row.description,
                    "price": row.price,
                    "category": row.category if hasattr(row, 'category') else "Others", # カテゴリ追加
                    "image_url": row.image_url
                })
            return items
    except Exception as e:
        return {"error": str(e)}

# ★新機能: 行動ログを保存するAPI
@app.post("/interactions")
def log_interaction(interaction: InteractionRequest):
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

@app.post("/generate-description")
def generate_description(item: ItemRequest):
    """Geminiに商品の魅力的な紹介文を作らせる"""
    if not GOOGLE_API_KEY:
        return {"comment": "Error: API Key not set."}
    
    try:
        # 高速なモデルを使用
        model = genai.GenerativeModel("gemini-2.5")
        
        prompt = f"""
        あなたはカリスマ店員です。以下の商品を、お客様が買いたくなるような短いセールストーク（60文字以内）で紹介してください。
        最後に必ず関連する絵文字を1つ付けてください。
        
        商品名: {item.name}
        特徴: {item.description}
        """
        
        response = model.generate_content(prompt)
        return {"comment": response.text.strip()}
        
    except Exception as e:
        return {"comment": f"AI Error: {str(e)}"}

# ★新機能: 履歴に基づいたレコメンドAPI (簡易版協調フィルタリング/コンテンツベース)
@app.get("/recommend/{user_id}")
def get_recommendations(user_id: int):
    try:
        with engine.connect() as connection:
            # 1. ユーザーが最近見たカテゴリを集計する
            # (MerRecデータが入れば、ここが強力になります)
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
            
            top_category = None
            row = history_result.fetchone()
            if row:
                top_category = row[0] # 一番見ているカテゴリ

            # 2. 全商品を取得
            items_result = connection.execute(text("SELECT * FROM items"))
            items = []
            for r in items_result:
                items.append({
                    "id": r.id,
                    "name": r.name,
                    "description": r.description,
                    "price": r.price,
                    "category": r.category if hasattr(r, 'category') else "Others",
                    "image_url": r.image_url
                })

            # 3. レコメンドロジック (ルールベースAI)
            recommended_items = []
            reason = ""

            if top_category:
                # ユーザーが好きなカテゴリの商品を優先的に上に持ってくる
                favorite_items = [i for i in items if i["category"] == top_category]
                other_items = [i for i in items if i["category"] != top_category]
                
                random.shuffle(favorite_items)
                random.shuffle(other_items)
                
                recommended_items = favorite_items + other_items
                reason = f"最近、{top_category}の商品をよく見ているあなたにおすすめです！"
            else:
                # 履歴がない場合はランダム
                random.shuffle(items)
                recommended_items = items
                reason = "今のトレンド商品です！"

            # 4. Geminiで理由をリライト (オプション)
            if GOOGLE_API_KEY and top_category:
                try:
                    model = genai.GenerativeModel("gemini-1.5-flash")
                    prompt = f"""
                    ユーザーは最近「{top_category}」カテゴリの商品に興味を持っています。
                    おすすめリストの先頭にある「{recommended_items[0]['name']}」を推薦する理由を、
                    30文字以内で魅力的に生成してください。
                    """
                    resp = model.generate_content(prompt)
                    reason = resp.text.strip()
                except:
                    pass

            return {
                "user_id": user_id,
                "reason": reason,
                "items": recommended_items
            }

    except Exception as e:
        return {"error": str(e)}

# ★DB初期化 (カテゴリ追加対応)
@app.post("/init-db")
def init_db():
    try:
        with engine.connect() as connection:
            # 1. itemsテーブル (category追加)
            # ※既存テーブルがある場合のエラー回避のため、本来はALTER TABLEですが
            # ハッカソンなのでDROPして作り直すのが早いです（データ消えますがOK？）
            connection.execute(text("DROP TABLE IF EXISTS items"))
            connection.execute(text("""
                CREATE TABLE items (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    description TEXT,
                    price INT,
                    category VARCHAR(50),
                    image_url VARCHAR(255)
                )
            """))
            
            # 2. interactionsテーブル (履歴用)
            connection.execute(text("""
                CREATE TABLE IF NOT EXISTS interactions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT,
                    item_id INT,
                    event_type VARCHAR(20),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            # データ投入
            sample_items = [
                {"name": "ビンテージ フィルムカメラ", "desc": "1980年代の名機。", "price": 12000, "cat": "Camera"},
                {"name": "プロ仕様 一眼レフレンズ", "desc": "ポートレートに最適。", "price": 45000, "cat": "Camera"},
                {"name": "キャンプ用 ランタン", "desc": "雰囲気が出ます。", "price": 4500, "cat": "Outdoor"},
                {"name": "折りたたみチェア", "desc": "軽量で持ち運び便利。", "price": 3000, "cat": "Outdoor"},
                {"name": "Python入門書", "desc": "基礎から学べます。", "price": 1500, "cat": "Book"},
            ]
            
            for item in sample_items:
                connection.execute(
                    text("INSERT INTO items (name, description, price, category, image_url) VALUES (:name, :desc, :price, :cat, '')"),
                    {"name": item["name"], "desc": item["desc"], "price": item["price"], "cat": item["cat"]}
                )
            
            connection.commit()
            return {"message": "DB initialized with Categories and Interactions table!"}
            
    except Exception as e:
        return {"error": str(e)}

# (その他の関数はそのまま)