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

; Thonny AIA: the language of Windows decides the wizard's language (Jakub 2026-11-26): Czech
; on a Czech Windows, English on an English one, Czech (the first of [Languages]) on any
; other. Not the language of the previous installation, which preselected English after an
; English installation while the dialog itself spoke the language of Windows.
LanguageDetectionMethod=uilanguage
UsePreviousLanguage=no


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
Filename: "{app}\pythonw.exe"; Parameters: "-m compileall ."; StatusMsg: "{cm:CompilingLibrary}"
; "Launch Thonny AIA" on the last page, checked (Jakub 2026-11-25, from 5.5.0); postinstall runs
; it as the user who started the installer, not elevated; not after /SILENT or /VERYSILENT
Filename: "{app}\{#ExeName}"; Description: "{cm:LaunchProgram,{#AppTitle}}"; Flags: postinstall nowait skipifsilent


[UninstallDelete]
Type: filesandordirs; Name: "{app}\*"
Type: filesandordirs; Name: "{app}"
; the local model that Thonny AIA prepared (Ollama and the models, cortex-zamestnani#120);
; the user's settings in %APPDATA%\Thonny AIA stay, like upstream's
Type: filesandordirs; Name: "{localappdata}\Thonny AIA\local-models"
Type: dirifempty; Name: "{localappdata}\Thonny AIA"

[Messages]
english.FinishedHeadingLabel=Great success!
czech.FinishedHeadingLabel=Povedlo se!

; Thonny AIA: the texts that upstream's [Code] wrote in English whatever the language chosen
; (Jakub 2026-11-26: the first and the last page were English with Czech selected). Czech is
; informal like AIA's own texts; %1 is the application's name, %2 its version.
[CustomMessages]
english.WelcomeTitle=Welcome to using %1!
english.InstallAllUsers=This wizard will install %1 %2 for all users.
english.UpgradeAllUsers=This wizard will upgrade %1 to version %2 for all users.
english.InstallThisUser=This wizard will install %1 %2 for your account.
english.UpgradeThisUser=This wizard will upgrade %1 to version %2 for your account.
english.DualWarningThisUser=Warning!%n%nLooks like you have already installed %1 for your account. In order to avoid confusion, you may want to cancel this wizard and uninstall single-user %1 first.
english.DualWarningAllUsers=Warning!%n%nLooks like %1 is already installed for all users. In order to avoid confusion, you may want to cancel this wizard and uninstall all-users %1 first.
english.NowUpgraded=%1 is now upgraded.
english.NowInstalled=%1 is now installed.
english.HowToRun=Run it via shortcut or right-click a *.py file and select "Edit with %1".
english.AmpersandInPath=Directory paths containing "&" are known to cause problems in %1%nPlease choose another directory!
english.OldInstallation=A previous %1 installation created by an older installer was found.%n%nIf you continue, %1 may be installed twice.%nIt is recommended to uninstall the old version first.%n%nDo you want to continue anyway?
english.CompilingLibrary=Compiling standard library... (takes a while)
czech.WelcomeTitle=Vítej v %1!
czech.InstallAllUsers=Tento průvodce nainstaluje %1 %2 pro všechny uživatele.
czech.UpgradeAllUsers=Tento průvodce aktualizuje %1 na verzi %2 pro všechny uživatele.
czech.InstallThisUser=Tento průvodce nainstaluje %1 %2 pro tvůj účet.
czech.UpgradeThisUser=Tento průvodce aktualizuje %1 na verzi %2 pro tvůj účet.
czech.DualWarningThisUser=Pozor!%n%nVypadá to, že pro tvůj účet už instalace %1 existuje. Aby v tom nebyl zmatek, můžeš tohoto průvodce zrušit a napřed odinstalovat %1 nainstalované pro jeden účet.
czech.DualWarningAllUsers=Pozor!%n%nVypadá to, že instalace %1 pro všechny uživatele už existuje. Aby v tom nebyl zmatek, můžeš tohoto průvodce zrušit a napřed odinstalovat %1 nainstalované pro všechny uživatele.
czech.NowUpgraded=Aktualizace %1 je hotová.
czech.NowInstalled=Instalace %1 je hotová.
czech.HowToRun=Spustíš ho zástupcem, nebo klikni pravým tlačítkem na soubor *.py a vyber „Edit with %1“.
czech.AmpersandInPath=Cesty ke složkám se znakem „&“ dělají v %1 potíže.%nVyber prosím jinou složku!
czech.OldInstallation=Byla nalezena předchozí instalace %1 vytvořená starším instalátorem.%n%nKdyž budeš pokračovat, může být %1 nainstalované dvakrát.%nDoporučujeme starou verzi napřed odinstalovat.%n%nChceš přesto pokračovat?
czech.CompilingLibrary=Kompiluje se standardní knihovna... (chvíli to trvá)
english.Quote0=Ninety-ninety rule: The first 90 percent of the code accounts for the first 90 percent of the development time. The remaining 10 percent of the code accounts for the other 90 percent of the development time.%n– Tom Cargill 
english.Quote1=Now is better than never.%n– Tim Peters 
english.Quote2=Give someone a program, frustrate them for a day; teach them how to program, frustrate them for a lifetime.%n%n– David Leinweber 
english.Quote3=Talk is cheap. Show me the code.%n%n– Linus Torvalds 
english.Quote4=Every great developer you know got there by solving problems they were unqualified to solve until they actually did it.%n%n– Patrick McKenzie 
english.Quote5=Formal education will make you a living. Self-education will make you a fortune.%n%n– Jim Rohn 
english.Quote6=First do it, then do it right, then do it better.%n%n– Addy Osmani 
english.Quote7=If you want to set off and go develop some grand new thing, you don't need millions of dollars of capitalization. You need enough pizza and Diet Coke to stick in your refrigerator, a cheap PC to work on and the dedication to go through with it.%n– John Carmack 
english.Quote8=Computers are useless. They can only give you answers.%n%n– Pablo Picasso 
english.Quote9=The best way to predict the future is to invent it.%n%n– Alan Kay 
english.Quote10=Programming isn't about what you know; it's about what you can figure out.%n%n– Chris Pine 
english.Quote11=The only way to learn a new programming language is by writing programs in it.%n%n– Dennis Ritchie 
english.Quote12=Code is like humor. When you have to explain it, it's bad.%n%n– Cory House 
english.Quote13=Just keep swimming!%n%n– Dory 
czech.Quote0=Pravidlo devadesát na devadesát: Prvních 90 procent kódu zabere prvních 90 procent času vývoje. Zbývajících 10 procent kódu zabere dalších 90 procent času vývoje.%n– Tom Cargill 
czech.Quote1=Teď je lepší než nikdy.%n– Tim Peters 
czech.Quote2=Dej někomu program a potrápíš ho na jeden den; nauč ho programovat a potrápíš ho na celý život.%n%n– David Leinweber 
czech.Quote3=Řeči jsou laciné. Ukaž mi kód.%n%n– Linus Torvalds 
czech.Quote4=Každý skvělý vývojář, kterého znáš, se jím stal tak, že řešil problémy, na které nestačil, dokud je opravdu nevyřešil.%n%n– Patrick McKenzie 
czech.Quote5=Formální vzdělání ti zajistí živobytí. Samostudium ti přinese jmění.%n%n– Jim Rohn 
czech.Quote6=Nejdřív to udělej, pak to udělej správně, pak to udělej líp.%n%n– Addy Osmani 
czech.Quote7=Když se chceš pustit do vývoje nějaké velké nové věci, nepotřebuješ miliony dolarů kapitálu. Potřebuješ dost pizzy a dietní koly v ledničce, levné PC na práci a odhodlání to dotáhnout.%n– John Carmack 
czech.Quote8=Počítače jsou k ničemu. Umějí dávat jen odpovědi.%n%n– Pablo Picasso 
czech.Quote9=Nejlepší způsob, jak předpovědět budoucnost, je vynalézt ji.%n%n– Alan Kay 
czech.Quote10=Programování není o tom, co víš, ale o tom, na co dokážeš přijít.%n%n– Chris Pine 
czech.Quote11=Jediný způsob, jak se naučit nový programovací jazyk, je psát v něm programy.%n%n– Dennis Ritchie 
czech.Quote12=Kód je jako vtip. Když ho musíš vysvětlovat, je špatný.%n%n– Cory House 
czech.Quote13=Prostě plav dál!%n%n– Dory 



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
begin
    // Quote0 .. Quote13 in [CustomMessages], in the language of the wizard
    Result := CustomMessage('Quote' + IntToStr(StrToInt(GetDateTimeString('ss', #0, #0)) mod 14));
end;

procedure InitializeWizard;
var
  DualWarningLabel: TLabel;
begin
  Upgraded := False;
  WizardForm.WelcomeLabel1.Caption := FmtMessage(CustomMessage('WelcomeTitle'), ['{#AppTitle}']);

  DualWarningLabel := TLabel.Create(WizardForm);

  if StartedForAllUsers() then
  begin
    WizardForm.WelcomeLabel2.Caption := FmtMessage(CustomMessage('InstallAllUsers'), ['{#AppTitle}', '{#AppVer}']);
    if InstalledForThisUser() then
    begin    
      DualWarningLabel.Caption := FmtMessage(CustomMessage('DualWarningThisUser'), ['{#AppTitle}']);
    end
    else if InstalledForAllUsers() then
    begin
      WizardForm.WelcomeLabel2.Caption := FmtMessage(CustomMessage('UpgradeAllUsers'), ['{#AppTitle}', '{#AppVer}']);
      Upgraded := True;
    end;
  end
  else  // single user
  begin
    WizardForm.WelcomeLabel2.Caption := FmtMessage(CustomMessage('InstallThisUser'), ['{#AppTitle}', '{#AppVer}']);
    if InstalledForAllUsers() then
    begin    
      DualWarningLabel.Caption := FmtMessage(CustomMessage('DualWarningAllUsers'), ['{#AppTitle}']);
    end
    else if InstalledForThisUser() then
    begin
      WizardForm.WelcomeLabel2.Caption := FmtMessage(CustomMessage('UpgradeThisUser'), ['{#AppTitle}', '{#AppVer}']);
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
    MsgBox(FmtMessage(CustomMessage('AmpersandInPath'), ['{#AppTitle}']), mbError, MB_OK);
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
        WizardForm.FinishedLabel.Caption := FmtMessage(CustomMessage('NowUpgraded'), ['{#AppTitle}'])
      else
        WizardForm.FinishedLabel.Caption := FmtMessage(CustomMessage('NowInstalled'), ['{#AppTitle}']);
      WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption 
        + ' ' + FmtMessage(CustomMessage('HowToRun'), ['{#AppTitle}']);

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
      // Thonny AIA: the longest quotes take five lines and the author's line was cut off
      if Length(QuoteLabel.Caption) > 150 then
      begin
        QuoteLabel.Height := ScaleY(130);
      end;
      QuoteLabel.Left := WizardForm.FinishedLabel.Left + (WizardForm.FinishedLabel.Width - QuoteLabel.Width);
      QuoteLabel.Alignment := taRightJustify;
      //QuoteLabel.Font.Style := [fsItalic]; // causes cropping in leftmost letters
      
      QuoteLabel.Top := WizardForm.FinishedPage.Height - QuoteLabel.Height - ScaleY(20);

      // Thonny AIA: the list with "Launch Thonny AIA" reaches the bottom of the page and, being
      // a window, covered the quote (a label drawn on the page); it ends above the quote now
      if WizardForm.RunList.Visible then
      begin
        WizardForm.RunList.Top := WizardForm.FinishedLabel.Top + WizardForm.FinishedLabel.Height + ScaleY(20);
        WizardForm.RunList.Height := QuoteLabel.Top - WizardForm.RunList.Top - ScaleY(8);
      end;
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
    Msg := FmtMessage(CustomMessage('OldInstallation'), ['{#AppTitle}']);

    if MsgBox(Msg, mbConfirmation, MB_YESNO) <> IDYES then
      Result := False;
  end; 
end;
