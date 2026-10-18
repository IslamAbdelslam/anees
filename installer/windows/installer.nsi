; Anees Windows installer (NSIS 3 / MUI2).
;
; Builds the per-machine installer for anees-gui:
;   makensis /DAPP_VERSION=1.2.3 installer.nsi
; Sources default to the release binary and the Python backend in the repo
; (paths relative to this script via ${__FILEDIR__}), so no extra defines are
; needed when run from a full checkout that has built `cargo build --release`.

!include "MUI2.nsh"
!include "FileFunc.nsh"

!ifndef APP_VERSION
  !define APP_VERSION "0.1.0"
!endif
!ifndef SRC_EXE
  !define SRC_EXE "${__FILEDIR__}\..\..\target\release\anees-gui.exe"
!endif
!ifndef SRC_BACKEND
  !define SRC_BACKEND "${__FILEDIR__}\..\..\python-backend"
!endif

!define APP_NAME "Anees"
!define APP_PUBLISHER "Anees"
!define APP_URL "https://github.com/IslamAbdelslam/anees"
!define APP_EXE "anees-gui.exe"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"

Name "${APP_NAME} ${APP_VERSION}"
OutFile "${__FILEDIR__}\${APP_NAME}-${APP_VERSION}-Setup.exe"
Unicode true
RequestExecutionLevel admin
InstallDir "$PROGRAMFILES64\${APP_NAME}"
InstallDirRegKey HKLM "Software\${APP_NAME}" "InstallDir"
SetCompressor /SOLID lzma

!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

;-------------------------------------------------------------------- install
Section "Anees" SEC_MAIN
  SectionIn RO

  SetOutPath "$INSTDIR"
  File "${SRC_EXE}"

  ; Python backend source; its dependencies and models are fetched on first
  ; run (see anees-gui first-run setup). Kept at $INSTDIR\python-backend so
  ; the default backend path resolves relative to the app directory.
  SetOutPath "$INSTDIR\python-backend"
  File /r /x __pycache__ /x .pytest_cache /x .ruff_cache /x *.pyc "${SRC_BACKEND}\*.*"
  SetOutPath "$INSTDIR"

  WriteUninstaller "$INSTDIR\Uninstall.exe"

  WriteRegStr HKLM "Software\${APP_NAME}" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "Software\${APP_NAME}" "Version" "${APP_VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\App Paths\${APP_EXE}" "" "$INSTDIR\${APP_EXE}"

  WriteRegStr HKLM "${UNINST_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKLM "${UNINST_KEY}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKLM "${UNINST_KEY}" "URLInfoAbout" "${APP_URL}"
  WriteRegStr HKLM "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE},0"
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoRepair" 1

  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\Uninstall ${APP_NAME}.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
SectionEnd

; Optional: register Anees as a handler for .pdf without stealing the user's
; current default (OpenWithProgids only).
Section "Associate .pdf files with Anees" SEC_PDF
  WriteRegStr HKLM "Software\Classes\Anees.PDF" "" "PDF Document"
  WriteRegStr HKLM "Software\Classes\Anees.PDF\DefaultIcon" "" "$INSTDIR\${APP_EXE},0"
  WriteRegStr HKLM "Software\Classes\Anees.PDF\shell\open\command" "" '"$INSTDIR\${APP_EXE}" --file "%1"'
  WriteRegStr HKLM "Software\Classes\.pdf\OpenWithProgids" "Anees.PDF" ""

  WriteRegStr HKLM "Software\Classes\Applications\${APP_EXE}" "FriendlyAppName" "${APP_NAME}"
  WriteRegStr HKLM "Software\Classes\Applications\${APP_EXE}\SupportedTypes" ".pdf" ""
  WriteRegStr HKLM "Software\Classes\Applications\${APP_EXE}\shell\open\command" "" '"$INSTDIR\${APP_EXE}" --file "%1"'
  ${RefreshShellIcons}
SectionEnd

LangString DESC_SEC_MAIN ${LANG_ENGLISH} "Anees application files."
LangString DESC_SEC_PDF ${LANG_ENGLISH} "Offer Anees when opening .pdf files (does not change your default)."
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_MAIN} $(DESC_SEC_MAIN)
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_PDF} $(DESC_SEC_PDF)
!insertmacro MUI_FUNCTION_DESCRIPTION_END

;------------------------------------------------------------------ uninstall
Section "Uninstall"
  Delete "$INSTDIR\${APP_EXE}"
  RMDir /r "$INSTDIR\python-backend"
  Delete "$INSTDIR\Uninstall.exe"

  DeleteRegKey HKLM "${UNINST_KEY}"
  DeleteRegKey HKLM "Software\${APP_NAME}"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\App Paths\${APP_EXE}"

  DeleteRegKey HKLM "Software\Classes\Anees.PDF"
  DeleteRegValue HKLM "Software\Classes\.pdf\OpenWithProgids" "Anees.PDF"
  DeleteRegKey HKLM "Software\Classes\Applications\${APP_EXE}"

  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Uninstall ${APP_NAME}.lnk"
  RMDir "$SMPROGRAMS\${APP_NAME}"
  Delete "$DESKTOP\${APP_NAME}.lnk"

  RMDir "$INSTDIR"
  ${RefreshShellIcons}
SectionEnd
