import re

# 1. 전처리를 담당하는 순수 함수 정의 (반복문 밖)
def clean_text(text):
    if not text: return ""
    # 특수문자 제거
    text = re.sub(r'[^a-zA-Z0-9가-힣\s]', '', text)
    # 중복 공백 제거 및 양끝 공백 정리
    return re.sub(r'\s+', ' ', text).strip()