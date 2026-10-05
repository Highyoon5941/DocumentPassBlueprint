@echo off
chcp 65001 >nul
cd /d %~dp0..
set PYTHONUTF8=1
set PYTHONPATH=

echo ============================================
echo  Valeo_SVMtrial  Windows 설치
echo  conda 환경 이름: Valeo_SVM_Trial
echo ============================================
echo.

where conda >nul 2>&1
if errorlevel 1 (
  echo [X] conda 를 찾을 수 없습니다. Anaconda Prompt 에서 실행하세요.
  pause & exit /b 1
)

call conda env list | findstr /b /c:"Valeo_SVM_Trial " >nul
if %errorlevel%==0 (
  echo [i] 기존 환경을 갱신합니다.
  call conda env update -n Valeo_SVM_Trial -f environment.yml
) else (
  if exist wheelhouse (
    echo [i] 오프라인 설치 ^(wheelhouse 사용^)
    call conda create -y -n Valeo_SVM_Trial python=3.11 pip
    call conda run -n Valeo_SVM_Trial pip install --no-index --find-links wheelhouse -r requirements.txt
  ) else (
    echo [i] 온라인 설치
    call conda env create -f environment.yml
  )
)
if errorlevel 1 (
  echo [X] 환경 생성 실패. 사내 프록시/SSL 설정을 확인하세요 ^(docs\Runbook.md 문제 해결^).
  pause & exit /b 1
)

call conda run -n Valeo_SVM_Trial pip install -e . --no-deps
if not exist .env copy .env.example .env >nul

echo.
echo [i] 환경 점검 실행
call conda run --no-capture-output -n Valeo_SVM_Trial python scripts\smoke_test.py
if errorlevel 1 (
  echo [X] smoke test 실패 — docs\Runbook.md 의 문제 해결을 보세요.
  pause & exit /b 1
)

echo.
echo ============================================
echo  [다음 단계]
echo   1^) .env 편집:
echo        SVMTRIAL_BACKEND=vertex
echo        GOOGLE_CLOUD_PROJECT=^(사내 GCP 프로젝트 ID^)
echo        GOOGLE_CLOUD_LOCATION=^(global 또는 asia-northeast3^)
echo   2^) gcloud auth application-default login
echo      gcloud auth application-default set-quota-project ^<PROJECT_ID^>
echo   3^) run_windows.bat check          ^(모델 목록 + 테스트 호출^)
echo   4^) run_windows.bat all --group ^<doc_type^>__^<customer^>
echo  자세한 내용: docs\migration_vertexAI.md , docs\Runbook.md
echo ============================================
pause
