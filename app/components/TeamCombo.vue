<script setup lang="ts">
// 홈/원정팀 선택용 검색형 콤보박스. 클릭만 하면(입력 없이) 전체 목록이 드롭다운으로 뜨고,
// 글자를 치면 그 글자로 "시작하는" 팀만 남는다(예: "아스" -> 아스날 등).
const props = defineProps<{
  modelValue: string
  options: { id: string; label: string }[]
  placeholder?: string
}>()
const emit = defineEmits<{ 'update:modelValue': [string]; 'select': [] }>()

const query = ref('')
const typed = ref(false) // 사용자가 실제로 타이핑을 시작했는지 — 아니면 전체 목록을 보여준다
const open = ref(false)
const highlighted = ref(0)
const inputEl = ref<HTMLInputElement | null>(null)
const rootEl = ref<HTMLElement | null>(null)

function labelFor(id: string) {
  return props.options.find(o => o.id === id)?.label ?? ''
}
watch(() => props.modelValue, id => { query.value = labelFor(id) }, { immediate: true })
watch(() => props.options, () => { query.value = labelFor(props.modelValue) })

const filtered = computed(() => {
  if (!typed.value || !query.value.trim()) return props.options
  const q = query.value.trim().toLowerCase()
  return props.options.filter(o => o.label.toLowerCase().startsWith(q))
})

function onFocus() {
  open.value = true
  highlighted.value = 0
  inputEl.value?.select()
}
function onInput() {
  typed.value = true
  open.value = true
  highlighted.value = 0
}
function choose(id: string) {
  emit('update:modelValue', id)
  query.value = labelFor(id)
  typed.value = false
  open.value = false
  emit('select')
}
function onBlur() {
  // 옵션 클릭이 blur보다 먼저 처리되도록 살짝 늦춘다.
  setTimeout(() => {
    open.value = false
    // 고른 적 없는(또는 무효한) 글자를 쳐놓고 벗어나면 원래 선택값 라벨로 되돌린다.
    query.value = labelFor(props.modelValue)
    typed.value = false
  }, 150)
}
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'ArrowDown') { e.preventDefault(); open.value = true; highlighted.value = Math.min(highlighted.value + 1, filtered.value.length - 1) }
  else if (e.key === 'ArrowUp') { e.preventDefault(); highlighted.value = Math.max(highlighted.value - 1, 0) }
  else if (e.key === 'Enter') { e.preventDefault(); const opt = filtered.value[highlighted.value]; if (opt) choose(opt.id) }
  else if (e.key === 'Escape') { open.value = false; inputEl.value?.blur() }
}
</script>

<template>
  <div ref="rootEl" class="teamCombo">
    <input
      ref="inputEl" v-model="query" :placeholder="placeholder ?? '선택'" autocomplete="off"
      @focus="onFocus" @input="onInput" @blur="onBlur" @keydown="onKeydown"
    >
    <ul v-if="open" class="comboList">
      <li v-if="!filtered.length" class="comboEmpty">일치하는 팀 없음</li>
      <li
        v-for="(o, i) in filtered" :key="o.id" class="comboItem"
        :class="{ hi: i === highlighted, sel: o.id === modelValue }"
        @mousedown.prevent="choose(o.id)" @mouseenter="highlighted = i"
      >{{ o.label }}</li>
    </ul>
  </div>
</template>

<style scoped>
.teamCombo{position:relative}
.teamCombo input{width:100%;box-sizing:border-box;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.15);border-radius:4px;color:#fff;padding:5px 7px;font-size:11px}
.comboList{position:absolute;z-index:20;top:calc(100% + 2px);left:0;right:0;max-height:180px;overflow-y:auto;margin:0;padding:2px;list-style:none;background:#20242b;border:1px solid #4a5563;border-radius:4px;box-shadow:0 6px 18px rgba(0,0,0,.4)}
.comboItem{padding:5px 8px;font-size:11px;color:#eee;border-radius:3px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.comboItem.hi{background:rgba(240,180,41,.18)}
.comboItem.sel{color:#f0b429;font-weight:700}
.comboEmpty{padding:6px 8px;font-size:11px;color:#77818d}
</style>
