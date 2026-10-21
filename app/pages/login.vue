<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  browserLocalPersistence,
  browserSessionPersistence,
  setPersistence,
  signInWithEmailAndPassword
} from 'firebase/auth'

const SAVED_ID_KEY = 'did_saved_id'

const uId = ref('')
const uPw = ref('')
const saveId = ref(true)
const autoLogin = ref(false)
const message = ref('')
const loading = ref(false)
const showPw = ref(false)

const { $auth } = useNuxtApp()
const { request } = useBackendApi()

onMounted(() => {
  const saved = localStorage.getItem(SAVED_ID_KEY)
  if (saved) {
    uId.value = saved
    saveId.value = true
  }
})

// Firebase Auth는 이메일 형식만 받는다. "@"가 포함된 값은 실제 이메일로 그대로 쓰고,
// 짧은 ID만 입력한 경우엔 내부용 가짜 이메일로 변환한다.
const toInternalEmail = (id: string) => (id.includes('@') ? id : `${id}@jpd-did.internal`)

// 로그인 실패 원인에 맞는 안내 문구. 아이디/비밀번호 문제가 아닌데도 "일치하지 않습니다"라고
// 보여주면 서버·네트워크 장애를 계정 문제로 오해하게 된다.
function loginErrorMessage(error: unknown) {
  const { code, status } = (error ?? {}) as { code?: string; status?: number }

  if (typeof code === 'string' && code.startsWith('auth/')) {
    switch (code) {
      case 'auth/invalid-credential':
      case 'auth/wrong-password':
      case 'auth/user-not-found':
      case 'auth/invalid-email':
        return '아이디 또는 비밀번호가 일치하지 않습니다.'
      case 'auth/network-request-failed':
        return '인터넷 연결을 확인한 뒤 다시 시도해 주세요.'
      case 'auth/too-many-requests':
        return '로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.'
      case 'auth/user-disabled':
        return '사용이 중지된 계정입니다. 관리자에게 문의해 주세요.'
      default:
        return '로그인에 실패했습니다. 잠시 후 다시 시도해 주세요.'
    }
  }

  // 인증은 통과했지만 이후 서버 단계에서 실패한 경우.
  if (status === 401 || status === 403) return '로그인 권한을 확인하지 못했습니다. 관리자에게 문의해 주세요.'
  return '서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.'
}

async function login() {
  if (loading.value) return
  if (!uId.value || !uPw.value) {
    message.value = 'ID / PASSWORD 를 입력해줘.'
    return
  }

  loading.value = true
  message.value = ''

  try {
    // 자동로그인 체크: 브라우저를 닫아도 세션 유지. 해제 시: 탭 닫으면 로그아웃.
    await setPersistence($auth, autoLogin.value ? browserLocalPersistence : browserSessionPersistence)
    const credential = await signInWithEmailAndPassword($auth, toInternalEmail(uId.value), uPw.value)
    // Firestore profile creation is backend-owned; the browser holds Auth only.
    await request('/api/v1/match-input/recorder-profile', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: uId.value.trim() }),
    })

    if (saveId.value) {
      localStorage.setItem(SAVED_ID_KEY, uId.value)
    } else {
      localStorage.removeItem(SAVED_ID_KEY)
    }

    await navigateTo('/schedule')
  } catch (error) {
    message.value = loginErrorMessage(error)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="bg" />

    <div class="card">
      <div class="brand">
        <div class="brandTop">AIMBROAD</div>
        <div class="brandSub">CODE INPUT DEVICE</div>
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
          <div class="pwWrap">
            <input
              v-model="uPw"
              class="input pwInput"
              :type="showPw ? 'text' : 'password'"
              placeholder="PASSWORD"
              autocomplete="current-password"
              @keyup.enter="login"
            />
            <!-- mousedown.prevent: 버튼을 눌러도 입력칸 포커스가 빠지지 않아 태블릿 키보드가 닫히지 않는다. -->
            <button
              type="button"
              class="pwToggle"
              :aria-pressed="showPw"
              :aria-label="showPw ? '비밀번호 숨기기' : '비밀번호 보기'"
              @mousedown.prevent
              @click="showPw = !showPw"
            >
              {{ showPw ? '숨김' : '보기' }}
            </button>
          </div>

          <button class="lockBtn" type="button" :disabled="loading" @click="login">
            {{ loading ? '로그인 중…' : '로그인' }}
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

        <p v-if="message" class="message" role="alert">{{ message }}</p>
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
    radial-gradient(900px 400px at 20% 70%, rgba(0,217,255,0.10), transparent 55%),
    radial-gradient(900px 400px at 80% 70%, rgba(255,204,0,0.07), transparent 55%),
    linear-gradient(180deg, rgba(0,0,0,0.55), rgba(0,0,0,0.75));
  filter: saturate(1.1);
}

.card {
  box-sizing: border-box;
  position: relative;
  width: 673.3281px;
  height: 393.3281px;
  display: grid;
  grid-template-columns: minmax(0, 0.85fr) minmax(0, 1.3fr);
  gap: 24px;
  padding: 28px;
  border-radius: 10px;
  background: rgba(10, 14, 22, 0.75);
  border: 1px solid rgba(255,255,255,0.08);
  box-shadow: 0 18px 60px rgba(0,0,0,0.55);
  backdrop-filter: blur(8px);
}

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
.brandSub {
  margin-top: 10px;
  color: rgba(255,255,255,0.65);
  font-size: 12px;
  letter-spacing: 0.18em;
}

.form {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  align-content: center;
  gap: 12px;
  min-width: 0;
  padding-left: 10px;
}

.input {
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
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
  grid-template-columns: minmax(0, 1fr) 84px;
  gap: 8px;
  align-items: center;
}

.pwWrap {
  position: relative;
  min-width: 0;
}
.pwInput {
  padding-right: 62px;
}
.pwToggle {
  position: absolute;
  top: 50%;
  right: 6px;
  transform: translateY(-50%);
  height: 34px;
  padding: 0 10px;
  border: none;
  border-radius: 4px;
  background: rgba(255,255,255,0.10);
  color: rgba(255,255,255,0.85);
  font-size: 13px;
  cursor: pointer;
}
.pwToggle:active {
  background: rgba(255,255,255,0.2);
}

.lockBtn {
  box-sizing: border-box;
  height: 46px;
  padding: 0;
  border-radius: 4px;
  border: none;
  background: #f1b400;
  color: #111;
  font-size: 14px;
  font-weight: 700;
  white-space: nowrap;
  cursor: pointer;
  box-shadow: 0 10px 24px rgba(241, 180, 0, 0.25);
}
.lockBtn:active {
  transform: translateY(1px);
}
.lockBtn:disabled {
  opacity: 0.6;
  cursor: default;
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
  color: #ff9e9e;
  font-size: 14px;
  line-height: 1.4;
}

</style>
