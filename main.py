import os
import random
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text

app = FastAPI()

# --- CORS設定 (Frontendからのアクセスを許可) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- データベース接続設定 ---
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

# --- APIエンドポイント ---

@app.get("/")
def read_root():
    return {"message": "Backend is running!"}

@app.get("/items")
def get_items():
    """商品一覧を取得するAPI"""
    try:
        with engine.connect() as connection:
            # itemsテーブルから全件取得
            result = connection.execute(text("SELECT * FROM items"))
            items = []
            for row in result:
                # 行を辞書型に変換
                items.append({
                    "id": row.id,
                    "name": row.name,
                    "description": row.description,
                    "price": row.price,
                    "image_url": row.image_url
                })
            return items
    except Exception as e:
        return {"error": str(e)}

@app.post("/init-db")
def init_db():
    """【重要】テーブル作成とテストデータを投入する便利ボタン"""
    try:
        with engine.connect() as connection:
            # 1. itemsテーブルを作成
            connection.execute(text("""
                CREATE TABLE IF NOT EXISTS items (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    description TEXT,
                    price INT,
                    image_url VARCHAR(255)
                )
            """))
            
            # 2. recommendationsテーブルを作成 (AI用)
            connection.execute(text("""
                CREATE TABLE IF NOT EXISTS recommendations (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT,
                    item_id INT,
                    reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            # 3. データが空ならテストデータを投入
            result = connection.execute(text("SELECT COUNT(*) FROM items"))
            count = result.fetchone()[0]
            
            if count == 0:
                # サンプルデータ (MerRecのふりをしたデータ)
                sample_items = [
                    {"name": "ビンテージ フィルムカメラ", "desc": "1980年代の名機。動作確認済み。", "price": 12000},
                    {"name": "キャンプ用 ランタン", "desc": "LEDですが暖色系の光で雰囲気が出ます。", "price": 4500},
                    {"name": "プログラミング入門書", "desc": "Pythonの基礎から学べます。少し書き込みあり。", "price": 1500},
                    {"name": "ワイヤレスイヤホン", "desc": "ノイズキャンセリング機能付き。", "price": 8000},
                    {"name": "手作り レザートートバッグ", "desc": "本革を使用したハンドメイド品です。", "price": 25000},
                ]
                
                for item in sample_items:
                    # プレースホルダーを使って安全に挿入
                    connection.execute(
                        text("INSERT INTO items (name, description, price, image_url) VALUES (:name, :desc, :price, '')"),
                        {"name": item["name"], "desc": item["desc"], "price": item["price"]}
                    )
                
                connection.commit()
                return {"message": "Tables created and sample data inserted!"}
            else:
                return {"message": "Tables already exist. Skipped data insertion."}
                
    except Exception as e:
        return {"error": str(e)}