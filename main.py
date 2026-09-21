import json
import os
import re
import time
from datetime import datetime, timezone, timedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

load_dotenv()

TWITTERAPI_KEY = os.environ["TWITTERAPI_KEY"]
XAI_API_KEY = os.environ["XAI_API_KEY"]
WEBHOOK = os.environ["DISCORD_WEBHOOK_URL"]
WOEID = int(os.getenv("WOEID", "23424856"))
TREND_LIMIT = int(os.getenv("TREND_LIMIT", "8"))
TREND_SCAN = int(os.getenv("TREND_SCAN", "30"))
TREND_GENRES = tuple(
    g.strip()
    for g in os.getenv("TREND_GENRES", "政治,経済,AI,ゲーム").split(",")
    if g.strip()
)

TA_HEADERS = {"X-API-Key": TWITTERAPI_KEY}
JST = timezone(timedelta(hours=9))
TRANSIENT = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.RetryError,
)


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=False,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


HTTP = _session()


def _request(method: str, url: str, *, retries: int = 3, **kwargs) -> requests.Response:
    last: Exception | None = None
    for i in range(retries):
        try:
            r = HTTP.request(method, url, **kwargs)
            if r.status_code in (429, 500, 502, 503, 504) and i + 1 < retries:
                time.sleep(0.8 * (i + 1))
                continue
            r.raise_for_status()
            return r
        except TRANSIENT as e:
            last = e
            time.sleep(0.8 * (i + 1))
    if last is not None:
        raise last
    raise RuntimeError(f"request failed: {method} {url}")


def _unwrap_trend(item: dict) -> dict:
    """twitterapi.io は {"trend": {"name": ...}} で返す。"""
    if not isinstance(item, dict):
        return {}
    inner = item.get("trend")
    if isinstance(inner, dict):
        return inner
    return item


def get_trends(woeid: int, limit: int | None = None) -> list[dict]:
    r = _request(
        "GET",
        "https://api.twitterapi.io/twitter/trends",
        headers=TA_HEADERS,
        params={"woeid": woeid, "count": 30},
        timeout=(10, 30),
    )
    data = r.json()

    trends = data.get("trends") or data.get("data") or []
    if isinstance(trends, dict):
        trends = trends.get("trends", [])

    out = []
    for item in trends:
        t = _unwrap_trend(item)
        name = (t.get("name") or t.get("trend_name") or "").strip()
        if not name:
            continue
        target = t.get("target") or {}
        query = target.get("query") if isinstance(target, dict) else None
        out.append(
            {
                "name": name,
                "query": (query or name).strip(),
                "rank": t.get("rank"),
                "meta_description": t.get("meta_description") or "",
            }
        )
        if limit is not None and len(out) >= limit:
            break
    return out


def _search(query: str, query_type: str) -> list[dict]:
    r = _request(
        "GET",
        "https://api.twitterapi.io/twitter/tweet/advanced_search",
        headers=TA_HEADERS,
        params={"query": query, "queryType": query_type},
        timeout=(10, 25),
        retries=2,
    )
    return r.json().get("tweets") or []


def _search_term(name: str, query_hint: str | None = None) -> str:
    q = (query_hint or name or "").strip()
    if not q:
        return ""
    if q.startswith("#") or q.startswith('"'):
        return q
    return f'"{q}"'


def search_posts(
    name: str,
    query_hint: str | None = None,
    *,
    limit: int = 8,
    quick: bool = False,
) -> list[dict]:
    term = _search_term(name, query_hint)
    if not term:
        return []

    attempts = [
        (f"{term} lang:ja within_time:24h", "Top"),
        (f"{term} lang:ja", "Top"),
        (f"{term} lang:ja", "Latest"),
        (term, "Latest"),
    ]
    if quick:
        attempts = attempts[:2]
    seen: set[tuple[str, str]] = set()
    for query, query_type in attempts:
        key = (query, query_type)
        if key in seen:
            continue
        seen.add(key)
        try:
            posts = _search(query, query_type)
        except (requests.RequestException, OSError) as e:
            print(
                f"search failed {name} [{query_type}]: {type(e).__name__}: {e}",
                flush=True,
            )
            continue
        if posts:
            return posts[:limit]
    return []


def _post_url(p: dict) -> str:
    return p.get("url") or p.get("twitterUrl") or ""


