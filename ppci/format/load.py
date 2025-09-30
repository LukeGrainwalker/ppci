from ppci.binutils import ObjectFile, deserialise
from io import FormatError
import json

def load_object_file(file):
    """
    this function converts any supported 
    object file format into an instance of ObjectFile.
    The object file should be opened in binary mode.
    """
    start=file.read(4)
    file.seek(0)
    if b"ELF" in start:
        # read elf
    else:
        data = file.read().decode()
        if data.isprintable():
            return deserialise(json.loads(data))
        else:
            raise FormatError("file format not recognised")
    
