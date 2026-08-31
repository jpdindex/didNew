<script setup lang="ts">
const menus = [
  { label: '팀 관리', desc: '팀 추가/수정', to: '/manage/teams', ready: false },
  { label: '선수 관리', desc: '선수 정보 + 이적시장', to: '/manage/players', ready: false },
  { label: '경기 일정 관리', desc: '경기 일정 등록/수정', to: '/manage/schedules', ready: false }
]
</script>

<template>
  <div class="page">
    <div class="bg" />

    <div class="frame">
      <div class="topBar">
        <div class="title">데이터 관리</div>
        <NuxtLink class="backBtn" to="/schedule">← 경기 선택으로</NuxtLink>
      </div>

      <div class="grid">
        <component
          :is="menu.ready ? 'NuxtLink' : 'div'"
          v-for="menu in menus"
          :key="menu.label"
          :to="menu.ready ? menu.to : undefined"
          class="card"
          :class="{ disabled: !menu.ready }"
        >
          <div class="label">{{ menu.label }}</div>
          <div class="desc">{{ menu.desc }}</div>
          <div v-if="!menu.ready" class="badge">준비 중</div>
        </component>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page {
  box-sizing: border-box;
  width: 1280px;
  height: 800px;
  display: grid;
  place-items: center;
  position: relative;
  overflow: hidden;
  background: #0b0f17;
}

.bg {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(1200px 500px at 50% 35%, rgba(255,255,255,0.08), transparent 60%),
    linear-gradient(180deg, rgba(0,0,0,0.55), rgba(0,0,0,0.75));
}

.frame {
  box-sizing: border-box;
  position: relative;
  width: 1280px;
  height: 800px;
  border-radius: 0;
  background: rgba(10, 14, 22, 0.75);
  border: 1px solid rgba(255,255,255,0.08);
  box-shadow: 0 18px 60px rgba(0,0,0,0.55);
  backdrop-filter: blur(8px);
  padding: 24px;
}

.topBar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 16px;
  margin-bottom: 20px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.title { color: rgba(255,255,255,0.85); font-weight: 700; letter-spacing: 0.02em; }

.backBtn {
  color: rgba(255,255,255,0.55);
  font-size: 13px;
  text-decoration: none;
}
.backBtn:hover { color: #fff; }

.grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}

.card {
  position: relative;
  display: block;
  padding: 20px;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.1);
  background: rgba(255,255,255,0.03);
  text-decoration: none;
  cursor: pointer;
}
.card:hover { border-color: rgba(0,217,255,0.4); background: rgba(0,217,255,0.05); }
.card.disabled { cursor: not-allowed; opacity: 0.5; }
.card.disabled:hover { border-color: rgba(255,255,255,0.1); background: rgba(255,255,255,0.03); }

.label { color: #fff; font-weight: 700; font-size: 15px; margin-bottom: 6px; }
.desc { color: rgba(255,255,255,0.55); font-size: 13px; }

.badge {
  position: absolute;
  top: 12px;
  right: 12px;
  font-size: 11px;
  color: rgba(255,255,255,0.4);
  border: 1px solid rgba(255,255,255,0.15);
  border-radius: 4px;
  padding: 2px 6px;
}

</style>
