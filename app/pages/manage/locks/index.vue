<script setup lang="ts">
// 경기 배정 변경 — 세팅 저장으로 잡힌 분석관 배정(inputDrafts.participants)을
// 해제한다. 예전에는 다른 경기를 맡아야 할 때 Firestore 에서 직접 지웠다.
// 해제는 시작 전(ready, 기록 없음) 경기만 서버가 허용한다. 주 분석관을 해제하면
// 그 팀(홈/원정)의 부 분석관·매니저까지 모두 비운다.
// TODO: 관리 화면 권한 정책이 확정되면 이 화면에도 별도 관리자 접근 제어를 둔다.

type Role = 'primary' | 'assistant' | 'manager'
type Assignment = {
  gmId: string
  side: 'H' | 'A'
  date: string
  kickoffTime: string | null
  leagueId: string | null
  teamName: string
  opponentName: string
  status: string
  releasable: boolean
  primaryUid: string | null
  participants: { uid: string; name: string; role: Role }[]
}

const { request } = useBackendApi()
const assignments = ref<Assignment[]>([])
const loading = ref(false)
const error = ref('')
const keyword = ref('')
const releasingKey = ref<string | null>(null)

const roleLabels: Record<Role, string> = { primary: '주 분석관', assistant: '부 분석관', manager: '매니저' }
const statusLabels: Record<string, string> = {
  ready: '시작 전', H1: '전반 진행', H1_done: '전반 종료', H2: '후반 진행', H2_done: '후반 종료', final: '분석 종료',
}

const filtered = computed(() => {
  const query = keyword.value.trim().toLocaleLowerCase()
  if (!query) return assignments.value
  return assignments.value.filter(item =>
    `${item.teamName} ${item.opponentName} ${item.gmId} ${item.participants.map(p => p.name).join(' ')}`
      .toLocaleLowerCase().includes(query),
  )
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const response = await request<{ assignments: Assignment[] }>('/api/v1/match-input/assignments')
    assignments.value = response.assignments
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '배정 목록을 불러오지 못했습니다.'
  } finally {
    loading.value = false
  }
}

