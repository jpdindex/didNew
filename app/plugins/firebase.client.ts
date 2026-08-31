import { initializeApp } from 'firebase/app'
import { getAuth, onAuthStateChanged } from 'firebase/auth'
import { getFirestore } from 'firebase/firestore'

export default defineNuxtPlugin(() => {
  const config = useRuntimeConfig()

  const app = initializeApp(config.public.firebase)
  const auth = getAuth(app)
  const db = getFirestore(app)

  // Firebase는 로그인 상태를 비동기로 복원하므로, 최초 1회 상태가 정해질 때까지 기다릴 수 있는 promise를 같이 제공한다.
  const authReady = new Promise<void>((resolve) => {
    const unsubscribe = onAuthStateChanged(auth, () => {
      unsubscribe()
      resolve()
    })
  })

  return {
    provide: { firebaseApp: app, auth, db, authReady }
  }
})
