<script setup lang="ts">
// APK 원본 좌표계(1280×800)는 유지하고, 실제 태블릿 뷰포트에 맞춰서만 축소한다.
// 화면 내부 클릭은 각 요소의 실제 표시 영역을 기준으로 좌표를 환산하므로 기록 좌표는 변하지 않는다.
const canvasScale = ref(1)

function fitCanvas() {
  // PWA/browser chrome can make innerHeight larger than the actually visible
  // area. Use visualViewport so the fixed canvas is always fully visible.
  const viewport = window.visualViewport
  const width = viewport?.width ?? window.innerWidth
  const height = viewport?.height ?? window.innerHeight
  canvasScale.value = Math.min(width / 1280, height / 800)
}

onMounted(() => {
  fitCanvas()
  window.addEventListener('resize', fitCanvas)
  window.visualViewport?.addEventListener('resize', fitCanvas)
})

onUnmounted(() => window.removeEventListener('resize', fitCanvas))
onUnmounted(() => window.visualViewport?.removeEventListener('resize', fitCanvas))
</script>

<template>
  <main class="canvasHost">
    <div class="apkCanvas" :style="{ '--canvas-scale': String(canvasScale) }">
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

.canvasHost {
  width: 100vw;
  height: 100dvh;
  display: grid;
  place-items: center;
  overflow: hidden;
}

.apkCanvas {
  width: 1280px;
  height: 800px;
  flex: 0 0 auto;
  transform: scale(var(--canvas-scale));
  transform-origin: center center;
}
</style>
