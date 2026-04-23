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
pipx ensurepath
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

---

## Usage

```bash
badge-sender --port /dev/ttyUSB0 --image myface.png --force
```

| Flag | Description |
|---|---|
| `--port` | Serial port, e.g. `/dev/ttyUSB0` (Linux), `/dev/tty.usbserial-*` (macOS), `COM3` (Windows) |
| `--image` | Path to your image file |
| `--force` | Auto-convert and resize any image to 128×128 B&W |
| `--clear` | Clear the current image from the device |
| `--delay` | Delay between chunks in seconds (default: `0.2`) |

---

## Updates

```bash
pipx install --force git+https://github.com/bunnie/dc34-image.git
```