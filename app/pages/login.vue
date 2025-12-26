<template>
  <div class="page">
    <!-- 배경(스타디움 느낌) -->
    <div class="bg" />

    <!-- 중앙 로그인 카드 -->
    <div class="card">
      <div class="brand">
        <div class="brandTop">AIMBROAD</div>
        <div class="brandSub">DIGITAL BROADCAST</div>
      </div>

      <div class="form">
        <input
          v-model="uId"
          class="input"
          type="text"
          placeholder="ID"
          autocomplete="username"
        />
        <div class="row">
          <input
            v-model="uPw"
            class="input"
            type="password"
            placeholder="PASSWORD"
            autocomplete="current-password"
            @keyup.enter="onSubmit"
          />
          <button class="lockBtn" @click="onSubmit" aria-label="Login">
            🔒
          </button>
        </div>

        <div class="options">
          <label class="check">
            <input v-model="saveId" type="checkbox" />
            <span>아이디 저장</span>
          </label>
          <label class="check">
            <input v-model="autoLogin" type="checkbox" />
            <span>자동로그인</span>
          </label>
        </div>

        <p v-if="message" class="message">{{ message }}</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const uId = ref('')
const uPw = ref('')
const saveId = ref(true)
const autoLogin = ref(false)
const message = ref('')

// 지금은 “화면”만. 연동은 다음 단계에서.
function onSubmit() {
  if (!uId.value || !uPw.value) {
    message.value = 'ID / PASSWORD 를 입력해줘.'
    return
  }
  message.value = `입력됨: ${uId.value} (연동은 다음 단계)`
}
</script>

<style scoped>
/* iPad 기준: 가로 1024~1366을 상정 */
.page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  position: relative;
  overflow: hidden;
  background: #0b0f17;
}

/* 배경: 어두운 경기장 느낌(이미지 없이도 그럴듯하게) */
.bg {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(1200px 500px at 50% 35%, rgba(255,255,255,0.08), transparent 60%),
    radial-gradient(900px 400px at 20% 70%, rgba(0,217,255,0.10), transparent 55%),
    radial-gradient(900px 400px at 80% 70%, rgba(255,204,0,0.07), transparent 55%),
    linear-gradient(180deg, rgba(0,0,0,0.55), rgba(0,0,0,0.75));
  filter: saturate(1.1);
}

/* 로그인 카드 */
.card {
  position: relative;
  width: min(920px, 92vw);
  height: min(420px, 70vh);
  display: grid;
  grid-template-columns: 1fr 1.2fr;
  gap: 24px;
  padding: 28px;
  border-radius: 10px;
  background: rgba(10, 14, 22, 0.75);
  border: 1px solid rgba(255,255,255,0.08);
  box-shadow: 0 18px 60px rgba(0,0,0,0.55);
  backdrop-filter: blur(8px);
}

/* 좌측 로고 영역 */
.brand {
  display: grid;
  place-content: center;
  text-align: center;
  border-right: 1px solid rgba(255,255,255,0.08);
  padding-right: 24px;
}
.brandTop {
  color: rgba(255,255,255,0.75);
  letter-spacing: 0.25em;
  font-weight: 700;
  font-size: 18px;
}
.brandMain {
  color: #ffffff;
  font-weight: 800;
  font-size: 88px;
  line-height: 1;
  margin-top: 6px;
}
.brandSub {
  margin-top: 10px;
  color: rgba(255,255,255,0.65);
  font-size: 12px;
  letter-spacing: 0.18em;
}

/* 우측 폼 영역 */
.form {
  display: grid;
  align-content: center;
  gap: 12px;
  padding-left: 10px;
}

.input {
  width: 100%;
  height: 46px;
  padding: 0 14px;
  border-radius: 4px;
  border: 1px solid rgba(255,255,255,0.12);
  background: rgba(255,255,255,0.06);
  color: #fff;
  outline: none;
  font-size: 16px;
}
.input:focus {
  border-color: rgba(0,217,255,0.55);
  box-shadow: 0 0 0 3px rgba(0,217,255,0.12);
}

.row {
  display: grid;
  grid-template-columns: 1fr 74px;
  gap: 10px;
  align-items: center;
}

.lockBtn {
  height: 46px;
  border-radius: 4px;
  border: none;
  background: #f1b400; /* 레거시 느낌의 옐로우 */
  color: #111;
  font-size: 18px;
  cursor: pointer;
  box-shadow: 0 10px 24px rgba(241, 180, 0, 0.25);
}
.lockBtn:active {
  transform: translateY(1px);
}

.options {
  display: flex;
  gap: 18px;
  margin-top: 4px;
}

.check {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: rgba(255,255,255,0.75);
  font-size: 14px;
}

.message {
  margin-top: 10px;
  color: rgba(255,255,255,0.8);
  font-size: 14px;
}

/* 작은 화면 대응 */
@media (max-width: 860px) {
  .card {
    grid-template-columns: 1fr;
    height: auto;
  }
  .brand {
    border-right: none;
    border-bottom: 1px solid rgba(255,255,255,0.08);
    padding-right: 0;
    padding-bottom: 16px;
  }
}
</style>
