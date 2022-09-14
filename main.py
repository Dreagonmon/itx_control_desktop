import os, sys, traceback, time, subprocess, re
APP_ROOT = os.path.abspath(os.path.dirname(__file__))
LIB_ROOT = os.path.join(APP_ROOT, "lib")
sys.path.append(LIB_ROOT)

from device import SerialDevice
from pywifi import PyWiFi, const

PAGESIZE = 5

def ip_info():
    p = subprocess.run(["ip", "addr", "show"], stdout=subprocess.PIPE)
    result = p.stdout.decode("utf8")
    try:
        pt = re.compile("inet\s+(\S+)")
        find = pt.findall(result)
        return "\n".join(find)
    except:
        return result

async def select(device: SerialDevice, title = "", options = []):
    if len(options) <= PAGESIZE + 1:
        return await device.select_list(title, options)
    offset = 0
    while True:
        if offset >= len(options):
            offset = 0
        opt = options[offset : offset + PAGESIZE]
        opt.append("= More =")
        sel = await device.select_list(title, opt)
        if sel < 0:
            return sel - offset if sel > (-PAGESIZE) else - offset - 1
        elif sel >= len(opt) - 1:
            offset += PAGESIZE
            continue
        else:
            return sel + offset

async def main_menu(device: SerialDevice):
    while not os.path.exists("/var/run/wpa_supplicant"):
        pass
    wifi = PyWiFi()
    while True:
        sel = await device.select_list("Menu", ["Set Wifi", "Start SSHD", "IP Address"], "ENTER", "EXIT")
        if sel < 0:
            break
        elif sel == 0:
            # set wifi
            # select interface
            interfaces = wifi.interfaces()
            if len(interfaces) <= 0:
                await device.dialog("No network interface found.")
                continue
            sel = await select(device, "Interface", [iface.name() for iface in interfaces])
            if sel < 0:
                continue
            # scan wifi
            iface = interfaces[sel]
            iface.scan()
            time.sleep(2.0)
            wifi_list: list = iface.scan_results()
            if len(wifi_list) <= 0:
                await device.dialog("No wifi found.")
                continue
            wifi_list.sort(key=lambda p: p.signal, reverse=True)
            opt = [ f"{p.ssid} ({p.signal}) {'5G' if p.freq >= 5000 else ''}" for p in wifi_list ]
            sel = await select(device, "WIFI", opt)
            if sel < 0:
                continue
            profile = wifi_list[sel]
            password = await device.input_text("", "PASSWORD")
            # connect
            profile.key = password
            iface.remove_all_network_profiles()
            iface.add_network_profile(profile)
            iface.connect(profile)
            # enable dhcp
            subprocess.run("systemctl start dhcpcd", shell=True)
            time.sleep(5.0)
            if iface.status() == const.IFACE_CONNECTED:
                await device.dialog("Connected!")
            else:
                await device.dialog("Connected Failed!")
        elif sel == 1:
            subprocess.run("systemctl start sshd", shell=True)
            await device.dialog("sshd started.")
        elif sel == 2:
            await device.dialog(ip_info())

async def main():
    device = SerialDevice()
    while True:
        try:
            if await device.init():
                print("inited")
                await main_menu(device)
            await asyncio.sleep(5.0)
        except KeyboardInterrupt:
            break
        except:
            try:
                await asyncio.sleep(5.0)
            except KeyboardInterrupt: break
            traceback.print_exc(1)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
