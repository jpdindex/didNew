<script setup lang="ts">
import {
  Box3, BufferGeometry, Group, Line, LineBasicMaterial, Points, PointsMaterial,
  Mesh, MeshBasicMaterial, PerspectiveCamera, Scene, SphereGeometry, Vector3, WebGLRenderer, type Material,
} from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'

type Point = { x: number; y: number; z: number }
const props = defineProps<{
  points: Point[]; measuredPoints: Point[]; goalPoint: Point | null; goalX: number | null; goalCenterY: number | null
  flightTime: number | null
}>()
const container = ref<HTMLElement>()
const failed = ref(false)
const playback = ref<'idle' | 'playing' | 'ended'>('idle')
const playTrajectory = ref<() => void>()
let dispose: (() => void) | undefined

function scenePoint(point: Point) {
  // Match the screen-axis convention enabled in the ShotX viewer.
  return new Vector3(point.x, point.z, (props.goalCenterY ?? 34) - point.y)
}

function renderTrajectory() {
  dispose?.()
  dispose = undefined
  failed.value = false
  playback.value = 'idle'
  playTrajectory.value = undefined
  const element = container.value
  const points = props.points.filter(point => [point.x, point.y, point.z].every(Number.isFinite)).map(scenePoint)
  const measured = props.measuredPoints.filter(point => [point.x, point.y, point.z].every(Number.isFinite)).map(scenePoint)
  const framingPoints = points.length >= 2 ? points : measured
  if (!element || framingPoints.length < 2) return
  let renderer: WebGLRenderer
  try {
    renderer = new WebGLRenderer({ antialias: true, alpha: true })
  } catch {
    failed.value = true
    return
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  element.appendChild(renderer.domElement)
  const scene = new Scene()
  const camera = new PerspectiveCamera(42, 1, 0.1, 800)
  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enablePan = true
  controls.maxPolarAngle = Math.PI / 2
  const plot = new Group()
  scene.add(plot)

  function line(vertices: Vector3[], color: number) {
    plot.add(new Line(new BufferGeometry().setFromPoints(vertices), new LineBasicMaterial({ color })))
  }

  if (points.length >= 2) line(points, 0x70f0bd)
  if (measured.length) {
    plot.add(new Points(new BufferGeometry().setFromPoints(measured), new PointsMaterial({ color: 0xd6e4e8, size: .28 })))
  }
  const x = props.goalX ?? props.goalPoint?.x ?? (framingPoints.at(-1)!.x > framingPoints[0]!.x ? 105 : 0)
  const inward = x > 52.5 ? -1 : 1
  line([
    new Vector3(x, 0, -3.66), new Vector3(x, 2.44, -3.66),
    new Vector3(x, 2.44, 3.66), new Vector3(x, 0, 3.66),
  ], 0xeaf2ff)
  if (props.goalPoint) {
    const point = scenePoint(props.goalPoint)
    plot.add(new Points(new BufferGeometry().setFromPoints([point]), new PointsMaterial({ color: 0xffca77, size: .48 })))
  }

  const bounds = new Box3().setFromObject(plot)
  const center = bounds.getCenter(new Vector3())
  const size = Math.max(8, bounds.getSize(new Vector3()).length())
  line([new Vector3(x, 0, -34), new Vector3(x, 0, 34)], 0x557470)
  for (const [depth, width] of [[5.5, 18.32], [16.5, 40.32]] as const) {
    const edge = x + depth * inward
    line([
      new Vector3(x, 0, -width / 2), new Vector3(edge, 0, -width / 2),
      new Vector3(edge, 0, width / 2), new Vector3(x, 0, width / 2),
    ], 0x557470)
  }
  controls.target.copy(center)
  controls.minDistance = size * 0.25
  controls.maxDistance = size * 5

  function redraw() {
    renderer.render(scene, camera)
  }

  const ball = new Mesh(new SphereGeometry(.27, 16, 12), new MeshBasicMaterial({ color: 0xfff8db }))
  ball.visible = false
  scene.add(ball)
  let animationFrame = 0
  const lastSample = framingPoints.length - 1
  const durationMs = props.flightTime != null && Number.isFinite(props.flightTime) && props.flightTime > 0
    ? props.flightTime * 1000 : 1000

  playTrajectory.value = () => {
    if (playback.value === 'playing') return
    playback.value = 'playing'
    ball.position.copy(framingPoints[0]!)
    ball.visible = true
    redraw()
    const startedAt = performance.now()

    function animate(now: number) {
      const progress = Math.max(0, Math.min((now - startedAt) / durationMs, 1))
      const sample = progress * lastSample
      const first = Math.min(Math.floor(sample), lastSample - 1)
      ball.position.lerpVectors(framingPoints[first]!, framingPoints[first + 1]!, sample - first)
      redraw()
      if (progress < 1) {
        animationFrame = requestAnimationFrame(animate)
      } else {
        playback.value = 'ended'
      }
    }

    animationFrame = requestAnimationFrame(animate)
  }

  function resetCamera() {
    camera.position.copy(center).addScaledVector(new Vector3(inward * .58, .85, 1.02), size * (camera.aspect < 1.3 ? 1.3 : 1))
    controls.update()
  }

  const resize = new ResizeObserver(() => {
    const width = element.clientWidth
    const height = element.clientHeight
    if (!width || !height) return
    camera.aspect = width / height
    camera.updateProjectionMatrix()
    renderer.setSize(width, height)
    redraw()
  })
  controls.addEventListener('change', redraw)
  resetCamera()
  resize.observe(element)
  dispose = () => {
    cancelAnimationFrame(animationFrame)
    resize.disconnect()
    controls.dispose()
    ball.geometry.dispose()
    ball.material.dispose()
    plot.children.forEach(child => {
      const item = child as Line<BufferGeometry, Material> | Points<BufferGeometry, Material>
      item.geometry.dispose()
      item.material.dispose()
    })
    renderer.dispose()
    renderer.domElement.remove()
  }
}

onMounted(renderTrajectory)
watch(() => [props.points, props.measuredPoints, props.goalPoint, props.goalX, props.goalCenterY, props.flightTime], renderTrajectory, { flush: 'post' })
onBeforeUnmount(() => dispose?.())
</script>

<template>
  <div v-show="!failed" class="viewer-frame">
    <div ref="container" class="viewer" role="img" aria-label="복원된 슈팅 궤적과 골문 통과 위치" />
    <button v-if="playTrajectory" type="button" class="trajectory-play" :disabled="playback === 'playing'" @click="playTrajectory()">
      <span v-if="playback !== 'playing'" aria-hidden="true">▶</span>
      {{ playback === 'playing' ? '재생 중…' : playback === 'ended' ? '다시 재생' : '궤적 재생' }}
    </button>
  </div>
  <p v-if="failed">이 환경에서는 3D 궤적을 표시할 수 없습니다.</p>
</template>

<style scoped>
.viewer { width: 100%; height: 15rem; border-radius: .6rem; overflow: hidden; background: #0b171c; }
.viewer-frame { position: relative; }
.trajectory-play { position: absolute; inset: 10px 10px auto auto; min-height: 40px; padding: 7px 12px; border: 1px solid #52746b; border-radius: 8px; background: #142825ee; color: #b6f2d7; font: inherit; font-size: 13px; font-weight: 700; cursor: pointer; }
.trajectory-play span { margin-right: 3px; font-size: 10px; }
.trajectory-play:disabled { color: #d5e0db; cursor: default; }
.trajectory-play:focus-visible { outline: 2px solid #a4e7c9; outline-offset: 2px; }
</style>
