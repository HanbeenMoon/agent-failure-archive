#!/usr/bin/env python3
"""Agent Failure Archive를 MCP 서버로 노출한다. 에이전트가 도구로 직접 부르고, 유료 칸은 자기 지갑으로 낸다.

왜 이걸 만들었나 (실측 근거):
    x402 판매 상위권을 재보니 돈은 **디렉토리 발견**으로 흐르지 않는다. 등재 사이트의 챗 채널은
    108일째 호출 0인데(통산 10툴), 같은 기간 온체인은 30일 780만 콜이 돌았다.
    즉 이 시장의 수요 경로는 "개발자가 어떤 서비스를 자기 에이전트에 **일부러 꽂는 것**"이다.
    MCP는 정확히 그 꽂는 행위의 표준 통로다. 그래서 디렉토리 최적화 대신 여기에 붙인다.

구조 (중요):
    이 서버는 **사용자 컴퓨터에서 로컬로 돈다.** 결제 게이트는 여기가 아니라 원격 HTTP 서비스에 있다.
    로컬에 게이트를 두면 사용자가 그냥 지워버릴 수 있어서 아무 의미가 없다.
    따라서 이 파일은 다리다. 무료 도구는 그냥 GET, 유료 도구는 402를 받아 사용자 지갑으로 서명해 재요청.

지갑:
    유료 도구는 환경변수 `X402_PRIVATE_KEY`(사용자 지갑)를 쓴다. **우리는 그 값을 보지도, 받지도,
    기록하지도 않는다.** 사용자 기계 안에서만 서명에 쓰인다. 키가 없으면 조용히 실패하지 않고
    무엇이 필요한지 말한 뒤 402 명세를 그대로 돌려준다(우리가 파는 그 교훈).

설치 (사용자):
    pip install "mcp[cli]" "x402[mcp]" requests eth-account
    # Claude Desktop / Cursor 설정
    # "agent-failure-archive": {"command": "python3", "args": ["<이 파일 경로>"],
    #                           "env": {"X402_PRIVATE_KEY": "0x..."}}      # 유료 도구 쓸 때만

무료 도구만 쓸 거면 키 없이 그냥 돌아간다.
"""
from __future__ import annotations

import json
import os

import requests

BASE = os.environ.get("AFA_BASE_URL", "https://desktop-ai2ata5-1.tailfeb765.ts.net").rstrip("/")
NETWORK = os.environ.get("AFA_NETWORK", "eip155:8453")
TIMEOUT = int(os.environ.get("AFA_TIMEOUT", "30"))

# mcp 2.x는 FastMCP를 MCPServer로 이름을 바꿨다. 둘 다 받는다.
# 한쪽만 잡으면 사용자 절반에게 "Connection closed"만 뜨고 이유는 안 보인다(실측으로 당했다).
try:
    from mcp.server.mcpserver import MCPServer as _Server  # mcp >= 2.0
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP as _Server  # mcp 1.x
    except ImportError as e:  # 조용히 죽지 않는다
        raise SystemExit(
            "This MCP server needs the mcp package.\n"
            '  pip install "mcp[cli]" requests\n'
            'For the paid tools also: pip install "x402[mcp]" eth-account'
        ) from e

mcp = _Server(name="agent-failure-archive")

_PAID_SESSION = None
_PAID_ERROR: str | None = None


def _paid_session():
    """사용자 지갑으로 402를 자동 결제하는 세션. 키가 없으면 None + 이유를 남긴다.

    키는 이 프로세스 밖으로 나가지 않는다. 로그에도 안 찍는다(찍으면 그게 사고다).
    """
    global _PAID_SESSION, _PAID_ERROR
    if _PAID_SESSION is not None or _PAID_ERROR is not None:
        return _PAID_SESSION
    key = os.environ.get("X402_PRIVATE_KEY", "").strip()
    if not key:
        _PAID_ERROR = (
            "X402_PRIVATE_KEY is not set, so paid tools cannot pay. Set it in this MCP server's "
            "env to a wallet holding USDC on Base. The key stays on your machine; it is used "
            "locally to sign and is never sent anywhere except as an x402 payment signature."
        )
        return None
    try:
        from eth_account import Account
        from x402 import SchemeRegistration, x402ClientConfig
        from x402.http.clients.requests import wrapRequestsWithPaymentFromConfig
        from x402.mechanisms.evm.exact import ExactEvmScheme
        from x402.mechanisms.evm.signers import EthAccountSigner

        signer = EthAccountSigner(Account.from_key(key))
        config = x402ClientConfig(
            schemes=[SchemeRegistration(network=NETWORK, client=ExactEvmScheme(signer=signer))]
        )
        _PAID_SESSION = wrapRequestsWithPaymentFromConfig(requests.Session(), config)
        return _PAID_SESSION
    except Exception as e:
        # 무엇이 없어서 못 하는지 정확히 말한다. "실패했습니다"는 고칠 수가 없다.
        _PAID_ERROR = f"could not build a paying session: {type(e).__name__}: {e}"
        return None


