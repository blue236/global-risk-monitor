# Weekly TODO — Global Risk Monitor (GRM)

## P1 (Must-do)
1. Telegram 명령 안정화
   - `/status`, `/refresh`, `/report`, `/triggers`, `/help` 운영 검증
   - scheduler `max_instances` 경고 제거(중첩 실행 방지)

2. 외부 접속 + 로그인 안정화
   - HTTP/HTTPS 모드별 `GRM_COOKIE_SECURE` 운영 가이드 확정
   - 로그인 실패/차단 정책 동작 재검증

3. 운영 안전장치
   - `/refresh` 중복 호출 시 락 처리(실행 중 재호출 차단)

## P2
4. 알림 소음 관리
   - 반복 메시지 억제 정책 점검(해시/시간 창)

5. 문서 업데이트
   - README에 Telegram 명령 사용법 추가
   - HTTPS 전환 체크리스트 추가

---

# Day Plan (Today)
1. `max_instances` 경고 재현 및 원인 고정
2. Telegram poll job 중복 실행 방지 패치
3. `/refresh` 락 처리 추가
4. .env 운영 가이드(HTTP/HTTPS) 문서화
5. 재시작 후 Telegram 명령 스모크 테스트
