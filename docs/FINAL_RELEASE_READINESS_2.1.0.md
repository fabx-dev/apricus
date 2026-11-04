# FINAL RELEASE READINESS — APRICUS 2.1.0

Rapporto unico conclusivo della finalizzazione locale; sostituisce come verdetto
corrente le liste di gate storiche nei rapporti precedenti. Nessuna pubblicazione.

## 1. Commit sorgente definitivo

**PASS** — sorgente funzionalmente congelato e baseline comune dei pacchetti:
`6ed08690ca9ad117bdec0086c10972855e7a28d9`, ramo locale `release-2.1.0-prep`.
Sigilla i metadati stabili con data **2026-11-04**, confermata dal maintainer.
Deriva dalla baseline verificata `dd5c729`, da `4daaaae` (rapporto conclusivo)
e `c6ecfc5` (x15 accettata).
La sola modifica applicativa successiva è un testo di indisponibilità Flatpak
nel control center. Nessuna modifica al player, HTTPS, policy, audio,
interazione o estensione rev8. Snap fissa esattamente le versioni yt-dlp/EJS
già accettate su x15; non aggiorna il resolver.

**PASS** — data, versione e stato stabile sigillati nel commit sorgente comune.
Nessun tag esiste o è stato creato: preparazione stabile non significa pubblicazione.

## 2. Stato della worktree e repository

**PASS** — worktree pulita dopo il commit locale; `git fsck --full --no-reflogs`
termina senza errore. Submodule shared-modules:
`b8236c7961e3dd447b1b9605375a54a12f9f24b6`.
I commit conclusivi di documentazione non modificano la baseline
applicativa/metadati/packaging `6ed0869` dei binari.
Nessun tag locale `v2.1.0` creato. Nessun push, merge remoto, release,
workflow remoto, upload, promozione o modifica di credenziali/permessi Store.

**PASS** — rifinitura editoriale README autorizzata dopo `5807d0d`: corretti
policy di ripresa, novità Interact, requisiti XWayland/rev8, installazione
Flatpak/RPM, accesso a Done e stato di finalizzazione locale. Preferiti e
cronologia web confermati dal codice; limiti Flatpak/YouTube conservati.
Il README della release non è incluso nei payload DEB, RPM, Snap o Flatpak:
verificati contenuto e percorsi negli artefatti estratti. Nessuna rigenerazione
necessaria; tutti i checksum della sezione 5 restano invariati. Le sole
modifiche successive interessano README e questa annotazione, non eseguibili,
metainfo, permessi o immagini. Collegamenti locali e sintassi dei comandi
verificati senza eseguire installazioni, build o accessi remoti.

**PASS** — scansione dei file tracciati: nessuna chiave privata, token GitHub,
chiave AWS o URL media firmato riconosciuti dai pattern; nessun percorso home
personale, pacchetto binario, log, assertion o chiave privata tracciato.
È una verifica mirata, non una certificazione dell'assenza di ogni segreto
possibile. I materiali diagnostici restano fuori dal repository e dagli upload.

**NOT TESTED** — punta remota e stato corrente PR/regole GitHub: nessun fetch o
intervento remoto in questa fase. La procedura futura deve riconciliarli prima
di integrare, senza assumere aggiornato il vecchio draft PR #1.

## 3. Test automatici e verifiche funzionali

| Verifica | Esito | Ambito |
|---|---|---|
| pytest sulla baseline funzionale invariata | **PASS** | 668 passati, 5 saltati, 31 warning, 42,96 s su dd5c729; non ripetuti per soli metadata |
| Test saltati | **NOT TESTED** | Non trasformati in PASS |
| Ruff lint e format | **PASS** | src e tests; 96 file già formattati |
| Diff whitespace | **PASS** | `git diff --check` |
| Harness GJS | **PASS** | extension-state, extension-rescan, status-bridge, window-push, web-interaction |
| Accettazione desktop x14 / affinamento x15 | **PASS** | Rapporti precedenti, ambiti distinti sotto |
| Sensor-to-visible-pause ≤500 ms | **NOT TESTED** | Nessuna certificazione temporale rigorosa |
| Audit screen-reader/contrasto completo | **NOT TESTED** | Tastiera Done verificata, non equivale a audit completo |
| Perdita reale estensione/owner e riconfigurazione monitor/workspace sul pacchetto finale | **NOT TESTED** | Copertura automatica non presentata come collaudo desktop |
| Verifiche mirate dopo sigillo data | **PASS** | Metainfo compilato/validato offline, versione/data coerenti, confronto completo payload estratti, manifest/permessi/dipendenze e checksum |

