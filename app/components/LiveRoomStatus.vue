<script setup lang="ts">
const props = defineProps<{ status: {
  phase: string; revision: number; pending: number; error: string
  connection?: string
  participants: Record<string, { name?: string; role?: string; online?: boolean }>
  conflict?: { commandId: string; local: string; server: string }
} | null; compact?: boolean }>()
const emit = defineEmits<{ 'accept-server': []; 'keep-local': [] }>()
const errorLabel = computed(() => props.status?.error.includes('primary_required')
  ? '주 분석관 권한이 필요한 요청입니다.' : props.status?.error.includes('draft_conflict')
    ? '같은 항목의 저장이 겹쳤습니다.' : props.status?.error)
const label = computed(() => {
  const status = props.status
  if (!status) return ''
  if (status.phase === 'conflict') return '충돌 · 서버 확인 필요'
  if (status.phase === 'error') return '저장 중지 · 확인 필요'
  if (status.phase === 'offline') return `연결 끊김 · 대기 ${status.pending}건`
  if (status.pending) return `저장 중 · 대기 ${status.pending}건`
  if (status.phase === 'saved') {
    if (status.connection === 'offline') return '저장됨 · 실시간 연결 끊김'
    if (status.connection === 'connecting') return '저장됨 · 연결 중'
    return '저장됨'
  }
  return '연결 확인 중'
})
function accept() {
  if (confirm('충돌하거나 거절된 명령 한 건을 취소하고 서버 상태를 적용합니다. 나머지 대기 명령은 다시 검증합니다. 계속할까요?')) emit('accept-server')
}
function keep() {
  if (confirm('충돌한 요청의 내 입력을 서버에 다시 저장합니다. 같은 항목의 서버 내용은 덮어씁니다. 다른 대기 요청은 유지됩니다. 계속할까요?')) emit('keep-local')
}
</script>

<template>
  <div v-if="status" class="liveRoomStatus" :class="{ compact }" role="status" aria-live="polite">
    <!-- 참가자(역할·이름·접속) 목록은 표시하지 않는다. 저장 상태만 보여준다. -->
    <span :title="status.phase === 'error' || status.phase === 'conflict' ? status.error : `서버 버전 ${status.revision}`">{{ label }}<template v-if="status.phase === 'error' || status.phase === 'conflict'"> · {{ errorLabel }}</template></span>
    <button v-if="status.phase === 'conflict' || status.phase === 'error'" @click="accept">서버 기준 재동기화</button>
    <details v-if="status.phase === 'conflict' && status.conflict" class="conflictDetails">
      <summary>충돌 내용 비교</summary>
      <p>내 저장 요청</p><pre>{{ status.conflict.local }}</pre>
      <p>서버에 저장된 내용</p><pre>{{ status.conflict.server }}</pre>
      <button @click="keep">내 입력으로 다시 저장</button>
    </details>
  </div>
</template>

<style scoped>
.liveRoomStatus { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; padding: 6px 12px; font-size: 12px; background: #17212d; color: #e6edf5; }
.liveRoomStatus button { padding: 3px 8px; border: 1px solid #76889e; border-radius: 4px; background: transparent; color: inherit; cursor: pointer; }
.liveRoomStatus.compact { box-sizing: border-box; width: 100%; min-width: 0; max-height: 50px; display: grid; grid-template-columns: minmax(0, 1.2fr) repeat(2, minmax(0, 1fr)); gap: 3px 8px; padding: 4px 6px; font-size: 11px; background: transparent; }
.compact span { min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.compact button { grid-column: 1 / -1; justify-self: start; padding: 2px 6px; font-size: 11px; }
.conflictDetails { grid-column: 1 / -1; width: 100%; }
.conflictDetails pre { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; }
.liveRoomStatus.compact:has(.conflictDetails[open]) { max-height: none; }
</style>
