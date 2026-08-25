"""원격 MCP 엔드포인트. 설치할 것 없이 URL 하나로 붙고, 유료 도구는 MCP 층에서 바로 결제된다.

왜 로컬 다리(mcp_server.py)로 안 끝냈나 (실측 근거):
    공식 MCP 레지스트리 100건을 열어보니 **71건이 remote**(streamable-http URL)였고
    로컬 패키지는 npm 28 · pypi 1 · oci 1뿐이다. 즉 이 생태계의 기본 배포 모양은 URL이다.
    로컬 다리는 지갑을 쓰는 사람이 쥐어야 해서 남기고, 발견·설치의 정문은 여기로 낸다.

무료 도구는 지갑도 설정도 없이 아무 MCP 클라이언트에서 그냥 된다.
유료 도구는 x402 MCP 확장으로 감싼다. 낼 수 있는 클라이언트는 그 자리에서 내고,
못 내는 클라이언트는 조용히 실패하지 않고 결제 명세를 받는다.

배선: server.py 가 `/mcp` 아래에 마운트한다. import 가 깨지면 무료 HTTP 서비스는 그대로 살고
경고만 stderr 로 나간다(마운트 실패로 본체가 죽으면 그게 더 큰 사고다).
"""
from __future__ import annotations

import json
import os

# ⚠️ x402의 MCP 결제 래퍼가 `mcp.server.fastmcp.Context`를 하드 import 한다.
# mcp 2.x는 그 경로를 없앴다(실측 ModuleNotFoundError). 그래서 이 프로세스만 mcp 1.x로 고정한다.
# 본체 서비스 venv는 안 건드린다 = MCP가 깨져도 돈 받는 HTTP는 계속 판다.
try:
    from mcp.server.fastmcp import FastMCP as MCPServer  # mcp 1.x (x402 결제 래퍼 호환)
except ImportError:  # pragma: no cover
    from mcp.server.mcpserver import MCPServer  # mcp 2.x (무료 도구만 동작)
from x402 import x402ResourceServer
from x402.http import HTTPFacilitatorClient
from x402.mcp import create_payment_wrapper
from x402.mechanisms.evm.exact.register import register_exact_evm_server
from x402.schemas.payments import PaymentRequirements, ResourceInfo

PAY_TO = os.environ.get("X402_PAY_TO", "0xFC15354FE6a96d87399582dbe9DF8d2739B1fF9a").strip()
NETWORK = os.environ.get("X402_MCP_NETWORK", "eip155:8453")
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
FACILITATOR = os.environ.get("X402_FACILITATOR", "https://facilitator.payai.network").strip()

# 원자 단위. USDC는 소수점 6자리라 1달러가 1,000,000이다. 여기 0 하나 틀리면 값이 10배가 된다.
AMOUNTS = {"audit": "20000", "search": "10000", "brief": "50000",
           "research": "250000", "archive": "1000000"}


def _accepts(tool: str) -> list[PaymentRequirements]:
    return [
        PaymentRequirements(
            scheme="exact",
            network=NETWORK,
            asset=USDC,
            amount=AMOUNTS[tool],
            pay_to=PAY_TO,
            max_timeout_seconds=300,
            # EIP-712 도메인. 이게 없으면 지갑이 서명할 대상을 특정 못 한다.
            extra={"name": "USD Coin", "version": "2"},
        )
    ]


