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
from fastapi.responses import JSONResponse, PlainTextResponse

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
PRICE_SEARCH = os.environ.get("X402_PRICE_SEARCH", "$0.01")
PRICE_BRIEF = os.environ.get("X402_PRICE_BRIEF", "$0.05")

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
@app.get("/")
async def index():
    return {
        "service": "Agent Failure Archive",
        "what": "Real post-mortems from running a multi-session AI agent system for 8 months.",
        "why": "Public repos show code that worked. These are the ones that died silently.",
        "cases": len(rows()),
        "with_measured_evidence": sum(1 for r in rows() if r["evidence"]),
        "endpoints": {
            "/sample": "free preview (2 cases)",
            "/search?q=<symptom>": f"{PRICE_SEARCH} per call, 3 cases with root cause + fix",
            "/brief?action=<what you are about to do>": f"{PRICE_BRIEF} per call, pre-flight risk brief",
        },
        "excludes": "no personal data, no operator utterances, no business records",
        "paid_routes_live": bool(PAY_TO),
        "networks": NETWORKS,
        "facilitator": FACILITATOR,
    }


@app.get("/.well-known/x402")
@app.get("/.well-known/x402.json")
async def well_known_x402():
    """크롤러·디렉토리가 표준으로 찾아보는 자리. 여기 없으면 아무도 못 줍는다."""
    return {
        "x402Version": 2,
        "name": "Agent Failure Archive",
        "description": (
            "186 post-mortems from running a multi-session AI agent system in production "
            "for 8 months: silent cron deaths, repairs wired to signals nobody consumes, "
            "watchers that exit 0 after their session expired."
        ),
        "endpoints": [
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
        ],
        "free": ["/", "/sample", "/llms.txt"],
        "accepts": [
            {"scheme": "exact", "network": n, "asset": "USDC", "payTo": PAY_TO} for n in NETWORKS
        ],
        "facilitator": FACILITATOR,
        "source": "https://github.com/HanbeenMoon/agent-failure-archive",
    }


@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt():
    """에이전트가 사람 페이지 대신 읽는 자리. 무엇을 파는지 한 화면에."""
    return f"""# Agent Failure Archive

{len(rows())} post-mortems from running a multi-session AI agent system in production for
8 months. Public repositories show code that worked; these are the wirings that looked
correct, passed review, ran for weeks, and were dead the whole time.

{sum(1 for r in rows() if r['evidence'])} of the cases carry measured evidence
(durations, counts, rates). Each case has a symptom, a root cause, the fix that was
applied, and the prevention rule it produced.

## Free
- GET /            what this is
- GET /sample      two full cases, no payment
- GET /llms.txt    this file

## Paid ({PRICE_SEARCH} - {PRICE_BRIEF} USDC on Base, x402, no API key, no signup)
- GET /search?q=<symptom>
  Three incidents matching a symptom. Try: "cron job silently stopped",
  "hook dies without error", "process exits 0 but did nothing".
- GET /brief?action=<what you are about to do>
  A pre-flight risk brief. Describe an action in plain words and get back the ways
  that class of action has actually failed, plus the checklist each incident produced.
  Reach for this before anything irreversible.

## Not included
No personal data, no operator utterances, no business records. Source documents
mentioning a person, a business relationship or a monetary amount are excluded whole.

## Caveat
This is one operator's system. Treat it as prior art, not as a guarantee. Coverage is
skewed toward multi-session coordination, hook and cron wiring, retrieval pipelines,
and scheduled repair.

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


# ── x402 페이월 (지갑이 있을 때만 장착) ──────────────────────────────
if PAY_TO:
    from x402 import x402ResourceServer
    from x402.http import HTTPFacilitatorClient
    from x402.http.middleware.fastapi import payment_middleware
    from x402.mechanisms.evm.exact.register import register_exact_evm_server

    def _accepts(price: str) -> list[dict]:
        return [{"scheme": "exact", "payTo": PAY_TO, "price": price, "network": n} for n in NETWORKS]

    ROUTES = {
        "GET /search": {
            "accepts": _accepts(PRICE_SEARCH),
            "extensions": {"bazaar": {"discoverable": True, "info": {"input": {"q": "hook dies silently"}}}},
        },
        "GET /brief": {
            "accepts": _accepts(PRICE_BRIEF),
            "extensions": {"bazaar": {"discoverable": True, "info": {"input": {"action": "wire a new cron job"}}}},
        },
    }

    # exact 스킴을 서버 쪽에 등록하지 않으면 라우트가 통째로 죽는다
    # (RouteConfigurationError: No scheme for "exact"). 이 한 줄이 그 관문이다.
    _fac = HTTPFacilitatorClient({"url": FACILITATOR}) if FACILITATOR else HTTPFacilitatorClient()
    _server = register_exact_evm_server(x402ResourceServer(_fac), networks=NETWORKS)
    _mw = payment_middleware(ROUTES, _server)

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
