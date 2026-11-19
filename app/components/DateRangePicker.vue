<script setup lang="ts">
const props = withDefaults(defineProps<{ from: string; to: string; active?: boolean; placeholder?: string }>(), {
  active: false,
  placeholder: '기간 직접 선택',
})
const emit = defineEmits<{ change: [range: { from: string; to: string }] }>()

const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토']

const root = ref<HTMLElement | null>(null)
const open = ref(false)
const viewYear = ref(new Date().getFullYear())
const viewMonth = ref(new Date().getMonth())
const draftFrom = ref('')
const draftTo = ref('')

const todayKey = toKey(new Date())

function toKey(date: Date) {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function display(value: string) {
  return value ? value.replaceAll('-', '.') : ''
}

const label = computed(() => {
  if (!props.from && !props.to) return ''
  return `${display(props.from) || '처음'} ~ ${display(props.to) || '오늘'}`
})

const days = computed(() => {
  const first = new Date(viewYear.value, viewMonth.value, 1)
  const start = new Date(first)
  start.setDate(1 - first.getDay())
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(start)
    date.setDate(start.getDate() + index)
    return { key: toKey(date), day: date.getDate(), weekday: date.getDay(), outside: date.getMonth() !== viewMonth.value }
  })
})

const hint = computed(() => (draftFrom.value && !draftTo.value ? '종료일을 선택하세요' : '시작일을 선택하세요'))

function toggle() {
  if (open.value) {
    open.value = false
    return
  }
  draftFrom.value = props.from
  draftTo.value = props.to
  const base = props.from ? new Date(`${props.from}T00:00:00`) : new Date()
  viewYear.value = base.getFullYear()
  viewMonth.value = base.getMonth()
  open.value = true
}

function shiftMonth(delta: number) {
  const next = new Date(viewYear.value, viewMonth.value + delta, 1)
  viewYear.value = next.getFullYear()
  viewMonth.value = next.getMonth()
}

function pick(key: string) {
  if (!draftFrom.value || draftTo.value) {
    draftFrom.value = key
    draftTo.value = ''
    return
  }
  const [from, to] = key < draftFrom.value ? [key, draftFrom.value] : [draftFrom.value, key]
  draftFrom.value = from
  draftTo.value = to
  open.value = false
  emit('change', { from, to })
}

function clear() {
  draftFrom.value = ''
  draftTo.value = ''
  open.value = false
  emit('change', { from: '', to: '' })
}

function goToday() {
  const now = new Date()
  viewYear.value = now.getFullYear()
  viewMonth.value = now.getMonth()
}

function dayClass(cell: { key: string; weekday: number; outside: boolean }) {
  const from = draftFrom.value
  const to = draftTo.value
  return {
    outside: cell.outside,
    sun: cell.weekday === 0,
    sat: cell.weekday === 6,
    today: cell.key === todayKey,
    start: cell.key === from,
    end: cell.key === to || (!to && cell.key === from),
    inRange: !!from && !!to && cell.key > from && cell.key < to,
  }
}

function onOutside(event: PointerEvent) {
  if (open.value && root.value && !root.value.contains(event.target as Node)) open.value = false
}
function onKey(event: KeyboardEvent) {
  if (event.key === 'Escape') open.value = false
}

