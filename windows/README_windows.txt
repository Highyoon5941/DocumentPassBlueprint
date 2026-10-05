Valeo_SVMtrial — Windows 실행 안내
==================================

준비물
------
1. Anaconda 또는 Miniconda
2. Google Cloud CLI  (인증 방식 A = ADC 를 쓸 때만)
   https://cloud.google.com/sdk/docs/install
3. 사내 GCP 프로젝트에서 IAM 역할 `roles/aiplatform.user`
4. 사내 GCP 프로젝트 ID 와 사용 가능한 Gemini 모델 ID
   → docs\migration_vertexAI.md 의 "사람이 직접 받아와야 하는 것" 표를 보세요.

설치
----
1. 이 zip 을 한글/공백이 없는 경로에 풉니다.  예: C:\valeo_svmtrial
   (Windows 260자 경로 제한 때문에 깊은 경로는 피하세요)
2. 시작 메뉴에서 "Anaconda Prompt" 를 엽니다.
3. cd C:\valeo_svmtrial
4. windows\setup_windows.bat

설정
----
5. 메모장으로 .env 를 엽니다. 다음을 채웁니다.
     SVMTRIAL_BACKEND=vertex
     GOOGLE_CLOUD_PROJECT=<사내 GCP 프로젝트 ID>
     GOOGLE_CLOUD_LOCATION=global          (또는 asia-northeast3)
     GEMINI_MODEL_FAST=                    (6번에서 확인한 값)
     GEMINI_MODEL_PRO=                     (6번에서 확인한 값)
6. 인증 (방식 A):
     gcloud auth application-default login
     gcloud auth application-default set-quota-project <PROJECT_ID>
   인증 (방식 B, 서비스계정 키):
     .env 에 GOOGLE_APPLICATION_CREDENTIALS=C:\keys\svmtrial-sa.json
     ※ 키 파일은 이 zip 에 들어 있지 않습니다. 별도로 전달받아 두세요.
7. windows\run_windows.bat models     → 모델 ID 목록을 보고 5번의 빈칸을 채웁니다.
8. windows\run_windows.bat check      → 실제 호출 1회 테스트

실행
----
9.  데이터를 넣습니다.
      data\raw\pdfs\<문서종류>\<고객사>\<doc_id>.pdf
      data\raw\labels\*.xlsx
    ※ PDF 파일명(확장자 제외)이 평가 시트의 doc_id 와 같아야 합니다.
10. run_windows.bat all --group 대책서__CUST_A
    ※ 사람 검토 관문(🔒)에서 멈춥니다. 안내 메시지대로 후보 파일을 검토한 뒤
      run_windows.bat approve --group 대책서__CUST_A --gate sections
      run_windows.bat approve --group 대책서__CUST_A --gate concepts
      를 실행하고 10번을 다시 실행하세요.
11. 결과: outputs\<run_id>\<그룹>\ 안에
      template_spec.json / template_spec.md / template.docx(또는 .pptx) / analysis_report.md

문제가 생기면 docs\Runbook.md 의 "문제 해결" 을 보세요.
