# IR-Adapter-Pilot: Registry-Konfiguration, 2026-11-01

## Erneuter Collector- und Consumercheck nach der Integration

Die Originalartefakte unten bleiben unverändert. Die erneuten Aufrufe am
01.11.2026 schreiben neue `*-v2.json`-Dateien:

| Ticker | Collector | Verifiziert im Schema-1-Snapshot und im öffentlichen Consumer |
|---|---|---|
| RHM.DE | Exit 0 | `ir-pilot-RHM.DE-v2.json`: **14 Figuren**, davon 8 Jahres- und 6 datierte Release-Figuren; Guidance **`cut`**. Backlog-/Kürzungsevidenz, Mess-/Veröffentlichungsdaten und Vertragsbasis kommen an. |
| ENR.DE | Exit 0 | `ir-pilot-ENR.DE-v2.json`: **7 Figuren**, Guidance **`confirm`**; Pre-tax-FCF und die vollständige Bestätigungsquotation kommen an. Quellformat `2,319 Mio EUR` bleibt erhalten. |

`ir-pilot-consumer-validation-v2.json` dokumentiert die realen
`get_news_supplement`-Aufrufe mit dem Snapshot-`as_of`; Fakten werden außerhalb
des kurzen Headlinefensters gerendert. Collector-`as_of`, Release-Einzelbelege
und Guidanceobjects sind inzwischen durch die [Transportintegration](ir-snapshot-transport.md)
mit getesteten historischen Grenzen gesichert. Vollständige archivierte
Emittenten-Vintages werden damit nicht behauptet. Oracle bleibt deaktiviert.

## Ergebnis des ersten echten Collector-Aufrufs (historische Artefakte)

Die Abrufe erfolgten ohne Modellaufruf über `news_collect.py facts`, mit Analyse-
und Abrufdatum **2026-11-01**. Erfolg bedeutet hier nicht HTTP 200, sondern
geprüften Dateiinhalt und belegte Zahlen/Aussagen. Die folgenden Dateien liegen
im dauerhaften Report-Unterverzeichnis `framework-audit/2026-11-01/`.

| Ticker | Collector | Tatsächliches Snapshot-Ergebnis | Audit-Abdeckung |
|---|---|---|---|
| RHM.DE | Exit 0 | `ir-pilot-RHM.DE.json`, Schema 1, 8 Jahreskennzahlen, Referenz 31.12.2025 | Jahresblock und Umsatz-/Margenguidance vorhanden. Neue Release-Fakten im Vendor vorhanden, im damaligen Snapshot-Serializer **noch verloren**. |
| ENR.DE | Exit 0 | `ir-pilot-ENR.DE.json`, Schema 1, 7 Einträge: 6 Kennzahlen plus Bestätigung, Referenz 2026-08-05 | Q3-Ergebnisrelease vom 05.08.2026 korrekt statt der neueren Buyback-Meldung ausgewählt; Bestätigung und obere Margenspanne kommen an. |
| ORCL | Exit 2 | **Kein** `ir-pilot-ORCL.json` erzeugt; `ir-pilot-ORCL-status.json` protokolliert den Fehler | Adapter bewusst deaktiviert: Rechteprüfung und datierbarer, zugänglicher Ergebnisrelease fehlen. Kein fingierter Fakten-Snapshot. |

`ir-pilot-validation.json` enthält tatsächliche Exit-Codes, Datum, Mengen und
Fehler; `ir-pilot-RHM.DE-vendor.json` und `ir-pilot-ENR.DE-vendor.json` bewahren
die vollständigen Vendor-Ergebnisse einschließlich belegter Release-Fakten.

### Rheinmetall: Datum und Vertragsbasis

