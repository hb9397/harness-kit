# prompts/detection.md
# 역할: 실행 컨텍스트·프로젝트 유형·세팅 모드를 감지하는 규칙

---

## [실행 컨텍스트 감지]

현재 스킬이 어디서 실행되고 있는지 판정한다.

### 감지 순서

1. 현재 플랫폼의 파일 조회 도구로 다음 안정 식별자 조합을 확인한다.

   - `maintainer/skills/harness-plugin-maintainer/SKILL.md`
   - `maintainer/plugin/CAPABILITIES.json`
   - `.agents/plugins/marketplace.json` 또는 `.claude-plugin/marketplace.json`

   날짜가 들어간 계획서 이름이나 특정 shell 명령은 관리 레포 판정 기준으로 쓰지
   않는다. 이전 구조 fallback이 필요하면 `skills/harness-setup/SKILL.md`와
   `.user-docs/Harness_Engineering.md`의 공존 여부만 읽기 전용으로 확인한다.

2. 위 조건이 성립하면 → **하네스 관리 레포 내부**. 사용자에게 대상 프로젝트 루트 경로를 질문한다. 부모 폴더를 자동 적용하지 않는다.

3. 위 조건 불충족 시, 현재 플랫폼의 파일 조회 도구로 현재 위치의 `.ai-docs/`,
   `AGENTS.md` 존재 여부를 함께 확인한다.

4. `.ai-docs/` 또는 `AGENTS.md`가 존재하면 → **하네스 문서가 있는 프로젝트**.
   현재 위치를 프로젝트 루트로 설정한다.

5. 위 모두 불충족 → 사용자에게 프로젝트 루트 경로를 직접 질문.

`.claude/skills/`, `.agents/skills/` 또는 `skills/*/SKILL.md`가 존재하면
legacy/custom local skill 후보로만 기록하고, 실행 컨텍스트 판정의 주 기준으로
쓰지 않는다.

### 하네스 관리 레포에서 실행 시 추가 확인

사용자에게 대상 프로젝트 루트 경로를 확인한다:

> "하네스 관리 레포(`{현재 폴더}`) 안에서 실행 중입니다.
> `.ai-docs`와 루트 컨텍스트를 세팅할 대상 프로젝트 루트 경로를 알려주세요."

---

## [Claude instruction 선점 검사]

프로젝트 루트가 확정되면 파일을 쓰기 전에 해당 경로부터 파일시스템 루트까지 부모
경로를 하나씩 올라가며 다음 파일을 읽기 전용으로 확인한다.

- `{각 경로}/CLAUDE.md`
- `{각 경로}/.claude/CLAUDE.md`
- `{각 경로}/CLAUDE.local.md`

Claude Code의 기본 `claude-md-or-agents-md` 설정에서는 작업 디렉토리 또는 상위 경로에
이 파일 중 하나가 있으면 `AGENTS.md` 대신 Claude instruction이 선택될 수 있다.

| 발견 위치·상태 | 처리 |
|---|---|
| 없음 | `AGENTS.md` 직접 로드 전제 통과 |
| 프로젝트 루트 `CLAUDE.md`가 정확히 하나의 harness 관리 bridge이고 marker 밖에 공백만 있음 | 갱신 모드의 승인형 bridge 제거 후보 |
| 프로젝트 루트 `.claude/CLAUDE.md` 또는 `CLAUDE.local.md` | 자동 변경 없이 중단하고 경로 보고 |
| 프로젝트 루트의 unmanaged·malformed `CLAUDE.md` 또는 관리 블록 밖 사용자 내용 존재 | 자동 변경 없이 중단하고 `AGENTS.md`로 수동 이관할 내용 보고 |
| 프로젝트 상위 경로에서 하나 이상 발견 | 프로젝트 범위 밖이므로 자동 변경 없이 중단하고 정확한 경로 보고 |

프로젝트 루트의 관리 bridge를 제거할 때도 먼저 최신 `AGENTS.md` 관리 블록에 Claude
portable routing·host trust 규칙이 들어 있는지 검증한다. 같은 실행의 승인된 갱신에서만
루트 bridge와 `.ai-docs/root-context/CLAUDE.md` 관리 사본을 제거한다. 어느 파일이든
사용자 내용이 있거나 출처를 확정할 수 없으면 삭제하지 않는다.

