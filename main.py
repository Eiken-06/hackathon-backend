import os
import random
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import create_engine, text
import google.generativeai as genai  # <-- 追加
from pydantic import BaseModel      # <-- 追加

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

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)

class ItemRequest(BaseModel):
    name: str
    description: str

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

@app.post("/generate-description")
def generate_description(item: ItemRequest):
    """商品名と説明を受け取り、Geminiに魅力的なセールストークを作らせる"""
    if not GOOGLE_API_KEY:
        return {"comment": "AI機能は現在オフラインです。（APIキー未設定）"}
    
    try:
        model = genai.GenerativeModel("gemini-2.5-flash") # 高速で安いモデル
        
        prompt = f"""
        あなたはプロのフリマアプリのバイヤーです。
        以下の商品を、買いたくなるような短いセールストーク（50文字以内）で紹介してください。
        絵文字を1つ使って、親しみやすくしてください。
        
        商品名: {item.name}
        元の説明: {item.description}
        """
        
        response = model.generate_content(prompt)
        return {"comment": response.text.strip()}
        
    except Exception as e:
        return {"comment": f"AI生成エラー: {str(e)}"}