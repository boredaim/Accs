# Steam Account Tracker (lokal)

Lokale Windows-Webapp zum Tracken mehrerer Steam-Accounts (Flask + SQLite) im Steam-ähnlichen Dark Theme.

## Setup (Windows)

1. **Projektordner öffnen**
   ```bash
   cd /path/to/Accs
   ```

2. **Virtuelle Umgebung erstellen & aktivieren**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```

3. **Abhängigkeiten installieren**
   ```bash
   pip install -r requirements.txt
   ```

4. **.env anlegen**
   ```bash
   copy .env.example .env
   ```
   Trage in `.env` deinen Steam Web API Key ein:
   ```
   STEAM_API_KEY=...
   ```

5. **App starten**
   ```bash
   python app.py
   ```

Die App läuft anschließend unter `http://127.0.0.1:5000`.

## Hinweise

- Steam-Infos werden über die Steam Web API geladen und in der SQLite-Datenbank gecached.
- Falls API-Aufrufe fehlschlagen, wird der Account dennoch angelegt und eine dezente Warnung angezeigt.
- **Weekly Reset** der Checkbox „Weekly Drop“ erfolgt **nur beim App-Start**.
  - Reset-Zeitpunkt: Mittwoch, 01:00 (Europe/Berlin)
  - Die Prüfung erfolgt beim Start der App.

## Optional: config.json

Alternativ zur `.env` kann ein `config.json` im Projektverzeichnis genutzt werden:

```json
{
  "STEAM_API_KEY": "dein_api_key_hier"
}
```
