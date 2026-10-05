@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONPATH=
cd /d %~dp0..

if "%1"=="" (
  echo 사용법:
  echo   run_windows.bat check                              ^(Vertex AI 연결 점검^)
  echo   run_windows.bat models                             ^(모델 ID 목록만^)
  echo   run_windows.bat smoke                              ^(환경 점검^)
  echo   run_windows.bat doctor                             ^(설정 점검^)
  echo   run_windows.bat all --group 대책서__CUST_A
  echo   run_windows.bat diagnose --pdf C:\docs\new.pdf --group 대책서__CUST_A
  echo   run_windows.bat ^<ingest^|ocr^|labels^|sections^|model^|template^|report^|approve^> ...
  exit /b 0
)

if "%1"=="check"  (call conda run --no-capture-output -n Valeo_SVM_Trial python scripts\check_gemini.py & goto :eof)
if "%1"=="models" (call conda run --no-capture-output -n Valeo_SVM_Trial python scripts\check_gemini.py --list-only & goto :eof)
if "%1"=="smoke"  (call conda run --no-capture-output -n Valeo_SVM_Trial python scripts\smoke_test.py & goto :eof)

call conda run --no-capture-output -n Valeo_SVM_Trial python -m svmtrial %*
