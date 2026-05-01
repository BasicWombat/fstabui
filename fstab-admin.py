#!/usr/bin/env python3
"""fstabUI — Graphical fstab administration tool for Linux (GTK3)"""

import os
import shutil
import subprocess
import tempfile
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

FSTAB_PATH = "/etc/fstab"
APP_TITLE  = "fstabUI"

COMMON_FS_TYPES = [
    "ext4", "ext3", "ext2", "xfs", "btrfs", "f2fs",
    "vfat", "exfat", "ntfs", "ntfs-3g",
    "swap", "tmpfs", "proc", "sysfs", "devpts", "cgroup2",
    "cifs", "nfs", "nfs4",
    "iso9660", "udf", "auto",
]

COMMON_MOUNT_OPTIONS = [
    ("defaults",    "Use defaults (rw, suid, dev, exec, auto, nouser, async)"),
    ("ro",          "Mount read-only"),
    ("rw",          "Mount read-write (explicit)"),
    ("noatime",     "Do not update file access times"),
    ("nodiratime",  "Do not update directory access times"),
    ("relatime",    "Update access time relative to modify time"),
    ("noexec",      "Prevent execution of binaries"),
    ("exec",        "Allow execution of binaries (explicit)"),
    ("nosuid",      "Ignore set-user-ID / set-group-ID bits"),
    ("nodev",       "Do not interpret block/character special devices"),
    ("user",        "Allow non-root users to mount this filesystem"),
    ("users",       "Allow any user to mount and unmount"),
    ("noauto",      "Do not mount automatically at boot"),
    ("nofail",      "Do not report errors if device is absent at boot"),
    ("_netdev",     "Requires network — delays mount until network is up"),
    ("async",       "Asynchronous I/O (default)"),
    ("sync",        "Synchronous I/O"),
    ("discard",     "Enable TRIM/discard for SSDs"),
    ("x-gvfs-show", "Show in GNOME Files and other GVFS file managers"),
]

CSS = b"""
treeview.view row.comment-row { color: #888; font-style: italic; }
treeview.view row.special-row { background-color: #f7f7f7; color: #555; }
treeview.view row:selected     { background-color: #3584e4; color: white; }
.modified-label { color: #c01c28; font-weight: bold; }
.toolbar button { margin: 2px 3px; }
"""

# ─── Data model ───────────────────────────────────────────────────────────────

class Entry:
    MOUNT   = "mount"
    COMMENT = "comment"
    BLANK   = "blank"

    def __init__(self, kind=None, device="", mount="", fstype="",
                 options="defaults", dump="0", passno="0", raw=""):
        self.kind    = kind or Entry.MOUNT
        self.device  = device
        self.mount   = mount
        self.fstype  = fstype
        self.options = options
        self.dump    = dump
        self.passno  = passno
        self.raw     = raw          # verbatim line for COMMENT / BLANK

    def to_line(self):
        if self.kind != Entry.MOUNT:
            return self.raw
        return (f"{self.device}\t{self.mount}\t{self.fstype}\t"
                f"{self.options}\t{self.dump}\t{self.passno}")

    @staticmethod
    def parse(line):
        s = line.rstrip("\n")
        stripped = s.strip()
        if not stripped:
            return Entry(kind=Entry.BLANK, raw=s)
        if stripped.startswith("#"):
            return Entry(kind=Entry.COMMENT, raw=s)
        parts = stripped.split()
        if len(parts) < 3:
            return Entry(kind=Entry.COMMENT, raw=s)   # malformed → treat as comment
        return Entry(
            kind    = Entry.MOUNT,
            device  = parts[0],
            mount   = parts[1],
            fstype  = parts[2],
            options = parts[3] if len(parts) > 3 else "defaults",
            dump    = parts[4] if len(parts) > 4 else "0",
            passno  = parts[5] if len(parts) > 5 else "0",
        )


# ─── Privilege-aware writer ───────────────────────────────────────────────────

