import os
import sys
import subprocess

def main():
    # Base directory where tracker.py lives
    base_dir = os.path.dirname(os.path.abspath(__file__))
    script_dir = os.path.join(base_dir, 'script')
    
    scripts = [
        "update_general_sports.py",
        "update_sports_Fancode.py"
    ]
    
    has_failed = False
    
    for script_name in scripts:
        script_path = os.path.join(script_dir, script_name)
        
        if os.path.exists(script_path):
            print(f"==========================================")
            print(f" Running: {script_name}")
            print(f"==========================================")
            
            # Execute with cwd set to script_dir so relative file paths work properly
            result = subprocess.run([sys.executable, script_path], cwd=script_dir)
            
            if result.returncode == 0:
                print(f"✓ Finished {script_name} successfully.\n")
            else:
                print(f"✗ Failed {script_name} (Exit Code: {result.returncode})\n")
                has_failed = True
        else:
            print(f"⚠ Warning: Could not find {script_path}\n")
            has_failed = True

    # Force GitHub Actions to report a failure if any script failed
    if has_failed:
        sys.exit(1)

if __name__ == "__main__":
    main()
