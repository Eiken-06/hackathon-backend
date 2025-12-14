# Python 3.11の軽量版を使用
FROM python:3.11-slim

# 作業ディレクトリを設定
WORKDIR /app

# 依存関係ファイルをコピーしてインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ソースコードをコピー
COPY . .

# Cloud Runはポート8080 (環境変数PORT) で待機する必要がある
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}