# prompts/update-mode.md
# 역할: 이미 세팅된 프로젝트에서 .ai-docs·루트 컨텍스트를 갱신하는 절차

---

## 전제

- SKILL.md Step 3에서 **갱신 모드**로 판정.
- `.ai-docs/` 또는 `AGENTS.md`가 존재.
- 프로젝트 유형(단일/복수)은 Step 2에서 확정.
- 권한 정책이 있으면 SKILL.md의 **선택 권한 정책 연계**에서 현재 계정의 `admin`
  범위와 정확한 갱신 경로를 검증한 상태.

---

## 1. 플러그인 설치 상태 확인

후속 스킬 사용이 실패하면 `harness-kit` 플러그인 설치 상태와 새 세션 여부를 안내한다.
이 스킬은 프로젝트 `.claude/skills/`, `.agents/skills/`, `skills/`에 사용자
스킬을 생성·복사·동기화하지 않는다.

---

## 2. 갱신 계획 사용자 확인

기존 파일과 번들 템플릿을 먼저 읽고 파일별로 다음 상태를 분류한다.

- `new`: 대상 파일이 없음
- `managed`: 정확히 한 쌍의 관리 블록 marker가 있음
- `unmanaged`: marker가 없고 사용자 또는 구버전 내용이 있음
- `malformed`: marker가 중복되거나 시작·끝이 맞지 않음

Markdown marker는
`<!-- harness-kit:managed:start -->` /
`<!-- harness-kit:managed:end -->`, `.gitignore` marker는
`# harness-kit:managed:start` /
`# harness-kit:managed:end`를 사용한다.

각 대상의 현재 내용 hash, 상태, 변경될 관리 블록 diff를 요약하여 사용자에게
확인받는다. `unmanaged` 또는 `malformed` 파일은 자동 갱신 대상에 넣지 않는다.

> ✋ **갱신 대상 확인**
>
> | 유형 | 대상 | 상태 | 처리 |
> |------|------|------|------|
> | `.ai-docs` 안내·정책 | README/.gitignore/_inbox | {new/managed/unmanaged/malformed} | {생성/관리 블록 갱신/보존} |
> | 루트 컨텍스트 | 단일 앱 AGENTS.md 정본 또는 복수 앱 root-context 관리 원본·루트 실행본 | {상태} | {처리} |
> | legacy Claude instruction | 프로젝트·상위 경로의 CLAUDE.md/.claude/CLAUDE.md/CLAUDE.local.md | {없음/관리 bridge/사용자 파일/상위 파일} | {통과/승인 후 제거/중단} |
> | legacy local skill copy | 읽기 전용 report만 출력 |
>
> 진행하시겠습니까? **(승인 / 수정 / 취소)**

---

## 3. `.ai-docs/` 안내·정책 파일 갱신

`.ai-docs/`가 존재하면(단일·복수 공통) 아래 안내·정책 파일의 **관리 블록만**
최신 템플릿으로 맞춘다. 관리 블록 밖의 사용자 확장과 `_inbox/` 내용은
절대 덮어쓰지 않는다.

| 파일 | 단일 앱 템플릿 | 복수 앱 템플릿 | 처리 |
|------|----------------|----------------|------|
| `.ai-docs/README.md` | `docs-readme-single.template` | `docs-readme-multi.template` | 없으면 생성, 있으면 관리 블록만 교체 |
| `.ai-docs/.gitignore` | `docs-gitignore.template` | (동일) | 없으면 생성, 있으면 관리 블록만 교체 |
| `.ai-docs/_inbox/` | — | — | 없으면 생성(`.gitkeep`+README), 내용 보존 |

번들 리소스는 `SKILL.md`의 **플러그인 리소스 해석 계약**으로 읽는다.

1. 프로젝트 유형에 따라 `templates/docs-readme-single.template` 또는
   `templates/docs-readme-multi.template`의 관리 블록을 준비한다.
2. `templates/docs-gitignore.template`의 관리 블록을 준비한다.
3. `.ai-docs/_inbox/`가 없을 때만 디렉토리, 빈 `.gitkeep`,
   `templates/inbox-readme.template` 기반 README를 만든다.
4. 기존 `_inbox/` 내용은 보존한다.

