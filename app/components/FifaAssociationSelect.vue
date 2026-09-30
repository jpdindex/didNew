<script setup lang="ts">
import { FIFA_ASSOCIATIONS, type FifaAssociation } from '~/data/fifaAssociations'

const props = defineProps<{
  modelValue: string
  disabled?: boolean
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

function labelOf(item: FifaAssociation): string {
  return `${item.nameKr} (${item.code})`
}

function itemByCode(code: string): FifaAssociation | undefined {
  return FIFA_ASSOCIATIONS.find(item => item.code === code.toUpperCase())
}

function displayValue(code: string): string {
  const item = itemByCode(code)
  return item ? labelOf(item) : code
}

const results = computed(() => {
  const term = normalize(query.value)
  if (!term) return FIFA_ASSOCIATIONS
  return FIFA_ASSOCIATIONS.filter(item => {
    const targets = [item.code, item.nameKr, item.nameEn, ...(item.aliases ?? [])]
    return targets.some(value => normalize(value).includes(term))
  })
})

watch(() => props.modelValue, code => {
  if (!open.value) query.value = displayValue(code || '')
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

function selectItem(item: FifaAssociation) {
  emit('update:modelValue', item.code)
  query.value = labelOf(item)
  open.value = false
}

function closeAndNormalize() {
  window.setTimeout(() => {
    if (!open.value) return
    const term = normalize(query.value)
    const exact = FIFA_ASSOCIATIONS.find(item =>
      [item.code, item.nameKr, item.nameEn, labelOf(item), ...(item.aliases ?? [])]
        .some(value => normalize(value) === term),
    )
    if (exact) selectItem(exact)
    else query.value = displayValue(props.modelValue || '')
    open.value = false
  }, 0)
}

function keydown(event: KeyboardEvent) {
  // 한글 조합을 확정하는 Enter를 국가 선택 Enter로 처리하지 않는다.
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
    const item = results.value[activeIndex.value]
    if (item) selectItem(item)
  } else if (event.key === 'Escape') {
    open.value = false
    query.value = displayValue(props.modelValue || '')
    inputRef.value?.blur()
  }
}
</script>

<template>
  <div class="associationSelect">
    <input
      ref="inputRef"
      :value="query"
      :disabled="disabled"
      autocomplete="off"
      placeholder="국가명 또는 FIFA 코드 검색"
      @focus="focusInput"
      @input="inputQuery"
      @compositionupdate="inputComposingQuery"
      @compositionend="inputQuery"
      @keydown="keydown"
      @blur="closeAndNormalize"
    >
    <div v-if="open && !disabled" class="associationMenu">
      <button
        v-for="(item, index) in results"
        :key="item.code"
        type="button"
        :class="{ active: index === activeIndex }"
        @mousedown.prevent="selectItem(item)"
      >
        <span>{{ item.nameKr }}</span>
        <small>{{ item.nameEn }}</small>
        <b>{{ item.code }}</b>
      </button>
      <div v-if="!results.length" class="associationEmpty">검색 결과가 없습니다.</div>
    </div>
  </div>
</template>

<style scoped>
.associationSelect{position:relative;width:100%}
.associationSelect>input{width:100%;height:34px;box-sizing:border-box;margin:0;padding:0 9px;border:1px solid #3b4654;border-radius:4px;background:#171d27;color:#fff;font:inherit}
.associationSelect>input:disabled{color:#7f8996;background:#171c23;border-color:#2a323d;cursor:not-allowed}
.associationMenu{position:absolute;z-index:40;top:calc(100% + 3px);left:0;right:0;max-height:230px;overflow-y:auto;border:1px solid #596474;border-radius:4px;background:#202731;box-shadow:0 12px 28px rgba(0,0,0,.48)}
.associationMenu button{display:grid;grid-template-columns:minmax(100px,.9fr) minmax(140px,1.4fr) 42px;gap:10px;align-items:center;width:100%;min-height:38px;padding:7px 10px;border:0;border-top:1px solid rgba(255,255,255,.06);background:transparent;color:#fff;text-align:left;cursor:pointer}
.associationMenu button:first-child{border-top:0}
.associationMenu button:hover,.associationMenu button.active{background:#286fd1}
.associationMenu span{font-weight:700}
.associationMenu small{overflow:hidden;color:#aeb9c7;text-overflow:ellipsis;white-space:nowrap}
.associationMenu button:hover small,.associationMenu button.active small{color:#e8f1ff}
.associationMenu b{color:#f0b429;text-align:right}
.associationMenu button:hover b,.associationMenu button.active b{color:#fff}
.associationEmpty{padding:14px;color:#aab3be;text-align:center}
</style>
