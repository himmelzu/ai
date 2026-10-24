# PDF에서 여비지급명세서로

개인별 출장내역 PDF를 불러오면 **실제 출장시간을 정리하고 일비를 계산해 여비지급명세서 PDF로 저장**하는 Windows 프로그램입니다. 변환 결과를 화면에서 바로 확인·수정할 수 있습니다.

## Windows 프로그램 다운로드

**[여비청구서 변환기 v0.3.0 ZIP 다운로드](https://github.com/himmelzu/ai/raw/refs/heads/main/downloads/TravelClaim-Windows-v0.3.0.zip)** · Windows 10/11 64비트

1. ZIP을 풀고 `여비청구서변환기.exe`를 실행합니다. Python 설치는 필요하지 않습니다.
2. **PDF 선택**으로 출장내역 파일을 넣습니다. 내역과 금액이 자동으로 준비됩니다.
3. 필요한 내용을 화면에서 고친 뒤 **PDF 저장**을 누릅니다. 실제 시각·차량·포함 대상을 바꾸면 일비도 다시 계산됩니다.

[상세 사용설명서](docs/사용설명서.md) · [가명 예시 입력 PDF](downloads/example-input.pdf) · [예시 출력 PDF](downloads/example-statement.pdf)

출력물에는 ‘검토용’·‘검토 중’ 제목이나 워터마크를 넣지 않습니다. 확인이 필요한 사항은 프로그램 화면에 표시합니다. 기관 지급기준은 접힌 설정에서 조정할 수 있고, 원본 PDF에 없는 직급은 필요할 때 한 번 입력할 수 있습니다.

![프로그램 화면 — 가명 자료](downloads/app-screenshot.png)

## 지원하는 PDF

- **개인별 출장내역 PDF:** 신청행과 확인행을 출장별로 합치고, 확인행의 실제 출발·복귀 일시를 사용합니다. 페이지 사이에 나뉜 행도 이어서 읽습니다. 명시된 확인행의 상태를 우선하며, 미전송 등은 기본 청구 대상에서 제외합니다.
- **18열 출장 월별집계 텍스트 PDF:** 기존 형식도 지원합니다. 상태가 없는 이 형식에는 시스템에서 결재완료된 내역만 내려받는다는 업무 규칙을 적용합니다.

관외·여러 날 출장은 기본 청구 대상에서 제외하며 전체 목록에서 확인할 수 있습니다. 일비의 초기값은 예시이므로 소속 기관 기준에 맞춰 사용합니다. 실제 시간이 없으면 신청시간으로 대신 채우지 않으며, 서명·날인은 자동 생성하지 않습니다.

v0.3.0은 PDF 전용입니다. PDF 안의 문자와 표를 직접 추출하고, 정해진 규칙으로 계산·서식화합니다. 실행 중 이미지 OCR이나 생성형 AI를 사용하지 않으며, 원본과 출장 내역을 외부 서버로 보내지 않습니다. 소스와 배포 예시는 가명 자료입니다.

## 복사·수정할 수 있는 제안서

**[제안서 Word(DOCX)](https://github.com/himmelzu/ai/raw/refs/heads/main/downloads/contest-proposal.docx)** · **[복사용 텍스트(TXT)](https://github.com/himmelzu/ai/raw/refs/heads/main/downloads/contest-proposal.txt)** · [제안서 PDF](downloads/contest-proposal.pdf)

제안서는 일반 문단과 표로 작성한 편집 가능한 초안입니다. PDF 전용 구현과 AI 개발 보조의 역할을 구분해 설명하며, 개인 참가정보·동의·서명은 작성자가 입력합니다. `scripts/create_proposal_docs.py`로 다시 만들 수 있고 문서 생성에만 `python-docx==1.2.0`이 필요합니다.

배포 ZIP에는 실행파일, 사용설명서, 가명 예시, 제안서 초안, 소스와 라이선스가 포함됩니다. [SHA256 체크섬](downloads/SHA256SUMS.txt)으로 파일을 확인할 수 있습니다.

## 개발 실행과 Windows 빌드

Python 3.12에서 다음 명령을 이 저장소에서 실행합니다.

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app/backend.py --desktop
```

Windows 실행파일은 Linux에서 빌드할 수 있습니다.

```sh
.venv/bin/python -m pip install -r build-requirements.txt
.venv/bin/python scripts/build_windows.py
```

빌드 단계에서 고정 버전의 Python·PyMuPDF·fontTools·Go 및 필요한 런타임을 준비하고 체크섬을 검증합니다. 실행파일에는 Python과 한국어 글꼴이 포함되어 있으며, 사용 중에는 인터넷 연결이 필요하지 않습니다.

```sh
.venv/bin/python app/backend.py --convert report.pdf --output statement.pdf
.venv/bin/python -m unittest discover -s tests -v
```

지원하는 시스템의 텍스트 PDF를 대상으로 하며, 암호를 해제하거나 임의의 문서 형식을 읽는 기능은 제공하지 않습니다. 실제 Windows PC의 브라우저·다운로드 동작은 사용 환경에서 확인하세요. 원본 첨부 문서와 실제 직원 정보, `.build/`·`dist/`의 개인 테스트 결과는 배포하지 않습니다.

## 라이선스

프로그램 소스는 AGPL-3.0-or-later입니다. 구성요소별 라이선스는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)와 배포 파일의 라이선스 폴더를 참고하세요.