관리 블록 갱신 규칙:

1. `new`면 템플릿 전체를 새 파일로 생성한다.
2. `managed`면 시작 marker부터 끝 marker까지만 새 관리 블록으로 교체하고,
   앞뒤 사용자 내용을 byte-preserve한다.
3. `unmanaged`면 기존 파일과 제안 템플릿의 diff만 보여주고 파일을 보존한다.
   사용자가 명시적으로 마이그레이션을 승인하면 기존 내용을 삭제하지 않고
   관리 블록을 추가하는 merge안을 먼저 사용한다.
4. 사용자가 전체 교체를 별도로 승인한 경우에만 원본을
   `.ai-docs/archive/harness-setup/{timestamp}/{상대경로}`에 백업하고 교체한다.
5. `malformed`면 쓰기를 중단하고 marker 위치와 복구안을 보고한다.
6. 읽은 뒤 승인받기 전 원본 hash와 쓰기 직전 hash가 다르면 동시 수정으로
   판단해 쓰기를 중단하고 diff를 다시 산출한다.

`.ai-docs/`가 아직 없으면 초기 세팅의 해당 단일/복수 구조를 적용해 생성한다.
`AGENTS.md`만 존재한다는 이유로 `.ai-docs/` 생성을 건너뛰지 않는다.

---

## 4. 루트 컨텍스트 갱신

단일 앱은 루트 `AGENTS.md`가 공통 정본이다. 복수 앱은
`.ai-docs/root-context/AGENTS.md`가 Git 관리 원본이고 루트 `AGENTS.md`는 실행본이다.
Claude Code 전용 portable routing·host trust 규칙도 `AGENTS.md` 관리 블록에 둔다.

단일 앱:
- `AGENTS.md`가 없으면 `templates/root-context-single.template` 기반 뼈대를
  생성한다. `{{APP_ID}}`는 Step 2에서 확정한 단일 앱 식별자로 치환해
  `.ai-docs/{앱}-context.md`를 가리킨다. 기존 파일은 관리 블록만 갱신하고 블록 밖의
  프로젝트 규칙은 보존한다.
- marker가 없는 기존 파일은 Section 3의 `unmanaged` 규칙을 그대로 적용한다.

복수 앱:
- `.ai-docs/root-context/AGENTS.md`의 관리 블록을 검증한 뒤 루트 `AGENTS.md`의 같은
  관리 블록에 반영한다. 루트 실행본을 관리 원본에 자동 역반영하지 않는다.
- 두 위치 모두 없으면 `templates/root-context.template`을 확정된 앱 목록으로
  치환해 양쪽에 생성한다.
- 관리 원본만 없고 루트 실행본만 있으면 자동 승격하지 않는다. 실행본에서 관리 원본을
  복구할 diff를 보여주고 별도 승인을 받은 뒤 생성한다.

### 기존 Claude bridge 이관

`detection.md`의 Claude instruction 선점 검사 결과를 다음과 같이 처리한다.

1. 먼저 새 `AGENTS.md` 관리 블록에 기존 harness bridge의 portable routing manifest,
   앱별 routing instruction, Claude host hook 승인·신뢰 규칙이 모두 반영됐는지 검증한다.
2. 프로젝트 루트 `CLAUDE.md`가 알려진 harness 관리 marker 한 쌍만 포함하고 marker 밖에
   공백만 있으면 current hash와 삭제 diff를 계획에 넣는다. 사용자 승인과 쓰기 직전 hash
   재검증 뒤에만 제거한다.
3. 복수 앱의 `.ai-docs/root-context/CLAUDE.md`도 같은 조건을 만족할 때만 같은 승인
   계획에서 제거한다. Git 이력으로 복구할 수 있으므로 별도 사본을 만들지 않는다.
4. marker 밖 사용자 내용, marker가 없는 파일, malformed marker, `.claude/CLAUDE.md`,
   `CLAUDE.local.md`, 상위 경로의 Claude instruction 파일은 자동 이동·수정·삭제하지 않고
   갱신을 중단한다. `AGENTS.md`로 수동 이관할 내용과 정확한 경로를 보고한다.
