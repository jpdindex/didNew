<script setup lang="ts">
type TestGoalFields = {
  id?: string
  clock: string
  playerName: string
  home: string
  away: string
  scoringSide: 'H' | 'A'
  date: string
  shotId: string | null
}
type ShotCandidate = {
  id: string
  title: string
  home: string | null
  away: string | null
  clock: string | null
  date: string | null
  playerName: string | null
}

const props = defineProps<{ initial: TestGoalFields }>()
const emit = defineEmits<{ close: []; saved: [id: string] }>()
const { request } = useBackendApi()
const dialog = ref<HTMLDialogElement>()
const { id, ...initial } = props.initial
const form = reactive(initial)
const search = ref('')
const candidates = ref<ShotCandidate[] | null>(null)
const shotTitle = ref('')
const searching = ref(false)
const saving = ref(false)
const searchError = ref('')
const saveError = ref('')
const clockPattern = '(?:[0-9]{1,3}\\+)?[0-9]{1,3}:[0-5][0-9]'
let searchRequest: AbortController | undefined

async function findShots() {
  searchRequest?.abort()
  const controller = new AbortController()
  searchRequest = controller
  searchError.value = ''
  candidates.value = null
  searching.value = true
  try {
    const params = new URLSearchParams({ search: search.value })
    const response = await request<{ candidates: ShotCandidate[] }>('/api/v1/goalx/test-goals/shotx-candidates?' + params, {
      signal: controller.signal,
    })
    if (!controller.signal.aborted) candidates.value = response.candidates
  } catch (error) {
    if (!controller.signal.aborted) searchError.value = error instanceof Error ? error.message : 'ShotX 검색에 실패했습니다.'
  } finally {
    if (!controller.signal.aborted) searching.value = false
  }
}

function useShot(candidate: ShotCandidate) {
  const clock = candidate.clock?.trim()
  if (clock && new RegExp('^' + clockPattern + '$').test(clock)) form.clock = clock
  if (candidate.home) form.home = candidate.home
  if (candidate.away) form.away = candidate.away
  if (candidate.date) form.date = candidate.date
  if (candidate.playerName) form.playerName = candidate.playerName
  form.shotId = candidate.id
  shotTitle.value = candidate.title
  candidates.value = null
}

async function save() {
  if (saving.value) return
  saveError.value = ''
  if (form.home.trim().toLowerCase() === form.away.trim().toLowerCase()) {
    saveError.value = '홈팀과 원정팀은 서로 달라야 합니다.'
    return
  }
  saving.value = true
  try {
    const response = await request<{ id: string }>('/api/v1/goalx/test-goals' + (id ? '/' + encodeURIComponent(id) : ''), {
      method: id ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form),
    })
    emit('saved', response.id)
  } catch (error) {
    saveError.value = error instanceof Error ? error.message : 'TESTGOAL 득점 기록을 저장하지 못했습니다.'
  } finally {
    saving.value = false
  }
}

function cancel(event: Event) {
  if (saving.value) event.preventDefault()
}

onMounted(() => dialog.value?.showModal())
onBeforeUnmount(() => searchRequest?.abort())
</script>

