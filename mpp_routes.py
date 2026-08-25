"""같은 아카이브를 두 번째 결제 규격(MPP)으로도 판다. 경로만 다르고 물건은 같다.

왜 두 번째 규격인가 (실측 근거):
    x402 쪽은 기계가 읽는 발견 통로가 **전부 거래를 전제로** 한다(x402scan 검색은 사용 실적,
    payai 디렉토리는 결제 시도 관측, CDP Bazaar는 유료콜). 즉 신규 판매자는 아무도 안 산 상태에서
    자동 발견면에 안 나온다. MPP(Machine Payments Protocol)는 IETF 초안이 있는 별도 규격이고,
    **공식 디렉토리가 PR로 등재를 받는다**(tempoxyz/mpp `schemas/services.ts`).
    즉 실적 없이도 들어갈 수 있는 문이다. 그 문 하나 때문에 붙인다.

경계:
    서버가 요구하는 MPP_SECRET_KEY는 **지갑 개인키가 아니라 우리 챌린지 ID를 서명하는 HMAC 비밀**이다
    (mpp/server/mpp.py: "Server secret for HMAC-bound challenge IDs"). 세션 시크릿과 같은 급이라
    우리가 만들어 서버 환경에만 둔다. 레포·로그·문서 어디에도 안 들어간다.

정산 위치가 다르다 (중요):
    MPP 기본 경로는 Tempo 체인(chain_id 4217)이다. x402는 Base다. **같은 주소로 오지만 체인이 다르다.**
    그래서 계기(dollar_oracle)가 Base만 보면 MPP 수입을 못 본다 = 조용한 검출기.
    이 파일을 붙이면 계기도 같이 넓혀야 한다.

x402 라우트와 안 겹치게 `/mpp/...` 아래에만 붙인다. x402 미들웨어는 자기 경로만 게이트하므로
서로 간섭하지 않는다. 한쪽이 깨져도 다른 쪽은 계속 판다.
"""
# ⚠️ `from __future__ import annotations` 를 쓰지 않는다. 그걸 켜면 타입 표기가 문자열이 되고,
# FastAPI가 그 문자열을 **모듈 전역**에서 찾는다. Request를 함수 안에서 import 하면 전역에 없으니
# 못 찾고 결국 `request`를 쿼리 파라미터로 취급해 422를 뱉는다(실측: loc=["query","request"]).
# 그래서 Request는 모듈 최상단에서 import 하고 future 표기는 끈다.
import json
import os

from fastapi import Request

PAY_TO = os.environ.get("X402_PAY_TO", "0xFC15354FE6a96d87399582dbe9DF8d2739B1fF9a").strip()

PRICES = {"search": "0.01", "audit": "0.02", "brief": "0.05", "research": "0.25", "archive": "1.00"}


def attach(app, logic) -> list[str]:
    """앱에 /mpp/* 유료 라우트를 붙이고 붙은 경로 목록을 돌려준다.

    실패하면 예외를 올린다. 부르는 쪽이 try/except로 감싸서 **본체는 계속 팔게** 한다.
    """
    from mpp.methods.tempo import ChargeIntent, tempo
    from mpp.server import Mpp

    # realm을 안 주면 요청 Host에서 뽑는데, 로컬 테스트에선 "localhost"가 박혀 나온다(실측).
    # 결제 챌린지에 우리 공개 이름이 찍혀야 사는 쪽이 무엇에 내는지 안다.
    realm = os.environ.get("MPP_REALM", "desktop-ai2ata5-1.tailfeb765.ts.net")
    server = Mpp.create(method=tempo(intents={"charge": ChargeIntent()}, recipient=PAY_TO), realm=realm)

    def _mount(name: str, price: str, desc: str, fn):
        # ⚠️ server.pay 가 돌려주는 래퍼는 (request) 하나만 받는 형태라, FastAPI 데코레이터에 바로
        # 얹으면 FastAPI가 `request`를 **쿼리 파라미터로 착각**해서 422를 뱉는다(실측).
        # 그래서 래퍼를 먼저 만들고, FastAPI에는 시그니처가 깨끗한 얇은 함수를 등록한다.
        async def _inner(request, credential, receipt, _fn=fn):
            return _fn(request)

        wrapped = server.pay(amount=price, description=desc)(_inner)

        async def _endpoint(request: Request, _w=wrapped):
            return await _w(request)

        app.add_api_route(f"/mpp/{name}", _endpoint, methods=["GET"], name=f"mpp_{name}")
        return f"/mpp/{name}"

    def _search(request):
        q = request.query_params.get("q", "")
        return {"query": q, "cases": [logic._card(r) for r in logic.find(q, 3)]}

    def _audit(request):
        claim = request.query_params.get("claim", "")
        ev = request.query_params.get("evidence", "")
        text = request.query_params.get("text", "")
        if text.strip():
            found = logic._claims(text)
            return {"mode": "text", "claims_found": len(found),
                    "claims": [{"claim": c, **logic._audit(c, text, trigger_on=c)} for c in found]}
        r = logic._audit(claim, ev)
        return {"claim": claim, "unaddressed": r["unaddressed"], "checks": r["triggered"]}

    def _brief(request):
        action = request.query_params.get("action", "")
        hits = logic.find(action, 5)
        return {"action": action, "risk_cases": [logic._card(r) for r in hits],
                "checklist": [c for c in (r["prevention"] for r in hits) if c][:5]}

    def _research(request):
        q = request.query_params.get("q", "")
        pool = logic._research_rows()
        hits = [h for h in logic.find(q, 5) if h in pool] if q else []
        return {"query": q, "pool_size": len(pool),
                "cases": [logic._card(r) for r in (hits or pool[:5])]}

    def _archive(_request):
        rs = logic.rows()
        return {"count": len(rs), "cases": [logic._card(r) for r in rs]}

    mounted = [
        _mount("search", PRICES["search"],
               "Three real agent post-mortems matching a symptom, with root cause and the fix.", _search),
        _mount("audit", PRICES["audit"],
               "Audit a claim against nine documented ways of fooling yourself.", _audit),
        _mount("brief", PRICES["brief"],
               "Pre-flight risk brief before an irreversible action.", _brief),
        _mount("research", PRICES["research"],
               "107 measurement failures from trying to measure one person with embeddings.", _research),
        _mount("archive", PRICES["archive"],
               "Every one of the 186 cases in a single response.", _archive),
    ]
    return mounted


def catalog() -> str:
    return json.dumps({"protocol": "mpp", "paths": {f"/mpp/{k}": f"${v}" for k, v in PRICES.items()}})