def write_fstab(content: str, path: str = FSTAB_PATH):
    """
    Write *content* to *path*.
    Returns (success: bool, detail: str | None).
    On failure, detail holds the path to a saved temp file.
    """
    bak = path + ".bak"

    # Write to temp file first (always succeeds for current user)
    fd, tmp = tempfile.mkstemp(prefix="fstab_", suffix=".tmp", dir="/tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
    except Exception as e:
        return False, str(e)

    # Direct write if we already own the file (running as root)
    if os.access(path, os.W_OK):
        try:
            shutil.copy2(path, bak)
            shutil.copy2(tmp, path)
            os.unlink(tmp)
            return True, None
        except Exception as e:
            return False, str(e)

    # Escalate via pkexec (PolicyKit — shows a GUI authentication dialog)
    shell_cmd = f'cp "{path}" "{bak}" 2>/dev/null; cp "{tmp}" "{path}"'
    for escalate in (["pkexec", "sh", "-c"], ["sudo", "sh", "-c"]):
        try:
            r = subprocess.run(escalate + [shell_cmd], capture_output=True)
            if r.returncode == 0:
                os.unlink(tmp)
                return True, None
        except FileNotFoundError:
            continue

    # All methods failed — leave the temp file for a manual copy
    return False, tmp


# ─── Add / Edit entry dialog ──────────────────────────────────────────────────

class EntryDialog(Gtk.Dialog):
    def __init__(self, parent, entry: Entry = None):
        title = "Edit Mount Entry" if entry else "Add Mount Entry"
        super().__init__(title=title, transient_for=parent, modal=True)
        self.set_default_size(480, -1)
        self.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK,     Gtk.ResponseType.OK,
        )
        self.set_default_response(Gtk.ResponseType.OK)
        self.result: Entry | None = None

        e = entry or Entry()
        grid = Gtk.Grid(column_spacing=12, row_spacing=8,
                        margin_top=12, margin_bottom=4,
                        margin_start=16, margin_end=16)

        def lbl(text):
            l = Gtk.Label(label=text, xalign=1.0)
            l.set_width_chars(16)
            return l

        # Device
        self._device = Gtk.Entry(text=e.device, hexpand=True)
        self._device.set_placeholder_text("/dev/sdXN  or  UUID=…")
        grid.attach(lbl("Device / UUID:"), 0, 0, 1, 1)
        grid.attach(self._device,          1, 0, 2, 1)

        # Mount point
        self._mount = Gtk.Entry(text=e.mount, hexpand=True)
        self._mount.set_placeholder_text("/mnt/data")
        grid.attach(lbl("Mount Point:"), 0, 1, 1, 1)
        grid.attach(self._mount,         1, 1, 2, 1)

        # Filesystem type
        fs_store = Gtk.ListStore(str)
        for fs in COMMON_FS_TYPES:
            fs_store.append([fs])
        self._fstype = Gtk.ComboBoxText.new_with_entry()
        for fs in COMMON_FS_TYPES:
            self._fstype.append_text(fs)
        self._fstype.get_child().set_text(e.fstype)
        self._fstype.set_hexpand(True)
        grid.attach(lbl("Filesystem Type:"), 0, 2, 1, 1)
        grid.attach(self._fstype,            1, 2, 2, 1)

        # Options
        grid.attach(lbl("Options:"),                      0, 3, 1, 1)
        grid.attach(self._build_options_widget(e.options), 1, 3, 2, 1)

        # Dump + Pass on same row
        self._dump = Gtk.SpinButton.new_with_range(0, 1, 1)
        self._dump.set_value(int(e.dump) if e.dump.isdigit() else 0)
        self._dump.set_tooltip_text("0 = do not dump; 1 = include in dump")
        dump_box = Gtk.Box(spacing=4)
        dump_box.pack_start(Gtk.Label(label="Dump:"), False, False, 0)
        dump_box.pack_start(self._dump, False, False, 0)

        self._passno = Gtk.SpinButton.new_with_range(0, 2, 1)
        self._passno.set_value(int(e.passno) if e.passno.isdigit() else 0)
        self._passno.set_tooltip_text(
            "0 = skip fsck; 1 = check first (root); 2 = check after root")
        pass_box = Gtk.Box(spacing=4)
        pass_box.pack_start(Gtk.Label(label="Pass:"), False, False, 0)
        pass_box.pack_start(self._passno, False, False, 0)

        dp_box = Gtk.Box(spacing=24)
        dp_box.pack_start(dump_box, False, False, 0)
        dp_box.pack_start(pass_box, False, False, 0)
        grid.attach(lbl(""), 0, 4, 1, 1)
        grid.attach(dp_box, 1, 4, 2, 1)

        self.get_content_area().pack_start(grid, True, True, 0)
        self.show_all()

        self._device.connect("activate", lambda _: self._ok())
        self.connect("response", self._on_response)

    def _ok(self):
        device = self._device.get_text().strip()
        mount  = self._mount.get_text().strip()
        fstype = self._fstype.get_child().get_text().strip()

        if not device:
            self._show_err("Device / UUID is required."); return
        if not mount:
            self._show_err("Mount point is required."); return
        if not fstype:
            self._show_err("Filesystem type is required."); return

        self.result = Entry(
            kind    = Entry.MOUNT,
            device  = device,
            mount   = mount,
            fstype  = fstype,
            options = self._get_options() or "defaults",
            dump    = str(int(self._dump.get_value())),
            passno  = str(int(self._passno.get_value())),
        )

    # ── Options widget ────────────────────────────────────────────────────────

    def _build_options_widget(self, initial: str) -> Gtk.Widget:
        parts = [p.strip() for p in initial.split(",") if p.strip()]
        known = {opt for opt, _ in COMMON_MOUNT_OPTIONS}
        extra_parts = [p for p in parts if p not in known]

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_hexpand(True)

        # Toggle button
        self._opts_mode_btn = Gtk.Button(label="Switch to Manual")
        self._opts_mode_btn.set_relief(Gtk.ReliefStyle.NONE)
        self._opts_mode_btn.set_halign(Gtk.Align.END)
        self._opts_mode_btn.connect("clicked", self._toggle_options_mode)
        outer.pack_start(self._opts_mode_btn, False, False, 0)

        self._opts_stack = Gtk.Stack()
        self._opts_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._opts_stack.set_transition_duration(120)

        # ── Checkbox page ──
        cb_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        cb_grid = Gtk.Grid(column_spacing=4, row_spacing=2)
        cb_grid.set_hexpand(True)
        self._option_checks = {}
        for i, (opt, tip) in enumerate(COMMON_MOUNT_OPTIONS):
            cb = Gtk.CheckButton(label=opt)
            cb.set_tooltip_text(tip)
            cb.set_active(opt in parts)
            cb.set_hexpand(True)
            self._option_checks[opt] = cb
            cb_grid.attach(cb, i % 3, i // 3, 1, 1)
        cb_box.pack_start(cb_grid, False, False, 0)

        extra_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        extra_row.pack_start(
            Gtk.Label(label="Additional:", xalign=1.0), False, False, 0)
        self._extra_opts = Gtk.Entry(hexpand=True)
        self._extra_opts.set_placeholder_text("uid=1000,gid=1000,umask=022, …")
        self._extra_opts.set_text(",".join(extra_parts))
        extra_row.pack_start(self._extra_opts, True, True, 0)
        cb_box.pack_start(extra_row, False, False, 0)

        self._opts_stack.add_named(cb_box, "checkboxes")

        # ── Manual page ──
        self._opts_manual = Gtk.Entry(hexpand=True)
        self._opts_manual.set_placeholder_text("defaults,noatime")
        self._opts_manual.set_text(initial)
        self._opts_stack.add_named(self._opts_manual, "manual")

        self._opts_stack.set_visible_child_name("checkboxes")
        outer.pack_start(self._opts_stack, False, False, 0)
        return outer

    def _toggle_options_mode(self, _btn):
        if self._opts_stack.get_visible_child_name() == "checkboxes":
            self._opts_manual.set_text(self._get_options())
            self._opts_stack.set_visible_child_name("manual")
            self._opts_mode_btn.set_label("Switch to Checkboxes")
        else:
            self._sync_checkboxes_from_text(self._opts_manual.get_text())
            self._opts_stack.set_visible_child_name("checkboxes")
            self._opts_mode_btn.set_label("Switch to Manual")

    def _sync_checkboxes_from_text(self, text: str):
        parts = [p.strip() for p in text.split(",") if p.strip()]
        known = {opt for opt, _ in COMMON_MOUNT_OPTIONS}
        for opt, cb in self._option_checks.items():
            cb.set_active(opt in parts)
        self._extra_opts.set_text(",".join(p for p in parts if p not in known))

    def _get_options(self) -> str:
        if self._opts_stack.get_visible_child_name() == "checkboxes":
            checked = [opt for opt, cb in self._option_checks.items()
                       if cb.get_active()]
            extra = [p.strip() for p in self._extra_opts.get_text().split(",")
                     if p.strip()]
            return ",".join(checked + extra)
        return self._opts_manual.get_text().strip()

    # ─────────────────────────────────────────────────────────────────────────

    def _show_err(self, msg):
        d = Gtk.MessageDialog(transient_for=self, modal=True,
                              message_type=Gtk.MessageType.WARNING,
                              buttons=Gtk.ButtonsType.OK, text=msg)
        d.run(); d.destroy()

    def _on_response(self, _dlg, resp):
        if resp == Gtk.ResponseType.OK:
            self._ok()


# ─── Comment dialog ────────────────────────────────────────────────────────────

class CommentDialog(Gtk.Dialog):
    def __init__(self, parent, text: str = ""):
        super().__init__(title="Edit Comment", transient_for=parent, modal=True)
        self.set_default_size(420, -1)
        self.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK,     Gtk.ResponseType.OK,
        )
        self.set_default_response(Gtk.ResponseType.OK)
        self.result: str | None = None

        box = self.get_content_area()
        box.set_spacing(8)
        box.set_margin_top(12)
        box.set_margin_bottom(4)
        box.set_margin_start(16)
        box.set_margin_end(16)

        box.pack_start(
            Gtk.Label(label="Comment text  (# added automatically):", xalign=0),
            False, False, 0)

        clean = text.lstrip("#").strip()
        self._entry = Gtk.Entry(text=clean, hexpand=True, width_chars=50)
        self._entry.connect("activate", lambda _: self.response(Gtk.ResponseType.OK))
        box.pack_start(self._entry, False, False, 0)
        self.show_all()

        self.connect("response", self._on_response)

    def _on_response(self, _dlg, resp):
        if resp == Gtk.ResponseType.OK:
            t = self._entry.get_text().strip()
            if t and not t.startswith("#"):
                t = "# " + t
            self.result = t


