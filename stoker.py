import argparse
import os
import sys
import time
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

webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
if not webhook_url:
    raise ValueError(".env ファイルに DISCORD_WEBHOOK_URL が設定されていません。")

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


def post_discord(webhook_url: str, text: str):
    """テキストをDiscordのWebHookに送信（2000文字制限対策として分割送信）"""
    chunks = []
    current_chunk = ""
    for line in text.splitlines(keepends=True):
        if len(current_chunk) + len(line) > 1900:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            while len(line) > 1900:
                chunks.append(line[:1900])
                line = line[1900:]
        current_chunk += line
    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    for i, chunk in enumerate(chunks):
        res = requests.post(webhook_url, json={"content": chunk})
        if res.status_code not in (200, 204):
            print(f"Discordへの送信エラー ({res.status_code}): {res.text}")
        if i < len(chunks) - 1:
            time.sleep(0.5)


if response.status_code == 200:
    data = response.json()
    tweets = data.get("tweets", [])

    report_lines = [
        f"**対象ユーザー: @{target_user}**",
        f"取得したポスト件数: {len(tweets)}\n",
    ]

    for i, tweet in enumerate(tweets, 1):
        msg = tweet.get("text") or tweet.get("full_text") or ""
        created_at_str = tweet.get("createdAt", "")
        formatted_time = parse_created_at(created_at_str)
        time_header = f" [{formatted_time}]" if formatted_time else ""

        report_lines.append(f"--- メッセージ {i}{time_header} ---")
        report_lines.append(msg)
        report_lines.append("")

    report_text = "\n".join(report_lines)

    print(report_text)
    post_discord(webhook_url, report_text)
    print("Discordへポストしました。")
else:
    error_msg = f"エラーが発生しました: {response.status_code}\n{response.text}"
    print(error_msg)
    post_discord(webhook_url, error_msg)