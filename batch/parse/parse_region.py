
def parse_region(provider_name):
    if not provider_name:
        return "central", "central"

    parts = provider_name.split()
    
    # 최소 "경상북도 영주시" 처럼 두 단어 이상이어야 함
    if len(parts) >= 2:
        sido = parts[0]
        sigungu = parts[1]
        
        # 행정구역을 나타내는 대표적인 접미사들
        # '시'의 경우 '특별시', '광역시' 등을 포함하기 위해 처리
        region_suffixes = ('도', '시', '군', '구', '특별시', '광역시', '자치시', '자치도')
        
        # 첫 번째 단어(sido)가 행정구역 키워드로 끝나는지 확인
        if any(sido.endswith(s) for s in region_suffixes):
            return sido, sigungu
            
    # 위 조건(행정구역 명칭)에 부합하지 않으면 모두 central로 리턴
    return "central", "central"