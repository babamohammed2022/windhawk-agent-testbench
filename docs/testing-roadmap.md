# Ridurre i test manuali Windows: roadmap concreta

## Obiettivo e confine

Il risultato utile non è «Wine diventa Windows»: è **spostare la maggior parte
dell'iterazione di sviluppo in test automatici**, lasciando a Windows le verifiche
che dipendono davvero dal sistema operativo. Per mod semplici/medie, classificare
per dipendenze è più utile che classificare per numero di righe:

| Tipo di mod | Cosa automatizzare localmente | Cosa resta su Windows |
| --- | --- | --- |
| Logica, impostazioni, trasformazioni di dati | Test di funzioni estratte dal codice della mod, casi limite, proprietà/invarianti | Integrazione col vero motore Windhawk |
| Hook di API pubbliche Win32 | Host controllato, stimoli ripetibili, confronto con/senza mod, assert su effetti | Differenze Wine/Windows e compatibilità col processo reale |
| Finestre, messaggi, GDI, subclassing | App Win32 di prova, eventi controllati, geometria, catene di messaggi, distruzione finestre | DPI, temi, font, focus, input, compositor e applicazione bersaglio |
| Explorer, taskbar, Start, simboli interni | Analisi, matrice simboli, logica isolabile | Windows **client**, shell reale e motore reale; Wine non è un sostituto |

Una mod di 30 righe su un simbolo privato della taskbar richiede più Windows di
una mod di 500 righe che trasforma dati tramite API pubbliche.

## Base aggiunta in questa iterazione

Queste sono funzionalità implementate nel codice, non una dichiarazione che gli
scenari runtime siano già stati validati:

- `awt check`: controlli statici offline su metadati, firme lifecycle comuni,
  impostazioni letterali e dipendenze interne. È un linter euristico, non un parser
  C++/YAML completo. Macro, raw string, compilazione condizionale, impostazioni
  annidate e chiavi dinamiche possono ingannarlo. Non cambia `verdict.level`.
- Override delle impostazioni per la build Wine/native Windows:
  `--settings key=value` ripetibile e `--settings-json file.json`.
  Precedenza: default della mod < JSON < flag ripetuti, ultimo valore vince.
  `build.effective_settings` e `build.settings_override_unknown` registrano cosa
  è stato richiesto, anche quando manca il compilatore. Le chiavi sconosciute
  vengono mantenute; il runner Wine aggiunge anche un warning statico.
- `awt batch`: directory ricorsive, glob o file, esecuzione sequenziale, JSON con
  tutti i report e JUnit. Un errore non interrompe la raccolta degli altri risultati.
  Non esegue casi in parallelo sullo stesso prefix. **Non** ripristina storage o
  registro tra casi. Il JSON batch evita di dipendere dal solo `report.json`, che
  resta il report dell'ultima esecuzione.
- Attività aggiunta al codice dell'host self-mode: lettura/scrittura file,
  finestra propria, messaggi, geometria, font/testo, letture registro,
  caricamento/risoluzione DLL e query di parametri di sistema. Il catalogo contiene
  20 API con marker `activity`. I marker precedono la chiamata: registrano un
  **tentativo**, non il successo dell'operazione né la correttezza dell'hook.
- `hook_coverage`: distingue log osservati (`fired`), API stimolate senza log
  (`driven_no_log`), assenza di marker (`not_driven`, con
  `driven_unknown` se la telemetria non è disponibile), e init non confermato
  (`init_not_confirmed`). Le firings sono ancora inferite dai log della mod:
  questa non è strumentazione indipendente. `wine_expectation` è un'aspettativa
  statica, non un risultato di conformance.
- Marker lifecycle: after-init, notifica delle impostazioni senza cambio dei
  valori, oppure overload che richiede reload. La richiesta di reload viene
  registrata, **non eseguita**. Non è ancora un test di modifica a caldo.
- `crash`: triage descrittivo da output host e codici di uscita host/target,
  con evidenze limitate. Un timeout non viene chiamato automaticamente deadlock;
  un frame Wine/ntdll/shim non viene usato per attribuire la colpa. La root cause
  resta sconosciuta. Le sezioni diagnostiche non cambiano la scala dei verdetti.
