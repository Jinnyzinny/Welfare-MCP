import os
from tenacity import retry, stop_after_attempt, wait_fixed
import requests

# 1. 환경 변수 및 설정
API_KEY = os.environ["WELFARE_API_KEY"]

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_page(page: int):
    # 이미지 명세서에 따른 정확한 Endpoint 및 파라미터 설정
    url = "https://api.odcloud.kr/api/gov24/v3/serviceDetail"
    params = {
        "serviceKey": API_KEY,
        "page": page,          # 이미지 확인 결과: pageNo 아님
        "perPage": 100,        # 페이지당 데이터 수
        "returnType": "JSON"   # 이미지 확인 결과: resultType 아님
    }
    res = requests.get(url, params=params, timeout=20)
    res.raise_for_status()
    return res.json()