Il PASS funzionale A/V e interazione appartiene a x14/`afbbcb2`, seguito dal
PASS x15/`c6ecfc5` del messaggio di stato con parità runtime protetta verificata:
ingresso/uscita ripetuti, DOM conservato, Esc dei dialoghi HTML, shortcut,
Done fullscreen e da tastiera, finestre minimizzate/massimizzate intatte,
Done dopo chiusura GUI, recupero limitato da JS bloccato, Stop/Resume,
cambio sorgente, pausa automatica senza rientro, icone e Overview;
video locale udibile/in movimento, pausa immediata, ripresa, mute e un campione
YouTube funzionante. Nessuna nuova campagna manuale ripetuta.
Le identità native osservate nel test privato non sono identità strumentate
della sessione visibile. La causa dell'anomalia storica di minimizzazione
rimane non dimostrata.

## 4. Flatpak e parità delle funzionalità

**FAIL** — parità di **Interact with webpage** con architettura congelata rev8.
È una differenza funzionale documentata, non un'autorizzazione ad aggirare il sandbox.

**PASS** — diagnosi mirata: il namespace applicativo contiene `fabx_dev`, mentre
il bridge posseduto dal server è `io.github.fabxdev.Apricus.GnomeBridge`.
Sul bus privato con `Bridge` reale, policy attuale: nessuna ownership;
solo ownership esatta: Register autenticato rifiutato;
ownership esatta più sola visibilità `org.gnome.Shell`: Register accettato.
`org.gnome.Shell.Extensions` non sostituisce il controllo owner di
`org.gnome.Shell`. Nessun accesso indiscriminato o `talk` generico alla Shell
è stato aggiunto.

**PASS** — evidenza indipendente del limite PID: nel sandbox il processo vede
PID 2; rev8 confronta il PID del player con quello host di Mutter, oltre a
token/titolo/ruolo. Le sole autorizzazioni D-Bus non risolvono questo binding.
Una traduzione autenticata dei namespace richiede un cambiamento di protocollo
fuori dal congelamento funzionale. Non è stato indebolito alcun controllo PID.

**PASS** — manifest Flatpak invariato: rete, IPC, DRI, X11/Wayland, audio,
Videos in sola lettura e nomi D-Bus specifici già presenti. Nessun nuovo
permesso host, wildcard o socket bus. GUI e documentazione non suggeriscono
più che basti aggiornare l'estensione per ottenere Interact su Flatpak.

**NOT TESTED** — revisione/approvazione Flathub; non è stata effettuata alcuna
submission. La conservazione di permessi stretti non equivale ad approvazione.
La visualizzazione delle pagine resta distinta dall'interazione; M3 viene
esposto solo dopo una verifica runtime positiva. Dettagli tecnici in
`docs/FLATPAK_INTERACTION_2.1.0.md`.

**NOT TESTED** — decisione di pubblicazione Flatpak: separata e non autorizzata.
App e companion locale restano nel set locale preparato, non vengono automaticamente
inclusi in alcun futuro upload.

## 5. Pacchetti finali e checksum SHA-256

**PASS** — quattro formati stabili allineati al commit `6ed0869`, versione
2.1.0, data **2026-11-04**, metainfo `type="stable"`. Ricompilato offline
soltanto il metainfo tradotto con Meson/gettext; riconfezionati DEB, RPM, Snap
e Flatpak dai payload già verificati. Nessuna dipendenza riscaricata o
ricompilata. Il confronto completo dei payload estratti dagli artefatti finali
consente una sola differenza rispetto a dd5c729: il metainfo compilato.
Tutti gli altri file, bit di modalità, symlink e runtime rimangono identici.
Locale Flatpak e ZIP rev8 sono riutilizzati byte per byte, non ricompilati.
Il confronto ha scartato un primo export Flatpak dalla staging combinata,
che includeva anche dati split Debug/Locale; il definitivo parte dal precedente
payload app-only esportato e ammette esclusivamente il delta metainfo.
L'intermedio scartato e i log rimangono fuori dalla directory degli artefatti finali.

