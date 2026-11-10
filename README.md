# RS7 Control

A local GTK desktop configurator for the Smailwolf RS7 on Linux.

## Run v0.1

On Zorin OS or Ubuntu, install `python3-gi` and `gir1.2-gtk-3.0` if they are missing. Install `bluez` for Bluetooth detection. From this directory, run:

```sh
/usr/bin/python3 app.py
```

The app checks the connection automatically about every two seconds and displays one mode. It prioritizes a detected USB cable, then a connected Bluetooth mouse, then a responding 2.4 GHz receiver. Bluetooth provides connection status only. Wired USB configuration is experimental.

If the status reports access denied, verify the displayed device belongs to the RS7. Grant access to that device node for your user, replacing `N` with the displayed number:

```sh
sudo setfacl -m "u:$(id -un):rw" /dev/hidrawN
```

Reconnects can change the node number and clear the temporary permission. Run the app as your regular user.

## DPI

The editor targets the active DPI stage. Use the slider or enter a value between 200 and 24000, then click **Apply DPI**. The slider moves in steps of 100, and the input accepts integers. The firmware can reject or normalize a custom value.

**Save profile** stores the value in the editor as a local JSON file. **Load profile** fills the editor from that file. Click **Apply DPI** to write the value to the active stage.

The app reads the current shared configuration before writing, checks the returned data after writing, and attempts to restore the original configuration if verification fails. **Undo last change** restores the previous stage value during the current session. The editor refuses writes if the device reports a configuration layout that has not been verified.

Protocol reference: [Smailwolf RS7 web configurator](https://k2a11.yjx2012.com/).
