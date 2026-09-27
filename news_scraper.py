"""
시간대별 뉴스 스크랩 프로그램
- 여러 신문사 RSS에서 헤드라인(제목·링크·요약)을 모아 SQLite에 저장
- 수집 시각 기준으로 시간대별로 묶어 news.html 로 보여줌

설치:  pip install feedparser schedule
실행:  python news_scraper.py          # 1시간마다 계속 수집
       python news_scraper.py --once   # 한 번만 수집하고 종료
       python news_scraper.py --ci     # GitHub Actions용 (한 번 수집, 브라우저 안 엶)
"""

import argparse
import html
import re
import sqlite3
import time
import webbrowser
from urllib.parse import quote
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import feedparser

# ─── 설정 ────────────────────────────────────────────────
# 이 단어가 제목이나 요약에 들어간 기사만 저장해요. 여러 개 넣으면 하나라도 포함되면 저장.
KEYWORDS = ["증평"]

# 원하는 신문사 RSS 주소를 넣고 빼면 됩니다. (주소는 신문사 사정에 따라 바뀔 수 있어요)
# 구글 뉴스 검색 피드는 키워드로 전국·지역 언론 기사를 한꺼번에 찾아줘서 지역 뉴스에 특히 유용해요.
FEEDS = {
    "구글뉴스": "https://news.google.com/rss/search?q="
               + quote(" OR ".join(KEYWORDS)) + "&hl=ko&gl=KR&ceid=KR:ko",
    "연합뉴스": "https://www.yna.co.kr/rss/news.xml",
    "동아일보": "https://rss.donga.com/total.xml",
    "경향신문": "https://www.khan.co.kr/rss/rssdata/total_news.xml",
    "한겨레": "https://www.hani.co.kr/rss/",
}
INTERVAL_MINUTES = 60      # 수집 간격
KEEP_DAYS = 3              # 이보다 오래된 기사는 자동 삭제
BASE = Path(__file__).parent
DB_PATH = BASE / "news.db"
HTML_PATH = BASE / "index.html"   # GitHub Pages가 이 파일을 첫 화면으로 보여줌
# ────────────────────────────────────────────────────────


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            link       TEXT PRIMARY KEY,
            source     TEXT,
            title      TEXT,
            summary    TEXT,
            fetched_at TEXT
        )""")
    con.commit()
    return con


def clean(text, limit=140):
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def collect():
    con = init_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    added = 0
    for source, url in FEEDS.items():
        try:
            feed = feedparser.parse(url, agent="Mozilla/5.0 (personal news reader)")
            for e in feed.entries:
                link = e.get("link")
                title = clean(e.get("title"), 200)
                summary = clean(e.get("summary"))
                if not link or not any(k in title + summary for k in KEYWORDS):
                    continue
                name = source
                if source == "구글뉴스":   # 구글뉴스는 실제 언론사 이름을 따로 알려줌
                    name = e.get("source", {}).get("title") or source
                    title = re.sub(r"\s+-\s+[^-]+$", "", title)   # 제목 끝 " - 언론사" 제거
                    summary = ""                                     # 구글 요약은 제목 반복이라 생략
                cur = con.execute(
                    "INSERT OR IGNORE INTO articles VALUES (?,?,?,?,?)",
                    (link, name, title, summary, now))
                added += cur.rowcount
        except Exception as err:
            print(f"[{source}] 수집 실패: {err}")

    cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d %H:%M")
    con.execute("DELETE FROM articles WHERE fetched_at < ?", (cutoff,))
    con.commit()
    render(con)
    con.close()
    print(f"{now}  새 기사 {added}건 저장 → {HTML_PATH.name}")


def render(con):
    rows = con.execute(
        "SELECT source, title, summary, link, fetched_at FROM articles "
        "ORDER BY fetched_at DESC, source").fetchall()

    slots = defaultdict(list)          # "2026-09-28 14시" → 기사 목록
    for src, title, summary, link, fetched in rows:
        slots[fetched[:13]].append((src, title, summary, link))

    sources = sorted({r[0] for r in rows})
    chips = "".join(f'<button class="chip" data-src="{html.escape(s)}">{html.escape(s)}</button>'
                    for s in sources)

    body = []
    for slot, items in slots.items():
        day, hour = slot.split(" ")
        label = f"{int(day[5:7])}월 {int(day[8:10])}일 {int(hour)}시"
        cards = "".join(
            f'<li data-src="{html.escape(s)}"><span class="src">{html.escape(s)}</span>'
            f'<a href="{html.escape(l)}" target="_blank" rel="noopener">{html.escape(t)}</a>'
            + (f'<p>{html.escape(sm)}</p>' if sm else "") + "</li>"
            for s, t, sm, l in items)
        body.append(f'<section><h2>{label}<small>{len(items)}건</small></h2><ul>{cards}</ul></section>')

    if not body:
        body.append('<p class="empty">아직 증평 관련 기사가 없어요. 다음 수집 때 다시 확인해요.</p>')

    page = TEMPLATE.replace("{{CHIPS}}", chips).replace("{{BODY}}", "".join(body)) \
                   .replace("{{UPDATED}}", datetime.now().strftime("%m월 %d일 %H:%M"))
    HTML_PATH.write_text(page, encoding="utf-8")


TEMPLATE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="600">
<title>증평 뉴스</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{--bg:#EEF2F5;--ink:#1B2733;--muted:#5E6E7D;--line:#C9D3DC;--mark:#2F5D8A;--card:#FFFFFF}
@media (prefers-color-scheme:dark){:root{--bg:#141B22;--ink:#E3E9EF;--muted:#8D9AA6;--line:#2C3845;--mark:#7FB0DE;--card:#1B242D}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 "IBM Plex Sans KR",system-ui,sans-serif}
header{max-width:760px;margin:0 auto;padding:32px 20px 8px}
h1{font-size:1.9rem;margin:0 0 4px;font-weight:600}
.updated{color:var(--muted);font-size:.9rem;margin:0 0 16px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{font:inherit;font-size:.9rem;padding:4px 12px;border-radius:999px;border:1px solid var(--line);
      background:transparent;color:var(--ink);cursor:pointer}
.chip.off{opacity:.4;text-decoration:line-through}
.chip:focus-visible,a:focus-visible{outline:2px solid var(--mark);outline-offset:2px}
main{max-width:760px;margin:0 auto;padding:8px 20px 60px}
section{position:relative;padding-left:28px;border-left:2px solid var(--line);margin-left:6px}
section::before{content:"";position:absolute;left:-7px;top:30px;width:12px;height:12px;border-radius:50%;background:var(--mark)}
h2{position:sticky;top:0;background:var(--bg);font-size:1.15rem;font-weight:600;margin:0;padding:22px 0 10px;z-index:1}
h2 small{font-weight:400;color:var(--muted);font-size:.85rem;margin-left:8px}
ul{list-style:none;margin:0 0 12px;padding:0}
li{background:var(--card);border-radius:6px;padding:12px 16px;margin-bottom:8px}
li.hide{display:none}
.src{display:block;font-size:.8rem;color:var(--mark);font-weight:600}
li a{color:var(--ink);text-decoration:none;font-weight:600}
li a:hover{text-decoration:underline}
li p{margin:4px 0 0;color:var(--muted);font-size:.92rem}
.empty{color:var(--muted)}
</style></head><body>
<header><h1>증평 뉴스</h1><p class="updated">마지막 업데이트 {{UPDATED}}</p>
<div class="chips">{{CHIPS}}</div></header>
<main>{{BODY}}</main>
<script>
document.querySelectorAll('.chip').forEach(c=>c.onclick=()=>{
  c.classList.toggle('off');
  const off=[...document.querySelectorAll('.chip.off')].map(x=>x.dataset.src);
  document.querySelectorAll('li').forEach(li=>li.classList.toggle('hide',off.includes(li.dataset.src)));
});
</script></body></html>"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="한 번만 수집")
    ap.add_argument("--ci", action="store_true", help="GitHub Actions용")
    args = ap.parse_args()

    collect()
    if args.ci:
        raise SystemExit
    webbrowser.open(HTML_PATH.resolve().as_uri())
    if not args.once:
        import schedule
        schedule.every(INTERVAL_MINUTES).minutes.do(collect)
        print(f"{INTERVAL_MINUTES}분마다 수집합니다. 멈추려면 Ctrl+C")
        while True:
            schedule.run_pending()
            time.sleep(30)
