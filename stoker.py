import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import requests

# Windows等の環境で絵文字が含まれても出力エラーにならないようUTF-8を設定
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# .env ファイルから環境変数を読み込む
load_dotenv()

api_key = os.getenv("TWITTERAPI_KEY")
if not api_key:
    raise ValueError(".env ファイルに TWITTERAPI_KEY が設定されていません。")

# 1. 引数から対象ユーザーを指定する
parser = argparse.ArgumentParser(description="指定したユーザーの直近1日分のポスト（ツイート）を取得します。")
parser.add_argument(
    "target_user",
    nargs="?",
    default="elonmusk",
    help="対象のX（Twitter）ユーザー名（デフォルト: elonmusk）",
)
args = parser.parse_args()

target_user = args.target_user

# 直近1日分（24時間）の期間をUTCで計算
now = datetime.now(timezone.utc)
yesterday = now - timedelta(days=1)

# Xの検索クエリ用に YYYY-MM-DD_HH:MM:SS_UTC 形式等で指定
# 例: from:elonmusk since:2026-09-20_10:00:00_UTC
since_str = yesterday.strftime("%Y-%m-%d_%H:%M:%S_UTC")
query = f"from:{target_user} since:{since_str}"

url = "https://api.twitterapi.io/twitter/tweet/advanced_search"

headers = {"X-API-Key": api_key}
params = {"query": query}

response = requests.get(url, headers=headers, params=params)

def parse_created_at(created_at_str: str) -> str:
    """createdAt文字列（例: 'Mon Sep 21 00:08:32 +0000 2026'）をJST日時に変換"""
    if not created_at_str:
        return ""
    try:
        dt = datetime.strptime(created_at_str, "%a %b %d %H:%M:%S %z %Y")
        jst = dt.astimezone(timezone(timedelta(hours=9)))
        return jst.strftime("%Y-%m-%d %H:%M:%S (JST)")
    except Exception:
        return created_at_str

if response.status_code == 200:
    data = response.json()
    tweets = data.get("tweets", [])
    print(f"対象ユーザー: @{target_user}")
    print(f"取得したポスト件数: {len(tweets)}\n")

    for i, tweet in enumerate(tweets, 1):
        msg = tweet.get("text") or tweet.get("full_text") or ""
        created_at_str = tweet.get("createdAt", "")
        formatted_time = parse_created_at(created_at_str)
        time_header = f" [{formatted_time}]" if formatted_time else ""

        print(f"--- メッセージ {i}{time_header} ---")
        print(msg)
        print()
else:
    print(f"エラーが発生しました: {response.status_code}")
    print(response.text)