def _xai_chat(prompt: str, *, temperature: float = 0.3, json_object: bool = False) -> str:
    payload: dict = {
        "model": "grok-4.6",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    if json_object:
        payload["response_format"] = {"type": "json_object"}
    r = _request(
        "POST",
        "https://api.x.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {XAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=(10, 90),
        retries=3,
    )
    return r.json()["choices"][0]["message"]["content"].strip()


def _parse_json_object(text: str) -> dict:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def _normalize_genre(value: object) -> str | None:
    if value is None:
        return None
    g = str(value).strip()
    if not g or g.lower() in {"null", "none", "その他", "該当なし", "n/a"}:
        return None
    aliases = {"ａｉ": "AI", "a.i.": "AI", "ai": "AI", "ゲーム・エンタメ": "ゲーム"}
    g = aliases.get(g.lower(), g)
    allowed = {x.lower(): x for x in TREND_GENRES}
    return allowed.get(g.lower())


def classify_trends(
    trends: list[dict],
    posts_by_name: dict[str, list[dict]],
) -> list[dict]:
    if not TREND_GENRES:
        return [{**t, "genre": ""} for t in trends[:TREND_LIMIT]]

    allowed = " / ".join(TREND_GENRES)
    blocks = []
    for t in trends:
        posts = posts_by_name.get(t["name"]) or []
        snippets = []
        for p in posts[:4]:
            text = (p.get("text") or "").replace("\n", " ").strip()
            if text:
                snippets.append(text[:120])
        sample = " / ".join(snippets) or "(投稿なし)"
        blocks.append(f"- {t['name']}\n  投稿: {sample}")

    prompt = f"""日本のXトレンドを、許可ジャンルだけに分類してください。
許可ジャンル: {allowed}
どれにも入らなければ genre は null。

基準:
- 政治: 政党、選挙、国会、外交、法案、政治家、行政
- 経済: 相場、為替、金利、企業業績、物価、雇用、税制、景気
- AI: 生成AI、LLM、機械学習、AI企業・モデル・ツール
- ゲーム: ゲーム作品、メーカー、eスポーツ、ゲーム配信・イベント
アイドル、ドラマ、スポーツ試合、グルメ、天気は除外。
投稿を優先し、名前だけで推測しすぎない。

トレンド:
{chr(10).join(blocks)}

JSONのみ:
{{"items":[{{"name":"トレンド名","genre":"政治"}}]}}
genre は {allowed} のいずれか、または null。
"""
    raw = _xai_chat(prompt, temperature=0, json_object=True)
    try:
        data = _parse_json_object(raw)
    except json.JSONDecodeError:
        data = _parse_json_object(_xai_chat(prompt, temperature=0))
    items = data.get("items") if isinstance(data, dict) else data
    by_name = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        genre = _normalize_genre(item.get("genre"))
        if name:
            by_name[name] = genre
            by_name[name.lstrip("#")] = genre

    picked = []
    for t in trends:
        genre = by_name.get(t["name"]) or by_name.get(t["name"].lstrip("#"))
        if not genre:
            continue
        picked.append({**t, "genre": genre})
        if len(picked) >= TREND_LIMIT:
            break
    return picked


def explain(name: str, posts: list[dict], meta: str = "") -> str:
    lines = []
    for p in posts:
        text = (p.get("text") or "").replace("\n", " ")
        url = _post_url(p)
        likes = p.get("likeCount", 0)
        views = p.get("viewCount", 0)
        author = (p.get("author") or {}).get("userName") or ""
        who = f"@{author} " if author else ""
        lines.append(f"- {who}いいね{likes} 表示{views}: {text[:180]} {url}")

    extra = f"\n公式メタ: {meta}" if meta else ""
    prompt = f"""あなたは日本のXトレンド解説Botです。
トレンド「{name}」について、関連投稿だけを根拠に日本語で解説してください。{extra}

関連投稿:
{chr(10).join(lines) or "(投稿なし)"}

出力ルール:
- 4行以内
- 1行目: 一言まとめ
- 2行目: 何の話か
- 3行目: なぜ今伸びていそうか
- 4行目: 未確認なら「未確認」と書く。投稿から内容が分かれば書かない
- 断定しすぎない
- 投稿にない事実を作らない
"""

    return _xai_chat(prompt, temperature=0.3)


def to_chunks(text: str, limit: int = 1900) -> list[str]:
    chunks, buf = [], ""
    for block in text.split("\n\n"):
        add = block if not buf else buf + "\n\n" + block
        if len(add) <= limit:
            buf = add
        else:
            if buf:
                chunks.append(buf)
            buf = block[:limit]
    if buf:
        chunks.append(buf)
    return chunks


def post_discord(text: str):
    for i, chunk in enumerate(to_chunks(text)):
        _request("POST", WEBHOOK, json={"content": chunk}, timeout=(10, 30))
        if i < 10:
            time.sleep(0.4)


def build_report() -> str:
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    trends = get_trends(WOEID, TREND_SCAN)
    if not trends:
        raise RuntimeError("トレンドが取得できませんでした")

    genre_label = " / ".join(TREND_GENRES) if TREND_GENRES else "指定なし"
    posts_by_name: dict[str, list[dict]] = {}
    for t in trends:
        name = t["name"]
        try:
            posts_by_name[name] = search_posts(
                name, t.get("query"), limit=4, quick=True
            )
        except (requests.RequestException, OSError) as e:
            print(f"scan failed {name}: {type(e).__name__}: {e}", flush=True)
            posts_by_name[name] = []
        print(f"scan {name}: {len(posts_by_name[name])} posts", flush=True)
        time.sleep(0.15)

    picked = classify_trends(trends, posts_by_name)
    print(
        "picked: "
        + (", ".join(f"{t['name']}({t.get('genre')})" for t in picked) or "(none)"),
        flush=True,
    )

    parts = [
        f"**本日のXトレンド解説**  {now}\n日本 / ジャンル: {genre_label} / {len(picked)}件"
    ]
    if not picked:
        parts.append("指定ジャンルに該当するトレンドはありませんでした。")
        return "\n\n".join(parts)

    for i, t in enumerate(picked, 1):
        name = t["name"]
        try:
            posts = search_posts(name, t.get("query")) or posts_by_name.get(name) or []
        except (requests.RequestException, OSError):
            posts = posts_by_name.get(name) or []
        print(f"{i}. {name} [{t.get('genre')}]: {len(posts)} posts", flush=True)
        try:
            summary = explain(name, posts, t.get("meta_description") or "")
        except (requests.RequestException, OSError) as e:
            summary = f"解説の取得に失敗しました ({type(e).__name__})"
        sample = next((_post_url(p) for p in posts if _post_url(p)), "")
        genre = t.get("genre") or ""
        tag = f" `{genre}`" if genre else ""
        block = f"**{i}. {name}**{tag}\n{summary}"
        if sample:
            block += f"\n<{sample}>"
        parts.append(block)
        time.sleep(0.3)

    return "\n\n".join(parts)


def main():
    post_discord(build_report())
    print("posted")


if __name__ == "__main__":
    main()