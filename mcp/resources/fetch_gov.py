from typing import Any, Dict
import httpx

# 공통으로 사용할 비동기 HTTP 클라이언트 함수

async def fetch_gov24(
    url: str,
    params: Dict[str, Any]
) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()