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
@app.get("/")
async def index():
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
        "triggers": ["no signal", "no effect", "not significant", "null result", "nothing found",
                     "no difference", "no correlation", "absent", "failed to detect"],
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


def _audit(claim: str, evidence: str) -> dict:
    text = f"{claim} {evidence}".lower()
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


@app.get("/audit")
async def audit(claim: str = "", evidence: str = ""):
    """결론을 내기 전에 부르는 관문. 매 실험·매 판정마다 필요하니 반복 호출된다."""
    if not PAY_TO:
        return _no_wallet()
    if not claim.strip():
        return {
            "error": "claim is required",
            "usage": "/audit?claim=<what you concluded>&evidence=<what you actually measured>",
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

    def _route(price: str, desc: str, sample_input: dict) -> dict:
        return {
            "accepts": _accepts(price),
            "description": desc,
            "mime_type": "application/json",
            "service_name": SERVICE,
            "tags": TAGS,
            # 입력 스키마가 비면 등재기가 "non-invocable"로 걸러낸다(x402scan DISCOVERY.md).
            # 인자가 없는 라우트도 형식만 채워 둔다.
            "extensions": {"bazaar": {"discoverable": True, "info": {"input": sample_input}}},
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


@app.middleware("http")
async def no_store_discovery(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path in _NO_STORE:
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp
