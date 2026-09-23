# 배포 가이드

이 프로젝트는 Nuxt(SSR 비활성화, SPA)로 정적 빌드하여 **Firebase Hosting**(`jpd-did` 프로젝트)에 배포합니다.

## 사전 조건

- `firebase` CLI 설치 및 로그인 상태 (`firebase login`)
- 프로젝트가 `jpd-did`로 연결되어 있는지 확인 (`.firebaserc` 참고, 기본값이라 별도 설정 불필요)
- `.env`에 `NUXT_PUBLIC_*` 값들이 채워져 있어야 함 — 빌드 시점에 정적 파일에 박히는 값들이라 배포 전 반드시 확인

## 배포 절차

```bash
# 1. 정적 파일 빌드 (.output/public 생성)
npm run generate

# 2. Firebase Hosting에 배포
firebase deploy --only hosting
```

## Firestore 규칙/인덱스가 바뀐 경우

```bash
firebase deploy --only firestore:rules,firestore:indexes
```

전체(hosting + firestore)를 한 번에 배포하려면:

```bash
firebase deploy
```

## 참고

- `package.json`에는 `build`(`nuxt build`, SSR용)와 `generate`(`nuxt generate`, 정적 SPA용) 스크립트가 둘 다 있지만, 이 프로젝트는 `ssr: false` + Firebase Hosting 조합이므로 배포 시에는 **`generate`**를 사용한다.
- `backend/` 파이썬 서버는 별도 배포 대상이 아님 (Dockerfile/Cloud Run 설정 없음, `.env`의 `NUXT_PUBLIC_BACKEND_URL`도 로컬 주소 `http://127.0.0.1:8001`). 로컬에서만 실행하는 구조.
