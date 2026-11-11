# Thonny AIA pro macOS (Apple Silicon)

Samostatná macOS aplikace z Thonny AIA 5.5.0 beta 7. Obsahuje Python 3.14,
Tk pro macOS a SDK pro Ollama, Anthropic, OpenAI a Google. Python ani Homebrew
není potřeba instalovat. Rozhraní zůstává původní Tk/Aqua, nejde o přepis do SwiftUI.

## Instalace

Otevřete `thonny-aia-5.5.0b7-macos-arm64.dmg` a přetáhněte **Thonny AIA.app**
do složky Applications. Spusťte aplikaci z Finderu. Vyberte poskytovatele AI,
nebo nastavte AI později. Ollama se instaluje samostatně. Model se nevybírá
automaticky: výchozí je `gpt-oss:20b`, jiný model, který má Ollama v počítači,
vyberete ve Volbách → AI asistent v poli „Model:“. Na Macu s menší pamětí
stáhněte menší model (`ollama pull …`) a vyberte ho tam.

Toto sestavení má lokální ad-hoc podpis, nemá Apple Developer ID ani notarizaci.
Pokud macOS staženou aplikaci zablokuje, povolte ji v Nastavení systému →
Soukromí a zabezpečení → Přesto otevřít.

Nastavení jsou v `~/Library/Thonny AIA`, odděleně od původního Thonny.
API klíče jsou stejně jako v původním vydání uloženy v konfiguračním souboru.
Podporovaná architektura: arm64. Intel verze není součástí tohoto sestavení.

## Sestavení

Na Apple Silicon Macu s Pythonem 3 a Xcode Command Line Tools spusťte z kořene:

```sh
python3 packaging/mac/build_aia.py
```

Skript stáhne oficiální balíček Thonny 5.0.0, ověří jeho SHA-256 a rozbalí jej
bez instalace do systému. Do jeho přenositelného Pythonu nainstaluje tento
zdrojový strom včetně AI SDK. Přidá nativní arm64 spouštěč, vlastní identitu,
ikonu, ad-hoc podpis a vytvoří `.app` a `.dmg` ve složce `dist/`.
Existující výstup `dist/Thonny AIA.app` při opakovaném sestavení nahradí.
Konkrétní verze závislostí jsou v `dist/bundled-requirements.txt`, informace
o zdrojích v `Contents/Resources/build-info.json`. Závislosti se při novém
sestavení řeší znovu podle `pyproject.toml`.

Původní projekt: https://github.com/senkyr/thonny-aia/releases/tag/v5.5.0b7-aia
Runtime: https://github.com/thonny/thonny/releases/tag/v5.0.0
Licence: MIT; přibalené komponenty zachovávají vlastní licence.
