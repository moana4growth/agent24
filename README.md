# MoodBreak

**브랜드/기획을 입력하면, 전형적인 AI 디자인 틀을 의도적으로 깨는 디자인 시스템(DESIGN.md + tokens)과 화면을 만들어주는 agentic design consultant.**

핵심 논리: *깨야 할 전형성(미적 클리셰)과 지켜야 할 관습(도메인 필수 요소)을 구분하는, 통제된 이탈.*

## 실행

```bash
pip install -r requirements.txt
cp .env.example .env   # OPENAI_API_KEY 입력
python main.py
```

- 메인 UI: http://127.0.0.1:8787/
- **세컨드 화면 (raw tool_call/tool_result 스트림): http://127.0.0.1:8787/stream** ← 결선 요구사항. 별도 창으로 열어 프로젝터에.

## 아키텍처

```
Orchestrator (판단 주체 — 고정 파이프라인 없음)
│  도구: submit_plan, ask_user, present_mood_cards, domain_checklist, artifacts…
│
├─ Researcher      웹 검색 → KEEP(지킬 관습) / BREAK(깰 클리셰) 이원 분석
├─ ArtDirector     무드 방향 2~3개 생성+추천 → 선택 후 전문가 지시서 작성
├─ LayoutArchitect ┐
├─ ColorConcept    ├ 병렬 실행 (배치/색·컨셉/워딩 3축)
├─ VoiceTone       ┘
├─ Synthesizer     tokens.json + DESIGN.md + 변주 시안 2개 + 핵심 화면 (파일 직접 저장)
└─ Critic          scan_cliches(정적 블랙리스트) + WCAG 대비 + KEEP 요소 검사
                   → fail 시 실패 축만 재호출 (최대 2루프)
```

### 왜 워크플로우가 아니라 에이전트인가

1. **동적 계획**: 오케스트레이터는 매 입력마다 `submit_plan`으로 실행 계획을 스스로 수립·정당화. 레퍼런스 이미지가 있으면 리서치 축소, 낯선 도메인이면 심화 — 입력이 다르면 도구 호출 그래프가 달라진다 (raw 스트림에서 확인 가능).
2. **도구 과잉 공급**: 필요한 것보다 많은 도구를 주고 선택하게 함. 안 쓴 도구가 판단의 증거.
3. **질문도 판단**: `ask_user`는 고정 단계가 아니라 신호 상충 시에만 에이전트가 선택하는 도구.
4. **Critic 재량**: 심각도 판정 → 재시도/수용/투명 공개 중 오케스트레이터가 선택. 반복 횟수가 실행마다 다름.
5. **피드백 턴**: 전체 재실행이 아니라 영향 분석 → 해당 축만 재호출.

## 데모 스크립트 (7분)

1. 슬라이드 (≤1.5분): 문제 — "AI로 만들면 다 똑같이 생겼다. 프롬프트로 취향을 설명해보라, 실패한다."
2. 라이브: 브리프 입력 → **계획이 화면에 뜨는 순간** 세컨드 화면 가리키기
3. ArtDirector의 방향 결정 근거 낭독 (30초)
4. 무드 카드 선택 (에이전트 추천 표시 강조) → 전문가 3축 병렬 tool_call이 스트림에 찍히는 것 보여주기
5. Critic 반려→재호출이 찍히면 반드시 언급: "방금 스스로 반려하고 layout만 다시 시켰습니다"
6. 즉석 태스크 수신 → 피드백 턴: 영향 분석("유지 X, 변경 Y") 후 최소 재호출
7. (시간 되면) DESIGN.md를 Claude Code/Cursor에 붙여 새 화면 생성 — "이 md 하나로 앞으로의 모든 AI 작업물이 이 브랜드로 나옵니다"

## 심사 Q&A 예상 답변

- **"고정 워크플로우랑 뭐가 다르죠?"** → "워크플로우면 어떤 입력이든 호출 그래프가 같아야 합니다. 첫 실행과 방금 즉석 태스크의 그래프를 비교해보세요. 계획, 호출된 전문가, Critic 반복 횟수가 다르고, 이 경로는 에이전트가 그 자리에서 결정한 겁니다."
- **"전형성을 어떻게 보장하죠?"** → scan_cliches 블랙리스트(코드) + LLM 비평 이중 구조. 정적 검사라 재현 가능.
- **"리서치 데이터 근거는요?"** → 수치 생성 금지 프롬프트, 정성 패턴 + 검색 인용만. (Researcher instructions 참조)
- **"참신성이 있나요? Stitch/Uizard랑?"** → 그들은 스타일을 말로 설명하게 함. 우리는 취향의 언어화 실패를 전제로, 에이전트가 방향을 제안·추천하고 사용자는 반응만 한다. 그리고 산출물이 목업이 아니라 이식 가능한 DESIGN.md다.

## 리허설 체크리스트

- [ ] 서로 다른 브리프 2개 실행 → tool 그래프가 실제로 달라지는지 raw 스트림 캡처
- [ ] 피드백 턴 소요시간 측정 (40초 넘으면 MODEL_MAIN을 gpt-5-mini로)
- [ ] 이미지 첨부 경로 1회 테스트 (데모 필수 경로엔 넣지 말 것 — 보험)
- [ ] Wi-Fi 불안정 대비: 로컬 서버라 인터넷은 OpenAI API + web search에만 필요
- [ ] `generated/` 폴더 비우고 시작

## 구조

```
moodbreak/
├── main.py          FastAPI + WS 2본 (앱/뷰어), 세션·피드백 턴 관리
├── agents_def.py    에이전트 7개 정의 (프롬프트 = Prompt Quality 심사 대상)
├── tools.py         function tools: 계획/상호작용/산출물/검증(WCAG·클리셰·도메인 체크리스트)
├── static/
│   ├── index.html   메인 UI
│   └── stream.html  raw 이벤트 뷰어 (세컨드 화면)
└── generated/<sid>/ 세션별 산출물 (tokens.json, DESIGN.md, *.html)
```
