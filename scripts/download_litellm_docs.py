import os
import subprocess
import shutil
from pathlib import Path
import pkg_resources

def get_litellm_version():
    try:
        return pkg_resources.get_distribution('litellm').version
    except pkg_resources.DistributionNotFound:
        return None

def download_litellm_docs():
    # Get the installed version of litellm
    version = get_litellm_version()
    if not version:
        print("litellm is not installed. Please install it first.")
        return

    # Create docs directory if it doesn't exist
    docs_dir = Path(__file__).parent.parent / 'docs' / 'litellm'
    docs_dir.mkdir(parents=True, exist_ok=True)

    # Create a temporary directory for cloning
    temp_dir = Path(__file__).parent.parent / 'temp_litellm'
    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    try:
        # Clone the repository
        subprocess.run(['git', 'clone', 'https://github.com/BerriAI/litellm.git', str(temp_dir)], check=True)
        
        # Checkout the specific version
        subprocess.run(['git', 'checkout', f'v{version}'], cwd=str(temp_dir), check=True)
        
        # Copy the docs
        docs_source = temp_dir / 'docs' / 'my-website' / 'docs'
        if docs_source.exists():
            # Clear existing docs
            if docs_dir.exists():
                shutil.rmtree(docs_dir)
            # Copy new docs
            shutil.copytree(docs_source, docs_dir)
            print(f"Successfully downloaded litellm docs version {version}")
        else:
            print(f"Could not find docs in the repository for version {version}")
    
    finally:
        # Clean up
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

if __name__ == '__main__':
    download_litellm_docs() 