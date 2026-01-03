async def fetch_gov24(
    url: str,
    params: Dict[str, Any]
) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()