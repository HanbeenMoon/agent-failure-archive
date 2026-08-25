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
from fastapi.responses import JSONResponse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
# 배포 환경엔 레포가 없다. 동봉본을 먼저 보고, 없으면 레포 원본으로 폴백.
CORPUS = next(
    (p for p in (HERE / "data" / "failure_corpus.jsonl",
                 ROOT / "T9OS" / "data" / "revenue" / "failure_corpus.jsonl") if p.exists()),
    HERE / "data" / "failure_corpus.jsonl",
)

PAY_TO = os.environ.get("X402_PAY_TO", "").strip()
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


def rows() -> list[dict]:
    global _ROWS
    if not _ROWS and CORPUS.exists():
        _ROWS = [json.loads(l) for l in CORPUS.read_text(encoding="utf-8").splitlines() if l.strip()]
    return _ROWS


def _score(r: dict, terms: list[str]) -> int:
    blob = f"{r['title']} {r['symptom']} {r['root_cause']} {r['fix']} {r['prevention']}"
    s = sum(blob.count(t) for t in terms)
    s += 4 * sum(1 for t in terms if t in r["title"])
    s += 2 * sum(1 for t in terms for e in r["evidence"] if t in e)
    return s


def find(q: str, k: int = 3) -> list[dict]:
    terms = [t for t in re.split(r"[\s,]+", q.strip()) if len(t) > 1]
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

    @app.middleware("http")
    async def x402_paywall(request: Request, call_next):
        return await _mw(request, call_next)

    print(f"[x402] paid routes live · payTo={PAY_TO[:10]}… network={NETWORK}", file=sys.stderr)
else:
    print("[x402] X402_PAY_TO unset — free routes only, paid routes return 503", file=sys.stderr)
