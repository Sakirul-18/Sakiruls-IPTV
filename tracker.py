import os
import sys
import subprocess

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    script_dir = os.path.join(base_dir, 'script')
    
    # List all target update scripts
    scripts = [
        "update_general_sports.py",
        "update_sports_Fancode.py"
    ]
    
    for script_name in scripts:
        script_path = os.path.join(script_dir, script_name)
        
        if os.path.exists(script_path):
            print(f"==========================================")
            print(f" Running: {script_name}")
            print(f"==========================================")
            
            # Run script with a timeout to prevent workflow hanging
            try:
                result = subprocess.run(
                    [sys.executable, script_path], 
                    cwd=base_dir, 
                    timeout=300
                )
                if result.returncode == 0:
                    print(f"✓ Finished {script_name} successfully.\n")
                else:
                    print(f"✗ Warning: {script_name} returned exit code {result.returncode}\n")
            except subprocess.TimeoutExpired:
                print(f"⌛ Timeout: {script_name} took longer than 5 minutes.\n")
        else:
            print(f"⚠ Warning: File not found at {script_path}\n")

    # Always exit 0 so GitHub Actions proceeds to commit any updated streams
    sys.exit(0)

if __name__ == "__main__":
    main()
