본 MCP는 개인정보를 저장하지 않고,
사용자 상태를 정규화하여
복지 서비스의 신청 가능성과 준비 서류를
사전 안내하는 Stateless 지원 도우미입니다.

# Welfare Services MCP

> Stateless MCP Server for Welfare Eligibility Pre-Check and Required Documents Guidance

---

## 📌 프로젝트 개요

**Welfare Services MCP**는  
개인정보를 저장하지 않고, 최소한의 사용자 상태 정보를 정규화하여  
복지 서비스의 **신청 가능성**과 **준비 서류**를 사전에 안내하는  
**Stateless MCP Server**입니다.

본 MCP는 PlayMCP 플랫폼에서 요구하는 MCP 철학을 충실히 따릅니다.

- **Resource**: 공공 복지 서비스 원문 데이터 제공
- **Tool**: 사용자 상태 수집 및 해석
- **Agent**: 상태 오케스트레이션 및 사용자 설명

---

## 🎯 설계 목표

- 개인정보 최소 수집 (금액, 주민번호 등 ❌)
- 최소 질문 수 기반 사용자 상태 정규화
- 공공데이터(OpenAPI) 원문 훼손 없이 해석만 수행
- "확정"이 아닌 **사전 자가진단** 서비스 제공

---

## 🧠 MCP 아키텍처

- MCP Server는 **UserProfile을 저장하지 않습니다**
- 모든 상태는 **Agent가 JSON으로 전달**합니다

---

## 🧩 도메인 모델

### UserProfile (정규화된 사용자 상태)

```json
{
  "age_group": "YOUTH",
  "income_level": "MEDIAN_50_100",
  "employment_status": "UNEMPLOYED",
  "household_type": "SINGLE",
  "special_status": [],
  "assets": {
    "has_real_estate": false,
    "has_vehicle": false
  }
}
```

## 🔍 검색 전략

`check_eligibility` 툴은 세 가지 점수를 합산하여 복지 서비스를 추천합니다.

| 전략              | 설명                                                          | 가중치        |
| ----------------- | ------------------------------------------------------------- | ------------- |
| **Vector Score**  | `jhgan/ko-sroberta-multitask` 임베딩 코사인 유사도 (pgvector) | 기본 점수     |
| **Intent Bonus**  | 취업·주거·창업 등 의도 키워드 일치 여부                       | +0.5          |
| **Profile Bonus** | 지역 / 성별 / 가구 형태 / 소득 기준 프로필 매칭               | +0.05 ~ +0.20 |

최종적으로 상위 5개 서비스를 반환합니다.

---

## 🛠 MCP Tools

### `check_eligibility`

사용자의 나이, 성별, 지역, 가구 형태, 소득 수준과 자연어 질문을 기반으로 복지 서비스를 추천합니다.

**응답 예시**

```json
{
  "count": 5,
  "search_strategy": "Semantic + Intent + Profile",
  "recommended_services": [
    {
      "service_id": "WS12345",
      "name": "청년 취업 지원 사업",
      "purpose": "미취업 청년의 취업 역량 강화",
      "url": "https://www.bokjiro.go.kr/...",
      "score_breakdown": {
        "vector": 0.82,
        "intent": 0.5,
        "profile": 0.35,
        "total": 1.67
      }
    }
  ]
}
```

---

### `required_documents`

선택한 서비스 ID의 구비서류 목록을 조회하고, 사용자 프로필에 따른 조건부 서류를 안내합니다.

**응답 예시**

```json
{
  "service_name": "청년 취업 지원 사업",
  "required_now": ["신분증 사본", "주민등록등본"],
  "verified_by_officer": ["건강보험료 납부확인서"],
  "conditional_by_profile": ["고용보험 미가입 확인서"],
  "apply_url": "https://www.bokjiro.go.kr/...",
  "status": "success"
}
```

## ⚙️ 데이터 파이프라인

```
공공 OpenAPI
    │
    ▼
[batch] 페이지 단위 수집 + 벡터 임베딩 생성
    │  Advisory Lock + Checkpoint 기반 안정적 실행
    ▼
welfare_service (PostgreSQL + pgvector)
    │
    ▼
[normalize] Claude Batch API (Haiku) 로 지원 대상·선정 기준 정규화
    │  10,000건 청크 단위 비동기 처리
    ▼
welfare_target / welfare_criteria
    │
    ▼
[welfare_mcp] MCP Tool로 검색 · 서류 안내
```

### 배치 특징

- **Advisory Lock**: 동시에 여러 프로세스가 실행되지 않도록 PostgreSQL Advisory Lock 사용
- **Checkpoint**: 페이지 단위로 진행 상태를 저장, 실패 시 이어서 재실행 가능
- **Upsert**: `ON CONFLICT DO UPDATE` 로 중복 수집 없이 최신 데이터 유지

### 정규화 특징

- **Claude Message Batches API** 사용 → 대량 처리 비용 절감
- **Prompt Caching** (`cache_control: ephemeral`) → 시스템 프롬프트 반복 전송 비용 최소화
- **SAVEPOINT** 기반 개별 레코드 실패 격리 → 일부 실패 시 전체 트랜잭션 유지

---

## 🔧 기술 스택

| 분류          | 기술                                                                            |
| ------------- | ------------------------------------------------------------------------------- |
| MCP Framework | [FastMCP](https://github.com/modelcontextprotocol/python-sdk) (Streamable HTTP) |
| Web Framework | Starlette + Uvicorn                                                             |
| DB            | PostgreSQL + [pgvector](https://github.com/pgvector/pgvector)                   |
| DB 드라이버   | psycopg3 (비동기, psycopg_pool)                                                 |
| 임베딩 모델   | `jhgan/ko-sroberta-multitask` (sentence-transformers)                           |
| AI            | Anthropic Claude Haiku (Message Batches API)                                    |
| 언어          | Python 3.13                                                                     |
| 컨테이너      | Docker                                                                          |
| CI/CD         | GitHub Actions → Docker Hub → AWS LightSail                                     |

## ⚠️ 면책 조항

본 서비스는 복지 서비스 신청 가능성을 **사전 자가진단**하는 용도로만 제공됩니다.  
실제 수급 자격은 담당 기관의 공식 심사를 통해 결정되며, 본 MCP의 결과는 법적 효력을 갖지 않습니다.
