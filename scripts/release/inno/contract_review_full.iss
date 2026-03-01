; Full-package installer for very large payloads (venv + models)
#define AppName "Smart Contract Review"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef StageDir
  #define StageDir "..\\..\\..\\runtime\\releases\\stage"
#endif
#ifndef OutDir
  #define OutDir "..\\..\\..\\runtime\\releases"
#endif
#ifndef LangFile
  #define LangFile "compiler:Default.isl"
#endif

[Setup]
AppId={{A74F58A2-3A06-4D9B-9AB5-6D10219E7F21}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=ShineiNozen0310
DefaultDirName={localappdata}\SmartContractReview
DefaultGroupName={#AppName}
OutputDir={#OutDir}
OutputBaseFilename=SmartContractReview_Full_Setup_v{#AppVersion}
Compression=zip
SolidCompression=no
DiskSpanning=yes
ReserveBytes=10485760
DiskSliceSize=max
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
WizardStyle=modern
DisableProgramGroupPage=no
ChangesAssociations=no
UsePreviousAppDir=yes
UninstallDisplayIcon={app}\contract_review_launcher.ico

[Languages]
Name: "default"; MessagesFile: "{#LangFile}"

[Files]
Source: "{#StageDir}\\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\\{#AppName}"; Filename: "{app}\\launch_flutter_release_oneclick.bat"; WorkingDir: "{app}"; IconFilename: "{app}\\contract_review_launcher.ico"
Name: "{autodesktop}\\{#AppName}"; Filename: "{app}\\launch_flutter_release_oneclick.bat"; WorkingDir: "{app}"; IconFilename: "{app}\\contract_review_launcher.ico"

[Run]
Filename: "{app}\\launch_flutter_release_oneclick.bat"; Description: "立即启动 {#AppName}"; Flags: postinstall nowait skipifsilent unchecked
