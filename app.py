"""Windows GUI. Secrets stay on this PC and are encrypted using Windows DPAPI."""
import base64
import ctypes
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from datetime import datetime
from core import Client, Store, UTC, category, service, parse_time, DEFAULT_INSTANT, DEFAULT_PRIORITY

DATA = Path(os.environ.get("LOCALAPPDATA", str(Path.cwd()))) / "GineeInstantNotifier"
ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
_instance_handle = None


def single_instance():
    global _instance_handle
    if sys.platform != "win32":
        return True
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel.CreateMutexW(None, False, "Local\\GineeInstantNotifier")
    if not handle:
        raise RuntimeError("Windows tidak dapat membuat pengunci aplikasi.")
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)
        return False
    _instance_handle = handle
    return True


def protect(raw, decrypt=False):
    if sys.platform != "win32":
        raise RuntimeError("Penyimpanan kunci hanya tersedia di Windows.")
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError("Windows gagal mengenkripsi/membuka kunci API.")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def speak(text):
    if sys.platform != "win32":
        return
    from ctypes import wintypes
    filename = "prioritas.mp3" if "prioritas" in text.casefold() else "instan.mp3"
    clip = ASSETS / "audio" / filename
    if not clip.is_file():
        clip = DATA / "audio" / filename
    if not clip.is_file() or clip.stat().st_size == 0:
        raise RuntimeError("Rekaman suara Indonesia belum tersedia.")
    command = ctypes.WinDLL("winmm", use_last_error=True).mciSendStringW
    command.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, wintypes.HWND]
    command.restype = wintypes.DWORD
    alias = "ginee_voice_" + str(threading.get_ident())
    if command(f'open "{clip}" type mpegvideo alias {alias}', None, 0, None):
        raise RuntimeError("Windows tidak dapat membuka rekaman suara.")
    try:
        if command(f"play {alias} wait", None, 0, None):
            raise RuntimeError("Windows gagal memutar rekaman suara.")
    finally:
        command(f"close {alias}", None, 0, None)


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Pengumuman Pesanan Instan — Ginee")
        self.root.geometry("1080x740")
        self.root.minsize(850, 640)
        DATA.mkdir(parents=True, exist_ok=True)
        self.store = Store(DATA / "history.sqlite")
        self.events = queue.Queue()
        self.audio = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.running = False
        self.next_reminder = {}
        self.popup_window = None
        self.config_file = DATA / "settings.json"
        self.keys_file = DATA / "keys.bin"
        self.key = tk.StringVar()
        self.secret = tk.StringVar()
        self.instant = tk.StringVar(value=DEFAULT_INSTANT)
        self.priority = tk.StringVar(value=DEFAULT_PRIORITY)
        self.interval = tk.StringVar(value="30")
        self.sound = tk.BooleanVar(value=True)
        self.popup_enabled = tk.BooleanVar(value=True)
        self.remind = tk.BooleanVar(value=False)
        self.auto = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Belum terhubung. Masukkan API lalu klik Mulai.")
        self.last_status_time = 0
        self.build_ui()
        self.load()
        threading.Thread(target=self.audio_loop, daemon=True).start()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(250, self.pump)
        root.after(1000, self.tick)
        if self.auto.get() and self.key.get() and self.secret.get():
            root.after(500, self.start)

    def build_ui(self):
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Pengumuman Pesanan Instan", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Ginee OMS • Aplikasi harus tetap berjalan; boleh diminimalkan.").pack(anchor="w", pady=(2,12))
        tabs = ttk.Notebook(outer)
        tabs.pack(fill="both", expand=True)
        monitor, settings = ttk.Frame(tabs, padding=12), ttk.Frame(tabs, padding=12)
        tabs.add(monitor, text="Pesanan & status")
        tabs.add(settings, text="Pengaturan API & layanan")
        buttons = ttk.Frame(monitor)
        buttons.pack(fill="x")
        self.start_button = ttk.Button(buttons, text="Mulai / uji koneksi", command=self.start)
        self.start_button.pack(side="left")
        ttk.Button(buttons, text="Hentikan", command=self.stop).pack(side="left", padx=6)
        ttk.Button(buttons, text="Tes suara & pop-up", command=self.test_alert).pack(side="left")
        ttk.Button(buttons, text="Tes instan biasa", command=lambda: self.test_alert("INSTAN")).pack(side="left", padx=6)
        ttk.Button(buttons, text="Salin log", command=self.copy_log).pack(side="left")
        ttk.Label(monitor, textvariable=self.status, wraplength=950).pack(anchor="w", pady=12)
        ttk.Label(monitor, text="Timer packing hanya Shopee Instan Prioritas: pembayaran + 15 menit. TikTok instan tidak memakai timer ini.",
            foreground="#a34a00").pack(anchor="w", pady=(0,8))
        columns = ("number", "shop", "service", "kind", "remaining", "state")
        box = ttk.Frame(monitor)
        box.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(box, columns=columns, show="headings", selectmode="browse")
        for field, label, width in zip(columns,
            ("Nomor pesanan", "ID toko", "Layanan", "Jenis", "Sisa packing*", "Status lokal"),
            (170, 125, 240, 95, 110, 110)):
            self.tree.heading(field, text=label)
            self.tree.column(field, width=width, minwidth=70)
        self.tree.tag_configure("priority", foreground="#b42318")
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)
        ttk.Button(monitor, text="Sudah ditangani — hentikan pengingat pesanan terpilih",
            command=self.acknowledge).pack(anchor="w", pady=8)
        ttk.Label(monitor, text="Tombol ini hanya mengubah status di aplikasi ini, bukan status pesanan Ginee.").pack(anchor="w")
        ttk.Label(monitor, text="Layanan yang terdeteksi (tanpa data pembeli):").pack(anchor="w", pady=(10,2))
        self.log = tk.Text(monitor, height=5, state="disabled", wrap="word", font=("Consolas", 9))
        self.log.pack(fill="x")
        settings.columnconfigure(1, weight=1)
        self.settings_controls = []
        for row, (label, var, masked) in enumerate([
            ("Access Key", self.key, True), ("Secret Key", self.secret, True),
            ("Pola instan biasa", self.instant, False), ("Pola instan prioritas", self.priority, False),
            ("Interval cek (detik, minimal 15)", self.interval, False)]):
            ttk.Label(settings, text=label).grid(row=row, column=0, sticky="w", padx=(0,15), pady=8)
            entry = ttk.Entry(settings, textvariable=var, show="•" if masked else "")
            entry.grid(row=row, column=1, sticky="ew")
            self.settings_controls.append(entry)
        ttk.Label(settings, wraplength=760, text=(
            "Pola dipisah koma, dicocokkan dengan nama/jenis/ID layanan. Isi pola PRIORITAS hanya setelah "
            "memastikan label API pesanan prioritas. SPX Instant Prioritas sudah terkonfirmasi. Nama kurir saja tidak cukup membedakan "
            "instan dan same-day. Lihat daftar layanan di tab Pesanan. Pola prioritas diperiksa lebih dulu."
        )).grid(row=5, column=0, columnspan=2, sticky="w", pady=8)
        for row, (label, var) in enumerate([
            ("Suara pengumuman", self.sound), ("Pop-up pesanan", self.popup_enabled),
            ("Pengingat prioritas tiap 60 detik sampai ditandai ditangani", self.remind),
            ("Langsung memantau saat aplikasi dibuka", self.auto)], start=6):
            control = ttk.Checkbutton(settings, text=label, variable=var)
            control.grid(row=row, column=0, columnspan=2, sticky="w", pady=4)
            self.settings_controls.append(control)
        self.save_button = ttk.Button(settings, text="Simpan pengaturan", command=self.save)
        self.save_button.grid(row=10, column=0, sticky="w", pady=12)
        ttk.Label(settings, wraplength=800, text=(
            "Kunci disimpan terenkripsi memakai Windows DPAPI, hanya untuk akun Windows ini. "
            "Hentikan pemantauan sebelum mengubah pengaturan. Pemantauan awal membaca 15 menit terakhir; "
            "pesanan instan aktif yang belum tercatat juga dapat diumumkan."
        )).grid(row=11, column=0, columnspan=2, sticky="w", pady=10)

    def load(self):
        try:
            if self.config_file.exists():
                config = json.loads(self.config_file.read_text(encoding="utf-8"))
                for name in ("instant", "priority", "interval", "sound", "popup_enabled", "remind", "auto"):
                    if name in config:
                        getattr(self, name).set(config[name])
            if self.keys_file.exists():
                keys = json.loads(protect(self.keys_file.read_bytes(), decrypt=True))
                self.key.set(keys["key"])
                self.secret.set(keys["secret"])
        except Exception:
            self.status.set("Pengaturan/kunci tersimpan tidak dapat dibuka. Isi ulang pada tab Pengaturan.")

    def options(self):
        try:
            interval = int(self.interval.get())
            if not 15 <= interval <= 3600:
                raise ValueError
        except ValueError:
            raise ValueError("Interval harus antara 15 dan 3600 detik.") from None
        if not self.key.get().strip() or not self.secret.get().strip():
            raise ValueError("Access Key dan Secret Key harus diisi.")
        return interval

    def save(self):
        try:
            self.options()
            encrypted = protect(json.dumps({"key": self.key.get().strip(), "secret": self.secret.get().strip()}).encode())
            self.keys_file.write_bytes(encrypted)
            config = {name: getattr(self, name).get() for name in
                ("instant", "priority", "interval", "sound", "popup_enabled", "remind", "auto")}
            self.config_file.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            self.status.set("Pengaturan disimpan di komputer ini.")
            return True
        except Exception as exc:
            messagebox.showerror("Pengaturan", str(exc))
            return False

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.save():
            return
        config = (self.key.get().strip(), self.secret.get().strip(), self.options(), self.instant.get(), self.priority.get())
        self.stop_event.clear()
        self.running = True
        self.start_button.configure(state="disabled")
        self.save_button.configure(state="disabled")
        for control in self.settings_controls:
            control.configure(state="disabled")
        self.status.set("Menghubungkan ke Ginee…")
        self.worker = threading.Thread(target=self.poll, args=config, daemon=True)
        self.worker.start()

    def stop(self):
        self.running = False
        self.stop_event.set()
        try:
            while True:
                self.audio.get_nowait()
        except queue.Empty:
            pass
        self.status.set("Menghentikan pemantauan…")
        if not self.worker or not self.worker.is_alive():
            self.unlock()

    def unlock(self):
        self.start_button.configure(state="normal")
        self.save_button.configure(state="normal")
        for control in self.settings_controls:
            control.configure(state="normal")

    def poll(self, key, secret, interval, instant, priority):
        store = Store(DATA / "history.sqlite")
        client = Client(key, secret)
        delay = interval
        try:
            while not self.stop_event.is_set():
                until = datetime.now(UTC)
                since = store.since(until)
                try:
                    # Break extended offline periods into API-compatible windows.
                    from datetime import timedelta
                    end = min(until, since + timedelta(days=14))
                    orders = client.orders(since, end)
                    if self.stop_event.is_set():
                        break
                    store.ingest(orders, end, instant, priority)
                    services = sorted({
                        f"{o.get('channelId') or o.get('channel') or '?'} | "
                        f"{o.get('orderStatus', '?')} | {o.get('externalOrderSn', '?')} | "
                        f"{service(o).strip(' /|') or '(layanan belum tersedia)'} => "
                        f"{category(o, instant, priority)}" for o in orders})
                    self.events.put(("sync", (len(orders), services, end < until)))
                    delay = interval
                    if end < until:
                        continue
                except Exception as exc:
                    # No raw responses, credentials, or customer data are logged.
                    from core import ApiError
                    reason = str(exc) if isinstance(exc, ApiError) else "Kesalahan pemrosesan respons; perlu pemeriksaan format API."
                    self.events.put(("status", f"Koneksi/sinkronisasi gagal: {reason} Coba lagi dalam {delay} detik."))
                    if self.stop_event.wait(delay):
                        break
                    delay = min(delay * 2, 300)
                    continue
                if self.stop_event.wait(delay):
                    break
        finally:
            store.db.close()
            self.events.put(("stopped", None))

    def audio_loop(self):
        while True:
            text = self.audio.get()
            try:
                speak(text)
            except Exception:
                self.events.put(("status", "Suara gagal diputar. Periksa volume, perangkat audio, dan file rekaman; pop-up tetap tersedia."))

    def alert(self, row, reminder=False):
        text = ("Pengingat. " if reminder else "") + ("Ada pesanan instan prioritas baru. Silakan diproses." if row["kind"] == "PRIORITAS"
            else "Ada pesanan instan baru. Silakan diproses.")
        text += " Nomor pesanan " + row["number"][-6:] + "."
        if self.sound.get():
            self.audio.put(text)
        if self.popup_enabled.get():
            self.show_popup(text + "\n" + row["number"] + "\n" + row["service"])

    def show_popup(self, text):
        # Keep a single pop-up so a burst of orders cannot cover the desktop.
        if self.popup_window and self.popup_window.winfo_exists():
            self.popup_label.configure(text=text + "\n\nLihat daftar untuk pesanan lainnya.")
            return
        pop = tk.Toplevel(self.root)
        self.popup_window = pop
        pop.title("Pesanan instan — Ginee")
        pop.attributes("-topmost", True)
        pop.geometry("470x220")
        self.popup_label = ttk.Label(pop, text=text, wraplength=430, padding=18, font=("Segoe UI", 12))
        self.popup_label.pack(fill="both", expand=True)
        ttk.Button(pop, text="Tutup pemberitahuan", command=pop.destroy).pack(pady=12)

    def test_alert(self, kind="PRIORITAS"):
        self.alert({"kind": kind, "number": "CONTOH-123456", "service": "Tes suara dan pop-up, bukan pesanan nyata"})

    def copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log.get("1.0", "end-1c"))

    def pump(self):
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "status":
                    self.status.set(payload)
                elif event == "stopped":
                    self.running = False
                    self.unlock()
                    self.status.set("Pemantauan berhenti.")
                elif event == "sync":
                    count, services, catching_up = payload
                    now = datetime.now().strftime("%H:%M:%S")
                    self.status.set(f"{'Mengejar data tertunda' if catching_up else 'Terhubung'} • Sinkron terakhir {now} • {count} pesanan diperiksa")
                    self.log.configure(state="normal")
                    for item in services:
                        self.log.insert("end", now + " " + (item or "Layanan belum tersedia") + "\n")
                    lines = int(self.log.index("end-1c").split(".")[0])
                    if lines > 200:
                        self.log.delete("1.0", f"{lines - 199}.0")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                    for row in self.store.pending():
                        if not row["delivered"] and not row["acknowledged"] and self.running:
                            self.alert(row)
                            self.store.mark_delivered(row["id"])
                            self.next_reminder[row["id"]] = time.monotonic() + 60
        except queue.Empty:
            pass
        self.root.after(250, self.pump)

    def tick(self):
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        now = datetime.now(UTC)
        for row in self.store.rows():
            deadline = parse_time(row["deadline"])
            remaining = "—"
            if deadline:
                seconds = int((deadline - now).total_seconds())
                remaining = ("Lewat " if seconds < 0 else "") + f"{abs(seconds)//60:02}:{abs(seconds)%60:02}"
            self.tree.insert("", "end", iid=row["id"], values=(row["number"], row["shop"], row["service"],
                row["kind"], remaining if not row["acknowledged"] else "—", "Ditangani" if row["acknowledged"] else "Perlu ditangani"),
                tags=("priority",) if row["kind"] == "PRIORITAS" and not row["acknowledged"] else ())
            if (self.running and self.remind.get() and row["kind"] == "PRIORITAS" and not row["acknowledged"]
                and row["delivered"] and time.monotonic() >= self.next_reminder.setdefault(row["id"], time.monotonic() + 60)):
                self.alert(row, reminder=True)
                self.next_reminder[row["id"]] = time.monotonic() + 60
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        self.root.after(1000, self.tick)

    def acknowledge(self):
        selected = self.tree.selection()
        if selected:
            self.store.acknowledge(selected[0])

    def close(self):
        if self.running and not messagebox.askyesno("Tutup aplikasi?", "Pemantauan akan berhenti. Tutup aplikasi?\nPilih Tidak lalu minimalkan untuk tetap memantau."):
            return
        self.stop_event.set()
        self.root.destroy()