def _free_get(path: str, **params) -> str:
    try:
        r = requests.get(f"{BASE}{path}", params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return json.dumps(r.json(), ensure_ascii=False, indent=1)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}", "route": path}, ensure_ascii=False)


def _paid_get(path: str, **params) -> str:
    s = _paid_session()
    if s is None:
        # 결제를 못 하더라도 무엇을 사는 건지는 보여준다. 402 명세가 곧 가격표다.
        try:
            r = requests.get(f"{BASE}{path}", params=params, timeout=TIMEOUT)
            challenge = r.headers.get("payment-required", "")
        except Exception:
            challenge = ""
        return json.dumps(
            {
                "paid": False,
                "reason": _PAID_ERROR,
                "route": f"{BASE}{path}",
                "how_to_pay": "any x402 client, or set X402_PRIVATE_KEY here and call again",
                "payment_challenge_b64": challenge[:400],
                "free_alternative": "/precheck and /sample need no wallet at all",
            },
            ensure_ascii=False,
            indent=1,
        )
    try:
        r = s.get(f"{BASE}{path}", params=params, timeout=TIMEOUT)
        r.raise_for_status()
        out = r.json()
        if isinstance(out, dict):
            out["_settled"] = bool(r.headers.get("payment-response"))
        return json.dumps(out, ensure_ascii=False, indent=1)
    except Exception as e:
        return json.dumps({"paid": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


# ── 무료: 지갑 없이 그냥 된다 ────────────────────────────────────────
@mcp.tool()
def precheck(claim: str, evidence: str = "") -> str:
    """Free. Check a conclusion against nine known ways of fooling yourself.

    Returns which checks your claim trips and the question each one asks. Costs nothing and
    needs no wallet. Use this before writing "we found that". The paid `audit` tool adds why
    each check matters, the real incident behind it with measured numbers, and what to run.
    """
    return _free_get("/precheck", claim=claim, evidence=evidence)


@mcp.tool()
def sample() -> str:
    """Free. Two complete post-mortems from the archive, no payment needed."""
    return _free_get("/sample")


@mcp.tool()
def service_info() -> str:
    """Free. What this archive contains, what each paid tool costs, and how payment works."""
    return _free_get("/")


# ── 유료: 사용자 지갑으로 낸다 ───────────────────────────────────────
@mcp.tool()
def audit(claim: str, evidence: str = "") -> str:
    """$0.02. Full audit of a claim: why each tripped check matters, the incident behind it
    with the numbers measured at the time, and the specific thing to run to settle it.
    Deterministic, so the same input always returns the same audit."""
    return _paid_get("/audit", claim=claim, evidence=evidence)


@mcp.tool()
def search(q: str) -> str:
    """$0.01. Three real agent post-mortems matching a symptom, each with root cause, the fix
    that worked, and the prevention rule it produced. Try symptom words: "cron silently
    stopped", "hook dies without error", "process exits 0 but did nothing"."""
    return _paid_get("/search", q=q)


@mcp.tool()
def brief(action: str) -> str:
    """$0.05. Pre-flight risk brief before an irreversible action. Describe the action in plain
    words; get back the ways that class of action has actually failed, plus a checklist."""
    return _paid_get("/brief", action=action)


@mcp.tool()
def research(q: str = "") -> str:
    """$0.25. The measurement failures: 107 cases from an eight-month attempt to measure one
    person's individuation with embeddings. Silent detectors, unequal treatment arms, absent
    positive controls, instruments that kept measuring themselves."""
    return _paid_get("/research", q=q)


@mcp.tool()
def archive() -> str:
    """$1.00. Every case in one response. One payment, no subscription, no account."""
    return _paid_get("/archive")


if __name__ == "__main__":
    mcp.run()
