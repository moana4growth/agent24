# MoodBreak — 코딩 에이전트용 컨텍스트

브랜드/기획 입력 → 전형적 AI 디자인을 의도적으로 깨는 디자인 시스템(DESIGN.md + tokens.json) + HTML 화면을 생성하는 해커톤 프로젝트. OpenAI Agents SDK 기반.

## 실행
```bash
pip install -r requirements.txt
cp .env.example .env   # OPENAI_API_KEY 필수
python main.py
# 메인 UI: http://127.0.0.1:8787/  |  raw 스트림(세컨드 화면): /stream
```

## 구조 (파일 3개가 전부)
- `main.py` — FastAPI + WS 2본. `/ws/app`(클라이언트), `/ws/viewer`(raw tool_call/tool_result 무가공 스트림 — **대회 필수 요건, 가공 금지**). 세션별 히스토리로 피드백 턴 지원.
- `agents_def.py` — 에이전트 7개. Orchestrator가 판단 주체, 나머지는 agent-as-tool. **프롬프트가 심사 대상(Prompt Quality 20%)이므로 수정 시 명확성 유지.**
- `tools.py` — function tools. `SessionCtx.wait_user()`가 미드런 사용자 입력(무드 선택/질문)의 핵심 메커니즘.

## 수정 시 불변 조건 (심사 기준과 직결)
1. Orchestrator에 고정 순서 하드코딩 금지 — 동적 계획(submit_plan)이 "워크플로우가 아닌 에이전트"의 근거.
2. raw 스트림 이벤트는 가공/요약 금지 (`to_jsonable`은 직렬화만).
3. Critic 실패 시 전체 재실행 금지 — 실패 축만 재호출.
4. 산출물은 반드시 `save_artifact` 도구 호출로 (자유 텍스트 파싱 금지).
5. 피드백 턴은 영향 분석 → 최소 재호출 (전문가 1~2개만).

## 모델
`.env`의 MODEL_MAIN(기본 gpt-5.1) / MODEL_FAST(기본 gpt-5-mini). 데모 지연 시 둘 다 mini로.
