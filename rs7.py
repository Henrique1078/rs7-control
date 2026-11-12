"""Linux HID transport for the Smailwolf RS7."""
import fcntl
import os
import re
import select
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

MIN_DPI, MAX_DPI = 200, 24000
COLLECTION = bytes.fromhex("06 01 ff 09 10 a1 01")
READ_CONFIG = bytes.fromhex("00 55 0e a5 0b 2f 01 01")
READ_BATTERY = bytes.fromhex("00 55 30 a5 0b 2e 01 01")
WRITE_CONFIG = bytes.fromhex("00 55 0f ae 0a 2f 01 01 00 00")


def connections():
    devices = {}
    for device in sorted(Path("/sys/bus/hid/devices").glob("0003:A8A[45]:2255.*")):
        try:
            if not (device / "report_descriptor").read_bytes().startswith(COLLECTION):
                continue
        except OSError:
            continue
        mode = "USB cable" if ":A8A4:" in device.name else "2.4 GHz receiver"
        for node in (device / "hidraw").glob("hidraw*"):
            devices[str(Path("/dev") / node.name)] = mode
    try:
        output = subprocess.check_output(
            ["bluetoothctl", "devices", "Connected"], text=True,
            stderr=subprocess.DEVNULL, timeout=2,
        )
        bluetooth = any(
            re.fullmatch(r"Device [0-9A-F:]{17} (?:mouse )?RS7(?: BT5\.1| [12])?",
                         line.strip(), re.IGNORECASE)
            for line in output.splitlines()
        )
    except (OSError, subprocess.SubprocessError):
        bluetooth = None
    return devices, bluetooth


@contextmanager
def open_device(path):
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        node = Path("/sys/dev/char") / f"{os.major(os.fstat(fd).st_rdev)}:{os.minor(os.fstat(fd).st_rdev)}"
        device = node / "device"
        if (not re.fullmatch(r"0003:A8A[45]:2255\.[0-9A-F]+", device.resolve().name)
                or not (device / "report_descriptor").read_bytes().startswith(COLLECTION)):
            raise OSError("The RS7 interface changed. Refresh and try again.")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd
    finally:
        os.close(fd)


def send(fd, packet):
    if os.write(fd, packet.ljust(65, b"\0")) != 65:
        raise OSError("Incomplete HID write.")


def query(fd, command):
    header = bytes((0xAA, command[2], 0xA5))
    for _ in range(3):
        send(fd, command)
        deadline = time.monotonic() + 0.8
        while (remaining := deadline - time.monotonic()) > 0:
            if not select.select([fd], [], [], remaining)[0]:
                break
            report = os.read(fd, 64)
            if len(report) == 64 and report.startswith(header):
                return report
    raise TimeoutError("The RS7 did not respond. Wake the mouse to reconnect.")


def dpi_values(report):
    return tuple(int.from_bytes(report[i:i + 2], "little") for i in range(13, 25, 2))


def read_status(path):
    with open_device(path) as fd:
        config = query(fd, READ_CONFIG)
        values = dpi_values(config)
        if (not 1 <= config[12] <= config[11] <= 6
                or not all(MIN_DPI <= dpi <= MAX_DPI for dpi in values[:config[11]])):
            raise ValueError("The mouse did not return a valid DPI configuration.")
        try:
            battery = query(fd, READ_BATTERY)[8]
        except TimeoutError:
            battery = None
    return values, config[12], battery if battery is not None and battery <= 100 else None


def config_state(report):
    # Byte 54 is present in wired readback (100 at full charge) but the
    # vendor command has no writable counterpart for it.
    return report[8:54] + report[55:]


def set_dpi(path, stage, value, previous):
    if not 1 <= stage <= 6 or not MIN_DPI <= value <= MAX_DPI:
        raise ValueError("Invalid DPI or stage.")
    with open_device(path) as fd:
        original = query(fd, READ_CONFIG)
        if original[12] != stage:
            raise ValueError("The active DPI stage changed. Try again after detection.")
        if dpi_values(original)[stage - 1] != previous:
            raise ValueError("The stage changed since the last read. Refresh before applying.")
        if value == previous:
            return
        # Writes replace a shared block. Only use the layout verified on hardware.
        if (not 1 <= original[12] <= original[11] <= 6
                or any(original[25:48]) or original[54] > 100 or any(original[55:])
                or not all(0 < dpi <= MAX_DPI for dpi in dpi_values(original))):
            raise ValueError("Unrecognized configuration layout. DPI writes are disabled.")
        restore = bytearray(65)
        restore[:10] = WRITE_CONFIG
        restore[10:26] = original[9:25]
        restore[49:55] = original[48:54]
        packet = restore.copy()
        offset = 13 + (stage - 1) * 2
        expected = bytearray(original)
        expected[offset:offset + 2] = value.to_bytes(2, "little")
        packet[offset + 1:offset + 3] = expected[offset:offset + 2]
        try:
            send(fd, packet)
            time.sleep(0.25)
            if config_state(query(fd, READ_CONFIG)) != config_state(expected):
                raise ValueError("The device did not confirm the requested configuration.")
        except (OSError, ValueError) as error:
            try:
                try:
                    restored = config_state(query(fd, READ_CONFIG)) == config_state(original)
                except TimeoutError:
                    restored = False
                if not restored:
                    send(fd, restore)
                    time.sleep(0.25)
                    if config_state(query(fd, READ_CONFIG)) != config_state(original):
                        raise ValueError("Readback differs from the original.")
            except (OSError, ValueError) as restore_error:
                raise OSError(
                    f"Could not verify the original settings. Original stages: {dpi_values(original)}. "
                    f"{restore_error}"
                ) from error
            raise OSError(f"{error} Original settings verified.") from error
