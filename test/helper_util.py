import os
import re
import sys
import subprocess
import socket
import time
import shutil
import logging
import string
from functools import lru_cache

# Store testdir for safe switch back to directory:
testdir = os.path.dirname(os.path.abspath(__file__))
logger = logging.getLogger("util")


def make_filename(s):
    """Remove all invalid characters from a string for a valid filename.

    And create a directory if none present.
    """
    folders = ["listings"]
    parts = list(map(only_valid_chars, s.split(".")))
    assert parts
    folders.extend(parts[:-1])
    basename = parts[-1]
    assert basename.startswith("test_")
    basename = basename[5:]
    assert basename
    output_dir = relpath(*folders)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    return os.path.join(output_dir, basename)


def only_valid_chars(s):
    valid_chars = string.ascii_letters + string.digits + "_"
    return "".join(c for c in s if c in valid_chars)


def relpath(*args):
    return os.path.normpath(os.path.join(testdir, *args))


def source_files(folder, extension):
    for filename in os.listdir(folder):
        if filename.endswith(extension):
            yield os.path.join(folder, filename)


qemu_app = "qemu-system-arm"
iverilog_app = "iverilog"


def tryrm(fn):
    try:
        os.remove(fn)
    except OSError:
        pass


def do_long_tests(arch):
    """Determine whether to run samples, these take somewhat longer"""
    if "LONGTESTS" not in os.environ:
        return False
    val = os.environ["LONGTESTS"]
    if arch in val or val == "all":
        return True
    else:
        return False


def do_iverilog():
    return "IVERILOG" in os.environ


def has_qemu():
    """Determines if qemu is possible"""
    if hasattr(shutil, "which"):
        return bool(shutil.which(qemu_app))
    else:
        try:
            subprocess.check_call([qemu_app, "--version"])
            return True
        except:  # noqa
            return False


def run_qemu(kernel, machine="lm3s811evb", dump_file=None, dump_range=None):
    """Runs qemu on a given kernel file"""

    if not has_qemu():
        return ""
    # Check bin file exists:
    assert os.path.isfile(kernel)

    logger.debug("Running qemu with machine=%s and image %s", machine, kernel)

    args = [
        "qemu-system-arm",
        "-M",
        machine,
        "-m",
        "16M",
        "-nographic",
        "-kernel",
        kernel,
    ]
    return qemu(args)


def create_qemu_launch_script(filename, qemu_cmd):
    """Create a shell script for a qemu command.

    This can be used to launch qemu manually for a specific test example.
    """

    # Add additional options for qemu when running from command line:
    qemu_cmd = qemu_cmd + [
        "-serial",
        "stdio",  # route serial port to stdout.
        # '-S',  # Halt on startup
        # '-gdb', 'tcp::1234',  # open gdb port
        "-D",
        "trace.txt",
        "-d",
        "in_asm,exec,int,op_opt,cpu",  # Extensive tracing
        "-singlestep",  # make sure we step a single instruction at a time.
    ]

    if "-nographic" in qemu_cmd:
        qemu_cmd.remove("-nographic")

    with open(filename, "w") as f:
        print("#!/bin/bash", file=f)
        print("", file=f)
        print("# *** automatically generated QEMU launch file! ***", file=f)
        print("", file=f)
        print("{}".format(" ".join(qemu_cmd)), file=f)
        print("", file=f)

    if sys.platform == "linux":
        # chmod +x:
        import stat

        st = os.stat(filename)
        os.chmod(filename, st.st_mode | stat.S_IEXEC)


def qemu(args):
    """Run qemu with given arguments and capture serial output"""
    # Listen to the control socket:
    qemu_control_serve = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    qemu_control_serve.bind(("", 0))  # Using 0 as port for autoselect port
    ctrl_port = qemu_control_serve.getsockname()[1]

    # Allow a queue of connections, since we start qemu first, then accept
    # the connection.
    qemu_control_serve.listen(1)

    # Listen to the serial output:
    qemu_serial_serve = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    qemu_serial_serve.bind(("", 0))
    ser_port = qemu_serial_serve.getsockname()[1]
    qemu_serial_serve.listen(1)

    logger.debug("Listening on {} for data".format(ser_port))

    args = args + [
        "-monitor",
        "tcp:localhost:{}".format(ctrl_port),
        "-serial",
        "tcp:localhost:{}".format(ser_port),
        "-S",
    ]
    logger.debug("Starting qemu like this: %s", args)

    if hasattr(subprocess, "DEVNULL"):
        qemu_process = subprocess.Popen(args)  # stderr=subprocess.DEVNULL)
    else:
        # pypy3 has no dev null:
        qemu_process = subprocess.Popen(args)

    # qemu_serial Give process some time to boot:
    qemu_serial_serve.settimeout(5)
    qemu_control_serve.settimeout(5)
    qemu_serial, _ = qemu_serial_serve.accept()
    qemu_control, _ = qemu_control_serve.accept()

    # Give the go command:
    qemu_control.send("cont\n".encode("ascii"))

    qemu_serial.settimeout(1.0)

    # Receive all data:
    data = bytearray()
    while True:
        try:
            data_byte = qemu_serial.recv(1)
            if len(data_byte) == 0:
                raise RuntimeError("Connection gone loco?")
            if data_byte == bytes([4]):  # EOT (end of transmission)
                break
            data += data_byte
        except socket.timeout:
            logger.warning("Timeout on socket")
            break
    data = data.decode("ascii", errors="ignore")
    logger.debug("Received {} characters".format(len(data)))
    # print('data', data)

    # Send quit command:
    qemu_control.send("quit\n".encode("ascii"))
    if hasattr(subprocess, "TimeoutExpired"):
        try:
            qemu_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            qemu_process.kill()
    else:
        time.sleep(2)
        qemu_process.kill()
    qemu_control.close()
    qemu_serial.close()
    qemu_control_serve.shutdown(0)
    qemu_control_serve.close()
    qemu_serial_serve.shutdown(0)
    qemu_serial_serve.close()

    logger.debug("Qemu closed")

    # Check that output was correct:
    return data


