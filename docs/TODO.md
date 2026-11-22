# Hueme Studio — TODO / task board

> **Read this after `docs/PROGRESS.md`.** Update this file whenever a task starts,
> lands or is deliberately deferred. One checkbox = one shippable change.
>
> This is the UX/workflow restructure board. The earlier P0/P1/P2 items are
> merged in (P0/P1 complete; remaining P2 items live in section 6). The legacy
> Effect Builder was not counted as P3 progress; P3 slice 1 replaced it.

Last updated: 2026-11-22 (P3 slice 1: Effect Builder replaced; board re-audited
against the code)

## Current state

- Core engine, UI shell, presets/filters, expressions, filter keyframes,
  Effect Builder, Timeline Analysis, P1 polish, P2 slices 1–2 and P3 slice 1 are
  in place.
- **179 passed**, 6 subtests; `node --test tests/test_snap.mjs tests/test_autoshow.mjs`
  adds 9 JS cases.
- P3 slice 1 replaced the old form-dump dialog with a module rack (pipeline
  rail, contextual inspector, 320×180 clip-local preview, saved looks, local
  ◇/ƒ automation). The engine still renders one generator per clip; multiple
  generator/colour/transform modules are the open P3 engine work.
- Honest assessment: usability and creative flow improved, but layer hierarchy,
  the multi-generator pipeline, event triggers and the `/live` rework remain.
- Reference analysis: `docs/effectcraft-analysis.md` (read-only).
- Server: `python3 -m hueme_studio --host 0.0.0.0 --port 8090`.

## 0. Stabilität, Performance & bekannte Bugs beheben

- [x] Memory-Wachstum im Renderer/Server untersuchen und begrenzen
      (Preview-Cache LRU mit 64 Einträgen; Regressionstest). Der 16-GB-Spike
      wurde lokal nicht reproduziert, der einzige unbegrenzte Server-Cache ist
      gedeckelt.
- [x] Doppelklick-Mehrfachauslösung bei Presets/Filtern in `studio.js` beheben.
- [x] Timeline-Expression-Vorschau mit echten Audio-/Envelope-/Blueprint-Werten
      speisen statt fester Dummy-Werte.
- [x] Sync-Offset-State sauber synchronisieren (keine verwaisten
      `localStorage`-Stände gegenüber dem Server-Zustand).
- [x] 1800K-Amber-Extraktion in `hal.py` praxistauglich kalibrieren.
- [x] Kleine Code-Bugs in `presets.py` (`shockwave_3d`, `strobe_blinder`),
      `server.py` (sauberer Restart) und `mcp.py` beheben.
- [x] `config/sendspin_pairings.json` und `config/sendspin_identity.json` aus
      dem Repo nehmen (Runtime-Secrets; bereits in `.gitignore`).
- [x] Effect-Builder-Vorschau entschärft: `mirror_symmetry` nutzt auf dem
      Front-Grid ein exaktes O(n)-Mapping statt O(n²) pro Frame. Gemessen:
      7,3 s → 9 ms bei 320×180 für einen Clip mit Spiegel-Filter.
- [ ] Sendspin-PSK rotieren und neu paaren (extern; der alte PSK stand in der
      Git-Historie).

## 1. UI/UX-Klarheit, Toolbar, Inspector & Viewport-Orientierung

- [ ] Topbar entrümpeln und responsiv gestalten: klare Menüs (`Datei`, `Bearbeiten`,
      `Ebenen`, `Filter & Effekte`, `Ansicht`, `Hilfe`), keine abgeschnittenen Badges,
      konsistente Sprache und Symbolik (MDI-Icons) statt Textwüste. Menüs und
      Icons existieren; offen sind responsive Dichte und Badge-Verhalten.
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
      Parent/Blend/Mute/Solo/Clipping existieren im Inspector und Kontextmenü;
      Drag-to-Parent und eine klarere Hierarchie-Darstellung fehlen.
- [ ] Farbliche & Gradient-Keyframes sauber unterstützen. Farbkeyframes sind
      im Inspector keybar und erscheinen als Verlaufsstreifen plus Swatches in
      der Timeline-Lane; offen sind Palette-/Gradient-Stopp-Keyframes und eine
      Easing-Auswahl pro Keyframe.
