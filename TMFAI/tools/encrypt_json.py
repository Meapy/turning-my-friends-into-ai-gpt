from cryptography.fernet import Fernet
import os

def encrypt_json_file(dir_path):
    # Resolve project base dir (two levels up from this file)
    project_base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

    # Ensure key exists at project_base_dir/key/key.key; create one if missing
    key_dir = os.path.join(project_base_dir, 'key')
    key_path = os.path.join(key_dir, 'key.key')
    if not os.path.exists(key_path):
        os.makedirs(key_dir, exist_ok=True)
        key = Fernet.generate_key()
        with open(key_path, 'wb') as kf:
            kf.write(key)
        try:
            os.chmod(key_path, 0o600)
        except Exception:
            # ignore on platforms that don't support chmod semantics
            pass

    # Load the key
    with open(key_path, 'rb') as f:
        key = f.read()

    # Create a Fernet object using the key
    fernet = Fernet(key)

    # Find all .jsonl files in the directory
    jsonl_files = [fn for fn in os.listdir(dir_path) if fn.endswith('.jsonl')]

    # Define output path under project base dir: TMFAI/output/encrypted_jsonl.txt
    output_dir = os.path.join(project_base_dir, 'TMFAI', 'output')
    os.makedirs(output_dir, exist_ok=True)
    output_file_path = os.path.join(output_dir, 'encrypted_jsonl.txt')

    # Open the output file in write-binary mode
    with open(output_file_path, 'wb') as out_file:
        for filename in jsonl_files:
            file_path = os.path.join(dir_path, filename)
            with open(file_path, 'rb') as f:
                original = f.read()
            encrypted = fernet.encrypt(original)
            out_file.write(encrypted + b'\n')

if __name__ == '__main__':
    # default example path (relative to repo root) can be overridden by first CLI arg
    import sys
    default_dir = os.path.join(project_base_dir, 'TMFAI', 'example') if 'project_base_dir' in globals() else 'tmfai/example'
    dir_to_encrypt = sys.argv[1] if len(sys.argv) > 1 else default_dir
    encrypt_json_file(dir_to_encrypt)