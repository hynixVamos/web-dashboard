# AI 인프라 트래킹 대시보드 (라이브 웹 버전)

GPU 렌탈가 / 주가수익률 / 하이퍼스케일러 Capex-OCF-FCF를 한 페이지에서 보여주는
Flask 대시보드. SK하이닉스 ADR 대시보드와 동일한 아키텍처(Flask + gunicorn post_fork
백그라운드 캐시 스레드 + Render)로 만들었습니다.

## 핵심 구조 (ADR 대시보드와 동일한 패턴)
- **절대 요청마다 외부 API를 호출하지 않음.** 백그라운드 스레드가 30분마다
  `gpu_rental_tracker` / `stock_returns_tracker` / `hyperscaler_tracker`를 실행해서
  `cache_refresh.CACHE`에 저장하고, Flask 라우트는 이 캐시만 읽습니다.
- 이 백그라운드 스레드는 **gunicorn의 post_fork 훅**(`gunicorn.conf.py`)에서
  워커 프로세스 안에서 시작됩니다. master 프로세스에서 시작하면 ADR 대시보드 때
  겪으셨던 fork 버그가 재발할 수 있어서 이 부분은 절대 바꾸지 마세요.
- `workers = 1` 로 고정 (캐시가 프로세스 메모리 안에 있어서, 워커가 여러 개면
  캐시가 워커마다 따로 놀고 API 호출도 워커 수만큼 중복됨)

## 파일 구성
```
web_dashboard/
├── app.py                  # Flask 앱, 라우트
├── cache_refresh.py        # 백그라운드 캐시 갱신 로직
├── gunicorn.conf.py        # post_fork 훅 (핵심)
├── config.py                       ┐
├── gpu_rental_tracker.py            │ 로컬 배치 버전과 동일한 모듈 재사용
├── stock_returns_tracker.py         │
├── hyperscaler_tracker.py          ┘
├── templates/index.html    # 대시보드 페이지
├── static/style.css        # 다크 터미널 스타일
├── requirements.txt
└── Procfile                # Render/Heroku용 실행 명령
```

## 로컬에서 먼저 테스트
```powershell
cd web_dashboard
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt

# API 키 설정 (auto_tracker 때와 동일)
setx VAST_API_KEY "your_key"
setx SEC_USER_AGENT "personal-research-tool your_email@example.com"

python app.py
```
새 터미널에서 `http://localhost:5000` 접속해서 확인.
처음 뜰 때는 백그라운드 스레드가 첫 갱신을 하는 동안(수십 초) 빈 테이블이 보일 수 있어요 — 새로고침하면 채워집니다.

## Render 배포
1. 이 `web_dashboard` 폴더를 별도 GitHub 저장소로 만들어 push
2. Render 대시보드 → New → Web Service → 해당 저장소 연결
3. 설정:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn -c gunicorn.conf.py app:app`
   - **Plan**: Starter (ADR 대시보드 때와 동일)
4. Environment Variables에 추가:
   - `VAST_API_KEY`
   - `RUNPOD_API_KEY` (선택)
   - `SEC_USER_AGENT` (본인 실제 이메일 포함해서)
5. Deploy 후 발급되는 `https://your-app.onrender.com` 주소가 대시보드 URL

## 텔레그램 공유
ADR 대시보드 때처럼 Render URL을 텔레그램 채널에 그냥 공유하면 됩니다.
텔레그램 봇으로 주기적 알림(예: GPU 가격 급등 시 자동 메시지)을 붙이고 싶으시면
추가로 말씀해주세요 — `cache_refresh.py`의 `_refresh_once()` 안에 조건 체크 후
텔레그램 봇 API 호출을 붙이는 방식으로 확장 가능합니다.

## 확인해야 할 것
- Render Starter 플랜은 일정 시간 트래픽 없으면 슬립될 수 있음 (Free 플랜만 해당,
  Starter는 상시 구동이지만 요금 확인 필요)
- SEC EDGAR, Yahoo Finance, Vast.ai 모두 외부 서비스라 정책/스키마가 바뀔 수 있음 —
  `/health` 라우트로 `last_error` 필드를 주기적으로 확인하면 조기에 문제 파악 가능