def build(logic) -> MCPServer:
    """logic = server 모듈. 같은 프로세스라 HTTP를 한 번 더 타지 않고 함수를 직접 부른다."""
    mcp = MCPServer(
        name="agent-failure-archive",
        instructions=(
            "186 real post-mortems from running a multi-session AI agent system for eight "
            "months, 107 of them failures from trying to measure one person with embeddings. "
            "precheck and sample are free and need no wallet. The paid tools settle in USDC "
            "on Base per call, with no account and no subscription."
        ),
    )
    server = register_exact_evm_server(x402ResourceServer(HTTPFacilitatorClient({"url": FACILITATOR})))

    def paid(tool: str):
        return create_payment_wrapper(
            server,
            accepts=_accepts(tool),
            resource=ResourceInfo(url=f"mcp://tool/{tool}", description=f"Agent Failure Archive: {tool}"),
        )

    # ── 무료 ────────────────────────────────────────────────────────
    @mcp.tool(
        name="precheck",
        description=(
            "Free, no wallet. Check a conclusion against nine known ways of fooling yourself "
            "and get back which checks it trips plus the question each one asks. Use it before "
            "writing 'we found that'. The paid audit tool adds why each matters, the real "
            "incident with the numbers measured at the time, and what to run."
        ),
    )
    async def precheck(claim: str, evidence: str = "") -> str:
        r = logic._audit(claim, evidence)
        return json.dumps({
            "claim": claim,
            "verdict": "hold" if r["unaddressed"] else ("proceed" if r["triggered"] else "no_checks_matched"),
            "checks_tripped": [
                {"id": c["id"], "question": c["question"], "looks_addressed": c["looks_addressed"]}
                for c in r["triggered"]
            ],
            "paid_upgrade": {"tool": "audit", "price": "$0.02"},
        }, ensure_ascii=False)

    @mcp.tool(name="sample", description="Free, no wallet. Two complete post-mortems from the archive.")
    async def sample() -> str:
        rs = logic.rows()[:2]
        return json.dumps({"cases": [logic._card(r) for r in rs], "total_available": len(logic.rows())},
                          ensure_ascii=False)

    @mcp.tool(
        name="catalog",
        description="Free, no wallet. What the archive holds, what each paid tool costs, and how payment works.",
    )
    async def catalog() -> str:
        all_rows = logic.rows()
        return json.dumps({
            "cases": len(all_rows),
            "with_measured_evidence": sum(1 for r in all_rows if r["evidence"]),
            "measurement_failures": len(logic._research_rows()),
            "free_tools": ["precheck", "sample", "catalog", "contents"],
            "paid_tools": {"audit": "$0.02", "search": "$0.01", "brief": "$0.05",
                           "research": "$0.25", "archive": "$1.00"},
            "settlement": {"network": NETWORK, "asset": "USDC", "pay_to": PAY_TO,
                           "protocol": "x402", "account_required": False},
            "http_equivalent": "https://desktop-ai2ata5-1.tailfeb765.ts.net",
        }, ensure_ascii=False)

    @mcp.tool(
        name="contents",
        description=("Free, no wallet. Every case title in the archive, tagged with the trap it "
                     "illustrates. Titles only, no bodies. Read this to see what the paid archive "
                     "actually contains before deciding it is worth a dollar."),
    )
    async def contents(theme: str = "") -> str:
        all_rows = logic.rows()
        counts: dict = {}
        items = []
        for r in all_rows:
            th = logic._themes(r)
            for x in th:
                counts[x] = counts.get(x, 0) + 1
            if theme and theme not in th:
                continue
            items.append({"case_id": f"{r['kind']}-{r['id']}", "title": r["title"], "themes": th,
                          "has_measured_evidence": bool(r["evidence"])})
        return json.dumps({
            "total_cases": len(all_rows), "showing": len(items),
            "themes_available": dict(sorted(counts.items(), key=lambda x: -x[1])),
            "titles_only": True,
            "full_text": {"tool": "archive", "price": "$1.00"},
            "cases": items,
        }, ensure_ascii=False)

    # ── 유료 ────────────────────────────────────────────────────────
    @mcp.tool(name="audit", description="$0.02. Full audit of a claim: why each tripped check matters, "
                                        "the incident behind it with measured numbers, and what to run.")
    @paid("audit")
    async def audit(claim: str, evidence: str = "") -> str:
        r = logic._audit(claim, evidence)
        return json.dumps({"claim": claim, "unaddressed": r["unaddressed"], "checks": r["triggered"]},
                          ensure_ascii=False)

    @mcp.tool(name="search", description="$0.01. Three real agent post-mortems matching a symptom, each "
                                         "with root cause, the fix that worked, and the prevention rule.")
    @paid("search")
    async def search(q: str) -> str:
        hits = logic.find(q, 3)
        return json.dumps({"query": q, "cases": [logic._card(r) for r in hits]}, ensure_ascii=False)

    @mcp.tool(name="brief", description="$0.05. Pre-flight risk brief before an irreversible action, "
                                        "drawn from the ways that class of action actually failed.")
    @paid("brief")
    async def brief(action: str) -> str:
        hits = logic.find(action, 5)
        return json.dumps({
            "action": action,
            "risk_cases": [logic._card(r) for r in hits],
            "checklist": [c for c in (r["prevention"] for r in hits) if c][:5],
        }, ensure_ascii=False)

    @mcp.tool(name="research", description="$0.25. The 107 measurement failures from an eight-month "
                                           "attempt to measure one person's individuation with embeddings.")
    @paid("research")
    async def research(q: str = "") -> str:
        pool = logic._research_rows()
        hits = logic.find(q, 5) if q else []
        hits = [h for h in hits if h in pool] or pool[:5]
        return json.dumps({"query": q, "pool_size": len(pool),
                           "cases": [logic._card(r) for r in hits]}, ensure_ascii=False)

    @mcp.tool(name="archive", description="$1.00. Every case in one response. One payment, no "
                                          "subscription, no account.")
    @paid("archive")
    async def archive() -> str:
        all_rows = logic.rows()
        return json.dumps({"count": len(all_rows), "cases": [logic._card(r) for r in all_rows]},
                          ensure_ascii=False)

    return mcp