Claude Code를 사용할 예정이면 2.1.277 이상인지 확인 가능한 범위에서 버전을 읽고,
구버전이면 `AGENTS.md` 직접 로드를 지원하지 않는다고 보고한 뒤 중단한다. 버전을 확인할
수 없는 다른 host의 호환성을 추정하지 않는다.

---

## [프로젝트 유형 감지]

프로젝트 루트 확정 후, 단일/복수 애플리케이션 여부를 판정한다.

### 감지 기준

**단일 애플리케이션 시그널** — 프로젝트 루트 자체가 앱 루트:
- 루트에 빌드/의존성 매니페스트가 있음: `package.json`, `pom.xml`, `build.gradle`, `go.mod`, `requirements.txt`, `Cargo.toml`, `*.sln`, `*.csproj`, `Gemfile`, `pyproject.toml`, `composer.json`
- 루트에 소스 디렉토리가 있음: `src/`, `app/`, `lib/`, `cmd/`
- 루트에 엔트리포인트가 있음: `main.*`, `index.*`, `App.*`

**복수 애플리케이션 시그널** — 프로젝트 루트 아래에 여러 앱 루트가 존재:
- 하위 디렉토리 각각이 위 매니페스트를 보유
- 하위 디렉토리 각각이 독립 `.git/`을 보유
- 프로젝트 루트 자체에는 매니페스트가 없음 (또는 하네스 레포/`.ai-docs` 등 인프라만 있음)

### 감지 절차

현재 플랫폼의 파일 조회 도구를 사용해 다음을 순서대로 수행한다. Bash, PowerShell
등 특정 shell 문법을 그대로 실행 전제로 두지 않는다.

1. 프로젝트 루트의 매니페스트 후보를 확인한다.
2. 하위 1-depth 디렉토리별 매니페스트와 독립 `.git/` 존재 여부를 확인한다.
3. `.ai-docs/`, `.claude/`, `.agents/`, `node_modules/`, `.git/`, 관리 하네스
   저장소는 앱 후보에서 제외한다.
4. 후보마다 근거가 된 매니페스트 또는 `.git/` 경계를 함께 기록한다.

### 판정 규칙

| 루트 매니페스트 | 하위 앱 후보 | 판정 |
|----------------|-------------|------|
| 있음 | 0~1개 | **단일 애플리케이션** |
| 없음 | 2개 이상 | **복수 애플리케이션** |
| 있음 | 2개 이상 | 사용자에게 확인 — 모노레포일 수 있음 |
| 없음 | 0~1개 | 사용자에게 직접 질문 |

> 어떤 경우든 **판정 결과를 사용자에게 반드시 보여주고 승인받는다**.

---

## [세팅 모드 판별]

프로젝트 루트(확정)에서 현재 플랫폼의 파일 조회 도구로 다음을 읽기 전용
탐색한다.

- `.ai-docs/`와 그 안의 Markdown·`root-context/`
- 루트 `AGENTS.md`와 Claude instruction 선점 검사 대상
- `.claude/skills/*/SKILL.md`, `.agents/skills/*/SKILL.md`,
  `skills/*/SKILL.md` legacy/custom local copy 후보

| 조건 | 모드 |
|------|------|
| `.ai-docs/` 또는 `AGENTS.md`가 존재 | **갱신 모드** |
| 위 조건 불충족 | **초기 세팅 모드** |

> `.claude/skills/`, `.agents/skills/` 또는 `skills/*/SKILL.md`만 있는 경우:
> legacy/custom local skill 후보로 보고하되, 문서 하네스가 없으면 **초기 세팅**으로
> 분류한다. 이 경로들은 세팅 모드와 관계없이 생성·수정·동기화하지 않는다.

### Portable routing 상태 판별

`.ai-docs/harness/artifact-routing.json`이 있으면 manifest의 mode, app id와 host별
status를 읽는다. bundle은 있으나 host adapter/config가 없거나 `uninstalled`이면
**manual portable adoption**으로 분류한다. 이 분류는 `-Plan`/`-Check`만으로는
바뀌지 않으며 host-local 적용은 G10 승인 뒤에만 제안한다.
