<script setup lang="ts">
// 기록 잠금 관리 — 관리자 전용. basic 등급 기록자가 "전/후반 갱신"을 누르면
// 그 half의 "수정"이 잠기는데(§TeamSelection.vue), 그 잠금을 실제로 푸는 곳은
// 여기뿐이다. 기록자 화면(TeamSelection)에는 해제 버튼을 절대 두지 않는다 —
// 거기 두면 basic 사용자 본인이 스스로 풀 수 있어 "관리자만 해제"가 무의미해진다.
//
// TODO: 지금은 진행 중인 경기 하나(useMatchState)만 보여준다. Firestore 연동 후에는
// recordings 컬렉션 전체에서 h1Locked/h2Locked = true 인 세션을 모아 보여줘야 한다.
// TODO: 이 화면 자체도 recorders/{uid}.role === 'admin' 인 사람만 들어올 수 있어야 한다
// (지금은 로그인만 하면 /manage 전체에 접근 가능 — 권한 분리 전이다).

const game = useMatchState()

const halfRows = computed(() => [
  { key: 'H1' as const, label: '전반', locked: game.value.h1Locked },
  { key: 'H2' as const, label: '후반', locked: game.value.h2Locked },
])

function unlock(key: 'H1' | 'H2') {
  const label = key === 'H1' ? '전반' : '후반'
  if (!confirm(`${label} 갱신 잠금을 해제하시겠습니까?\n해제하면 기록자가 다시 수정할 수 있습니다.`)) return
  if (key === 'H1') game.value.h1Locked = false
  else game.value.h2Locked = false
}
</script>

<template>
  <div class="page">
    <div class="bg" />

    <div class="frame">
      <div class="topBar">
        <div class="title">기록 잠금 관리</div>
        <NuxtLink class="backBtn" to="/manage">← 데이터 관리로</NuxtLink>
      </div>

      <p class="matchInfo">
        <template v-if="game.matchId">현재 진행 중인 경기: <b>{{ game.matchId }}</b></template>
        <template v-else>진행 중인 경기가 없습니다.</template>
      </p>

      <div class="rows">
        <div v-for="row in halfRows" :key="row.key" class="row">
          <div class="rowLeft">
            <span class="half">{{ row.label }}</span>
            <span class="state" :class="{ locked: row.locked }">
              {{ row.locked ? '🔒 잠김 — 갱신됨' : '잠금 없음' }}
            </span>
          </div>
          <button
            class="unlockBtn"
            :disabled="!row.locked"
            @click="unlock(row.key)"
          >잠금 해제</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page {
  box-sizing: border-box;
  width: 1280px;
  height: 800px;
  display: grid;
  place-items: center;
  position: relative;
  overflow: hidden;
  background: #0b0f17;
}
.bg {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(1200px 500px at 50% 35%, rgba(255,255,255,0.08), transparent 60%),
    linear-gradient(180deg, rgba(0,0,0,0.55), rgba(0,0,0,0.75));
}
.frame {
  box-sizing: border-box;
  position: relative;
  width: 1280px;
  height: 800px;
  background: rgba(10, 14, 22, 0.75);
  border: 1px solid rgba(255,255,255,0.08);
  box-shadow: 0 18px 60px rgba(0,0,0,0.55);
  backdrop-filter: blur(8px);
  padding: 24px;
}
.topBar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 16px;
  margin-bottom: 20px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.title { color: rgba(255,255,255,0.85); font-weight: 700; letter-spacing: 0.02em; }
.backBtn { color: rgba(255,255,255,0.55); font-size: 13px; text-decoration: none; }
.backBtn:hover { color: #fff; }

.matchInfo { color: rgba(255,255,255,0.6); font-size: 13px; margin: 0 0 20px; }
.matchInfo b { color: #fff; font-weight: 700; }

.rows { display: flex; flex-direction: column; gap: 12px; max-width: 520px; }
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 18px;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.1);
  background: rgba(255,255,255,0.03);
}
.rowLeft { display: flex; align-items: center; gap: 12px; }
.half { color: #fff; font-weight: 700; font-size: 14px; }
.state { color: rgba(255,255,255,0.4); font-size: 12px; }
.state.locked { color: #f0b429; font-weight: 700; }

.unlockBtn {
  height: 34px;
  padding: 0 16px;
  border-radius: 4px;
  border: 1px solid rgba(255,255,255,0.16);
  background: rgba(255,255,255,0.04);
  color: #ddd;
  font-weight: 700;
  font-size: 12px;
  cursor: pointer;
}
.unlockBtn:hover:not(:disabled) { background: rgba(240,180,41,0.15); border-color: #f0b429; color: #f0b429; }
.unlockBtn:disabled { opacity: 0.3; cursor: not-allowed; }
</style>
