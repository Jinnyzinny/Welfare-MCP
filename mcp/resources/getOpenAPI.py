import os
from typing import Optional, Dict, Any
from fastapi_mcp import FastApiMCP

ServiceKey=os.getenv("WELFARE_API_KEY")# 여기도 수정해야함

mcp = FastApiMCP(
    name="Welfare Services MCP",
    description="MCP for accessing welfare services information"
)

@mcp.tool(
    name="search_welfare_services",
    description="복지 서비스 목록을 검색합니다."
)
async def search_welfare_services(
    query: str,
) -> Dict[str, Any]:
    """복지 서비스 목록을 검색합니다."""

    return await fetch_gov24(
        url="https://api.odcloud.kr/gov24/v3/serviceList",
        params={
            "page": page,
            "perPage": 10,
            "returnType": "JSON",
            "cond[서비스명::LIKE]": query,
            "serviceKey": ServiceKey
        }
    )


@mcp.tool()
async def get_welfare_serviceDetail(
    region: Optional[str] = None
) -> Dict[str, Any]:
    return await fetch_gov24(
        url="https://api.odcloud.kr/gov24/v3/serviceDetail",
        params={
            "page": 1,
            "perPage": 1,
            "returnType": "JSON",
            "cond[서비스ID::EQ]": service_id,
            "serviceKey": ServiceKey
        }
    )


@mcp.tool()
async def get_welfare_supportConditions(
    service_id: str
) -> Dict[str, Any]:
    """특정 복지 서비스의 지원 조건을 조회합니다."""
    
    raw = await fetch_gov24(
        url="https://api.odcloud.kr/gov24/v3/serviceDetail",
        params={
            "page": 1,
            "perPage": 1,
            "returnType": "JSON",
            "cond[서비스ID::EQ]": service_id,
            "serviceKey": ServiceKey
        }
    )

    # AI가 쓰기 쉽게 가공
    item = raw.get("data", [{}])[0]
    return {
        "service_id": service_id,
        "support_conditions": item.get("지원조건"),
        "apply_method": item.get("신청방법")
    }