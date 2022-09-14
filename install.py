from curses import echo
import os, sys, subprocess, shlex
APP_ROOT = os.path.abspath(os.path.dirname(__file__))
TARGET_DIR = "/etc/systemd/system"
service_file = os.path.join(APP_ROOT, "itx_control.service")
target_file = os.path.join(TARGET_DIR, "itx_control.service")

def main():
    os.chdir(APP_ROOT)
    py = os.path.join(APP_ROOT, ".venv", "bin", "python")
    if not os.path.exists(py):
        subprocess.run([sys.executable, "-m", "venv", ".venv"], shell=False)
    subprocess.run([py, "-m", "pip", "install", "-r", "requirements.txt"], shell=False)
    with open(service_file, "rt", encoding="utf8") as f:
        srv_content = f.read()
    srv_content = srv_content.replace("{{python}}", py)
    srv_content = srv_content.replace("{{main.py}}", os.path.join(APP_ROOT, "main.py"))
    echo_cmd = shlex.join(["echo", srv_content]) + " > " + target_file
    subprocess.run(["sudo", "bash", "-c", echo_cmd], shell=False)
    subprocess.run(["sudo", "chmod", "755", target_file], shell=False)
    subprocess.run(["sudo", "systemctl", "enable", "itx_control"], shell=False)
    subprocess.run(["sudo", "systemctl", "start", "itx_control"], shell=False)
    # print(srv_content)

if __name__ == "__main__":
    main()
