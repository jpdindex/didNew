<script setup lang="ts">
import { collection, getDocs, setDoc, doc, Timestamp } from 'firebase/firestore'

type Recorder = {
  id: string
  name: string
  level: 'basic' | 'advanced'
}

const { $db } = useNuxtApp()
const recorders = ref<Recorder[]>([])
const loading = ref(false)
const savingId = ref<string | null>(null)
const error = ref('')
const keyword = ref('')
const levelFilter = ref<'all' | Recorder['level']>('all')

const filteredRecorders = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  return recorders.value.filter(recorder =>
    (levelFilter.value === 'all' || recorder.level === levelFilter.value)
    && (!query || recorder.id.toLowerCase().includes(query) || recorder.name.toLowerCase().includes(query)),
  )
})

async function loadRecorders() {
  loading.value = true
  error.value = ''
  try {
    const snapshot = await getDocs(collection($db, 'recorders'))
    recorders.value = snapshot.docs
      .map((item): Recorder => {
        const data = item.data()
        return {
          id: item.id,
          name: String(data.name || data.displayName || item.id),
          level: data.level === 'basic' ? 'basic' : 'advanced',
        }
      })
      .sort((a, b) => a.name.localeCompare(b.name))
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '분석관 계정을 불러오지 못했습니다.'
  } finally {
    loading.value = false
  }
}

async function saveRecorder(recorder: Recorder) {
  savingId.value = recorder.id
  error.value = ''
  try {
    await setDoc(doc($db, 'recorders', recorder.id), {
      name: recorder.name,
      level: recorder.level,
      updatedAt: Timestamp.now(),
      updatedBy: 'manage-ui',
    }, { merge: true })
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '분석관 계정 저장에 실패했습니다.'
  } finally {
    savingId.value = null
  }
}

onMounted(loadRecorders)
</script>

<template>
  <div class="page">
    <div class="frame">
      <header class="manageHeader">
        <NuxtLink to="/manage" class="back">← 데이터 관리</NuxtLink>
        <h1>분석관 계정 관리</h1>
        <div class="headerAction" aria-hidden="true" />
      </header>
      <p class="pageDescription">로그인한 계정이 자동 등록됩니다. 여기서 입력 화면의 분석관 등급을 관리합니다.</p>

      <p v-if="error" class="error">{{ error }}</p>
      <div v-if="loading" class="empty">계정을 불러오는 중입니다.</div>
      <div v-else-if="recorders.length === 0" class="empty">등록된 분석관 계정이 없습니다.</div>
      <template v-else>
        <div class="filters">
          <input v-model="keyword" type="search" class="search" placeholder="계정 ID 또는 표시 이름 검색" aria-label="계정 검색" />
          <select v-model="levelFilter" aria-label="등급 필터">
            <option value="all">전체 등급</option>
            <option value="advanced">ADVANCED</option>
            <option value="basic">BASIC</option>
          </select>
          <span class="count">{{ filteredRecorders.length }} / {{ recorders.length }}명</span>
        </div>
        <div v-if="filteredRecorders.length === 0" class="empty">조건에 맞는 분석관 계정이 없습니다.</div>
        <div v-else class="table">
          <div class="row head"><span>계정 ID</span><span>표시 이름</span><span>등급</span><span /></div>
          <div v-for="recorder in filteredRecorders" :key="recorder.id" class="row">
            <code>{{ recorder.id }}</code>
            <input v-model.trim="recorder.name" aria-label="표시 이름" />
            <select v-model="recorder.level" aria-label="분석관 등급">
              <option value="advanced">ADVANCED</option>
              <option value="basic">BASIC</option>
            </select>
            <button :disabled="savingId === recorder.id" @click="saveRecorder(recorder)">{{ savingId === recorder.id ? '저장 중' : '저장' }}</button>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.page { min-height: 100vh; background: #0b0f17; color: #eef2f7; padding: 40px; box-sizing: border-box; }
.frame { max-width: 1120px; margin: 0 auto; }
header.manageHeader { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 20px; margin: 0 -28px 16px; padding-bottom: 18px; border-bottom: 1px solid #29313b; }
h1 { margin: 0; text-align: center; font-size: 26px; }.pageDescription { color: rgba(255,255,255,.6); margin: 0; font-size: 13px; }
.back { justify-self: start; height: 34px; display: inline-flex; align-items: center; color: #9da7b3; font-size: 14px; font-weight: 600; text-decoration: none; }.headerAction { justify-self: end; }
.filters { display: flex; align-items: center; gap: 10px; margin-top: 24px; }
.filters .search { flex: 0 1 360px; height: 40px; padding: 0 12px; }.filters select { width: 150px; height: 40px; }
.count { margin-left: auto; color: rgba(255,255,255,.55); font-size: 13px; font-weight: 700; }
.table { margin-top: 12px; border: 1px solid rgba(255,255,255,.12); border-radius: 6px; overflow: hidden; }
.row { display: grid; grid-template-columns: 2fr 1.3fr 1fr 72px; gap: 12px; align-items: center; padding: 12px; border-top: 1px solid rgba(255,255,255,.08); }
.row:first-child { border-top: 0; }.head { color: rgba(255,255,255,.55); font-size: 12px; font-weight: 800; }
code { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #a8b7ca; } input, select { min-width: 0; height: 34px; background: #171d27; color: #eef2f7; border: 1px solid rgba(255,255,255,.17); border-radius: 4px; padding: 0 8px; }
input:focus, select:focus { outline: none; border-color: #8ce6f5; box-shadow: 0 0 0 2px rgba(140,230,245,.18); }
select {
  appearance: none; -webkit-appearance: none; cursor: pointer; padding-right: 30px;
  font-size: 13px; font-weight: 800; letter-spacing: .04em;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M2.5 4.5 6 8l3.5-3.5' fill='none' stroke='%238ce6f5' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: right 10px center;
}
select option { background: #171d27; color: #eef2f7; font-weight: 700; }
/* 지원 브라우저(Chromium 135+)에서는 펼침 목록까지 다크 테마로 */
@supports (appearance: base-select) {
  select, select::picker(select) { appearance: base-select; }
  select { display: inline-flex; align-items: center; }
  select::picker-icon { display: none; }
  select::picker(select) { margin-top: 4px; padding: 4px; background: #171d27; border: 1px solid rgba(255,255,255,.17); border-radius: 6px; box-shadow: 0 10px 24px rgba(0,0,0,.45); }
  select option { padding: 9px 10px; border-radius: 4px; font-size: 13px; letter-spacing: .04em; }
  select option::checkmark { display: none; }
  select option:checked { background: rgba(140,230,245,.14); color: #8ce6f5; }
  select option:focus-visible { outline: none; background: rgba(255,255,255,.08); }
  select option:active { background: rgba(140,230,245,.22); }
}
button { height: 34px; border: 1px solid #eab529; border-radius: 4px; background: #eab529; color: #17120a; font-weight: 800; cursor: pointer; } button:disabled { opacity: .5; cursor: wait; }
.empty, .error { margin-top: 24px; color: rgba(255,255,255,.6); }.error { color: #ff9f9f; }
@media (max-width: 760px) { .page { padding: 20px; }.filters { flex-wrap: wrap; }.filters .search { flex: 1 1 100%; }.row { grid-template-columns: 1fr 1fr; }.head { display: none; } code { grid-column: 1 / -1; } }
</style>
