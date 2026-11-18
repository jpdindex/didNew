<script setup lang="ts">
// Dark-themed replacement for <select>. The native popup cannot be styled and
// ignores the scaled 1280×800 canvas, so the list is rendered in-page instead.
type Option = { value: string; label: string }

const props = defineProps<{
  modelValue: string
  options: Option[]
  placeholder?: string
  disabled?: boolean
  label?: string
}>()
const emit = defineEmits<{ 'update:modelValue': [string]; change: [string] }>()

const open = ref(false)
const active = ref(0)
const root = ref<HTMLElement>()
const list = ref<HTMLElement>()
const listId = useId()

const selected = computed(() => props.options.find(option => option.value === props.modelValue))

function scrollActive() {
  void nextTick(() => list.value?.children[active.value]?.scrollIntoView({ block: 'nearest' }))
}

function toggle() {
  if (props.disabled || !props.options.length) return
  open.value = !open.value
  if (open.value) {
    active.value = Math.max(0, props.options.findIndex(option => option.value === props.modelValue))
    scrollActive()
  }
}

function choose(option: Option) {
  open.value = false
  if (option.value === props.modelValue) return
  emit('update:modelValue', option.value)
  emit('change', option.value)
}

function onKeydown(event: KeyboardEvent) {
  if (props.disabled) return
  if (!open.value) {
    if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
      event.preventDefault()
      toggle()
    }
    return
  }
  if (event.key === 'Escape' || event.key === 'Tab') open.value = false
  else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    const step = event.key === 'ArrowDown' ? 1 : -1
    active.value = Math.min(props.options.length - 1, Math.max(0, active.value + step))
    scrollActive()
  } else if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    const option = props.options[active.value]
    if (option) choose(option)
  }
}

function closeOutside(event: PointerEvent) {
  if (open.value && !root.value?.contains(event.target as Node)) open.value = false
}
onMounted(() => document.addEventListener('pointerdown', closeOutside, true))
onBeforeUnmount(() => document.removeEventListener('pointerdown', closeOutside, true))
</script>

<template>
  <div ref="root" class="filterSelect" :class="{ open, disabled }">
    <button
      type="button"
      class="trigger"
      role="combobox"
      :aria-label="label"
      :aria-expanded="open"
      :aria-controls="listId"
      :disabled="disabled || !options.length"
      @click="toggle"
      @keydown="onKeydown"
    >
      <span class="value" :class="{ placeholder: !selected }">{{ selected?.label ?? placeholder ?? '선택' }}</span>
      <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 4.5 6 8l3.5-3.5" /></svg>
    </button>
    <ul v-show="open" :id="listId" ref="list" class="menu" role="listbox">
      <li
        v-for="(option, index) in options"
        :key="option.value"
        role="option"
        :aria-selected="option.value === modelValue"
        :class="{ active: index === active, selected: option.value === modelValue }"
        @pointerenter="active = index"
        @click="choose(option)"
      >
        <span>{{ option.label }}</span>
        <svg v-if="option.value === modelValue" viewBox="0 0 12 12" aria-hidden="true"><path d="m2.5 6.2 2.3 2.3 4.7-5" /></svg>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.filterSelect { position: relative; min-width: 0; }
.trigger { display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%; height: 40px; padding: 0 12px; box-sizing: border-box; border: 1px solid rgba(255,255,255,.14); border-radius: 6px; background: #141a22; color: #edf3f8; font: inherit; font-size: 14px; text-align: left; cursor: pointer; }
.trigger:focus-visible { outline: 2px solid rgba(103,204,225,.65); outline-offset: 1px; }
.trigger:disabled { opacity: .5; cursor: default; }
.open .trigger { border-color: rgba(103,204,225,.7); }
.value { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.value.placeholder { color: rgba(255,255,255,.45); }
.trigger svg { width: 12px; height: 12px; flex: none; fill: none; stroke: rgba(255,255,255,.6); stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; transition: transform .12s; }
.open .trigger svg { transform: rotate(180deg); }
.menu { position: absolute; z-index: 30; top: calc(100% + 6px); left: 0; right: 0; max-height: 320px; margin: 0; padding: 6px; overflow-y: auto; list-style: none; box-sizing: border-box; border: 1px solid rgba(255,255,255,.12); border-radius: 8px; background: #161d27; box-shadow: 0 12px 28px rgba(0,0,0,.45); }
.menu li { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-height: 40px; padding: 0 10px; border-radius: 5px; color: rgba(237,243,248,.86); font-size: 14px; cursor: pointer; }
.menu li span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.menu li.active { background: rgba(255,255,255,.06); }
.menu li:active { background: rgba(103,204,225,.2); }
.menu li.selected { color: #8ce6f5; font-weight: 750; }
.menu li svg { width: 14px; height: 14px; flex: none; fill: none; stroke: #8ce6f5; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
</style>