def runtime_self_test(report_file):
    """Check the built Windows executable without API credentials or audio output."""
    global DATA
    import ssl
    import tempfile
    result = {"platform": sys.platform, "frozen": bool(getattr(sys, "frozen", False)), "checks": {}}
    previous_data = DATA
    root = None
    view = None
    try:
        if sys.platform != "win32" or not result["frozen"]:
            raise RuntimeError("This check requires the packaged Windows executable.")
        sample = b"ginee-runtime-check"
        if protect(protect(sample), decrypt=True) != sample:
            raise RuntimeError("Windows DPAPI roundtrip failed.")
        result["checks"]["windows_dpapi"] = "passed"
        for name in ("instan.mp3", "prioritas.mp3"):
            if (ASSETS / "audio" / name).stat().st_size < 1000:
                raise RuntimeError("Bundled audio is incomplete: " + name)
        result["checks"]["bundled_audio_files"] = "passed; speaker playback not tested"
        ssl.create_default_context()
        result["checks"]["ssl_runtime"] = "passed"
        if not single_instance() or single_instance():
            raise RuntimeError("Windows single-instance mutex check failed.")
        result["checks"]["single_instance_mutex"] = "passed"
        with tempfile.TemporaryDirectory(prefix="ginee-runtime-") as folder:
            DATA = Path(folder)
            try:
                root = tk.Tk()
                root.withdraw()
                view = App(root)
                root.update_idletasks()
                view.store.ingest([], datetime.now(UTC), DEFAULT_INSTANT, DEFAULT_PRIORITY)
                result["checks"]["tkinter_and_sqlite"] = "passed"
            finally:
                if view is not None:
                    view.stop_event.set()
                    view.store.db.close()
                if root is not None:
                    root.destroy()
                    root = None
        result["ok"] = True
        return_code = 0
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)
        return_code = 1
    finally:
        DATA = previous_data
    if report_file:
        Path(report_file).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return return_code


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        report = None
        if "--self-test-report" in sys.argv:
            position = sys.argv.index("--self-test-report") + 1
            report = sys.argv[position] if position < len(sys.argv) else None
        sys.exit(runtime_self_test(report))
    root = tk.Tk()
    root.withdraw()
    if not single_instance():
        messagebox.showinfo("Aplikasi sudah berjalan", "Buka Pengumuman Pesanan Instan yang sudah ada di taskbar Windows.")
        root.destroy()
        sys.exit(0)
    ttk.Style().theme_use("clam")
    App(root)
    root.deiconify()
    root.mainloop()
