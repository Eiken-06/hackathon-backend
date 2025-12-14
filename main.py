import os
import sys
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

app = FastAPI()

# --- CORS設定 (Frontendからのアクセスを許可) ---
# 本番ではVercelのドメインを指定するのが安全ですが、ハッカソン中は"*"（全許可）で進めるのが無難です。
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
    
    # Cloud Run環境かどうかの判別 (INSTANCE_CONNECTION_NAMEがあるかどうか)
    instance_connection_name = os.environ.get("INSTANCE_CONNECTION_NAME")

    if instance_connection_name:
        # 【Cloud Run用】 Unix Socket経由で接続
        # 形式: mysql+pymysql://<user>:<pass>@/<db_name>?unix_socket=/cloudsql/<instance_connection_name>
        socket_path = f"/cloudsql/{instance_connection_name}"
        return f"mysql+pymysql://{db_user}:{db_pass}@/{db_name}?unix_socket={socket_path}"
    else:
        # 【ローカル開発用】 TCP経由で接続 (Cloud SQL Proxy等を使う場合やローカルDB)
        db_host = os.environ.get("DB_HOST", "127.0.0.1")
        db_port = os.environ.get("DB_PORT", "3306")
        return f"mysql+pymysql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"

# DBエンジンの作成
DATABASE_URL = get_db_connection_string()
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# --- APIエンドポイント ---

@app.get("/")
def read_root():
    return {"message": "Hello from Cloud Run (Python)!"}

@app.get("/db-check")
def db_check():
    """DB接続テスト用エンドポイント"""
    try:
        # 简单的に時刻を取得して接続確認
        with engine.connect() as connection:
            result = connection.execute(text("SELECT NOW()"))
            current_time = result.fetchone()[0]
        return {
            "status": "success", 
            "db_time": current_time, 
            "message": "Cloud SQL Connection OK!"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}