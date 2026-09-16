# DidInput merge checkpoint

이 파일과 `DIDINPUT_MERGE_DIFF.patch`는 2026-09-16 merge 전 DidInput 작업 기록이다.

- 기록표 재진입/추가 시 최신 행이 보이도록 하단 자동 스크롤
- 기록 행 long-press 수정: 시간(분/초), 액트, 위치, 선수 선택
- 다른 기록 선택 시 기존 수정 상태 해제
- DAP/DTP/STP 조건에 따른 선수 입력 및 경로 선수 초기화
- 수정된 기록 행 초록색 표시
- Player 버튼은 기존 Select와 같은 Player 열에 배치
- 이전 기록의 액트/결과 버튼이 다음 입력에서 자동 활성화되지 않도록 처리
- 터치 마커/하이라이트 렌더링 최적화 속성 추가
- `editingId`는 rows computed보다 먼저 선언되어 초기화 TDZ 오류를 방지해야 함

merge 시 `app/pages/DidInput.vue` 충돌을 우선 확인하고, 위 동작을 유지한다.
