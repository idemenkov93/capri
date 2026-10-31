; Установщик Capri (Inno Setup 6). Собирает build_exe.bat или GitHub Actions:
;   ISCC /DAppVersion=0.9.0 [/DSuffix=-personal] packaging\capri.iss
; Ставится в папку пользователя — права администратора не нужны.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef Suffix
  #define Suffix ""
#endif

[Setup]
AppId={{6E0B7C2A-4F1D-4C55-9C1B-CA9F1E0D2B51}
AppName=Capri
AppVersion={#AppVersion}
AppVerName=Capri {#AppVersion}
AppPublisher=Ihor Demenkov
AppPublisherURL=https://github.com/idemenkov93/capri
DefaultDirName={localappdata}\Programs\Capri
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=CapriSetup-{#AppVersion}{#Suffix}
SetupIconFile=capri.ico
UninstallDisplayIcon={app}\Capri.exe
UninstallDisplayName=Capri
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

[CustomMessages]
ru.Autostart=Запускать Капри вместе с Windows
en.Autostart=Start Capri with Windows
de.Autostart=Capri mit Windows starten
ru.Setup=Мастер настройки Капри
en.Setup=Capri setup wizard
de.Setup=Capri-Einrichtung

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked
Name: "autostart"; Description: "{cm:Autostart}"; Flags: unchecked

[Files]
Source: "..\dist\Capri\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
; старые файлы программы от прошлой версии (настройки и анимации лежат в %LOCALAPPDATA%\Capri — их не трогаем)
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{userprograms}\Capri"; Filename: "{app}\Capri.exe"
Name: "{userprograms}\{cm:Setup}"; Filename: "{app}\Capri.exe"; Parameters: "--setup"
Name: "{userdesktop}\Capri"; Filename: "{app}\Capri.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Capri"; \
  ValueData: """{app}\Capri.exe"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\Capri.exe"; Description: "{cm:LaunchProgram,Capri}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM Capri.exe /F"; Flags: runhidden; RunOnceId: "KillCapri"
