import os
import subprocess
import shutil
from pathlib import Path
import pkg_resources
import tempfile

def get_litellm_version():
    try:
        return pkg_resources.get_distribution('litellm').version
    except pkg_resources.DistributionNotFound:
        return None

def download_docs_from_repo(repo_url, repo_name, docs_path, target_dir, branch='main'):
    """
    Download documentation from a specified repository.
    """
    docs_dir = os.path.join('docs', target_dir)
    os.makedirs(docs_dir, exist_ok=True)
    
    temp_dir = os.path.join(tempfile.gettempdir(), f'temp_{repo_name}')
    os.makedirs(temp_dir, exist_ok=True)
    
    try:
        # Initialize git repository
        subprocess.run(['git', 'init'], cwd=temp_dir, check=True)
        subprocess.run(['git', 'remote', 'add', 'origin', repo_url], cwd=temp_dir, check=True)
        subprocess.run(['git', 'config', 'core.sparseCheckout', 'true'], cwd=temp_dir, check=True)
        
        # Set up sparse checkout
        sparse_checkout_path = os.path.join(temp_dir, '.git', 'info', 'sparse-checkout')
        with open(sparse_checkout_path, 'w') as f:
            f.write(f'{docs_path}/*\n')
        
        # Fetch only the necessary files
        subprocess.run(['git', 'pull', '--depth=1', 'origin', branch], cwd=temp_dir, check=True)
        
        # Copy documentation
        source_path = os.path.join(temp_dir, docs_path)
        if os.path.exists(source_path):
            shutil.copytree(source_path, docs_dir, dirs_exist_ok=True)
        else:
            print(f"Warning: Documentation path {docs_path} not found in {repo_name}")
    finally:
        # Clean up
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

def download_docs():
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
    temp_dir.mkdir(parents=True)

    try:
        # Initialize git repo
        subprocess.run(['git', 'init'], cwd=str(temp_dir), check=True)
        
        # Add remote
        subprocess.run(['git', 'remote', 'add', 'origin', 'https://github.com/BerriAI/litellm.git'], cwd=str(temp_dir), check=True)
        
        # Enable sparse checkout
        subprocess.run(['git', 'config', 'core.sparseCheckout', 'true'], cwd=str(temp_dir), check=True)
        
        # Set sparse checkout paths
        sparse_checkout_path = temp_dir / '.git' / 'info' / 'sparse-checkout'
        sparse_checkout_path.parent.mkdir(parents=True, exist_ok=True)
        sparse_checkout_path.write_text('docs/my-website/docs')
        
        # Fetch specific version with only the files we need
        subprocess.run(['git', 'pull', '--depth=1', 'origin', f'v{version}'], cwd=str(temp_dir), check=True)
        
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

def download_all_docs():
    """
    Download documentation from all specified repositories.
    """
    # Download LiteLLM docs
    download_docs()
    
    # Download Pydantic docs
    download_docs_from_repo(
        'https://github.com/pydantic/pydantic.git',
        'pydantic',
        'docs',
        'pydantic'
    )
    
    # Download Google Cloud SQL docs
    download_docs_from_repo(
        'https://github.com/GoogleCloudPlatform/python-docs-samples.git',
        'google-cloud-sql',
        'cloud-sql',
        'google-cloud-sql'
    )
    
    # Download SQLite docs
    download_docs_from_repo(
        'https://github.com/sqlite/sqlite.git',
        'sqlite',
        'doc',
        'sqlite',
        branch='master'  # SQLite uses 'master' as its default branch
    )

if __name__ == '__main__':
    download_all_docs() 