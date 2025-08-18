"""Helper script to dump all information for an architecture"""

import html
from pathlib import Path

from ppci import api
from ppci.arch import encoding

this_path = Path(__file__).resolve().parent
build_path = this_path.parent / "build"
if not build_path.exists():
    build_path.mkdir(parents=True)
arch = api.get_arch("msp430")
arch = api.get_arch("x86_64")


def mkstr(s):
    if isinstance(s, str):
        return s
    elif isinstance(s, encoding.Operand):
        return f"${s._name}"
    else:
        raise NotImplementedError()


filename = build_path / "arch_info.html"
with filename.open("w") as f:
    print(
        """<html>
    <body>
    """,
        file=f,
    )

    # Create a list:
    instructions = []
    for i in arch.isa.instructions:
        if not i.syntax:
            continue
        syntax = "".join(mkstr(s) for s in i.syntax.syntax)
        instructions.append((syntax, i))

    print("<h1>Instructions</h1>", file=f)
    print(f"<p>{len(instructions)} instructions available</p>", file=f)
    print('<table border="1">', file=f)
    print("<tr><th>syntax</th><th>Class</th></tr>", file=f)
    for syntax, ins_class in sorted(instructions, key=lambda x: x[0]):
        print("<tr>".format(), file=f)
        print(f"<td>{html.escape(syntax)}</td>", file=f)
        print(f"<td>{html.escape(str(ins_class))}</td>", file=f)
        print("</tr>".format(), file=f)
    print("</table>", file=f)

    print(
        """</body>
    </html>
    """,
        file=f,
    )
print(f"Created {filename}")
