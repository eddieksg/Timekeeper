# TimeKeeper

A Python desktop NTP client for Windows 10 and later, inspired by the general functionality of Tardis 2000. This is an independent project and is not affiliated with its publisher.

**Features:** query configured NTP servers; display estimated offset, round trip delay and stratum; repeat checks on a configurable interval; manually adjust the Windows clock after confirmation; optionally answer NTP requests on a LAN. Settings live in `%APPDATA%\TimeKeeper\settings.json`.

## Run from source

Install Python 3.10+ with Tkinter. In this folder, run:

```bat
py -3 timekeeper.py
```

Enter an NTP hostname or IPv4 address on each line and click **Check now**. UDP 123 must be reachable. To change the Windows clock, start Command Prompt as administrator and run the app from there. Click **Set Windows clock** within 30 seconds of a successful check. Automatic checks only measure and report; they never change the clock.

## Build a portable executable on Windows

```bat
py -3 -m pip install pyinstaller
py -3 -m PyInstaller --onefile --windowed --name TimeKeeper timekeeper.py
```

The output is `dist\TimeKeeper.exe`. Build on Windows, then test the executable on a separate Windows 10 or 11 machine. This repository does not ship a prebuilt binary or installer.

## Optional LAN NTP response

The checkbox enables a basic UDP 123 responder after a successful upstream check. It uses a recent offset estimate to answer requests and stops responding after the sample expires. Ensure UDP 123 is free; Windows Time or other NTP software may already bind it. Configure the Windows Firewall for the trusted LAN only. Saving settings persists the checkbox and bind address. The server is active only while the GUI is running.

## Suitability and limitations

The desktop client is suitable for learning, demonstrations and controlled test networks. For a production workstation, you may use its monitoring display after local validation, but rely on Windows Time or another established time daemon for continuous clock discipline. **Do not use this implementation as an authoritative production NTP server**, especially for Active Directory, rail, security, recording or other systems where traceable and reliable time is required. It has no service mode, authenticated NTP/NTS, redundant source consensus, holdover model, leap handling, rate limiting, persistent audit log or monitoring integration. An NTP offset from one reply is subject to asymmetric delay and compromised upstream sources. Large manual time steps may affect applications, certificates and logs.

The implementation has no runtime dependencies outside Python's standard library. No telemetry or automatic update is included.

## Tests

```bat
py -3 -m unittest discover -s tests -v
```

The tests use fake NTP packets and do not alter the machine clock or contact external servers. Windows CI runs the tests on pushes and pull requests.

## License

MIT. See [LICENSE](LICENSE).
