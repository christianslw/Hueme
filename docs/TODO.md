# Hueme Studio — TODO / task board

> **Read this after `docs/PROGRESS.md`.** Update this file whenever a task starts,
> lands or is deliberately deferred. One checkbox = one shippable change.

Last updated: 2026-11-22 (P2 slice 2: view cube, structure band, colour keys, auto-show)

## Current state

- Core engine, UI shell, presets/filters, expressions, filter keyframes,
  Effect Builder, Timeline Analysis and P1 usability polish are in place
  (**177 passed**, 6 subtests; `node --test tests/test_snap.mjs tests/test_autoshow.mjs`
  adds 9 JS cases).
- Honest assessment: many pieces exist as isolated features, while overall
  usability, creative flow, Show-building speed, spatial/emitter depth, and
  UI clarity still need substantial improvement.
- Reference analysis: `docs/effectcraft-analysis.md` (read-only).
- Server: `python3 -m hueme_studio --host 0.0.0.0 --port 8090`.

## 0. Stabilität, Performance & bekannte Bugs beheben

- [x] Memory-Wachstum im Renderer/Server untersuchen und begrenzen
      (Preview-Cache jetzt LRU mit 64 Einträgen; Regressionstest). Der
      16-GB-Spike wurde lokal nicht reproduziert, der einzige unbegrenzte
      Server-Cache ist damit gedeckelt.
- [x] Doppelklick-Mehrfachauslösung bei Presets/Filtern in `studio.js` beheben.
- [x] Timeline-Expression-Vorschau mit echten Audio-/Envelope-/Blueprint-Werten
      speisen statt fester Dummy-Werte.
- [x] Sync-Offset-State sauber synchronisieren (keine verwaisten
      `localStorage`-Stände gegenüber dem Server-Zustand).
- [x] 1800K-Amber-Extraktion in `hal.py` praxistauglich kalibrieren.
- [x] Kleine Code-Bugs in `presets.py` (`shockwave_3d`, `strobe_blinder`),
      `server.py` (sauberer Restart) und `mcp.py` beheben.
- [x] `config/sendspin_pairings.json` und `config/sendspin_identity.json` aus
      dem Repo nehmen (Runtime-Secrets; bereits in `.gitignore`). PSK nach dem
      Historie-Leak rotieren/neu paaren.

## 1. UI/UX-Klarheit, Toolbar, Inspector & Viewport-Orientierung

- [ ] Topbar entrümpeln und responsiv gestalten: klare Menüs (`Datei`, `Bearbeiten`,
      `Ebenen`, `Filter & Effekte`, `Ansicht`, `Hilfe`), keine abgeschnittenen Badges,
      konsistente Sprache und Symbolik (MDI-Icons) statt Textwüste. Die Menüs und
      Icons existieren bereits; offen sind responsive Dichte und Badge-Verhalten.
- [x] Inspector übersichtlicher strukturieren: lange Hilfetexte in Tooltips
      (Sync-Offset), Keyframe-Navigator `◀ ◆ ▶` an animierten Parametern und
      Inline-`ƒ`-Chips mit Live-Wert; Farbparameter bekommen ◇ ohne ƒ.
- [x] Perspektiv-/Achsen-Selector im 3D-Viewport als interaktiven View-Cube
      ausbauen (klickbare Faces je Achse, `Persp`/`Ortho`-Pille, Status unten
      rechts).
- [x] Viewport-Hinweise entschlacken (kurzer Modus-Hinweis unten, `?`-Button
      öffnet die Tastenkürzel).
- [ ] Visuelle Rückmeldung & Hierarchie im gesamten Studio schärfen (aktive Zustände,
      klare Gruppierung, konsistente Dichte).

## 2. Timeline-Workflow, Ebenen-Hierarchie & schnelles Show-Bauen

- [x] Struktur- & Marker-Band über der Timeline übersichtlich darstellen (Sections,
      Drops, Cues als farbige Strips und kollisionsfreie Labels im Ruler).
- [x] Timeline-Ergonomie verbessern: magnetisches Snapping auf Grid, Marker,
      Clip-Kanten und Playhead (8 px), mit `Alt` temporär lösbar; Verschieben/
      Trimmen und Spurhöhen (`Alt+Wheel`) sind vorhanden.
- [ ] Ebenen-Hierarchie & Maskierung intuitiv bedienbar machen (Global-Layer,
      Lampen-Layer, Unterebenen, Clipping-Masken, Mute/Solo, Blend-Modes).
- [ ] Farbliche & Gradient-Keyframes sauber unterstützen. Farbkeyframes sind
      im Inspector keybar und erscheinen als Verlaufsstreifen plus Swatches in
      der Timeline-Lane; offen sind Palette-/Gradient-Stopp-Keyframes und eine
      Easing-Auswahl pro Keyframe.
- [x] Show-Erstellung beschleunigen: Auto-Show-Grundgerüst aus der Songanalyse
      (ein Track, ein Clip pro Section, Preset-Mapping, Beat-Gate auf Build/Drop,
      eine Undo-Transaktion, bestehende Spuren bleiben unangetastet).

