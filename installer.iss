; 脑电分析软件 安装包（Inno Setup 脚本）
; 由 build_exe.py --installer 调用：先用 PyInstaller 打包到 D:\脑电分析软件\脑电分析软件，再把整个文件夹做成一个安装程序。
; 手动编译：ISCC.exe /DMyAppVersion=2.1.2 installer.iss

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef DistDir
  #define DistDir "D:\脑电分析软件\脑电分析软件"
#endif
#ifndef OutDir
  #define OutDir "D:\脑电分析软件_安装包"
#endif
#define MyAppName "脑电分析软件"
#define MyAppExe "脑电分析软件.exe"

[Setup]
AppId={{6E3B7C2A-5D41-4F0B-9A8E-3EE2A1F0C0DE}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=eeg-analyzer
VersionInfoVersion={#MyAppVersion}
; 默认只给当前用户安装，不需要管理员权限；安装时也可以选“为所有用户安装”
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; 每次都显示“选择安装位置”这一页（默认升级时会跳过）
DisableDirPage=no
DisableWelcomePage=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutDir}
OutputBaseFilename={#MyAppName}-安装
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chs"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "在桌面创建快捷方式"; GroupDescription: "快捷方式："

[InstallDelete]
; 升级时先清掉旧版的程序文件，避免新旧库混在一起
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autoprograms}\{#MyAppName} 使用说明"; Filename: "{app}\使用说明.md"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "安装完成后打开{#MyAppName}"; Flags: nowait postinstall skipifsilent