5. 제거 뒤 프로젝트 루트부터 파일시스템 루트까지 다시 검사해 `CLAUDE.md`,
   `.claude/CLAUDE.md`, `CLAUDE.local.md`가 0건일 때만 AGENTS-only 이관 성공으로 보고한다.

이 이관은 반복 실행해도 추가 삭제나 내용 변경이 없어야 한다. 제거된 관리 bridge는
이전 Harness Kit release의 template 또는 Git 이력으로 복구할 수 있다.

---

## 5. 복수 애플리케이션 추가 갱신

프로젝트가 **복수 애플리케이션**인 경우에만 수행.

### 5-1. 루트 컨텍스트 갱신

`.ai-docs/root-context/AGENTS.md`를 다시 읽어 관리 블록을 검증한 뒤, 루트 파일의
같은 관리 블록에만 반영한다. 파일 전체를 복사하지 않으며 관리 원본과 루트 실행본
각각의 블록 밖 사용자 확장을 보존한다.

> 만약 `.ai-docs/root-context/` 파일이 존재하지 않으면 (다른 스킬에 의해 아직 안 만들어졌거나 삭제된 경우),
> 갱신하지 않고 사용자에게 알린다.

### 5-2. 신규 애플리케이션 감지

Step 2 감지 결과에서 `.ai-docs/{앱}-context.md`가 없는 새 앱 폴더가 발견되면 현재
플랫폼의 파일 도구로 다음을 만든다. 단, 권한 정책이 활성화된 프로젝트에서는
앱 핵심 문서를 `admin`이 대신 만들지 않는다.

- `.ai-docs/{앱}-context.md`
- `.ai-docs/{앱}/context-base/`
- `.ai-docs/{앱}/instruction/`
- `.ai-docs/{앱}/impl-doc/`

사용자에게 신규 앱 추가 사실을 알린다.

권한 정책이 활성화된 경우에는 admin 범위인 루트 컨텍스트와
`.ai-docs/harness/artifact-routing.json`의 앱·repository 지도만 갱신한다.
`.ai-docs/{앱}-context.md`, `context-base/DESIGN.md`, `instruction/*.md`는 만들지 않고,
정책에 새 앱과 `pm-pl` 또는 `app-doc-lead`가 배정돼 있는지 보고한다. 앱 문서 권한자가
`design-doc`과 `context-doc`을 실행할 후속 작업으로 넘긴다. 정책이 없으면 기존 생성
흐름을 유지한다.

---

## 6. legacy local skill copy 읽기 전용 report

`.agents/skills/`, `.claude/skills/` 또는 `skills/*/SKILL.md`가 있으면 다음
기준으로 읽기 전용 분류만 보고한다.

| 분류 | 기준 | 기본 처리 |
|------|------|----------|
| 알려진 옛 하네스 copy | 과거 release inventory와 파일 목록·hash 일치 | 보존, 승인형 migration 후보 |
| 사용자가 수정한 copy | 이름은 같지만 hash 불일치 | 보존, 수동 검토 필요 |
| 무관한 custom skill | 과거 하네스 목록에 없음 | 보존 |
| plugin 이름 충돌 | 현재 plugin 제공 스킬과 같은 이름 | 보존, 충돌 보고 |

승인 전에는 backup·remove·rename을 수행하지 않는다.

---

## 7. 결과 정리

갱신 결과를 요약한다:

```
## 갱신 결과

- `.ai-docs/` 안내·정책: README/.gitignore 관리 블록 갱신됨 / 사용자 확장 보존 / `_inbox/` 유지(또는 신규 생성)
- 루트 컨텍스트: AGENTS 관리 블록 갱신됨 / 기존 관리 bridge 제거됨 / 사용자 파일로 중단 / 변경 없음
- (복수앱) 신규 앱 감지: {앱명} (구조 추가됨)
- legacy local skill copy: 읽기 전용 report N건 / 없음
- local skill projection 변경: 없음 (`.agents/skills`, `.claude/skills`, `skills`)
```

## 8. 실행 후 불변조건 검증