# ─── Raw fstab viewer ─────────────────────────────────────────────────────────

class RawViewDialog(Gtk.Dialog):
    def __init__(self, parent, content: str):
        super().__init__(title="Raw fstab Content", transient_for=parent, modal=True)
        self.set_default_size(720, 420)
        self.add_button(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE)

        sw = Gtk.ScrolledWindow()
        tv = Gtk.TextView(editable=False, monospace=True,
                          left_margin=8, right_margin=8,
                          top_margin=6, bottom_margin=6)
        tv.get_buffer().set_text(content)
        sw.add(tv)

        self.get_content_area().pack_start(sw, True, True, 0)
        self.show_all()
        self.run()
        self.destroy()


# ─── Network Share Wizard ──────────────────────────────────────────────────────

_PAGE_INTRO       = 0
_PAGE_TYPE        = 1
_PAGE_LOCATION    = 2
_PAGE_MOUNTPOINT  = 3
_PAGE_CREDENTIALS = 4
_PAGE_OPTIONS     = 5
_PAGE_REVIEW      = 6

_SMB_OPTIONS = [
    ("_netdev",        "Wait for network before mounting"),
    ("nofail",         "Don't fail boot if share is unavailable"),
    ("noauto",         "Don't mount automatically at boot"),
    ("x-gvfs-show",    "Show in GNOME Files / file managers"),
    ("rw",             "Read-write (default)"),
    ("ro",             "Read-only"),
    ("file_mode=0644", "Default permissions for files"),
    ("dir_mode=0755",  "Default permissions for directories"),
    ("vers=3.0",       "Force SMB protocol version 3.0"),
    ("vers=2.0",       "Force SMB protocol version 2.0"),
    ("sec=ntlmssp",    "Use NTLM SSP authentication"),
    ("iocharset=utf8", "UTF-8 character encoding"),
    ("nobrl",          "Disable byte-range locks (helps some apps)"),
    ("cache=loose",    "Loose caching for better read performance"),
]

_NFS_OPTIONS = [
    ("_netdev",   "Wait for network before mounting"),
    ("nofail",    "Don't fail boot if server is unavailable"),
    ("noauto",    "Don't mount automatically at boot"),
    ("x-gvfs-show", "Show in GNOME Files / file managers"),
    ("rw",        "Read-write (default)"),
    ("ro",        "Read-only"),
    ("hard",      "Retry indefinitely on server failure (recommended)"),
    ("soft",      "Return error after timeout (may lose data)"),
    ("intr",      "Allow interrupting hung NFS operations"),
    ("nolock",    "Disable NFS file locking"),
    ("vers=4",    "Use NFS version 4"),
    ("vers=3",    "Use NFS version 3"),
    ("rsize=8192","Read block size (bytes)"),
    ("wsize=8192","Write block size (bytes)"),
]


