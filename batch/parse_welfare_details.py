import re

def parse_welfare_details(item_data):
    """나이, 성별, 소득, 자산, 지역, 가구형태를 모두 추출"""
    text = item_data.get("support_target", "") + " " + item_data.get("apply_org_name", "")
    
    # 1. 지역 정보 추출 (시/도, 시/군/구)
    # 예: "서울특별시 강남구" -> sido: 서울특별시, sigungu: 강남구
    region_match = re.search(r'([가-힣]+(?:세종|특별|광역|도|시))\s+([가-힣]+(?:구|시|군))?', text)
    sido = region_match.group(1) if region_match else "전국"
    sigungu = region_match.group(2) if region_match and region_match.group(2) else "전체"

    # 2. 가구 형태 (Household Type)
    # 예: 다자녀, 1인가구, 저소득층 등
    household_type = "일반"
    if "다자녀" in text: household_type = "다자녀"
    elif "1인" in text or "독거" in text: household_type = "1인가구"
    elif "한부모" in text: household_type = "한부모"

    # 3. 소득/자산 숫자 추출 (단위: 만원 가정)
    # 정교한 추출을 위해선 더 복잡한 Regex가 필요하나, 우선 기본형으로 구현
    income_nums = re.findall(r'(\d+)%\s*이하', text) # 중위소득 % 기준
    min_income = 0
    max_income = int(income_nums[0]) if income_nums else 999 # % 기준 저장

    return sido, sigungu, household_type, min_income,max_income   