Offizieller Ergebnisrelease: [H1 2026, veröffentlicht 06.08.2026](https://www.rheinmetall.com/de/media/news-watch/news/2026/08/2026-08-06-rheinmetall-pressemitteilung-halbjahresfinanzbericht-h1).

- Jahresblock unverändert: Auftragsbestand **63,8 Mrd EUR**, Referenz
  **31.12.2025**, fortgeführte Aktivitäten. Nicht der aktuelle H1-Bestand.
- Neu als separate Release-Evidenz: **80,5 Mrd EUR Rheinmetall Backlog**, gemessen
  **30.06.2026**, veröffentlicht **06.08.2026**, Konzern. Die Regex verlangt
  zusätzlich die Aussage, dass erwartete Abrufe aus bestehenden Rahmenverträgen
  enthalten sind. Dies sind **nicht ausschließlich feste Aufträge**; Nomination,
  Segment-Backlogs und garantierte Umsatzdeckung sind damit nicht gleichzusetzen.
- Neu als separate Release-Evidenz: explizite **300 Mio EUR Kürzung** der
  Umsatzerwartung für Marine und Konzern im laufenden FY2026, infolge F126.
  Kein Ist-Umsatz und keine nachträglich errechnete Differenz.
- Guidance tatsächlich: Umsatz **13,7–14,2 Mrd EUR**, operative Marge **rund 19 %**.
  Im ersten Pilotartefakt war `guidance_direction` noch `unspecified`.
  Die zusätzliche deutsche Erwartungs-Kürzungsregression korrigiert das
  Richtungsfeld auf `cut`; die Originaldatei wurde nicht überschrieben.
- Die vier vorhandenen H1/Q2-Prosaeinträge erhielten Messdatum 30.06.2026 und
  Konzern-/Periodenangaben; ihre Werte bleiben 15,0 %, 17,1 %, 5.227 Mio EUR
  Umsatz und 786 Mio EUR operatives Ergebnis.

**Historischer Schnittstellenbefund, inzwischen behoben:** Im ersten Pilot
verwarf `_normalise_facts` die sechs Release-Figuren und deren Einzelbelege;
außerdem fehlte das explizite Collector-`as_of`. Der anschließende generische
Transportfix erhält beide Datenfamilien und prüft Zeitgrenzen beim Build,
Import und Consumer. Die V2-Artefakte oben prüfen den wirklichen Transport;
die Originaldateien bleiben Belege des vorherigen Fehlers.

### Siemens Energy: Bestätigung ohne neue Zahlen

Quelle: [offizieller IR-Index](https://www.siemens-energy.com/global/en/home/investor-relations.html),
Eintrag **Siemens Energy Q3 FY26 Earnings Release – August 5, 2026**.
Q3 FY2026 umfasst 01.04.–30.06.2026; Veröffentlichung ist nicht Messdatum.

Tatsächlich extrahiert: Auftragseingang **17,9 Mrd EUR**, Auftragsbestand
**162 Mrd EUR** am Quartalsende, Umsatz **11,4 Mrd EUR**, Profit vor
Sondereffekten **1.623 Mio EUR**, **Free Cash Flow vor Steuern 2.319 Mio EUR**,
Basic EPS **1,28 EUR**. Die FCF-Zeile ist ausdrücklich pre-tax und durch
Kundenanzahlungen mitgestützt; sie darf nicht als nachsteuerlicher FCF erscheinen.

Die vollständige Bestätigung wird sowohl als generische Guidance
(`guidance_direction: confirm`) als auch durch einen zusätzlichen
`prose_facts`-Eintrag übernommen:

> Siemens Energy confirms its fiscal year 2026 outlook, which was raised after
> the end of the first half-year, and expects its Profit margin before Special
> Items to be towards the upper end of the guided range.

Kein erneut genannter Zahlenkorridor und keine erneute Anhebung werden daraus
konstruiert. Die Formulierung und ihr Veröffentlichungsdatum bleiben maßgeblich.
Die vorhandenen Kalenderdaten und deren Prüfdatum 29.10.2026 wurden unverändert
belassen; der Pilot behauptet keine neue Kalenderverifikation.

### Oracle: präziser Nicht-Erfolg

Geprüfte offizielle Kandidaten:

- [IR-News-Index](https://investor.oracle.com/investor-news/default.aspx),
  `https://investor.oracle.com/sitemap.xml` und
  [Q1-Ergebnisrelease](https://investor.oracle.com/investor-news/news-details/2026/Oracle-Announces-Q1-Results-Driven-by-Triple-Digit-Growth-in-Cloud-Infrastructure-Revenues/default.aspx):
  alle beim direkten Terminal-Abruf **HTTP 403**.
- [IR-robots.txt](https://investor.oracle.com/robots.txt): HTTP 200,
  `Allow: /`, `Crawl-delay: 10`. robots ist keine Nutzungsrechtsfreigabe.
- [Oracle Terms of Use](https://www.oracle.com/legal/terms.html): HTTP 200;
  verbietet Robot/Spider/Scraper/sonstige automatisierte Zugriffe ohne
  ausdrückliche schriftliche Genehmigung. Die Konfiguration bleibt daher
  konservativ deaktiviert, bis die einschlägigen Rechte geklärt sind.
- [Öffentlicher Newsroom](https://www.oracle.com/news/) und dessen Sitemap
  waren erreichbar. Der abgerufene aktuelle Newsroom-Ausschnitt enthielt keinen
  passenden Q1-Ergebnisrelease. HTTP 200 genügt nicht zur Quellenkonfiguration.
- Wayback-Verfügbarkeitsabfrage fand für den IR-Index einen Archivzeitpunkt
  01.09.2026. Das beweist weder die aktuelle Q1-Veröffentlichung noch deren
  Datum; der Archivindex wurde nicht als heutige Faktenquelle ausgegeben.

Der frühere Audit enthält die Aussagen **RPO 664 Mrd USD** und FY2027
**mindestens 90 Mrd USD Umsatz / 8,10 USD Non-GAAP EPS**, aber keine unabhängig
validierte Veröffentlichungsdatierung dieser Quelle. Sie wurden **nicht** als
neue Collector-Fakten ausgegeben. Ein URL-Jahr `2026` liefert keinen Kalendertag;
RPO ist kein Rheinmetall-artiger Backlog. Keine Kalenderdaten oder Verfügbarkeit
wurden erfunden. Der neue ORCL-Block enthält nur öffentliche Identität,
Quellenkandidaten und den dokumentierten Sperrgrund, keinen ungetesteten
`entry_regex` oder aktivierten Fallback.

## Ticker-unabhängiges Onboarding-Verfahren

1. Bestehenden Registry-Eintrag lesen und vor Änderung vollständig sichern.
   Nur eigene IR-Felder ändern; unbekannte und fremde Felder erhalten.
2. Öffentliche HTTPS-Quelle, robots und einschlägige Nutzungsbedingungen prüfen.
   `terms_checked` ist ein Prüfvermerk, kein automatischer Erlaubnisnachweis.
   Bei fehlender Genehmigung oder Zugänglichkeit Adapter deaktiviert lassen.
3. JSON-Kennzahlen mit Stichtagsfußnote bevorzugen; alternativ einen
   datierbaren Ergebnisrelease im Sitemap- oder `release_index`-Mechanismus
   identifizieren. Titel, Datum und Text müssen demselben Eintrag angehören.
   Body am nächsten Eintrag abschneiden, nicht blind feste Zeichenfenster mischen.
4. Prosa-Fakten ausschließlich über Registry-Regex extrahieren: `label`,
   Wertgruppe `v`, Einheit, Messdatum und Perimeter. Bei Prognosen Zielperiode
   und tatsächliche Veröffentlichungs-/Verifikationszeit getrennt halten.
   Bei Bestand Vertragsbasis prüfen; bei Cashflow Steuern und Definition nennen.
5. Bestätigung/oberes oder unteres Ende auch ohne Zahlen übernehmen. Fehlende
   Zahlen nicht aus einem älteren Release unter aktueller Datierung ergänzen.
6. Den echten Collector ausführen und **Dateiinhalt** prüfen:

   ```sh
   python "$REPO/hermes/skills/hermes-trading-scenarios/scripts/news_collect.py" \
     facts --ticker "$TICKER" --date "$AS_OF" \
     --out "$REPORT_DIR/ir-pilot-$TICKER.json"
   ```

   Schema 1, Ticker, `as_of`, Figuren, Guidance, Mess-/Publikationsdatum,
   Quellen und Coverage kontrollieren. Vendor-Ergebnis zusätzlich vergleichen:
   Transportverlust ist keine fehlende Quelle und darf nicht als Erfolg gelten.
7. Tests mit tatsächlich belegten kurzen Quellenausschnitten ergänzen;
   Datums-Cutoff vor Veröffentlichung muss dieselben Fakten zurückhalten.
   Erst separate End-to-End-Analystenprüfung beweist Prompt-Zustellung.

## Öffentliches Konfigurationsbeispiel

Der vorhandene generische Mechanismus benötigt keinen Ticker-Zweig im Code.
Ausschnitt aus dem Siemens-Energy-Profil (kein vollständiger Kalender):

```json
{
  "ir_facts": {
    "enabled": true,
    "terms_checked": "2026-11-01: robots.txt inspected; IR path allowed; personal internal analysis only",
    "facts_json": "",
    "quote_json": "",
    "overview_url": "https://www.siemens-energy.com/global/en/home/investor-relations.html",
    "reference_date": "",
    "perimeter_note": "Konzern, Q3 FY2026; 01.04.-30.06.2026",
    "release_index": {
      "url": "https://www.siemens-energy.com/global/en/home/investor-relations.html",
      "entry_regex": "&#34;dc:title&#34;:&#34;(?P<title>[^&]{10,200}?)\\s*-\\s*(?P<date>[A-Z][a-z]+ \\d{1,2}, \\d{4})&#34;",
      "date_format": "%B %d, %Y",
      "block_chars": 15000
    },
    "prose_facts": [{
      "label": "Free Cash Flow vor Steuern Q3 FY26",
      "regex": "Free cash flow pre tax increased to €(?P<v>[\\d.,]+)m",
      "suffix": " Mio EUR",
      "reference_date": "2026-06-30",
      "perimeter_note": "Konzern; Q3 FY2026; pre-tax, nicht nach Steuern"
    }],
    "calendar": [],
    "calendar_verified": ""
  }
}
```

## Point-in-time und verbleibende Grenzen

- Der Datenstichtag 30.06.2026 rechtfertigt **keine Verfügbarkeit am 30.06.2026**:
  RHM-Veröffentlichung 06.08.2026, ENR-Veröffentlichung 05.08.2026.
- Aktuelle JSON-Endpunkte, Kurs und Kalender sind keine historischen Snapshots.
  Ein heute gelesener Release-Index kann früher vorhandene Einträge verlieren
  oder verändern. Datumsfilter auf diesem Index beweisen keinen historischen
  Seitenzustand. Historische Current-Index-Rekonstruktion ist im Pilot
  **nicht verfügbar/nicht verifiziert**; kein Backtest-Erfolgsnachweis.
- Der aktuelle Snapshot-Serializer verliert Einzelbeleg-Datierung/Perimeter
  auch bei ENR. Die Vendor-Belege wurden deshalb separat dauerhaft gespeichert.
- Kein Oracle-Live-Adapter, kein Oracle-Fakten-Snapshot, kein LLM-/Analystenlauf.
  Gesamtintegration und Analysten-Prompt-Zustellung sind getrennt zu prüfen.
- Lokale Registry bleibt private Betriebskonfiguration; öffentliche Beispiele
  enthalten keine Kontodaten oder absoluten privaten Home-Verzeichnisse.

## Geänderte Schlüssel und Testnachweis

Semantischer Vergleich mit Backup: ausschließlich
`instruments.RHM.DE.ir_facts.prose_facts`,
`instruments.ENR.DE.ir_facts.terms_checked`,
`instruments.ENR.DE.ir_facts.prose_facts` sowie der neue öffentliche
`instruments.ORCL`-Eintrag. Alle übrigen Datenfelder unverändert.

`tests/test_ir_adapter_pilot.py`: **4 Tests bestanden** (Backlog-Basis und beide
Datumsarten, Verfügbarkeit vor Veröffentlichung gesperrt, Bestätigung ohne
Zahlen, Index-Body ohne Eintragsüberschneidung). Keine Änderungen am
`issuer_facts`-Code, keine Commits oder Pushes.
