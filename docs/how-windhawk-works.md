# Come funziona Windhawk (e dove questo testbench si ferma)

Documento introduttivo in italiano, scritto per gli agenti e per chi usa questo
testbench. Non è documentazione ufficiale: riassume, con parole proprie, fonti
pubbliche verificate il 2026-11-26. Non contiene codice copiato da Windhawk.
Il progetto Windhawk è di m417z/Ramensoftware; il suo repository engine è
licenziato **GPL-3.0**. Questo repository resta MIT e non include sorgente
dell'engine.

Fonti primarie:

- [Windhawk — README e architettura ad alto livello](https://github.com/ramensoftware/windhawk) (in particolare `README.md` e `diagram.png`);
- [Wiki ufficiale — Creare una nuova mod](https://github.com/ramensoftware/windhawk/wiki/Creating-a-new-mod);
- [Wiki ufficiale — Ciclo di vita di una mod](https://github.com/ramensoftware/windhawk/wiki/Mod-lifetime);
- [Wiki ufficiale — Processi bersaglio e processi di sistema](https://github.com/ramensoftware/windhawk/wiki/Injection-targets-and-critical-system-processes);
- [Wiki ufficiale — Mod come tool in un processo dedicato](https://github.com/ramensoftware/windhawk/wiki/Mods-as-tools%3A-Running-mods-in-a-dedicated-process);
- [Wiki ufficiale — Oggetti globali e chiusura del processo](https://github.com/ramensoftware/windhawk/wiki/Global-objects-and-process-shutdown);
- [Wiki ufficiale — Consigli di sviluppo e Windhawk Utils](https://github.com/ramensoftware/windhawk/wiki/Development-tips);
- gli header ufficiali già cached in `docs/official/` (`windhawk_api.h`,
  `windhawk_api_internal.h`, `windhawk_utils.h`, `mods_api.h`).

## 1. I pezzi del sistema

Windhawk non è un eseguibile che "modifica" un altro programma scrivendone il
file sul disco. È una piattaforma di iniezione e hooking a runtime:

| Parte | Ruolo |
|---|---|
| Interfaccia utente (`windhawk.exe`) | installa, abilita e configura le mod; mostra i log. |
| Engine (`windhawk.dll`, 32-bit e 64-bit) | viene iniettato nei processi bersaglio e carica le mod al posto giusto. |
| Servizio (installazione normale) / daemon (portable) | fa parte dell'installazione; la wiki documenta i livelli di privilegio che ciascuno dei due modi può dare alle tool mod. L'iniezione globale è descritta come comportamento dell'engine. |
| Mod (`.wh.cpp` → DLL) | un singolo file C++ per mod, compilato con gli header di Windhawk. |

Il README ufficiale descrive il progetto come "rendere più facile personalizzare
i programmi Windows" e rimanda a un articolo sul metodo di iniezione globale e
hooking usato; i dettagli implementativi dell'engine non sono replicati qui.

Perché l'iniezione è globale? Per intercettare la creazione di nuovi processi e
caricare le mod **prima** che il processo bersaglio inizi a girare: alcune mod
funzionano solo se sono presenti così presto. Windhawk inietta l'engine nella
maggior parte dei processi, tranne liste predefinite di processi critici o
incompatibili (per esempio `csrss.exe`, `lsass.exe`, `dwm.exe`, giochi con
anti-cheat) e liste configurabili dall'utente. Esiste anche un elenco di
processi di sistema esclusi dalle sole corrispondenze con wildcard: in quei
processi una mod con `@include *` non entra, serve un nome esplicito.

## 2. Dalla mod sorgente alla DLL

Una mod è un file C++ con tre blocchi opzionali:

1. `==WindhawkMod==`: metadati (`@id`, `@name`, `@version`, `@author`,
   `@include`, `@exclude`, `@architecture`, `@compilerOptions`, `@license`, ...).
   `@include`/`@exclude` sono nomi o percorsi con wildcard (`*`, `?`) e
   variabili d'ambiente come `%SystemRoot%`; una mod è selezionata quando almeno
   un include corrisponde e nessun exclude lo vieta. Alcuni campi, come
   `@name`, possono essere localizzati (`@name:it-IT`).
2. `==WindhawkModReadme==`: testo Markdown mostrato nell'interfaccia.
3. `==WindhawkModSettings==`: YAML con nome, valore predefinito e annotazioni
   di presentazione (`$name`, `$description`, `$options`). I valori possono
   essere booleani, interi, stringhe, array e gruppi annidati. La versione
   1.7.3 accetta solo quelle tre annotazioni; la 2.0 alpha ne aggiunge altre
   (`$format`, `$float`, `$min`/`$max`, `$dynamicSelect`, `$showIf`/`$hideIf`).

Gli header definiscono le funzioni `Wh_*` che la mod chiama e le callback che
l'engine chiama nella mod. Il compilatore ufficiale genera una DLL per
architettura; `@architecture` può dichiarare `x86`, `amd64`, `arm64` oppure
`x86-64`, il cui significato su ARM64 dipende dall'elenco di processi noti
(StartMenuExperienceHost, explorer.exe, notepad.exe e altri).

## 3. Il ciclo di vita dentro il processo bersaglio

La sequenza documentata nella wiki è:

```
Wh_ModInit  →  applicazione implicita delle operazioni di hook  →  Wh_ModAfterInit
                                  ↓
                    in attesa di eventi (engine thread)
                    ↙ settings cambiati        ↘ disabilitazione / unload
        Wh_ModSettingsChanged          Wh_ModBeforeUninit
                                          → rimozione degli hook
                                          → Wh_ModUninit
```

Punti importanti, spesso causa di falsi negativi nei test:

- Se la mod viene caricata **prima** che il processo inizi, `Wh_ModInit` e
  `Wh_ModAfterInit` girano sul thread principale; le notifiche successive e
  l'unload girano su un thread dell'engine. Se la mod viene caricata in un
  processo già in esecuzione, tutti gli eventi sono sul thread dell'engine.
- `Wh_ModInit` può restituire `FALSE` per rifiutare il carico: è un comportamento
  normale di mod difensive, non un errore dell'ambiente.
- Gli hook si registrano con `Wh_SetFunctionHook` (e si possono rimuovere con
  `Wh_RemoveFunctionHook`), ma l'engine li applica in blocco subito dopo
  `Wh_ModInit`. Chiamare `Wh_ApplyHookOperations` a mano è possibile ma lento:
  gli header consigliano di registrarli tutti in `Wh_ModInit`.
- `Wh_ModBeforeUninit` arriva **prima** della rimozione degli hook;
  `Wh_ModUninit` dopo. È qui che la mod deve fermare thread e scaricare
  risorse di cui gli hook avevano bisogno.
- **All'uscita del processo host Windhawk non chiama `Wh_ModBeforeUninit` né
  `Wh_ModUninit`.** Girano solo i distruttori degli oggetti globali, in un
  contesto ostile (altri thread già terminati, loader lock, subsystem COM/XAML
  già smontati). Un kill forzato non esegue nulla. Per questo le mod con thread
  o COM/WinRT globali usano pulizia esplicita in `Wh_ModUninit` e, quando
  servono, `[[clang::no_destroy]]`.

## 4. Le API che l'engine mette a disposizione

- **Log**: `Wh_Log`, con formato printf-like; il testo può includere il numero
  di riga e, in editing mode, il contesto della mod.
- **Settings**: `Wh_GetIntSetting` / `Wh_GetStringSetting` leggono i valori
  dell'utente. I nomi possono contenere formattazione printf, per esempio
  `Wh_GetIntSetting(L"items[%d].size", i)`, usata per gruppi e array.
  `Wh_FreeStringSetting` libera la stringa restituita.
- **Storage locale**: `Wh_Get`/`Wh_Set` per valori interi, stringa e binari,
  `Wh_DeleteValue`, `Wh_GetModStoragePath`: una directory per mod, distinta
  dalle impostazioni utente.
- **Hook di funzioni**: `Wh_SetFunctionHook` riceve puntatore alla funzione
  target, alla funzione detour e (opzionalmente) all'indirizzo di una trampolina
  che chiama l'originale. Sono operazioni registrate e applicate in blocco.
- **Simboli**: `Wh_FindFirstSymbol` / `Wh_FindNextSymbol` / `Wh_FindCloseSymbol`
  enumerano simboli di un modulo (PDB/export); `WindhawkUtils::HookSymbols`
  è il wrapper che risolve e hookinga una tabella di simboli e mette in cache
  il risultato per versione di binario. È il meccanismo con cui le mod di shell
  agganciano membri interni di Explorer/twinui senza puntatori fissi.
- **Utilità** (`windhawk_utils.h`): wrapper tipizzati come
  `WindhawkUtils::SetFunctionHook`, `StringSetting` (RAII per le stringhe),
  `SetWindowSubclassFromAnyThread` (subclassing cross-thread).
- **Altro**: `Wh_Disasm`, `Wh_GetUrlContent`, opzioni dinamiche delle
  impostazioni (queste ultime sono un'aggiunta della 2.0 alpha).

Le firme esatte sono negli header ufficiali: si noti che alcune API hanno
versioni minime (`@since`), per esempio `Wh_DeleteValue` dalla 1.5 e
`Wh_GetUrlContent` dalla 1.5.

## 5. Tool mod: una mod in un processo tutto suo

Una mod normale viene caricata nel processo che corrisponde ai suoi include.
Una *tool mod* invece esegue la propria logica in un processo dedicato, utile
per attività di sistema che non devono vivere dentro Explorer.

- In Windhawk 1.7.3 la ricetta documentata è un modello che, nella mod, avvia
  un secondo `windhawk.exe` con un argomento `-tool-mod <id>`, usa un mutex per
  evitare duplicati e sposta la logica nelle callback `WhTool_*`. La mod
  targeting `windhawk.exe` distingue l'istanza "launcher" da quella hosting.
- In Windhawk 2.0 alpha questa modalità è di prima classe: esistono host
  dedicati (`windhawk-mod.exe`, variante UIAccess e variante elevated) e un
  header `windhawk_tool_mod.h`; l'host è scelto dal nome nell'@include e dal
  livello di privilegio disponibile.

Per un agente significa che una tool mod **non** è una mod che "carica in tutti"
e il suo comportamento non è dimostrabile con un host in-process.

## 6. Cosa riproduce questo testbench, e cosa no

| Realtà Windhawk | Testbench |
|---|---|
| Servizio/daemon + engine iniettato in quasi tutti i processi, prima dell'avvio | Nessun servizio; host in-process (`awt-host.exe`) o tentativo di iniezione che Wine non supporta in modo affidabile. |
| Mod caricate nel contesto del processo bersaglio vero | DLL caricata con `LoadLibraryW` nel proprio host, oppure stand-in dichiarati (`testapp.exe`, `trayapp.exe`, desktop simulata). |
| `Wh_*` implementati dall'engine con storage, settings e simboli reali | Shim locale: API principali implementate, simboli e alcune API deliberatamente stub. |
| PDB/export risolti per la build esatta di Windows | Nessun PDB a runtime; `awt/symbols/` controlla solo i **nomi** offline. |
| Rimozione degli hook e callback d'unload gestite dall'engine | Parziale: l'integrità byte-per-byte dopo l'unload non è verificata; l'host chiama le callback anche su detach della DLL, diversamente da Windhawk. |
| Explorer, Start/XAML, DWM, COM/WinRT reali | Stand-in Win32; superfici `simulated` o `not_verifiable` in `shell_surfaces`. |

Il testbench non sostituisce Windhawk e non lo implementa: risponde a "questa
mod compila, carica, chiama API pubbliche senza crashare nel sandbox?" e dice
esplicitamente quali domande restano aperte. Per il significato di `verified`,
`simulated` e `not_verifiable`, vedere `AGENTS.md` e `docs/limitations.md`.
