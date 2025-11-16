from cryptography.fernet import Fernet
    
def create_key():
    # Generate a key
    key = Fernet.generate_key()

    # Save the key into a file
    with open('TMFAI/tools/key/key.key', 'wb') as f:
        f.write(key)

create_key()