- Il builder ricompila l'host se cambiano sorgente o builder; la classificazione
  dei backend Windows/VM legge il sorgente, non il nome del file.

Esempi:

```bash
python3 -m awt.cli check examples/settings-demo.wh.cpp --json .sandbox/check.json
python3 -m awt.cli run examples/settings-demo.wh.cpp --mode self --settings retries=0
python3 -m awt.cli run examples/settings-demo.wh.cpp --mode self --settings enabled=false
python3 -m awt.cli batch examples/ --json .sandbox/batch.json --junit .sandbox/junit.xml
```

Override: il parser shim attuale supporta una mappa piatta di interi a 32 bit,
booleani (normalizzati a 0/1), stringhe UTF-8 senza escape JSON. Oggetti, array,
float, valori nulli, backslash, virgolette e caratteri di controllo sono rifiutati
negli override anziché testare silenziosamente valori alterati. Questa limitazione
non è risolta per tutti i default già presenti nelle mod.
Il protocollo VM non trasporta ancora correttamente gli override: richiederli al
backend VM produce `not_executed`, mai un'esecuzione con i default a insaputa
dell'utente. I controlli statici automatici sono collegati ai runner Wine e
Windows nativo disponibili; `check` è sempre utilizzabile separatamente.

**Exit code batch**, più severi del comando `run` storico:

- `0`: tutti hanno un livello di esecuzione positivo; non prova correttezza funzionale.
- `1`: almeno un build/load/crash/errore/esito sconosciuto, oppure timeout/crash
  esplicito nei dati runtime anche se il livello storico non lo riflette; per
  livelli positivi richiede anche init confermata e processo terminato con codice 0.
- `2`: uso/output non valido o nessuna mod scoperta.
- `3`: almeno un `not_executed`, se non ci sono fallimenti; anche nei batch misti.

Le mod dimostrative `broken`, `crash-demo` e `internal-symbol-demo` includono
fallimenti intenzionali: non aspettarsi un batch verde su `examples/` quando
l'esecuzione è disponibile. JUnit rappresenta questi esiti, non conosce ancora
manifest con risultati negativi attesi.

## Priorità successive

### P0 — Specificare e verificare il comportamento (massimo valore)

1. **Scenari dichiarativi per mod** con stimoli e risultati attesi: creare un file
   fixture, chiamare API con input precisi, creare/ridimensionare/distruggere una
   finestra, inviare messaggi, misurare l'effetto. Separare `pass`, `fail`, `skip`,
   `unsupported` per ogni assert, indipendentemente dal vecchio livello di verdict.
2. **Esecuzione differenziale senza/con mod**, con identici input e stato iniziale.
   Esempio: file A normalmente aperto, file A negato con mod attiva, file B ancora
   apribile. Un hook che fa solo log non deve soddisfare l'assert funzionale.
3. **Telemetria indipendente** del motore/host: hook richiesti/installati, errori
   MinHook, indirizzi/moduli target. Per le invocazioni usare probe controllati o
   wrapper che preservino ABI, registri, LastError e calling convention; non
   generare indiscriminatamente trampolini da regex C++. L'effetto osservato
   dall'host è comunque più forte di un contatore di hook.
4. **Controlli negativi**: la stessa suite deve fallire se la mod è disattivata,
   l'hook è rimosso, il risultato viene ignorato o l'originale non viene chiamata.

Criterio di uscita: fixture buone e mutate distinguibili, assert non soddisfacibili
semplicemente stampando il nome di un'API nel log, prove verificabili nel report.

### P0 — Lifecycle e isolamento affidabili

- Gestire init rifiutata, after-init, modifica effettiva delle impostazioni,
  richiesta di reload, before-uninit, rimozione hook e uninit una sola volta.
- Correggere l'attuale doppio percorso di cleanup host/shim: l'host chiama uninit
  e lo shim la richiama su detach. Evitare callback arbitrarie sotto loader lock.
  Verificare anche thread della mod ancora attivi durante unload.
- Ripetere load/unload nello stesso processo; verificare che i hook rimossi non
  puntino a codice scaricato e che lo stato non si accumuli.
- Workdir, file, storage e registro isolati per caso; timeout con arresto di tutto
  l'albero dei processi, cleanup garantito con `finally`, nessun `pkill` globale.
