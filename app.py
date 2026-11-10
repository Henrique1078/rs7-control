#!/usr/bin/python3
"""RS7 Control desktop application."""
import json
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from rs7 import MAX_DPI, MIN_DPI, connections, read_status, set_dpi


class Window(Gtk.Window):
    def __init__(self):
        super().__init__(title="RS7 Control")
        self.set_default_size(660, 440)
        self.path = None
        self.values = ()
        self.undo = None
        self.busy = False
        self.reading = False
        self.pending_write = None
        self.syncing = False
        self.stage = 1

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        root.set_border_width(24)
        self.add(root)
        header = Gtk.Box(spacing=12)
        root.pack_start(header, False, False, 0)
        header.pack_start(Gtk.Image.new_from_icon_name("input-mouse-symbolic", Gtk.IconSize.DIALOG),
                          False, False, 0)
        title = Gtk.Label(xalign=0)
        title.set_markup("<span size='xx-large' weight='bold'>RS7 Control</span>\nSmailwolf RS7 · v0.1")
        header.pack_start(title, True, True, 0)
        self.connection = self.label(root, "Detecting connection…")
        self.battery = self.label(root, "Battery  —")
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.get_style_context().add_class("card")
        root.pack_start(card, False, False, 0)
        label = self.label(card, "")
        label.set_markup("<b>Sensor DPI</b>")
        self.active_stage = self.label(card, "Active stage  —")

        row = Gtk.Box(spacing=12)
        card.pack_start(row, False, False, 0)
        self.slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, MIN_DPI, MAX_DPI, 100)
        self.slider.set_draw_value(False)
        self.slider.connect("value-changed", self.slider_changed)
        row.pack_start(self.slider, True, True, 0)
        self.input = Gtk.Entry(width_chars=7, max_length=5, placeholder_text="DPI")
        self.input.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self.input.connect("changed", self.input_changed)
        row.pack_start(self.input, False, False, 0)
        self.label(card, "200–24,000 DPI · Apply writes to the active stage.")

        actions = Gtk.Box(spacing=8)
        card.pack_start(actions, False, False, 0)
        self.apply = Gtk.Button(label="Apply DPI")
        self.apply.get_style_context().add_class("suggested-action")
        self.apply.connect("clicked", lambda *_: self.apply_dpi())
        actions.pack_end(self.apply, False, False, 0)
        self.undo_button = Gtk.Button(label="Undo last change")
        self.undo_button.connect("clicked", lambda *_: self.undo_dpi())
        actions.pack_end(self.undo_button, False, False, 0)
        for caption, saving in (("Save profile…", True), ("Load profile…", False)):
            button = Gtk.Button(label=caption)
            button.connect("clicked", lambda _, saving=saving: self.profile(saving))
            actions.pack_start(button, False, False, 0)
        self.status = self.label(root, "")
        self.status.set_line_wrap(True)
        self.card = card
        self.refresh()
        GLib.timeout_add_seconds(2, self.poll)

    @staticmethod
    def label(parent, text):
        label = Gtk.Label(label=text, xalign=0)
        parent.pack_start(label, False, False, 0)
        return label

    def update_controls(self):
        self.card.set_sensitive(bool(self.values) and not self.busy)
        value = self.input.get_text()
        valid = value.isascii() and value.isdecimal() and MIN_DPI <= int(value) <= MAX_DPI
        self.apply.set_sensitive(bool(self.values and valid
                                      and int(value) != self.values[self.stage - 1]))
        self.undo_button.set_sensitive(self.undo is not None and self.undo[:2] == (self.path, self.stage))

    def set_input(self, value):
        self.input.set_text(str(value))

    def input_changed(self, *_):
        value = self.input.get_text()
        if value.isascii() and value.isdecimal() and MIN_DPI <= int(value) <= MAX_DPI:
            self.syncing = True
            self.slider.set_value(int(value))
            self.syncing = False
        self.update_controls()

    def slider_changed(self, slider):
        if not self.syncing:
            self.set_input(int(slider.get_value()))

    def poll(self):
        if not self.busy and not self.reading:
            self.refresh()
        return True

    def profile(self, saving):
        value = self.input.get_text()
        if saving and not (value.isascii() and value.isdecimal()
                           and MIN_DPI <= int(value) <= MAX_DPI):
            self.status.set_text("Enter a valid DPI before saving a profile.")
            return
        dialog = Gtk.FileChooserDialog(
            title="Save DPI profile" if saving else "Load DPI profile",
            transient_for=self,
            action=Gtk.FileChooserAction.SAVE if saving else Gtk.FileChooserAction.OPEN,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save" if saving else "Load", Gtk.ResponseType.ACCEPT)
        if saving:
            dialog.set_current_name("rs7-profile.json")
            dialog.set_do_overwrite_confirmation(True)
        try:
            if dialog.run() != Gtk.ResponseType.ACCEPT:
                return
            path = Path(dialog.get_filename())
            if saving:
                path.write_text(json.dumps({"version": 1, "dpi": int(value)}, indent=2) + "\n")
                self.status.set_text("DPI profile saved.")
            else:
                profile = json.loads(path.read_text())
                if (not isinstance(profile, dict) or profile.get("version") != 1
                        or type(profile.get("dpi")) is not int
                        or not MIN_DPI <= profile["dpi"] <= MAX_DPI):
                    raise ValueError("Invalid RS7 DPI profile.")
                self.set_input(profile["dpi"])
                self.status.set_text("Profile loaded. Click Apply DPI to write it to the mouse.")
        except (OSError, ValueError) as error:
            self.status.set_text(str(error))
        finally:
            dialog.destroy()

    def run(self, task, complete, *, background=False):
        if background:
            self.reading = True
        else:
            self.busy = True
            self.update_controls()

        def finish(result, error):
            if background:
                self.reading = False
            else:
                self.busy = False
            complete(result, error)
            self.update_controls()
            return False

        def worker():
            try:
                result = task()
            except (OSError, ValueError) as error:
                GLib.idle_add(finish, None, error)
            else:
                GLib.idle_add(finish, result, None)

        threading.Thread(target=worker, daemon=False).start()

    def refresh(self, message=""):
        def read():
            devices, bluetooth = connections()
            cable = next((path for path, mode in devices.items() if mode == "USB cable"), None)
            receiver = next((path for path, mode in devices.items() if mode == "2.4 GHz receiver"), None)
            if bluetooth and not cable:
                return "Bluetooth", None, None, "DPI control is available over USB or 2.4 GHz."
            path = cable or receiver
            if not path:
                return "Disconnected", None, None, ""
            mode = devices[path]
            try:
                return mode, path, read_status(path), ""
            except PermissionError:
                label = mode if cable else "Unknown"
                return label, path, None, f"Access denied for {path}."
            except (OSError, ValueError) as error:
                label = mode if cable else "Unknown"
                return label, path, None, str(error)

        def finish(result, error):
            if error:
                result = ("Unknown", None, None, str(error))
            mode, path, data, detail = result
            values, active, battery = data or ((), 0, None)
            old = (self.path, self.values, self.stage)
            self.path, self.values, self.stage = path, values, active
            if path != old[0]:
                self.undo = None
            self.connection.set_text(f"Connection  ·  {mode}")
            self.battery.set_text("Battery  ·  " + (f"{battery}%" if battery is not None else "—"))
            self.active_stage.set_text(
                f"Active stage  ·  {active}  /  {values[active - 1]:,} DPI" if values else "Active stage  —"
            )
            if old != (path, values, active) or message:
                self.input.set_text(str(values[active - 1]) if values else "")
                self.status.set_text(detail or message)
            elif detail:
                self.status.set_text(detail)

            pending = self.pending_write
            self.pending_write = None
            if pending:
                target, stage, value, previous, undoing = pending
                if path == target and values and active == stage and values[stage - 1] == previous:
                    self.write(stage, value, previous, undoing)
                else:
                    self.status.set_text("The connection or active DPI changed. Apply again.")

        self.run(read, finish, background=True)

    def apply_dpi(self):
        if not self.busy and self.values:
            self.write(self.stage, int(self.input.get_text()), self.values[self.stage - 1])

    def undo_dpi(self):
        if self.busy or self.undo is None:
            return
        _, stage, original, applied = self.undo
        self.write(stage, original, applied, undoing=True)

    def write(self, stage, value, previous, undoing=False):
        path = self.path
        if self.reading:
            self.pending_write = (path, stage, value, previous, undoing)
            self.status.set_text("Waiting for the current device check…")
            return
        self.status.set_text("Writing and verifying DPI…")

        def finish(_, error):
            if not error:
                self.undo = None if undoing else (path, stage, previous, value)
            self.refresh(str(error) if error else f"Stage {stage} verified at {value:,} DPI.")

        self.run(lambda: set_dpi(path, stage, value, previous), finish)


if __name__ == "__main__":
    css = Gtk.CssProvider()
    css.load_from_data(b"""
        .card { padding: 18px; border-radius: 14px; border: 1px solid alpha(currentcolor, 0.18);
                background-color: alpha(currentcolor, 0.035); }
        button { border-radius: 8px; }
    """)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    window = Window()
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    Gtk.main()