## 3. Echter Pop-up Effekt-Baukasten (Modularer FX- & Emitter-Editor)

- [ ] Effekt-Baukasten als großzügiges, aufgeräumtes Pop-up-Fenster (`<dialog>`)
      neu konzipieren (kein bloßer Clip-Inspector-Klon). Ein `<dialog>` mit
      Live-Vorschau existiert; Umbau auf modulare Pipeline steht aus.
- [ ] Modulare Effekt-Pipeline aufbauen: Emitter/Generatoren (Punkt, Linie/Strip,
      Fläche, Partikel/Schwarm, Wellen, Noise, Szenen), Farbreaktion/Verläufe,
      3D-Transformationen/Masken und Filter-Kette (Blur, Glow/Bloom, Pixelation,
      Shutter/Gate, Grain, Inertia) flexibel kombinierbar und umsortierbar.
- [ ] Direkte Modulator- (`ƒ`) und Keyframe- (`◇`) Anbindung an Emitter-, Farb-
      und Filter-Parameter im Baukasten.
- [ ] Live-Vorschau im Baukasten mit Playback/Scrubbing, passendem Auto-Namen und
      1-Klick-Aktionen: *Als eigenen Effekt/Preset speichern*, *Auf Clip anwenden*,
      *Als neuen Clip einfügen*.
- [ ] Library um Favoriten, Zuletzt verwendet, Such-Highlighting und Sortieroptionen
      erweitern.

## 4. Audio-, Rhythmus- & Emitter-Expressions + Event-Trigger

- [ ] Ausdrucksstarke Audio- & Rhythmus-Signale bereitstellen (Beats, Takte, Phrasen,
      Abschnitte, Drop-Abstände, Frequenz-/Stem-Verläufe wie Bass/Kick/Snare/Hi-Hat/
      Energie/Dynamik) — algorithmisch/DSP-basiert und mit optionalen leichten CPU-
      Modellen für den 8700G.
- [ ] Raum- & Emitter-Bezug in Expressions/Modulatoren ermöglichen (Emitter-Index/
      Reihenfolge, Position, Abstand, Phasenversatz über Lampen/Strips).
- [ ] Geführte Funktions-Presets & Modulatoren im `ƒ`-Dialog anbieten (Puls,
      Ducking/Sidechain, Riser, Welle/Spread, Wiggle) mit Live-Vorschau und Fehlerfeedback.
- [ ] Event-basierte Auslöser ermöglichen (z. B. Partikel-/Shockwave-Spawn oder
      Impuls bei Beat, Onset, Snare, Kick oder Schwellwert statt nur starrem Timer).
- [ ] Spektrumanalyzer / Audio-Basis-Ansicht verfeinern (klare Erklärungen der Werte,
      optionale Einzel-Ausgabe/Isolierung eines Parameters, VU-Anzeigen sauber
      ausgelagert/einklappbar).

## 5. Lichtziele, Gruppen-HW-Out, Start-Hub & Live-Modus (`/live`)

- [ ] Zielauswahl für Ebenen/Effekte vereinfachen (einzelne Lampen, Mehrfachauswahl,
      Lampengruppen, Bereiche/Zonen, Global) inkl. Auto-Gruppen nach Typ/Bereich und
      anpassbarer Gruppen-Darstellung ohne überladene Menüs.
- [ ] Hardware-Ausgabe (`HW OUT`) nicht nur pro Fixture, sondern auch pro Gruppe/
      Bereich steuerbar machen (inkl. klarer Statusanzeige).
- [ ] Startseite / Projekt-Hub aufbauen („da weitermachen, wo du aufgehört hast“,
      letzte Shows, neue Show anlegen, Vorlagen/Szenen).
- [ ] `/live`-Modus neu ausrichten: erkannter Audio-Stream/Track, Umschaltung
      zwischen **Auto-Show**, **Shows**, **Szenen** (auch statische/dynamische
      Szenen ohne Audio) und **Direkt-Effekten** auf Global, Gruppen oder Lampen.

## 6. Spätere Erweiterungen (Zukunft / Backburner)

- [ ] Radar-gestützte Partikel- & Raum-Effekte (auf Basis der Hueme-Gateway-/
      Stream-Radar-Logik, inkl. Radar-Einstellungen und Raum-Visualisierung im Studio).
- [ ] Graph-Editor mit Kurven-/Geschwindigkeitsbearbeitung und Syntax-Highlighting /
      Autocomplete im Expression-Editor.
- [ ] Preset-Import/-Export (`.huemepreset`) und Live-Audio-FFT als Ergänzung zum
      deterministischen Blueprint.

## Verification checklist

- [x] `python3 -m pytest -q -W error`
- [x] `python3 -m compileall -q hueme_studio`
- [x] `node --check hueme_studio/static/*.js`
- [x] `node --test tests/test_snap.mjs tests/test_autoshow.mjs`
- [x] Headless-Browser-Check auf `/studio` und `/live` ohne Konsolen-/Serverfehler.
- [x] `docs/PROGRESS.md` und Checkpoint unter `docs/checkpoints/` aktualisieren.
