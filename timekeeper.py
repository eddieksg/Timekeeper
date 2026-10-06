"""TimeKeeper: a small Windows NTP desktop client and optional LAN server."""
import ctypes
import json
import os
import queue
import socket
import struct
import threading
import time
import secrets
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import messagebox, ttk

EPOCH = 2208988800
SETTINGS = Path(os.getenv('APPDATA', str(Path.home()))) / 'TimeKeeper' / 'settings.json'
DEFAULTS = {'servers': 'time.windows.com\npool.ntp.org', 'interval': 60,
            'timeout': 3, 'serve': False, 'bind': '0.0.0.0'}


def query_ntp(host, timeout=3):
    """Return offset, delay, stratum and server UTC, following RFC 5905 timestamps."""
    request = bytearray(48)
    request[0] = 0x23  # client, version 4
    nonce = secrets.token_bytes(8)
    request[40:48] = nonce
    t1 = time.time()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.sendto(request, (host, 123))
        packet, address = sock.recvfrom(512)
        t4 = time.time()
    finally:
        sock.close()
    if (len(packet) < 48 or packet[0] & 7 != 4 or packet[1] == 0
            or packet[1] > 15 or packet[0] >> 6 == 3
            or packet[24:32] != nonce or address[1] != 123):
        raise ValueError('Invalid or unsynchronized NTP response')
    def stamp(pos):
        sec, frac = struct.unpack_from('!II', packet, pos)
        return sec - EPOCH + frac / 2**32
    t2, t3 = stamp(32), stamp(40)
    if not t2 or not t3 or t3 < t2:
        raise ValueError('NTP server returned empty timestamps')
    delay = (t4 - t1) - (t3 - t2)
    if delay < -0.05 or delay > 10:
        raise ValueError('NTP response has implausible round trip delay')
    return {'offset': ((t2 - t1) + (t3 - t4)) / 2,
            'delay': max(0, delay), 'stratum': packet[1],
            'utc': datetime.fromtimestamp(t3, timezone.utc)}


