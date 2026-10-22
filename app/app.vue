<script setup lang="ts">
// APK 원본 좌표계(1280×800)는 유지하고, 실제 태블릿 뷰포트에 맞춰서만 축소한다.
// 화면 내부 클릭은 각 요소의 실제 표시 영역을 기준으로 좌표를 환산하므로 기록 좌표는 변하지 않는다.
const canvasScale = ref(1)
const canvasOffsetY = ref(0)
const canvasEl = ref<HTMLElement | null>(null)
// 키보드가 없을 때의 뷰포트 크기. 키보드가 올라와도 배율은 이 기준으로 유지한다.
let baseWidth = 0
let baseHeight = 0
// 이 값보다 보이는 높이가 줄어들면 가상 키보드가 열린 것으로 본다.
const KEYBOARD_MIN_SHRINK = 120

function focusedTextInput(): HTMLElement | null {
  const element = document.activeElement
  const isText = element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || Boolean(element?.isContentEditable)
  return isText ? element as HTMLElement : null
}

function fitCanvas() {
  const viewport = window.visualViewport
  const width = viewport?.width ?? window.innerWidth
  const height = viewport?.height ?? window.innerHeight
  const top = viewport?.offsetTop ?? 0

  // 가로 폭이 바뀌면(회전 등) 기준 크기를 새로 잡는다. 키보드 열림/닫힘은 높이만 변한다.
  const widthChanged = Math.abs(width - baseWidth) > 50
  const keyboardOpen = baseHeight > 0 && !widthChanged && height < baseHeight - KEYBOARD_MIN_SHRINK
  if (!keyboardOpen) {
    baseWidth = width
    baseHeight = height
  }

  // 키보드가 열려도 화면을 줄이지 않는다. 줄이면 로그인 입력칸이 너무 작아진다.
  const scale = Math.min(baseWidth / 1280, baseHeight / 800)
  canvasScale.value = scale

  const canvasHeight = 800 * scale
  const input = keyboardOpen ? focusedTextInput() : null
  const canvas = canvasEl.value
  if (!input || !canvas) {
    // 키보드가 없으면 기존처럼 위쪽 기준 + 세로 여백.
    canvasOffsetY.value = keyboardOpen ? top : Math.max(0, (height - canvasHeight) / 2)
    return
  }

  // 포커스된 입력칸의 캔버스 안 위치(원본 1280×800 좌표)를 구해서,
  // 키보드 위 보이는 영역의 가운데쯤에 오도록 캔버스를 위아래로 옮긴다.
  const canvasRect = canvas.getBoundingClientRect()
  const realScale = canvasRect.height / 800 || scale
  const inputRect = input.getBoundingClientRect()
  const inputY = (inputRect.top + inputRect.height / 2 - canvasRect.top) / realScale
  const wanted = top + height * 0.5 - inputY * scale

  // 보이는 영역 밖으로 캔버스가 비어 보이지 않게, 위(top)~아래(top+height-canvasHeight) 사이로 제한한다.
  const minOffset = top + height - canvasHeight
  canvasOffsetY.value = canvasHeight <= height
    ? top + (height - canvasHeight) / 2
    : Math.min(top, Math.max(minOffset, wanted))
}

onMounted(() => {
  fitCanvas()
  window.addEventListener('resize', fitCanvas)
  window.visualViewport?.addEventListener('resize', fitCanvas)
  window.addEventListener('focusin', fitCanvas)
  window.addEventListener('focusout', fitCanvas)
})

onUnmounted(() => window.removeEventListener('resize', fitCanvas))
onUnmounted(() => window.visualViewport?.removeEventListener('resize', fitCanvas))
onUnmounted(() => window.removeEventListener('focusin', fitCanvas))
onUnmounted(() => window.removeEventListener('focusout', fitCanvas))
</script>

<template>
  <main class="canvasHost">
    <div ref="canvasEl" class="apkCanvas" :style="{ '--canvas-scale': String(canvasScale), marginTop: `${canvasOffsetY}px` }">
      <NuxtPage />
    </div>
  </main>
</template>

<style>
html, body, #__nuxt {
  width: 100%;
  height: 100%;
  margin: 0;
}

body {
  overflow: hidden;
  background: #0b0f17;
}

/* 기본 흰색 스크롤바 대신 어두운 화면에 맞춘 얇은 스크롤바 */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

::-webkit-scrollbar-track,
::-webkit-scrollbar-corner {
  background: transparent;
}

::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, .18);
  border-radius: 999px;
}

::-webkit-scrollbar-thumb:active {
  background: rgba(255, 255, 255, .32);
}

::-webkit-scrollbar-button {
  display: none;
}

/* Firefox는 ::-webkit-scrollbar를 모르므로 표준 속성으로 맞춘다 */
@supports not selector(::-webkit-scrollbar) {
  * {
    scrollbar-width: thin;
    scrollbar-color: rgba(255, 255, 255, .18) transparent;
  }
}

.canvasHost {
  width: 100vw;
  height: 100dvh;
  display: grid;
  justify-items: center;
  align-items: start;
  overflow: hidden;
}

.apkCanvas {
  width: 1280px;
  height: 800px;
  flex: 0 0 auto;
  transform: scale(var(--canvas-scale));
  transform-origin: top center;
}
</style>