async function release(item: Assignment, participant: Assignment['participants'][number]) {
  const team = `${item.teamName} (${item.side === 'H' ? '홈' : '원정'})`
  const others = item.participants.filter(p => p.uid !== participant.uid)
  const message = participant.role === 'primary' && others.length
    ? `${team} 주 분석관 ${participant.name} 배정을 해제합니다.\n함께 배정된 ${others.map(p => p.name).join(', ')}도 모두 해제됩니다.\n계속하시겠습니까?`
    : `${team} ${roleLabels[participant.role]} ${participant.name} 배정을 해제하시겠습니까?`
  if (!confirm(message)) return

  releasingKey.value = `${item.gmId}_${item.side}_${participant.uid}`
  error.value = ''
  try {
    await request(
      `/api/v1/match-input/drafts/${encodeURIComponent(item.gmId)}/${item.side}/participants/${encodeURIComponent(participant.uid)}`,
      { method: 'DELETE' },
    )
    await load()
  } catch (cause) {
    const code = (cause as { code?: string }).code
    error.value = code === 'assignment_started'
      ? '이미 시작된 경기라 해제할 수 없습니다.'
      : cause instanceof Error ? cause.message : '배정 해제에 실패했습니다.'
    await load()
  } finally {
    releasingKey.value = null
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="bg" />

    <div class="frame">
      <header class="topBar">
        <NuxtLink class="backBtn" to="/manage">← 데이터 관리</NuxtLink>
        <h1>경기 배정 변경</h1>
        <div class="headerAction">
          <button class="refreshBtn" :disabled="loading" @click="load">{{ loading ? '불러오는 중…' : '새로고침' }}</button>
        </div>
      </header>

      <div class="toolbar">
        <input v-model="keyword" class="search" type="search" placeholder="팀·분석관 이름으로 검색">
        <p class="hint">시작 전 경기만 해제할 수 있습니다. 주 분석관을 해제하면 같은 팀의 부 분석관도 함께 해제됩니다.</p>
      </div>

      <p v-if="error" class="error">{{ error }}</p>

      <div class="list">
        <p v-if="!loading && !filtered.length" class="empty">배정된 경기가 없습니다.</p>
        <article v-for="item in filtered" :key="`${item.gmId}_${item.side}`" class="card" :class="{ locked: !item.releasable }">
          <div class="cardHead">
            <span class="date">{{ item.date || '-' }}<template v-if="item.kickoffTime"> {{ item.kickoffTime }}</template></span>
            <span class="match">
              <b>{{ item.teamName }}</b>
              <span class="sideTag">{{ item.side === 'H' ? '홈' : '원정' }}</span>
              <span class="vs">vs {{ item.opponentName }}</span>
            </span>
            <span class="status" :class="{ ready: item.status === 'ready' && item.releasable }">
              {{ item.releasable ? '시작 전' : (statusLabels[item.status] ?? item.status) + ' · 해제 불가' }}
            </span>
          </div>
          <div class="people">
            <div v-for="participant in item.participants" :key="participant.uid" class="person">
              <span class="role" :class="participant.role">{{ roleLabels[participant.role] ?? participant.role }}</span>
              <span class="name">{{ participant.name }}</span>
              <button
                class="releaseBtn"
                :disabled="!item.releasable || releasingKey !== null"
                @click="release(item, participant)"
              >{{ releasingKey === `${item.gmId}_${item.side}_${participant.uid}` ? '해제 중…' : '배정 해제' }}</button>
            </div>
          </div>
        </article>
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
  display: flex;
  flex-direction: column;
  background: rgba(10, 14, 22, 0.75);
  border: 1px solid rgba(255,255,255,0.08);
  box-shadow: 0 18px 60px rgba(0,0,0,0.55);
  backdrop-filter: blur(8px);
  padding: 24px;
}
.topBar {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 20px;
  margin: 0 28px 16px;
  border-bottom: 1px solid #29313b;
  padding-bottom: 18px;
}
.topBar h1 { margin: 0; color: #eee; text-align: center; font-size: 26px; }
.backBtn { justify-self: start; height: 44px; display: inline-flex; align-items: center; color: #9da7b3; font-size: 14px; font-weight: 600; text-decoration: none; }
.backBtn:active { color: #fff; }
.headerAction { justify-self: end; }

.refreshBtn,
.releaseBtn {
  height: 44px;
  padding: 0 18px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.16);
  background: rgba(255,255,255,0.04);
  color: #ddd;
  font-weight: 700;
  font-size: 13px;
  cursor: pointer;
}
.refreshBtn:active:not(:disabled) { background: rgba(255,255,255,0.12); }
.releaseBtn { border-color: rgba(240,90,90,0.45); color: #f08080; }
.releaseBtn:active:not(:disabled) { background: rgba(240,90,90,0.18); color: #fff; }
.refreshBtn:disabled,
.releaseBtn:disabled { opacity: 0.3; cursor: not-allowed; }

.toolbar { display: flex; align-items: center; gap: 16px; margin: 0 28px 12px; }
.search {
  width: 300px;
  height: 44px;
  padding: 0 14px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.14);
  background: rgba(255,255,255,0.04);
  color: #eee;
  font-size: 14px;
}
.hint { margin: 0; color: rgba(255,255,255,0.5); font-size: 12px; }
.error { margin: 0 28px 12px; color: #f08080; font-size: 13px; font-weight: 600; }
.empty { color: rgba(255,255,255,0.45); font-size: 14px; text-align: center; margin-top: 60px; }

.list { flex: 1; min-height: 0; overflow-y: auto; padding: 0 28px; display: flex; flex-direction: column; gap: 10px; }
.card {
  padding: 14px 18px;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.1);
  background: rgba(255,255,255,0.03);
}
.card.locked { opacity: 0.55; }
.cardHead { display: flex; align-items: center; gap: 14px; margin-bottom: 10px; }
.date { color: rgba(255,255,255,0.55); font-size: 13px; font-variant-numeric: tabular-nums; }
.match { display: flex; align-items: center; gap: 8px; color: #fff; font-size: 15px; }
.sideTag { padding: 2px 8px; border-radius: 4px; background: rgba(255,255,255,0.08); color: #bbb; font-size: 11px; font-weight: 700; }
.vs { color: rgba(255,255,255,0.5); font-size: 13px; }
.status { margin-left: auto; color: rgba(255,255,255,0.45); font-size: 12px; font-weight: 700; }
.status.ready { color: #4fd1c5; }

.people { display: flex; flex-wrap: wrap; gap: 10px; }
.person {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 6px 6px 12px;
  border-radius: 6px;
  background: rgba(255,255,255,0.04);
}
.role { padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 800; background: rgba(255,255,255,0.08); color: #bbb; }
.role.primary { background: rgba(79,209,197,0.16); color: #4fd1c5; }
.role.assistant { background: rgba(240,180,41,0.16); color: #f0b429; }
.name { color: #eee; font-size: 14px; font-weight: 600; min-width: 80px; }
</style>