<template>
  <dialog ref="dialog" class="test-editor" aria-labelledby="test-goal-title" @cancel="cancel" @close="emit('close')">
    <header>
      <h2 id="test-goal-title">{{ id ? 'TESTGOAL 득점 정보 수정' : 'TESTGOAL 득점 추가' }}</h2>
      <button type="button" :disabled="saving" @click="dialog?.close()">닫기</button>
    </header>

    <section class="import-shot" aria-label="ShotX 결과 불러오기">
      <form class="search" @submit.prevent="findShots">
        <input v-model.trim="search" aria-label="ShotX 결과 검색" placeholder="팀, 경기 시각, 파일명으로 검색" maxlength="150" :disabled="saving">
        <button type="submit" :disabled="searching || saving">{{ searching ? '불러오는 중…' : 'ShotX 결과 불러오기' }}</button>
      </form>
      <p v-if="searchError" class="error" role="alert">{{ searchError }}</p>
      <p v-else-if="candidates && !candidates.length" class="muted">검색 결과가 없습니다.</p>
      <ul v-if="candidates?.length" class="shot-results">
        <li v-for="candidate in candidates" :key="candidate.id">
          <button type="button" :disabled="saving" @click="useShot(candidate)">
            <strong>{{ candidate.title }}</strong>
            <span>{{ [candidate.home, candidate.away, candidate.clock].filter(Boolean).join(' · ') }}</span>
          </button>
        </li>
      </ul>
      <p v-if="form.shotId" class="linked-shot">
        <span>{{ shotTitle || 'ShotX 결과 연결됨' }}</span>
        <button type="button" :disabled="saving" @click="form.shotId = null; shotTitle = ''">연결 해제</button>
      </p>
    </section>

    <form class="goal-form" @submit.prevent="save">
      <label>득점자
        <input v-model.trim="form.playerName" required maxlength="120" :disabled="saving">
      </label>
      <label>득점 시각
        <input v-model.trim="form.clock" required :pattern="clockPattern" placeholder="예 74:31" :disabled="saving">
      </label>
      <label>홈팀
        <input v-model.trim="form.home" required maxlength="120" :disabled="saving">
      </label>
      <label>원정팀
        <input v-model.trim="form.away" required maxlength="120" :disabled="saving">
      </label>
      <label>득점 팀
        <select v-model="form.scoringSide" :disabled="saving">
          <option value="H">{{ form.home || '홈팀' }}</option>
          <option value="A">{{ form.away || '원정팀' }}</option>
        </select>
      </label>
      <label>경기 날짜
        <input v-model="form.date" type="date" required :disabled="saving">
      </label>
      <p v-if="saveError" class="error" role="alert">{{ saveError }}</p>
      <footer>
        <button class="save" type="submit" :disabled="saving">{{ saving ? '저장 중…' : '저장' }}</button>
      </footer>
    </form>
  </dialog>
</template>

<style scoped>
.test-editor { width: min(590px, calc(100vw - 30px)); max-height: 86vh; box-sizing: border-box; padding: 0; border: 1px solid #35454a; border-radius: 14px; background: #142126; color: #e4ece8; }
.test-editor::backdrop { background: #071014b8; }
header, .linked-shot { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
header { padding: 14px 18px; border-bottom: 1px solid #304344; }
h2 { margin: 0; font-size: 17px; }
button, input, select { font: inherit; box-sizing: border-box; border: 1px solid #3b5051; border-radius: 7px; background: #102022; color: inherit; padding: 8px 10px; }
button { cursor: pointer; }
button:disabled { opacity: .55; cursor: default; }
button:focus-visible, input:focus-visible, select:focus-visible { outline: 2px solid #a4e7c9; outline-offset: 2px; }
.import-shot { padding: 12px 18px; border-bottom: 1px solid #304344; }
.search { display: flex; flex-wrap: wrap; gap: 6px; }
.search input { flex: 1; min-width: 140px; }
.shot-results { max-height: 170px; overflow: auto; list-style: none; padding: 0; margin: 8px 0 0; }
.shot-results button { display: grid; text-align: left; gap: 3px; width: 100%; margin-bottom: 4px; }
.shot-results span, .muted { color: #a4b5b0; }
.linked-shot { margin: 8px 0 0; color: #a4e7c9; }
.goal-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 11px; padding: 14px 18px 18px; }
.goal-form label { display: grid; gap: 5px; min-width: 0; font-size: 13px; }
.goal-form input, .goal-form select { width: 100%; min-width: 0; color-scheme: dark; }
.goal-form p, footer { grid-column: 1 / -1; }
.error { color: #ffadb2; }
footer { display: flex; justify-content: flex-end; }
.save { min-width: 76px; border-color: #9ee4c4; background: #9ee4c4; color: #10251f; font-weight: 750; }
</style>
