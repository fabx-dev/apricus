#!/usr/bin/env python3
"""Generate it/de/fr/es catalogs from apricus.pot. Run from repo root."""
import re
from pathlib import Path

META = {
    "it": ("Italian", "Fabrizio"),
    "de": ("German", "Fabrizio"),
    "fr": ("French", "Fabrizio"),
    "es": ("Spanish", "Fabrizio"),
}

T = {}
T["it"] = {
    "Set For {monitor}": "Imposta per {monitor}",
    "Set For All": "Imposta per tutti",
    "Couldn't connect to server": "Impossibile connettersi al server",
    "Welcome to Apricus 🤗": "Benvenuto in Apricus 🤗",
    "Quickstart for adding local videos:\n ・Click the folder icon to open your video folder\n ・Put your videos there\n ・Click the refresh button\n ・Click to select, Ctrl+click for more than one": "Guida rapida per aggiungere video locali:\n ・Fai clic sull'icona della cartella per aprire la tua cartella video\n ・Metti lì i tuoi video\n ・Fai clic sul pulsante di aggiornamento\n ・Fai clic per selezionare, Ctrl+clic per più di uno",
    "Oops!": "Ops!",
    "Choose video folder (current: {folder})": "Scegli la cartella video (attuale: {folder})",
    "Cancel": "Annulla",
    "Choose": "Scegli",
    "No Video Selected": "Nessun video selezionato",
    "There are no video selected.\nPlease choose one first (Ctrl+click for more than one).": "Non è stato selezionato alcun video.\nScegline prima uno (Ctrl+clic per più di uno).",
    "Please choose a HTML file": "Scegli un file HTML",
    "Failed to access {url}. Error:\n{error}": "Impossibile accedere a {url}. Errore:\n{error}",
    "Failed to access {url}. Error code: {code}": "Impossibile accedere a {url}. Codice di errore: {code}",
    "Failed to stream {url}. Error:\n{error}": "Impossibile riprodurre {url} in streaming. Errore:\n{error}",
    "Videos in {folder} ({count})": "Video in {folder} ({count})",
    "No videos in {folder}": "Nessun video in {folder}",
    "Show Apricus": "Mostra Apricus",
    "Toggle Mute Audio": "Attiva/disattiva l'audio",
    "Toggle Play/Pause": "Riproduci/Pausa",
    "Reload": "Ricarica",
    "I'm Feeling Lucky": "Mi sento fortunato",
    "Quit Apricus": "Esci da Apricus",
    "Live wallpaper for Linux. Apricus (lat.) — sunny, exposed to the sun.": "Sfondi animati per Linux. Apricus (lat.) — soleggiato, esposto al sole.",
    "Quick toggles": "Azioni rapide",
    "Autostart after login": "Avvio automatico all'accesso",
    "Video frame can be applied as system wallpaper, look great in GNOME": "Un fotogramma del video può essere usato come sfondo di sistema, ottimo in GNOME",
    "Apply static wallpaper": "Applica sfondo statico",
    "Automatically pauses playback when maximized window or full screen mode is detected": "Mette in pausa automaticamente quando viene rilevata una finestra massimizzata o a tutto schermo",
    "Pause when maximized window": "Pausa con finestra massimizzata",
    "Automatically mutes playback when maximized window or full screen mode is detected": "Silenzia automaticamente quando viene rilevata una finestra massimizzata o a tutto schermo",
    "Mute when maximized window": "Silenzia con finestra massimizzata",
    "Volume control": "Controllo volume",
    "Open config file": "Apri il file di configurazione",
    "About Apricus": "Informazioni su Apricus",
    '<span size="x-large"><b>Local Video</b></span>\nVideos located in the video folder': '<span size="x-large"><b>Video locali</b></span>\nVideo nella cartella video',
    "Refresh": "Aggiorna",
    "Open video folder": "Apri la cartella video",
    "Choose video folder": "Scegli la cartella video",
    "Choose a video folder to begin": "Scegli una cartella video per iniziare",
    "none chosen yet": "nessuna scelta",
    "Apply": "Applica",
    "Local Video": "Video locali",
    '<span size="x-large"><b>Streaming</b></span>\nVideos from streaming services': '<span size="x-large"><b>Streaming</b></span>\nVideo dai servizi di streaming',
    "<b>Streaming URL</b>": "<b>URL streaming</b>",
    "💡\tThis feature uses <i>yt-dlp</i> as backend": "💡\tQuesta funzione usa <i>yt-dlp</i> come motore",
    '😉\tTry playing any YouTube video! <a href="https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md">Supported sites (yt-dlp)</a>': '😉\tProva a riprodurre un qualsiasi video di YouTube! <a href="https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md">Siti supportati (yt-dlp)</a>',
    "Hint": "Suggerimento",
    "Streaming": "Streaming",
    '<span size="x-large"><b>Web Page</b></span>\nSet a web page as wallpaper': '<span size="x-large"><b>Pagina web</b></span>\nImposta una pagina web come sfondo',
    "<b>Web Page URL</b>": "<b>URL pagina web</b>",
    '💡\tCheck this website for some really amazing examples!\n\t<a href="https://alteredqualia.com/">https://alteredqualia.com/</a>': '💡\tGuarda questo sito per esempi fantastici!\n\t<a href="https://alteredqualia.com/">https://alteredqualia.com/</a>',
    "<b>Local Web Page</b>": "<b>Pagina web locale</b>",
    "Alternately, you can choose a HTML file on your PC": "In alternativa puoi scegliere un file HTML sul tuo PC",
    "Web Page": "Pagina web",
    "#": "#",
    "Video": "Video",
    "Playlist": "Playlist",
    "<span size=\"x-large\"><b>Playlist</b></span>\nMuted loop over your videos": "<span size=\"x-large\"><b>Playlist</b></span>\nRiproduzione continua dei tuoi video (senza audio)",
    "Add selected": "Aggiungi selezionato",
    "Add the video selected in Local Video": "Aggiungi il video selezionato in Video locali",
    "Add all": "Aggiungi tutti",
    "Add every video in the library": "Aggiungi tutti i video della libreria",
    "Up": "Su",
    "Down": "Giù",
    "Remove": "Rimuovi",
    "Clear": "Svuota",
    "Shuffle": "Casuale",
    "Apply playlist": "Applica playlist",
    "Disabled in playlist mode (muted by design)": "Disabilitato in modalità playlist (muta per scelta)",
    "Apply selected": "Applica selezionato",
    "Add to playlist": "Aggiungi alla playlist",
    "Add to playlist ({n})": "Aggiungi alla playlist ({n})",
    "Add all to playlist": "Aggiungi tutti alla playlist",
    "Add the selected videos to the playlist queue": "Aggiungi i video selezionati alla coda playlist",
    "Add every video in the library to the playlist queue": "Aggiungi tutti i video della libreria alla coda playlist",
    "View queue": "Vedi coda",
    "Added {a} to queue (total {n})": "Aggiunti {a} alla coda (totale {n})",
    "Added {a}, {d} already in queue (total {n})": "Aggiunti {a}, {d} già in coda (totale {n})",
    "Already in queue (total {n})": "Già in coda (totale {n})",
    "Click to select. Ctrl+click or Shift+click selects more than one.": "Fai clic per selezionare. Ctrl+clic o Maiusc+clic per selezionarne più di uno.",
    "No selection": "Nessuna selezione",
    "1 video selected": "1 video selezionato",
    "{n} videos selected": "{n} video selezionati",
    "Selecting videos": "Selezionare i video",
    "How to select videos": "Come selezionare i video",
    " ・Click a video to select it\n ・Ctrl+click adds or removes single videos\n ・Shift+click selects a whole range\n ・Right-click a video to set it on a monitor": " ・Fai clic su un video per selezionarlo\n ・Ctrl+clic aggiunge o toglie singoli video\n ・Maiusc+clic seleziona un intervallo intero\n ・Tasto destro su un video per impostarlo su un monitor",
}
T["de"] = {
    "Set For {monitor}": "Für {monitor} festlegen",
    "Set For All": "Für alle festlegen",
    "Couldn't connect to server": "Keine Verbindung zum Server möglich",
    "Welcome to Apricus 🤗": "Willkommen bei Apricus 🤗",
    "Quickstart for adding local videos:\n ・Click the folder icon to open your video folder\n ・Put your videos there\n ・Click the refresh button\n ・Click to select, Ctrl+click for more than one": "Kurzanleitung zum Hinzufügen lokaler Videos:\n ・Klicke auf das Ordnersymbol, um deinen Videoordner zu öffnen\n ・Lege deine Videos dort ab\n ・Klicke auf Aktualisieren\n ・Anklicken zum Auswählen, Strg+Klick für mehrere",
    "Oops!": "Ups!",
    "Choose video folder (current: {folder})": "Videoordner wählen (aktuell: {folder})",
    "Cancel": "Abbrechen",
    "Choose": "Auswählen",
    "No Video Selected": "Kein Video ausgewählt",
    "There are no video selected.\nPlease choose one first (Ctrl+click for more than one).": "Es ist kein Video ausgewählt.\nWähle zuerst eines aus (Strg+Klick für mehrere).",
    "Please choose a HTML file": "Bitte wähle eine HTML-Datei",
    "Failed to access {url}. Error:\n{error}": "Zugriff auf {url} fehlgeschlagen. Fehler:\n{error}",
    "Failed to access {url}. Error code: {code}": "Zugriff auf {url} fehlgeschlagen. Fehlercode: {code}",
    "Failed to stream {url}. Error:\n{error}": "Stream von {url} fehlgeschlagen. Fehler:\n{error}",
    "Videos in {folder} ({count})": "Videos in {folder} ({count})",
    "No videos in {folder}": "Keine Videos in {folder}",
    "Show Apricus": "Apricus anzeigen",
    "Toggle Mute Audio": "Ton umschalten",
    "Toggle Play/Pause": "Wiedergabe/Pause",
    "Reload": "Neu laden",
    "I'm Feeling Lucky": "Ich versuch's mal",
    "Quit Apricus": "Apricus beenden",
    "Live wallpaper for Linux. Apricus (lat.) — sunny, exposed to the sun.": "Live-Hintergrund für Linux. Apricus (lat.) — sonnig, der Sonne ausgesetzt.",
    "Quick toggles": "Schnellzugriff",
    "Autostart after login": "Automatisch nach Anmeldung starten",
    "Video frame can be applied as system wallpaper, look great in GNOME": "Ein Videobild kann als Systemhintergrund verwendet werden, sieht in GNOME toll aus",
    "Apply static wallpaper": "Statischen Hintergrund anwenden",
    "Automatically pauses playback when maximized window or full screen mode is detected": "Pausiert die Wiedergabe automatisch, wenn ein maximiertes Fenster oder Vollbild erkannt wird",
    "Pause when maximized window": "Bei maximiertem Fenster pausieren",
    "Automatically mutes playback when maximized window or full screen mode is detected": "Schaltet die Wiedergabe stumm, wenn ein maximiertes Fenster oder Vollbild erkannt wird",
    "Mute when maximized window": "Bei maximiertem Fenster stummschalten",
    "Volume control": "Lautstärkeregelung",
    "Open config file": "Konfigurationsdatei öffnen",
    "About Apricus": "Über Apricus",
    '<span size="x-large"><b>Local Video</b></span>\nVideos located in the video folder': '<span size="x-large"><b>Lokales Video</b></span>\nVideos im Videoordner',
    "Refresh": "Aktualisieren",
    "Open video folder": "Videoordner öffnen",
    "Choose video folder": "Videoordner wählen",
    "Choose a video folder to begin": "Wähle einen Videoordner, um zu beginnen",
    "none chosen yet": "noch keine gewählt",
    "Apply": "Anwenden",
    "Local Video": "Lokales Video",
    '<span size="x-large"><b>Streaming</b></span>\nVideos from streaming services': '<span size="x-large"><b>Streaming</b></span>\nVideos von Streaming-Diensten',
    "<b>Streaming URL</b>": "<b>Streaming-URL</b>",
    "💡\tThis feature uses <i>yt-dlp</i> as backend": "💡\tDiese Funktion nutzt <i>yt-dlp</i> als Backend",
    '😉\tTry playing any YouTube video! <a href="https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md">Supported sites (yt-dlp)</a>': '😉\tVersuch doch mal ein YouTube-Video! <a href="https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md">Unterstützte Seiten (yt-dlp)</a>',
    "Hint": "Hinweis",
    "Streaming": "Streaming",
    '<span size="x-large"><b>Web Page</b></span>\nSet a web page as wallpaper': '<span size="x-large"><b>Webseite</b></span>\nEine Webseite als Hintergrund festlegen',
    "<b>Web Page URL</b>": "<b>Webseiten-URL</b>",
    '💡\tCheck this website for some really amazing examples!\n\t<a href="https://alteredqualia.com/">https://alteredqualia.com/</a>': '💡\tSchau dir diese Website für tolle Beispiele an!\n\t<a href="https://alteredqualia.com/">https://alteredqualia.com/</a>',
    "<b>Local Web Page</b>": "<b>Lokale Webseite</b>",
    "Alternately, you can choose a HTML file on your PC": "Alternativ kannst du eine HTML-Datei auf deinem PC wählen",
    "Web Page": "Webseite",
    "#": "#",
    "Video": "Video",
    "Playlist": "Playlist",
    "<span size=\"x-large\"><b>Playlist</b></span>\nMuted loop over your videos": "<span size=\"x-large\"><b>Playlist</b></span>\nStumme Endlosschleife deiner Videos",
    "Add selected": "Auswahl hinzufügen",
    "Add the video selected in Local Video": "Das in Lokales Video gewählte Video hinzufügen",
    "Add all": "Alle hinzufügen",
    "Add every video in the library": "Alle Videos der Bibliothek hinzufügen",
    "Up": "Hoch",
    "Down": "Runter",
    "Remove": "Entfernen",
    "Clear": "Leeren",
    "Shuffle": "Zufällig",
    "Apply playlist": "Playlist anwenden",
    "Disabled in playlist mode (muted by design)": "Im Playlist-Modus deaktiviert (absichtlich stumm)",
    "Apply selected": "Auswahl anwenden",
    "Add to playlist": "Zur Playlist hinzufügen",
    "Add to playlist ({n})": "Zur Playlist hinzufügen ({n})",
    "Add all to playlist": "Alle zur Playlist hinzufügen",
    "Add the selected videos to the playlist queue": "Die gewählten Videos zur Playlist-Warteschlange hinzufügen",
    "Add every video in the library to the playlist queue": "Alle Videos der Bibliothek zur Playlist-Warteschlange hinzufügen",
    "View queue": "Warteschlange ansehen",
    "Added {a} to queue (total {n})": "{a} zur Warteschlange hinzugefügt (gesamt {n})",
    "Added {a}, {d} already in queue (total {n})": "{a} hinzugefügt, {d} bereits in der Warteschlange (gesamt {n})",
    "Already in queue (total {n})": "Bereits in der Warteschlange (gesamt {n})",
    "Click to select. Ctrl+click or Shift+click selects more than one.": "Klicken zum Auswählen. Strg+Klick oder Umschalt+Klick für mehrere.",
    "No selection": "Keine Auswahl",
    "1 video selected": "1 Video ausgewählt",
    "{n} videos selected": "{n} Videos ausgewählt",
    "Selecting videos": "Videos auswählen",
    "How to select videos": "So wählst du Videos aus",
    " ・Click a video to select it\n ・Ctrl+click adds or removes single videos\n ・Shift+click selects a whole range\n ・Right-click a video to set it on a monitor": " ・Video anklicken zum Auswählen\n ・Strg+Klick fügt einzelne Videos hinzu oder entfernt sie\n ・Umschalt+Klick wählt einen ganzen Bereich\n ・Rechtsklick auf ein Video, um es einem Monitor zuzuweisen",
}
T["fr"] = {
    "Set For {monitor}": "Définir pour {monitor}",
    "Set For All": "Définir pour tous",
    "Couldn't connect to server": "Impossible de se connecter au serveur",
    "Welcome to Apricus 🤗": "Bienvenue dans Apricus 🤗",
    "Quickstart for adding local videos:\n ・Click the folder icon to open your video folder\n ・Put your videos there\n ・Click the refresh button\n ・Click to select, Ctrl+click for more than one": "Guide rapide pour ajouter des vidéos locales :\n • Cliquez sur l'icône du dossier pour ouvrir votre dossier vidéo\n • Mettez-y vos vidéos\n • Cliquez sur le bouton d'actualisation\n ・Cliquez pour sélectionner, Ctrl+clic pour plusieurs",
    "Oops!": "Oups !",
    "Choose video folder (current: {folder})": "Choisir le dossier vidéo (actuel : {folder})",
    "Cancel": "Annuler",
    "Choose": "Choisir",
    "No Video Selected": "Aucune vidéo sélectionnée",
    "There are no video selected.\nPlease choose one first (Ctrl+click for more than one).": "Aucune vidéo sélectionnée.\nChoisissez-en d'abord une (Ctrl+clic pour plusieurs).",
    "Please choose a HTML file": "Veuillez choisir un fichier HTML",
    "Failed to access {url}. Error:\n{error}": "Impossible d'accéder à {url}. Erreur :\n{error}",
    "Failed to access {url}. Error code: {code}": "Impossible d'accéder à {url}. Code d'erreur : {code}",
    "Failed to stream {url}. Error:\n{error}": "Impossible de lire {url} en continu. Erreur :\n{error}",
    "Videos in {folder} ({count})": "Vidéos dans {folder} ({count})",
    "No videos in {folder}": "Aucune vidéo dans {folder}",
    "Show Apricus": "Afficher Apricus",
    "Toggle Mute Audio": "Activer/couper le son",
    "Toggle Play/Pause": "Lecture/Pause",
    "Reload": "Recharger",
    "I'm Feeling Lucky": "Je me sens chanceux",
    "Quit Apricus": "Quitter Apricus",
    "Live wallpaper for Linux. Apricus (lat.) — sunny, exposed to the sun.": "Fond d'écran animé pour Linux. Apricus (lat.) — ensoleillé, exposé au soleil.",
    "Quick toggles": "Accès rapides",
    "Autostart after login": "Lancement automatique à la connexion",
    "Video frame can be applied as system wallpaper, look great in GNOME": "Une image de la vidéo peut servir de fond d'écran système, superbe sous GNOME",
    "Apply static wallpaper": "Appliquer le fond d'écran statique",
    "Automatically pauses playback when maximized window or full screen mode is detected": "Met en pause automatiquement quand une fenêtre maximisée ou en plein écran est détectée",
    "Pause when maximized window": "Pause si fenêtre maximisée",
    "Automatically mutes playback when maximized window or full screen mode is detected": "Coupe le son automatiquement quand une fenêtre maximisée ou en plein écran est détectée",
    "Mute when maximized window": "Couper le son si fenêtre maximisée",
    "Volume control": "Contrôle du volume",
    "Open config file": "Ouvrir le fichier de configuration",
    "About Apricus": "À propos d'Apricus",
    '<span size="x-large"><b>Local Video</b></span>\nVideos located in the video folder': '<span size="x-large"><b>Vidéo locale</b></span>\nVidéos dans le dossier vidéo',
    "Refresh": "Actualiser",
    "Open video folder": "Ouvrir le dossier vidéo",
    "Choose video folder": "Choisir le dossier vidéo",
    "Choose a video folder to begin": "Choisissez un dossier vidéo pour commencer",
    "none chosen yet": "aucun choisi",
    "Apply": "Appliquer",
    "Local Video": "Vidéo locale",
    '<span size="x-large"><b>Streaming</b></span>\nVideos from streaming services': '<span size="x-large"><b>Streaming</b></span>\nVidéos des services de streaming',
    "<b>Streaming URL</b>": "<b>URL de streaming</b>",
    "💡\tThis feature uses <i>yt-dlp</i> as backend": "💡\tCette fonction utilise <i>yt-dlp</i> comme moteur",
    '😉\tTry playing any YouTube video! <a href="https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md">Supported sites (yt-dlp)</a>': '😉\tEssayez n\'importe quelle vidéo YouTube ! <a href="https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md">Sites pris en charge (yt-dlp)</a>',
    "Hint": "Astuce",
    "Streaming": "Streaming",
    '<span size="x-large"><b>Web Page</b></span>\nSet a web page as wallpaper': '<span size="x-large"><b>Page web</b></span>\nDéfinir une page web comme fond d\'écran',
    "<b>Web Page URL</b>": "<b>URL de la page web</b>",
    '💡\tCheck this website for some really amazing examples!\n\t<a href="https://alteredqualia.com/">https://alteredqualia.com/</a>': '💡\tJetez un œil à ce site pour des exemples incroyables !\n\t<a href="https://alteredqualia.com/">https://alteredqualia.com/</a>',
    "<b>Local Web Page</b>": "<b>Page web locale</b>",
    "Alternately, you can choose a HTML file on your PC": "Vous pouvez aussi choisir un fichier HTML sur votre PC",
    "Web Page": "Page web",
    "#": "#",
    "Video": "Vidéo",
    "Playlist": "Playlist",
    "<span size=\"x-large\"><b>Playlist</b></span>\nMuted loop over your videos": "<span size=\"x-large\"><b>Playlist</b></span>\nLecture en boucle de vos vidéos (sans son)",
    "Add selected": "Ajouter la sélection",
    "Add the video selected in Local Video": "Ajouter la vidéo sélectionnée dans Vidéo locale",
    "Add all": "Tout ajouter",
    "Add every video in the library": "Ajouter toutes les vidéos de la bibliothèque",
    "Up": "Monter",
    "Down": "Descendre",
    "Remove": "Retirer",
    "Clear": "Effacer",
    "Shuffle": "Aléatoire",
    "Apply playlist": "Appliquer la playlist",
    "Disabled in playlist mode (muted by design)": "Désactivé en mode playlist (muet par choix)",
    "Apply selected": "Appliquer la sélection",
    "Add to playlist": "Ajouter à la playlist",
    "Add to playlist ({n})": "Ajouter à la playlist ({n})",
    "Add all to playlist": "Tout ajouter à la playlist",
    "Add the selected videos to the playlist queue": "Ajouter les vidéos sélectionnées à la file de la playlist",
    "Add every video in the library to the playlist queue": "Ajouter toutes les vidéos de la bibliothèque à la file de la playlist",
    "View queue": "Voir la file",
    "Added {a} to queue (total {n})": "{a} ajoutés à la file (total {n})",
    "Added {a}, {d} already in queue (total {n})": "{a} ajoutés, {d} déjà dans la file (total {n})",
    "Already in queue (total {n})": "Déjà dans la file (total {n})",
    "Click to select. Ctrl+click or Shift+click selects more than one.": "Cliquez pour sélectionner. Ctrl+clic ou Maj+clic pour en sélectionner plusieurs.",
    "No selection": "Aucune sélection",
    "1 video selected": "1 vidéo sélectionnée",
    "{n} videos selected": "{n} vidéos sélectionnées",
    "Selecting videos": "Sélectionner des vidéos",
    "How to select videos": "Comment sélectionner des vidéos",
    " ・Click a video to select it\n ・Ctrl+click adds or removes single videos\n ・Shift+click selects a whole range\n ・Right-click a video to set it on a monitor": " ・Cliquez sur une vidéo pour la sélectionner\n ・Ctrl+clic ajoute ou retire des vidéos une par une\n ・Maj+clic sélectionne toute une plage\n ・Clic droit sur une vidéo pour l'assigner à un moniteur",
}
T["es"] = {
    "Set For {monitor}": "Establecer para {monitor}",
    "Set For All": "Establecer para todos",
    "Couldn't connect to server": "No se pudo conectar con el servidor",
    "Welcome to Apricus 🤗": "Bienvenido a Apricus 🤗",
    "Quickstart for adding local videos:\n ・Click the folder icon to open your video folder\n ・Put your videos there\n ・Click the refresh button\n ・Click to select, Ctrl+click for more than one": "Guía rápida para añadir vídeos locales:\n • Haz clic en el icono de la carpeta para abrir tu carpeta de vídeos\n • Pon allí tus vídeos\n • Haz clic en el botón de actualizar\n ・Haz clic para seleccionar, Ctrl+clic para varios",
    "Oops!": "¡Ups!",
    "Choose video folder (current: {folder})": "Elegir la carpeta de vídeos (actual: {folder})",
    "Cancel": "Cancelar",
    "Choose": "Elegir",
    "No Video Selected": "Ningún vídeo seleccionado",
    "There are no video selected.\nPlease choose one first (Ctrl+click for more than one).": "No hay ningún vídeo seleccionado.\nElige uno primero (Ctrl+clic para varios).",
    "Please choose a HTML file": "Elige un archivo HTML",
    "Failed to access {url}. Error:\n{error}": "No se pudo acceder a {url}. Error:\n{error}",
    "Failed to access {url}. Error code: {code}": "No se pudo acceder a {url}. Código de error: {code}",
    "Failed to stream {url}. Error:\n{error}": "No se pudo reproducir {url} en continuo. Error:\n{error}",
    "Videos in {folder} ({count})": "Vídeos en {folder} ({count})",
    "No videos in {folder}": "Ningún vídeo en {folder}",
    "Show Apricus": "Mostrar Apricus",
    "Toggle Mute Audio": "Activar/silenciar audio",
    "Toggle Play/Pause": "Reproducir/Pausar",
    "Reload": "Recargar",
    "I'm Feeling Lucky": "Voy a tener suerte",
    "Quit Apricus": "Salir de Apricus",
    "Live wallpaper for Linux. Apricus (lat.) — sunny, exposed to the sun.": "Fondo animado para Linux. Apricus (lat.) — soleado, expuesto al sol.",
    "Quick toggles": "Accesos rápidos",
    "Autostart after login": "Inicio automático al iniciar sesión",
    "Video frame can be applied as system wallpaper, look great in GNOME": "Un fotograma del vídeo puede usarse como fondo del sistema, queda genial en GNOME",
    "Apply static wallpaper": "Aplicar fondo estático",
    "Automatically pauses playback when maximized window or full screen mode is detected": "Pausa automáticamente cuando se detecta una ventana maximizada o a pantalla completa",
    "Pause when maximized window": "Pausar si hay ventana maximizada",
    "Automatically mutes playback when maximized window or full screen mode is detected": "Silencia automáticamente cuando se detecta una ventana maximizada o a pantalla completa",
    "Mute when maximized window": "Silenciar si hay ventana maximizada",
    "Volume control": "Control de volumen",
    "Open config file": "Abrir el archivo de configuración",
    "About Apricus": "Acerca de Apricus",
    '<span size="x-large"><b>Local Video</b></span>\nVideos located in the video folder': '<span size="x-large"><b>Vídeo local</b></span>\nVídeos en la carpeta de vídeos',
    "Refresh": "Actualizar",
    "Open video folder": "Abrir la carpeta de vídeos",
    "Choose video folder": "Elegir la carpeta de vídeos",
    "Choose a video folder to begin": "Elige una carpeta de vídeos para empezar",
    "none chosen yet": "ninguna elegida",
    "Apply": "Aplicar",
    "Local Video": "Vídeo local",
    '<span size="x-large"><b>Streaming</b></span>\nVideos from streaming services': '<span size="x-large"><b>Streaming</b></span>\nVídeos de servicios de streaming',
    "<b>Streaming URL</b>": "<b>URL de streaming</b>",
    "💡\tThis feature uses <i>yt-dlp</i> as backend": "💡\tEsta función usa <i>yt-dlp</i> como motor",
    '😉\tTry playing any YouTube video! <a href="https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md">Supported sites (yt-dlp)</a>': '😉\t¡Prueba con cualquier vídeo de YouTube! <a href="https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md">Sitios compatibles (yt-dlp)</a>',
    "Hint": "Consejo",
    "Streaming": "Streaming",
    '<span size="x-large"><b>Web Page</b></span>\nSet a web page as wallpaper': '<span size="x-large"><b>Página web</b></span>\nEstablecer una página web como fondo',
    "<b>Web Page URL</b>": "<b>URL de página web</b>",
    '💡\tCheck this website for some really amazing examples!\n\t<a href="https://alteredqualia.com/">https://alteredqualia.com/</a>': '💡\t¡Mira este sitio para ver ejemplos increíbles!\n\t<a href="https://alteredqualia.com/">https://alteredqualia.com/</a>',
    "<b>Local Web Page</b>": "<b>Página web local</b>",
    "Alternately, you can choose a HTML file on your PC": "También puedes elegir un archivo HTML de tu PC",
    "Web Page": "Página web",
    "#": "#",
    "Video": "Vídeo",
    "Playlist": "Playlist",
    "<span size=\"x-large\"><b>Playlist</b></span>\nMuted loop over your videos": "<span size=\"x-large\"><b>Playlist</b></span>\nReproducción en bucle de tus vídeos (sin sonido)",
    "Add selected": "Añadir selección",
    "Add the video selected in Local Video": "Añadir el vídeo seleccionado en Vídeo local",
    "Add all": "Añadir todos",
    "Add every video in the library": "Añadir todos los vídeos de la biblioteca",
    "Up": "Subir",
    "Down": "Bajar",
    "Remove": "Quitar",
    "Clear": "Vaciar",
    "Shuffle": "Aleatorio",
    "Apply playlist": "Aplicar playlist",
    "Disabled in playlist mode (muted by design)": "Desactivado en modo playlist (silencioso por diseño)",
    "Apply selected": "Aplicar selección",
    "Add to playlist": "Añadir a la playlist",
    "Add to playlist ({n})": "Añadir a la playlist ({n})",
    "Add all to playlist": "Añadir todos a la playlist",
    "Add the selected videos to the playlist queue": "Añadir los vídeos seleccionados a la cola de la playlist",
    "Add every video in the library to the playlist queue": "Añadir todos los vídeos de la biblioteca a la cola de la playlist",
    "View queue": "Ver la cola",
    "Added {a} to queue (total {n})": "{a} añadidos a la cola (total {n})",
    "Added {a}, {d} already in queue (total {n})": "{a} añadidos, {d} ya en la cola (total {n})",
    "Already in queue (total {n})": "Ya en la cola (total {n})",
    "Click to select. Ctrl+click or Shift+click selects more than one.": "Haz clic para seleccionar. Ctrl+clic o Mayús+clic para varios.",
    "No selection": "Ninguna selección",
    "1 video selected": "1 vídeo seleccionado",
    "{n} videos selected": "{n} vídeos seleccionados",
    "Selecting videos": "Seleccionar vídeos",
    "How to select videos": "Cómo seleccionar vídeos",
    " ・Click a video to select it\n ・Ctrl+click adds or removes single videos\n ・Shift+click selects a whole range\n ・Right-click a video to set it on a monitor": " ・Haz clic en un vídeo para seleccionarlo\n ・Ctrl+clic añade o quita vídeos sueltos\n ・Mayús+clic selecciona un rango entero\n ・Clic derecho en un vídeo para asignarlo a un monitor",
}


