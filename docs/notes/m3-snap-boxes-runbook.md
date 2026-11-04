# Runbook: collaudo Snap 2.1.0 su GNOME Boxes — VM `ubuntu-26-test 2`

Stato: PROCEDURA PREPARATA, NON ESEGUITA. Ogni sezione indica se richiede
autorizzazione. L'agente opera dentro la VM di sviluppo e NON vede né tocca
l'hypervisor esterno: tutti i passi Boxes/VM-test sono manuali (utente).

## 0. Ruoli

- Agente (VM sviluppo): solo lettura + preparazione artefatti/documenti.
- Utente: interfaccia Boxes, terminale nella VM di test, chiavetta USB,
  logout/login **solo** nella VM di test. Mai sulla VM di sviluppo.

## 1. Selezionare la VM corretta

1. Aprire GNOME Boxes sull'host.
2. Verificare il nome esatto `ubuntu-26-test 2` (non la VM di sviluppo).
3. Annotare: stato (accesa/spenta), RAM/CPU assegnate, dimensione disco.

## 2. Proteggere lo stato iniziale (AUTORIZZAZIONE RICHIESTA, non eseguire ora)

In ordine di preferenza, nella VM di test:

1. Snapshot Boxes: Proprietà VM → cercare "Snapshot/Istantanee" → crearne
   uno nominato `pre-apricus-210`. Se la voce non esiste, passare oltre.
2. Altrimenti clone: se Boxes lo offre per quella VM, clonare e usare il
   clone per i test (l'originale resta intatto).
3. Altrimenti backup a VM spenta: spegnere la VM, copiare il file disco
   (solitamente `~/.local/share/gnome-boxes/images/<nome>`) su disco
   esterno, annotare SHA256 del file copiato.

Senza uno di questi tre punti verificati, non iniziare i test che
modificano la VM.

## 3. Preflight sola lettura (nella VM di test, nessun permesso speciale)

```bash
lsb_release -a; gnome-shell --version; echo $XDG_SESSION_TYPE
snap --version; snap list 2>/dev/null | grep -i apricus || echo "nessuna apricus"
gnome-extensions list | grep -i apricus || echo "nessuna estensione apricus"
free -m | head -2; nproc; df -h / | tail -1
cat /sys/kernel/security/apparmor/profiles 2>/dev/null | grep -c snap || echo "apparmor-check-skipped"
```

Idoneità: Ubuntu 26.04, GNOME Shell 50.x, `wayland`, snapd presente,
nessuna Apricus installata (o 2.0.7 da Store per il test upgrade),
almeno 3 GB RAM libere e 15 GB di disco.

## 4. Build Snap 2.1.0 (nella VM di test, DOPO autorizzazione)

```bash
sudo snap install snapcraft --classic
git clone --recurse-submodules https://github.com/fabx-dev/apricus.git apricus-210
cd apricus-210
# ATTENZIONE: usare il branch/commit indicato al momento del test;
# la baseline di preparazione è la worktree release-2.1.0-prep.
snapcraft pack --destructive-mode
ls -la dist/*.snap
sha256sum dist/*.snap | tee ../snap-210.sha256
```

Verifiche: file `apricus_2.1.0_amd64.snap` presente; versione interna 2.1.0
(`unsquashfs -d /tmp/chk dist/*.snap usr/share/metainfo/...xml` e cercare
`release version="2.1.0"`); confinamento `strict` in `snap/snapcraft.yaml`.

## 5. Estensione rev6

ZIP costruito dai sorgenti 2.1.0 con
`gnome-extension/build-zip.sh` (nella VM di test o sviluppo, non richiede
privilegi). Deve contenere 6 file, incluso `windowPush.js`.
Verificare entrambi i lati con:

```bash
sha256sum apricus-integration@fabx-dev.zip
unzip -l apricus-integration@fabx-dev.zip
```

Riferimento preparazione (HEAD 051fb75):
`windowPush.js` presente; `CODE_REVISION = 6`; `metadata.json` v2.
Ricalcolare gli hash al momento del test: fanno fede quelli del commit
usato, non questi.

## 6. Trasferimento (chiavetta USB, metodo primario)

Boxes non offre cartelle condivise affidabili e le due VM non si vedono
in rete (NAT separati): usare chiavetta USB con redirect in Boxes
(Dispositivi → USB → selezionare la chiavetta) prima nella VM di
sviluppo (copia), poi nella VM di test (lettura). Verificare gli SHA256
su entrambi i lati prima di procedere. File minimi: `.snap`, `.zip`,
`m3-snap-e2e-protocol.md` (testo), checksum.

## 7. Installazione (AUTORIZZAZIONE RICHIESTA, solo VM di test)

```bash
sudo snap install --dangerous ./apricus_2.1.0_amd64.snap
snap list apricus
./install-host.sh --zip apricus-integration@fabx-dev.zip --enable
# logout/login SOLO nella VM di test (da interfaccia GNOME, non da terminale remoto)
gnome-extensions info apricus-integration@fabx-dev | grep -E 'Stato|State'
journalctl --user -g apricus-integration | tail -5   # atteso: code revision 6
```

## 8. Campagna unica (dettagli e comandi D-Bus nel protocollo
`docs/notes/m3-snap-e2e-protocol.md`, Test A–G)

Sequenza: avvio da menu → video locale + YouTube → Play/Pausa/Stop →
fullscreen/maximized → **chiusura GUI e ripetizione fullscreen/maximized**
→ riapertura GUI e confronto stato → manuali → restart server
(`snap restart apricus` o quit/relaunch da GUI) → processi/orphan check
(`ps`, buss `ListNames`, journal) → upgrade 2.0.7→2.1.0 se in piano.
Registrare ogni esito PASS/FAIL/NOT TESTED con log salvati su file.

## 9. Raccolta risultati (senza copia-incolla)

Tutti i log/output su file nella VM di test, poi copiati sulla chiavetta
USB con checksum; dall'altra parte, verifica checksum e consegna. Nessun
servizio di rete aggiuntivo, nessuna pubblicazione.

## 10. Ripristino

Ripristinare lo snapshot/clone/backup del punto 2; in difetto:
`gnome-extensions disable apricus-integration@fabx-dev`, logout/login,
`sudo snap remove apricus`, cancellare
`~/.local/share/gnome-shell/extensions/apricus-integration@fabx-dev`,
verificare `snap list`, estensioni e config come da baseline annotata.
Conservare i log su chiavetta, eliminare gli artefatti di test dalla VM.

## 11. Cosa resta bloccato senza queste autorizzazioni

Snapshot/clone/backup, build Snap con privilegi, installazioni,
logout/login, esecuzione del protocollo. La verifica resta NOT TESTED
finché la campagna non gira davvero nella VM di test.
