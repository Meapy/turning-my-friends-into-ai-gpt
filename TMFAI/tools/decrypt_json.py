from cryptography.fernet import Fernet
import os
import sys

def decrypt_json_file(encrypted_path=None):
    # Resolve project base dir (two levels up from this file)
    project_base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

    # Key path
    key_path = os.path.join(project_base_dir, 'key', 'key.key')
    if not os.path.exists(key_path):
        raise FileNotFoundError(f'Key not found: {key_path}')

    # Load key and create Fernet
    with open(key_path, 'rb') as kf:
        key = kf.read()
    fernet = Fernet(key)

    # Default encrypted file path
    if encrypted_path is None:
        encrypted_path = os.path.join(project_base_dir, 'TMFAI', 'output', 'encrypted_jsonl.txt')

    if not os.path.exists(encrypted_path):
        raise FileNotFoundError(f'Encrypted file not found: {encrypted_path}')

    # Prepare output directories
    out_dir = os.path.join(project_base_dir, 'TMFAI', 'output')
    per_file_dir = os.path.join(out_dir, 'decrypted')
    os.makedirs(per_file_dir, exist_ok=True)

    aggregate_path = os.path.join(out_dir, 'decrypted_jsonl.txt')

    # Decrypt each non-empty line and write outputs
    with open(encrypted_path, 'rb') as enf, open(aggregate_path, 'wb') as agg:
        idx = 0
        for raw in enf:
            token = raw.strip()
            if not token:
                continue
            try:
                plaintext = fernet.decrypt(token)
            except Exception as e:
                # skip lines that cannot be decrypted
                continue
            idx += 1
            per_path = os.path.join(per_file_dir, f'decrypted_{idx}.jsonl')
            with open(per_path, 'wb') as pf:
                pf.write(plaintext)
            # append to aggregate with a newline separator
            agg.write(plaintext + b'\n')

    return idx  # number of decrypted items

if __name__ == '__main__':
    # optional CLI arg: path to encrypted file
    enc = sys.argv[1] if len(sys.argv) > 1 else None
    count = decrypt_json_file(enc)
    print(f'Decrypted {count} item(s).')