Directory locale dei pacchetti: `../apricus-stable-2.1.0-20261104/packages/`
(relativa alla radice del repository).
I checksum del set locale completo sono in `packages/SHA256SUMS.txt` e riportati
sotto. Lo ZIP host rev8 è un asset aggiuntivo; il Flatpak locale è
un companion bundle, non un quinto formato applicativo.

| Formato / verifica | Esito | Ambito effettivo |
|---|---|---|
| DEB amd64 install/dipendenze, baseline dd5c729 | **PASS** | Ubuntu 24.04 isolato, apt risolve le dipendenze; versione 2.1.0, dpkg audit senza problemi |
| RPM x86_64 install/dipendenze, baseline dd5c729 | **PASS** | Fedora 43 isolato con RPM Fusion free; rpm verifica payload senza modifiche |
| DEB/RPM avvio e comandi, baseline dd5c729 | **PASS** | Xvfb e bus privato: server/player, Pause/Resume, Stop/Resume, sorgente web, richiesta GUI e Quit senza traceback |
| DEB/RPM resa desktop/audio udibile | **NOT TESTED** | Nessun nuovo collaudo visivo/uditivo, né prova rev8 su Fedora |
| Flatpak app e locale build/import | **PASS** | GNOME 50; bundle importati in repo locale vuoto, commit OSTree uguali al build per app e locale |
| Flatpak avvio/comandi in staging, baseline dd5c729 | **PASS** | Payload read-only in sandbox, Xvfb e bus privato interno: server/player, trasporto, sorgente web, richiesta GUI e Quit |
| Flatpak installazione utente reale finale | **NOT TESTED** | Non installato sull'host; smoke di staging non equivale a collaudo bundle installato |
| Flatpak parità permessi | **PASS** | Application, Context e policy D-Bus identici al precedente pacchetto; nessun permesso aggiunto |
| Snap build/payload/runtime | **PASS** | Strict/core24; 1.047 file yt-dlp, 6 EJS, Deno, libvlc, libvlccore e helper HTTPS byte-identici a x15 |
| Snap manifest/permessi | **PASS** | Identico a x15 dopo esclusione delle sole descrizioni aggiornate |
| Snap installazione/avvio aggiornato, baseline dd5c729 | **PASS** | Upgrade locale da rev15 ufficiale nel container, nuova revisione locale x1; server/player avviati e Quit verificato |
| Nuovo collaudo visivo Snap 6ed0869 | **NOT TESTED** | Non ripetuto: nessun delta funzionale Snap rispetto a x15; x15 sull'host intatta |
| Payload comuni | **PASS** | Tutti i 36 file applicativi e due copie da 7 file rev8 per formato identici al sorgente; schema, gresource, icone, desktop e 5 traduzioni presenti |
| File diagnostici/privati nei payload | **PASS** | Nessun fixture temporaneo, log, pacchetto annidato o API diagnostica del prototipo aggiunto |
| SHA-256 | **PASS** | Tutte le sei somme ricalcolate con `sha256sum -c` |

Gli esiti di installazione/avvio e dello smoke nella tabella si riferiscono
ai payload funzionali dd5c729 già accettati. **NOT TESTED** — nuova installazione
dei contenitori 6ed0869 dopo il solo cambio metainfo: non ripetuta, perché il
confronto completo certifica l'assenza di delta eseguibili o di permessi.
**PASS** — validazione offline dello skeleton Snap finale; estrazione reale di
DEB/RPM/Snap e import/checkout del nuovo bundle Flatpak per verificare i contenuti.

Percorsi completi degli artefatti: directory sopra + nome esatto sotto.

```text
6bea02626da43fa150cc8c999bb38b13e3ce5ab2040bed468c2e98c29e9f4f16  apricus_2.1.0_amd64.deb
9baaf1c7611711500c49d5f22f1046bba9cae719bfb903ba43bd13fda5962ae0  apricus-2.1.0-1.x86_64.rpm
d9dbc37e5ecf7dc97dc9ce50630fba72576e2bec0bc6bc8cf720e584f7ee10cb  apricus_2.1.0_amd64.snap
bd1c04fd4832c95daca507b063bfe987884509ddc7f34a00b6004be39fe263e9  apricus-2.1.0.flatpak
b7380baba3287f1f602098ef177cb14c88948757f0a77d1a738ff26727a8a3e2  apricus-locale.flatpak
f55c5ed0c39f14873630f11b4d73c9d3a350851b29d2d83caaeff701a89ef45f  apricus-integration@fabx-dev.zip
```

