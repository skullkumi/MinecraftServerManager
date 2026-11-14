; Minecraft Server Manager インストーラー (Inno Setup 6)
; build\build.ps1 から ISCC.exe /DAppVersion=x.y.z /DSourceDir=... /DOutputDir=... で呼び出す。
; 管理者権限は不要 (現在のユーザーの %LOCALAPPDATA%\Programs にインストールする)。

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\MinecraftServerManager"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
AppId={{6BB34A3C-3EFB-4883-9512-2D9100652952}
AppName=Minecraft Server Manager
AppVersion={#AppVersion}
AppVerName=Minecraft Server Manager {#AppVersion}
AppPublisher=Minecraft Server Manager contributors
DefaultDirName={localappdata}\Programs\MinecraftServerManager
DefaultGroupName=Minecraft Server Manager
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#OutputDir}
OutputBaseFilename=MinecraftServerManager-Setup-v{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\MinecraftServerManager.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
LicenseFile=..\docs\LICENSE.txt

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "デスクトップにショートカットを作成する"; GroupDescription: "ショートカット:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\README.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\docs\FEATURES.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\docs\CHANGELOG.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\docs\LICENSE.txt"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\docs\THIRD_PARTY_LICENSES.txt"; DestDir: "{app}\docs"; Flags: ignoreversion

[Icons]
Name: "{group}\Minecraft Server Manager"; Filename: "{app}\MinecraftServerManager.exe"
Name: "{group}\使い方 (README)"; Filename: "{app}\docs\README.md"
Name: "{group}\アンインストール"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Minecraft Server Manager"; Filename: "{app}\MinecraftServerManager.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MinecraftServerManager.exe"; Description: "Minecraft Server Manager を起動する"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; アプリ本体のみ削除する。サーバー・ワールド・バックアップ・設定 (%LOCALAPPDATA%\MinecraftServerManager) は残す
Type: filesandordirs; Name: "{app}\_internal"