onMounted(() => {
  document.addEventListener('pointerdown', onOutside)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onOutside)
  document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div ref="root" class="drp">
    <button type="button" class="trigger" :class="{ active, open }" :aria-expanded="open" @click="toggle">
      <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2" y="3" width="12" height="11" rx="2" /><path d="M2 6.5h12M5.5 1.5v3M10.5 1.5v3" /></svg>
      <span :class="{ placeholder: !label }">{{ label || placeholder }}</span>
    </button>

    <div v-if="open" class="popover" role="dialog" aria-label="기간 선택">
      <div class="head">
        <button type="button" class="nav" aria-label="이전 달" @click="shiftMonth(-1)">‹</button>
        <strong>{{ viewYear }}년 {{ viewMonth + 1 }}월</strong>
        <button type="button" class="nav" aria-label="다음 달" @click="shiftMonth(1)">›</button>
      </div>
      <div class="grid weekdays">
        <span v-for="(name, index) in WEEKDAYS" :key="name" :class="{ sun: index === 0, sat: index === 6 }">{{ name }}</span>
      </div>
      <div class="grid">
        <button v-for="cell in days" :key="cell.key" type="button" class="day" :class="dayClass(cell)" @click="pick(cell.key)">
          <span>{{ cell.day }}</span>
        </button>
      </div>
      <div class="foot">
        <span class="hint">{{ hint }}</span>
        <div class="actions">
          <button type="button" class="link" @click="goToday">오늘</button>
          <button type="button" class="link muted" @click="clear">지우기</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.drp { position: relative; }
.trigger { display: inline-flex; align-items: center; gap: 8px; height: 36px; padding: 0 12px; border: 1px solid rgba(255,255,255,.14); border-radius: 6px; background: #10161f; color: #eef2f6; font: inherit; font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; cursor: pointer; white-space: nowrap; }
.trigger svg { width: 15px; height: 15px; fill: none; stroke: rgba(238,242,246,.55); stroke-width: 1.4; stroke-linecap: round; }
.trigger .placeholder { color: rgba(238,242,246,.5); }
.trigger.active { border-color: rgba(111,211,230,.6); }
.trigger.active svg { stroke: #6fd3e6; }
.trigger.open { border-color: #6fd3e6; box-shadow: 0 0 0 2px rgba(111,211,230,.18); }

.popover { position: absolute; top: calc(100% + 8px); left: 0; z-index: 50; width: 300px; padding: 14px; border: 1px solid rgba(255,255,255,.12); border-radius: 12px; background: #151c26; box-shadow: 0 18px 40px rgba(0,0,0,.55); }
.head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.head strong { font-size: 15px; font-weight: 800; color: #eef2f6; }
.nav { width: 36px; height: 36px; border: 0; border-radius: 8px; background: rgba(255,255,255,.05); color: #eef2f6; font-size: 20px; line-height: 1; cursor: pointer; }
.nav:active { background: rgba(255,255,255,.14); }

.grid { display: grid; grid-template-columns: repeat(7, 1fr); row-gap: 2px; }
.weekdays { margin-bottom: 4px; }
.weekdays span { padding: 4px 0; color: rgba(238,242,246,.45); font-size: 11px; font-weight: 700; text-align: center; }
.weekdays .sun { color: rgba(255,130,120,.75); }
.weekdays .sat { color: rgba(120,170,255,.75); }

.day { position: relative; height: 38px; padding: 0; border: 0; background: transparent; color: #eef2f6; font: inherit; font-size: 13px; font-variant-numeric: tabular-nums; cursor: pointer; }
.day span { position: relative; z-index: 1; display: inline-grid; place-items: center; width: 34px; height: 34px; border-radius: 50%; }
.day.sun { color: #ff9a90; }
.day.sat { color: #8fb8ff; }
.day.outside { color: rgba(238,242,246,.22); }
.day.today span { box-shadow: inset 0 0 0 1px rgba(240,192,74,.7); }
.day:active span { background: rgba(255,255,255,.1); }
.day.inRange { background: rgba(111,211,230,.14); }
.day.start:not(.end) { background: linear-gradient(90deg, transparent 50%, rgba(111,211,230,.14) 50%); }
.day.end:not(.start) { background: linear-gradient(90deg, rgba(111,211,230,.14) 50%, transparent 50%); }
.day.start span, .day.end span { background: #6fd3e6; color: #0a0e14; font-weight: 800; box-shadow: none; }

.foot { display: flex; align-items: center; justify-content: space-between; margin-top: 10px; padding-top: 10px; border-top: 1px solid rgba(255,255,255,.08); }
.hint { color: rgba(238,242,246,.45); font-size: 12px; }
.actions { display: flex; gap: 4px; }
.link { height: 32px; padding: 0 10px; border: 0; border-radius: 6px; background: transparent; color: #6fd3e6; font: inherit; font-size: 13px; font-weight: 700; cursor: pointer; }
.link.muted { color: rgba(238,242,246,.6); }
.link:active { background: rgba(255,255,255,.08); }
@media (hover: hover) {
  .day:not(.start):not(.end):hover span { background: rgba(255,255,255,.07); }
  .nav:hover { background: rgba(255,255,255,.1); }
}
</style>
