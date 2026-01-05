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
- 서버 상태 저장 ❌ (Stateless)
- 최소 질문 수 기반 사용자 상태 정규화
- 공공데이터(OpenAPI) 원문 훼손 없이 해석만 수행
- “확정”이 아닌 **사전 자가진단** 서비스 제공

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
