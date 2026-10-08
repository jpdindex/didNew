<script setup lang="ts">
const inputMenus = [
  { label: '팀 관리', desc: '팀 정보와 엠블럼을 빠르게 등록·수정', to: '/manage/teams', ready: true },
  { label: '선수 관리', desc: '선수·등번호·포지션을 한 번에 등록·수정', to: '/manage/players', ready: true },
  { label: '경기 일정 관리', desc: '경기 날짜·팀·리그 일정을 빠르게 등록·수정', to: '/manage/schedules', ready: true },
  { label: '감독 관리', desc: '감독 정보와 소속 팀을 빠르게 등록·수정', to: '/manage/coaches', ready: true }
]
const operationMenus = [
  { label: '분석관 운영 대시보드', desc: '경기·팀·주/부 역할·담당 DAP 기준으로 분석관 운영 현황 조회', to: '/manage/analyst-dashboard', ready: true },
  { label: '분석관 계정 관리', desc: '분석관 표시 이름·등급·관리자 권한 변경', to: '/manage/recorders', ready: true },
  { label: 'BASIC 기록 승인', desc: '승인 대기 Draft 검토 후 최종 RAW로 승격', to: '/manage/input-approvals', ready: true },
  { label: '기록 잠금 관리', desc: '갱신으로 잠긴 반(半) 잠금 해제', to: '/manage/locks', ready: true }
]
const dataMenus = [
  { label: 'SQL 데이터 이관', desc: '레거시 SQL 경기 데이터를 Firestore로 이관 (1회성)', to: '/manage/legacy-import', ready: true },
  { label: 'Firestore 데이터 뷰어', desc: '컬렉션 조회 + 값 수정 (삭제는 없음)', to: '/manage/data-viewer', ready: true },
  { label: '입력 데이터 (경기 기록)', desc: '경기 → 팀 → 레코드/KPI/카드 순으로 클릭만으로 조회', to: '/manage/input-data', ready: true }
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

      <div class="sectionTitle firstSection">
        <span>경기 입력 준비</span>
      </div>
      <div class="grid twoColumnGrid">
        <template v-for="menu in inputMenus" :key="menu.label">
          <NuxtLink v-if="menu.ready" :to="menu.to" class="card">
            <div class="label">{{ menu.label }}</div>
            <div class="desc">{{ menu.desc }}</div>
          </NuxtLink>
          <div v-else class="card disabled">
            <div class="label">{{ menu.label }}</div>
            <div class="desc">{{ menu.desc }}</div>
            <div class="badge">준비 중</div>
          </div>
        </template>
      </div>

      <div class="sectionTitle">
        <span>경기 기록 운영</span>
      </div>
      <div class="grid twoColumnGrid">
        <NuxtLink
          v-for="menu in operationMenus.filter(menu => menu.ready)"
          :key="menu.label"
          :to="menu.to"
          class="card"
        >
          <div class="label">{{ menu.label }}</div>
          <div class="desc">{{ menu.desc }}</div>
        </NuxtLink>

        <div
          v-for="menu in operationMenus.filter(menu => !menu.ready)"
          :key="menu.label"
          class="card disabled"
        >
          <div class="label">{{ menu.label }}</div>
          <div class="desc">{{ menu.desc }}</div>
          <div class="badge">준비 중</div>
        </div>
      </div>

      <div class="sectionTitle">
        <span>데이터 점검·이관</span>
      </div>
      <div class="grid threeColumnGrid">
        <NuxtLink
          v-for="menu in dataMenus.filter(menu => menu.ready)"
          :key="menu.label"
          :to="menu.to"
          class="card"
        >
          <div class="label">{{ menu.label }}</div>
          <div class="desc">{{ menu.desc }}</div>
        </NuxtLink>

        <div
          v-for="menu in dataMenus.filter(menu => !menu.ready)"
          :key="menu.label"
          class="card disabled"
        >
          <div class="label">{{ menu.label }}</div>
          <div class="desc">{{ menu.desc }}</div>
          <div class="badge">준비 중</div>
        </div>
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
  margin-bottom: 17px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.title {
  color: #67cce1;
  font-size: 20px;
  font-weight: 800;
  letter-spacing: 0.01em;
}

.backBtn {
  color: rgba(255,255,255,0.55);
  font-size: 13px;
  text-decoration: none;
}
.backBtn:hover { color: #fff; }

.grid {
  display: grid;
  gap: 14px 16px;
}
.twoColumnGrid {
  grid-template-columns: repeat(2, 1fr);
}
.threeColumnGrid {
  grid-template-columns: repeat(3, 1fr);
}
.sectionTitle {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin: 21px 0 11px;
  padding-top: 16px;
  border-top: 1px solid rgba(255,255,255,0.08);
  color: rgba(255,255,255,0.72);
  font-size: 14px;
  font-weight: 800;
  letter-spacing: .04em;
}
.firstSection {
  margin-top: 0;
  padding-top: 0;
  border-top: 0;
}

.card {
  position: relative;
  display: block;
  padding: 18px 20px;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.1);
  background: rgba(255,255,255,0.03);
  text-decoration: none;
  cursor: pointer;
}
.card:hover { border-color: rgba(0,217,255,0.4); background: rgba(0,217,255,0.05); }
.card.disabled { cursor: not-allowed; opacity: 0.5; }
.card.disabled:hover { border-color: rgba(255,255,255,0.1); background: rgba(255,255,255,0.03); }

.label { color: #fff; font-weight: 700; font-size: 15.5px; margin-bottom: 6px; }
.desc { color: rgba(255,255,255,0.55); font-size: 13px; line-height: 1.4; }

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
