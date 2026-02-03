import re


# target 대상을 파싱하는 유틸리티 함수
def parse_target_info(text):
    """지원 대상 텍스트에서 나이와 성별을 추출하는 유틸리티"""
    min_a, max_a = 0, 200  # 기본값: 0세 ~ 200세
    gender = "A"  # 기본값: 공통(All)

    if not text:
        return min_a, max_a, gender

    # 1. 나이 범위 추출 (예: 19~29세, 19-29세)
    range_match = re.search(r"(\d+)\s*[~-]\s*(\d+)", text)
    if range_match:
        min_a, max_a = int(range_match.group(1)), int(range_match.group(2))
    else:
        # 단일 숫자 추출 (예: 19세 이상, 34세 이하)
        nums = re.findall(r"\d+", text)
        if nums:
            val = int(nums[0])
            if "이상" in text:
                min_a = val
            elif "이하" in text:
                max_a = val

    # 2. 성별 추출
    if "여성" in text:
        gender = "F"
    elif "남성" in text:
        gender = "M"

    return min_a, max_a, gender
