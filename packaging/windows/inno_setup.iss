; Thonny AIA (fork with the AI Assistant): own ids and names, so it installs next to
; the regular Thonny instead of over it, and its launcher is thonny-aia.exe
#define AppUserModelID "Thonny.ThonnyAIA"
#define ThonnyPyProgID "ThonnyAIA.py"
#define AppTitle "Thonny AIA"
#define ExeName "thonny-aia.exe"
; Give AppVer and SourceFolder from command line, eg:
; "C:\Program Files (x86)\Inno Setup 5\iscc" /dAppVer=1.13 /dSourceFolder=build inno_setup.iss 
;#define AppVer "9.9.9"
;#define InstallerPrefix "thonny"
;#define SourceFolder "C:\Users\Aivar Annamaa\python_stuff\thonny\packaging\windows\build"
;#define SupportedArchitectures "x64"
;#define Arch "x64"


[Setup]
AppId=ThonnyAIA
AppName={#AppTitle}
AppVersion={#AppVer}
AppVerName={#AppTitle} {#AppVer}
;AppComments string is displayed on the "Support" dialog of the Add/Remove Programs Control Panel applet
AppComments=Unofficial fork of Thonny (Python IDE for beginners by Aivar Annamaa) with the AI assistant AIA by Jakub Šenkýř
AppPublisher=Jakub Šenkýř
AppPublisherURL=https://github.com/senkyr/thonny-aia
AppSupportURL=https://github.com/senkyr/thonny-aia/issues
AppUpdatesURL=https://github.com/senkyr/thonny-aia/releases
; Thonny first, AIA second (cortex-zamestnani#78); shown in the installer's file properties
AppCopyright=Copyright (c) 2026 Aivar Annamaa, Copyright (c) 2026 Jakub Šenkýř
VersionInfoCopyright=Copyright (c) 2026 Aivar Annamaa, Copyright (c) 2026 Jakub Šenkýř
VersionInfoCompany=Jakub Šenkýř
VersionInfoDescription={#AppTitle} {#AppVer} Setup
VersionInfoProductName={#AppTitle}
VersionInfoProductTextVersion={#AppVer}

; Actual privileges depend on how user started the installer
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline dialog

; Compatibility
MinVersion=10.0
ArchitecturesAllowed={#SupportedArchitectures}
ArchitecturesInstallIn64BitMode={#SupportedArchitectures}

; Will show important info on welcome page
DisableWelcomePage=no

DisableProgramGroupPage=auto
DefaultGroupName={#AppTitle}

; Note that DefaultDirName can be overridden with installer's /DIR=... parameter
DefaultDirName={autopf}\{#AppTitle}
DisableDirPage=auto
DirExistsWarning=auto
UsePreviousAppDir=yes

DisableReadyPage=no
AlwaysShowDirOnReadyPage=yes


; Request extra space for precompiling libraries
ExtraDiskSpaceRequired=25000000
OutputDir=dist
OutputBaseFilename={#InstallerPrefix}-{#AppVer}-{#Arch}
Compression=lzma2/ultra
SolidCompression=yes

LicenseFile=license-for-win-installer.txt
WizardImageFile=screenshot_with_logo_semidark.bmp
WizardSmallImageFile=small_logo.bmp
ChangesAssociations=yes

; Signing
; Certum Unizeto provides free certs for open source
; http://www.certum.eu/certum/cert,offer_en_open_source_cs.xml
; http://pete.akeo.ie/2011/11/free-code-signing-certificate-for-open.html
; http://blog.ksoftware.net/2011/07/exporting-your-code-signing-certificate-to-a-pfx-file-from-firefox/
; http://certhelp.ksoftware.net/support/solutions/articles/17157-how-do-i-export-my-code-signing-certificate-from-internet-explorer-or-chrome-
; http://blog.ksoftware.net/2011/07/how-to-automate-code-signing-with-innosetup-and-ksign/
; https://www.digicert.com/code-signing/signcode-signtool-command-line.htm
; http://www.jrsoftware.org/ishelp/index.php?topic=setup_signtool
;
; signtool prefix to be configured in Tools => Configure sign tools:
; "C:\Program Files (x86)\Windows Kits\10\App Certification Kit\signtool.exe" sign /n "Open Source Developer Aivar Annamaa" $p
; "C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x86\signtool.exe" sign /n "Open Source Developer Aivar Annamaa" $p
; NB! Don't forget the trailing $p
; Thonny AIA is not signed (no SignTool)


[Languages]
Name: "czech"; MessagesFile: "compiler:Languages\Czech.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[InstallDelete]
; Clean old installation before copying new files
; (list specific subdirectories to avoid disaster when user selects a wrong directory for installation)
Type: filesandordirs; Name: "{app}\DLLs"
Type: filesandordirs; Name: "{app}\Doc"
Type: filesandordirs; Name: "{app}\include"
Type: filesandordirs; Name: "{app}\Lib"
Type: filesandordirs; Name: "{app}\libs"
Type: filesandordirs; Name: "{app}\Scripts"
Type: filesandordirs; Name: "{app}\tcl"
Type: filesandordirs; Name: "{app}\Tools"
Type: filesandordirs; Name: "{app}\python*"

[Tasks]
Name: CreateDesktopIcon; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked;

[Files]
Source: "{#SourceFolder}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\{#AppTitle}"; Filename: "{app}\{#ExeName}"; IconFilename: "{app}\{#ExeName}"
Name: "{autodesktop}\{#AppTitle}"; Filename: "{app}\{#ExeName}"; IconFilename: "{app}\{#ExeName}"; Tasks: CreateDesktopIcon


[Registry]
; Register the application
; http://msdn.microsoft.com/en-us/library/windows/desktop/ee872121%28v=vs.85%29.aspx
; https://docs.microsoft.com/en-us/windows/desktop/shell/app-registration
; TODO: investigate also SupportedProtocols subkey of this key
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#ExeName}"; ValueType: string; ValueName: "";                 ValueData: "{app}\{#ExeName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#ExeName}";                       ValueType: string; ValueName: "";                 ValueData: "{#AppTitle}";  Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#ExeName}";                       ValueType: string; ValueName: "FriendlyAppName";  ValueData: "{#AppTitle}";  Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#ExeName}";                       ValueType: string; ValueName: "AppUserModelID";   ValueData: "{#AppUserModelID}";  Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#ExeName}\SupportedTypes";        ValueType: string; ValueName: ".py";              ValueData: "";        Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#ExeName}\SupportedTypes";        ValueType: string; ValueName: ".pyw";             ValueData: "";        Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#ExeName}\SupportedTypes";        ValueType: string; ValueName: ".pyde";             ValueData: "";        Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#ExeName}\shell\open\command";    ValueType: string; ValueName: "";                 ValueData: """{app}\{#ExeName}"" ""%1"""; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#ExeName}\shell\Edit with Thonny AIA\command";   ValueType: string; ValueName: "";      ValueData: """{app}\{#ExeName}"" ""%1"""; Flags: uninsdeletekey

; Explicit marker for the modern 64-bit install mode.
Root: HKA; Subkey: "Software\Thonny AIA"; ValueType: string; ValueName: "InstallMode"; ValueData: "64-bit"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Thonny AIA"; ValueType: string; ValueName: "InstallArchitecture"; ValueData: "{#Arch}"; Flags: uninsdeletevalue

; Add link to Thonny under existing Python.File ProgID
Root: HKA; Subkey: "Software\Classes\Python.File\shell\Edit with Thonny AIA"; ValueType: none; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Python.File\shell\Edit with Thonny AIA\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#ExeName}"" ""%1""";  Flags: uninsdeletekey

; Create separate ProgID (Thonny.py) which represents Thonny's ability to handle Python files
; These settings will be used when user chooses Thonny as default program for opening *.py files
Root: HKA; Subkey: "Software\Classes\{#ThonnyPyProgID}"; ValueType: string; ValueName: "";                 ValueData: "Python file";  Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\{#ThonnyPyProgID}"; ValueType: string; ValueName: "FriendlyTypeName"; ValueData: "Python file";  Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\{#ThonnyPyProgID}"; ValueType: string; ValueName: "AppUserModelID"; ValueData: "{#AppUserModelID}";  Flags: uninsdeletekey

;Root: HKA; Subkey: "Software\Classes\{#ThonnyPyProgID}"; ValueType: string; ValueName: "EditFlags"; TODO: https://docs.microsoft.com/en-us/windows/desktop/api/Shlwapi/ne-shlwapi-filetypeattributeflags

Root: HKA; Subkey: "Software\Classes\{#ThonnyPyProgID}\shell\open\command";     ValueType: string; ValueName: ""; ValueData: """{app}\{#ExeName}"" ""%1""";  Flags: uninsdeletekey

; Relate this ProgID with *.pyde, *.py and *.pyw extensions
; https://docs.microsoft.com/en-us/windows/desktop/shell/how-to-include-an-application-on-the-open-with-dialog-box
Root: HKA; Subkey: "Software\Classes\.py\OpenWithProgIds";  ValueType: string; ValueName: "{#ThonnyPyProgID}";   Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.pyw\OpenWithProgIds"; ValueType: string; ValueName: "{#ThonnyPyProgID}";   Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.pyde\OpenWithProgIds"; ValueType: string; ValueName: "{#ThonnyPyProgID}";   Flags: uninsdeletevalue

; Add "Python file" to Explorer's "New" context menu (don't remove on uninstallation)
Root: HKA; Subkey: "Software\Classes\.py\ShellNew";  ValueType: string; ValueData: "Python.File";  
Root: HKA; Subkey: "Software\Classes\.py\ShellNew";  ValueType: string; ValueName: "NullFile"; ValueData: "";  

[Run]
Filename: "{app}\pythonw.exe"; Parameters: "-m compileall ."; StatusMsg: "Compiling standard library... (takes a while)"


[UninstallDelete]
Type: filesandordirs; Name: "{app}\*"
Type: filesandordirs; Name: "{app}"
; the local model that Thonny AIA prepared (Ollama and the models, cortex-zamestnani#120);
; the user's settings in %APPDATA%\Thonny AIA stay, like upstream's
Type: filesandordirs; Name: "{localappdata}\Thonny AIA\local-models"
Type: dirifempty; Name: "{localappdata}\Thonny AIA"

[Messages]
FinishedHeadingLabel=Great success!



[Code]


const
  ModernInstallKey = 'Software\Thonny AIA';
  ModernInstallModeValue = '64-bit';

var
  QuoteLabel: TLabel;
  Upgraded : Boolean;

function StartedForAllUsers(): Boolean;
begin
    Result := IsAdminInstallMode();
end;

function StartedForThisUser(): Boolean;
begin
    Result := not IsAdminInstallMode();
end;

function InstalledForAllUsers(): Boolean;
begin
    Result := RegKeyExists(HKEY_LOCAL_MACHINE, 'Software\Classes\Applications\{#ExeName}');
end;

function InstalledForThisUser(): Boolean;
begin
    Result := RegKeyExists(HKEY_CURRENT_USER, 'Software\Classes\Applications\{#ExeName}');
end;


function GetRandomQuote(): String;
var
    Quotes: array[0..13] of string;
begin
    Quotes[0] := 'Ninety-ninety rule: The first 90 percent of the code accounts for the first 90 percent of the development time. The remaining 10 percent of the code accounts for the other 90 percent of the development time.'#13#10'– Tom Cargill ';
    Quotes[1] := 'Now is better than never.'#13#10'– Tim Peters ';
    Quotes[2] := 'Give someone a program, frustrate them for a day; teach them how to program, frustrate them for a lifetime.'#13#10#13#10'– David Leinweber ';
    Quotes[3] := 'Talk is cheap. Show me the code.'#13#10#13#10'– Linus Torvalds ';
    Quotes[4] := 'Every great developer you know got there by solving problems they were unqualified to solve until they actually did it.'#13#10#13#10'– Patrick McKenzie ';
    Quotes[5] := 'Formal education will make you a living. Self-education will make you a fortune.'#13#10#13#10'– Jim Rohn ';
    Quotes[6] := 'First do it, then do it right, then do it better.'#13#10#13#10'– Addy Osmani ';
    Quotes[7] := 'If you want to set off and go develop some grand new thing, you don''t need millions of dollars of capitalization. You need enough pizza and Diet Coke to stick in your refrigerator, a cheap PC to work on and the dedication to go through with it.'#13#10'– John Carmack ';
    Quotes[8] := 'Computers are useless. They can only give you answers.'#13#10#13#10'– Pablo Picasso ';
    Quotes[9] := 'The best way to predict the future is to invent it.'#13#10#13#10'– Alan Kay ';
    Quotes[10] := 'Programming isn''t about what you know; it''s about what you can figure out.'#13#10#13#10'– Chris Pine ';
    Quotes[11] := 'The only way to learn a new programming language is by writing programs in it.'#13#10#13#10'– Dennis Ritchie ';
    Quotes[12] := 'Code is like humor. When you have to explain it, it''s bad.'#13#10#13#10'– Cory House ';
    Quotes[13] := 'Just keep swimming!'#13#10#13#10'– Dory ';

    Result := Quotes[StrToInt(GetDateTimeString('ss', #0, #0)) mod Length(Quotes)];
end;

procedure InitializeWizard;
var
  DualWarningLabel: TLabel;
begin
  Upgraded := False;
  WizardForm.WelcomeLabel1.Caption := 'Welcome to using {#AppTitle}!';

  DualWarningLabel := TLabel.Create(WizardForm);

  if StartedForAllUsers() then
  begin
    WizardForm.WelcomeLabel2.Caption := 'This wizard will install {#AppTitle} {#AppVer} for all users.';
    if InstalledForThisUser() then
    begin    
      DualWarningLabel.Caption := 'Warning!'
          + ''#13#10''
          + ''#13#10''
          + 'Looks like you have already installed {#AppTitle} for your account. In order to avoid confusion, you may want to cancel this wizard and uninstall single-user {#AppTitle} first.';
    end
    else if InstalledForAllUsers() then
    begin
      WizardForm.WelcomeLabel2.Caption := 'This wizard will upgrade {#AppTitle} to version {#AppVer} for all users.';
      Upgraded := True;
    end;
  end
  else  // single user
  begin
    WizardForm.WelcomeLabel2.Caption := 'This wizard will install {#AppTitle} {#AppVer} for your account.';
    if InstalledForAllUsers() then
    begin    
      DualWarningLabel.Caption := 'Warning!'
        + ''#13#10''
        + ''#13#10''
        + 'Looks like {#AppTitle} is already installed for all users. In order to avoid confusion, you may want to cancel this wizard and uninstall all-users {#AppTitle} first.';
    end
    else if InstalledForThisUser() then
    begin
      WizardForm.WelcomeLabel2.Caption := 'This wizard will upgrade {#AppTitle} to version {#AppVer} for your account.';
      Upgraded := True;
    end;

  end;


  WizardForm.WelcomeLabel2.AutoSize := True;

  DualWarningLabel.Parent := WizardForm.WelcomePage;
  DualWarningLabel.AutoSize := True;
  DualWarningLabel.WordWrap := True;
  DualWarningLabel.Left := WizardForm.WelcomeLabel2.Left;
  DualWarningLabel.Width := WizardForm.WelcomeLabel2.Width;
  DualWarningLabel.Font.Style := [fsBold];
  //DualWarningLabel.Color := clRed;
  DualWarningLabel.Top := WizardForm.WelcomePage.Height - DualWarningLabel.Height - ScaleY(20);
  
  // Quotes
  QuoteLabel := TLabel.Create(WizardForm);
  QuoteLabel.Caption := GetRandomQuote();

  QuoteLabel.Parent := WizardForm.FinishedPage;

  // make accepting license the default
  WizardForm.LicenseAcceptedRadio.Checked := True;

end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  if (CurPageID = wpSelectDir) and (pos('&', WizardDirValue) > 1) then
  begin
    MsgBox('Directory paths containing "&" are known to cause problems in {#AppTitle}'
          + ''#13#10''
          + 'Please choose another directory!', mbError, MB_OK);
    Result := False
  end
  else
  begin
    Result := True
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
    if CurPageID = wpFinished then
    begin
      if Upgraded then
        WizardForm.FinishedLabel.Caption := '{#AppTitle} is now upgraded.'
      else
        WizardForm.FinishedLabel.Caption := '{#AppTitle} is now installed.';
      WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption 
        + ' Run it via shortcut or right-click a *.py file and select "Edit with Thonny AIA".';

      WizardForm.FinishedLabel.AutoSize := True;

      QuoteLabel.WordWrap := True;
      QuoteLabel.Width := round(WizardForm.FinishedLabel.Width * 0.8);
      QuoteLabel.AutoSize := True;
      
      // for some reason AutoSize doesn't work for longer quotes -- they are cropped from the bottom
      QuoteLabel.Height := ScaleY(60);
      if Length(QuoteLabel.Caption) > 100 then
      begin
        QuoteLabel.Height := ScaleY(80);
      end;
      QuoteLabel.Left := WizardForm.FinishedLabel.Left + (WizardForm.FinishedLabel.Width - QuoteLabel.Width);
      QuoteLabel.Alignment := taRightJustify;
      //QuoteLabel.Font.Style := [fsItalic]; // causes cropping in leftmost letters
      
      QuoteLabel.Top := WizardForm.FinishedPage.Height - QuoteLabel.Height - ScaleY(20);
    end;
end;

function Modern64BitInstallMarkerExists(RootKey: Integer): Boolean;
var
  InstallMode: String;
begin
  Result := False;
  if RegQueryStringValue(RootKey, ModernInstallKey, 'InstallMode', InstallMode) then
    Result := CompareText(InstallMode, ModernInstallModeValue) = 0;
end;

function InitializeSetup(): Boolean;
var
  Msg: String;
begin
  Result := True;

  if (InstalledForAllUsers() or InstalledForThisUser()) 
    and not (Modern64BitInstallMarkerExists(HKEY_CURRENT_USER) or Modern64BitInstallMarkerExists(HKEY_LOCAL_MACHINE)) then
  begin
      Msg := 'A previous {#AppTitle} installation created by an older installer was found.';

    Msg := Msg + #13#10#13#10 +
      'If you continue, {#AppTitle} may be installed twice.' + #13#10 +
      'It is recommended to uninstall the old version first.' + #13#10#13#10 +
      'Do you want to continue anyway?';

    if MsgBox(Msg, mbConfirmation, MB_YESNO) <> IDYES then
      Result := False;
  end; 
end;
