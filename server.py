#!/usr/bin/env python3
"""에이전트 운영 실패 사례를 호출당 과금으로 파는 x402 서버 (공식 x402 SDK 2.20.0 위에 얹음).

파는 것: T9OS를 8개월 굴리며 쌓인 실패·부검 기록 183건. 성공한 코드는 GitHub에 넘치지만
"이렇게 배선했더니 22시간 조용히 죽어 있었다"는 기록은 공개된 적이 거의 없다. 그 희소성이 값이다.
안 파는 것: 파롤(운영자 발화 원문)·개인정보. failure_corpus.py가 이미 걷어냈다.

라우트
    GET  /                     무료. 서비스 설명 + 무엇을 파는지
    GET  /sample               무료. 사례 2건 미리보기 (콜드스타트용 미끼)
    GET  /search?q=...         $0.01. 증상으로 사례 3건 검색
    GET  /brief?action=...     $0.05. 위험 작업 착수 전 사전 브리핑 (사례 종합)

지갑·정산
    환경변수 X402_PAY_TO 가 있어야 유료 라우트가 켜진다. 없으면 무료 라우트만 뜨고
    유료 라우트는 503 + 이유를 반환한다 (조용히 죽지 않는다 = 우리가 판 그 교훈).
    기본 facilitator는 테스트넷만 정산하므로 X402_FACILITATOR 를 키 없이 메인넷을 도는
    곳(payai/xpay)으로 둔다. X402_NETWORKS 기본 = Base 메인넷.

실행
    T9OS/pipes/revenue/x402svc/.venv/bin/python -m uvicorn server:app --port 8402
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
# 배포 환경엔 레포가 없다. 동봉본을 먼저 보고, 없으면 레포 원본으로 폴백.
CORPUS = next(
    (p for p in (HERE / "data" / "failure_corpus.jsonl",
                 ROOT / "T9OS" / "data" / "revenue" / "failure_corpus.jsonl") if p.exists()),
    HERE / "data" / "failure_corpus.jsonl",
)

# 수령 주소는 원래 공개되어야 결제가 성립한다(402 응답 본문에 실린다). 환경변수가 우선.
DEFAULT_PAY_TO = "0xFC15354FE6a96d87399582dbe9DF8d2739B1fF9a"
PAY_TO = os.environ.get("X402_PAY_TO", DEFAULT_PAY_TO).strip()
# 여러 체인을 동시에 연다. 사는 쪽이 이미 잔고를 가진 체인으로 내면 된다.
# Base 메인넷이 기본. 아발란체(eip155:43114)는 SDK가 그 체인 USDC를 아직 몰라 500이 난다(실측).
NETWORKS = [n.strip() for n in os.environ.get("X402_NETWORKS", "eip155:8453").split(",") if n.strip()]
NETWORK = NETWORKS[0]
# 기본 facilitator는 테스트넷만 정산한다. 실돈은 키 없이 메인넷을 도는 곳으로.
FACILITATOR = os.environ.get("X402_FACILITATOR", "https://facilitator.payai.network").strip()
PRICE_AUDIT = os.environ.get("X402_PRICE_AUDIT", "$0.02")
PRICE_SEARCH = os.environ.get("X402_PRICE_SEARCH", "$0.01")
PRICE_BRIEF = os.environ.get("X402_PRICE_BRIEF", "$0.05")
PRICE_RESEARCH = os.environ.get("X402_PRICE_RESEARCH", "$0.25")
PRICE_ARCHIVE = os.environ.get("X402_PRICE_ARCHIVE", "$1.00")

# 개인 온톨로지·개체화 측정을 하다 실패한 기록. 여기가 이 아카이브의 희소한 절반이다.
RESEARCH = re.compile(
    r"odnar|parole|langue|disparation|individuat|ontolog|embedding|vector|corpus|"
    r"SAE|retrieval|RAG|measur|calibrat|오드나|파롤|랑그|개체화|임베딩|측정",
    re.I,
)

app = FastAPI(title="Agent Failure Archive", version="0.1.0")

_ROWS: list[dict] = []


CORPUS_URL = os.environ.get(
    "X402_CORPUS_URL",
    "https://raw.githubusercontent.com/HanbeenMoon/agent-failure-archive/main/data/failure_corpus.jsonl",
)


def rows() -> list[dict]:
    """코퍼스를 한 번만 읽어 캐시한다. 파일이 없는 서버리스 환경이면 공개 URL에서 받는다."""
    global _ROWS
    if _ROWS:
        return _ROWS
    text = ""
    if CORPUS.exists():
        text = CORPUS.read_text(encoding="utf-8")
    elif CORPUS_URL:
        try:
            import urllib.request

            with urllib.request.urlopen(CORPUS_URL, timeout=10) as fh:
                text = fh.read().decode("utf-8")
        except Exception as e:  # 조용히 빈 목록으로 죽지 않는다
            print(f"[corpus] fetch failed: {type(e).__name__}: {e}", file=sys.stderr)
    _ROWS = [json.loads(l) for l in text.splitlines() if l.strip()]
    return _ROWS


# 검색어에 흔히 섞이지만 변별력이 없는 말. 이게 없으면 "the"가 점수를 지배한다.
STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "is", "it", "my", "for", "with", "that"}


def _score(r: dict, terms: list[str]) -> int:
    blob = f"{r['title']} {r['symptom']} {r['root_cause']} {r['fix']} {r['prevention']}".lower()
    title = r["title"].lower()
    s = sum(blob.count(t) for t in terms)
    s += 4 * sum(1 for t in terms if t in title)
    s += 2 * sum(1 for t in terms for e in r["evidence"] if t in e.lower())
    return s


def find(q: str, k: int = 3) -> list[dict]:
    terms = [t for t in re.split(r"[\s,]+", q.strip().lower()) if len(t) > 1 and t not in STOP]
    if not terms:
        return []
    scored = [(_score(r, terms), r) for r in rows()]
    scored = [(s, r) for s, r in scored if s > 0]
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:k]]


def _card(r: dict, full: bool = True) -> dict:
    d = {
        "case_id": f"{r['kind']}-{r['id']}",
        "title": r["title"],
        "root_cause": r["root_cause"],
        "evidence": r["evidence"],
    }
    if full:
        d["symptom"] = r["symptom"]
        d["fix"] = r["fix"]
        d["prevention"] = r["prevention"]
    return d


# ── 무료 (미끼 + 발견) ────────────────────────────────────────────────
# 사람이 브라우저로 들어오면 JSON 덩어리가 아니라 읽을 수 있는 것을 준다.
# 첫 손님이 기계가 아니라 사람일 확률이 높다고 실측으로 판단했는데(디렉토리 챗은 108일째 무호출),
# 정작 그 사람이 루트에서 보는 게 raw JSON이었다. 파는 자리에 간판이 없던 셈.
# ⚠️ Accept에 text/html 이 **명시**될 때만 HTML을 준다. 크롤러는 대개 */* 라서 JSON을 그대로 받는다.
def _wants_html(request: Request) -> bool:
    return "text/html" in (request.headers.get("accept") or "").lower()


_PAGE_CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#0b0d10;color:#d7dce3;font:16px/1.65 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:46rem;margin:0 auto;padding:3rem 1.25rem 5rem}
h1{font-size:1.65rem;line-height:1.25;margin:0 0 .4rem;color:#fff;letter-spacing:-.01em}
h2{font-size:1.05rem;margin:2.6rem 0 .7rem;color:#fff}
p{margin:.7rem 0}
.sub{color:#8b94a3;margin:0 0 2rem}
a{color:#79b8ff}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.87em}
.card{background:#12161c;border:1px solid #1f2630;border-radius:10px;padding:1rem 1.15rem;margin:.75rem 0}
.n{color:#fff;font-weight:600}
table{width:100%;border-collapse:collapse;margin:.6rem 0;font-size:.93rem}
td{padding:.34rem 0;border-bottom:1px solid #1a212b;vertical-align:top}
td:last-child{text-align:right;color:#8b94a3;white-space:nowrap;padding-left:1rem}
.try{display:inline-block;margin:.28rem .4rem .28rem 0;padding:.42rem .8rem;background:#182029;
     border:1px solid #26313d;border-radius:7px;color:#79b8ff;text-decoration:none;font-size:.9rem}
.try:hover{background:#1d2731}
.buy{display:inline-block;margin-top:.6rem;padding:.7rem 1.15rem;background:#1f6feb;color:#fff;
     border-radius:8px;text-decoration:none;font-weight:600}
.note{color:#8b94a3;font-size:.9rem}
hr{border:0;border-top:1px solid #1a212b;margin:2.5rem 0}
"""


