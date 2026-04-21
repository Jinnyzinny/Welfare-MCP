# parse/get_embedding.py
from sentence_transformers import SentenceTransformer

# 모델 로드 (최초 실행 시 약 400MB 모델 파일을 자동으로 다운로드합니다)
# 768 차원을 생성하는 한국어 최적화 모델입니다.
model = SentenceTransformer('jhgan/ko-sroberta-multitask')

def get_embedding(text):
    """
    텍스트를 768차원 벡터로 변환합니다.
    """
    if not text or text.strip() == "":
        return [0.0] * 768
        
    # 모델을 사용하여 임베딩 생성 (numpy array 반환)
    embedding = model.encode(text)
    
    # pgvector에 넣기 위해 리스트 형식으로 변환하여 반환
    return embedding.tolist()