def set_windows_clock(unix_time):
    if os.name != 'nt':
        raise RuntimeError('Clock changes are supported on Windows only')
    dt = datetime.fromtimestamp(unix_time, timezone.utc)
    class SYSTEMTIME(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ushort) for n in ('year','month','day_of_week','day','hour','minute','second','milliseconds')]
    value = SYSTEMTIME(dt.year, dt.month, (dt.weekday()+1) % 7, dt.day,
                       dt.hour, dt.minute, dt.second, dt.microsecond // 1000)
    if not ctypes.windll.kernel32.SetSystemTime(ctypes.byref(value)):
        raise ctypes.WinError()


def ntp_stamp(t):
    whole = int(t) + EPOCH
    return struct.pack('!II', whole & 0xffffffff, int((t % 1) * 2**32))


class App:
    def __init__(self, root):
        self.root = root
        self.root.title('TimeKeeper — NTP desktop')
        self.root.geometry('760x570')
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.server_stop = threading.Event()
        self.server_thread = None
        self.latest = None
        self.checking = False
        try:
            config = {**DEFAULTS, **json.loads(SETTINGS.read_text(encoding='utf-8'))}
        except (OSError, ValueError):
            config = DEFAULTS.copy()
        self.config = config
        self.status = tk.StringVar(value='No successful time check yet')
        self.clock = tk.StringVar()
        self.interval = tk.StringVar(value=str(config['interval']))
        self.timeout = tk.StringVar(value=str(config['timeout']))
        self.serve = tk.BooleanVar(value=bool(config['serve']))
        self.bind = tk.StringVar(value=config['bind'])
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='TimeKeeper', font=('Segoe UI', 18, 'bold')).pack(anchor='w')
        ttk.Label(frame, textvariable=self.clock).pack(anchor='w', pady=(2, 10))
        ttk.Label(frame, textvariable=self.status, wraplength=700).pack(anchor='w', pady=(0, 12))
        ttk.Label(frame, text='NTP servers (one hostname or IPv4 address per line):').pack(anchor='w')
        self.servers = tk.Text(frame, height=4, width=70)
        self.servers.pack(fill='x')
        self.servers.insert('1.0', config['servers'])
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=10)
        ttk.Label(row, text='Check interval (minutes)').pack(side='left')
        ttk.Entry(row, textvariable=self.interval, width=6).pack(side='left', padx=(6, 20))
        ttk.Label(row, text='Timeout (seconds)').pack(side='left')
        ttk.Entry(row, textvariable=self.timeout, width=6).pack(side='left', padx=6)
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x')
        ttk.Button(buttons, text='Check now', command=self.check).pack(side='left')
        ttk.Button(buttons, text='Set Windows clock', command=self.adjust).pack(side='left', padx=8)
        ttk.Button(buttons, text='Save settings', command=self.save).pack(side='left')
        ttk.Separator(frame).pack(fill='x', pady=12)
        ttk.Checkbutton(frame, text='Serve NTP to LAN clients after a successful check', variable=self.serve,
                        command=self.toggle_server).pack(anchor='w')
        row2 = ttk.Frame(frame)
        row2.pack(fill='x', pady=6)
        ttk.Label(row2, text='Listen address').pack(side='left')
        ttk.Entry(row2, textvariable=self.bind, width=18).pack(side='left', padx=8)
        ttk.Label(row2, text='UDP 123; may need firewall rule and free port').pack(side='left')
        ttk.Label(frame, text='Recent events').pack(anchor='w', pady=(8, 2))
        self.log = tk.Text(frame, height=9, state='disabled')
        self.log.pack(fill='both', expand=True)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(100, self.poll)
        self.root.after(1000, self.tick)
        threading.Thread(target=self.schedule, daemon=True).start()
        if self.serve.get():
            self.start_server()

    def write(self, line):
        self.log.configure(state='normal')
        self.log.insert('end', datetime.now().strftime('%H:%M:%S') + '  ' + line + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def current(self):
        names = [s.strip() for s in self.servers.get('1.0', 'end').splitlines() if s.strip()]
        interval, timeout = int(self.interval.get()), float(self.timeout.get())
        if not names or not 1 <= interval <= 10080 or not 0.5 <= timeout <= 30:
            raise ValueError('Enter a server, interval 1–10080 minutes, and timeout 0.5–30 seconds')
        return {'servers': '\n'.join(names), 'interval': interval, 'timeout': timeout,
                'serve': self.serve.get(), 'bind': self.bind.get().strip()}

    def save(self):
        try:
            self.config = self.current()
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS.write_text(json.dumps(self.config, indent=2), encoding='utf-8')
            self.write('Settings saved')
        except (ValueError, OSError) as e:
            messagebox.showerror('Settings', str(e))

    def check(self):
        if self.checking:
            return
        try:
            cfg = self.current()
        except ValueError as e:
            messagebox.showerror('Settings', str(e))
            return
        self.status.set('Checking NTP servers…')
        self.checking = True
        threading.Thread(target=self.worker, args=(cfg,), daemon=True).start()

    def worker(self, cfg):
        errors = []
        for host in cfg['servers'].splitlines():
            if self.stop.is_set():
                return
            try:
                result = query_ntp(host, cfg['timeout'])
                self.events.put(('success', host, result))
                return
            except (OSError, ValueError) as e:
                errors.append(f'{host}: {e}')
        self.events.put(('failure', '; '.join(errors)))

    def schedule(self):
        # Wait before first automatic check so the UI is ready.
        while not self.stop.wait(max(60, int(self.config['interval']) * 60)):
            self.events.put(('scheduled',))

    def poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == 'scheduled':
                    self.check()
                elif event[0] == 'success':
                    self.checking = False
                    _, host, result = event
                    self.latest = (host, result, time.time())
                    line = (f'{host} | offset {result["offset"] * 1000:+.1f} ms | '
                            f'delay {result["delay"] * 1000:.1f} ms | stratum {result["stratum"]}')
                    self.status.set('Last check: ' + line)
                    self.write(line)
                elif event[0] == 'failure':
                    self.checking = False
                    self.latest = None
                    self.status.set('Time check failed')
                    self.write('Failed: ' + event[1])
                elif event[0] == 'log':
                    self.write(event[1])
        except queue.Empty:
            pass
        if not self.stop.is_set():
            self.root.after(100, self.poll)

    def tick(self):
        self.clock.set('Local: ' + datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z') +
                       '     UTC: ' + datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
        if not self.stop.is_set():
            self.root.after(1000, self.tick)

    def adjust(self):
        if not self.latest or time.time() - self.latest[2] > 30:
            messagebox.showinfo('Fresh check required', 'Run Check now, then set the clock within 30 seconds.')
            return
        host, result, _ = self.latest
        offset = result['offset']
        if not messagebox.askyesno('Set Windows clock', f'Apply an estimated {offset:+.3f} second correction from {host}?\nAdministrator rights are required.'):
            return
        try:
            set_windows_clock(time.time() + offset)
            self.write(f'Windows clock adjusted using {host} ({offset:+.3f} s)')
            self.latest = None
        except (OSError, RuntimeError) as e:
            messagebox.showerror('Clock update failed', str(e))

    def toggle_server(self):
        if self.serve.get():
            self.start_server()
        else:
            self.server_stop.set()
            self.write('NTP server stopping')

    def start_server(self):
        if self.server_thread and self.server_thread.is_alive():
            self.write('Wait for the NTP server to stop before enabling again')
            return
        self.server_stop.clear()
        bind = self.bind.get().strip()
        self.server_thread = threading.Thread(target=self.server_loop, args=(bind,), daemon=True)
        self.server_thread.start()

    def server_loop(self, bind):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((bind, 123))
            sock.settimeout(0.5)
            self.events.put(('log', f'NTP server listening on {bind}:123'))
            while not self.server_stop.is_set() and not self.stop.is_set():
                try:
                    data, address = sock.recvfrom(512)
                except socket.timeout:
                    continue
                if len(data) < 48 or data[0] & 7 != 3:
                    continue
                # The server uses an estimated corrected clock, never raw local time.
                # Expire the sample quickly; stop serving if upstream is unavailable.
                if not self.latest or time.time() - self.latest[2] > 300:
                    continue
                offset = self.latest[1]['offset']
                received = time.time() + offset
                response = bytearray(48)
                response[0] = (data[0] & 0x38) | 4  # preserve client version, server mode
                response[1] = min(15, self.latest[1]['stratum'] + 1)
                response[2] = data[2]
                response[3] = 0xec  # precision -20
                response[24:32] = data[40:48]
                response[32:40] = ntp_stamp(received)
                response[40:48] = ntp_stamp(time.time() + offset)
                sock.sendto(response, address)
        except OSError as e:
            self.events.put(('log', f'NTP server error: {e}'))
        finally:
            sock.close()
            self.events.put(('log', 'NTP server stopped'))

    def close(self):
        self.stop.set()
        self.server_stop.set()
        self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    App(root)
    root.mainloop()