def _landing() -> str:
    all_rows = rows()
    counts: dict[str, int] = {}
    for r in all_rows:
        for t in _themes(r):
            counts[t] = counts.get(t, 0) + 1
    top = sorted(counts.items(), key=lambda x: -x[1])
    label = {c["id"]: c["question"] for c in CHECKS}
    trap_rows = "".join(
        f"<tr><td>{label.get(k, k)}</td><td>{v}</td></tr>" for k, v in top
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Failure Archive</title><link rel="icon" href="/favicon.svg"><style>{_PAGE_CSS}</style></head><body><div class="wrap">
<h1>{len(all_rows)} post-mortems from an agent system that kept breaking quietly</h1>
<p class="sub">Eight months in production. {sum(1 for r in all_rows if r["evidence"])} of them carry
numbers measured at the time. {len(_research_rows())} come from trying to measure one person with
embeddings, which mostly failed.</p>

<div class="card"><p style="margin-top:0"><strong>A real entry.</strong> A repair routine existed
and its runner ticked <span class="n">26,662 times over 22.7 hours</span>. It performed
<span class="n">zero</span> repairs, because a guard condition never matched. Fourteen sessions sat
dead the whole time, and every log line said <code>skip</code>.</p></div>

<h2>Try it now, free, no wallet</h2>
<p>Paste a conclusion and it tells you which known trap it falls into. Deterministic, no model
in the loop, so the same input always gives the same answer.</p>
<a class="try" href="/precheck?claim=the%20new%20ranker%20has%20no%20measurable%20effect&amp;evidence=ran%20it%20on%2040%20queries">
try /precheck</a>
<a class="try" href="/contents">see all {len(all_rows)} titles</a>
<a class="try" href="/sample">read 2 full cases</a>

<h2>What is actually in here</h2>
<p class="note">Every case tagged by the trap it illustrates. One case can show several.</p>
<table>{trap_rows}</table>

<h2>Buy the whole thing for $1</h2>
<p>One payment in USDC on Base. No account, no signup, no subscription. Open it in a browser with
a wallet and you get a connect-and-pay screen; call it with any x402 client and it settles inline.
Cheaper slices: <code>/search</code> $0.01, <code>/audit</code> $0.02, <code>/brief</code> $0.05,
<code>/research</code> $0.25.</p>
<a class="buy" href="/archive">Get all {len(all_rows)} cases &middot; $1.00</a>

<h2>Also an MCP server</h2>
<p>Add <code class="mono">{PUBLIC}/mcp</code> to Claude Desktop, Cursor, or anything that speaks
MCP. Four tools work with no wallet and no configuration.</p>

<hr>
<p class="note"><strong>Honest state.</strong> Nothing here has sold yet. The receiving address has
taken in exactly $0.00, checked on chain rather than from server logs. This is one operator's
system, so treat it as prior art rather than as a general sample.</p>
<p class="note"><a href="https://github.com/HanbeenMoon/agent-failure-archive">Source and corpus</a>
&middot; <a href="https://github.com/HanbeenMoon/agent-failure-archive/blob/main/MARKET.md">measured
notes on how this market actually behaves</a> &middot; <a href="/llms.txt">llms.txt</a></p>
</div></body></html>"""


@app.get("/")
async def index(request: Request):
    if _wants_html(request):
        return HTMLResponse(_landing())
    return {
        "service": "Agent Failure Archive",
        "what": (
            "Real post-mortems from 8 months of running a multi-session AI agent system "
            "while trying to measure one person's individuation."
        ),
        "why": "Public repos show code that worked. These are the ones that died silently.",
        "cases": len(rows()),
        "with_measured_evidence": sum(1 for r in rows() if r["evidence"]),
        "research_subset": len(_research_rows()),
        "endpoints": {
            "/sample": "free preview (2 cases)",
            "/contents": "free, every case title so you can see what $1 buys",
            "/precheck?claim=<what you concluded>&evidence=<what you measured>": (
                "free, which of the nine checks your claim trips"
            ),
            "/audit?claim=<what you concluded>&evidence=<what you measured>": (
                f"{PRICE_AUDIT} per call, nine checks against fooling yourself"
            ),
            "/search?q=<symptom>": f"{PRICE_SEARCH} per call, 3 cases with root cause + fix",
            "/brief?action=<what you are about to do>": f"{PRICE_BRIEF} per call, pre-flight risk brief",
            "/research?q=<topic>": f"{PRICE_RESEARCH} per call, failures from measuring personal ontology",
            "/archive": f"{PRICE_ARCHIVE} once, every case in one response",
        },
        "excludes": "no personal data, no operator utterances, no business records",
        "paid_routes_live": bool(PAY_TO),
        "networks": NETWORKS,
        "facilitator": FACILITATOR,
    }


PUBLIC = os.environ.get("X402_PUBLIC_BASE", "https://desktop-ai2ata5-1.tailfeb765.ts.net").rstrip("/")
PAID_PATHS = ["/audit", "/search", "/brief", "/research", "/archive"]


@app.get("/.well-known/x402")
@app.get("/.well-known/x402.json")
async def well_known_x402():
    """크롤러·디렉토리가 표준으로 찾아보는 자리. 여기 없으면 아무도 못 줍는다.

    ⚠️ `version` + `resources` 두 칸이 x402scan 규격이다(docs/DISCOVERY.md §B). 이게 없으면
    등재기가 우리 라우트를 아예 못 펼친다. 나머지 칸은 사람이 읽으라고 덧붙인 것.
    """
    return {
        "version": 1,
        "resources": [f"{PUBLIC}{p}" for p in PAID_PATHS],
        "x402Version": 2,
        "name": "Agent Failure Archive",
        "description": (
            "186 post-mortems from running a multi-session AI agent system in production "
            "for 8 months: silent cron deaths, repairs wired to signals nobody consumes, "
            "watchers that exit 0 after their session expired."
        ),
        "endpoints": [
            {
                "path": "/audit",
                "method": "GET",
                "price": PRICE_AUDIT,
                "description": (
                    "Audit a claim before acting on it. Returns the checks it trips, each one a "
                    "failure that actually shipped. Deterministic, no model in the loop."
                ),
                "input": {
                    "claim": "string, what you concluded",
                    "evidence": "string, what you actually measured",
                },
            },
            {
                "path": "/search",
                "method": "GET",
                "price": PRICE_SEARCH,
                "description": "3 incidents matching a symptom, with root cause, fix and prevention.",
                "input": {"q": "string, e.g. 'cron job silently stopped running'"},
            },
            {
                "path": "/brief",
                "method": "GET",
                "price": PRICE_BRIEF,
                "description": "Pre-flight risk brief before an irreversible action, drawn from 5 incidents.",
                "input": {"action": "string, e.g. 'wire a new scheduled repair job'"},
            },
            {
                "path": "/research",
                "method": "GET",
                "price": PRICE_RESEARCH,
                "description": (
                    "Failures from an 8-month attempt to measure one person's individuation: "
                    "corpus contamination, embedding scope, retrieval collapse, withdrawn measurements."
                ),
                "input": {"q": "string, e.g. 'corpus purity' or 'negative control'"},
            },
            {
                "path": "/archive",
                "method": "GET",
                "price": PRICE_ARCHIVE,
                "description": "Every case in one response. One purchase, no subscription.",
                "input": {},
            },
        ],
        "free": ["/", "/sample", "/llms.txt"],
        "accepts": [
            {"scheme": "exact", "network": n, "asset": "USDC", "payTo": PAY_TO} for n in NETWORKS
        ],
        "facilitator": FACILITATOR,
        "source": "https://github.com/HanbeenMoon/agent-failure-archive",
    }


# 목록에 아이콘이 없으면 경쟁 항목들 사이에서 빈칸으로 뜬다. 실측: 우리 favicon=null,
# 외부에서 favicon.ico/png/svg 를 HEAD로 찾다가 404를 30번 받아갔다. 외부 의존 없이 직접 그린다.
_FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect width="64" height="64" rx="12" fill="#0b0d10"/>'
    '<path d="M14 44 L32 16 L50 44" fill="none" stroke="#f0b429" stroke-width="6" '
    'stroke-linecap="round" stroke-linejoin="round"/>'
    '<circle cx="32" cy="50" r="3.5" fill="#f0b429"/></svg>'
)


@app.get("/favicon.svg")
@app.get("/favicon.ico")
async def favicon():
    return Response(content=_FAVICON, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt():
    """에이전트가 사람 페이지 대신 읽는 자리. 무엇을 파는지 한 화면에."""
    return f"""# Agent Failure Archive

{len(rows())} post-mortems from 8 months of running a multi-session AI agent system in
production while trying to do one specific research thing with it: measure a single
person's individuation, continuously, from their own writing.

Both halves failed often, and both halves were written down.

Public repositories show code that worked. These are the wirings that looked correct,
passed review, ran for weeks, and were dead the whole time. {sum(1 for r in rows() if r['evidence'])} of the cases carry
measured evidence (durations, counts, rates). Each has a symptom, a root cause, the fix
applied, and the prevention rule it produced.

{len(_research_rows())} of them come from the research half, which is the part you will
not find elsewhere: what happens when you try to measure a person and the measurement
keeps turning out to be measuring your own instrument instead.

## Free
- GET /            what this is
- GET /sample      two full cases, no payment
- GET /contents    free. Every case title, grouped by the trap it illustrates. Titles only,
                   no bodies. This is the shelf: read it before deciding whether /archive
                   is worth a dollar to you. Filter with ?theme=<id>.
- GET /precheck?claim=<conclusion>&evidence=<what you measured>
                   free. Tells you which of the nine checks your claim trips and the
                   question each one asks. Costs nothing and is useful on its own.
                   The paid /audit adds why each matters, the incident behind it with
                   the measured numbers, and the specific thing to run.
- GET /llms.txt    this file

## Paid (USDC on Base, x402, no API key, no signup)
- GET /audit?claim=<conclusion>&evidence=<what you measured>   {PRICE_AUDIT}
  Nine checks against fooling yourself, applied to your own claim. Silent detectors,
  unequal treatment arms, inflated denominators, exit-0 successes that did nothing,
  cached views read as state. Deterministic: same input, same audit, no model consulted.
  Reach for this every time you are about to write "we found that".
- GET /search?q=<symptom>                     {PRICE_SEARCH}
  Three incidents matching a symptom. Try: "cron job silently stopped",
  "hook dies without error", "process exits 0 but did nothing".
- GET /brief?action=<what you are about to do>  {PRICE_BRIEF}
  A pre-flight risk brief. Describe an action in plain words and get back the ways that
  class of action has actually failed. Reach for this before anything irreversible.
- GET /research?q=<topic>                     {PRICE_RESEARCH}
  The measurement failures. Corpus that turned out to be 12% the person and 88% machine
  output. Detectors that returned the same answer whether the signal was there or not.
  Findings that were withdrawn after a negative control was finally run.
- GET /archive                                {PRICE_ARCHIVE}
  Everything, in one response, once.

## Not included
No personal data, no operator utterances, no business records. Source documents
mentioning a person, a business relationship or a monetary amount are excluded whole,
not redacted line by line.

## Caveat
This is one operator's system. Treat it as prior art, not as a guarantee. Coverage skews
toward multi-session coordination, hook and cron wiring, retrieval pipelines, scheduled
repair, and the measurement design of a single-subject study.

Source: https://github.com/HanbeenMoon/agent-failure-archive
"""


@app.get("/sample")
async def sample():
    picks = [r for r in rows() if r["evidence"] and r["root_cause"]][:2]
    return {
        "note": "Free preview. Paid endpoints return the full record including fix and prevention.",
        "cases": [_card(r, full=False) for r in picks],
    }


# ── 유료 ─────────────────────────────────────────────────────────────
def _no_wallet() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "paid routes not configured",
            "reason": "X402_PAY_TO is unset, so there is no address to receive payment.",
            "fix": "set X402_PAY_TO to a wallet address and restart",
        },
    )


@app.get("/search")
async def search(q: str = "", k: int = 3):
    if not PAY_TO:
        return _no_wallet()
    hits = find(q, min(max(k, 1), 5))
    if not hits:
        return {"query": q, "cases": [], "note": "no match; try symptom words like 'timeout', 'hook', 'silent'"}
    return {"query": q, "cases": [_card(r) for r in hits]}


@app.get("/brief")
async def brief(action: str = ""):
    if not PAY_TO:
        return _no_wallet()
    hits = find(action, 5)
    return {
        "action": action,
        "risk_cases": [_card(r) for r in hits],
        "checklist": [c for c in (r["prevention"] for r in hits) if c][:5],
        "caveat": "Drawn from one operator's system. Treat as prior art, not as a guarantee.",
    }


def _research_rows() -> list[dict]:
    return [r for r in rows() if RESEARCH.search(json.dumps(r, ensure_ascii=False))]


@app.get("/research")
async def research(q: str = "", k: int = 5):
    """개체화·개인 온톨로지 측정을 시도하다 실패한 기록만. 이게 정말 안 나오는 데이터다."""
    if not PAY_TO:
        return _no_wallet()
    pool = _research_rows()
    terms = [t for t in re.split(r"[\s,]+", q.strip().lower()) if len(t) > 1 and t not in STOP]
    if terms:
        scored = sorted(((_score(r, terms), r) for r in pool), key=lambda x: -x[0])
        hits = [r for s, r in scored if s > 0][: min(max(k, 1), 10)] or pool[:k]
    else:
        hits = pool[: min(max(k, 1), 10)]
    return {
        "query": q,
        "scope": (
            "Failures from an 8-month attempt to measure one person's individuation "
            "(personal ontology): what broke in the corpus, the embeddings, the retrieval, "
            "and the measurement design itself."
        ),
        "pool_size": len(pool),
        "cases": [_card(r) for r in hits],
        "caveat": "Negative results included on purpose. Several of these measurements were later withdrawn.",
    }


@app.get("/archive")
async def archive(format: str = "json"):
    """전량. 값을 $1로 둔 건 한 번 팔리면 이 실험이 끝나기 때문이다(목표가 1달러였다)."""
    if not PAY_TO:
        return _no_wallet()
    all_rows = rows()
    return {
        "count": len(all_rows),
        "with_measured_evidence": sum(1 for r in all_rows if r["evidence"]),
        "research_subset": len(_research_rows()),
        "license": "Use freely. Attribution appreciated, not required.",
        "cases": [_card(r) for r in all_rows],
    }


# ── /audit: 판정을 내리기 전에 자기를 속이고 있는지 검사한다 ─────────
# 이 시장이 사는 건 일회성 지식이 아니라 매 실행마다 필요한 것이다(판매 상위 전원이 그렇다).
# 그래서 아카이브에서 얻은 규율을 "결론을 낼 때마다 부르는 관문"으로 내놓는다.
# LLM을 안 쓴다. 결정론이라 값이 싸고, 같은 입력에 같은 답이 나오고, 틀려도 왜 틀렸는지 보인다.
#
# 아홉 칸 전부 우리가 실제로 당한 것이고, 숫자는 그때 잰 값 그대로다.
CHECKS: list[dict] = [
    {
        "id": "positive_control",
        # ⚠️ "no significant difference"에는 "no difference"도 "not significant"도 안 들어 있다.
        # 실제 문장을 넣어보고서야 알았다(검출기에 양성 대조를 돌린 것). 근접 표현을 같이 넣는다.
        "triggers": ["no signal", "no effect", "not significant", "no significant", "insignificant",
                     "null result", "nothing found", "no difference", "did not differ",
                     "no measurable", "no correlation", "absent", "failed to detect",
                     "p > 0.05", "p>0.05"],
        "question": "Did you show the detector firing on something it should catch?",
        "why": "A silent detector and a healthy system produce identical output. Absence of "
               "signal is also consistent with a broken instrument, or with the stimulus never "
               "arriving at all.",
        "incident": "Global operationalizations returned null three times and were read as a "
                    "finding. With a positive control added, one arm turned out never to have "
                    "received the treatment.",
        "run": "Point the same detector at a case where the effect is known to exist. If it "
               "stays silent there too, your null says nothing.",
        "addressed_if": ["positive control", "known-positive", "sanity check", "control arm"],
    },
    {
        "id": "dose_response_equal",
        "triggers": ["threshold", "cutoff", "parameter", "min length", "arms", "condition",
                     "we set", "we varied", "dose", "tuned"],
        "question": "Did the same parameter apply the same treatment to every arm?",
        "why": "Equal parameters are not equal treatments. Strength must be matched by measured "
               "effect, not by the number you typed.",
        "incident": "Raising a minimum-length floor from 3 to 5 dropped 20.5% of fragments in "
                    "one arm and 2.7% in the other. Same number, two different experiments. One "
                    "effect size sat at zero for that reason alone.",
        "run": "Report the per-arm attrition (or equivalent) side by side before comparing outcomes.",
        "addressed_if": ["per-arm", "attrition", "each arm", "matched", "balanced"],
    },
    {
        "id": "denominator_inflation",
        "triggers": ["files", "rows", "records", "count", "total", "we have", "corpus size",
                     "documents", "entries", "n ="],
        "question": "Is your denominator counting the same thing more than once?",
        "why": "Append-only snapshot pipelines re-dump the same unit under a new name. Counting "
               "files then means counting history, and long-lived units dominate any sample.",
        "incident": "4,279 stored conversation files were 2,273 unique sessions plus 2,006 "
                    "re-dumps. Every count inflated 2.3x, and retrieval over-represented the "
                    "oldest session by up to 57x.",
        "run": "Deduplicate by the natural key, not the filename, and recount.",
        "addressed_if": ["deduplicat", "dedupe", "unique", "distinct", "by key"],
    },
    {
        "id": "silent_success",
        "triggers": ["exit 0", "succeeded", "ran fine", "no error", "completed", "passed",
                     "worked", "healthy", "green"],
        "question": "Would this look identical if it had done nothing?",
        "why": "Exit code zero means the process ended, not that the work happened. Expired "
               "credentials, empty inputs and skipped branches all exit clean.",
        "incident": "A publishing watcher exited 0 for days after its login expired. Nothing "
                    "was ever published and no alarm fired, because the process kept succeeding.",
        "run": "Assert on the produced artifact, not on the exit code. Count outputs, not runs.",
        "addressed_if": ["asserted", "verified output", "artifact", "counted output", "end-to-end"],
    },
    {
        "id": "no_call_site",
        "triggers": ["monitor", "detector", "watcher", "guard", "repair", "alert", "signal",
                     "health check", "we built", "automated"],
        "question": "Who actually calls this, and how often did it fire?",
        "why": "A diagnostic wired only to a human-readable signal has no executor. The work "
               "silently becomes someone's future decision, which means nobody's.",
        "incident": "A repair routine existed and its runner ticked 26,662 times over 22.7 "
                    "hours. It performed zero repairs, because a guard condition never matched. "
                    "Fourteen sessions sat dead the whole time.",
        "run": "Grep for the call site. If the only consumer is a log line or a human summary, "
               "the deterministic part belongs in the scheduler instead.",
        "addressed_if": ["call site", "cron", "scheduler", "invoked by", "wired to", "fired"],
    },
    {
        "id": "stale_cache_read",
        "triggers": ["listing", "dashboard", "api returned", "shows", "still says", "displayed",
                     "index", "cached", "ui"],
        "question": "Did you read the state, or a view of the state?",
        "why": "A read path can be cached at a different layer than the write path, so a "
               "successful write and an unchanged display are perfectly compatible.",
        "incident": "A re-registration reported four resources written, while the list endpoint "
                    "kept showing the previous text. Querying the record directly showed the new "
                    "values had landed four minutes earlier. The list was a cached view.",
        "run": "Fetch the single record by id, not the collection. Compare timestamps.",
        "addressed_if": ["by id", "direct query", "primary record", "bypass cache", "timestamp"],
    },
    {
        "id": "summary_not_source",
        "triggers": ["according to", "the readme", "the docs say", "summary", "listed as",
                     "appears to", "seems", "based on the description", "filename"],
        "question": "Have you read the primary text, or a compression of it?",
        "why": "Summaries, directory listings and filenames are lossy. What the compression "
               "dropped is exactly what reverses comparative verdicts.",
        "incident": "An external repository was judged weaker from its listing and one-line "
                    "descriptions. After reading all fourteen files the verdict inverted: the "
                    "thing that mattered, a held-out evaluation set, was invisible in the summary.",
        "run": "Clone or open the source and read the parts your claim depends on. State how "
               "much you read.",
        "addressed_if": ["read the source", "full text", "cloned", "read all", "verbatim"],
    },
    {
        "id": "self_measurement",
        "triggers": ["user said", "speaker", "authored", "personal", "human", "their writing",
                     "utterance", "voice", "style", "individual"],
        "question": "Is the instrument measuring its own output back to itself?",
        "why": "Pipelines that record 'user' turns often capture injected system text, agent "
               "output and inter-process messages under the same label. The measurement then "
               "reports the machine to the machine.",
        "incident": "Blocks labelled as human turns were between 14% and 56% machine-authored "
                    "depending on the month, and the contamination rate tracked agent activity "
                    "rather than anything about the person.",
        "run": "Sample the largest remaining items by hand. An exclusion list only removes what "
               "you already knew about.",
        "addressed_if": ["sampled", "hand-check", "manually inspect", "filtered and verified",
                         "provenance"],
    },
    {
        "id": "doc_claims_code",
        "triggers": ["configured", "the system does", "weighting", "the flag", "by design",
                     "documented", "supposed to", "should be"],
        "question": "Did you verify the behaviour in the code, or quote a document about it?",
        "why": "Code changes and documentation does not follow. A dead sentence outlives the "
               "behaviour it described, and reading it aloud launders it back into fact.",
        "incident": "A weighting rule was quoted from documentation for weeks. A full read of "
                    "the retrieval code found no such weighting anywhere. It had been removed "
                    "six weeks earlier.",
        "run": "Grep the behaviour in the code before citing it, and fix the document in the "
               "same sitting if it disagrees.",
        "addressed_if": ["grepped", "read the code", "verified in code", "traced"],
    },
]


# 사람은 "결론 한 줄 + 증거 한 줄"로 말하지 않는다. 보고서 문단으로 말한다.
# 그래서 문단을 받아 그 안에서 주장 문장을 골라낸다. 결정론이다(모델 안 부른다).
# 표지는 "무언가를 단언하는 문장"에 붙는 말들이다. 없으면 서술이지 주장이 아니다.
CLAIM_MARKERS = (
    "we found", "we show", "we observe", "we conclude", "results show", "results indicate",
    "this shows", "this demonstrates", "this confirms", "this proves", "indicates that",
    "suggests that", "demonstrates that", "no significant", "not significant", "significant",
    "outperform", "improves", "improved", "reduces", "reduced", "increases", "increased",
    "faster than", "better than", "worse than", "no effect", "no difference", "correlat",
    "caused by", "because of", "therefore", "so we", "which means", "confirms",
    "p =", "p<", "p <", "p-value", "accuracy", "%",
)

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _claims(text: str, cap: int = 8) -> list[str]:
    """문단에서 짚어볼 문장을 고른다.

    표지 목록만으로 고르면 목록에 없는 표현을 통째로 놓친다(실측: 다섯 문장 중 하나만 걸렸다).
    그래서 기준을 바꿨다: **검사 하나라도 걸리는 문장**이면 짚어볼 값이 있다.
    이미 있는 검사표가 곧 관련성 필터라, 표지 목록을 따로 관리할 필요가 없다.
    단언 표지는 순위를 매기는 데만 쓴다(같은 수의 검사가 걸리면 단언하는 쪽을 위로).
    """
    scored = []
    for raw in _SENT_SPLIT.split(text or ""):
        sent = raw.strip()
        if len(sent) < 20:
            continue
        low = sent.lower()
        hits = sum(1 for c in CHECKS if any(t in low for t in c["triggers"]))
        if not hits:
            continue
        assertive = sum(1 for m in CLAIM_MARKERS if m in low)
        scored.append((hits + assertive, sent))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:cap]]


def _audit(claim: str, evidence: str, trigger_on: str | None = None) -> dict:
    """trigger_on을 주면 그 문장으로만 검사를 걸고, 다뤄졌는지는 evidence 전체에서 본다.

    문단 모드에서 이게 필요하다. 문단 전체로 검사를 걸면 다른 문장의 위험이 이 문장에 붙어서,
    모든 주장이 모든 검사에 걸린 것처럼 보인다(실측: 문장 하나에 5칸이 붙었다).
    걸리는 것은 그 문장이 말하는 것으로, 해소됐는지는 글 전체로 본다. 그게 맞는 짝이다.
    """
    text = (trigger_on if trigger_on is not None else f"{claim} {evidence}").lower()
    ev = evidence.lower()
    triggered, unaddressed = [], []
    for c in CHECKS:
        if not any(t in text for t in c["triggers"]):
            continue
        done = any(a in ev for a in c["addressed_if"])
        triggered.append({
            "id": c["id"],
            "question": c["question"],
            "why_it_matters": c["why"],
            "real_incident": c["incident"],
            "what_to_run": c["run"],
            "looks_addressed": done,
        })
        if not done:
            unaddressed.append(c["id"])
    return {"triggered": triggered, "unaddressed": unaddressed}


def _themes(r: dict) -> list[str]:
    """이 사례가 어느 함정을 예시하는가. 검사표를 그대로 재사용한다(분류를 따로 만들지 않는다)."""
    blob = json.dumps(r, ensure_ascii=False).lower()
    return [c["id"] for c in CHECKS if any(t in blob for t in c["triggers"])]


@app.get("/contents")
async def contents(theme: str = ""):
    """무료. 선반을 통째로 보여준다. 제목만, 본문은 없다.

    미리보기 2건으로는 폭을 알 수 없어서 1달러가 비싼지 싼지 판단이 안 된다.
    목록을 다 보여주면 무엇을 사는지가 눈에 보이고, 그게 정직한 판매다.
    """
    all_rows = rows()
    research = {id(r) for r in _research_rows()}
    items = []
    counts: dict[str, int] = {}
    for r in all_rows:
        th = _themes(r)
        for t in th:
            counts[t] = counts.get(t, 0) + 1
        if theme and theme not in th:
            continue
        items.append({
            "case_id": f"{r['kind']}-{r['id']}",
            "title": r["title"],
            "themes": th,
            "has_measured_evidence": bool(r["evidence"]),
            "measurement_half": id(r) in research,
        })
    return {
        "total_cases": len(all_rows),
        "showing": len(items),
        "filter": theme or None,
        "themes_available": dict(sorted(counts.items(), key=lambda x: -x[1])),
        "with_measured_evidence": sum(1 for r in all_rows if r["evidence"]),
        "measurement_half": len(research),
        "titles_only": True,
        "full_text": {"route": "/archive", "price": PRICE_ARCHIVE,
                      "note": "every case in one response, one payment, no subscription"},
        "cases": items,
    }


@app.get("/precheck")
async def precheck(claim: str = "", evidence: str = "", text: str = ""):
    """무료. 어느 칸에 걸리는지까지만 알려준다.

    콜드스타트에서 진짜 병목은 발견이 아니라 "왜 돈을 내야 하는지 모른다"는 것이다.
    이 라우트는 걸린 칸의 *이름과 질문*을 공짜로 준다. 그것만으로도 쓸모가 있어서 돌려보게 되고,
    돌려보면 "그래서 뭘 어떻게 확인하라는 건데"가 남는다. 그 답이 유료 /audit이다.
    미끼가 정직하려면 무료분만으로도 실제로 도움이 돼야 한다.
    """
    if text.strip():
        found = _claims(text)
        if not found:
            return {
                "text_len": len(text),
                "claims_found": 0,
                "note": ("No sentence in this text asserts a finding. Nothing to check. "
                         "If you expected claims here, they may be phrased as description."),
            }
        per = []
        for c in found:
            rr = _audit(c, text, trigger_on=c)
            per.append({
                "claim": c,
                "verdict": "hold" if rr["unaddressed"] else "proceed",
                "checks_tripped": [x["id"] for x in rr["triggered"]],
                "unaddressed": rr["unaddressed"],
            })
        return {
            "mode": "text",
            "claims_found": len(found),
            "claims_holding": sum(1 for x in per if x["verdict"] == "hold"),
            "claims": per,
            "free_tier": "Which sentences are claims, and which checks each one trips.",
            "paid_tier": {"route": "/audit", "price": PRICE_AUDIT,
                          "adds": "why each check matters and the real incident behind it"},
            "note": "Deterministic. Sentences are selected by assertion markers, not by a model.",
        }
    if not claim.strip():
        return {
            "error": "claim or text is required",
            "usage": ("/precheck?claim=<what you concluded>&evidence=<what you measured>"
                      "  or  /precheck?text=<paste a paragraph of your findings>"),
        }
    r = _audit(claim, evidence)
    return {
        "claim": claim,
        "verdict": "hold" if r["unaddressed"] else ("proceed" if r["triggered"] else "no_checks_matched"),
        "checks_tripped": [
            {"id": c["id"], "question": c["question"], "looks_addressed": c["looks_addressed"]}
            for c in r["triggered"]
        ],
        "unaddressed_count": len(r["unaddressed"]),
        "free_tier": "You get which checks you tripped and the question each one asks.",
        "paid_tier": {
            "route": "/audit",
            "price": PRICE_AUDIT,
            "adds": (
                "why each check matters, the real incident behind it with the numbers measured "
                "at the time, and the specific thing to run to settle it"
            ),
        },
        "note": "Deterministic. No model is consulted, and this preview costs nothing.",
    }


@app.get("/audit")
async def audit(claim: str = "", evidence: str = "", text: str = ""):
    """결론을 내기 전에 부르는 관문. 매 실험·매 판정마다 필요하니 반복 호출된다."""
    if not PAY_TO:
        return _no_wallet()
    if text.strip():
        found = _claims(text)
        if not found:
            return {"mode": "text", "claims_found": 0,
                    "note": "No sentence in this text asserts a finding."}
        return {
            "mode": "text",
            "claims_found": len(found),
            "claims": [{"claim": c, **_audit(c, text, trigger_on=c)} for c in found],
            "checks_available": len(CHECKS),
            "caveat": "A clean pass is not proof. It means these nine known traps were considered.",
        }
    if not claim.strip():
        return {
            "error": "claim or text is required",
            "usage": ("/audit?claim=<what you concluded>&evidence=<what you measured>"
                      "  or  /audit?text=<paste a paragraph of your findings>"),
        }
    r = _audit(claim, evidence)
    # 판정은 규칙이 내린다. 걸린 칸 중 증거에서 다뤄진 흔적이 없는 게 하나라도 있으면 보류다.
    verdict = "hold" if r["unaddressed"] else ("proceed" if r["triggered"] else "no_checks_matched")
    return {
        "claim": claim,
        "verdict": verdict,
        "unaddressed": r["unaddressed"],
        "checks": r["triggered"],
        "checks_available": len(CHECKS),
        "note": (
            "Deterministic. No model is consulted, so the same input always returns the same "
            "checks. Every check is a failure this operator actually shipped, with the numbers "
            "measured at the time."
        ),
        "caveat": "A clean pass is not proof the claim is true. It only means these nine known "
                  "ways of fooling yourself were considered.",
    }


# ── x402 페이월 (지갑이 있을 때만 장착) ──────────────────────────────
if PAY_TO:
    from x402 import x402ResourceServer
    from x402.http import HTTPFacilitatorClient
    from x402.http.middleware.fastapi import payment_middleware
    from x402.http.types import PaywallConfig
    from x402.mechanisms.evm.exact.register import register_exact_evm_server

    def _accepts(price: str) -> list[dict]:
        return [{"scheme": "exact", "payTo": PAY_TO, "price": price, "network": n} for n in NETWORKS]

    # 목록에 뜨는 한 줄이 전부다. 등재기(x402scan)는 RouteConfig의 description·tags를
    # 그대로 카드에 싣는데, 비워두면 FastAPI가 함수 이름으로 "Search"를 채운다.
    # 사는 쪽은 그 한 줄만 보고 고르므로 "무엇을 주는지"를 첫 문장에 박는다.
    # 한도(등재기가 조용히 잘라낸다): service_name 32자, tag 5개·각 32자.
    SERVICE = "Agent Failure Archive"
    TAGS = ["failures", "postmortem", "agents", "debugging", "reliability"]

    # ⚠️ bazaar 확장은 {info, schema} 두 칸을 다 요구한다. info만 넣었더니 SDK가
    # "malformed"로 경고하고 그 확장을 통째로 흘렸다(실측, 기동 로그에만 뜨는 조용한 종류).
    # 손으로 모양을 맞추지 말고 공식 헬퍼가 만들게 한다. 그래야 규격이 바뀌어도 따라간다.
    try:
        from x402.extensions.bazaar import declare_discovery_extension as _declare
    except ImportError:  # 구버전 SDK 폴백
        def _declare(input=None):  # noqa: A002
            return {"bazaar": {"info": {"input": input or {}}, "schema": {}}}

    def _route(price: str, desc: str, sample_input: dict) -> dict:
        return {
            "accepts": _accepts(price),
            "description": desc,
            "mime_type": "application/json",
            "service_name": SERVICE,
            "tags": TAGS,
            # 입력 스키마가 비면 등재기가 "non-invocable"로 걸러낸다(x402scan DISCOVERY.md).
            # 인자가 없는 라우트도 형식만 채워 둔다.
            "extensions": _declare(input=sample_input),
        }

    ROUTES = {
        "GET /audit": _route(
            PRICE_AUDIT,
            "Check a claim against nine ways of fooling yourself, before you act on it. "
            "Deterministic, no model in the loop. Every check is a failure that actually shipped.",
            {"claim": "the feature has no measurable effect", "evidence": "ran it on 40 samples, p=0.4"},
        ),
        "GET /search": _route(
            PRICE_SEARCH,
            "Search 186 real AI-agent post-mortems by symptom. Returns 3 matching incidents "
            "with root cause, the fix that worked, and the rule that stops it recurring.",
            {"q": "hook dies silently"},
        ),
        "GET /brief": _route(
            PRICE_BRIEF,
            "Pre-flight check before an irreversible action. Describe the move; get back the "
            "failure modes that already hit that exact move, and what to verify first.",
            {"action": "wire a new cron job"},
        ),
        "GET /research": _route(
            PRICE_RESEARCH,
            "107 measurement failures from eight months of trying to measure one person with "
            "embeddings. Silent detectors, absent positive controls, instruments measuring themselves.",
            {"q": "positive control"},
        ),
        "GET /archive": _route(
            PRICE_ARCHIVE,
            "Every one of the 186 incidents in a single response. One payment, no signup, "
            "no subscription. Buy it once and the whole corpus is yours.",
            {"format": "json"},
        ),
    }

    # exact 스킴을 서버 쪽에 등록하지 않으면 라우트가 통째로 죽는다
    # (RouteConfigurationError: No scheme for "exact"). 이 한 줄이 그 관문이다.
    _fac = HTTPFacilitatorClient({"url": FACILITATOR}) if FACILITATOR else HTTPFacilitatorClient()
    _server = register_exact_evm_server(x402ResourceServer(_fac), networks=NETWORKS)
    # 브라우저로 들어온 사람에게는 SDK가 지갑 연결 결제창을 띄운다. 이름을 안 주면
    # 그 창이 "Payment Required"로만 뜨고 무엇을 사는지가 안 보인다.
    _mw = payment_middleware(ROUTES, _server, PaywallConfig(app_name="Agent Failure Archive"))

    LEDGER = Path(os.environ.get("X402_LEDGER", "/tmp/afa_payments.jsonl"))

    @app.middleware("http")
    async def x402_paywall(request: Request, call_next):
        paying = bool(request.headers.get("x-payment") or request.headers.get("payment"))
        resp = await _mw(request, call_next)
        # 결제를 시도한 호출만 남긴다. 402를 받고 사라진 사람은 손님이 아니라 구경꾼이다.
        # 첫 결제가 거절당하면 그 사람은 돈만 잃으므로, 거절 이유가 안 남으면 고칠 수가 없다.
        if paying:
            try:
                with LEDGER.open("a", encoding="utf-8") as f:
                    f.write(
                        json.dumps(
                            {
                                "path": request.url.path,
                                "query": str(request.url.query),
                                "status": resp.status_code,
                                "settled": bool(resp.headers.get("payment-response")),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except Exception as e:
                print(f"[ledger] {type(e).__name__}: {e}", file=sys.stderr)
            print(f"[x402] payment attempt {request.url.path} -> {resp.status_code}", file=sys.stderr)
        return resp

    print(f"[x402] paid routes live · payTo={PAY_TO[:10]}… network={NETWORK}", file=sys.stderr)
else:
    print("[x402] X402_PAY_TO unset — free routes only, paid routes return 503", file=sys.stderr)


# ── 두 번째 결제 규격(MPP) ────────────────────────────────────────
# x402 쪽 자동 발견면이 전부 "이미 거래가 있었을 것"을 요구해서 신규 판매자는 안 보인다.
# MPP는 공식 디렉토리가 PR로 등재를 받는다 = 실적 없이도 들어갈 수 있는 문. 그래서 같이 연다.
# 경로를 /mpp/* 로 갈라 x402 미들웨어와 안 겹치게 했고, 실패해도 본체는 계속 팔도록 감쌌다.
MPP_PATHS: list[str] = []
try:
    import mpp_routes  # noqa: E402

    MPP_PATHS = mpp_routes.attach(app, sys.modules[__name__])
    print(f"[mpp] paid routes live · {len(MPP_PATHS)} paths", file=sys.stderr)
except Exception as e:  # 조용히 죽지 않는다
    print(f"[mpp] not attached: {type(e).__name__}: {e}", file=sys.stderr)


# ── OpenAPI에 결제 정보 심기 (등재기 1순위 발견 경로) ────────────────
# x402scan은 /openapi.json을 먼저 본다. 거기 x-payment-info와 402 응답이 없으면
# 유료 라우트인 줄 모르고 지나간다(docs/DISCOVERY.md §A).
# 등재기는 operation의 summary/description을 카드 문구로 그대로 쓴다.
# 비워두면 FastAPI가 함수 이름("Search")을 넣고, 목록에서 그 한 줄이 전부라 아무도 안 누른다.
_PAID = {
    "/audit": (
        PRICE_AUDIT,
        "Audit a claim before you act on it",
        "Give it what you concluded and what you actually measured. It returns the checks your "
        "claim trips, each one a failure that really shipped: silent detectors, unequal "
        "treatment arms, inflated denominators, exit-0 successes that did nothing, cached views "
        "read as state. Deterministic, so the same input always returns the same audit.",
    ),
    "/search": (
        PRICE_SEARCH,
        "Search agent post-mortems by symptom",
        "Search 186 real AI-agent post-mortems by symptom. Returns 3 matching incidents with "
        "root cause, the fix that worked, and the rule that stops it recurring. No signup.",
    ),
    "/brief": (
        PRICE_BRIEF,
        "Pre-flight risk check before an irreversible action",
        "Describe what you are about to do. Get back the failure modes that already hit that "
        "exact move, drawn from 186 logged incidents, and what to verify before you commit.",
    ),
    "/research": (
        PRICE_RESEARCH,
        "107 failures from measuring a person with embeddings",
        "Eight months of trying to measure one person's individuation. Silent detectors, "
        "missing positive controls, dose-response arms that were never equal, instruments "
        "that kept measuring themselves. The failures nobody publishes.",
    ),
    "/archive": (
        PRICE_ARCHIVE,
        "The entire corpus in one response",
        "Every one of the 186 incidents in a single call. One payment, no subscription, "
        "no account. Buy it once and the whole corpus is yours.",
    ),
}
_PRICES = {p: v[0] for p, v in _PAID.items()}


def _openapi():
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi

    schema = get_openapi(
        title="Agent Failure Archive",
        version="0.2.0",
        description=(
            "186 post-mortems from 8 months of running a multi-session AI agent system "
            "while trying to measure one person's individuation. Pay per call over x402."
        ),
        routes=app.routes,
    )
    schema["servers"] = [{"url": PUBLIC}]
    for path, (price, summary, blurb) in _PAID.items():
        op = schema.get("paths", {}).get(path, {}).get("get")
        if not op:
            continue
        op["summary"] = summary
        op["description"] = f"{blurb} Price: {price} per call, settled in USDC on Base via x402."
        op["operationId"] = "afa_" + path.strip("/")
        op["tags"] = ["Agent Failure Archive"]
        op["x-payment-info"] = {
            "protocols": ["x402"],
            "price": {"mode": "fixed", "currency": "USD", "amount": price.lstrip("$")},
            "networks": NETWORKS,
            "payTo": PAY_TO,
            "asset": "USDC",
        }
        op.setdefault("responses", {})["402"] = {
            "description": "Payment required. The challenge ships in the Payment-Required header."
        }
    app.openapi_schema = schema
    return schema


app.openapi = _openapi


# 등재기(Next.js)는 fetch 응답을 캐시한다. 캐시 헤더가 없으면 첫 크롤 결과를 계속 재사용해서,
# 설명·태그를 고쳐 다시 등록해도 옛 문구가 그대로 남는다(실측: 재등록 registered=4인데
# lastUpdated가 첫 등록 시각에서 안 움직임). 발견 문서만 no-store로 못박아 매번 새로 읽게 한다.
_NO_STORE = ("/openapi.json", "/.well-known/x402", "/.well-known/x402.json", "/llms.txt")


# ⚠️ 실측 1,031건 중 632건(61%)이 405였다. 크롤러가 HEAD·OPTIONS로 생존과 지원 메서드를 훑는데
# FastAPI는 GET 라우트에 HEAD를 자동으로 붙이지 않는다. 생존 확인을 HEAD로 하는 색인기 눈에는
# 우리가 죽은 주소로 보인다("reachable endpoint" 집계에서 빠지는 자리).
# 라우트를 다 고치는 대신 바깥에서 HEAD를 GET으로 돌리고 본문만 버린다(HEAD 의미 그대로).
# 접속 원장. uvicorn 기본 로그는 User-Agent를 안 남겨서 "크롤러인가 사람인가"를 못 가른다.
# 실측으로 이게 문제가 됐다: 외부 요청 1,031건을 보고 "전환 문제"라고 단정했는데
# IP를 조회해보니 전부 AWS·GCP였다 = 색인기지 손님이 아니다. 판정이 통째로 바뀌는 차이다.
# IP는 /24까지만 남긴다(제3자 주소를 통째로 쌓을 이유가 없다).
ACCESS = Path(os.environ.get("X402_ACCESS_LOG", "/tmp/afa_access.jsonl"))


@app.middleware("http")
async def access_log(request: Request, call_next):
    resp = await call_next(request)
    try:
        host = (request.client.host if request.client else "") or ""
        prefix = ".".join(host.split(".")[:3]) + ".0" if host.count(".") == 3 else host
        with ACCESS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "path": request.url.path,
                "method": request.method,
                "status": resp.status_code,
                "ua": (request.headers.get("user-agent") or "")[:180],
                "ref": (request.headers.get("referer") or "")[:180],
                "ip24": prefix,
            }, ensure_ascii=False) + "\n")
    except Exception as e:  # 원장이 막혀도 서비스는 계속 판다
        print(f"[access] {type(e).__name__}: {e}", file=sys.stderr)
    return resp


@app.middleware("http")
async def head_as_get(request: Request, call_next):
    if request.method != "HEAD":
        return await call_next(request)
    request.scope["method"] = "GET"
    resp = await call_next(request)
    # 헤더는 GET이 줬을 것 그대로 두고 본문만 비운다. content-length도 유지가 맞다.
    return Response(status_code=resp.status_code, headers=dict(resp.headers))


@app.middleware("http")
async def no_store_discovery(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path in _NO_STORE:
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp
