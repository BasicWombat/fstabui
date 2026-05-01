# fstabUI

A graphical `/etc/fstab` editor for Linux, built with GTK3 and Python.

Manage your mount entries without touching a text editor — add, edit, remove, and reorder entries through a clean table interface, with privilege-aware saving via PolicyKit or sudo.

## Features

- **Table view** of all fstab entries with device, mount point, filesystem type, options, dump, and pass columns
- **Add / edit / remove** mount entries via a dialog with:
  - Device or UUID field
  - Mount point field
  - Filesystem type combo (ext4, xfs, btrfs, vfat, ntfs, nfs, cifs, swap, tmpfs, and more)
  - Options editor — checkbox UI with tooltips **or** a manual text field, switchable on the fly
  - Dump and Pass spinners
- **Network Share Wizard** — guided step-by-step setup for:
  - **SMB / CIFS** (Windows / Samba shares) with guest, inline credentials, or credentials-file authentication
  - **NFS** (Unix / Linux network filesystems)
  - Sensible default mount options pre-selected (`_netdev`, `nofail`)
- **Comment editing** — add and edit `# comment` lines without breaking them
- **Show / hide comments** toggle in toolbar and View menu
- **Move entries up / down** to reorder the file
- **View Raw** — read-only preview of the exact text that will be written
- **Privilege-aware saving** — escalates via `pkexec` (PolicyKit GUI prompt) or `sudo`; falls back to a temp file with manual-copy instructions if both fail
- **Automatic backup** — writes `fstab.bak` alongside `fstab` before every save
- **Unsaved-changes guard** — warns before closing with pending edits

## Requirements

- Python 3.10+
- GTK 3 + PyGObject

Install PyGObject for your distribution:

| Distro | Command |
|--------|---------|
| Ubuntu / Debian | `sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0` |
| Fedora / RHEL | `sudo dnf install python3-gobject gtk3` |
| Arch Linux | `sudo pacman -S python-gobject gtk3` |

For SMB / CIFS shares you also need `cifs-utils`; for NFS you need `nfs-common` (Debian/Ubuntu) or `nfs-utils` (Fedora/Arch).

## Installation

No packaging step is required — the tool is a single self-contained script.

```bash
git clone https://github.com/BasicWombat/fstabui.git
cd fstabui
chmod +x fstab-admin.py
```

## Usage

```bash
python3 fstab-admin.py
```

Or run directly if the executable bit is set:

```bash
./fstab-admin.py
```

The application reads `/etc/fstab` on launch. Saving writes back to `/etc/fstab` and requires elevated privileges; a PolicyKit authentication dialog appears automatically.

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+S` | Save |
| `Ctrl+R` | Reload from disk |
| `Return` | Edit selected entry |
| `Delete` | Remove selected entry |
| `Alt+Up` | Move entry up |
| `Alt+Down` | Move entry down |
| `F1` | About |

## Supported Filesystem Types

`ext4` `ext3` `ext2` `xfs` `btrfs` `f2fs` `vfat` `exfat` `ntfs` `ntfs-3g` `swap` `tmpfs` `proc` `sysfs` `devpts` `cgroup2` `cifs` `nfs` `nfs4` `iso9660` `udf` `auto`

## How Saving Works

1. The new fstab content is written to a temporary file in `/tmp`.
2. The existing `/etc/fstab` is copied to `/etc/fstab.bak`.
3. The temp file is copied over `/etc/fstab` using `pkexec` (preferred) or `sudo`.
4. If both escalation methods fail, the temp file is left in place and you are shown the path along with the manual `sudo cp` command needed to apply it.

## License

MIT

---

*This README was generated with [Claude Code](https://claude.ai/code).*