def parse_pot(path):
    text = Path(path).read_text(encoding="utf-8")
    chunks = re.split(r"\n\n+", text)
    header, entries = chunks[0], []
    for ch in chunks[1:]:
        refs = re.findall(r"^#:.*$", ch, re.M)
        lines = ch.split("\n")
        # collect the msgid block (msgid line + continuation "..." lines)
        started = False
        parts = []
        for ln in lines:
            m = re.match(r'^msgid "(.*)"$', ln)
            if m:
                started = True
                parts.append(m.group(1))
                continue
            if started:
                m2 = re.match(r'^"(.*)"$', ln)
                if m2:
                    parts.append(m2.group(1))
                    continue
                break
        if not parts:
            continue
        raw = "".join(parts)
        msgid = raw.encode().decode("unicode_escape").encode("latin-1").decode("utf-8")
        if msgid == "":
            continue
        entries.append((refs, msgid))
    return header, entries


def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def emit(msgid, msgstr, refs):
    lines = refs + [f'msgid "{esc(msgid)}"']
    if "\n" not in msgstr:
        lines.append(f'msgstr "{esc(msgstr)}"')
    else:
        parts = msgstr.split("\n")
        lines.append('msgstr ""')
        for i, part in enumerate(parts):
            suffix = "\\n" if i < len(parts) - 1 else ""
            lines.append(f'"{esc(part)}{suffix}"')
    return "\n".join(lines)


def main():
    header, entries = parse_pot("po/apricus.pot")
    header = re.sub(
        r"Project-Id-Version: .*?\\n",
        lambda _: "Project-Id-Version: apricus" + chr(92) + "n",
        header,
    )
    for lang, (name, author) in META.items():
        out = [header.replace("FULL NAME <EMAIL@ADDRESS>", f"{author} <fabx-dev>")]
        bs_n = chr(92) + "n"
        out[0] = re.sub(
            r"Language-Team: .*?\\n", lambda _: f"Language-Team: {name}" + bs_n, out[0]
        )
        out[0] = re.sub(
            r"Language: .*?\\n", lambda _: f"Language: {lang}" + bs_n, out[0]
        )
        missing = []
        for refs, msgid in entries:
            msgstr = T[lang].get(msgid)
            if msgstr is None:
                missing.append(msgid[:60])
                msgstr = ""
            out.append(emit(msgid, msgstr, refs))
        Path(f"po/{lang}.po").write_text("\n\n".join(out) + "\n", encoding="utf-8")
        print(f"{lang}: {len(entries) - len(missing)}/{len(entries)} translated")
        for m in missing:
            print("  MISSING:", m)


if __name__ == "__main__":
    main()