- [x] Show-Erstellung beschleunigen: Auto-Show-Grundgerüst aus der Songanalyse
      (ein Track, ein Clip pro Section, Preset-Mapping, Beat-Gate auf Build/Drop,
      eine Undo-Transaktion, bestehende Spuren bleiben unangetastet).

## 3. Echter Pop-up Effekt-Baukasten (Modularer FX- & Emitter-Editor)

- [x] Effekt-Baukasten als großzügiges, aufgeräumtes Pop-up-Fenster (`<dialog>`)
      neu konzipieren. Der alte Zwei-Spalten-Formularklon ist ersetzt:
      1180×840-Dialog mit Pipeline-Rail, kontextuellem Inspector, großer
      320×180-Vorschau, clip-lokalem Transport und gespeicherten Looks.
- [ ] Modulare Effekt-Pipeline aufbauen. Vorhanden: Generator + Maske +
      umsortierbare Filterkette. Offen: mehrere Generatoren gleichzeitig,
      Colour-/Transform-Module und deren Ausführung im Compositor.
- [x] Direkte Modulator- (`ƒ`) und Keyframe- (`◇`) Anbindung an Generator- und
      Filter-Parameter im Baukasten. Die Automation lebt im Rezept und reist mit
      Apply/Insert in den Clip; Emitter-/Transform-Module folgen mit 3.2.
- [x] Live-Vorschau im Baukasten mit Playback/Scrubbing, passendem Auto-Namen und
      1-Klick-Aktionen: *Als eigenen Look speichern*, *Auf Clip anwenden*,
      *Als neuen Clip einfügen*. Gespeicherte Looks lassen sich im Rail laden.
- [x] Library um Favoriten, Zuletzt verwendet, Such-Highlighting und Sortieroptionen
      erweitern (P1).

## 4. Audio-, Rhythmus- & Emitter-Expressions + Event-Trigger

- [ ] Ausdrucksstarke Audio- & Rhythmus-Signale bereitstellen. Vorhanden:
      Beat/Takt/Phrase, Sections, `beatsToDrop`, `bass`/`mid`/`high` sowie
      `kick`/`snare`/`hat`/`energy`/`percussive`/`prominence`. Offen: eine echte
      `dynamics`-Kurve und DSP-Feinschliff.
- [ ] Raum- & Emitter-Bezug in Expressions/Modulatoren ermöglichen. Presets
      kennen `emitterIdx`, `emitterNorm`, `nx`/`ny`/`nz` und `distCenter`;
      Clip-Expressions und Modulatoren haben noch keinen Emitter-Bezug.
- [x] Geführte Funktions-Presets & Modulatoren im `ƒ`-Dialog anbieten (Beat LFO,
      Stem Follower, Sidechain Duck, Phrase Riser, Emitter Wave, Wiggle) mit
      Live-Vorschau und Fehlerfeedback.
- [ ] Event-basierte Auslöser ermöglichen (z. B. Partikel-/Shockwave-Spawn oder
      Impuls bei Beat, Onset, Snare, Kick oder Schwellwert statt nur starrem Timer).
- [ ] Spektrumanalyzer / Audio-Basis-Ansicht verfeinern. Analysis-Modus, Legende,
      Playhead-Werte, Spektrum-Strip und Dominance-Readout existieren; offen sind
      Isolierung einzelner Parameter, einklappbare VU-Anzeigen und Hilfetexte.

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
- [ ] Weitere Blend-Modes nur ergänzen, wenn ein konkreter Look sie braucht
      (die fünf bestehenden reichen bislang).

## Verification checklist

- [x] `python3 -m pytest -q -W error` (179 passed, 6 subtests)
- [x] `python3 -m compileall -q hueme_studio`
- [x] `node --check hueme_studio/static/client.js studio.js live.js icons.js snap.js autoshow.js`
- [x] `node --test tests/test_snap.mjs tests/test_autoshow.mjs` (9 cases)
- [x] Headless-Browser-Check auf `/studio` und `/live` ohne Konsolen-/Serverfehler.
- [x] Effect Builder: Modul-Rail, Filter hinzufügen, Maske, lokaler Keyframe/
      Ausdruck, Look speichern/laden, Apply und Insert headless geprüft.
- [x] `docs/PROGRESS.md` und Checkpoint unter `docs/checkpoints/` aktualisieren.