class NetworkShareWizard(Gtk.Assistant):
    def __init__(self, parent, on_complete):
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_title("Network Share Wizard")
        self.set_default_size(640, 540)

        self._on_complete    = on_complete
        self._option_checks: dict = {}

        self._build_pages()
        self.set_forward_page_func(self._forward_page, None)
        self.connect("prepare", self._on_prepare)
        self.connect("apply",   self._on_apply)
        self.connect("cancel",  lambda _: self.destroy())
        self.connect("close",   lambda _: self.destroy())

    # ── Navigation ────────────────────────────────────────────────────────────

    def _forward_page(self, current, _data):
        if current == _PAGE_MOUNTPOINT and self._get_share_type() == "nfs":
            return _PAGE_OPTIONS   # NFS has no credentials step
        return current + 1

    def _get_share_type(self):
        return "smb" if self._smb_radio.get_active() else "nfs"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _pbox(self):
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                    margin_top=16, margin_bottom=16,
                    margin_start=24, margin_end=24)
        return b

    def _add_page(self, widget, page_type, title, complete=False):
        self.append_page(widget)
        self.set_page_type(widget, page_type)
        self.set_page_title(widget, title)
        self.set_page_complete(widget, complete)
        return widget

    # ── Page builders ─────────────────────────────────────────────────────────

    def _build_pages(self):
        self._build_intro_page()
        self._build_type_page()
        self._build_location_page()
        self._build_mountpoint_page()
        self._build_credentials_page()
        self._build_options_page()
        self._build_review_page()

    def _build_intro_page(self):
        box = self._pbox()
        lbl = Gtk.Label(xalign=0)
        lbl.set_line_wrap(True)
        lbl.set_markup(
            "This wizard helps you add a <b>network share</b> entry to "
            "<tt>/etc/fstab</tt>.\n\n"
            "<b>Supported share types:</b>\n"
            "  •  <b>SMB / CIFS</b> — Windows shares and Samba servers\n"
            "  •  <b>NFS</b> — Unix / Linux network filesystems\n\n"
            "<b>Steps:</b>\n"
            "  1.  Choose share type\n"
            "  2.  Enter server address and share location\n"
            "  3.  Set a local mount point\n"
            "  4.  Configure credentials  <i>(SMB only)</i>\n"
            "  5.  Choose mount options\n"
            "  6.  Review and apply\n\n"
            "Click <b>Forward</b> to begin."
        )
        box.pack_start(lbl, False, False, 0)
        self._add_page(box, Gtk.AssistantPageType.INTRO, "Welcome", complete=True)

    def _build_type_page(self):
        box = self._pbox()
        box.pack_start(
            Gtk.Label(label="Select the type of network share to add:", xalign=0),
            False, False, 0)

        self._smb_radio = Gtk.RadioButton.new_with_label(
            None, "SMB / CIFS  —  Windows / Samba shares")
        self._nfs_radio = Gtk.RadioButton.new_with_label_from_widget(
            self._smb_radio, "NFS  —  Unix / Linux network filesystem")
        self._smb_radio.set_tooltip_text(
            "Uses the cifs kernel module.  Device format: //server/share")
        self._nfs_radio.set_tooltip_text(
            "Uses the nfs kernel module.  Device format: server:/export/path")

        box.pack_start(self._smb_radio, False, False, 6)
        box.pack_start(self._nfs_radio, False, False, 6)

        self._type_hint = Gtk.Label(xalign=0)
        self._type_hint.set_line_wrap(True)
        box.pack_start(self._type_hint, False, False, 8)

        self._smb_radio.connect("toggled", self._on_type_toggled)
        self._on_type_toggled(None)

        self._add_page(box, Gtk.AssistantPageType.CONTENT, "Share Type", complete=True)

    def _on_type_toggled(self, _):
        if self._smb_radio.get_active():
            self._type_hint.set_markup(
                "<i>Filesystem type:</i>   <tt>cifs</tt>\n"
                "<i>Device format:</i>     <tt>//hostname/sharename</tt>\n"
                "<i>Requires package:</i>  <tt>cifs-utils</tt>")
        else:
            self._type_hint.set_markup(
                "<i>Filesystem type:</i>   <tt>nfs</tt>\n"
                "<i>Device format:</i>     <tt>hostname:/export/path</tt>\n"
                "<i>Requires package:</i>  <tt>nfs-common</tt>")

    def _build_location_page(self):
        box = self._pbox()
        grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        grid.set_hexpand(True)

        def lbl(t):
            l = Gtk.Label(label=t, xalign=1.0)
            l.set_width_chars(14)
            return l

        self._loc_host = Gtk.Entry(hexpand=True)
        self._loc_host.set_placeholder_text("192.168.1.100  or  fileserver.local")
        grid.attach(lbl("Server / Host:"), 0, 0, 1, 1)
        grid.attach(self._loc_host,         1, 0, 1, 1)

        self._smb_share_lbl = lbl("Share Name:")
        self._loc_smb_share = Gtk.Entry(hexpand=True)
        self._loc_smb_share.set_placeholder_text("shared  or  Documents")
        grid.attach(self._smb_share_lbl, 0, 1, 1, 1)
        grid.attach(self._loc_smb_share, 1, 1, 1, 1)

        self._nfs_path_lbl = lbl("Export Path:")
        self._loc_nfs_path = Gtk.Entry(hexpand=True)
        self._loc_nfs_path.set_placeholder_text("/srv/nfs/data  or  /export/home")
        grid.attach(self._nfs_path_lbl, 0, 2, 1, 1)
        grid.attach(self._loc_nfs_path, 1, 2, 1, 1)

        box.pack_start(grid, False, False, 0)
        box.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

        preview_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        preview_row.pack_start(Gtk.Label(label="Device:"), False, False, 0)
        self._loc_preview = Gtk.Label(label="—", xalign=0)
        preview_row.pack_start(self._loc_preview, True, True, 0)
        box.pack_start(preview_row, False, False, 0)

        self._loc_page = self._add_page(
            box, Gtk.AssistantPageType.CONTENT, "Share Location")

        for w in (self._loc_host, self._loc_smb_share, self._loc_nfs_path):
            w.connect("changed", self._on_location_changed)
        self._smb_radio.connect("toggled", self._on_location_changed)
        self._on_location_changed(None)

    def _on_location_changed(self, _):
        is_smb = self._get_share_type() == "smb"
        self._smb_share_lbl.set_visible(is_smb)
        self._loc_smb_share.set_visible(is_smb)
        self._nfs_path_lbl.set_visible(not is_smb)
        self._loc_nfs_path.set_visible(not is_smb)

        host = self._loc_host.get_text().strip()
        if is_smb:
            share  = self._loc_smb_share.get_text().strip()
            device = f"//{host}/{share}" if host and share else ""
            ok     = bool(host and share)
        else:
            path   = self._loc_nfs_path.get_text().strip()
            device = f"{host}:{path}" if host and path else ""
            ok     = bool(host and path)

        self._loc_preview.set_markup(
            f"<tt>{GLib.markup_escape_text(device)}</tt>" if device else "—")
        self.set_page_complete(self._loc_page, ok)

    def _build_mountpoint_page(self):
        box = self._pbox()
        grid = Gtk.Grid(column_spacing=12, row_spacing=10)

        lbl = Gtk.Label(label="Mount Point:", xalign=1.0)
        lbl.set_width_chars(14)
        self._mountpoint = Gtk.Entry(hexpand=True)
        self._mountpoint.set_placeholder_text("/mnt/myshare")
        grid.attach(lbl,              0, 0, 1, 1)
        grid.attach(self._mountpoint, 1, 0, 1, 1)
        box.pack_start(grid, False, False, 0)

        hint = Gtk.Label(xalign=0)
        hint.set_line_wrap(True)
        hint.set_markup(
            "\n<i>The directory must exist before the share can be mounted.</i>\n"
            "Create it with:  <tt>sudo mkdir -p /mnt/myshare</tt>"
        )
        box.pack_start(hint, False, False, 0)

        self._mp_page = self._add_page(
            box, Gtk.AssistantPageType.CONTENT, "Mount Point")
        self._mountpoint.connect("changed", self._on_mountpoint_changed)

    def _on_mountpoint_changed(self, _):
        v = self._mountpoint.get_text().strip()
        self.set_page_complete(self._mp_page, bool(v and v.startswith("/")))

    def _build_credentials_page(self):
        box = self._pbox()

        note = Gtk.Label(xalign=0)
        note.set_line_wrap(True)
        note.set_markup(
            "<i>Authentication for SMB / CIFS shares.  "
            "NFS uses host-based access control and skips this step.</i>")
        box.pack_start(note, False, False, 0)
        box.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

        self._cred_guest  = Gtk.RadioButton.new_with_label(
            None, "Guest / anonymous  (no credentials required)")
        self._cred_inline = Gtk.RadioButton.new_with_label_from_widget(
            self._cred_guest, "Username and password  (stored in fstab)")
        self._cred_file   = Gtk.RadioButton.new_with_label_from_widget(
            self._cred_guest, "Credentials file  (recommended)")

        for rb in (self._cred_guest, self._cred_inline, self._cred_file):
            box.pack_start(rb, False, False, 2)
            rb.connect("toggled", self._on_cred_toggled)

        # Inline credentials
        self._inline_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                   spacing=6, margin_start=28)
        ig = Gtk.Grid(column_spacing=12, row_spacing=8)

        def ilbl(t):
            l = Gtk.Label(label=t, xalign=1.0)
            l.set_width_chars(10)
            return l

        self._cred_user   = Gtk.Entry(hexpand=True)
        self._cred_user.set_placeholder_text("username")
        self._cred_pass   = Gtk.Entry(hexpand=True)
        self._cred_pass.set_visibility(False)
        self._cred_pass.set_placeholder_text("password")
        self._cred_domain = Gtk.Entry(hexpand=True)
        self._cred_domain.set_placeholder_text("WORKGROUP  (optional)")

        ig.attach(ilbl("Username:"), 0, 0, 1, 1); ig.attach(self._cred_user,   1, 0, 1, 1)
        ig.attach(ilbl("Password:"), 0, 1, 1, 1); ig.attach(self._cred_pass,   1, 1, 1, 1)
        ig.attach(ilbl("Domain:"),   0, 2, 1, 1); ig.attach(self._cred_domain, 1, 2, 1, 1)
        self._inline_box.pack_start(ig, False, False, 0)

        warn = Gtk.Label(xalign=0)
        warn.set_line_wrap(True)
        warn.set_markup(
            "<span foreground='#c01c28'>"
            "⚠  Passwords stored in fstab are readable by all users.\n"
            "   A credentials file is strongly recommended.</span>")
        self._inline_box.pack_start(warn, False, False, 4)
        box.pack_start(self._inline_box, False, False, 4)

        # Credentials file
        self._file_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                 spacing=6, margin_start=28)
        fg = Gtk.Grid(column_spacing=12, row_spacing=8)
        fp_lbl = Gtk.Label(label="File path:", xalign=1.0)
        fp_lbl.set_width_chars(10)
        self._cred_filepath = Gtk.Entry(hexpand=True)
        self._cred_filepath.set_placeholder_text("/etc/samba/credentials")
        fg.attach(fp_lbl,              0, 0, 1, 1)
        fg.attach(self._cred_filepath, 1, 0, 1, 1)
        self._file_box.pack_start(fg, False, False, 0)

        fhint = Gtk.Label(xalign=0)
        fhint.set_line_wrap(True)
        fhint.set_markup(
            "<i>File format:\n"
            "<tt>username=myuser\npassword=mypassword\ndomain=WORKGROUP</tt>\n\n"
            "Secure it after creation:\n"
            "<tt>sudo chown root:root /etc/samba/credentials\n"
            "sudo chmod 600 /etc/samba/credentials</tt></i>"
        )
        self._file_box.pack_start(fhint, False, False, 4)
        box.pack_start(self._file_box, False, False, 4)

        self._cred_page = self._add_page(
            box, Gtk.AssistantPageType.CONTENT, "Credentials", complete=True)
        self._on_cred_toggled(None)

    def _on_cred_toggled(self, _):
        self._inline_box.set_visible(self._cred_inline.get_active())
        self._file_box.set_visible(self._cred_file.get_active())

    def _build_options_page(self):
        box = self._pbox()
        box.pack_start(
            Gtk.Label(label="Select mount options  (hover for descriptions):", xalign=0),
            False, False, 0)

        self._opts_grid = Gtk.Grid(column_spacing=4, row_spacing=2)
        self._opts_grid.set_hexpand(True)
        box.pack_start(self._opts_grid, False, False, 0)

        box.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

        extra_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        extra_lbl = Gtk.Label(label="Additional:", xalign=1.0)
        extra_lbl.set_width_chars(14)
        extra_row.pack_start(extra_lbl, False, False, 0)
        self._opts_extra = Gtk.Entry(hexpand=True)
        self._opts_extra.set_placeholder_text("uid=1000,gid=1000, …")
        extra_row.pack_start(self._opts_extra, True, True, 0)
        box.pack_start(extra_row, False, False, 0)

        self._opts_page = self._add_page(
            box, Gtk.AssistantPageType.CONTENT, "Options", complete=True)

    def _populate_options(self):
        for child in self._opts_grid.get_children():
            self._opts_grid.remove(child)
        self._option_checks.clear()

        opts     = _SMB_OPTIONS if self._get_share_type() == "smb" else _NFS_OPTIONS
        defaults = {"_netdev", "nofail"}

        for i, (opt, tip) in enumerate(opts):
            cb = Gtk.CheckButton(label=opt)
            cb.set_tooltip_text(tip)
            cb.set_active(opt in defaults)
            cb.set_hexpand(True)
            self._option_checks[opt] = cb
            self._opts_grid.attach(cb, i % 3, i // 3, 1, 1)

        self._opts_grid.show_all()

    def _build_review_page(self):
        box = self._pbox()
        box.pack_start(
            Gtk.Label(label="Review the generated fstab entry:", xalign=0),
            False, False, 0)

        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        self._review_tv = Gtk.TextView(
            editable=False, monospace=True,
            wrap_mode=Gtk.WrapMode.CHAR,
            left_margin=8, right_margin=8, top_margin=6, bottom_margin=6)
        frame.add(self._review_tv)
        box.pack_start(frame, False, False, 0)

        self._review_detail = Gtk.Label(xalign=0)
        self._review_detail.set_line_wrap(True)
        box.pack_start(self._review_detail, False, False, 8)

        box.pack_start(
            Gtk.Label(label="Click Apply to add this entry to the list.", xalign=0),
            False, False, 0)

        self._review_page = self._add_page(
            box, Gtk.AssistantPageType.CONFIRM, "Review & Apply")

    # ── Events ────────────────────────────────────────────────────────────────

    def _on_prepare(self, _asst, page):
        if page == self._opts_page:
            self._populate_options()
        elif page == self._review_page:
            self._update_review()

    def _build_entry(self) -> Entry:
        is_smb = self._get_share_type() == "smb"
        host   = self._loc_host.get_text().strip()

        if is_smb:
            device = f"//{host}/{self._loc_smb_share.get_text().strip()}"
            fstype = "cifs"
        else:
            device = f"{host}:{self._loc_nfs_path.get_text().strip()}"
            fstype = "nfs"

        opts = [o for o, cb in self._option_checks.items() if cb.get_active()]

        if is_smb:
            if self._cred_inline.get_active():
                u = self._cred_user.get_text().strip()
                p = self._cred_pass.get_text()
                d = self._cred_domain.get_text().strip()
                if u: opts.append(f"username={u}")
                if p: opts.append(f"password={p}")
                if d: opts.append(f"domain={d}")
            elif self._cred_file.get_active():
                fp = self._cred_filepath.get_text().strip()
                if fp:
                    opts.append(f"credentials={fp}")
            else:
                opts.append("guest")

        extra = [p.strip()
                 for p in self._opts_extra.get_text().split(",") if p.strip()]
        opts.extend(extra)

        return Entry(
            kind    = Entry.MOUNT,
            device  = device,
            mount   = self._mountpoint.get_text().strip(),
            fstype  = fstype,
            options = ",".join(opts) if opts else "defaults",
            dump    = "0",
            passno  = "0",
        )

    def _update_review(self):
        e = self._build_entry()
        self._review_tv.get_buffer().set_text(e.to_line())
        self._review_detail.set_markup(
            f"<b>Device:</b>       <tt>{GLib.markup_escape_text(e.device)}</tt>\n"
            f"<b>Mount point:</b>  <tt>{GLib.markup_escape_text(e.mount)}</tt>\n"
            f"<b>Type:</b>         <tt>{e.fstype}</tt>\n"
            f"<b>Options:</b>      <tt>{GLib.markup_escape_text(e.options)}</tt>\n"
            f"<b>Dump / Pass:</b>  0 / 0"
        )
        self.set_page_complete(self._review_page, True)

    def _on_apply(self, _):
        self._on_complete(self._build_entry())
        self.destroy()


# ─── Main window ──────────────────────────────────────────────────────────────

# ListStore column indices
COL_DISPLAY  = 0   # device (or comment text) shown in first column
COL_MOUNT    = 1
COL_FSTYPE   = 2
COL_OPTIONS  = 3
COL_DUMP     = 4
COL_PASS     = 5
COL_ROW_TYPE = 6   # "mount" | "comment"
COL_INDEX    = 7   # index into self.entries


class FstabAdminWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_TITLE)
        self.set_default_size(1000, 580)

        self.entries: list[Entry] = []
        self.modified = False
        self._show_comments = True
        self._syncing_toggle = False

        self._accel_group = Gtk.AccelGroup()
        self.add_accel_group(self._accel_group)

        self._apply_css()
        self._build_ui()
        self._load()

        self.connect("delete-event", self._on_close)

    # ── CSS ───────────────────────────────────────────────────────────────────

    def _apply_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)

        vbox.pack_start(self._build_menubar(),   False, False, 0)
        vbox.pack_start(self._build_toolbar(),   False, False, 0)
        vbox.pack_start(self._build_treeview(),  True,  True,  0)
        vbox.pack_start(self._build_statusbar(), False, False, 0)

    def _build_menubar(self):
        menubar = Gtk.MenuBar()

        def item(label, cb, accel=None):
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", lambda _: cb())
            if accel:
                key, mod = Gtk.accelerator_parse(accel)
                mi.add_accelerator("activate", self._accel_group, key, mod,
                                   Gtk.AccelFlags.VISIBLE)
            return mi

        # File menu
        file_menu = Gtk.Menu()
        file_menu.append(item("Reload",    self._load,    "<Control>r"))
        file_menu.append(Gtk.SeparatorMenuItem())
        file_menu.append(item("Save",      self._save,    "<Control>s"))
        file_menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _: self.close())
        file_menu.append(quit_item)
        file_root = Gtk.MenuItem(label="File")
        file_root.set_submenu(file_menu)
        menubar.append(file_root)

        # Edit menu
        edit_menu = Gtk.Menu()
        edit_menu.append(item("Edit",        self._edit,   "Return"))
        edit_menu.append(item("Remove",      self._remove, "Delete"))
        edit_menu.append(Gtk.SeparatorMenuItem())
        edit_menu.append(item("Move Up",     self._move_up,   "<Alt>Up"))
        edit_menu.append(item("Move Down",   self._move_down, "<Alt>Down"))
        edit_root = Gtk.MenuItem(label="Edit")
        edit_root.set_submenu(edit_menu)
        menubar.append(edit_root)

        # Add menu
        add_menu = Gtk.Menu()
        add_menu.append(item("Add Mount",          self._add_mount))
        add_menu.append(item("Add Comment",        self._add_comment))
        add_menu.append(item("Add Network Share…", self._add_network_share))
        add_root = Gtk.MenuItem(label="Add")
        add_root.set_submenu(add_menu)
        menubar.append(add_root)

        # View menu
        view_menu = Gtk.Menu()
        view_menu.append(item("View Raw", self._view_raw))
        view_menu.append(Gtk.SeparatorMenuItem())
        self._comments_menu_item = Gtk.CheckMenuItem(label="Show Comments")
        self._comments_menu_item.set_active(True)
        self._comments_menu_item.connect("toggled", self._on_toggle_comments_menu)
        view_menu.append(self._comments_menu_item)
        view_root = Gtk.MenuItem(label="View")
        view_root.set_submenu(view_menu)
        menubar.append(view_root)

        # Help menu
        help_menu = Gtk.Menu()
        help_menu.append(item("About", self._show_about, "F1"))
        help_root = Gtk.MenuItem(label="Help")
        help_root.set_submenu(help_menu)
        menubar.append(help_root)

        return menubar

    def _build_toolbar(self):
        tb = Gtk.Toolbar()
        tb.get_style_context().add_class("toolbar")
        tb.set_style(Gtk.ToolbarStyle.BOTH_HORIZ)

        def btn(icon, label, tip, cb):
            b = Gtk.ToolButton(icon_name=icon, label=label)
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _: cb())
            tb.insert(b, -1)
            return b

        def sep():
            tb.insert(Gtk.SeparatorToolItem(), -1)

        btn("view-refresh",    "Reload",       "Reload /etc/fstab from disk",         self._load)
        sep()
        btn("list-add",        "Add Mount",    "Add a new mount entry",               self._add_mount)
        btn("insert-text",     "Add Comment",  "Add a comment line",                  self._add_comment)
        btn("network-server",  "Net Share",    "Add a network share via wizard (SMB / NFS)", self._add_network_share)
        btn("document-edit",   "Edit",         "Edit the selected entry  (Enter)",    self._edit)
        btn("list-remove",     "Remove",       "Remove the selected entry  (Delete)", self._remove)
        sep()
        btn("go-up",           "Move Up",      "Move entry up",                       self._move_up)
        btn("go-down",         "Move Down",    "Move entry down",                     self._move_down)
        sep()
        btn("document-open",   "View Raw",     "View raw fstab text",                 self._view_raw)
        sep()
        self._comments_btn = Gtk.ToggleToolButton(icon_name="format-justify-left",
                                                   label="Comments")
        self._comments_btn.set_active(True)
        self._comments_btn.set_tooltip_text("Show / hide comment lines")
        self._comments_btn.connect("toggled", self._on_toggle_comments)
        tb.insert(self._comments_btn, -1)
        sep()
        self._save_btn = btn("document-save", "Save",
                             "Save changes to /etc/fstab (requires authentication)",
                             self._save)

        return tb

    def _build_treeview(self):
        self._store = Gtk.ListStore(
            str, str, str, str, str, str, str, int
            # device/comment, mount, fstype, options, dump, pass, row_type, entry_idx
        )

        self._tv = Gtk.TreeView(model=self._store, enable_search=False)
        self._tv.set_headers_visible(True)
        self._tv.set_activate_on_single_click(False)
        self._tv.get_selection().set_mode(Gtk.SelectionMode.SINGLE)

        columns = [
            ("Device / UUID",  COL_DISPLAY,  260, True),
            ("Mount Point",    COL_MOUNT,    150, True),
            ("Type",           COL_FSTYPE,    80, False),
            ("Options",        COL_OPTIONS,  200, True),
            ("Dump",           COL_DUMP,      55, False),
            ("Pass",           COL_PASS,      55, False),
        ]

        for title, col_idx, width, expand in columns:
            renderer = Gtk.CellRendererText()
            tvc = Gtk.TreeViewColumn(title, renderer, text=col_idx)
            tvc.set_min_width(width)
            tvc.set_expand(expand)
            tvc.set_cell_data_func(renderer, self._cell_style)
            self._tv.append_column(tvc)

        self._tv.connect("row-activated",    lambda tv, path, col: self._edit())
        self._tv.connect("key-press-event",  self._on_key_press)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.add(self._tv)
        return sw

    def _cell_style(self, col, renderer, model, it, col_idx):
        row_type = model[it][COL_ROW_TYPE]
        if row_type == Entry.COMMENT:
            renderer.set_property("foreground", "#888888")
            renderer.set_property("style", 2)   # ITALIC
            renderer.set_property("weight", 400)
        else:
            renderer.set_property("foreground", None)
            renderer.set_property("style",      0)   # NORMAL
            renderer.set_property("weight",     400)

    def _build_statusbar(self):
        sb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        sb.get_style_context().add_class("statusbar")

        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        frame.add(sb)

        self._status_lbl = Gtk.Label(label="", xalign=0,
                                     margin_start=8, margin_end=8,
                                     margin_top=3, margin_bottom=3)
        sb.pack_start(self._status_lbl, True, True, 0)

        self._modified_lbl = Gtk.Label(label="", xalign=1,
                                       margin_start=8, margin_end=8)
        self._modified_lbl.get_style_context().add_class("modified-label")
        sb.pack_end(self._modified_lbl, False, False, 0)

        return frame

    # ── Data operations ───────────────────────────────────────────────────────

    def _load(self):
        try:
            with open(FSTAB_PATH) as f:
                self.entries = [Entry.parse(line) for line in f]
        except PermissionError:
            self._err(f"Cannot read {FSTAB_PATH}.\nTry running as root.")
            return
        except FileNotFoundError:
            self._err(f"{FSTAB_PATH} not found.")
            return

        self.modified = False
        self._refresh_store()
        n = sum(1 for e in self.entries if e.kind == Entry.MOUNT)
        self._set_status(f"Loaded  {FSTAB_PATH}   ·   {n} mount entries")
        self._modified_lbl.set_text("")

    def _refresh_store(self):
        self._store.clear()
        for idx, entry in enumerate(self.entries):
            if entry.kind == Entry.BLANK:
                continue
            if entry.kind == Entry.COMMENT and not self._show_comments:
                continue
            if entry.kind == Entry.COMMENT:
                self._store.append([
                    entry.raw.strip(), "", "", "", "", "",
                    Entry.COMMENT, idx,
                ])
            else:
                self._store.append([
                    entry.device, entry.mount, entry.fstype,
                    entry.options, entry.dump, entry.passno,
                    Entry.MOUNT, idx,
                ])

    def _selected(self):
        """Return (store_iter, entry_index, Entry) or (None, None, None)."""
        model, it = self._tv.get_selection().get_selected()
        if it is None:
            return None, None, None
        idx = model[it][COL_INDEX]
        return it, idx, self.entries[idx]

    # ── Toolbar actions ───────────────────────────────────────────────────────

    def _add_mount(self):
        dlg = EntryDialog(self)
        dlg.run()
        new_entry = dlg.result
        dlg.destroy()
        if new_entry:
            insert_at = self._insert_position()
            self.entries.insert(insert_at, new_entry)
            self._mark_modified()
            self._refresh_store()
            self._select_entry_index(insert_at)
            self._set_status("Mount entry added.")

    def _add_comment(self):
        dlg = CommentDialog(self)
        dlg.run()
        text = dlg.result
        dlg.destroy()
        if text is not None:
            insert_at = self._insert_position()
            self.entries.insert(insert_at, Entry(kind=Entry.COMMENT, raw=text))
            self._mark_modified()
            self._refresh_store()
            self._select_entry_index(insert_at)
            self._set_status("Comment added.")

    def _add_network_share(self):
        def on_complete(entry):
            insert_at = self._insert_position()
            self.entries.insert(insert_at, entry)
            self._mark_modified()
            self._refresh_store()
            self._select_entry_index(insert_at)
            self._set_status(f"Network share added:  {entry.device}  →  {entry.mount}")

        wizard = NetworkShareWizard(self, on_complete)
        wizard.show_all()

    def _edit(self):
        it, idx, entry = self._selected()
        if entry is None:
            self._info("Select an entry to edit."); return

        if entry.kind == Entry.COMMENT:
            dlg = CommentDialog(self, entry.raw)
            dlg.run()
            if dlg.result is not None:
                self.entries[idx].raw = dlg.result
                self._mark_modified()
                self._refresh_store()
                self._select_entry_index(idx)
            dlg.destroy()

        elif entry.kind == Entry.MOUNT:
            dlg = EntryDialog(self, entry)
            dlg.run()
            if dlg.result:
                self.entries[idx] = dlg.result
                self._mark_modified()
                self._refresh_store()
                self._select_entry_index(idx)
            dlg.destroy()

    def _remove(self):
        it, idx, entry = self._selected()
        if entry is None:
            self._info("Select an entry to remove."); return

        label = (entry.raw.strip()
                 if entry.kind == Entry.COMMENT
                 else f"{entry.device}  →  {entry.mount}")

        dlg = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Remove entry?",
        )
        dlg.format_secondary_text(label)
        resp = dlg.run()
        dlg.destroy()

        if resp == Gtk.ResponseType.YES:
            del self.entries[idx]
            self._mark_modified()
            self._refresh_store()
            self._set_status("Entry removed.")

    def _move_up(self):
        it, idx, entry = self._selected()
        if idx is None or idx == 0:
            return
        self.entries[idx - 1], self.entries[idx] = self.entries[idx], self.entries[idx - 1]
        self._mark_modified()
        self._refresh_store()
        self._select_entry_index(idx - 1)

    def _move_down(self):
        it, idx, entry = self._selected()
        if idx is None or idx >= len(self.entries) - 1:
            return
        self.entries[idx], self.entries[idx + 1] = self.entries[idx + 1], self.entries[idx]
        self._mark_modified()
        self._refresh_store()
        self._select_entry_index(idx + 1)

    def _on_toggle_comments(self, btn):
        if self._syncing_toggle:
            return
        self._show_comments = btn.get_active()
        self._syncing_toggle = True
        self._comments_menu_item.set_active(self._show_comments)
        self._syncing_toggle = False
        self._refresh_store()

    def _on_toggle_comments_menu(self, item):
        if self._syncing_toggle:
            return
        self._show_comments = item.get_active()
        self._syncing_toggle = True
        self._comments_btn.set_active(self._show_comments)
        self._syncing_toggle = False
        self._refresh_store()

    def _view_raw(self):
        content = "\n".join(e.to_line() for e in self.entries) + "\n"
        RawViewDialog(self, content)

    def _save(self):
        if not self.modified:
            self._info("No unsaved changes."); return

        content = "\n".join(e.to_line() for e in self.entries) + "\n"
        ok, detail = write_fstab(content)

        if ok:
            self.modified = False
            self._modified_lbl.set_text("")
            self._set_status(f"Saved  {FSTAB_PATH}   ·   backup at {FSTAB_PATH}.bak")
            dlg = Gtk.MessageDialog(
                transient_for=self, modal=True,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text="fstab saved successfully.",
            )
            dlg.format_secondary_text(f"Backup created: {FSTAB_PATH}.bak")
            dlg.run(); dlg.destroy()
        else:
            msg = "Could not write to /etc/fstab."
            sec = ("Authentication was cancelled or pkexec/sudo is unavailable."
                   if detail is None
                   else f"A temporary copy was saved to:\n  {detail}\n\n"
                        f"Apply manually:\n  sudo cp {detail} /etc/fstab")
            dlg = Gtk.MessageDialog(
                transient_for=self, modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text=msg,
            )
            dlg.format_secondary_text(sec)
            dlg.run(); dlg.destroy()

    # ── Keyboard handler ──────────────────────────────────────────────────────

    def _on_key_press(self, widget, event):
        key = Gdk.keyval_name(event.keyval)
        if key == "Delete":
            self._remove(); return True
        if key in ("Return", "KP_Enter"):
            self._edit(); return True
        return False

    # ── Close handler ─────────────────────────────────────────────────────────

    def _on_close(self, *_):
        if self.modified:
            dlg = Gtk.MessageDialog(
                transient_for=self, modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.YES_NO,
                text="You have unsaved changes.",
            )
            dlg.format_secondary_text("Quit without saving?")
            resp = dlg.run()
            dlg.destroy()
            if resp != Gtk.ResponseType.YES:
                return True   # prevent close
        return False

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _insert_position(self):
        _, idx, _ = self._selected()
        return (idx + 1) if idx is not None else len(self.entries)

    def _select_entry_index(self, target_idx):
        for row in self._store:
            if row[COL_INDEX] == target_idx:
                self._tv.get_selection().select_iter(row.iter)
                self._tv.scroll_to_cell(row.path, None, False, 0, 0)
                return

    def _mark_modified(self):
        self.modified = True
        self._modified_lbl.set_text("● Unsaved changes")

    def _set_status(self, msg: str):
        self._status_lbl.set_text(msg)

    def _show_about(self):
        dlg = Gtk.AboutDialog()
        dlg.set_transient_for(self)
        dlg.set_modal(True)
        dlg.set_program_name("fstabUI")
        dlg.set_version("1.0")
        dlg.set_comments(
            "A graphical editor for /etc/fstab.\n"
            "Add, edit, remove, and reorder mount entries\n"
            "with privilege-aware saving."
        )
        dlg.set_license_type(Gtk.License.MIT_X11)
        dlg.set_website("https://github.com/BasicWombat/fstabui")
        dlg.set_website_label("GitHub")
        dlg.set_logo_icon_name("drive-harddisk")
        dlg.set_authors(["BasicWombat"])
        dlg.run()
        dlg.destroy()

    def _err(self, msg: str):
        d = Gtk.MessageDialog(transient_for=self, modal=True,
                              message_type=Gtk.MessageType.ERROR,
                              buttons=Gtk.ButtonsType.OK, text=msg)
        d.run(); d.destroy()

    def _info(self, msg: str):
        d = Gtk.MessageDialog(transient_for=self, modal=True,
                              message_type=Gtk.MessageType.INFO,
                              buttons=Gtk.ButtonsType.OK, text=msg)
        d.run(); d.destroy()


# ─── GTK Application ──────────────────────────────────────────────────────────

class FstabApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.local.fstabui")

    def do_activate(self):
        win = FstabAdminWindow(self)
        win.show_all()


def main():
    app = FstabApp()
    app.run(None)


if __name__ == "__main__":
    main()
