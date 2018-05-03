import time
import struct
import usb1

from fx2 import *
from fx2.format import input_data


__all__ = ['GlasgowDevice', 'GlasgowDeviceError']


VID_QIHW     = 0x20b7
PID_GLASGOW  = 0x9db1

REQ_EEPROM   = 0x10
REQ_FPGA_CFG = 0x11
REQ_STATUS   = 0x12
REQ_REGISTER = 0x13
REQ_IO_VOLT  = 0x14

ST_ERROR     = 1<<0
ST_FPGA_RDY  = 1<<1

IO_BUF_A     = 1<<0
IO_BUF_B     = 1<<1


class GlasgowDeviceError(FX2DeviceError):
    """An exception raised on a communication error."""


class GlasgowDevice(FX2Device):
    def __init__(self, firmware_file=None):
        super().__init__(VID_QIHW, PID_GLASGOW)
        if self._device.getDevice().getbcdDevice() == 0:
            if firmware_file is None:
                raise GlasgowDeviceError("Firmware is not uploaded")
            else:
                # TODO: log?
                with open(firmware_file, "rb") as f:
                    self.load_ram(input_data(f, fmt="ihex"))

                # let the device re-enumerate and re-acquire it
                time.sleep(1)
                super().__init__(VID_QIHW, PID_GLASGOW)

                # still not the right firmware?
                if self._device.getDevice().getbcdDevice() == 0:
                    raise GlasgowDeviceError("Firmware upload failed")

    def read_eeprom(self, idx, addr, length):
        """Read ``length`` bytes at ``addr`` from EEPROM at index ``idx``."""
        return self.control_read(usb1.REQUEST_TYPE_VENDOR, REQ_EEPROM, addr, idx, length)

    def write_eeprom(self, idx, addr, data):
        """Write ``data`` to ``addr`` in EEPROM at index ``idx``."""
        self.control_write(usb1.REQUEST_TYPE_VENDOR, REQ_EEPROM, addr, idx, data)

    def _status(self):
        """Read device status word."""
        return self.control_read(usb1.REQUEST_TYPE_VENDOR, REQ_STATUS, 0, 0, 1)[0]

    def _register_error(self, addr):
        if self._status() & ST_FPGA_RDY:
            raise GlasgowDeviceError("Register 0x{:02x} does not exit".format(addr))
        else:
            raise GlasgowDeviceError("FPGA is not configured")

    def read_register(self, addr):
        """Read byte FPGA register at ``addr``."""
        try:
            return self.control_read(usb1.REQUEST_TYPE_VENDOR, REQ_REGISTER, addr, 0, 1)[0]
        except usb1.USBErrorPipe:
            self._register_error(addr)

    def write_register(self, addr, value):
        """Write ``value`` to byte FPGA register at ``addr``."""
        try:
            self.control_write(usb1.REQUEST_TYPE_VENDOR, REQ_REGISTER, addr, 0, [value])
        except usb1.USBErrorPipe:
            self._register_error(addr)

    def download_bitstream(self, data):
        """Download bitstream ``data`` to FPGA."""
        # Send consecutive chunks of bitstream.
        # Sending 0th chunk resets the FPGA.
        index = 0
        while index * 1024 < len(data):
            self.control_write(usb1.REQUEST_TYPE_VENDOR, REQ_FPGA_CFG, 0, index,
                               data[index * 1024:(index + 1)*1024])
            index += 1
        # Complete configuration by sending a request with no data.
        self.control_write(usb1.REQUEST_TYPE_VENDOR, REQ_FPGA_CFG, 0, index, [])
        # Check if we've succeeded.
        if not (self._status() & ST_FPGA_RDY):
            raise GlasgowDeviceError("FPGA configuration failed")

    def _iobuf_spec_to_mask(self, spec, multi):
        if not multi and len(spec) > 1:
            raise GlasgowDeviceError("Only one I/O port may be specified for this operation")

        mask = 0
        for port in str(spec):
            if   port == "A":
                mask |= IO_BUF_A
            elif port == "B":
                mask |= IO_BUF_B
            else:
                raise GlasgowDeviceError("Unknown I/O port {}".format(port))
        return mask

    def set_voltage(self, spec, volts):
        self.control_write(usb1.REQUEST_TYPE_VENDOR, REQ_IO_VOLT,
            0, self._iobuf_spec_to_mask(spec, multi=True), struct.pack("<H", round(volts * 1000)))
        # Check if we've succeeded
        if self._status() & ST_ERROR:
            raise GlasgowDeviceError("Cannot set port(s) {} I/O voltage to {} V"
                                     .format(spec or "(none)", volts))

    def get_voltage(self, spec):
        try:
            return struct.unpack("<H",
                self.control_read(usb1.REQUEST_TYPE_VENDOR, REQ_IO_VOLT,
                    0, self._iobuf_spec_to_mask(spec, multi=False), 2))[0] / 1000
        except usb1.USBErrorPipe:
            raise GlasgowDeviceError("Cannot get port {} I/O voltage"
                                     .format(spec or "(none)"))