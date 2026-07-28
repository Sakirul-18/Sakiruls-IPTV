import os
import sys
import subprocess

def main():
    # Folder containing your individual update scripts
    script_dir = os.path.join(os.path.dirname(__file__), 'script')
    
    # List of python scripts to execute
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
            
            # Execute the sub-script
            result = subprocess.run([sys.executable, script_path])
            
            if result.returncode == 0:
                print(f"✓ Finished {script_name} successfully.\n")
            else:
                print(f"✗ Failed {script_name} (Exit Code: {result.returncode})\n")
        else:
            print(f"⚠ Warning: Could not find {script_path}\n")

if __name__ == "__main__":
    main()