이번 실행의 변경 목록이 `.ai-docs/**`, 루트 `AGENTS.md` 안에만
있는지 확인한다. `.agents/skills/**`, `.claude/skills/**`, `skills/**` 변경이
하나라도 있으면 성공으로 보고하지 않는다. 템플릿 placeholder와
Claude instruction 선점 파일 0건도 함께 검증한다. 승인된 legacy 관리 bridge 삭제는
예외 변경으로 별도 보고한다. 갱신 전후 사용자 관리 블록 밖 내용과
legacy local skill copy의 hash가 동일한지 확인하고, backup을 만든 경우 대상
목록과 복구 경로를 결과에 포함한다.

## 9. Portable routing update·manual adoption

`.ai-docs/harness/artifact-routing.json`을 current 상태로 읽고, shared bundle과 각
host-local file을 `created`/`modified`/`unchanged`, `local-only`/`shared`로 나눈
**current/proposed diff**를 먼저 출력한다. root non-Git, `.ai-docs` Git, 각 app Git은
각각 status만 읽어 별도 변경을 보존한다.

`-Plan`/`-Check`은 읽기 전용이고, manual portable adoption의 `-Apply`/`-Uninstall`은
G10 승인 뒤 `-ApproveHostInstall`과 함께만 실행한다. Codex hook은 `/hooks` 신뢰
증적 전까지 `pending-trust`이며 active로 변경하지 않는다.

갱신 때는 다음 portability migration을 같은 계획에 포함한다.

1. 공유 `.ai-docs/**`, 루트 컨텍스트, `.claude/settings.json`, `.codex/hooks.json`에서
   현재 PC의 사용자 홈·드라이브 절대경로가 생성 템플릿에 의해 들어간 부분을 찾는다.
2. `artifact-routing.json.project_root`는 `.`으로 바꾸고, host의 `status`, `trust`,
   `config_sha256`은 공유 manifest에서 제거한다. 기존 값은 존재할 때만 Git 무시 대상인
   `.ai-docs/.harness/routing-state.local.json`으로 옮긴다.
3. Codex managed hook command는 현재 session cwd의 상위에서 `.codex/hooks/`를 찾는
   portable command로 교체한다. 사용자 hook entry는 보존한다.
4. root context의 제목과 프로젝트 루트 표기는 체크아웃 폴더명·절대경로가 아니라
   일반 제목과 `./`로 갱신한다. 단일 앱 id는 기존 안정 id를 우선하며 폴더명으로
   다시 계산하지 않는다.
5. `_inbox` manifest는 원본의 basename과 hash만 기록하고 절대 `source_path`를 남기지
   않는다. 기존 manifest에 `source_path`가 있으면 basename인 `source_name`으로 바꾼다.
6. `.ai-docs/.harness/humanize-handoffs.json`의 기존 `artifact_bundle_id`에 절대
   프로젝트 루트가 들어 있으면 producer와 `artifact_fingerprint` 앞 16자를 사용한
   `{producer}:migrated-{fingerprint}`로 바꾼다. fingerprint·artifacts·events는 보존한다.
7. 현재 `managed_files`에 없어진 `.ai-docs/harness/settings.json`과
   `.ai-docs/harness/hooks/artifact-route-guard.ps1`이 알려진 구버전 생성물 signature와
   일치하면 승인된 update 계획에서 제거한다. 사용자가 수정했거나 출처를 확정할 수
   없으면 보존하고 해당 절대경로 위치를 보고한다.
8. 새 root context template으로 기존 `CLAUDE.md`의 portable routing·Claude host trust
   규칙을 `AGENTS.md` 관리 블록에 반영한 뒤, 알려진 관리 bridge만 위 Section 4의
   승인형 이관으로 제거한다.
9. 변경 뒤 공유 대상 전체에서 기존 절대 프로젝트 루트와 사용자 홈 문자열이 0건인지,
   다른 위치로 checkout한 fixture에서도 공유 산출물이 byte-identical인지 검증한다.
10. 프로젝트 루트부터 파일시스템 루트까지 Claude instruction 선점 파일이 0건인지 다시
    검사한다. 사용자 또는 상위 경로 파일이 남아 있으면 성공으로 보고하지 않는다.

이 migration은 반복 실행해도 추가 변경이 없어야 한다. 출처를 확정할 수 없는 사용자
작성 절대경로는 자동 치환하지 않고 정확한 파일과 값을 보고한다.
