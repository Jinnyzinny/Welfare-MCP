# Welfare Services MCP

> 개인정보를 저장하지 않고, 복지 서비스 신청 가능성과 준비 서류를 사전 안내하는 Stateless MCP Server

# 사용 방법

1. Claude의 입력 창 아래 + 버튼을 누른다면 커넥터 버튼이 나옵니다.
   <img width="732" height="409" alt="image" src="https://github.com/user-attachments/assets/ccf9487a-9b68-4ea3-890d-1540a0860243" />
2. Connector 버튼을 확인했다면 커넥터 관리 버튼을 클릭해주세요.
   <img width="763" height="419" alt="image" src="https://github.com/user-attachments/assets/c13964ea-bd7b-4ae0-ba6f-6bf3190e26ef" />
3. 커넥터 관리 버튼을 클릭했다면 오른쪽 상단의 + 버튼을 클릭한 뒤, Custom Connector(커스텀 커넥터) 추가 버튼을 클릭하세요.
   <img width="275" height="112" alt="image" src="https://github.com/user-attachments/assets/110d8d9d-4828-4743-a596-a8b38dd88099" />
4. 이름 : 대한민국 복지 MCP 서버 <br>
   원격 MCP 서버 URL : https://welfare-mcpserver.shop 을 입력해주세요.
   <img width="530" height="433" alt="image" src="https://github.com/user-attachments/assets/6bcf81ae-f2b5-4945-a5d5-22427167b5bc" />
5. 내게 맞는 서비스를 검색하세요.

---

## 프로젝트 개요

**Welfare Services MCP**는 공공 복지 서비스에 대한 **사전 자가진단** 도구입니다.

사용자의 나이·지역·소득·가구 형태 등 최소한의 상태 정보를 정규화하여 신청 가능성이 높은 복지 서비스를 추천하고, 필요한 구비서류를 사전에 안내합니다. 개인정보(주민번호, 금융정보 등)를 저장하지 않으며, 모든 상태는 Agent가 세션 내에서 JSON으로 관리합니다.

[PlayMCP](https://playmcp.io) 플랫폼 규격의 MCP 철학을 따릅니다.

---

## 주요 특징

- **무상태(Stateless)**: 개인정보 비저장, 세션 없음 — 확장성과 프라이버시 보장
- **의미 기반 검색**: 한국어 임베딩(pgvector)으로 자연어 질의와 복지 서비스 매칭
- **LLM 정규화**: Claude Batch API(Haiku)로 비정형 지원 대상·선정 기준 텍스트를 구조화
- **사전 진단**: 최종 수급 결정이 아닌 신청 가능성 사전 안내 목적

---

## 시스템 아키텍처

```
공공 OpenAPI (gov24)
        │
        ▼
  ┌─────────────┐
  │    batch    │  페이지 수집 + 벡터 임베딩 생성
  │             │  Advisory Lock + Checkpoint 기반 안정 실행
  └──────┬──────┘
         │
         ▼
  welfare_service (PostgreSQL + pgvector)
         │
         ▼
  ┌─────────────┐
  │  normalize  │  Claude Batch API (Haiku)로 지원 대상·선정 기준 정규화
  │             │  10,000건 청크 단위 비동기 처리
  └──────┬──────┘
         │
         ▼
  welfare_target / welfare_criteria
         │
         ▼
  ┌─────────────┐
  │ welfare_mcp │  MCP Tool (check_eligibility, required_documents)
  │  FastMCP    │  Streamable HTTP / Stateless
  └─────────────┘
```

### 모듈 구성

| 모듈           | 역할                                            |
| -------------- | ----------------------------------------------- |
| `batch/`       | 공공 OpenAPI 데이터 수집 및 벡터 임베딩 생성    |
| `normalize/`   | Claude Batch API로 복지 서비스 자격 기준 구조화 |
| `welfare_mcp/` | MCP 도구 및 프롬프트 제공 (FastMCP 서버)        |
| `db/`          | PostgreSQL 마이그레이션 스크립트                |

---

## MCP Tools

### `check_eligibility`

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

## 데이터 파이프라인

### Batch 수집

공공 OpenAPI(`gov24/v3/serviceDetail`)에서 복지 서비스 데이터를 수집하고 벡터 임베딩을 생성합니다.

- **Advisory Lock**: PostgreSQL Advisory Lock으로 동시 실행 방지
- **Checkpoint**: 페이지 단위로 진행 상태를 저장, 실패 시 이어서 재실행
- **Upsert**: `ON CONFLICT DO UPDATE`로 중복 없이 최신 데이터 유지
- **임베딩 모델**: `jhgan/ko-sroberta-multitask` (768차원, 한국어 특화)

### Normalize 정규화

비정형 텍스트(지원 대상, 선정 기준)를 Claude Batch API로 구조화된 JSON으로 변환합니다.

- **모델**: `claude-haiku-4-5-20251001` (비용 최적화)
- **Prompt Caching**: 시스템 프롬프트 캐싱으로 반복 전송 비용 절감
- **배치 크기**: 건당 10,000건 청크 처리 (API 최대 100,000건)
- **SAVEPOINT**: 개별 레코드 실패 격리 → 일부 오류 시 전체 트랜잭션 유지

## 기술 스택

| 분류          | 기술                                                                            |
| ------------- | ------------------------------------------------------------------------------- |
| MCP Framework | [FastMCP](https://github.com/modelcontextprotocol/python-sdk) (Streamable HTTP) |
| Web Framework | Starlette + Uvicorn                                                             |
| Database      | PostgreSQL + [pgvector](https://github.com/pgvector/pgvector)                   |
| DB 드라이버   | psycopg3 (비동기)                                                               |
| 임베딩 모델   | `jhgan/ko-sroberta-multitask` (sentence-transformers)                           |
| LLM           | Anthropic Claude Haiku (Message Batches API)                                    |
| 언어          | Python 3.13                                                                     |
| 컨테이너      | Docker                                                                          |
| CI/CD         | GitHub Actions → Docker Hub → Railway                                           |

---

## 면책 조항

본 서비스는 복지 서비스 신청 가능성을 **사전 자가진단**하는 용도로만 제공됩니다.
실제 수급 자격은 담당 기관의 공식 심사를 통해 결정되며, 본 MCP의 결과는 법적 효력을 갖지 않습니다.

Cloud Instance 이관에 도움을 주신 @chrisryugj 님께 감사드립니다.
