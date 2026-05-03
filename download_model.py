"""
Helper script to download a LiteRT-LM (.litertlm) model from Hugging Face.
Run this script to prepare the local LLM.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env to get HF_TOKEN
load_dotenv()

def download_gemma():
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("huggingface_hub not installed. Run: pip install huggingface_hub")
        return

    # Correct Repo ID for LiteRT community models
    repo_id = "litert-community/gemma-4-E2B-it-litert-lm"
    filename = "gemma-4-E2B-it.litertlm"
    
    hf_token = os.getenv("HF_TOKEN")
    if not hf_token:
        print("⚠️ Warning: HF_TOKEN not found in .env. Gated models require authentication.")
        print("Please add HF_TOKEN=\"your_token\" to your .env file.")
    
    target_dir = Path("./models")
    target_dir.mkdir(exist_ok=True)
    
    print(f"Downloading {filename} from {repo_id}...")
    
    try:
        path = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=target_dir,
            local_dir_use_symlinks=False,
            token=hf_token
        )
        print(f"✅ Model downloaded to: {path}")
        print("\nNext steps:")
        print("1. Set LLM_PROVIDER=litert in your .env file")
        print(f"2. Set LITERT_MODEL_PATH={path} in your .env file")
    except Exception as e:
        print(f"❌ Download failed: {e}")
        print("Make sure you have accepted the Gemma 4 license terms on Hugging Face.")

if __name__ == "__main__":
    download_gemma()
