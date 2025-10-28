"""
Automatic setup utility for ensuring all dependencies are installed.
This module will automatically install missing requirements when imported.
"""
import subprocess
import sys
import importlib.util

def is_package_installed(package_name):
    """Check if a package is installed."""
    spec = importlib.util.find_spec(package_name)
    return spec is not None

def install_requirements():
    """Install requirements from requirements.txt if packages are missing."""
    required_packages = {
        'requests': 'requests',
        'textual': 'textual',
        'spacy': 'spacy'
    }
    
    missing_packages = []
    for import_name, package_name in required_packages.items():
        if not is_package_installed(import_name):
            missing_packages.append(package_name)
    
    if missing_packages:
        print("=" * 80)
        print("INSTALLING MISSING DEPENDENCIES")
        print("=" * 80)
        print(f"The following packages need to be installed: {', '.join(missing_packages)}")
        print("This is a one-time operation...")
        print()
        
        try:
            # Install from requirements.txt
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "--user"
            ])
            print()
            print("✓ All dependencies installed successfully!")
            print("=" * 80)
            print()
        except subprocess.CalledProcessError as e:
            print(f"Error installing requirements: {e}")
            print("Please run manually: pip install -r requirements.txt")
            sys.exit(1)

# Run the check when this module is imported
if __name__ != "__main__":
    install_requirements()