import os
import sys
import subprocess
import venv
import platform
import json


def main():
    print("========================================")
    print("           BambuScribe Setup            ")
    print("========================================")

    # 1. Pre-requisite check
    print("\n[ATTENTION REQUIRED]")
    print("Before we begin, please ensure that your printer has both:")
    print("  1. LAN Only Mode turned ON")
    print("  2. Developer Mode turned ON (if applicable/available on your firmware)")
    print("You can find these settings in the Network section of your printer's screen.")
    input("Press Enter once you have confirmed both are turned ON... ")

    # 2. Ask the user for printer credentials
    print("\n--- Printer Credentials ---")
    printer_ip = input("Enter Printer IP Address (e.g., 192.168.1.50): ").strip()
    access_code = input("Enter Printer Access Code (from LAN Only mode): ").strip()
    serial_number = input("Enter Printer Serial Number: ").strip()

    # 3. Save credentials securely to config.json
    print("\nConfiguring your credentials...")
    config_data = {
        "PRINTER_IP": printer_ip,
        "ACCESS_CODE": access_code,
        "SERIAL_NUMBER": serial_number
    }

    try:
        with open("config.json", "w", encoding='utf-8') as f:
            json.dump(config_data, f, indent=4)
        print("Credentials saved successfully to config.json.")
    except Exception as e:
        print(f"\n[ERROR] Failed to write config.json: {e}")
        sys.exit(1)

    # 4. Check if we are already in a virtual environment or if one exists
    in_venv = sys.prefix != sys.base_prefix or os.environ.get('VIRTUAL_ENV') is not None
    venv_dir = "venv"
    venv_exists = os.path.isdir(venv_dir)

    do_install = False

    if in_venv:
        print("\n[INFO] Already running inside a virtual environment. Skipping venv operations.")
        python_exe = sys.executable
        activate_cmd = "(You are already using the active environment)"
    elif venv_exists:
        print(f"\n[INFO] Virtual environment '{venv_dir}' already exists. Skipping creation and installation.")
        if platform.system() == "Windows":
            python_exe = os.path.join(venv_dir, "Scripts", "python.exe")
            activate_cmd = f"{venv_dir}\\Scripts\\activate"
        else:
            python_exe = os.path.join(venv_dir, "bin", "python")
            activate_cmd = f"source {venv_dir}/bin/activate"
    else:
        # Create it only if it doesn't exist at all
        print(f"\nCreating a virtual environment in '{venv_dir}'...")
        builder = venv.EnvBuilder(with_pip=True)
        builder.create(venv_dir)
        do_install = True

        if platform.system() == "Windows":
            python_exe = os.path.join(venv_dir, "Scripts", "python.exe")
            pip_exe = os.path.join(venv_dir, "Scripts", "pip.exe")
            activate_cmd = f"{venv_dir}\\Scripts\\activate"
        else:
            python_exe = os.path.join(venv_dir, "bin", "python")
            pip_exe = os.path.join(venv_dir, "bin", "pip")
            activate_cmd = f"source {venv_dir}/bin/activate"

    # 5. Install dependencies ONLY if we just created the venv
    if do_install:
        packages = ["Flask", "paho-mqtt>=2.0.0", "opencv-python", "numpy", "Pillow", "Hershey-Fonts"]
        print("\nInstalling dependencies inside the virtual environment (this may take a minute)...")
        try:
            subprocess.check_call([pip_exe, "install", "--upgrade", "pip"], stdout=subprocess.DEVNULL)
            subprocess.check_call([pip_exe, "install"] + packages)
            print("Dependencies installed successfully.")
        except subprocess.CalledProcessError:
            print("\n[ERROR] Failed to install one or more dependencies. Please check your internet connection.")
            sys.exit(1)

    # 6. Display completion instructions and offer to run
    print("\n========================================")
    print("            Setup Complete!             ")
    print("========================================")
    print("In the future, to start BambuScribe manually, run:\n")
    print(f"    {activate_cmd}")
    print("    python app.py\n")

    start_now = input("Do you want to launch BambuScribe right now? (Y/n): ").strip().lower()
    if start_now != 'n':
        print("\nStarting BambuScribe... [Press Ctrl+C to exit]")
        try:
            subprocess.run([python_exe, "app.py"])
        except KeyboardInterrupt:
            print("\nShutting down BambuScribe. Goodbye! :-)")
    else:
        print("Setup finished. Have a great day! :-)")


if __name__ == "__main__":
    main()