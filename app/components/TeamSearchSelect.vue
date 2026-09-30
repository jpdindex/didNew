<script setup lang="ts">
interface TeamOption {
  id: string
  name?: string
  nameKr?: string
  nameFull?: string
  nameShort?: string
}

interface SearchOption {
  id: string
  label: string
  secondary: string
  special?: boolean
}

const OTHER_LEAGUE = '__OTHER_LEAGUE__'

const props = defineProps<{
  modelValue: string
  teams: TeamOption[]
  disabled?: boolean
  allowOtherLeague?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const inputRef = ref<HTMLInputElement | null>(null)
const query = ref('')
const open = ref(false)
const activeIndex = ref(0)

function normalize(value: string): string {
  return value
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .normalize('NFC')
    .toLowerCase()
    .replace(/[^a-z0-9가-힣]/g, '')
}

const options = computed<SearchOption[]>(() => {
  const teamOptions = props.teams
    .map(team => ({
      id: team.id,
      label: team.nameKr || team.name || team.id,
      secondary: team.nameFull || team.name || team.nameShort || team.id,
    }))
    .sort((a, b) => a.label.localeCompare(b.label, 'ko'))
  if (props.allowOtherLeague) {
    teamOptions.push({ id: OTHER_LEAGUE, label: '타 리그', secondary: '추적하지 않는 팀 또는 리그', special: true })
  }
  return teamOptions
})

function optionById(id: string): SearchOption | undefined {
  return options.value.find(option => option.id === id)
}

function displayValue(id: string): string {
  const option = optionById(id)
  return option ? option.label : id
}

const results = computed(() => {
  const term = normalize(query.value)
  if (!term) return options.value
  return options.value.filter(option =>
    [option.label, option.secondary, option.id].some(value => normalize(value).includes(term)),
  )
})

watch([() => props.modelValue, options], ([id]) => {
  if (!open.value) query.value = displayValue(id || '')
}, { immediate: true })

function focusInput() {
  if (props.disabled) return
  open.value = true
  activeIndex.value = 0
  nextTick(() => inputRef.value?.select())
}

function inputQuery(event: Event) {
  query.value = (event.target as HTMLInputElement).value
  emit('update:modelValue', '')
  open.value = true
  activeIndex.value = 0
}

function inputComposingQuery(event: CompositionEvent) {
  const input = event.target as HTMLInputElement
  window.requestAnimationFrame(() => {
    query.value = input.value
    emit('update:modelValue', '')
    open.value = true
    activeIndex.value = 0
  })
}

function selectOption(option: SearchOption) {
  emit('update:modelValue', option.id)
  query.value = option.label
  open.value = false
}

function closeAndNormalize() {
  window.setTimeout(() => {
    if (!open.value) return
    const term = normalize(query.value)
    const exact = options.value.find(option =>
      [option.label, option.secondary, option.id].some(value => normalize(value) === term),
    )
    if (exact) selectOption(exact)
    else query.value = displayValue(props.modelValue || '')
    open.value = false
  }, 0)
}

function keydown(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return
  if (!open.value && event.key !== 'Escape') open.value = true
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    activeIndex.value = Math.min(activeIndex.value + 1, results.value.length - 1)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    activeIndex.value = Math.max(activeIndex.value - 1, 0)
  } else if (event.key === 'Enter') {
    event.preventDefault()
    const option = results.value[activeIndex.value]
    if (option) selectOption(option)
  } else if (event.key === 'Escape') {
    open.value = false
    query.value = displayValue(props.modelValue || '')
    inputRef.value?.blur()
  }
}
</script>

<template>
  <div class="teamSearchSelect">
    <input
      ref="inputRef"
      :value="query"
      :disabled="disabled"
      autocomplete="off"
      placeholder="팀명 또는 팀 ID 검색"
      @focus="focusInput"
      @input="inputQuery"
      @compositionupdate="inputComposingQuery"
      @compositionend="inputQuery"
      @keydown="keydown"
      @blur="closeAndNormalize"
    >
    <div v-if="open && !disabled" class="teamMenu">
      <button
        v-for="(option, index) in results"
        :key="option.id"
        type="button"
        :class="{ active: index === activeIndex, special: option.special }"
        @mousedown.prevent="selectOption(option)"
      >
        <span>{{ option.label }}</span>
        <small>{{ option.secondary }}</small>
        <b>{{ option.special ? '이동' : option.id }}</b>
      </button>
      <div v-if="!results.length" class="teamEmpty">서버에 등록된 팀이 없습니다.</div>
    </div>
  </div>
</template>

<style scoped>
.teamSearchSelect{position:relative;width:100%}
.teamSearchSelect>input{width:100%;height:34px;box-sizing:border-box;margin:0;padding:0 9px;border:1px solid #3b4654;border-radius:4px;background:#171d27;color:#fff;font:inherit}
.teamSearchSelect>input:disabled{color:#7f8996;background:#171c23;border-color:#2a323d;cursor:not-allowed}
.teamMenu{position:absolute;z-index:40;top:calc(100% + 3px);left:0;right:0;max-height:230px;overflow-y:auto;border:1px solid #596474;border-radius:4px;background:#202731;box-shadow:0 12px 28px rgba(0,0,0,.48)}
.teamMenu button{display:grid;grid-template-columns:minmax(110px,.9fr) minmax(140px,1.4fr) 84px;gap:10px;align-items:center;width:100%;min-height:38px;padding:7px 10px;border:0;border-top:1px solid rgba(255,255,255,.06);background:transparent;color:#fff;text-align:left;cursor:pointer}
.teamMenu button:first-child{border-top:0}
.teamMenu button:hover,.teamMenu button.active{background:#286fd1}
.teamMenu button.special{border-top-color:rgba(240,180,41,.45);color:#f0b429}
.teamMenu span{font-weight:700}
.teamMenu small{overflow:hidden;color:#aeb9c7;text-overflow:ellipsis;white-space:nowrap}
.teamMenu button:hover small,.teamMenu button.active small{color:#e8f1ff}
.teamMenu b{overflow:hidden;color:#f0b429;text-align:right;text-overflow:ellipsis;white-space:nowrap}
.teamMenu button:hover b,.teamMenu button.active b{color:#fff}
.teamEmpty{padding:14px;color:#aab3be;text-align:center}
</style>

