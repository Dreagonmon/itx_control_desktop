import subprocess, re

def ip_info():
    p = subprocess.run(["ip", "addr", "show"], stdout=subprocess.PIPE)
    result = p.stdout.decode("utf8")
    try:
        pt = re.compile("inet\s+(\S+)")
        find = pt.findall(result)
        return "\n".join(find)
    except:
        return result

print(ip_info())
