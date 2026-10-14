import type { RouteLocationRaw } from 'vue-router'

// 상위 화면으로 "돌아가는" 이동. 바로 이전 기록이 같은 화면이면 push 대신 history back 을 써서
// 경기 스케줄 → 라인업 → 경기 입력 순서의 기록 스택을 그대로 유지한다.
// (push 로 돌아가면 대기방↔입력 왕복마다 기록이 쌓여 태블릿 뒤로가기가 입력 화면으로 되돌아간다.)
// 이전 기록이 없거나 다른 화면이면(새로고침·직접 진입) 현재 기록을 교체한다.
export function useReturnNavigation() {
  const router = useRouter()
  return (target: RouteLocationRaw) => {
    const back = window.history.state?.back
    if (typeof back === 'string' && router.resolve(back).path === router.resolve(target).path) {
      router.back()
      return
    }
    return router.replace(target)
  }
}
