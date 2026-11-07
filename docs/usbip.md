# USB printer on another computer (USB/IP)

USB/IP shares a USB device over the network. The computer the printer is plugged into exports it, the computer running the label designer attaches it and then sees it as if it were plugged in there, e.g., as `/dev/usb/lp0`. It is part of the Linux kernel, the tools come in the package `usbip` (Debian, Fedora, Arch) or, on Ubuntu, in `linux-tools-generic` and `linux-tools-$(uname -r)` for the running kernel. For a kernel without its own package, e.g., a mainline kernel, those of another version work too, called directly as `/usr/lib/linux-tools/<version>/usbip`.

USB/IP has neither encryption nor passwords: whoever reaches port 3240 of the exporting computer can take the device. Keep that port closed to others and connect through SSH as below.

## On the computer with the printer

```sh
sudo modprobe usbip-host
sudo usbipd -D
usbip list -l               # the bus ID of the printer, e.g., 3-2
sudo usbip bind -b 3-2
```

Many desktops run `ipp-usb`, which takes printers with IPP over USB, e.g., the VC-500W, and resets them again and again. Stop it until the next reboot before binding:

```sh
sudo systemctl mask --runtime --now ipp-usb.service
```

## On the computer with the label designer

```sh
sudo modprobe vhci-hcd
ssh -N -L 3240:127.0.0.1:3240 printer-computer &
sudo usbip attach -r 127.0.0.1 -b 3-2
```

If only the printer's computer can reach the other one, it opens the tunnel the other way round: `ssh -N -R 3240:127.0.0.1:3240 label-designer-computer`.

The printer then shows up as `/dev/usb/lpN` and is used like one plugged in there, in Docker with `--device /dev/usb/lpN`.

## When it stops working

- A printer that resets, e.g., after being switched off or detached, falls out of its binding: bind it again on its computer and attach it again.
- `usbip port` lists the attached devices, `sudo usbip detach -p <port>` releases one. One that never came up is released with `echo <port> | sudo tee /sys/devices/platform/vhci_hcd.0/detach`.
- The daemon, the bindings and the attached devices do not survive a reboot.