def run_python(kernel):
    """Run given file in python and capture output"""
    python_proc = subprocess.Popen(
        [sys.executable, kernel], stdout=subprocess.PIPE
    )

    # PYPY hack:
    if "pypy" in sys.executable:
        outs, _ = python_proc.communicate()
    else:
        outs, _ = python_proc.communicate(timeout=60)

    outs = outs.decode("ascii", errors="ignore")
    outs = outs.replace(os.linesep, "\n")
    return outs


def run_nodejs(js_filename):
    """Run given file in nodejs and capture output"""
    proc = subprocess.Popen(["node", js_filename], stdout=subprocess.PIPE)

    outs, _ = proc.communicate(timeout=10)

    outs = outs.decode("ascii", errors="ignore")
    outs = outs.replace(os.linesep, "\n")
    return outs


@lru_cache(maxsize=None)
def has_iverilog():
    """Determines if iverilog is installed"""
    return hasattr(shutil, "which") and bool(shutil.which(iverilog_app))


def run_msp430(pmem):
    """Run the given memory file in the openmsp430 iverilog project."""

    # Make a run file with the same name as the mem file:
    simv = pmem[:-4] + ".run"

    if not os.path.exists(simv):
        print()
        print("======")
        print("Compiling verilog!")
        print("======")

        # compile msp430 bench for this pmem:
        workdir = relpath(
            "..", "examples", "msp430", "test_system", "iverilog"
        )
        cmd = [
            "iverilog",
            "-o",
            simv,
            "-c",
            "args.f",
            "-D",
            'MEM_FILENAME="{}"'.format(pmem),
            "-D",
            "SEED=123",
        ]
        print(cmd)
        subprocess.check_call(cmd, stdout=sys.stdout, cwd=workdir)
        print("======")
        print("Compiled into", simv)
        print("======")

    print("======")
    print("Running", simv)
    print("======")

    # run build vvp simulation:
    outs = subprocess.check_output([simv])
    outs = outs.decode("ascii")
    print(outs)
    chars = []
    for c in re.finditer("Write serial: ([01]{8})", outs):
        ch = chr(int(c.group(1), 2))
        chars.append(ch)
    data = "".join(chars)
    return data


def run_picorv32(pmem):
    """Run the given memory file in the riscvpicorv32 iverilog project."""

    # Make a run file with the same name as the mem file:
    simv = pmem[:-4] + ".run"

    if not os.path.exists(simv):
        print()
        print("======")
        print("Compiling verilog!")
        print("======")

        # compile picorv32 bench for this pmem:
        workdir = relpath("..", "examples", "riscvpicorv32", "iverilog")
        cmd = [
            "iverilog",
            "-o",
            simv,
            "-c",
            "args.f",
            "-D",
            'MEM_FILENAME="{}"'.format(pmem),
        ]
        print(cmd)
        subprocess.check_call(cmd, cwd=workdir)
        print("======")
        print("Compiled into", simv)
        print("======")

    print("======")
    print("Running", simv)
    print("======")

    # run build vvp simulation:
    outs = subprocess.check_output([simv], timeout=30)
    outs = outs.decode("ascii")
    print(outs)
    return outs


avr_emu1 = relpath("..", "examples", "avr", "emu", "build", "emu1")


def has_avr_emulator():
    return os.path.exists(avr_emu1)


def run_avr(hexfile):
    """Run hexfile through avr emulator"""
    command = [avr_emu1, hexfile]
    logger.debug("Running %s", command)
    outs = subprocess.check_output(command, timeout=10)
    outs = outs.decode("ascii")
    print(outs)
    chars = []
    for c in re.finditer("uart: ([0-9A-F]+)", outs):
        ch = chr(int(c.group(1), 16))
        chars.append(ch)
    data = "".join(chars)
    return data


def gnu_assemble(source, as_args=(), prefix="arm-none-eabi-"):
    """Helper function to feed source through gnu assembling tools"""
    prefix = "arm-none-eabi-"
    gas = "{}as".format(prefix)
    objdump = prefix + "objdump"
    print("assembling...")
    p_as = subprocess.Popen([gas] + list(as_args), stdin=subprocess.PIPE)
    p_as.communicate(input=source.encode("ascii"))
    if p_as.returncode != 0:
        raise Exception("{}".format(p_as.returncode))

    p_objdump = subprocess.Popen([objdump, "-d"], stdout=subprocess.PIPE)
    output = p_objdump.communicate()[0].decode("ascii")
    if p_objdump.returncode != 0:
        raise Exception("{}".format(p_objdump.returncode))
    print(output)

    p_objdump = subprocess.Popen(
        [objdump, "-s", "-j", ".text"], stdout=subprocess.PIPE
    )
    output = p_objdump.communicate()[0].decode("ascii")
    if p_objdump.returncode != 0:
        raise Exception("{}".format(p_objdump.returncode))
    print(output)
    return output
