# AiMenu print bridge · ბეჭდვის ხიდი

Prints kitchen tickets, receipts and shift reports on **any cheap ESC/POS
thermal printer** (Xprinter, Goojprt, Rongta, Epson TM clones — 58 mm or
80 mm; USB, LAN/Wi-Fi or Bluetooth). Georgian text works on every printer
because the server renders each document as an image.

ბეჭდავს სამზარეულოს ბილეთებს, ჩეკებს და ცვლის ანგარიშებს ნებისმიერ იაფ
ESC/POS თერმულ პრინტერზე. ქართული ტექსტი ყველა პრინტერზე იბეჭდება, რადგან
სერვერი დოკუმენტს სურათად აგზავნის.

## 1. Add the printer in the dashboard · პრინტერის დამატება

Dashboard → **Printers** → *Add printer*: name, kind (kitchen / bar / receipt),
paper width, connection = *Print bridge*. Save; the page shows the **bridge key**
and a ready-made `bridge.toml`.

## 2. Install on the computer next to the printer · დაყენება

Any machine that stays on: a Windows PC, an old laptop, a Raspberry Pi.

```bash
python3 -m venv venv && . venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp bridge.example.toml bridge.toml               # paste the key, set the printer
python aimenu_print_bridge.py --list-usb         # shows usb://VID:PID for USB printers
python aimenu_print_bridge.py --self-test        # prints a plain page straight to the device
python aimenu_print_bridge.py                    # runs the bridge
```

Printer addresses (`printer =` in `bridge.toml`):

| Connection | Value |
|---|---|
| USB | `usb://` (first printer) or `usb://0x04b8:0x0202` |
| LAN / Wi-Fi | `net://192.168.1.50:9100` |
| Bluetooth / serial | `serial:///dev/rfcomm0:9600` (Windows `serial://COM3:9600`) |
| Windows-installed printer | `win://POS-80` (the name from *Printers & scanners*) |
| Testing | `file:///tmp/tickets.bin` |

*USB on Linux:* add a udev rule or run as root. *USB on Windows:* install the
WinUSB driver for the printer with Zadig, or install the vendor driver and use
`win://`. *Bluetooth on Linux:* `rfcomm bind 0 <printer MAC>`.

## 3. Keep it running · ავტომატური გაშვება

**Linux / Raspberry Pi (systemd):** copy the folder to `/opt/aimenu-print-bridge`,
then `sudo cp aimenu-print-bridge.service /etc/systemd/system/ && sudo systemctl enable --now aimenu-print-bridge`.

**Windows:** Task Scheduler → *Create task* → trigger *At log on* → action
`venv\Scripts\python.exe aimenu_print_bridge.py` with *Start in* = the folder.

The dashboard shows the printer as **online** while the bridge polls. When the
bridge is down, jobs wait in the queue and print as soon as it is back;
a job that fails three times is marked *failed* and can be retried from
Dashboard → Print jobs or from the POS.