- Registrare ambiente, versioni toolchain/Wine, hash sorgente e binari, seed e
  impostazioni per rendere i risultati ripetibili. Wine non è un confine di
  sicurezza: mod non fidate richiedono isolamento a livello di sistema/VM.

Criterio di uscita: contatori delle callback esatti, nessun residuo fra due casi,
nessun processo abbandonato dopo un crash o un timeout intenzionale.

### P1 — Fedeltà della shim e compilazione Windhawk

- Confronti comportamentali con il vero motore, non solo firme conformi.
- Settings YAML reali, gruppi/array, chiavi indicizzate, Unicode/escape, ownership
  e lifetime delle stringhe, thread safety, storage e notifiche a caldo.
- Stessa toolchain/flag/header della distribuzione Windhawk di riferimento per
  distinguere bug della mod da incompatibilità MinGW. Aggiungere x86 solo con
  builder, shim, hook engine e target testati end-to-end; ARM64 separatamente.
- API stub sempre evidenti; simulazioni controllate solo per test unitari. Non
  far apparire implementati simboli privati che Wine non possiede.

Criterio di uscita: corpus di casi confrontato col motore reale e differenze note
esplicitamente registrate. Zero stub chiamati non equivale a correttezza.

### P1 — Libreria di scenari e matrici

- Fixture per file/registro in namespace dedicati, Win32 window/message/subclass,
  comctl32, GDI, timer, thread e concorrenza. Input e fallimenti controllati.
- Matrice impostazioni: default, zero, vuoto, estremi validi, combinazioni pairwise,
  valori malformati dove applicabili; riavvio e reload come casi distinti.
- Stress ripetibile, handle/thread/memoria prima e dopo con tolleranze esplicite;
  race/deadlock detection e stack/dump con simboli di debug.
- Screenshot a stato stabilizzato, geometria/controlli accessibili prima dei pixel;
  temi, DPI e font controllati. Un'immagine uguale non dimostra comportamento uguale.

Criterio di uscita: scenari che falliscono su regressioni intenzionali, non soltanto
più chiamate API o più screenshot.

### P1 — Calibrazione reale Wine ↔ Windows

Usare lo stesso corpus di scenari e mutazioni su Wine e su **Windows client con
Windhawk reale**, annotando build OS, motore, mod, toolchain e modalità di caricamento.
Il backend VM esistente è un punto di partenza da validare end-to-end: eseguire il
nostro host su Windows non equivale a eseguire Windhawk nel vero processo target.
Windows Server CI aiuta sulle API generiche, ma non valida la shell client.

Misurare per famiglia di mod: scenario coverage, test saltati, falsi verdi, falsi
rossi, flakiness e ripetibilità. Non assegnare percentuali di «compatibilità Windows»
prima di aver misurato questo confronto.

Criterio di uscita: pubblicare quali famiglie di assert concordano, su quali build,
e quali no. Solo allora decidere quali test Windows ripetuti possano essere omessi.

### P2 — Test Windows mirati, automatici e meno frequenti

Snapshot pulita → login interattivo → vero Windhawk → applicazione/shell reale →
scenario → log/dump/screenshot → ripristino. Build Windows client rappresentative,
DPI/tema/monitor diversi e risultati aggregati. Non riutilizzare una shell corrotta
per il caso successivo. Conservare un controllo prima del rilascio per integrazione
reale, injection, shell e simboli interni.

## Stato delle verifiche di questa iterazione

In questo ambiente non sono disponibili Wine, MinGW e Xvfb; non ci sono un runner
Windows eseguibile qui né una VM configurata. I test Python sono eseguibili, ma non
provano che l'host C++ compili o che gli hook funzionino sotto Wine/Windows.
Quattro nuove verifiche runtime sono incluse nella suite di accettazione per
attività host, coverage, lifecycle e override; devono essere eseguite dove la
toolchain è presente. Quando non lo è, la suite segnala lo skip con exit code 2.

**In sintesi:** prima assert osservabili e controlli negativi, poi fedeltà e matrice,
poi calibrazione Windows. Aggiungere API simulate senza questi tre passaggi aumenta
la superficie coperta dal codice, non la fiducia nei risultati.
