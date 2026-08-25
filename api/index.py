"""Vercel 서버리스 진입점. 실제 앱은 server.py에 있고 여기선 경로만 이어준다.

Vercel Python 런타임은 이 파일의 `app`(ASGI)을 찾아 띄운다. server.py를 그대로
쓰기 위해 리포지토리 루트를 sys.path에 넣는다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import app  # noqa: E402

__all__ = ["app"]
