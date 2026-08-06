# DC34 Badge Image Uploader

Use the Python script in this repo to upload an image to your DC34 badge.

The image should be a black-and-white 128x128 PNG. However, you can attempt
to upload "any" image to your badge using the `--force` option and the script
will attempt to convert it into an compatible format.

## Prerequisites

You'll need **Python 3.9+** and **pipx**.

### Install pipx

**macOS**
```bash
brew install pipx
pipx ensurepath
```

**Windows**
```powershell
pip install pipx
python -m pipx ensurepath
```

**Linux**
```bash
pip install pipx
pipx ensurepath
```

After running `pipx ensurepath`, restart your terminal.

---

## Install

```bash
pipx install git+https://github.com/bunnie/dc34-image.git
```

or if cloned locally:

```bash
pipx install /path/to/your/local/repo
```

---

## Usage

Plug the badge into your computer and run the following script:

```bash
dc34-image --port /dev/ttyACM0 --image mylogo.png --force
```

| Flag | Description |
|---|---|
| `--port` | Serial port, e.g. `/dev/ttyACM0` (Linux), `/dev/tty.usbserial-*` (macOS), `COM3` (Windows) |
| `--image` | Path to your image file |
| `--force` | Auto-convert and resize any image to 128×128 B&W |
| `--clear` | Clears the current image from the device and exits (other arguments are ignored) |
| `--delay` | Delay between chunks in seconds (default: `0.2`) |

Don't know how to find your serial port? see the guide below.

---

## Updates

```bash
pipx install --force git+https://github.com/bunnie/dc34-image.git
```

---

# Finding Your Serial Port

The badge shows up as a serial device when plugged in via USB. Here's how to find the port name on various OSes.

---

## Linux

Plug in the badge, then run:

```bash
ls /dev/ttyACM*
```

It will most likely appear as `/dev/ttyACM0`. If you have multiple devices plugged in you might see `/dev/ttyACM1`, `/dev/ttyACM2`, etc. - try unplugging and replugging to see which one appears and disappears.

You can also get more detail with:

```bash
dmesg | tail -20
```

Look for a line mentioning `ttyACM0` or `cdc_acm` near the bottom.

### Permissions - avoiding sudo

On most Linux distros, serial ports are owned by the `dialout` group (sometimes `uucp`). By default your user may not be in this group, which means you'd need `sudo` to access the port. To fix this permanently:

```bash
sudo usermod -aG dialout $USER
```

Then **log out and log back in** (or reboot) for the change to take effect. You can verify it worked with:

```bash
groups
```

You should see `dialout` in the list. After that, `dc34-image` will work without `sudo`.

> **Note:** On some distros (e.g. Arch) the group is called `uucp` instead of `dialout`. If the `dialout` command has no effect, check the port's actual group with `ls -l /dev/ttyACM0` and substitute that group name in the `usermod` command above.

---

## macOS

macOS does not use `ttyACM` - the device will appear under a different name depending on the USB serial chip it uses. Plug in the badge, then run:

```bash
ls /dev/tty.usbmodem* /dev/tty.usbserial* 2>/dev/null
```

You should see something like `/dev/tty.usbmodem101` or `/dev/tty.usbserial-0001`. Use whichever appears.

If nothing shows up, try:

```bash
ls /dev/cu.*
```

And look for anything that wasn't there before you plugged in.

### Permissions

macOS does not require any special group membership - the port should be accessible to your user account without any extra steps.

---

## Windows

1. Plug in the badge.
2. Open **Device Manager** - press `Win + X` and select it from the menu, or search for it in the Start menu.
3. Expand the **Ports (COM & LPT)** section.
4. Look for an entry like **USB Serial Device (COM3)** - the `COM` number is what you need.

If nothing appears under Ports, try the **Other devices** section; you may need to install a driver. Check the badge documentation for the specific driver if so.

Use the port name exactly as shown, e.g.:

```bash
dc34-image --port COM3 --image mylogo.jpg
```

> **Tip:** If you're on Windows and using WSL, the port won't be directly available inside WSL. Either run `dc34-image` from a regular Windows terminal (PowerShell or CMD), or look into [usbipd-win](https://github.com/dorssel/usbipd-win) to forward USB devices into WSL.
