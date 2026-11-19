<script setup lang="ts" generic="T extends string">
const props = defineProps<{ options: { value: T; label: string }[]; ariaLabel?: string; width?: string }>()
const model = defineModel<T>({ required: true })
const emit = defineEmits<{ change: [value: T] }>()

const root = ref<HTMLElement | null>(null)
const open = ref(false)

const current = computed(() => props.options.find(option => option.value === model.value))

function choose(value: T) {
  open.value = false
  if (value === model.value) return
  model.value = value
  emit('change', value)
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
  <div ref="root" class="sm" :style="{ width: width ?? '124px' }">
    <button
      type="button"
      class="trigger"
      :class="{ open }"
      :aria-label="ariaLabel"
      aria-haspopup="listbox"
      :aria-expanded="open"
      @click="open = !open"
    >
      <span>{{ current?.label ?? '' }}</span>
      <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 4.5 6 7.5 9 4.5" /></svg>
    </button>
    <ul v-if="open" class="menu" role="listbox" :aria-label="ariaLabel">
      <li v-for="option in options" :key="option.value">
        <button
          type="button"
          role="option"
          class="option"
          :class="{ selected: option.value === model }"
          :aria-selected="option.value === model"
          @click="choose(option.value)"
        >
          <span>{{ option.label }}</span>
          <svg v-if="option.value === model" viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.2 5 8.5 9.5 3.5" /></svg>
        </button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.sm { position: relative; flex: none; }
.trigger { display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%; height: 36px; padding: 0 10px 0 12px; border: 1px solid rgba(255,255,255,.14); border-radius: 6px; background: #10161f; color: #eef2f6; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer; white-space: nowrap; }
.trigger span { overflow: hidden; text-overflow: ellipsis; }
.trigger svg { flex: none; width: 12px; height: 12px; fill: none; stroke: rgba(238,242,246,.55); stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; transition: transform .15s; }
.trigger.open { border-color: #6fd3e6; box-shadow: 0 0 0 2px rgba(111,211,230,.18); }
.trigger.open svg { transform: rotate(180deg); stroke: #6fd3e6; }

.menu { position: absolute; top: calc(100% + 6px); left: 0; z-index: 50; min-width: 100%; max-height: 320px; overflow-y: auto; overscroll-behavior: contain; margin: 0; padding: 6px; list-style: none; border: 1px solid rgba(255,255,255,.12); border-radius: 10px; background: #151c26; box-shadow: 0 18px 40px rgba(0,0,0,.55); box-sizing: border-box; }
.option { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; min-height: 40px; padding: 0 10px; border: 0; border-radius: 6px; background: transparent; color: rgba(238,242,246,.85); font: inherit; font-size: 13px; font-weight: 600; text-align: left; white-space: nowrap; cursor: pointer; }
.option svg { flex: none; width: 12px; height: 12px; fill: none; stroke: #6fd3e6; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.option.selected { background: rgba(111,211,230,.12); color: #6fd3e6; font-weight: 800; }
.option:active { background: rgba(255,255,255,.12); }
@media (hover: hover) { .option:not(.selected):hover { background: rgba(255,255,255,.06); } }
</style>