**PASS** — runtime vendorizzato nativo preservato. RPM rimuove la sola sezione
ELF non allocata `.comment` dal Deno verificato in download, cambiandone hash
globale rispetto al DEB/vecchio RPM: sezioni eseguibili/dati e segmenti runtime
verificati identici, versione 2.9.7 e smoke Fedora corretti. Nessuna falsa
affermazione di identità binaria globale tra questi due Deno.

**PASS** — ZIP rev8 prodotto dai medesimi sette file del sorgente. La revisione
del codice Shell (8) è distinta dalla versione pacchetto dell'estensione (2).

Evidenza definitiva di questo sigillo: `logs/FINAL_VERIFICATION.json` nella
directory `../apricus-stable-2.1.0-20261104/` (relativa alla radice repository).
Inventari confrontati: DEB 1.317 elementi, RPM 1.317, Snap 15.655, Flatpak 4.417;
in ogni formato il solo file differente è il metainfo compilato, identico a
quello prodotto da Meson sul commit comune. Identità/dipendenze/script RPM,
control DEB e permessi Snap/Flatpak verificati invariati. Nessuna API o fixture
diagnostica introdotta nei pacchetti definitivi.

Gli smoke nativi usano root soltanto nei container e l'override WebKit sandbox
necessario a quel fixture; non prescrivono avvio root agli utenti. Lo smoke
Flatpak monta soltanto il fixture temporaneo, senza socket del bus host né
allargamento del manifest; il primo tentativo senza bus privato interno si è
fermato per assenza di dbus-launch, poi è stato corretto il runner, non l'app.
I PASS sono di controllo/liveness, non prove di fotogrammi visibili o audio.
La build Flatpak è stata reindirizzata alla cache preesistente: dipendenze
validate riutilizzate, ricompilato il modulo applicativo. Un primo builder
senza quella cache è stato interrotto e non genera gli artefatti finali.

## 6. Aggiornamento dalla Snap 2.0.7 rev15

**PASS** — baseline ufficiale rev15 installata con assertion nel container
isolato `apricus-210-upgrade` (Ubuntu 24.04, snapd 2.77.1), senza sostituire x15.
Server e player rev15 avviati con un video locale; Quit regolare e verifica
dell'assenza di helper confinati. Snapshot `rev15-populated-quiescent` disponibile.
Lo snapshot contiene il fixture iniziale, corretto poi come descritto sotto;
non deve essere scambiato per un backup di dati personali reali.

Fixture sintetica, non dati personali: playlist con ordine e duplicati
intenzionali, preferiti/storici web e stream, volume/mute, preferenze policy,
sorgenti locali, directory media, impostazione extra da preservare, dati
revision-specific/common, file binario sentinella e symlink relativo.
Non sono stati contattati stream per questo test.

**PASS** — aggiornamento **locale snapd** da 2.0.7 rev15 ufficiale alla Snap
`dd5c729`, hash `8695c7dc…`, revisione locale x1. Confronto prima/dopo avvio e
Quit: playlist nell'ordine originale (duplicati intenzionali conservati, nessuno
aggiunto), preferiti, storici, preferenze, sorgenti, mute/volume, chiave extra,
dati revision-specific/common, file e symlink sentinella preservati; nessuna
duplicazione autostart. Hash dei due video esterni invariati. Schema v5
conservato, backfill previsto di policy/manual Stop/Pause e renderer `auto`;
nessun reset manuale indesiderato. Backup di configurazione presente e
confrontato. Server e player confinati avviati; dopo Quit zero helper vivi.

**PASS** — nessun delta di configurazione, server, player, policy o runtime
nella Snap finale `6ed0869` rispetto a questa prova: cambia soltanto metainfo.
**NOT TESTED** — nuova ripetizione dell'upgrade dopo il sigillo data, non necessaria
per questo delta e non dichiarata come ulteriore prova Store.

