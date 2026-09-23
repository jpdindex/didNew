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

async function loadRecorders() {
  loading.value = true
  error.value = ''
  try {
    const snapshot = await getDocs(collection($db, 'recorders'))
    recorders.value = snapshot.docs
      .map(item => {
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
      <header>
        <div>
          <h1>분석관 계정 관리</h1>
          <p>로그인한 계정이 자동 등록됩니다. 여기서 입력 화면의 분석관 등급을 관리합니다.</p>
        </div>
        <NuxtLink to="/manage" class="back">← 데이터 관리</NuxtLink>
      </header>

      <p v-if="error" class="error">{{ error }}</p>
      <div v-if="loading" class="empty">계정을 불러오는 중입니다.</div>
      <div v-else-if="recorders.length === 0" class="empty">등록된 분석관 계정이 없습니다.</div>
      <div v-else class="table">
        <div class="row head"><span>계정 ID</span><span>표시 이름</span><span>등급</span><span /></div>
        <div v-for="recorder in recorders" :key="recorder.id" class="row">
          <code>{{ recorder.id }}</code>
          <input v-model.trim="recorder.name" aria-label="표시 이름" />
          <select v-model="recorder.level" aria-label="분석관 등급">
            <option value="advanced">ADVANCED</option>
            <option value="basic">BASIC</option>
          </select>
          <button :disabled="savingId === recorder.id" @click="saveRecorder(recorder)">{{ savingId === recorder.id ? '저장 중' : '저장' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page { min-height: 100vh; background: #0b0f17; color: #eef2f7; padding: 40px; box-sizing: border-box; }
.frame { max-width: 1120px; margin: 0 auto; }
header { display: flex; justify-content: space-between; align-items: flex-start; padding-bottom: 18px; border-bottom: 1px solid rgba(255,255,255,.1); }
h1 { margin: 0; font-size: 24px; } p { color: rgba(255,255,255,.6); margin: 8px 0 0; }
.back { color: #8ce6f5; text-decoration: none; }
.table { margin-top: 24px; border: 1px solid rgba(255,255,255,.12); border-radius: 6px; overflow: hidden; }
.row { display: grid; grid-template-columns: 2fr 1.3fr 1fr 72px; gap: 12px; align-items: center; padding: 12px; border-top: 1px solid rgba(255,255,255,.08); }
.row:first-child { border-top: 0; }.head { color: rgba(255,255,255,.55); font-size: 12px; font-weight: 800; }
code { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #a8b7ca; } input, select { min-width: 0; height: 34px; background: #171d27; color: #eef2f7; border: 1px solid rgba(255,255,255,.17); border-radius: 4px; padding: 0 8px; }
button { height: 34px; border: 1px solid #eab529; border-radius: 4px; background: #eab529; color: #17120a; font-weight: 800; cursor: pointer; } button:disabled { opacity: .5; cursor: wait; }
.empty, .error { margin-top: 24px; color: rgba(255,255,255,.6); }.error { color: #ff9f9f; }
@media (max-width: 760px) { .page { padding: 20px; }.row { grid-template-columns: 1fr 1fr; }.head { display: none; } code { grid-column: 1 / -1; } }
</style>
