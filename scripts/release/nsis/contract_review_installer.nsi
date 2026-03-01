Unicode true

!include "MUI2.nsh"

!ifndef APP_VERSION
!define APP_VERSION "1.0.0"
!endif

!ifndef APP_NAME
!define APP_NAME "Smart Contract Review"
!endif

!ifndef PRODUCT_KEY
!define PRODUCT_KEY "SmartContractReview"
!endif

!ifndef APP_STAGE
!define APP_STAGE "..\\..\\..\\runtime\\releases\\stage"
!endif

!ifndef MAIN_BAT
!define MAIN_BAT "launch_flutter_release_oneclick.bat"
!endif

Name "${APP_NAME}"
OutFile "..\\..\\..\\runtime\\releases\\${PRODUCT_KEY}_Setup_v${APP_VERSION}.exe"
InstallDir "$LOCALAPPDATA\\${PRODUCT_KEY}"
InstallDirRegKey HKCU "Software\\${PRODUCT_KEY}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "SimpChinese"

Section "Install"
  SetOutPath "$INSTDIR"
  File /r "${APP_STAGE}\\*.*"

  WriteUninstaller "$INSTDIR\\uninstall.exe"

  WriteRegStr HKCU "Software\\${PRODUCT_KEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${PRODUCT_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${PRODUCT_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${PRODUCT_KEY}" "Publisher" "ShineiNozen0310"
  WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${PRODUCT_KEY}" "UninstallString" "$\"$INSTDIR\\uninstall.exe$\""

  CreateDirectory "$SMPROGRAMS\\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\\${APP_NAME}\\${APP_NAME}.lnk" "$INSTDIR\\${MAIN_BAT}" "" "$INSTDIR\\contract_review_launcher.ico" 0
  CreateShortcut "$SMPROGRAMS\\${APP_NAME}\\Uninstall.lnk" "$INSTDIR\\uninstall.exe"
  CreateShortcut "$DESKTOP\\${APP_NAME}.lnk" "$INSTDIR\\${MAIN_BAT}" "" "$INSTDIR\\contract_review_launcher.ico" 0
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\\${APP_NAME}\\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\\${APP_NAME}\\Uninstall.lnk"
  RMDir "$SMPROGRAMS\\${APP_NAME}"

  DeleteRegKey HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${PRODUCT_KEY}"
  DeleteRegKey HKCU "Software\\${PRODUCT_KEY}"

  RMDir /r "$INSTDIR"
SectionEnd