**PASS** — controllo negativo e correzione del test: il primo fixture inventava
`playlist_position=1`, chiave assente da rev15; la 2.1.0 la normalizza correttamente
a mappa vuota. Non è perdita di un formato rev15 valido. Conservato il fallimento,
ripristinato lo snapshot e rimossa soltanto quella chiave sintetica; ripetuto
il solo upgrade con fixture valido. Il runner è stato inoltre corretto per
attendere la readiness e riavviare Xvfb dopo restore a freddo. I fallimenti
del runner non sono stati cancellati né presentati come difetti del prodotto.
Evidenza definitiva: `upgrade/before-upgrade.json`, `upgrade/after-upgrade.json`,
`logs/valid-upgrade-data-check.log` e `logs/upgrade-media-verification.json`
nella directory locale di finalizzazione (fuori dai materiali pubblicabili).

**NOT TESTED** — consegna reale dell'aggiornamento tramite Snap Store e relative
assertion/auto-connessioni della nuova revisione. Un refresh locale non la certifica.
Per provarla: dopo autorizzazione separata, caricare la Snap senza canale,
ottenere la review dell'esatto slot D-Bus se richiesta, associare la revisione
a candidate; in VM ripristinabile partire dalla rev15 ufficiale con fixture,
quittare, salvare dati, refresh da candidate, confrontare e avviare server/player.
Non spostare stable per fare il test. Questa procedura non è stata eseguita.

## 7. Documentazione e metadati

**PASS** — aggiornati README, CHANGELOG, note 2.1.0, descrizione Snap e metainfo;
rev8, stesso WebView, Done/shortcut effettiva, focus che chiude l'interazione,
Stop/Resume web, dipendenza dall'estensione host e limite Flatpak sono espliciti.
La vecchia nota del bridge GUI-owned è marcata storica: oggi il bridge è del
server e sopravvive alla chiusura del control center.

**PASS** — AppStream compilato valida offline con due annotazioni informative;
versione 2.1.0, ID `io.github.fabx_dev.Apricus`, `date="2026-11-04"` e
`type="stable"` coerenti in tutti gli artefatti. CHANGELOG datato 2026-11-04
e note di rilascio allineate. Data autorizzata dal maintainer, non dedotta.
La pubblicazione esterna rimane separatamente soggetta ad approvazione.
Il precedente gate della data è chiuso; i pacchetti di sviluppo precedenti
restano soltanto evidenza storica e non devono essere mescolati al set stabile.

**NOT TESTED** — nuove schermate finali: le attuali sono illustrative/storiche.
GNOME 49/51 hanno revisione API statica, non convalida runtime; altri hardware,
Fedora visivo e altri desktop non sono dichiarati verificati. yt-dlp resta
Snap 2026.10.26, nativi/Flatpak 2026.07.04; EJS 0.8.0, Deno 2.9.7.
I download YouTube intermittenti restano una limitazione nota, senza nuova
campagna di trasporto, cambi UA/client o ripristino AVIO. Nessun M4.

## 8. Occorrenze personali nella cronologia

**PASS** — riesame dei due commit già pubblici: `9546a35` introduce un percorso
di lavoro in un fixture di test, `362a5fb` lo elimina (la stringa riappare sul
lato rimosso del diff). Non sono due nuove credenziali esposte. Entrambi sono
antenati del master pubblico già noto. Il contenuto rivela un nome/account
locale e la struttura di una directory: informazione personale a rischio
limitato, già pubblica; nessun token, password o media firmato sulla riga.

**PASS** — nessun percorso personale corrispondente nel tree corrente, nessuna
inclusione del fixture storico nei payload installabili. Nessuna riscrittura
della cronologia e nessuna riproduzione del percorso nel rapporto. Nessun
ostacolo concreto alla pubblicazione individuato in queste due occorrenze.

## 9. Problemi bloccanti e non bloccanti

**PASS** — gate di confezionamento chiuso: data approvata, metadati stabili
e quattro formati sigillati sul medesimo sorgente. Nessun problema realmente
bloccante individuato nelle verifiche locali. Non sostituire questi artefatti
dopo l'approvazione dei checksum e non confonderli con i precedenti set di sviluppo.

**PASS** — nessun nuovo difetto dimostrato di sicurezza, integrità dati o
funzionamento principale nelle verifiche completate. Non si converte ogni
NOT TESTED in un blocco funzionale.

