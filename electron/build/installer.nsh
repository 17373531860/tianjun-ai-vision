!macro customInit
  ; ============================================================
  ; Pre-install: backup user data BEFORE old uninstaller runs
  ; This prevents data loss when updating from an older version
  ; that stored data inside the installation directory.
  ;
  ; Uses * (not *.*) so that subdirectories like models/, sessions/
  ; are also copied.  Merges into existing AppData (does NOT skip
  ; when the destination already has some files).
  ; ============================================================

  ; Read the old installation path from the registry
  ReadRegStr $0 HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\{${APP_ID}}" "InstallLocation"
  ${If} $0 != ""
    StrCpy $1 "$APPDATA\tianjun-ai-vision"
    CreateDirectory "$1"

    ; Backup database (only if source exists and dest does not)
    ${If} ${FileExists} "$0\resources\backend\sql_app.db"
      ${IfNot} ${FileExists} "$1\sql_app.db"
        CopyFiles /SILENT "$0\resources\backend\sql_app.db" "$1\sql_app.db"
        DetailPrint "Backed up database to $1"
      ${EndIf}
    ${EndIf}

    ; Backup uploads folder (models, images, videos) — merge into dest
    ${If} ${FileExists} "$0\resources\backend\uploads\*"
      CreateDirectory "$1\uploads"
      CopyFiles /SILENT "$0\resources\backend\uploads\*" "$1\uploads"
      DetailPrint "Backed up uploads to $1\uploads"
    ${EndIf}

    ; Backup recordings folder — merge into dest
    ${If} ${FileExists} "$0\resources\backend\recordings\*"
      CreateDirectory "$1\recordings"
      CopyFiles /SILENT "$0\resources\backend\recordings\*" "$1\recordings"
      DetailPrint "Backed up recordings to $1\recordings"
    ${EndIf}
  ${EndIf}

  ; Also check installation directory passed by the user (for custom paths)
  ${If} ${FileExists} "$INSTDIR\resources\backend\sql_app.db"
    StrCpy $1 "$APPDATA\tianjun-ai-vision"
    CreateDirectory "$1"
    ${IfNot} ${FileExists} "$1\sql_app.db"
      CopyFiles /SILENT "$INSTDIR\resources\backend\sql_app.db" "$1\sql_app.db"
    ${EndIf}
  ${EndIf}

  ${If} ${FileExists} "$INSTDIR\resources\backend\uploads\*"
    StrCpy $1 "$APPDATA\tianjun-ai-vision"
    CreateDirectory "$1\uploads"
    CopyFiles /SILENT "$INSTDIR\resources\backend\uploads\*" "$1\uploads"
  ${EndIf}

  ${If} ${FileExists} "$INSTDIR\resources\backend\recordings\*"
    StrCpy $1 "$APPDATA\tianjun-ai-vision"
    CreateDirectory "$1\recordings"
    CopyFiles /SILENT "$INSTDIR\resources\backend\recordings\*" "$1\recordings"
  ${EndIf}
!macroend

!macro customInstall
  ; Install CH340/CH341 USB-to-Serial driver silently
  DetailPrint "Installing CH340 USB-to-Serial driver..."

  ; Try pnputil first (Windows 10/11 built-in, silent)
  nsExec::ExecToLog 'pnputil /add-driver "$INSTDIR\resources\drivers\CH341SER\CH341SER.INF" /install'
  Pop $0
  ${If} $0 == 0
    DetailPrint "CH340 driver installed successfully via pnputil."
  ${Else}
    DetailPrint "pnputil returned $0, trying SETUP.EXE as fallback..."
    ; Fallback: run WCH SETUP.EXE — must set working directory so it can find .SYS files
    SetOutPath "$INSTDIR\resources\drivers\CH341SER"
    nsExec::ExecToLog '"$INSTDIR\resources\drivers\CH341SER\SETUP.EXE" /S'
    Pop $0
    DetailPrint "CH340 SETUP.EXE returned $0"
    SetOutPath "$INSTDIR"
  ${EndIf}
!macroend

!macro customUnInstall
  ; Remove CH340 driver on uninstall (best-effort, non-blocking)
  nsExec::ExecToLog 'pnputil /delete-driver "$INSTDIR\resources\drivers\CH341SER\CH341SER.INF" /uninstall'
  ; NOTE: Do NOT delete $APPDATA\tianjun-ai-vision here
  ; User data should be preserved even after uninstall
!macroend