NON-BLOCKER documentati: Interact assente su Flatpak; alcuni stream YouTube
inaffidabili; distribuzioni/hardware aggiuntivi, X11 completo, screen reader,
timing rigoroso e rollback desktop in perdita integrazione non certificati;
schermate illustrative; versioni yt-dlp intenzionalmente differenti.
La review Store dello slot esatto e la prova di consegna candidate sono gate
operativi della fase B, non PASS già ottenuti né difetti presunti.

## 10. Pubblicazione GitHub — procedura non eseguita

**NOT TESTED** — tutte le operazioni di pubblicazione seguenti restano da
autorizzare. Nessun comando di questa sezione è stato eseguito.

1. Verificare data già confermata (2026-11-04), commit stabile e set di artefatti.
   Confermare worktree pulita, `git fsck` e verifiche mirate documentate;
   non ripetere la campagna funzionale senza un nuovo delta applicativo.
   Fissare `SOURCE_COMMIT` al commit approvato completo, non al titolo
   di un ramo. Conservare provenance separata da eventuali commit di rapporto.
2. Dopo autorizzazione, `git fetch origin --prune`; verificare remote URL,
   `git log --left-right --oneline origin/master...release-2.1.0-prep` e la
   punta reale del PR. Non forzare push, non ricreare i vecchi rami sanitizzati
   sopra modifiche altrui. Integrare il codice approvato tramite PR revisionato;
   confermare che l'albero risultante coincide con quello dei pacchetti
   (`git diff --exit-code "$SOURCE_COMMIT" "$RELEASE_COMMIT" -- src data
   gnome-extension snap pkgs meson.build po`). Se cambia, ricertificare il delta.
3. Verificare assenza del tag remoto `git ls-remote --tags origin refs/tags/v2.1.0`.
   Creare solo dopo approvazione `git tag -a v2.1.0 "$RELEASE_COMMIT" -m
   "Apricus 2.1.0"`, verificare `git rev-parse v2.1.0^{commit}`, quindi
   `git push origin refs/tags/v2.1.0`. Non spostare un tag già esistente.
4. Il push del tag attiva build CI, non pubblica da solo Release/Store.
   Controllare CI e risultati; non assumere binari CI identici bit-per-bit ai
   binari locali verificati. Pubblicare esattamente il set approvato con le
   sue somme, senza mischiare rebuild diversi.
5. Dalla directory contenente soltanto i pacchetti approvati e checksum,
   `sha256sum -c SHA256SUMS.txt`; creare draft esplicito:
   `gh release create v2.1.0 --repo fabx-dev/apricus --verify-tag --draft
   --title "Apricus 2.1.0" --notes-file "$APPROVED_NOTES"`.
   Allegare DEB, RPM, Snap, ZIP rev8 e somme usando una lista esplicita di file,
   non l'intera directory diagnostica. Flatpak app/locale **soltanto dopo
   decisione specifica separata**. Preparare un manifest di pubblicazione per
   il sottoinsieme approvato, mantenendo intatto quello locale completo; le
   somme dei file scelti devono coincidere con il manifest sigillato qui.
6. Scaricare gli allegati draft in directory vuota con `gh release download
   v2.1.0 --repo fabx-dev/apricus --dir "$VERIFY_DIR"`; eseguire di nuovo
   `sha256sum -c SHA256SUMS.txt` e verificare tag/sorgenti/note. Solo dopo
   autorizzazione finale: `gh release edit v2.1.0 --repo fabx-dev/apricus
   --draft=false`. Verificare URL pubblico e asset nuovamente scaricati.

**PASS** — review statica workflow: permessi globali read; allegati con write
solo nel job condizionato a dispatch esplicito + tag; upload Snap solo con
input booleano esplicito, senza `--release`. Nessuna promozione automatica
stable. La procedura manuale sopra evita di attivare `github_release=true`
che creerebbe subito una release non-draft. I workflow remoti non sono stati avviati.

## 11. Pubblicazione Snap Store — procedura non eseguita

**NOT TESTED** — nessun upload o cambio canale effettuato.

1. Approvare hash della Snap stabile finale, `snapcraft whoami`, account e nome
   `apricus`. Registrare `snapcraft status apricus` e `snapcraft revisions apricus`:
   stable attesa 2.0.7 rev15. Se diversa, fermarsi e riconciliare il baseline.
2. Solo con autorizzazione upload: `snapcraft upload "$APPROVED_SNAP"` **senza
   `--release`**. Registrare ID/revisione restituiti e confronto hash con la Snap
   approvata. Risolvere review per lo slot esatto
   `io.github.fabxdev.Apricus.GnomeBridge`; non ampliare permessi per superarla.
3. Dopo approvazione canale di prova: `snapcraft release apricus "$REV" candidate`.
   Verificare con `snapcraft status apricus`. Nella VM isolata fare il refresh
   dalla rev15 ufficiale a candidate, confrontare dati e connessioni, avviare
   server/player, fare soltanto smoke mirati. Verificare host rev8 e relativo
   logout/login se i file dell'estensione sono nuovi. Non sostituire x15 senza
   ulteriore autorizzazione. Non chiamare questo test PASS prima di eseguirlo.
4. Presentare revisione, hash, esito candidate e limiti rollback all'utente.
   **Soltanto dopo approvazione stable**: `snapcraft release apricus "$REV" stable`.
   Mai usare un numero di revisione dedotto o un nuovo rebuild al suo posto.
5. Verificare `snapcraft status apricus`, installazione/refresh isolati dal
   canale stable, revisione installata, connessioni, configurazioni e smoke.
   Annotare eventuali segnalazioni/rollback; non promettere affidabilità di
   ogni stream YouTube. Non pubblicare nulla su Flathub in questa procedura.

## 12. Piano di rollback

**NOT TESTED** — rollback reale Store; non è stato eseguito né simulato un
cambio stable.

1. Prima di ogni upgrade: Quit normale dell'istanza esatta; verificare PID,
   start token/stato e zero helper confinati vivi. Creare `sudo snap save apricus`
   e registrare set ID; copiare separatamente `~/snap/apricus` inclusi dati
   revision-specific/common e symlink, non solo il link `current`. Salvare
   media esterni, eventuali dati real-home/autostart, profilo web e configurazione
   estensione host rev8; non includerli nei pacchetti o nei log pubblici.
2. Su macchina interessata, se rev15 è ancora conservata:
   `sudo snap revert apricus --revision=15`. Verificare revisione, dati e
   avvio prima di dichiarare recupero. Se non conservata, reinstallazione
   della rev15 richiede disponibilità dal publisher/Store e procedura separata:
   non presumere che `revert` possa scaricarla.
3. `revert` non garantisce rollback completo di `SNAP_USER_COMMON`, dati
   esterni/media, dconf o estensione host. Non avviare la vecchia app contro
   dati già migrati senza averne verificato compatibilità. Se necessario,
   ripristinare backup completo compatibile / `sudo snap restore "$SET_ID" apricus`
   dopo Quit, poi controllare ownership, symlink e dati. Il ripristino può
   perdere preferiti/playlist aggiunti dopo il backup; esportarli separatamente
   prima, senza fusione automatica non verificata. Il formato base rimane v5,
   con nuove chiavi di policy: non è una promessa di downgrade universale.
4. Rollback publisher, con approvazione esplicita e rev15 ancora disponibile:
   `snapcraft release apricus 15 stable`; verificare stato canale. La
   ripromozione non forza tutti i client già aggiornati a tornare indietro e
   non ripristina dati utente: serve comunicazione e procedura client dedicata.
5. GitHub: segnalare problema nelle note, marcare/ritirare la release secondo
   approvazione, non riscrivere tag pubblici o storia automaticamente. Un hotfix
   usa nuova versione/tag. Non sostituire silenziosamente asset con hash diversi.
6. Per i test locali conservare snapshot rev15 e log fuori dal repository;
   non ripristinare il container sopra altri test ancora in corso.

**PASS** — cleanup finale: entrambi i container di questa fase arrestati,
nessun processo rimasto dei probe privati. Snapshot e tracce conservati senza
rimozioni ampie. x15 e i sette file dell'estensione host rev8 verificati intatti.

## Conclusione

**READY FOR PUBLICATION APPROVAL**

Data 2026-11-04 approvata, quattro formati stabili coerenti e checksum sigillati;
nessun nuovo difetto realmente bloccante. Le limitazioni e i NOT TESTED sopra
mantengono il proprio ambito. Flatpak è preparato, ma la sua pubblicazione
richiede una decisione separata. Questo verdetto non autorizza GitHub, Store o
Flathub: nessuna operazione remota, tag o pubblicazione è stata effettuata in
questa finalizzazione. x15 e Snap Store stable 2.0.7 rev15 non sono stati modificati.
